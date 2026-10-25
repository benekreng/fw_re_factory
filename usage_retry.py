"""Durable same-thread usage-limit retries; the watcher sends through native T3."""
import json
import re
import time
import uuid

from app import Problem, now
import engine


def policy(store, job):
    observation=job.get('observation') or {}
    error=str(observation.get('providerError') or '')
    confirmed=(job['state']=='usage_limited' and bool(job.get('thread_id'))
               and bool(re.search(r'429|rate.?limit|usage.limit|quota|cooling.down',error,re.I))
               and not re.search(r'usage.request.failed|usage.lookup|telemetry|management.request',error,re.I))
    interval=store.config().get('usageRetrySeconds',1200)
    since=engine.stamp(observation.get('usageLimitedAt')) or engine.stamp(job['updated'])
    intent=observation.get('usageResume') or {}
    attempted=engine.stamp(intent.get('requestedAt')) if intent.get('threadId')==job.get('thread_id') else None
    reset=engine.stamp(observation.get('resetAt'))
    due=reset if reset and reset>since else since+interval
    attempt_due=attempted+interval if attempted else 0
    if reset and attempted and reset>attempted:attempt_due=reset
    # With positive telemetry the first retry can happen immediately. Further
    # attempts are spaced even if the reader and inference endpoint disagree.
    return {'confirmed':confirmed,
            'retryAt':engine.iso(max(due,attempt_due,reset or 0)),
            'cooldownUntil':engine.iso(max(attempt_due,reset or 0)) if attempted or reset else None}


def observe(store, identifier, state, payload):
    job=store.job(identifier);observation=dict(job.get('observation') or {})
    observation.update(payload)
    if state=='usage_limited':
        observation.setdefault('usageLimitedAt',job['updated'] if job['state']=='usage_limited' else now())
    elif state=='running' and payload.get('threadStatus')=='running':
        observation.pop('usageLimitedAt',None)
    observation['observedAt']=now()
    return store.patch(identifier,state=state,observation=observation)


def prepare(store, owner, epoch, identifier):
    """Fence, verify idle and reserve one idempotent delivery, including crash recovery."""
    from controller import claim
    with engine.lock(store):
        engine.fence(store,owner,epoch)
        job=store.job(identifier);observation=job.get('observation') or {}
        intent=observation.get('usageResume') or {}
        if job['state'] not in ('usage_limited','running') or not job.get('thread_id'):
            raise Problem('Only an existing usage-limited worker can retry.',409)
        if job['state']=='running' and not intent:
            raise Problem('This worker has no uncertain usage retry to reconcile.',409)
        if store.config().get('schedulingPaused'):
            raise Problem('Scheduling is paused by the user.',409)
        reset=engine.stamp(observation.get('resetAt'))
        if reset and reset>time.time():raise Problem('Wait until the worker-reported reset time.',409)
        shown=engine.unwrap(store.cli('thread','show',job['thread_id'],'--messages','0','--json'))
        actual=shown.get('thread',shown)
        if actual.get('threadId',actual.get('id'))!=job['thread_id'] or actual.get('archived') or actual.get('deletedAt'):
            raise Problem('The exact existing worker is unavailable; reconcile its binding.',409)
        if actual.get('activeRunId') or actual.get('pendingRequestCount') or actual.get('status') in ('running','starting','preparing','queued'):
            return {'send':False,'reason':'The existing worker is active or already has a queued turn; do not send again.'}
        if actual.get('pendingRequestCount')!=0 or actual.get('status')!='failed' or not actual.get('latestRunId'):
            raise Problem('Verify a failed worker run and an explicitly empty queue before retrying.',409)
        if intent and intent.get('threadId')==job['thread_id'] and intent.get('failedRunId')==actual['latestRunId']:
            # A lost acknowledgement cannot authorize another continuation. Reuse
            # exactly the same message/key, even after process restart or handover.
            if not policy(store,job)['confirmed'] and job['state']!='running':
                raise Problem('Record the actual worker usage-limit evidence before retrying.',409)
            return {'send':True,'reconcile':True,'delivery':{k:intent[k] for k in ('threadId','mode','message','clientRequestId')}}
        if job['state']!='usage_limited' or not policy(store,job)['confirmed']:
            raise Problem('Read the latest failed run and record its actual usage-limit error first.',409)
        observed_run=observation.get('lastRunId')
        if observed_run!=actual['latestRunId']:
            raise Problem('The worker has a new run; inspect it and save its current evidence before retrying.',409)
        intent={'threadId':job['thread_id'],'mode':'auto','clientRequestId':'fw-usage-'+uuid.uuid4().hex,
                'failedRunId':actual['latestRunId'],'requestedAt':now(),
                'message':('The previous run stopped at a provider usage limit. Retry the remaining authorized assignment '
                           'in this same thread and project. Inspect STATUS.md and the current files, then continue from '
                           'the saved work with the original task, model settings and subagent policy. Reconcile any '
                           'interrupted or failed owned subagent tasks; do not assume they finished. Preserve originals '
                           'and existing archives. If usage is still unavailable, report the actual error and any reset time.')}
        claim(store,identifier,resume=True,_usage_retry=intent)
        return {'send':True,'reconcile':False,'delivery':{k:intent[k] for k in ('threadId','mode','message','clientRequestId')}}
