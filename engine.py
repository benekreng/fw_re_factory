"""Durable ownership, read-only usage sampling and conservative admission decisions."""
from __future__ import annotations
from contextlib import contextmanager, nullcontext
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
from pathlib import Path
import subprocess
import time
import uuid

from app import Problem, now

ACTIVE = {'stop_requested','preparing','prepared','launching','running','usage_limited','waiting','review_pending','incomplete','attention','recovery_pending','replacement_ready','replacement_launching'}
SLOTS = ACTIVE - {'usage_limited','waiting'}


def initialize(s):
    with s.connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS watcher (id INTEGER PRIMARY KEY CHECK(id=1), thread TEXT,
          epoch INTEGER NOT NULL DEFAULT 0, phase TEXT NOT NULL DEFAULT 'unconfigured',
          last_wake TEXT, heartbeat TEXT);
        INSERT OR IGNORE INTO watcher(id) VALUES(1);
        CREATE TABLE IF NOT EXISTS samples (id INTEGER PRIMARY KEY, time TEXT NOT NULL,
          kind TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS quota (id INTEGER PRIMARY KEY, time TEXT NOT NULL,
          account TEXT NOT NULL, provider TEXT NOT NULL, window TEXT NOT NULL,
          used REAL NOT NULL, reset_at TEXT, seconds INTEGER, scope TEXT, active INTEGER,
          active_workers INTEGER, profile TEXT);
        CREATE INDEX IF NOT EXISTS quota_history ON quota(account,window,time);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS launch_approvals(job TEXT PRIMARY KEY, target TEXT NOT NULL,
          requested TEXT NOT NULL, notified TEXT);
        CREATE TABLE IF NOT EXISTS worker_controls(job TEXT PRIMARY KEY, id TEXT NOT NULL UNIQUE,
          thread TEXT NOT NULL, phase TEXT NOT NULL, previous_state TEXT NOT NULL,
          requested TEXT NOT NULL, updated TEXT NOT NULL, notification TEXT, evidence TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS recoveries(id TEXT PRIMARY KEY, job TEXT NOT NULL,
          worker_thread TEXT NOT NULL, phase TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
          policy TEXT NOT NULL, rule_id TEXT NOT NULL, evidence TEXT NOT NULL,
          repair_attempts INTEGER NOT NULL DEFAULT 0, replacement_thread TEXT, result TEXT, error TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS one_open_recovery ON recoveries(job)
          WHERE phase NOT IN ('recovered','replaced','cancelled','attention');
        ''')
        recovery_cols={r[1] for r in c.execute('PRAGMA table_info(recoveries)')}
        for key,kind in [('origin',"TEXT NOT NULL DEFAULT 'automatic'"),('notification','TEXT')]:
            if key not in recovery_cols:c.execute('ALTER TABLE recoveries ADD COLUMN '+key+' '+kind)
        cols={r[1] for r in c.execute('PRAGMA table_info(quota)')}
        for key,kind in [('active_workers','INTEGER'),('profile','TEXT')]:
            if key not in cols:c.execute('ALTER TABLE quota ADD COLUMN '+key+' '+kind)
        if 'commit_sha' not in {r[1] for r in c.execute('PRAGMA table_info(checks)')}:
            c.execute('ALTER TABLE checks ADD COLUMN commit_sha TEXT')


@contextmanager
def lock(s, name='control'):
    with (s.data/(name+'.lock')).open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(f, fcntl.LOCK_UN)


def event(s, kind, detail, job=None):
    with s.connect() as c:
        c.execute('INSERT INTO events(job,time,kind,detail) VALUES (?,?,?,?)',
                  (job,now(),kind,json.dumps(detail)))


def owner(s):
    with s.connect() as c: return dict(c.execute('SELECT * FROM watcher WHERE id=1').fetchone())


def fence(s, thread, epoch, heartbeat=True):
    o=owner(s)
    if o['thread']!=thread or o['epoch']!=epoch or o['phase']!='ready':
        raise Problem('Watcher ownership changed or is not ready. Stop without acting.',409)
    if heartbeat:
        with s.connect() as c: c.execute('UPDATE watcher SET heartbeat=? WHERE id=1',(now(),))
    return o


def unwrap(value):
    if isinstance(value, str): value=json.loads(value)
    if isinstance(value,dict) and 'structuredContent' in value: return value['structuredContent']
    return value


def set_owner(s, thread):
    # Caller holds control lock; validate before revoking the current owner.
    if not isinstance(thread,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:._-]{0,255}',thread):
        raise Problem('A valid T3 thread ID is required.')
    result=unwrap(s.cli('thread','show',thread,'--messages','0','--json'))
    t=result.get('thread', result)
    if t.get('id',t.get('threadId'))!=thread or t.get('deletedAt') or t.get('archived'):
        raise Problem('The target thread was not found or is deleted.')
    current=owner(s)
    if current['thread']==thread and current['phase']=='ready':return current
    with s.connect() as c:
        c.execute("UPDATE watcher SET thread=?,epoch=epoch+1,phase='ready',last_wake=NULL,heartbeat=NULL WHERE id=1",(thread,))
    event(s,'watcher_handover',{'thread':thread,'epoch':owner(s)['epoch']})
    return owner(s)


def bootstrap(s):
    o=owner(s)
    if o['thread']: return o
    if o['phase']=='bootstrapping':
        raise Problem('Watcher creation is uncertain. Reconcile `t3 thread list` and use `re-launch watcher set ID`; do not create another.',409)
    config=s.config()
    if not config.get('watcherProjectId'): raise Problem('Configure a dedicated watcher project first.')
    # Persist uncertainty before the non-idempotent external call.
    with s.connect() as c: c.execute("UPDATE watcher SET phase='bootstrapping' WHERE id=1")
    marker='RE Watcher '+uuid.uuid4().hex[:10]
    event(s,'watcher_bootstrap',{'title':marker})
    message=('Read '+str(s.root/'CONTROLLER.md')+'. This is a new watcher. Find your T3 thread ID, '
             'then run re-launch watcher adopt SELF_THREAD_ID. No work before ownership is ready. '
             'Use the saved queue according to its launch mode. Do not request another confirmation for jobs whose launch is already authorized.')
    result=unwrap(s.cli('thread','start',config['watcherProjectId'],'-','--title',marker,'--json',input=message))
    tid=result.get('threadId') or result.get('id') or result.get('thread',{}).get('threadId') or result.get('thread',{}).get('id')
    if not tid: raise Problem('T3 returned no watcher ID; bootstrap is uncertain. Reconcile before retrying.')
    # No CLI model override: dedicated project's verified default includes exact watcher options.
    return set_owner(s,tid)


def wake(s, reason='periodic check', force=False):
    with lock(s):
        requested = pending_launch_notifications(s)
        from model_editor import catalog_snapshot
        catalog = catalog_snapshot(s)
        catalog_request = catalog['refreshRequest']
        catalog_notify = catalog_request and not catalog_request.get('notifiedAt')
        if s.config().get('watcherMessagesPaused') and not requested and not catalog_notify:
            return {'sent':False,'reason':'watcher messages paused by the user'}
        o=owner(s)
        if not o['thread'] or o['phase']!='ready': return {'sent':False,'reason':'no ready watcher'}
        seconds=s.config().get('wakeSeconds',240)
        if not force and not requested and not catalog_notify and o['last_wake'] and time.time()-stamp(o['last_wake']) < seconds:
            return {'sent':False,'reason':'coalesced'}
        shown=unwrap(s.cli('thread','show',o['thread'],'--messages','0','--json'))
        t=shown.get('thread',shown)
        if t.get('activeRunId') or t.get('pendingRequestCount',0):
            return {'sent':False,'reason':'watcher is already working'}
        if requested:reason='user requested launch for '+', '.join(x['job'] for x in requested)
        if catalog['refreshPending']:reason += '; refresh the live model catalog with orchestrator_capabilities and fenced catalog'
        message=(f'Firmware watch: {reason}. Read {s.root}/CONTROLLER.md. '
                 f'Ownership: thread={o["thread"]} epoch={o["epoch"]}. '
                 'Fence ownership before acting. Run the short check first; review sources only when needed.')
        # Persist delivery intent before send so timeout does not cause rapid repeated nudges.
        with s.connect() as c:
            c.execute('UPDATE watcher SET last_wake=? WHERE id=1',(now(),))
            if catalog_notify:
                catalog_request['notifiedAt']=now()
                c.execute("INSERT OR REPLACE INTO metadata VALUES ('model_catalog_request',?)",(json.dumps(catalog_request),))
            for request in requested:
                c.execute('UPDATE launch_approvals SET notified=? WHERE job=? AND target=?',
                          (now(),request['job'],request['target']))
        s.cli('thread','send',o['thread'],'-','--json',input=message)
        event(s,'watcher_wake',{'epoch':o['epoch'],'reason':reason})
        return {'sent':True,'thread':o['thread'],'epoch':o['epoch']}


def launch_target(job):
    if job['state']=='replacement_ready':
        rid=(job.get('observation') or {}).get('recoveryId')
        if not rid:raise Problem('Replacement recovery identity is missing.',409)
        return 'recovery:'+rid
    return 'initial'


def launch_approval(s, job):
    with s.connect() as c:
        row=c.execute('SELECT * FROM launch_approvals WHERE job=? AND target=?',
                      (job['id'],launch_target(job))).fetchone()
    return dict(row) if row else None


def require_launch_approval(s, job):
    if s.config().get('launchMode','manual')=='manual' and not launch_approval(s,job):
        raise Problem('Manual mode: waiting for you to click Launch in the dashboard.',409)


def request_launch(s, identifier):
    """Operator approval only; never starts a worker or changes its saved settings."""
    with lock(s):
        jobs=s.jobs();job=next((j for j in jobs if identifier in (j['id'],j['slug'])),None)
        if not job:raise Problem('Unknown launcher job.',404)
        if job['state'] not in ('queued','preparing','prepared','replacement_ready'):
            raise Problem('Only an unstarted job or prepared replacement can be launched.',409)
        if job.get('thread_id') and job['state']!='replacement_ready':
            raise Problem('This job already has a worker.',409)
        target=launch_target(job)
        with s.connect() as c:
            c.execute('''INSERT INTO launch_approvals(job,target,requested,notified) VALUES (?,?,?,NULL)
              ON CONFLICT(job) DO UPDATE SET target=excluded.target, requested=excluded.requested, notified=NULL''',
                      (job['id'],target,now()))
        event(s,'launch_requested',{'target':target},job['id'])
        return launch_approval(s,job)


def pending_launch_notifications(s):
    with s.connect() as c:rows=[dict(r) for r in c.execute('SELECT * FROM launch_approvals WHERE notified IS NULL')]
    jobs={j['id']:j for j in s.jobs()}
    return [r for r in rows if r['job'] in jobs and jobs[r['job']]['state'] in ('queued','preparing','prepared','replacement_ready')
            and r['target']==launch_target(jobs[r['job']])]


def notify_launch(s):
    # Approval is already durable; an unavailable watcher must not undo or hide it.
    try:return wake(s,'user requested launch',force=True)
    except Exception as error:return {'sent':False,'reason':str(error)}


def set_launch_mode(s, mode):
    from controller import atomic_json
    if mode not in ('manual','automatic'):raise Problem('Launch mode must be manual or automatic.')
    with lock(s):
        config=s.config();config['launchMode']=mode;atomic_json(s.data/'config.json',config)
        event(s,'launch_mode_changed',{'mode':mode})
    return {'launchMode':mode}


def replace_worker(s, identifier, thread, expected_old=None, reason='User supplied a replacement worker', *, _locked=False, _recovery_id=None):
    """Adopt an existing worker without sending it messages or copying chat context."""
    if not isinstance(thread,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:._-]{0,255}',thread):
        raise Problem('A valid replacement T3 thread ID is required.')
    with nullcontext() if _locked else lock(s):
        with s.connect() as c:
            row=c.execute('SELECT * FROM jobs WHERE id=?',(identifier,)).fetchone()
            if row is None:row=c.execute('SELECT * FROM jobs WHERE slug=?',(identifier,)).fetchone()
        if row is None:raise Problem('Unknown job ID or device directory name.',404)
        job=s.decode(row);old=job['thread_id']
        if old==thread:return {'changed':False,'job':job,'watcher':owner(s)}
        from app import STOP_STATES
        if job['state'] in STOP_STATES:raise Problem('The user stopped this worker; replacement is not authorized.',409)
        if not old or job['state']=='cancelled':raise Problem('Only an existing, uncancelled worker can be replaced.',409)
        if expected_old is not None and old!=expected_old:raise Problem('The worker binding changed; inspect it before retrying.',409)
        with s.connect() as c:
            uncertain=c.execute("SELECT * FROM recoveries WHERE job=? AND id!=? AND phase IN ('launching','attention') AND (replacement_thread IS NOT NULL OR phase='launching')",(job['id'],_recovery_id or '')).fetchall()
        if any(r['replacement_thread'] not in (thread,old) for r in uncertain):
            raise Problem('Reconcile the existing uncertain launch/candidate before adopting another worker.',409)
        watcher=owner(s)
        if watcher['phase'] not in ('ready','worker_handover'):
            raise Problem('Resolve the watcher ownership state before replacing a worker.',409)
        actual=unwrap(s.cli('thread','show',thread,'--messages','0','--json'))
        actual=actual.get('thread',actual)
        if actual.get('threadId',actual.get('id'))!=thread or actual.get('archived') or actual.get('deletedAt'):
            raise Problem('Replacement thread is unavailable or archived.')
        if actual.get('projectId')!=job['project_id']:
            raise Problem('Replacement must belong to the same project.',409)
        worktree=actual.get('worktreePath')
        if worktree and Path(worktree).resolve()!=Path(job['request']['destination']).resolve():
            raise Problem('Replacement is bound to a different workspace.',409)
        selection=job['request']['selection']
        if actual.get('providerInstanceId')!=selection['instanceId'] or actual.get('model')!=selection['model']:
            raise Problem('Replacement provider/model differs from this job; reconcile its profile first.',409)
        retired=unwrap(s.cli('thread','show',old,'--messages','0','--json'))
        retired=retired.get('thread',retired)
        if not retired.get('archived') or retired.get('activeRunId') or retired.get('pendingRequestCount',0):
            raise Problem('Stop and archive the old worker before replacing its binding.',409)
        at=now()
        status=actual.get('status')
        state='running' if actual.get('activeRunId') or status in ('running','starting','preparing','queued') else 'attention'
        observation={'monitoringSince':at,'observedAt':at,'threadStatus':status,
                     'summary':'Current worker adopted; progress verification pending.',
                     'modelOptionsVerified':False,'contextPolicy':'current_thread_and_project_only'}
        binding={'threadId':thread,'projectId':job['project_id'],'source':'adopted_existing_thread',
                 'providerInstanceId':actual['providerInstanceId'],'model':actual['model'],
                 'optionsVerified':False,'adoptedAt':at}
        detail={'oldThreadId':old,'threadId':thread,'reason':str(reason)[:1000],
                'contextPolicy':'current_thread_and_project_only','messagesSentToReplacement':False}
        try:
            with s.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                c.execute('''UPDATE jobs SET thread_id=?,state=?,updated=?,observation=?,review=NULL,
                             launch_result=?,error=NULL WHERE id=? AND thread_id=?''',
                          (thread,state,at,json.dumps(observation),json.dumps(binding),job['id'],old))
                if c.execute('SELECT changes()').fetchone()[0]!=1:raise Problem('Worker binding changed during replacement.',409)
                c.execute("UPDATE watcher SET epoch=epoch+1,phase='ready',last_wake=NULL,heartbeat=NULL WHERE id=1")
                c.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('retired_worker:'+old,json.dumps({'job':job['id'],'replacement':thread,'time':at})))
                c.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('calibration_after:'+job['request']['quotaPool'],at))
                c.execute('INSERT INTO events(job,time,kind,detail) VALUES (?,?,?,?)',(job['id'],at,'worker_replaced',json.dumps(detail)))
                c.execute("UPDATE recoveries SET phase='cancelled',updated=?,error='A replacement was adopted; superseded recovery context is closed.' WHERE job=? AND worker_thread=? AND id!=? AND phase NOT IN ('recovered','replaced','cancelled','attention')",(at,job['id'],old,_recovery_id or ''))
        except sqlite3.IntegrityError:
            raise Problem('Replacement thread already belongs to another job.',409)
        return {'changed':True,'job':s.job(job['id']),'watcher':owner(s)}


def stamp(value):
    if value is None: return None
    try:
        if isinstance(value,(float,int)):
            return float(value)/1000 if value>1e12 else float(value)
        parsed=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if parsed.tzinfo is None: return None
        return parsed.timestamp()
    except (ValueError,TypeError,OverflowError): return None


def iso(value):
    ts=stamp(value)
    return dt.datetime.fromtimestamp(ts,dt.timezone.utc).isoformat() if ts is not None else None


def numeric(v):
    return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)


def windows(account):
    usage=account.get('usage') or {}
    out=[]
    def add(name, v, seconds=None, scope='all', active=True):
        if not isinstance(v,dict): return
        used=v.get('utilization',v.get('used_percent',v.get('usedPercent',v.get('percent'))))
        if not numeric(used) or not 0<=used<=100: return
        reset=v.get('resets_at',v.get('reset_at',v.get('resetAt',v.get('resetsAt'))))
        seconds=v.get('limit_window_seconds',v.get('limitWindowSeconds',seconds))
        if not numeric(seconds) or seconds<=0: seconds=None
        out.append(dict(window=name,used=float(used),reset_at=iso(reset),seconds=seconds,
                        scope=str(scope),active=bool(active)))
    if account.get('provider')=='claude':
        for key,value in usage.items():
            if key.startswith(('five_hour','seven_day')):
                add(key,value,18000 if key.startswith('five_hour') else 604800,
                    key if key not in ('five_hour','seven_day') else 'all')
        if not out:
            for i,v in enumerate(usage.get('limits') or []):
                kind=str(v.get('kind',i))
                add(kind,v,18000 if 'hour' in kind else 604800 if 'week' in kind or 'day' in kind else None,
                    v.get('scope','all'),v.get('is_active',True))
    else:
        def rate(prefix,rate):
            if not isinstance(rate,dict):return
            for key in ('primary_window','secondary_window','primaryWindow','secondaryWindow'):
                add(prefix+key,rate.get(key),scope=prefix or 'all')
        rate('',usage.get('rate_limit',usage.get('rateLimit')))
        extra=usage.get('additional_rate_limits',usage.get('additionalRateLimits')) or []
        if isinstance(extra,dict): extra=[dict(v,limit_name=k) for k,v in extra.items() if isinstance(v,dict)]
        for i,r in enumerate(extra):
            if isinstance(r,dict): rate(str(r.get('limit_name',r.get('limitName',i)))+':',r.get('rate_limit',r.get('rateLimit',r)))
    return out


def save_sample(s, kind, payload, at=None):
    with s.connect() as c:
        c.execute('INSERT INTO samples(time,kind,payload) VALUES (?,?,?)',(at or now(),kind,json.dumps(payload)))


def ingest(s, payload):
    at=iso(payload.get('checked_at')) or now()
    accounts=[]
    jobs=s.jobs()
    previous=latest(s,'quota') or {}
    with s.connect() as c:
        for a in payload.get('accounts',[]):
            provider=a.get('provider')
            if provider not in ('codex','claude'):continue
            identity=str(a.get('auth_index') or a.get('name') or '')
            if not identity:continue
            key=provider+'-'+hashlib.sha256(identity.encode()).hexdigest()[:12]
            entry=dict(account=key,provider=provider,disabled=bool(a.get('disabled')),
                       errors=[str(e)[:600] for e in a.get('errors',[])],resets=a.get('resets'),windows=windows(a))
            accounts.append(entry)
            old=next((x for x in previous.get('accounts',[]) if x['account']==key),None)
            old_count=(old.get('resets') or {}).get('available_count') if old else None
            new_count=(entry.get('resets') or {}).get('available_count')
            if old_count is not None and new_count is not None and old_count!=new_count:
                c.execute('INSERT INTO events(time,kind,detail) VALUES (?,?,?)',(at,'reset_vouchers_changed',json.dumps({'account':key,'before':old_count,'after':new_count})))
            if not entry['disabled']:
                running=[j for j in jobs if j['state']=='running' and j['request']['quotaPool']==provider]
                profiles=[dict(selection=j['request']['selection'],subagents=j['request']['subagents'],cap=j['request']['subagentCap']) for j in running]
                profile=json.dumps(sorted(json.dumps(p,sort_keys=True) for p in profiles))
                for w in entry['windows']:
                    prev=c.execute('SELECT * FROM quota WHERE account=? AND window=? ORDER BY id DESC LIMIT 1',(key,w['window'])).fetchone()
                    if prev and w['used']<prev['used']-.5:
                        boundary=stamp(prev['reset_at'])
                        crossed=boundary is not None and stamp(prev['time'])<=boundary<=stamp(at)
                        detail={'account':key,'window':w['window'],'before':prev['used'],'after':w['used'],
                                'classification':'scheduled reset' if crossed else 'rolling recovery or possible provider replenishment'}
                        c.execute('INSERT INTO events(time,kind,detail) VALUES (?,?,?)',(at,'quota_decreased',json.dumps(detail)))
                    c.execute('INSERT INTO quota(time,account,provider,window,used,reset_at,seconds,scope,active,active_workers,profile) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        (at,key,provider,w['window'],w['used'],w['reset_at'],w['seconds'],w['scope'],int(w['active']),len(running),profile))
    save_sample(s,'quota',dict(ok=payload.get('ok'),accounts=accounts),at)
    return {'time':at,'accounts':accounts}


def sample_usage(s):
    with lock(s,'sample'):
        try:
            cmd=s.config().get('usageCommand',['/home/vesper/.local/bin/proxy-usage'])
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
            if r.returncode not in (0,3):raise Problem('Usage reader failed (exit '+str(r.returncode)+').')
            payload=json.loads(r.stdout)
            if not isinstance(payload.get('accounts'),list):raise Problem('Usage reader returned no account list.')
            return ingest(s,payload)
        except (OSError,subprocess.TimeoutExpired,ValueError,Problem) as e:
            save_sample(s,'quota',{'ok':False,'accounts':[],'error':str(e)[:600]})
            return {'error':str(e)}


def sample_tokens(s):
    # ccusage owns cumulative snapshot deduplication. Store totals, never sum repeated polls.
    results={}
    for provider in ('claude','codex'):
        cmd=[str(s.root/'vendor/node_modules/.bin/ccusage'),provider,'session','--json','--offline','--no-cost']
        try:
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=120)
            if r.returncode:raise Problem('ccusage '+provider+' failed (exit '+str(r.returncode)+'); local logs may be unavailable.')
            value=json.loads(r.stdout)
            results[provider]={'ok':True,'report':value}
        except (OSError,subprocess.TimeoutExpired,ValueError,Problem) as e:
            results[provider]={'ok':False,'error':str(e)[:600]}
    save_sample(s,'tokens',results)
    return results


def latest(s,kind):
    with s.connect() as c:r=c.execute('SELECT * FROM samples WHERE kind=? ORDER BY id DESC LIMIT 1',(kind,)).fetchone()
    return {'time':r['time'],**json.loads(r['payload'])} if r else None


def forecast(s):
    current=latest(s,'quota')
    if not current:return []
    result=[]
    for a in current.get('accounts',[]):
        for w in a['windows']:
            with s.connect() as c:
                cutoff=c.execute('SELECT value FROM metadata WHERE key=?',('calibration_after:'+a['provider'],)).fetchone()
                history=[dict(x) for x in c.execute('SELECT * FROM quota WHERE account=? AND window=? AND time>=? ORDER BY id DESC LIMIT 80',(a['account'],w['window'],cutoff[0] if cutoff else ''))][::-1]
            rates=[]; worker_rates=[]; reset_event=None
            for prev,cur in zip(history,history[1:]):
                elapsed=(stamp(cur['time']) or 0)-(stamp(prev['time']) or 0)
                change=cur['used']-prev['used']
                if change<-.5:
                    scheduled=stamp(prev['reset_at'])
                    crossed=scheduled is not None and stamp(prev['time'])<=scheduled<=stamp(cur['time'])
                    reset_event={'time':cur['time'],'kind':'scheduled reset' if crossed else 'rolling recovery or possible provider replenishment','drop':round(-change,2)}
                    rates=[];worker_rates=[]
                if cur['reset_at']!=prev['reset_at']:
                    rates=[];worker_rates=[]
                    continue
                if cur['profile']!=prev['profile'] or cur['active_workers']!=prev['active_workers']:
                    rates=[];worker_rates=[]
                    continue
                if 30<=elapsed<=3600 and change>=0:
                    rates.append((change,elapsed))
                    if cur['active_workers'] and cur['active_workers']==prev['active_workers'] and cur['profile']==prev['profile']:
                        worker_rates.append((change,elapsed*cur['active_workers']))
            total_seconds=sum(x[1] for x in rates)
            rate=sum(x[0] for x in rates)*3600/total_seconds if total_seconds else None
            # Account-level rate includes unrelated work. Never present it as attributed worker consumption.
            hours=(100-w['used'])/rate if rate and rate>0 else None
            result.append(dict(account=a['account'],provider=a['provider'],**w,ratePerHour=rate,
                               estimatedWorkerRate=sum(x[0] for x in worker_rates)*3600/sum(x[1] for x in worker_rates) if worker_rates else None,
                               workerIntervals=len(worker_rates),profile=history[-1]['profile'] if history else None,
                               exhaustHours=hours,intervals=len(rates),observedHours=total_seconds/3600,
                               confidence='low' if len(rates)<3 or total_seconds<900 else 'measured',resetEvent=reset_event))
    return result


def plan(s):
    config=s.config(); jobs=s.jobs(); active=[j for j in jobs if j['state'] in SLOTS]
    queued=sorted((j for j in jobs if j['state']=='queued'),key=lambda j:j['created'])
    resumes=sorted((j for j in jobs if j['state']=='usage_limited' or (j['state']=='waiting' and ((j.get('observation') or {}).get('checkpointPaused') or (j.get('observation') or {}).get('operatorResumeRequested')))),key=lambda j:j['created'])
    replacements=sorted((j for j in jobs if j['state']=='replacement_ready'),key=lambda j:j['created'])
    current=latest(s,'quota'); predictions=forecast(s)
    capacity=max(0,config.get('maxParallel',2)-len(active)); decisions=[]
    with s.connect() as c:
        used=c.execute("SELECT value FROM metadata WHERE key='admission_sample'").fetchone()
        sample=c.execute("SELECT id FROM samples WHERE kind='quota' ORDER BY id DESC LIMIT 1").fetchone()
    sample_used=bool(used and sample and used[0]==str(sample[0]))
    if sample_used:capacity=0
    fresh=current and bool(current.get('accounts')) and 0<=time.time()-stamp(current['time'])<config.get('quotaFreshSeconds',600)
    for job in replacements+resumes+queued:
        others=[a for a in active if a['id']!=job['id']] if job['state']=='replacement_ready' else active
        available=max(0,config.get('maxParallel',2)-len(others)) if job['state']=='replacement_ready' else capacity
        pool=job['request']['quotaPool']
        accounts=[a for a in (current or {}).get('accounts',[]) if a['provider']==pool and not a['disabled']]
        def applies(w):
            scope=w.get('scope','all').lower()
            families=[x for x in ('opus','sonnet','haiku') if x in scope]
            return not families or any(x in job['request']['selection']['model'].lower() for x in families)
        reason=None
        if config.get('launchMode','manual')=='manual' and job['state']=='queued' and not launch_approval(s,job):
            reason='Manual mode: waiting for you to click Launch in the dashboard.'
        elif config.get('schedulingPaused'):reason='Scheduling is paused by the user; monitoring continues.'
        elif not fresh:reason='Usage is missing or stale; sample before admission.'
        elif (stamp((job.get('observation') or {}).get('resetAt')) or 0)>time.time():reason='The worker reported a future usage reset; wait until that boundary.'
        elif not accounts:reason='No enabled account in the selected quota pool.'
        elif any(a['errors'] or not a['windows'] for a in accounts):reason='Account usage is incomplete; routing is not verified.'
        elif any(w['used']>=98 for a in accounts for w in a['windows'] if w['active'] and applies(w)):reason='An enabled account is at its limit; rotation is not verified.'
        elif sample_used or available<=0:reason='Concurrency limit reached or this quota sample already admitted a job.'
        elif len(others)>=job['request']['maxParallel']:reason='This batch concurrency limit is reached.'
        else:
            relevant=[f for f in predictions if f['provider']==pool and f['active'] and applies(f)]
            if config.get('policy','short-window')=='short-window':
                shorter=[f for f in relevant if f['seconds'] is None or f['seconds']<=86400]
                relevant=shorter or relevant
            # Weekly hard cap above always applies. A weekly-first policy selects weekly forecasts first.
            if config.get('policy')=='weekly-first':
                weekly=[f for f in relevant if f['seconds'] and f['seconds']>86400]
                relevant=weekly or relevant
            usable=[f for f in relevant if f['ratePerHour'] is not None and f['intervals']>=3 and f['observedHours']>=.25]
            if any(j['request']['quotaPool']==pool for j in others) and not usable:reason='Collect at least three usage intervals before increasing concurrency.'
            for f in usable:
                reset=stamp(f['reset_at'])
                horizon=max(0,(reset-time.time())/3600) if reset else None
                if horizon is not None and f['exhaustHours'] is not None and f['exhaustHours']<=horizon*1.15:
                    reason='Current depletion already reaches the limit before reset; hold additions.'
                pool_active=sum(j['state']=='running' and j['request']['quotaPool']==pool for j in others)
                estimated=f['estimatedWorkerRate']
                profile=json.dumps(dict(selection=job['request']['selection'],subagents=job['request']['subagents'],cap=job['request']['subagentCap']),sort_keys=True)
                if pool_active and profile not in json.loads(f['profile'] or '[]'):
                    reason='This model/settings/subagent profile needs its own demand calibration; wait for a free baseline.'
                if pool_active and estimated is None:
                    reason='Worker timeline has not yielded a stable demand estimate yet.'
                projected=(f['ratePerHour'] or 0)+(estimated or 0)
                if horizon and projected*horizon>(100-f['used'])*.85:
                    reason='Estimated additional worker would exhaust headroom before reset; hold additions.'
        admit=reason is None
        decisions.append({'jobId':job['id'],'name':job['name'],'admit':admit,
                          'action':'replace' if job['state']=='replacement_ready' else 'launch' if job['state']=='queued' else 'resume',
                          'reason':reason or 'Start one approved job to measure incremental demand; reassess before the next addition.'})
        if admit:
            capacity=0  # Deliberately add at most one job per sampling cycle.
            sample_used=True
    recommendations=[]
    pause_requests=[]
    if fresh:
        for pool in ('claude','codex'):
            running=[j for j in active if j['state']=='running' and j['request']['quotaPool']==pool]
            if len(running)<2:continue
            relevant=[f for f in predictions if f['provider']==pool and f['confidence']=='measured' and f['active']]
            if config.get('policy','short-window')=='short-window':
                shorter=[f for f in relevant if f['seconds'] is None or f['seconds']<=86400];relevant=shorter or relevant
            elif config.get('policy')=='weekly-first':
                longer=[f for f in relevant if f['seconds'] and f['seconds']>86400];relevant=longer or relevant
            for f in relevant:
                boundary=stamp(f['reset_at']);hours=f['exhaustHours']
                if boundary and hours is not None and hours<(boundary-time.time())/3600*.8:
                    target=max(running,key=lambda j:j['created'])
                    if not (target.get('observation') or {}).get('pauseRequestedAt'):
                        pause_requests.append({'jobId':target['id'],'name':target['name'],'enabled':bool(config.get('checkpointPauses')),'resumeAfter':f['reset_at'],
                            'reason':'Measured current demand exhausts the selected window substantially before reset. Request one cooperative checkpoint pause, then reassess.'})
                    break
    for a in (current or {}).get('accounts',[]):
        reset=a.get('resets') or {}
        grants=reset.get('grants') or reset.get('credits') or []
        for g in grants:
            if g.get('status') and str(g['status']).lower() not in ('available','active'):continue
            expiry=stamp(g.get('ends_at',g.get('expires_at',g.get('expiresAt'))))
            if expiry and 0<expiry-time.time()<86400 and (g.get('resets_left',1) or 0)>0:
                recommendations.append({'account':a['account'],'action':'Consider reset voucher before expiry; user redemption required.','expires':iso(expiry)})
    return {'time':now(),'launchMode':config.get('launchMode','manual'),'policy':config.get('policy','short-window'),'active':len(active),'queued':len(queued),
            'maxParallel':config.get('maxParallel',2),'quotaFresh':bool(fresh),'decisions':decisions,
            'rotation':'unverified; no account mutations','vouchers':'recommendations only; no redemption','recommendations':recommendations,'checkpointRequests':pause_requests}


def dashboard(s):
    import recovery
    from host_usage import host_usage
    with s.connect() as c:
        events=[dict(r) for r in c.execute('SELECT * FROM events ORDER BY id DESC LIMIT 100')]
        rows=[dict(r) for r in c.execute("SELECT * FROM quota WHERE time>=? ORDER BY id DESC LIMIT 5000",(dt.datetime.fromtimestamp(time.time()-86400,dt.timezone.utc).isoformat(),))][::-1]
    jobs=s.jobs()
    import worker_control
    import manual_replacement
    replacements={r['job']:r for r in manual_replacement.pending(s)}
    for job in jobs:
        job['launchApproval']=launch_approval(s,job)
        job['workerControl']=worker_control.get(s,job['id'])
        job['replacementControl']=replacements.get(job['id'])
        job['replaceAvailable']=bool(job.get('thread_id') and job.get('project_id') and (job['state'] in worker_control.STOPPABLE | {'stop_requested','stopped'} or job['id'] in replacements))
        job['stopAvailable']=bool(job.get('thread_id') and job['state'] in worker_control.STOPPABLE)
    policy_state=recovery.policy_snapshot(s)
    return dict(time=now(),watcher=owner(s),jobs=jobs,plan=plan(s),usage=latest(s,'quota'),hostUsage=host_usage.sample(),
                recoveries=recovery.records(s),recoveryPolicy=policy_state['policy'],
                recoveryPolicyRevision=policy_state['revision'],recoveryPolicyNotification=policy_state['notification'],
                forecasts=forecast(s),history=rows,tokens=latest(s,'tokens'),events=events,
                config={k:s.config().get(k) for k in ('wakeSeconds','sampleSeconds','maxParallel','policy','watcherMessagesPaused')})


def daemon(s):
    import recovery
    import worker_control
    import manual_replacement
    last_sample=0;last_tokens=0
    # systemd enforces singleton. This lock also protects a manually started second daemon.
    with lock(s,'daemon'):
        while True:
            try:
                manual_replacement.notify(s)
                worker_control.notify(s)
                recovery.notify_policy_update(s)
                if time.monotonic()-last_sample>=s.config().get('sampleSeconds',240):
                    sample_usage(s);last_sample=time.monotonic()
                if time.monotonic()-last_tokens>=s.config().get('tokenSeconds',900):
                    sample_tokens(s);last_tokens=time.monotonic()
                wake(s)
            except Exception as e:
                event(s,'controller_error',{'error':str(e)[:600]})
            time.sleep(10)
