"""Validated edits to unlaunched jobs and watcher-backed live model discovery."""
import datetime as dt
import hashlib
import json
import shutil
import uuid

from app import Problem, now, subagent_settings
import engine

EDITABLE = {'queued', 'preparing', 'prepared'}


def revision(request):
    value = {k: request.get(k) for k in ('selection', 'quotaPool', 'subagents', 'subagentCap', 'modelGeneration')}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def catalog_snapshot(store):
    try:
        catalog = store.capabilities()
    except Problem:
        catalog = {}
    fetched = catalog.get('fetchedAt')
    with store.connect() as c:
        row = c.execute("SELECT value FROM metadata WHERE key='model_catalog_request'").fetchone()
    request = json.loads(row[0]) if row else None
    pending = bool(request and (not fetched or request['requestedAt'] > fetched))
    return {'providers': [p for p in catalog.get('providers', [])
                          if not p.get('constraints') and p.get('models')],
            'fetchedAt': fetched,
            'stale': not fetched or (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(fetched)).total_seconds() > 3600,
            'refreshPending': pending, 'refreshRequest': request if pending else None}


def request_catalog(store):
    with engine.lock(store):
        snapshot = catalog_snapshot(store)
        if not snapshot['refreshPending']:
            request = {'id': str(uuid.uuid4()), 'requestedAt': now(), 'notifiedAt': None}
            with store.connect() as c:
                c.execute("INSERT OR REPLACE INTO metadata VALUES ('model_catalog_request',?)", (json.dumps(request),))
        elif snapshot['refreshRequest'].get('notifiedAt'):
            request = snapshot['refreshRequest'] | {'notifiedAt': None}
            with store.connect() as c:
                c.execute("INSERT OR REPLACE INTO metadata VALUES ('model_catalog_request',?)", (json.dumps(request),))
    try:
        notification = engine.wake(store, reason='user requested model catalog refresh')
    except Problem as error:
        notification = {'sent': False, 'reason': str(error)}
    return catalog_snapshot(store) | {'notification': notification}


def update_model(store, identifier, payload):
    with engine.lock(store):
        job = store.job(identifier)
        if job['state'] not in EDITABLE or job.get('thread_id'):
            raise Problem('Worker settings can only change before a job begins launching.', 409)
        if payload.get('expectedRevision') != revision(job['request']):
            raise Problem('This job’s settings changed elsewhere. Reload its saved settings.', 409)
        old = job['request']
        chosen = payload.get('selection', old['selection'])
        selection = old['selection'] if chosen == old['selection'] else store.model_selection(chosen)
        pool = payload.get('quotaPool', old.get('quotaPool'))
        policy, cap = subagent_settings(payload.get('subagents', old.get('subagents', 'none')),
                                       payload.get('subagentCap', old.get('subagentCap', 0)))
        if pool not in ('claude', 'codex'):
            raise Problem('Choose the actual quota pool: claude or codex.')
        if (selection == old['selection'] and pool == old.get('quotaPool')
                and policy == old.get('subagents', 'none') and cap == old.get('subagentCap', 0)):
            return job
        # Preserve prepared evidence before any later re-preparation overwrites it.
        artifacts = store.data / 'jobs' / identifier
        if artifacts.exists():
            archive = artifacts / 'model-history' / revision(old)
            archive.mkdir(parents=True, exist_ok=True)
            for name in ('START_PROMPT.txt', 'SKILL.md', 'provenance.json'):
                if (artifacts / name).is_file() and not (archive / name).exists():
                    shutil.copy2(artifacts / name, archive / name)
        request = old | {'selection': selection, 'quotaPool': pool, 'subagents': policy, 'subagentCap': cap,
                         'modelGeneration': old.get('modelGeneration', 0) + 1}
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE jobs SET request=?,state='queued',updated=?,error=NULL WHERE id=?",
                      (json.dumps(request), now(), identifier))
            c.execute('DELETE FROM launch_approvals WHERE job=?', (identifier,))
            c.execute('INSERT INTO events(job,time,kind,detail) VALUES (?,?,?,?)',
                      (identifier, now(), 'worker_settings_changed', json.dumps({
                          'previous': {k: old.get(k) for k in ('selection', 'quotaPool', 'subagents', 'subagentCap')},
                          'current': {'selection': selection, 'quotaPool': pool, 'subagents': policy, 'subagentCap': cap},
                          'revision': revision(request), 'readmissionRequired': True})))
        return store.job(identifier)
