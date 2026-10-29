"""User-defined, bounded thread recovery. External repair/launch tools stay with the watcher."""
import hashlib
import json
from pathlib import Path
import re
import uuid

from app import Problem, TERMINAL, STOP_STATES, now, sha
import engine

DEFAULT_POLICY={'enabled':False,'source':'Awaiting user instructions','rules':[],
                'maxReplacementsPerJob':3,'cooldownSeconds':900}
CLOSED={'recovered','replaced','cancelled','attention'}


def validate_policy(value):
    if not isinstance(value,dict):raise Problem('Recovery policy must be an object.')
    if type(value.get('enabled')) is not bool:raise Problem('Specify enabled as true or false.')
    if not isinstance(value.get('source'),str) or not value['source'].strip():raise Problem('Record the user message or file supplying this policy.')
    for key,low,high in [('maxReplacementsPerJob',1,20),('cooldownSeconds',0,86400)]:
        n=value.get(key)
        if type(n) is not int or not low<=n<=high:raise Problem(f'{key} must be {low}–{high}.')
    rules=value.get('rules')
    if not isinstance(rules,list) or len(rules)>32:raise Problem('Supply up to 32 recovery rules.')
    if value['enabled'] and not rules:raise Problem('Recovery cannot be enabled without user-defined rules.')
    seen=set();clean=[]
    for rule in rules:
        if not isinstance(rule,dict):raise Problem('Every rule must be an object.')
        key=rule.get('id','')
        if not isinstance(key,str) or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,63}',key) or key in seen:raise Problem('Rule IDs must be unique lowercase identifiers.')
        seen.add(key)
        if not isinstance(rule.get('when'),str) or not rule['when'].strip():raise Problem('Describe what counts as broken for each rule.')
        action=rule.get('action')
        if action not in ('replace','repair_then_replace'):raise Problem('Rule action must be replace or repair_then_replace.')
        steps=rule.get('repairSteps',[]);attempts=rule.get('maxRepairAttempts',0);verify=rule.get('verify','')
        if not isinstance(steps,list) or not all(isinstance(x,str) and x.strip() for x in steps):raise Problem('repairSteps must be nonempty instruction strings.')
        if type(attempts) is not int or not 0<=attempts<=5:raise Problem('maxRepairAttempts must be 0–5.')
        if not isinstance(verify,str):raise Problem('verify must describe how to check the repair.')
        if action=='repair_then_replace' and (not steps or not verify.strip() or attempts<1):raise Problem('Repair rules need repair steps, verification instructions and a bounded attempt count.')
        clean.append({'id':key,'when':rule['when'],'action':action,'repairSteps':steps,
                      'verify':verify,'maxRepairAttempts':attempts})
    return {k:value[k] for k in ('enabled','source','maxReplacementsPerJob','cooldownSeconds')}|{'rules':clean}


def policy(s):
    p=s.data/'recovery-policy.json'
    return validate_policy(json.loads(p.read_text())) if p.exists() else dict(DEFAULT_POLICY)


def set_policy(s, value):
    from controller import atomic_json
    value=validate_policy(value)
    atomic_json(s.data/'recovery-policy.json',value)
    engine.event(s,'recovery_policy_updated',{'enabled':value['enabled'],'source':value['source'],'ruleIds':[r['id'] for r in value['rules']]})
    return value


def policy_revision(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def policy_notification(s):
    with s.connect() as c:
        row=c.execute("SELECT value FROM metadata WHERE key='recovery_policy_notification'").fetchone()
    return json.loads(row[0]) if row else None


def _save_notification(s, value):
    with s.connect() as c:
        c.execute("INSERT OR REPLACE INTO metadata VALUES ('recovery_policy_notification',?)",(json.dumps(value),))
    return value


def policy_snapshot(s):
    value=policy(s);revision=policy_revision(value);notice=policy_notification(s)
    return {'policy':value,'revision':revision,
            'notification':notice if notice and notice['revision']==revision else None}


def update_dashboard_policy(s, value, expected_revision):
    """Save the operator's draft without overwriting a newer policy from another editor."""
    if not isinstance(value,dict):raise Problem('Recovery policy must be an object.')
    value=validate_policy({**value,'source':'Dashboard'})
    if not isinstance(expected_revision,str) or not re.fullmatch('[a-f0-9]{64}',expected_revision):
        raise Problem('Reload the saved recovery rules before editing.')
    with engine.lock(s):
        current=policy(s)
        if current!=value:
            if policy_revision(current)!=expected_revision:
                raise Problem('Recovery rules changed elsewhere. Your draft is retained; reload the saved rules before saving.',409)
            set_policy(s,value)
            _save_notification(s,{'id':uuid.uuid4().hex,'revision':policy_revision(value),'state':'pending','requestedAt':now()})
        elif not policy_notification(s) or policy_notification(s)['revision']!=policy_revision(value):
            _save_notification(s,{'id':uuid.uuid4().hex,'revision':policy_revision(value),'state':'pending','requestedAt':now()})
    notify_policy_update(s)
    return policy_snapshot(s)


def notify_policy_update(s, *, retry_revision=None):
    """Post operator policy edits immediately, even to a busy or paused watcher.

    Delivery intent is durable before the non-idempotent CLI call. A timeout or
    interrupted send is not retried automatically; an operator can explicitly retry.
    """
    with engine.lock(s):
        value=policy(s);revision=policy_revision(value);notice=policy_notification(s)
        if retry_revision is not None:
            if retry_revision!=revision:raise Problem('Recovery rules changed; reload before notifying the watcher.',409)
            notice=_save_notification(s,{'id':uuid.uuid4().hex,'revision':revision,'state':'pending','requestedAt':now()})
        if not notice:return None
        if notice['revision']!=revision:
            return _save_notification(s,{**notice,'state':'superseded'})
        if notice['state']!='pending':return notice
        owner=engine.owner(s)
        if not owner['thread'] or owner['phase']!='ready':
            return _save_notification(s,{**notice,'error':'No ready watcher. Rules are saved; notification will wait for one.'})
        notice=_save_notification(s,{**notice,'state':'sending','thread':owner['thread'],'epoch':owner['epoch'],'attemptedAt':now(),'error':None})
        message=(f'Recovery policy updated by the user in the dashboard. Revision={revision}. '
                 f'Ownership: thread={owner["thread"]} epoch={owner["epoch"]}. '
                 f'Read {s.root}/CONTROLLER.md and the current recovery policy now. '
                 'Apply the latest saved rules, independently of manual/automatic queue launch mode. '
                 'Reassess open incidents before further actions under a changed policy. '
                 'Keep queue launch mode and periodic watcher pause settings unchanged. '
                 'Do not send old conversation or recovery diagnostics to a replacement worker.\n\n'
                 'Saved policy:\n'+json.dumps(value,indent=2))
        try:
            s.cli('thread','send',owner['thread'],'-','--json',input=message)
        except Exception as error:
            notice=_save_notification(s,{**notice,'state':'uncertain','error':'Delivery not confirmed: '+str(error)[:600]})
        else:
            notice=_save_notification(s,{**notice,'state':'sent','sentAt':now()})
        engine.event(s,'recovery_policy_notification',{'revision':revision,'state':notice['state'],'thread':owner['thread']})
        return notice


def _decode(row):
    result=dict(row)
    for key in ('policy','result','notification'):
        if result.get(key):result[key]=json.loads(result[key])
    return result


def get(s, identifier):
    with s.connect() as c:row=c.execute('SELECT * FROM recoveries WHERE id=?',(identifier,)).fetchone()
    if not row:raise Problem('Unknown recovery record.',404)
    return _decode(row)


def records(s):
    with s.connect() as c:return [_decode(x) for x in c.execute('SELECT * FROM recoveries ORDER BY created DESC')]


def _update(s, identifier, **values):
    values['updated']=now()
    with s.connect() as c:
        c.execute('UPDATE recoveries SET '+','.join(k+'=?' for k in values)+' WHERE id=?',
                  [*[json.dumps(v) if isinstance(v,(dict,list)) else v for v in values.values()],identifier])
    return get(s,identifier)


def _show(s, thread):
    value=engine.unwrap(s.cli('thread','show',thread,'--messages','0','--json'))
    value=value.get('thread',value)
    if value.get('threadId',value.get('id'))!=thread:raise Problem('T3 returned a different thread.')
    return value


def _retired(s, thread):
    t=_show(s,thread)
    if t.get('archived') is not True or t.get('activeRunId') or type(t.get('pendingRequestCount')) is not int or t['pendingRequestCount']!=0:raise Problem('Stop and archive the broken thread before preparing its replacement.',409)


def _enabled(s):
    p=policy(s)
    if not p['enabled']:raise Problem('Automatic thread recovery is disabled; user criteria are required.',409)
    return p


def _bounds(s, job, p):
    with s.connect() as c:
        count=c.execute('SELECT COUNT(*) FROM recoveries WHERE job=? AND replacement_thread IS NOT NULL',(job,)).fetchone()[0]
        recent=c.execute("SELECT updated FROM recoveries WHERE job=? AND phase='replaced' ORDER BY updated DESC LIMIT 1",(job,)).fetchone()
    if count>=p['maxReplacementsPerJob']:raise Problem('Replacement limit reached; user intervention is required.',409)
    if recent and engine.stamp(now())-engine.stamp(recent[0])<p['cooldownSeconds']:raise Problem('Replacement cooldown has not elapsed.',409)


def _ticket(s, identifier, require_policy=False):
    ticket=get(s,identifier);job=s.job(ticket['job'])
    if job['state'] in STOP_STATES:raise Problem('The user stopped this worker; recovery is not authorized.',409)
    if ticket['phase'] not in ('replaced',) and job['thread_id']!=ticket['worker_thread']:
        # A crash after binding but before closing the ticket can be reconciled by record.
        if job['thread_id']!=ticket.get('replacement_thread'):raise Problem('This recovery belongs to a superseded worker.',409)
    if require_policy and ticket['origin'] != 'operator':
        current=_enabled(s)
        if current!=ticket['policy']:raise Problem('Recovery policy changed; cancel and reassess this incident.',409)
    return ticket,job


def _artifact(s, ticket):return s.data/'jobs'/ticket['job']/'recoveries'/ticket['id']


def _evidence(value):
    if not isinstance(value,str) or not value.strip() or len(value)>12000:raise Problem('Record concrete observed evidence (up to 12000 characters).')
    return value


def dispatch(s, owner, epoch, action, identifier=None, payload=None):
    """All recovery transitions fence ownership; this function never sends agent messages."""
    payload=payload or {}
    with engine.lock(s):
        engine.fence(s,owner,epoch)
        if action=='policy':return policy(s)
        if action=='policy_set':return set_policy(s,payload)
        if action=='list':return records(s)
        if action=='show':return get(s,identifier)
        if action=='report':
            p=_enabled(s);job=s.job(identifier)
            if not job['thread_id'] or job['state'] in TERMINAL | STOP_STATES:raise Problem('An unfinished existing worker is required.')
            if payload.get('expectedThreadId')!=job['thread_id']:raise Problem('Observed thread no longer matches this job.',409)
            rule=next((r for r in p['rules'] if r['id']==payload.get('ruleId')),None)
            if not rule:raise Problem('The observation must match an explicit user-defined rule.')
            evidence=_evidence(payload.get('evidence'))
            with s.connect() as c:
                opened=c.execute("SELECT * FROM recoveries WHERE job=? AND phase NOT IN ('recovered','replaced','cancelled','attention')",(identifier,)).fetchone()
                orphan=c.execute("SELECT id FROM recoveries WHERE job=? AND phase='attention' AND replacement_thread IS NOT NULL AND replacement_thread!=?",(identifier,job['thread_id'])).fetchone()
            if opened:return _decode(opened)
            if orphan:raise Problem('A previous uncertain replacement needs reconciliation before another recovery.',409)
            _bounds(s,identifier,p)
            rid=str(uuid.uuid4());at=now();phase='repair_pending' if rule['action']=='repair_then_replace' else 'retire_pending'
            with s.connect() as c:
                c.execute('INSERT INTO recoveries(id,job,worker_thread,phase,created,updated,policy,rule_id,evidence) VALUES (?,?,?,?,?,?,?,?,?)',
                          (rid,identifier,job['thread_id'],phase,at,at,json.dumps(p),rule['id'],evidence))
            s.patch(identifier,state='recovery_pending',observation={'recoveryId':rid,'summary':'Recovery rule matched; follow the saved bounded procedure.','observedAt':at})
            engine.event(s,'thread_recovery_reported',{'recoveryId':rid,'ruleId':rule['id']},identifier)
            return get(s,rid)
        ticket,job=_ticket(s,identifier,require_policy=action in ('retire_begin','repair_begin','prepare','launch'))
        rule=next(r for r in ticket['policy']['rules'] if r['id']==ticket['rule_id'])
        if action=='retire_begin':
            if ticket['phase']!='retire_pending':raise Problem('Only a saved retirement intent can interrupt this worker.',409)
            actual=_show(s,ticket['worker_thread']);run=actual.get('activeRunId')
            return {'threadId':ticket['worker_thread'],'runId':run,
                    'clientRequestId':'factory-retire:'+ticket['id']+':'+hashlib.sha256(str(run).encode()).hexdigest()[:16]}
        if action=='repair_begin':
            if ticket['phase']!='repair_pending' or ticket['repair_attempts']>=rule['maxRepairAttempts']:raise Problem('No further repair attempt is authorized.',409)
            return _update(s,identifier,phase='repairing',repair_attempts=ticket['repair_attempts']+1)
        if action=='repair_result':
            if ticket['phase']!='repairing' or type(payload.get('working')) is not bool:raise Problem('Record the outcome of the current repair attempt.')
            evidence=_evidence(payload.get('evidence'))
            if payload['working']:
                actual=_show(s,ticket['worker_thread'])
                if actual.get('archived'):raise Problem('An archived thread cannot be recorded as repaired.')
                state='running' if actual.get('activeRunId') else 'attention'
                s.patch(job['id'],state=state,observation={'summary':'Repair verified; normal observation can continue.','observedAt':now()})
                return _update(s,identifier,phase='recovered',result={'evidence':evidence,'working':True})
            phase='repair_pending' if ticket['repair_attempts']<rule['maxRepairAttempts'] else 'retire_pending'
            return _update(s,identifier,phase=phase,result={'evidence':evidence,'working':False})
        if action=='prepare':
            if ticket['phase'] not in ('retire_pending','replacement_ready'):raise Problem('Finish the authorized repair procedure before replacing the thread.',409)
            if ticket['origin'] != 'operator':_bounds(s,job['id'],ticket['policy'])
            _retired(s,ticket['worker_thread'])
            from controller import save_worker_prompt
            destination=Path(job['request']['destination']).resolve()
            if not destination.is_relative_to(s.projects) or not destination.is_dir():raise Problem('The existing project directory is unavailable.')
            marker=destination/'LAUNCH_REQUEST.json'
            if not marker.is_file() or json.loads(marker.read_text()).get('id')!=job['id']:raise Problem('Project ownership marker does not match this job.')
            for source in job['request']['files']:
                original=(destination/'originals'/source['path']).resolve()
                if not original.is_relative_to(destination) or not original.is_file() or sha(original)!=source['sha256']:
                    raise Problem('An original is missing or changed; preserve the workspace and investigate before replacement.')
            artifacts=_artifact(s,ticket)
            save_worker_prompt(job,artifacts,continuation=True)
            s.patch(job['id'],state='replacement_ready')
            return _update(s,identifier,phase='replacement_ready')|{'promptPath':str(artifacts/'START_PROMPT.txt')}
        if action=='launch':
            if ticket['phase']!='replacement_ready':raise Problem('Do not repeat an uncertain replacement launch; reconcile T3 threads first.',409)
            from controller import require_current_prompt
            require_current_prompt(_artifact(s,ticket))
            _retired(s,ticket['worker_thread'])
            if ticket['origin'] != 'operator':_bounds(s,job['id'],ticket['policy'])
            s.model_selection(job['request']['selection'])
            decision=next((d for d in engine.plan(s)['decisions'] if d['jobId']==job['id']),None)
            if not decision or not decision['admit']:raise Problem(decision['reason'] if decision else 'Replacement is not eligible.',409)
            title=job['name']+' · '+identifier[:8]
            spec={'title':title,'projectId':job['project_id'],'workspaceStrategy':{'type':'root'},
                  'modelSelection':job['request']['selection'],'promptPath':str(_artifact(s,ticket)/'START_PROMPT.txt')}
            at=now()
            with s.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                sample=c.execute("SELECT id FROM samples WHERE kind='quota' ORDER BY id DESC LIMIT 1").fetchone()
                c.execute("INSERT OR REPLACE INTO metadata VALUES ('admission_sample',?)",(str(sample[0]),))
                c.execute("UPDATE jobs SET state='replacement_launching',updated=? WHERE id=?",(at,job['id']))
                c.execute("UPDATE recoveries SET phase='launching',updated=?,result=? WHERE id=?",(at,json.dumps({'launchIntent':spec,'launchIntentAt':at}),identifier))
                c.execute('INSERT INTO events(job,time,kind,detail) VALUES (?,?,?,?)',(job['id'],at,'replacement_launch_reserved',json.dumps({'recoveryId':identifier,'title':title})))
            return spec
        if action=='record':
            tid=payload.get('threadId')
            if ticket['phase']=='replaced':
                if tid!=ticket['replacement_thread']:raise Problem('This recovery already has a different replacement.')
                return ticket
            if ticket['phase'] not in ('launching','attention') or not isinstance(tid,str) or not tid:raise Problem('A replacement launch result is required.')
            if tid==ticket['worker_thread']:raise Problem('A replacement launch cannot point to the retired worker.',409)
            if ticket['replacement_thread'] and ticket['replacement_thread']!=tid:raise Problem('Never bind a second candidate to an uncertain launch.',409)
            _update(s,identifier,replacement_thread=tid,result=payload)
            selection=payload.get('modelSelection') or {};options=selection.get('options') or {}
            if isinstance(options,list):options={x['id']:x['value'] for x in options}
            requested=job['request']['selection']
            if payload.get('projectId')!=job['project_id'] or selection.get('instanceId')!=requested['instanceId'] or selection.get('model')!=requested['model'] or options!=requested['options']:
                _update(s,identifier,phase='attention',error='Returned replacement settings mismatch; retained its ID. Do not launch again.')
                s.patch(job['id'],state='attention',error='Replacement model/settings mismatch; reconcile the saved candidate.')
                raise Problem('Replacement settings mismatch; candidate saved for reconciliation.',409)
            binding=engine.replace_worker(s,job['id'],tid,expected_old=ticket['worker_thread'],reason='Configured recovery rule '+rule['id'],_locked=True,_recovery_id=identifier)
            s.patch(job['id'],launch_result={**payload,'optionsVerified':True})
            result=_update(s,identifier,phase='replaced',error=None)
            return result|{'watcher':binding['watcher']}
        if action=='cancel':
            if ticket.get('replacement_thread'):
                _retired(s,ticket['replacement_thread'])
            elif ticket['phase']=='launching':raise Problem('Reconcile the uncertain launch before cancelling recovery.',409)
            if ticket['phase'] in CLOSED and ticket['phase']!='attention':raise Problem('This recovery is already closed.')
            s.patch(job['id'],state='attention',error='Recovery cancelled; inspect the current thread before resuming.')
            return _update(s,identifier,phase='cancelled')
        raise Problem('Unknown recovery action.')
