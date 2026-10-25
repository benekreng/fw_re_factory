#!/usr/bin/env python3
"""Transactional helpers for the T3 controller. This file never launches a model."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid

from app import Store, Problem, now, sha, TERMINAL

AREAS = {'assignment','original','specification','exact','faithful','main','browser'}


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_suffix('.tmp-'+uuid.uuid4().hex)
    temp.write_text(json.dumps(value,indent=2)+'\n')
    os.chmod(temp,0o600)
    temp.replace(path)


def claim(store, identifier, resume=False):
    import engine
    store.model_selection(store.job(identifier)['request']['selection'])
    decision=next((d for d in engine.plan(store)['decisions'] if d['jobId']==identifier),None)
    if not decision or not decision['admit']:
        raise Problem(decision['reason'] if decision else 'This job is not eligible for admission.',409)
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM jobs WHERE id=?',(identifier,)).fetchone()
        if not row:
            raise Problem('Unknown job.')
        job = store.decode(row)
        if job['state'] not in (('waiting','usage_limited') if resume else ('queued',)):
            raise Problem('Already claimed; reconcile its saved state instead of launching twice.',409)
        active = c.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('preparing','prepared','launching','running','review_pending','incomplete','attention','recovery_pending','replacement_ready','replacement_launching')").fetchone()[0]
        if active >= job['request']['maxParallel']:
            raise Problem('Concurrency limit reached; leave this job queued.',409)
        c.execute('UPDATE jobs SET state=?,updated=? WHERE id=?',('running' if resume else 'preparing',now(),identifier))
        sample=c.execute("SELECT id FROM samples WHERE kind='quota' ORDER BY id DESC LIMIT 1").fetchone()
        c.execute("INSERT OR REPLACE INTO metadata VALUES ('admission_sample',?)",(str(sample[0]),))
        c.execute('INSERT INTO events(job,time,kind,detail) VALUES (?,?,?,?)',(identifier,now(),'claimed',json.dumps({'sample':sample[0]})))
    return store.job(identifier)


def render_worker_prompt(job, *, continuation=False):
    r=job['request'];destination=Path(r['destination'])
    prompt_file = Path('/home/vesper/RE/bens_skills/firmware-rewrite/START_PROMPT.md')
    skill_file = prompt_file.with_name('SKILL.md')
    policy = ('If one firmware candidate was selected, treat its version as the latest; do not search for newer firmware. '
              'If that candidate is an archive containing multiple firmware versions, inspect those candidates and use the newest appropriate version. '
              'If multiple firmware candidates were selected, identify and use the newest appropriate supplied version for this device/hardware revision. '
              'Record the selection evidence; do not choose a bootloader or incompatible hardware variant as an application update.')
    extra = '\n\nRun details (these settings override the defaults above):\n'
    extra += 'Device: '+r['name']+'\nWorkspace: '+str(destination)+'\n'
    extra += 'Skill path: '+str(skill_file)+'\n'
    extra += 'Immutable selected originals are under originals/; LAUNCH_REQUEST.json records their hashes, roles and source URLs. Inventory these paths before analysis.\n'
    extra += policy+' Continue the skill\'s device/manual research in either case.\n'
    extra += 'Target host: '+(r.get('targetHost') or 'none; use the skill defaults')+'\n'
    extra += 'Serve the browser app on port '+str(r['port'])+' and leave it running. Give a working T3 preview or clear access instructions for the server machine.\n'
    extra += 'For every completion claim, cite the relevant artifact paths, rerunnable checks and remaining limitations.\n'
    policy_sub=r.get('subagents','none')
    if policy_sub=='none':
        extra += 'User subagent policy: NO subagents or delegated agents. Perform all work yourself. This explicitly overrides the skill parallel-agent and fresh-agent integration requirements. Document this exception and perform independent integration checks yourself; do not claim unchanged full skill compliance.\n'
    elif policy_sub=='integration':
        extra += 'User subagent policy: only one fresh integration agent at a time, solely for the skill integration checks. Do not delegate reverse engineering or implementation.\n'
    else:
        extra += 'User subagent policy: allowed, with at most '+str(r['subagentCap'])+' concurrent child agents in total, including nested children.\n'
    extra += 'Selected files:\n'+'\n'.join('- '+f['kind']+': originals/'+f['path'] for f in r['files'])+'\n'
    task = prompt_file.read_text()
    if continuation:
        task = ('Another agent has already worked on this project and was assigned the task prompt below. '
                'Its work is in this directory. Continue the work from the existing project files. '
                'Inspect and verify what is already there, then complete the remaining work. '
                'The run details at the bottom apply to this task.\n\n'
                '--- BEGIN TASK PROMPT ---\n'+task+
                '\n--- END TASK PROMPT ---\n')
    prompt = task+extra
    return prompt,prompt_file,skill_file


def prepare(store, identifier):
    job = store.job(identifier)
    if job['state'] not in ('preparing','prepared','launching'):
        raise Problem('Claim this queued job before preparing it.')
    r = job['request']
    destination = Path(r['destination'])
    if not destination.resolve().is_relative_to(store.projects):
        raise Problem('Destination is outside the RE root.')
    marker = destination/'LAUNCH_REQUEST.json'
    if destination.exists():
        if not marker.is_file() or json.loads(marker.read_text()).get('id') != identifier:
            raise Problem('Destination exists without this job marker; refusing to overwrite it.',409)
    else:
        destination.mkdir(parents=True,exist_ok=False)
        atomic_json(marker,r)
    for f in r['files']:
        source = (store.firmware/f['path']).resolve()
        if not source.is_relative_to(store.firmware) or sha(source)!=f['sha256']:
            raise Problem('Source does not match the catalog hash: '+f['path'])
        target = destination/'originals'/f['path']
        if not target.resolve().is_relative_to(destination.resolve()):
            raise Problem('Original destination escaped the job directory.')
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():
            if sha(target)!=f['sha256']:
                raise Problem('A prepared original changed; refusing to overwrite it: '+str(target))
        else:
            shutil.copy2(source,target)
            if sha(target)!=f['sha256']:
                raise Problem('Copied file failed hash verification: '+f['path'])
    prompt,prompt_file,skill_file = render_worker_prompt(job)
    artifacts = store.data/'jobs'/identifier
    artifacts.mkdir(parents=True,exist_ok=True)
    (artifacts/'START_PROMPT.txt').write_text(prompt)
    (artifacts/'SKILL.md').write_bytes(skill_file.read_bytes())
    atomic_json(artifacts/'provenance.json',dict(startPromptPath=str(prompt_file),
        startPromptSha256=sha(prompt_file),skillPath=str(skill_file),skillSha256=sha(skill_file),
        generatedPromptSha256=sha(artifacts/'START_PROMPT.txt'),preparedAt=now()))
    (destination/'.gitignore').touch(exist_ok=True)
    return store.patch(identifier,state='prepared',error=None)


def record_thread(store, identifier, result):
    job = store.job(identifier)
    if job['state'] not in ('prepared','launching') or job.get('thread_id'):
        raise Problem('This job cannot record another launch.',409)
    selection = result.get('modelSelection',{})
    requested = job['request']['selection']
    got_options = selection.get('options') or {}
    if isinstance(got_options,list):
        got_options = {x['id']:x['value'] for x in got_options}
    if selection.get('instanceId')!=requested['instanceId'] or selection.get('model')!=requested['model'] or got_options!=requested['options']:
        # Save the returned ID even on mismatch; never create a second thread to fix uncertainty.
        store.patch(identifier,state='attention',thread_id=result.get('threadId'),launch_result=result,
                    error='T3 returned different model settings. Do not launch again; investigate the existing thread.')
        raise Problem('Returned model settings do not match the authorized request.')
    if not result.get('threadId') or result.get('projectId')!=job['project_id']:
        store.patch(identifier,state='attention',thread_id=result.get('threadId'),launch_result=result,
                    error='Launch response project mismatch or missing thread ID. Reconcile before further action.')
        raise Problem('Returned thread/project is incomplete or does not match.')
    return store.patch(identifier,state='running',thread_id=result['threadId'],launch_result=result,error=None,observation={'launchedAt':now()})


def record_review(store, identifier, review):
    job = store.job(identifier)
    if job['state'] in ('recovery_pending','replacement_ready','replacement_launching'):
        raise Problem('Resolve the active thread recovery before reviewing completion.',409)
    if not job.get('thread_id'):
        raise Problem('This job has no worker thread.')
    verdict = review.get('verdict')
    if verdict not in ('verified_complete','incomplete','review_pending'):
        raise Problem('Invalid review verdict.')
    if not review.get('summary') or not review.get('commit'):
        raise Problem('Record a summary and reviewed commit.')
    if verdict=='verified_complete':
        if set(review.get('areas',{}))!=AREAS or not all(review['areas'].values()):
            raise Problem('Completion needs evidence for every required deliverable area.')
        with store.connect() as c:
            cutoff=(job.get('observation') or {}).get('monitoringSince','')
            checks = c.execute('SELECT * FROM checks WHERE job=? AND time>=?',(identifier,cutoff)).fetchall()
        if not checks or not any(x['exit_code']==0 and x['commit_sha']==review['commit'] for x in checks):
            raise Problem('Record at least one successfully rerun check before verifying completion.')
        if review.get('findings'):
            raise Problem('Unresolved findings prevent verified completion.')
        destination=job['request']['destination']
        current=subprocess.run(['git','rev-parse','HEAD'],cwd=destination,text=True,capture_output=True,check=True).stdout.strip()
        if current!=review['commit']:
            raise Problem('Reviewed commit is not the current checkout.')
        if subprocess.run(['git','diff','--quiet','HEAD'],cwd=destination).returncode:
            raise Problem('Tracked work differs from the reviewed commit.')
        if any(sha(x['log_path'])!=x['log_sha256'] for x in checks):
            raise Problem('A recorded check log changed.')
    review['reviewedAt']=now()
    return store.patch(identifier,state=verdict,review=review,error=None)


def run_check(store, identifier, command, owner, epoch):
    import engine
    with engine.lock(store):
        engine.fence(store,owner,epoch)
        job=store.job(identifier)
    if command and command[0]=='--':command=command[1:]
    if not command: raise Problem('Specify a check command after --.')
    check_id=str(uuid.uuid4())
    path=store.data/'jobs'/identifier/('check-'+check_id+'.log')
    path.parent.mkdir(parents=True,exist_ok=True)
    destination=job['request']['destination']
    head_before=subprocess.run(['git','rev-parse','HEAD'],cwd=destination,text=True,capture_output=True).stdout.strip()
    clean_before=subprocess.run(['git','diff','--quiet','HEAD'],cwd=destination).returncode==0
    with path.open('w') as log:
        try:
            run=subprocess.run(command,cwd=job['request']['destination'],stdout=log,stderr=subprocess.STDOUT,timeout=600)
            code=run.returncode
        except subprocess.TimeoutExpired:code=124
    with engine.lock(store), store.connect() as c:
        engine.fence(store,owner,epoch)
        head=subprocess.run(['git','rev-parse','HEAD'],cwd=job['request']['destination'],text=True,capture_output=True).stdout.strip()
        if head!=head_before or not clean_before or subprocess.run(['git','diff','--quiet','HEAD'],cwd=destination).returncode:
            head=''
        c.execute('INSERT INTO checks(id,job,time,command,exit_code,log_path,log_sha256,commit_sha) VALUES (?,?,?,?,?,?,?,?)',
                  (check_id,identifier,now(),json.dumps(command),code,str(path),sha(path),head))
    return {'checkId':check_id,'exitCode':code,'logPath':str(path),'sha256':sha(path)}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--owner', required=True)
    p.add_argument('--epoch', required=True, type=int)
    p.add_argument('action',choices=['pending','jobs','job','claim','resume','prepare','project','launching','thread','observe','review','check','catalog','attention','recovery'])
    p.add_argument('id',nargs='?')
    p.add_argument('command',nargs=argparse.REMAINDER)
    args=p.parse_args()
    store=Store()
    import engine
    if args.action=='recovery':
        import recovery
        try:
            payload=json.load(sys.stdin) if args.id in ('policy_set','report','repair_result','record') else None
            result=recovery.dispatch(store,args.owner,args.epoch,args.id,args.command[0] if args.command else None,payload)
            print(json.dumps(result,indent=2))
        except (Problem,OSError,ValueError) as e:
            print(json.dumps({'error':str(e)}),file=sys.stderr);raise SystemExit(1)
        return
    if args.action=='check':
        try:
            print(json.dumps(run_check(store,args.id,args.command,args.owner,args.epoch),indent=2))
        except (Problem,OSError,ValueError) as e:
            print(json.dumps({'error':str(e)}),file=sys.stderr);raise SystemExit(1)
        return
    with engine.lock(store):
        try:
            engine.fence(store,args.owner,args.epoch)
            if args.action=='pending': result=[j for j in store.jobs() if j['state'] not in TERMINAL]
            elif args.action=='jobs': result=store.jobs()
            elif args.action=='job': result=store.job(args.id)
            elif args.action=='claim': result=claim(store,args.id)
            elif args.action=='resume': result=claim(store,args.id,resume=True)
            elif args.action=='prepare': result=prepare(store,args.id)
            elif args.action=='project':
                payload=json.load(sys.stdin)
                if not payload.get('projectId'): raise Problem('A project ID is required.')
                if store.job(args.id)['state']!='prepared':raise Problem('Prepare this job before registering its project.')
                result=store.patch(args.id,project_id=payload['projectId'])
            elif args.action=='launching':
                job=store.job(args.id)
                if job['state']!='prepared' or not job['project_id'] or job['thread_id']:
                    raise Problem('Only a prepared job with its project recorded can start a launch.')
                engine.require_launch_approval(store,job)
                result=store.patch(args.id,state='launching')
            elif args.action=='thread': result=record_thread(store,args.id,json.load(sys.stdin))
            elif args.action=='observe':
                payload=json.load(sys.stdin)
                state=payload.pop('state')
                if store.job(args.id)['state'] in TERMINAL:raise Problem('A finished job cannot be resurrected by an observation.')
                if store.job(args.id)['state'] in ('recovery_pending','replacement_ready','replacement_launching'):
                    raise Problem('Use the recovery helpers while thread recovery is active.',409)
                if state not in ('running','usage_limited','waiting','attention','review_pending','incomplete'):
                    raise Problem('Invalid observed state.')
                result=store.patch(args.id,state=state,observation={**(store.job(args.id).get('observation') or {}),**payload,'observedAt':now()})
            elif args.action=='review': result=record_review(store,args.id,json.load(sys.stdin))
            elif args.action=='catalog':
                payload=json.load(sys.stdin)
                assert isinstance(payload.get('providers'),list)
                payload['fetchedAt']=now()
                atomic_json(store.data/'capabilities.json',payload)
                result={'updatedAt':payload['fetchedAt']}
            elif args.action=='attention': result=store.patch(args.id,state='attention',error=sys.stdin.read()[:3000])
            print(json.dumps(result,indent=2))
        except (Problem,AssertionError) as e:
            print(json.dumps({'error':str(e)}),file=sys.stderr)
            raise SystemExit(1)



if __name__=='__main__': main()
