"""Durable operator stops. T3 interruption stays with the watcher’s native tools."""
import hashlib
import json
import uuid

from app import Problem, now, STOP_STATES
import engine

STOPPABLE = {'running', 'usage_limited', 'waiting', 'review_pending', 'incomplete',
             'attention', 'recovery_pending', 'replacement_ready'}


def _decode(row):
    if row is None:return None
    value = dict(row)
    for key in ('notification', 'evidence'):
        if value.get(key):value[key] = json.loads(value[key])
    return value


def get(store, identifier):
    with store.connect() as c:
        return _decode(c.execute('SELECT * FROM worker_controls WHERE job=?', (identifier,)).fetchone())


def records(store):
    with store.connect() as c:
        return [_decode(r) for r in c.execute('SELECT * FROM worker_controls ORDER BY requested')]


def _notice(kind):
    return {'id': str(uuid.uuid4()), 'kind': kind, 'state': 'pending', 'requestedAt': now()}


def request_stop(store, identifier, expected_thread):
    with engine.lock(store):
        job = store.job(identifier)
        if not job.get('thread_id') or expected_thread != job['thread_id']:
            raise Problem('The worker binding changed. Refresh before stopping it.', 409)
        control = get(store, identifier)
        if job['state'] in STOP_STATES and control:
            if job['state'] == 'stop_requested':
                with store.connect() as c:
                    c.execute('UPDATE worker_controls SET notification=? WHERE job=?',
                              (json.dumps(_notice('stop')), identifier))
            return store.job(identifier)
        if job['state'] not in STOPPABLE:
            raise Problem('Only an existing unfinished worker can be stopped. Reconcile an in-flight launch first.', 409)
        with store.connect() as c:
            uncertain = c.execute("SELECT id FROM recoveries WHERE job=? AND (phase='launching' OR (replacement_thread IS NOT NULL AND replacement_thread!=? AND phase='attention'))", (identifier, job['thread_id'])).fetchone()
            if uncertain:raise Problem('Reconcile the in-flight replacement before stopping this job.', 409)
            at = now();request_id = str(uuid.uuid4())
            c.execute('BEGIN IMMEDIATE')
            c.execute('''INSERT OR REPLACE INTO worker_controls
                (job,id,thread,phase,previous_state,requested,updated,notification,evidence,error)
                VALUES (?,?,?,'pending',?,?,?,?,NULL,NULL)''',
                (identifier, request_id, job['thread_id'], job['state'], at, at, json.dumps(_notice('stop'))))
            c.execute("UPDATE jobs SET state='stop_requested',updated=?,error=NULL WHERE id=?", (at, identifier))
            c.execute('DELETE FROM launch_approvals WHERE job=?', (identifier,))
            c.execute("UPDATE recoveries SET phase='cancelled',updated=?,error='Stopped by the user; automatic recovery is disabled for this job.' WHERE job=? AND (phase NOT IN ('recovered','replaced','cancelled','attention') OR (origin='operator' AND phase='attention' AND replacement_thread IS NULL))", (at, identifier))
            # Invalidate any watcher decisions made before the operator’s stop.
            c.execute('UPDATE watcher SET epoch=epoch+1,last_wake=NULL WHERE id=1')
            c.execute('INSERT INTO events(job,time,kind,detail) VALUES (?,?,?,?)',
                      (identifier, at, 'worker_stop_requested', json.dumps({'requestId': request_id, 'threadId': job['thread_id']})))
    return store.job(identifier)


def request_resume(store, identifier, expected_thread):
    with engine.lock(store):
        job = store.job(identifier);control = get(store, identifier)
        if not control or expected_thread != job['thread_id'] or control['thread'] != expected_thread:
            raise Problem('The worker binding changed. Refresh before resuming it.', 409)
        if control['phase'] == 'resumed':return job
        if job['state'] != 'stopped' or control['phase'] != 'stopped':
            raise Problem('Wait for the stop to be confirmed before resuming this worker.', 409)
        at = now()
        observation = dict(job.get('observation') or {})
        for key in ('resetAt', 'pauseRequestedAt', 'checkpointPaused'):
            observation.pop(key, None)
        observation.update(operatorResumeRequested=True, operatorResumeRequestId=str(uuid.uuid4()),
                           operatorResumeMessage='The user requested Resume. Inspect the current project files and STATUS.md, then continue the remaining authorized assignment in this same thread with the original task and subagent policy.',
                           observedAt=at, summary='Resume requested by the user; waiting for admission checks.')
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE jobs SET state='waiting',observation=?,updated=?,error=NULL WHERE id=?", (json.dumps(observation), at, identifier))
            c.execute("UPDATE worker_controls SET phase='resumed',updated=?,notification=?,error=NULL WHERE job=?", (at, json.dumps(_notice('resume')), identifier))
            c.execute('UPDATE watcher SET epoch=epoch+1,last_wake=NULL WHERE id=1')
            c.execute('INSERT INTO events(job,time,kind,detail) VALUES (?,?,?,?)',
                      (identifier, at, 'worker_resume_requested', json.dumps({'threadId': expected_thread})))
    return store.job(identifier)


def notify(store):
    """Notify immediately, including busy/paused watchers; persist uncertain delivery."""
    notices = []
    with engine.lock(store):
        owner = engine.owner(store)
        for control in records(store):
            job = store.job(control['job']);notice = control['notification']
            needed = (job['state'] == 'stop_requested' or
                      (job['state'] == 'waiting' and (job.get('observation') or {}).get('operatorResumeRequested')))
            if not notice or not needed:continue
            if notice.get('thread') != owner['thread'] or notice.get('epoch') != owner['epoch']:
                notice = notice | {'state': 'pending'}
            if notice['state'] != 'pending':
                notices.append(notice);continue
            if not owner['thread'] or owner['phase'] != 'ready':
                notice = notice | {'error': 'No ready watcher; request remains saved.'}
                with store.connect() as c:
                    c.execute('UPDATE worker_controls SET notification=? WHERE job=?', (json.dumps(notice), control['job']))
                notices.append(notice);continue
            notice = notice | {'state': 'sending', 'thread': owner['thread'], 'epoch': owner['epoch'], 'attemptedAt': now()}
            with store.connect() as c:
                c.execute('UPDATE worker_controls SET notification=? WHERE job=?', (json.dumps(notice), control['job']))
            message = (f'User requested worker {notice["kind"]}: job={control["job"]}. '
                       f'Ownership: thread={owner["thread"]} epoch={owner["epoch"]}. '
                       f'Read {store.root}/CONTROLLER.md and fenced worker-control list now. '
                       'Handle operator stops before ordinary monitoring or continuation. '
                       'A stopped worker must stay stopped unless the operator explicitly requests Resume or Replace thread. '
                       'Use native T3 interruption and cancel pending queued turns; record verified idle state. '
                       'Resume requests use the same thread and normal admission checks. '
                       'Keep launch mode and periodic message settings unchanged.')
            try:store.cli('thread', 'send', owner['thread'], '-', '--json', input=message)
            except Exception as error:notice = notice | {'state': 'uncertain', 'error': str(error)[:600]}
            else:notice = notice | {'state': 'sent', 'sentAt': now(), 'error': None}
            with store.connect() as c:
                c.execute('UPDATE worker_controls SET notification=? WHERE job=?', (json.dumps(notice), control['job']))
            engine.event(store, 'worker_control_notification', notice, control['job'])
            notices.append(notice)
    return notices


def operator_action(store, identifier, action, expected_thread):
    if action not in ('stop', 'resume'):raise Problem('Unknown operator worker action.')
    job = (request_stop if action == 'stop' else request_resume)(store, identifier, expected_thread)
    notices = notify(store)
    return {'job': job, 'control': get(store, identifier), 'notifications': notices}


def _show(store, thread):
    value = engine.unwrap(store.cli('thread', 'show', thread, '--messages', '0', '--json'))
    value = value.get('thread', value)
    if value.get('threadId', value.get('id')) != thread:
        raise Problem('T3 returned a different worker thread.', 409)
    return value


def dispatch(store, owner, epoch, action, identifier=None, payload=None):
    payload = payload or {}
    with engine.lock(store):
        engine.fence(store, owner, epoch)
        if action == 'list':return records(store)
        job = store.job(identifier);control = get(store, identifier)
        if not control or job['state'] not in STOP_STATES or control['thread'] != job['thread_id']:
            raise Problem('This worker has no active operator stop.', 409)
        if action == 'begin':
            if control['phase'] == 'stopped':return {'stopped': True, 'control': control}
            actual = _show(store, control['thread'])
            run = actual.get('activeRunId')
            with store.connect() as c:
                c.execute("UPDATE worker_controls SET phase='interrupting',updated=? WHERE job=?", (now(), identifier))
            return {'threadId': control['thread'], 'runId': run, 'requestId': control['id'],
                    'clientRequestId': 'factory-stop:'+control['id']+':'+hashlib.sha256(str(run).encode()).hexdigest()[:16]}
        if payload.get('requestId') != control['id']:
            raise Problem('The stop request changed. Reload operator controls.', 409)
        if action == 'error':
            error = str(payload.get('error', 'Interruption not confirmed.'))[:2000]
            with store.connect() as c:
                c.execute('UPDATE worker_controls SET error=?,updated=? WHERE job=?', (error, now(), identifier))
            return get(store, identifier)
        if action != 'complete':raise Problem('Unknown worker-control action.')
        if control['phase'] == 'stopped':return control
        actual = _show(store, control['thread'])
        if (actual.get('activeRunId') or type(actual.get('pendingRequestCount')) is not int
                or actual['pendingRequestCount'] != 0 or actual.get('status') in ('running', 'starting', 'preparing', 'queued')):
            raise Problem('T3 has not confirmed an idle worker with no queued requests. Keep the stop pending.', 409)
        evidence = payload.get('evidence')
        if not isinstance(evidence, str) or not evidence.strip():
            raise Problem('Record the interruption and queue/child-work checks.')
        at = now()
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE jobs SET state='stopped',updated=?,error=NULL WHERE id=?", (at, identifier))
            c.execute("UPDATE worker_controls SET phase='stopped',updated=?,evidence=?,error=NULL WHERE job=?", (at, json.dumps({'summary': evidence, 'thread': actual}), identifier))
            c.execute('INSERT INTO events(job,time,kind,detail) VALUES (?,?,?,?)',
                      (identifier, at, 'worker_stopped', json.dumps({'requestId': control['id'], 'threadId': control['thread']})))
        return get(store, identifier)
