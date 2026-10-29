"""Durable review rounds. Only the owner watcher calls T3; reviewers write evidence files."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

from app import Problem, STOP_STATES, now, sha
import engine
import review_performance

ACTIVE = {'prepared', 'running', 'attention'}
VERDICTS = {'verified_complete', 'incomplete', 'review_pending'}


def initialize(store):
    with store.connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS completion_reviews (
          id TEXT PRIMARY KEY, job TEXT NOT NULL, state TEXT NOT NULL,
          created TEXT NOT NULL, updated TEXT NOT NULL, data TEXT NOT NULL);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_completion_review
          ON completion_reviews(job) WHERE state IN ('prepared','running','attention');
        CREATE TABLE IF NOT EXISTS completion_review_requests (
          job TEXT PRIMARY KEY, requested TEXT NOT NULL, reason TEXT NOT NULL,
          state TEXT NOT NULL);
        ''')


def policy(store):
    cfg = store.config().get('completionReviews') or {}
    return {'enabled': bool(cfg.get('enabled', False)),
            'selection': cfg.get('selection') or store.config().get('watcherSelection'),
            'maxConcurrent': min(4, max(1, int(cfg.get('maxConcurrent', 2)))),
            'maxReviewSeconds': min(3600, max(300, int(cfg.get('maxReviewSeconds', 1800)))),
            'maxCorrectionRounds': min(3, max(0, int(cfg.get('maxCorrectionRounds', 2))))}


def rounds(store, job=None):
    initialize(store)
    with store.connect() as c:
        rows = c.execute('SELECT * FROM completion_reviews '+('WHERE job=? ' if job else '')+
                         'ORDER BY created', (job,) if job else ()).fetchall()
    return [dict(json.loads(r['data']), id=r['id'], jobId=r['job'], state=r['state'],
                 createdAt=r['created'], updatedAt=r['updated']) for r in rows]


def get(store, rid):
    return next((r for r in rounds(store) if r['id'] == rid), None) or _missing()


def _missing():
    raise Problem('Unknown completion review.', 404)


def save(store, r, **updates):
    r = dict(r, **updates)
    with store.connect() as c:
        c.execute('UPDATE completion_reviews SET state=?,updated=?,data=? WHERE id=?',
                  (r['state'], now(), json.dumps(r), r['id']))
    return r


def request(store, job, reason):
    """Operator-authorized re-audit; keeps earlier reviews and worker assignments intact."""
    initialize(store)
    j = store.job(job)
    if not j.get('thread_id') or j['state'] in STOP_STATES or j['state'] == 'cancelled':
        raise Problem('Only an existing, non-stopped worker can be requested for review.', 409)
    with store.connect() as c:
        c.execute('INSERT INTO completion_review_requests VALUES (?,?,?,?) '
                  'ON CONFLICT(job) DO UPDATE SET requested=excluded.requested, '
                  'reason=excluded.reason,state=excluded.state', (job, now(), reason, 'pending'))
    engine.event(store, 'completion_review_requested', {'reason': reason}, job)


def fingerprint(job):
    root = Path(job['request']['destination'])
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root)
    commit = git('rev-parse', 'HEAD').decode().strip()
    h = hashlib.sha256(commit.encode() + git('diff', '--binary', 'HEAD'))
    # Include untracked, nonignored files: an uncommitted new algorithm also invalidates review.
    for name in sorted(git('ls-files', '--others', '--exclude-standard', '-z').split(b'\0')):
        if not name:
            continue
        p = root / name.decode()
        h.update(name)
        h.update(sha(p).encode() if p.is_file() else str(p).encode())
    for f in job['request'].get('files', []):
        p = root / 'originals' / f['path']
        h.update(str(p).encode())
        h.update(sha(p).encode() if p.is_file() else b'missing-original')
    return {'commit': commit, 'sourceFingerprint': h.hexdigest()}


def assignment(store, job):
    folder = store.data / 'jobs' / job['id']
    prompt = folder / 'START_PROMPT.txt'
    provenance = folder / 'provenance.json'
    if not prompt.is_file():
        raise Problem('Saved assignment is missing; recover the actual assignment before review.', 409)
    paths = [prompt]
    if provenance.is_file():
        paths.append(provenance)
        meta = json.loads(provenance.read_text())
        skill = Path(meta.get('assignedSkillPath') or folder / 'SKILL.md')
        if not skill.is_file() or (meta.get('skillSha256') and sha(skill) != meta['skillSha256']):
            raise Problem('Assigned skill snapshot is missing or changed.', 409)
        paths.append(skill)
    elif (folder / 'SKILL.md').is_file():
        paths.append(folder / 'SKILL.md')
    return {str(p): sha(p) for p in paths}


def available(store, job):
    import manual_replacement
    if job['state'] in STOP_STATES | {'cancelled', 'usage_limited', 'recovery_pending',
                                      'replacement_ready', 'replacement_launching'}:
        raise Problem('Worker is stopped, limited or recovering; do not review or resume it.', 409)
    if manual_replacement.pending(store, job['id']):
        raise Problem('Resolve the pending replacement before completion review.', 409)
    from worker_control import _show
    actual = _show(store, job['thread_id'])
    if (actual.get('activeRunId') or actual.get('pendingRequestCount') != 0 or
            actual.get('status') in {'running', 'starting', 'preparing', 'queued', 'waiting'} or
            actual.get('archived') or actual.get('deletedAt')):
        raise Problem('Completion review requires an idle worker and an explicitly empty queue.', 409)
    return actual


def current(store, r, *, check_run=True):
    job = store.job(r['jobId'])
    if job.get('thread_id') != r['workerThreadId']:
        raise Problem('Worker was replaced; this review belongs to the retired thread.', 409)
    actual = available(store, job)
    if check_run and actual.get('latestRunId') != r['workerRunId']:
        raise Problem('Worker has a newer run; re-review its new completion claim.', 409)
    if fingerprint(job) != {k: r[k] for k in ('commit', 'sourceFingerprint')}:
        raise Problem('Project changed after this review was prepared.', 409)
    if assignment(store, job) != r['assignmentHashes']:
        raise Problem('Saved assignment changed during review.', 409)
    return job


def prepare(store, owner, job_id, payload):
    cfg = policy(store)
    if not cfg['enabled']:
        raise Problem('Delegated completion reviews are disabled.', 409)
    job = store.job(job_id)
    actual = available(store, job)
    if not payload.get('completionClaim') or payload.get('workerRunId') != actual.get('latestRunId'):
        raise Problem('Supply the actual current run and its completion claim/evidence.', 409)
    fp = fingerprint(job)
    history = rounds(store, job_id)
    template_path = Path(__file__).parent / 'docs' / 'COMPLETION_REVIEWER.md'
    criteria_hash = hashlib.sha256((template_path.read_text() +
        (template_path.parent / 'REVIEW.md').read_text()).encode()).hexdigest()
    with store.connect() as c:
        requested = c.execute('SELECT requested FROM completion_review_requests WHERE job=?',
                              (job_id,)).fetchone()
    request_token = requested['requested'] if requested else None
    for r in reversed(history):
        if r['state'] in ACTIVE:
            if r['state'] == 'prepared' and r['ownerThreadId'] != owner:
                raise Problem('Reconcile the former watcher\'s uncertain delegation before adopting this round.', 409)
            if (r['workerThreadId'] != job['thread_id'] or r['workerRunId'] != actual['latestRunId']
                    or r['sourceFingerprint'] != fp['sourceFingerprint']):
                raise Problem('Reconcile/cancel the existing reviewer before starting another.', 409)
            return r
        if (r['state'] == 'completed' and r['workerThreadId'] == job['thread_id']
                and r['workerRunId'] == actual['latestRunId'] and r['sourceFingerprint'] == fp['sourceFingerprint']
                and r.get('criteriaHash') == criteria_hash and r.get('requestToken') == request_token
                and r.get('selection') == cfg['selection']):
            return r
    if sum(r['state'] in ACTIVE for r in rounds(store)) >= cfg['maxConcurrent']:
        raise Problem('Reviewer concurrency limit reached; keep the request pending.', 409)
    failures = [r for r in history if r['state'] in {'failed','cancelled'} and
                r['workerRunId'] == actual['latestRunId'] and r['sourceFingerprint'] == fp['sourceFingerprint']]
    if len(failures) >= 2:
        raise Problem('Two reviewer attempts failed on these inputs; report to the user.', 409)
    selection = store.model_selection(cfg['selection'])
    rid = str(uuid.uuid4())
    output = store.data / 'jobs' / job_id / 'completion-reviews' / rid
    output.mkdir(parents=True, exist_ok=False)
    r = {'id': rid, 'jobId': job_id, 'state': 'prepared', **fp,
         'criteriaVersion': review_performance.VERSION, 'criteriaHash': criteria_hash,
         'requestToken': request_token,
         'workerThreadId': job['thread_id'], 'workerRunId': actual['latestRunId'],
         'assignmentHashes': assignment(store, job), 'selection': selection,
         'ownerThreadId': owner, 'clientRequestId': 'fw-review-' + rid,
         'deadline': time.time() + cfg['maxReviewSeconds'],
         'outputDirectory': str(output), 'completionClaim': payload['completionClaim'],
         'assignmentNotes': payload.get('assignmentNotes', []),
         'seedEvidence': payload.get('seedEvidence', []),
         'workerTimeline': payload.get('workerTimeline', []),
         'workerTimelineCoverage': payload.get('workerTimelineCoverage', 'unknown; no complete timeline supplied'),
         'previousRounds': [{k: x.get(k) for k in ('id','workerThreadId','workerRunId','commit',
                             'state','result','followupSent','followupSentAt','workerTimeline','error')} for x in history],
         'priorControllerReview': job.get('review')}
    if not isinstance(r['workerTimeline'], list) or not all(isinstance(x, dict) and
            isinstance(x.get('runId'), str) for x in r['workerTimeline']):
        raise Problem('workerTimeline must contain observed T3 run objects with runId.', 409)
    template = template_path.read_text()
    task = template + '\n\nExact review assignment:\n' + json.dumps({
        k: v for k, v in r.items() if k not in {'state', 'selection'}}, indent=2)
    task += '\nWorker workspace (read-only): ' + job['request']['destination'] + '\n'
    task += 'Final result file: ' + str(output / 'RESULT.json') + '\n'
    (output / 'PROMPT.txt').write_text(task)
    r['promptPath'], r['promptSha256'] = str(output / 'PROMPT.txt'), sha(output / 'PROMPT.txt')
    r['delegation'] = {'task': task, 'title': 'Completion review: ' + job['name'],
                       'role': 'review', 'mode': 'async', 'isolation': 'shared',
                       'clientRequestId': r['clientRequestId'],
                       'target': {'providerInstanceId': selection['instanceId'],
                                  'model': selection['model'], 'options': selection['options']}}
    at = now()
    with store.connect() as c:
        c.execute('INSERT INTO completion_reviews VALUES (?,?,?,?,?,?)',
                  (rid, job_id, 'prepared', at, at, json.dumps(r)))
    store.patch(job_id, state='review_pending')
    engine.event(store, 'completion_review_prepared', {'reviewId': rid, **fp}, job_id)
    return r


def record(store, rid, response):
    r = get(store, rid)
    if r.get('taskId'):
        if r['taskId'] != response.get('taskId'):
            raise Problem('A different task is already bound; reconcile instead of duplicating.', 409)
        return r
    if r['state'] != 'prepared':
        raise Problem('Review is not awaiting delegation.', 409)
    # Retain returned IDs even if source/settings changed while the external call was in flight.
    if not response.get('taskId') or not response.get('childThreadId'):
        raise Problem('Retain/reconcile the uncertain delegation before retrying.', 409)
    r = save(store, r, state='running', taskId=response['taskId'],
             childThreadId=response['childThreadId'], childRunId=response.get('childRunId'),
             delegationResult=response)
    selected = r['selection']
    options = response.get('options') or {}
    if isinstance(options, list):
        options = {x['id']: x['value'] for x in options}
    if (response.get('providerInstanceId') != selected['instanceId'] or
            response.get('model') != selected['model'] or options != selected['options'] or
            response['childThreadId'] in {r['workerThreadId'], r['ownerThreadId']}):
        save(store, r, state='attention', error='Reviewer model/settings mismatch; reconcile saved task.')
        raise Problem('Reviewer settings mismatch; task retained, do not launch another.', 409)
    return r


def terminal(r, payload):
    task = payload.get('task') or {}
    if task:
        if task.get('taskId') != r.get('taskId') or task.get('childThreadId') != r.get('childThreadId'):
            raise Problem('Result belongs to a different reviewer task.', 409)
        if task.get('hasPendingChildRuns') or task.get('status') not in {'completed','failed','cancelled','interrupted'}:
            raise Problem('Reviewer is still working; do not publish a partial result.', 409)
        return task['status']
    # A replacement watcher cannot call task_status on its predecessor's child task.
    child = payload.get('childThread') or {}
    if (child.get('threadId') != r.get('childThreadId') or not r.get('childRunId') or
            child.get('latestRunId') != r['childRunId'] or child.get('activeRunId') or
            child.get('pendingRequestCount') != 0 or child.get('status') not in {'completed','failed','cancelled','interrupted'}):
        raise Problem('Read the exact saved child run to reconcile after watcher handover.', 409)
    return child['status']


def result(store, rid, payload):
    from controller import AREAS
    r = get(store, rid)
    if r['state'] == 'completed':
        return r
    status = terminal(r, payload)
    if status != 'completed':
        return save(store, r, state='failed', error='Reviewer ended with ' + status)
    try:
        current(store, r)
    except Problem as e:
        save(store, r, state='stale', error=str(e))
        raise
    data = json.loads((Path(r['outputDirectory']) / 'RESULT.json').read_text())
    if (data.get('commit') != r['commit'] or data.get('workerRunId') != r['workerRunId'] or
            data.get('workerThreadId') != r['workerThreadId'] or data.get('verdict') not in VERDICTS):
        raise Problem('Reviewer result does not identify the saved worker, run, commit and verdict.', 409)
    if not data.get('summary') or set(data.get('areas', {})) != AREAS or not all(
            isinstance(x, str) and x.strip() for x in data['areas'].values()):
        raise Problem('Review requires evidence for all seven assignment-aware areas.', 409)
    findings = data.get('findings')
    if not isinstance(findings, list) or (data['verdict'] == 'verified_complete' and findings):
        raise Problem('Accepted reviews cannot have unresolved required findings.', 409)
    for f in findings:
        if (not isinstance(f, dict) or f.get('area') not in AREAS or
                not all(isinstance(f.get(k), str) and f[k].strip()
                        for k in ('path','problem','impact','acceptance'))):
            raise Problem('Each required finding needs area, path, problem, impact and acceptance.', 409)
    if data['verdict'] == 'incomplete' and not findings:
        raise Problem('Incomplete needs concrete findings; uncertainty alone is review_pending.', 409)
    if r.get('criteriaVersion') == review_performance.VERSION:
        review_performance.validate(data.get('workerAssessment'))
        assessed = {x['reviewId'] for x in data['workerAssessment']['corrections']}
        delivered = {x['id'] for x in r.get('previousRounds', []) if x.get('followupSent')}
        # Historical corrections can predate the durable review registry. Keep
        # those evidence-backed assessments while requiring every saved delivery.
        if not delivered.issubset(assessed) or len(assessed) != len(data['workerAssessment']['corrections']):
            raise Problem('Assess every prior delivered correction; use not_assessed where evidence is missing.', 409)
    checks = data.get('checks', [])
    if not isinstance(checks, list):
        raise Problem('checks must be an array.')
    checked = []
    for item in checks:
        p = Path(item['logPath']).resolve()
        if (not p.is_relative_to(Path(r['outputDirectory']).resolve()) or not p.is_file()
                or sha(p) != item.get('logSha256') or type(item.get('exitCode')) is not int
                or not isinstance(item.get('command'), list) or not item['command']):
            raise Problem('Review check log/command/hash is invalid or outside its evidence directory.', 409)
        checked.append(item)
    if data['verdict'] == 'verified_complete' and not any(x['exitCode'] == 0 for x in checked):
        raise Problem('Acceptance needs at least one successful captured independent check.', 409)
    with store.connect() as c:
        for i, item in enumerate(checked):
            c.execute('INSERT OR IGNORE INTO checks(id,job,time,command,exit_code,log_path,log_sha256,commit_sha) '
                      'VALUES (?,?,?,?,?,?,?,?)', (str(uuid.uuid5(uuid.UUID(rid), str(i))), r['jobId'], now(),
                       json.dumps(item['command']), item['exitCode'], item['logPath'], item['logSha256'], r['commit']))
    r = save(store, r, state='completed', result=data, resultSha256=sha(Path(r['outputDirectory']) / 'RESULT.json'))
    with store.connect() as c:
        c.execute("UPDATE completion_review_requests SET state='done' WHERE job=? AND requested=?",
                  (r['jobId'], r.get('requestToken')))
    review_performance.export(store, store.job(r['jobId']), rounds(store, r['jobId']))
    engine.event(store, 'completion_review_result', {'reviewId': rid, 'verdict': data['verdict']}, r['jobId'])
    return r


def require_acceptance(store, job, review):
    if not policy(store)['enabled']:
        return
    r = get(store, review.get('completionReviewId'))
    if r['jobId'] != job['id'] or r['state'] != 'completed' or r['result']['verdict'] != 'verified_complete':
        raise Problem('Verified completion needs the completed independent reviewer acceptance.', 409)
    current(store, r)
    if sha(Path(r['outputDirectory']) / 'RESULT.json') != r['resultSha256']:
        raise Problem('Saved reviewer result changed.', 409)
    if review.get('commit') != r['commit']:
        raise Problem('Completion commit differs from the reviewer acceptance.', 409)


def followup(store, rid):
    r = get(store, rid)
    if r.get('followupSent'):
        return {'send': False, 'reason': 'This correction was already delivered.', 'review': r}
    if r['state'] != 'completed' or r['result']['verdict'] != 'incomplete':
        raise Problem('Only concrete required findings authorize a worker follow-up.', 409)
    job = current(store, r)
    if store.config().get('schedulingPaused') or (job.get('observation') or {}).get('checkpointPaused'):
        raise Problem('Scheduling/checkpoint pause blocks worker continuation.', 409)
    # Replacing the worker does not manufacture a new automatic correction budget.
    count = sum(bool(x.get('followupDelivery')) for x in rounds(store, r['jobId']) if x['id'] != rid)
    if count >= policy(store)['maxCorrectionRounds']:
        with store.connect() as c:
            c.execute("UPDATE completion_review_requests SET state='attention' WHERE job=?", (r['jobId'],))
        save(store, r, escalation='Automatic correction-round limit reached; report findings to the user.')
        return {'send': False, 'reason': 'Correction-round limit reached; user review required.'}
    delivery = r.get('followupDelivery')
    if not delivery:
        lines = ['Independent completion review found these remaining assignment requirements. '
                 'Work in this existing project with the saved assignment/settings/subagent policy. '
                 'Preserve successful evidence and unrelated work. Address only the findings below; '
                 'optional suggestions are not additional requirements.']
        for f in r['result']['findings']:
            lines.append(f"\n{f['area']} — {f['path']}\nProblem: {f['problem']}\n"
                         f"Consequence: {f['impact']}\nDone when: {f['acceptance']}")
        lines.append('\nRun affected checks, update the handoff honestly, commit useful changes and '
                     'report evidence and remaining limitations. Avoid new stages, exhaustive '
                     'quirk work or hardware/boot/scheduler reconstruction.')
        delivery = {'threadId': r['workerThreadId'], 'mode': 'auto',
                    'clientRequestId': 'fw-review-correction-' + rid, 'message': '\n'.join(lines)}
        r = save(store, r, followupDelivery=delivery)
    return {'send': True, 'delivery': delivery}


def dispatch(store, owner, epoch, action, identifier=None, payload=None):
    with engine.lock(store):
        engine.fence(store, owner, epoch)
        initialize(store)
        payload = payload or {}
        if action == 'list':
            with store.connect() as c:
                requests = [dict(x) for x in c.execute("SELECT * FROM completion_review_requests WHERE state!='done'")]
            keys = ('id','jobId','state','workerThreadId','workerRunId','commit','taskId',
                    'childThreadId','childRunId','ownerThreadId','promptPath','outputDirectory',
                    'updatedAt','followupSent','escalation','error')
            brief = [dict({k: r.get(k) for k in keys}, verdict=(r.get('result') or {}).get('verdict'),
                          overdue=r['state'] in ACTIVE and time.time() > r.get('deadline', float('inf')))
                     for r in rounds(store, identifier)]
            return {'policy': policy(store), 'requests': requests, 'rounds': brief}
        if action == 'show':
            return get(store, identifier)
        if action == 'prepare':
            return prepare(store, owner, identifier, payload)
        if action == 'performance':
            return review_performance.export(store, store.job(identifier), rounds(store, identifier))
        r = get(store, identifier)
        if action == 'record':
            return record(store, identifier, payload)
        if action == 'result':
            return result(store, identifier, payload)
        if action == 'cancel':
            if r.get('taskId') and terminal(r, payload) not in {'cancelled','failed','interrupted','completed'}:
                raise Problem('Cancel/reconcile the actual reviewer before closing its record.', 409)
            return save(store, r, state='cancelled', error=payload.get('reason', 'Review cancelled by watcher.'))
        if action == 'followup':
            return followup(store, identifier)
        if action == 'sent':
            delivery = r.get('followupDelivery') or {}
            if payload.get('threadId') != delivery.get('threadId') or not payload.get('messageId'):
                raise Problem('Record the actual saved worker correction delivery.', 409)
            job = store.job(r['jobId'])
            if job.get('thread_id') != r['workerThreadId'] or job['state'] in STOP_STATES:
                raise Problem('Worker stopped/replaced during delivery; reconcile without resuming it.', 409)
            r = save(store, r, followupSent=payload, followupSentAt=r.get('followupSentAt') or now())
            review_performance.export(store, job, rounds(store, r['jobId']))
            observation = dict(job.get('observation') or {})
            observation.update(completionReviewId=identifier, lastCorrectionMessageId=payload['messageId'],
                               summary='Independent completion-review corrections sent to existing worker.')
            store.patch(r['jobId'], state='running', observation=observation)
            return r
        raise Problem('Unknown completion-review action.')
