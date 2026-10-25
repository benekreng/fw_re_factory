#!/usr/bin/env python3
"""Terminal entrypoint for the firmware RE queue and launch controls."""
import argparse
import json
from pathlib import Path
import re
import sys
import uuid

from app import Store, Problem
from controller import atomic_json
import engine


def choose(label, choices, default=0):
    print('\n'+label)
    for i,(name,_) in enumerate(choices,1):print(f'  {i}. {name}')
    while True:
        answer=input(f'Choice [{default+1}]: ').strip()
        if not answer:return choices[default][1]
        if answer.isdigit() and 1<=int(answer)<=len(choices):return choices[int(answer)-1][1]
        print('Choose one of the numbered options.')


def selection(s):
    cap=s.capabilities()
    providers=[p for p in cap['providers'] if not p.get('constraints') and p['models']]
    p=choose('Worker provider',[(p.get('displayName') or p['providerInstanceId'],p) for p in providers])
    m=choose('Worker model',[(m.get('label') or m['id'],m) for m in p['models']])
    options={}
    for d in m.get('options') or []:
        if d['type']=='boolean':options[d['id']]=choose(d['label'],[('Off',False),('On',True)])
        else:
            vals=d['options'];default=next((i for i,v in enumerate(vals) if v.get('isDefault')),0)
            options[d['id']]=choose(d['label'],[(v.get('label') or v['id'],v['id']) for v in vals],default)
    return s.model_selection({'instanceId':p['providerInstanceId'],'model':m['id'],'options':options})


def wizard(s):
    print('Firmware RE queue · select supplied files, exact worker settings and subagent policy.')
    print('Firmware root: '+str(s.firmware)+'\nCatalog timestamp: '+s.capabilities().get('fetchedAt','unknown'))
    selected=selection(s)
    pool=choose('Actual quota pool (custom proxy models may use a different pool)',[('Codex','codex'),('Claude','claude')],1 if selected['instanceId']=='claudeAgent' else 0)
    sub=choose('Subagents',[('None (document exception to fresh-agent integration)','none'),('Integration checks only, one at a time','integration'),('Allowed with a concurrency cap','capped')])
    cap=0
    if sub=='capped':cap=int(input('Maximum concurrent child agents, 1–8 [2]: ').strip() or '2')
    groups=[]
    while True:
        name=input('\nDevice name: ').strip()
        suggested=re.sub(r'[^a-z0-9_-]+','-',name.lower()).strip('-')
        slug=input('Directory name ['+suggested+']: ').strip() or suggested
        print('Enter one firmware/reference path per line. Blank line ends the group.')
        print('Prefix reference files with ref: . Paths may be absolute or relative to the firmware root.')
        files=[]
        while True:
            value=input('Path: ').strip().strip('"').strip("'")
            if not value:break
            kind='reference' if value.startswith('ref:') else 'firmware'
            if value.startswith('ref:'):value=value[4:].strip()
            p=Path(value).expanduser()
            if p.is_absolute():
                try:value=str(p.resolve().relative_to(s.firmware))
                except ValueError:raise Problem('Firmware paths must be in '+str(s.firmware))
            files.append({'path':value,'kind':kind})
        groups.append({'name':name,'slug':slug,'files':files,'subagents':sub,'subagentCap':cap,'quotaPool':pool,
                       'targetHost':input('Target host/architecture [skill default]: ').strip()})
        if input('Add another device with these settings? [y/N]: ').lower()!='y':break
    maximum=int(input('Maximum concurrent workers, 1–4 [2]: ').strip() or '2')
    launch_mode=choose('Launch mode',[('Manual: queue jobs, then click Launch','manual'),('Automatic: start queued jobs when eligible','automatic')],
                       1 if s.config().get('launchMode')=='automatic' else 0)
    usage_policy=choose('Usage scheduling policy',[('Short window first (weekly exhaustion still blocks)','short-window'),('Balance all windows','balanced'),('Weekly budget first','weekly-first')])
    wake_seconds=int(input('Watcher wake interval in seconds, 60–3600 [240]: ').strip() or '240')
    pause_policy=input('Allow cooperative checkpoint pauses when usage forecasts exceed budget? [y/N]: ').lower()=='y'
    if not 60<=wake_seconds<=3600:raise Problem('Wake interval must be 60–3600 seconds.')
    payload={'requestId':str(uuid.uuid4()),'selection':selected,'groups':groups,'maxParallel':maximum}
    # Validate everything before offering to submit, including device collisions.
    for g in groups:s.validate_group(g,selected)
    print('\nApproved backlog request:\n'+json.dumps(payload,indent=2))
    print(f'Controller: {usage_policy}; watcher wakes every {wake_seconds} seconds; checkpoint pauses: {pause_policy}. Cache retention is measured, not guaranteed.')
    print('Jobs wait for your Launch approval.' if launch_mode=='manual' else 'Jobs can start automatically when usage and concurrency policy permit.')
    if input('Queue and notify the watcher? [y/N]: ').lower()!='y':return {'queued':False}
    with engine.lock(s):
        jobs=s.enqueue(payload)
        config=s.config();config.update(launchMode=launch_mode,maxParallel=maximum,policy=usage_policy,wakeSeconds=wake_seconds,sampleSeconds=wake_seconds,checkpointPauses=pause_policy);atomic_json(s.data/'config.json',config)
        engine.bootstrap(s)
    return {'jobs':[j['id'] for j in jobs],'watcher':engine.owner(s),'notification':engine.wake(s,'approved queue changed',force=True)}


def configure(s, key, value):
    if key=='launchMode':return engine.set_launch_mode(s,value)
    integers={'wakeSeconds':(60,3600),'sampleSeconds':(60,3600),'tokenSeconds':(60,3600),'maxParallel':(1,4),'quotaFreshSeconds':(60,3600),'usageRetrySeconds':(60,86400)}
    if key in integers:
        value=int(value);lo,hi=integers[key]
        if not lo<=value<=hi:raise Problem(f'{key} must be between {lo} and {hi}.')
    elif key=='policy':
        if value not in ('short-window','balanced','weekly-first'):raise Problem('Unknown usage policy.')
    elif key in ('checkpointPauses','schedulingPaused','watcherMessagesPaused'):
        if value not in ('true','false'):raise Problem('Use true or false.')
        value=value=='true'
    else:raise Problem('This setting is not editable here.')
    with engine.lock(s):
        conf=s.config();conf[key]=value;atomic_json(s.data/'config.json',conf)
    return {key:value}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,help=argparse.SUPPRESS)
    subs=p.add_subparsers(dest='action')
    subs.add_parser('wizard')
    for cmd in ('status','plan','sample','tokens','daemon','catalog'):subs.add_parser(cmd)
    q=subs.add_parser('enqueue');q.add_argument('request',type=Path);q.add_argument('--no-dispatch',action='store_true')
    q=subs.add_parser('cancel');q.add_argument('id')
    q=subs.add_parser('launch',help='Approve one job to launch; use only on explicit operator request.')
    q.add_argument('id',help='Job ID or device directory name.')
    q=subs.add_parser('worker');worker=q.add_subparsers(dest='operation',required=True)
    q=worker.add_parser('replace',help='Adopt an existing replacement after archiving the old worker.')
    q.add_argument('job',help='Job ID or device directory name.');q.add_argument('thread')
    q.add_argument('--expected-old');q.add_argument('--reason',default='User supplied a replacement worker')
    q=subs.add_parser('recovery');q.add_argument('operation',choices=['policy','set-policy','disable','list','show']);q.add_argument('value',nargs='?')
    q=subs.add_parser('watcher');q.add_argument('operation',choices=['show','set','create','adopt','wake']);q.add_argument('thread',nargs='?')
    q=subs.add_parser('config');q.add_argument('key',nargs='?');q.add_argument('value',nargs='?')
    q=subs.add_parser('dashboard');q.add_argument('--port',type=int,default=8787)
    q.add_argument('--host',default='127.0.0.1',help='Address to bind, such as the Tailscale IPv4 address.')
    args=p.parse_args();s=Store(root=args.root,firmware=args.root/'firmware',projects=args.root/'RE') if args.root else Store()
    try:
        if args.action in (None,'wizard'):result=wizard(s)
        elif args.action=='status':
            usage=engine.latest(s,'quota');tokens=engine.latest(s,'tokens')
            result={'watcher':engine.owner(s),'policy':s.config().get('policy'),'usageSampleAt':usage.get('time') if usage else None,
                    'tokenSampleAt':tokens.get('time') if tokens else None,
                    'jobs':[{k:j.get(k) for k in ('id','name','state','thread_id','error')} for j in s.jobs()]}
        elif args.action=='plan':result=engine.plan(s)
        elif args.action=='sample':result=engine.sample_usage(s)
        elif args.action=='tokens':result=engine.sample_tokens(s)
        elif args.action=='catalog':result=list(s.catalog.values())
        elif args.action=='config':result=configure(s,args.key,args.value) if args.key and args.value is not None else s.config()
        elif args.action=='launch':
            result={'approval':engine.request_launch(s,args.id),'notification':engine.notify_launch(s)}
        elif args.action=='enqueue':
            with engine.lock(s):
                jobs=s.enqueue(json.loads(args.request.read_text()))
                if not args.no_dispatch:engine.bootstrap(s)
            result={'jobs':jobs}
            if not args.no_dispatch:result['notification']=engine.wake(s,'approved queue changed',force=True)
        elif args.action=='cancel':
            with engine.lock(s):
                if s.job(args.id)['state']!='queued':raise Problem('Only an unstarted queued job can be cancelled here.')
                result=s.patch(args.id,state='cancelled')
        elif args.action=='watcher':
            if args.operation=='show':result=engine.owner(s)
            elif args.operation=='wake':result=engine.wake(s,'manual check',force=True)
            else:
                with engine.lock(s):
                    if args.operation=='adopt':
                        current=engine.owner(s)
                        if current['thread'] and current['thread']!=args.thread:raise Problem('A watcher already exists. Only the user can switch it with `watcher set`.')
                        if current['thread']==args.thread:result=current
                        else:result=engine.set_owner(s,args.thread)
                    else:result=engine.bootstrap(s) if args.operation=='create' else engine.set_owner(s,args.thread)
                if args.operation=='set':result['notification']=engine.wake(s,'watcher handover; load all existing jobs',force=True)
        elif args.action=='worker':
            result=engine.replace_worker(s,args.job,args.thread,args.expected_old,args.reason)
            if result['changed']:
                result['notification']=engine.wake(s,'Worker replaced: use the current registered thread and project files only; ignore retired conversation history; do not send handover messages to the replacement worker.',force=True)
        elif args.action=='recovery':
            import recovery
            if args.operation=='set-policy' and not args.value:raise Problem('Specify the recovery-policy JSON file.')
            if args.operation=='policy':result=recovery.policy(s)
            elif args.operation=='list':result=recovery.records(s)
            elif args.operation=='show':result=recovery.get(s,args.value)
            else:
                with engine.lock(s):
                    value=json.loads(Path(args.value).read_text()) if args.operation=='set-policy' else {**recovery.policy(s),'enabled':False}
                    result=recovery.set_policy(s,value)
        elif args.action=='daemon':engine.daemon(s);return
        elif args.action=='dashboard':
            from dashboard import serve
            serve(s,args.port,args.host);return
        print(json.dumps(result,indent=2))
    except (Problem,ValueError,OSError,KeyError) as e:
        print(json.dumps({'error':str(e)}),file=sys.stderr);raise SystemExit(1)
    except (KeyboardInterrupt,EOFError):print('\nCancelled.',file=sys.stderr);raise SystemExit(130)


if __name__=='__main__':main()
