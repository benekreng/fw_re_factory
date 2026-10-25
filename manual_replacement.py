"""One-shot operator replacement requests using the existing recovery state machine."""
import json
import uuid

from app import Problem, now, STOP_STATES
import engine
import recovery
from worker_control import STOPPABLE


def pending(store, job=None):
    return [r for r in recovery.records(store) if r['origin'] == 'operator'
            and r['phase'] not in ('recovered', 'replaced', 'cancelled')
            and (job is None or r['job'] == job)]


def request(store, identifier, expected_thread):
    with engine.lock(store):
        job = store.job(identifier)
        if not job.get('thread_id') or expected_thread != job['thread_id']:
            raise Problem('The worker binding changed. Refresh before replacing it.', 409)
        existing = next(iter(pending(store, identifier)), None)
        if existing:
            # Retry only notifies/reconciles this saved incident, never creates a second launch.
            if existing['worker_thread'] != expected_thread:
                raise Problem('Finish reconciling the existing replacement before requesting another.', 409)
            return recovery._update(store, existing['id'], notification=_notice())
        if job['state'] not in STOPPABLE | STOP_STATES or not job.get('project_id'):
            raise Problem('Only an existing unfinished worker with its project recorded can be replaced.', 409)
        with store.connect() as c:
            uncertain = c.execute("SELECT id FROM recoveries WHERE job=? AND (phase='launching' OR (phase='attention' AND replacement_thread IS NOT NULL AND replacement_thread!=?))", (identifier, expected_thread)).fetchone()
            if uncertain:raise Problem('Reconcile the existing uncertain replacement before requesting another.', 409)
            at = now();rid = str(uuid.uuid4())
            policy = {'enabled': True, 'source': 'Dashboard explicit Replace thread',
                      'maxReplacementsPerJob': 1, 'cooldownSeconds': 0,
                      'rules': [{'id': 'operator-replace', 'when': 'The user explicitly selected Replace thread for this worker.',
                                 'action': 'replace', 'repairSteps': [], 'verify': '', 'maxRepairAttempts': 0}]}
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE recoveries SET phase='cancelled',updated=?,error='Superseded by an explicit operator replacement.' WHERE job=? AND phase NOT IN ('recovered','replaced','cancelled','attention')", (at, identifier))
            c.execute('''INSERT INTO recoveries
                (id,job,worker_thread,phase,created,updated,policy,rule_id,evidence,origin,notification)
                VALUES (?,?,?,'retire_pending',?,?,?,?,?,'operator',?)''',
                (rid, identifier, expected_thread, at, at, json.dumps(policy), 'operator-replace',
                 'Explicit dashboard replacement request for the registered worker.', json.dumps(_notice())))
            observation = {'recoveryId': rid, 'summary': 'Replacement requested by the user; retire the old thread and create a clean continuation.', 'observedAt': at}
            c.execute("UPDATE jobs SET state='recovery_pending',updated=?,observation=?,error=NULL WHERE id=?", (at, json.dumps(observation), identifier))
            c.execute('DELETE FROM launch_approvals WHERE job=?', (identifier,))
            c.execute("UPDATE worker_controls SET phase='superseded',updated=?,notification=NULL,error='Explicit Replace thread superseded this stop/resume request.' WHERE job=?", (at, identifier))
            c.execute('UPDATE watcher SET epoch=epoch+1,last_wake=NULL WHERE id=1')
            c.execute('INSERT INTO events(job,time,kind,detail) VALUES (?,?,?,?)',
                      (identifier, at, 'operator_replacement_requested', json.dumps({'recoveryId': rid, 'threadId': expected_thread})))
        return recovery.get(store, rid)


def _notice():
    return {'id': str(uuid.uuid4()), 'state': 'pending', 'requestedAt': now()}


def notify(store):
    notices = []
    with engine.lock(store):
        owner = engine.owner(store)
        for ticket in pending(store):
            # An intentional Stop supersedes this operation, even if an attention record remains.
            if store.job(ticket['job'])['state'] in STOP_STATES:continue
            notice = ticket['notification']
            if not notice:continue
            if notice.get('thread') != owner['thread'] or notice.get('epoch') != owner['epoch']:
                notice = notice | {'state': 'pending'}
            if notice['state'] != 'pending':
                notices.append(notice);continue
            if not owner['thread'] or owner['phase'] != 'ready':
                notice = notice | {'error': 'No ready watcher; replacement request remains saved.'}
            else:
                notice = notice | {'state': 'sending', 'thread': owner['thread'], 'epoch': owner['epoch'], 'attemptedAt': now()}
                recovery._update(store, ticket['id'], notification=notice)
                message = (f'User explicitly requested Replace thread: job={ticket["job"]} recovery={ticket["id"]}. '
                           f'Ownership: thread={owner["thread"]} epoch={owner["epoch"]}. '
                           f'Read {store.root}/CONTROLLER.md and this saved recovery record. '
                           'The origin=operator ticket authorizes one fresh continuation independently of automatic recovery rules and queue launch mode. '
                           'Interrupt the old worker, cancel its queued/owned child work, confirm idle, and archive it. '
                           'Use the existing recovery prepare/launch/record helpers and exact saved project/model/settings. '
                           'No old conversation, thread identity or repair/error history goes into the continuation prompt. '
                           'If a launch is already in flight or uncertain, reconcile it; never launch a duplicate. '
                           'Quota/concurrency checks still apply. Keep launch mode and periodic message settings unchanged.')
                try:store.cli('thread', 'send', owner['thread'], '-', '--json', input=message)
                except Exception as error:notice = notice | {'state': 'uncertain', 'error': str(error)[:600]}
                else:notice = notice | {'state': 'sent', 'sentAt': now(), 'error': None}
                engine.event(store, 'operator_replacement_notification', notice, ticket['job'])
            recovery._update(store, ticket['id'], notification=notice)
            notices.append(notice)
    return notices


def operator_action(store, identifier, expected_thread):
    ticket = request(store, identifier, expected_thread)
    notices = notify(store)
    return {'job': store.job(identifier), 'replacement': recovery.get(store, ticket['id']), 'notifications': notices}
