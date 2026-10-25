import copy
import datetime as dt
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Store,Problem,now,sha
from controller import claim,prepare,record_thread,record_review,atomic_json,run_check
import engine
import dashboard


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        fw=self.root/'firmware';fw.mkdir();(fw/'vendor').mkdir()
        self.file=fw/'vendor/fw.bin';self.file.write_bytes(b'firmware-test')
        (fw/'MANIFEST.sha256').write_text(sha(self.file)+'  ./vendor/fw.bin\n')
        self.s=Store(self.root,firmware=fw,projects=self.root/'RE')
        self.selection={'instanceId':'codex','model':'test','options':{'effort':'max','fast':False}}
        atomic_json(self.s.data/'capabilities.json',{'providers':[{'providerInstanceId':'codex','constraints':[], 'models':[{'id':'test','options':[{'id':'effort','label':'Effort','type':'select','options':[{'id':'max'}]},{'id':'fast','label':'Fast','type':'boolean'}]}]}]})
        atomic_json(self.s.data/'config.json',{'maxParallel':3,'watcherProjectId':'project','wakeSeconds':240})
        self.payload={'requestId':str(uuid.uuid4()),'selection':self.selection,'maxParallel':3,'groups':[{'name':'Test device','slug':'test-device','files':[{'path':'vendor/fw.bin','kind':'firmware'}],'quotaPool':'codex','subagents':'none','subagentCap':0}]}
    def tearDown(self):self.tmp.cleanup()
    def enqueue(self):return self.s.enqueue(copy.deepcopy(self.payload))[0]
    def quota(self,used=10,at=None,reset=None,provider='codex'):
        at=at or now();reset=reset or dt.datetime.fromtimestamp(time.time()+3600,dt.timezone.utc).isoformat()
        usage={'rate_limit':{'primary_window':{'used_percent':used,'reset_at':reset,'limit_window_seconds':18000}}} if provider=='codex' else {'five_hour':{'utilization':used,'resets_at':reset}}
        return engine.ingest(self.s,{'ok':True,'checked_at':at,'accounts':[{'name':'account','auth_index':'stable','provider':provider,'disabled':False,'usage':usage,'errors':[]}]})
    def test_enqueue_retry_survives_created_destination(self):
        j=self.enqueue();(self.s.projects/'test-device').mkdir(parents=True)
        self.assertEqual(self.enqueue()['id'],j['id'])
        self.assertEqual(len(self.s.jobs()),1)
    def test_request_id_conflict(self):
        self.enqueue();self.payload['maxParallel']=2
        with self.assertRaises(Problem):self.enqueue()
    def test_reject_unknown_path(self):
        self.payload['groups'][0]['files'][0]['path']='../../outside.bin'
        with self.assertRaises(Problem):self.enqueue()
    def test_require_every_exact_model_option(self):
        del self.payload['selection']['options']['fast']
        with self.assertRaises(Problem):self.enqueue()
    def test_reject_fake_boolean(self):
        self.payload['selection']['options']['fast']='false'
        with self.assertRaises(Problem):self.enqueue()
    def test_no_claim_with_unknown_usage(self):
        j=self.enqueue()
        with self.assertRaises(Problem):claim(self.s,j['id'])
        self.assertEqual(self.s.job(j['id'])['state'],'queued')
    def test_one_admission_per_snapshot(self):
        j=self.enqueue();self.quota();claim(self.s,j['id'])
        self.payload['requestId']=str(uuid.uuid4());self.payload['groups'][0]['slug']='other-device'
        other=self.enqueue()
        with self.assertRaises(Problem):claim(self.s,other['id'])
    def test_prepare_idempotence_and_user_override(self):
        j=self.enqueue();self.quota();claim(self.s,j['id']);prepare(self.s,j['id']);prepare(self.s,j['id'])
        copied=Path(j['request']['destination'])/'originals/vendor/fw.bin'
        self.assertEqual(sha(copied),sha(self.file))
        prompt=(self.s.data/'jobs'/j['id']/'START_PROMPT.txt').read_text()
        self.assertIn('NO subagents',prompt);self.assertIn('do not search for newer firmware',prompt)
    def test_original_tampering_blocks_prepare(self):
        j=self.enqueue();self.quota();claim(self.s,j['id']);self.file.write_bytes(b'changed')
        with self.assertRaises(Problem):prepare(self.s,j['id'])
    def test_destination_symlink_escape(self):
        j=self.enqueue();self.quota();claim(self.s,j['id']);prepare(self.s,j['id'])
        orig=Path(j['request']['destination'])/'originals/vendor/fw.bin';orig.unlink()
        outside=self.root/'outside';outside.write_text('x');orig.symlink_to(outside)
        with self.assertRaises(Problem):prepare(self.s,j['id'])
    def test_model_mismatch_retains_thread_to_prevent_duplicate(self):
        j=self.enqueue();self.s.patch(j['id'],state='launching',project_id='p')
        result={'threadId':str(uuid.uuid4()),'projectId':'p','modelSelection':{**self.selection,'options':{'effort':'max','fast':True}}}
        with self.assertRaises(Problem):record_thread(self.s,j['id'],result)
        saved=self.s.job(j['id']);self.assertEqual(saved['thread_id'],result['threadId']);self.assertEqual(saved['state'],'attention')
        with self.assertRaises(Problem):record_thread(self.s,j['id'],result)
    def test_project_mismatch_also_retains_id(self):
        j=self.enqueue();self.s.patch(j['id'],state='launching',project_id='p')
        result={'threadId':str(uuid.uuid4()),'projectId':'wrong','modelSelection':self.selection}
        with self.assertRaises(Problem):record_thread(self.s,j['id'],result)
        self.assertEqual(self.s.job(j['id'])['thread_id'],result['threadId'])
    def test_exact_launch_options_array(self):
        j=self.enqueue();self.s.patch(j['id'],state='launching',project_id='p')
        sel={**self.selection,'options':[{'id':k,'value':v} for k,v in self.selection['options'].items()]}
        result=record_thread(self.s,j['id'],{'threadId':str(uuid.uuid4()),'projectId':'p','modelSelection':sel})
        self.assertEqual(result['state'],'running')
    def test_review_claim_without_checks_is_rejected(self):
        j=self.enqueue();self.s.patch(j['id'],thread_id=str(uuid.uuid4()))
        r={'verdict':'verified_complete','summary':'claimed done','commit':'a'*40,'areas':{k:'claimed' for k in ['assignment','original','specification','exact','faithful','main','browser']},'findings':[]}
        with self.assertRaises(Problem):record_review(self.s,j['id'],r)
    def test_handover_fences_previous_owner(self):
        first,second=str(uuid.uuid4()),str(uuid.uuid4())
        def show(*args,**kwargs):return json.dumps({'thread':{'id':args[2]}})
        with patch.object(self.s,'cli',side_effect=show):
            a=engine.set_owner(self.s,first);engine.fence(self.s,first,a['epoch'])
            b=engine.set_owner(self.s,second)
        self.assertGreater(b['epoch'],a['epoch'])
        with self.assertRaises(Problem):engine.fence(self.s,first,a['epoch'])
        engine.fence(self.s,second,b['epoch'])
    def test_app_owned_thread_id_is_accepted_without_stripping_prefix(self):
        tid='mcp:'+str(uuid.uuid4())
        with patch.object(self.s,'cli',return_value=json.dumps({'thread':{'threadId':tid}})):
            result=engine.set_owner(self.s,tid)
        self.assertEqual(result['thread'],tid)
        engine.fence(self.s,tid,result['epoch'])

    def test_invalid_handover_preserves_owner(self):
        with self.s.connect() as c:c.execute("UPDATE watcher SET thread='original',epoch=5,phase='ready'")
        with patch.object(self.s,'cli',return_value=json.dumps({'thread':{'id':'wrong'}})):
            with self.assertRaises(Problem):engine.set_owner(self.s,str(uuid.uuid4()))
        self.assertEqual(engine.owner(self.s)['thread'],'original')
    def test_uncertain_bootstrap_is_not_repeated(self):
        with patch.object(self.s,'cli',side_effect=Problem('timeout')) as call:
            with self.assertRaises(Problem):engine.bootstrap(self.s)
            with self.assertRaises(Problem):engine.bootstrap(self.s)
            self.assertEqual(call.call_count,1)
    def test_wake_does_not_queue_status_requests_to_busy_watcher(self):
        with self.s.connect() as c:c.execute("UPDATE watcher SET thread=?,epoch=1,phase='ready'",(str(uuid.uuid4()),))
        with patch.object(self.s,'cli',return_value=json.dumps({'thread':{'activeRunId':'run'}})) as call:
            self.assertFalse(engine.wake(self.s)['sent']);self.assertEqual(call.call_count,1)
    def test_wake_timeout_is_coalesced(self):
        with self.s.connect() as c:c.execute("UPDATE watcher SET thread=?,epoch=1,phase='ready'",(str(uuid.uuid4()),))
        with patch.object(self.s,'cli',side_effect=[json.dumps({'thread':{}}),Problem('timeout')]) as call:
            with self.assertRaises(Problem):engine.wake(self.s)
            self.assertEqual(engine.wake(self.s)['reason'],'coalesced');self.assertEqual(call.call_count,2)
    def test_usage_error_replaces_freshness_not_with_zero(self):
        self.quota(37)
        with patch('engine.subprocess.run',side_effect=OSError('offline')):engine.sample_usage(self.s)
        self.assertFalse(engine.plan(self.s)['quotaFresh']);self.assertEqual(engine.latest(self.s,'quota')['accounts'],[])
        with self.s.connect() as c:self.assertEqual(c.execute('SELECT used FROM quota').fetchone()[0],37)
    def test_camel_case_codex_and_null_unknown(self):
        w=engine.windows({'provider':'codex','usage':{'rateLimit':{'primaryWindow':{'usedPercent':22,'resetAt':int(time.time()+2000),'limitWindowSeconds':18000},'secondaryWindow':{'usedPercent':None}}}})
        self.assertEqual(len(w),1);self.assertEqual(w[0]['used'],22)
    def test_hard_weekly_exhaustion_blocks_short_window_policy(self):
        self.payload['groups'][0]['quotaPool']='claude';self.enqueue()
        engine.ingest(self.s,{'checked_at':now(),'ok':True,'accounts':[{'provider':'claude','auth_index':'x','errors':[],'usage':{'five_hour':{'utilization':0},'seven_day':{'utilization':100}}}]})
        self.assertFalse(engine.plan(self.s)['decisions'][0]['admit'])
    def test_forecast_replenishment_is_not_asserted_free_reset(self):
        reset=dt.datetime.fromtimestamp(time.time()+3600,dt.timezone.utc).isoformat()
        for mins,used in [(-15,50),(-10,60),(-5,20),(0,21)]:self.quota(used,dt.datetime.fromtimestamp(time.time()+mins*60,dt.timezone.utc).isoformat(),reset)
        f=engine.forecast(self.s)[0]
        self.assertEqual(f['intervals'],1);self.assertIn('possible',f['resetEvent']['kind']);self.assertEqual(round(f['ratePerHour']),12)
    def test_scheduled_reset_classification(self):
        boundary=dt.datetime.fromtimestamp(time.time()-100,dt.timezone.utc).isoformat()
        self.quota(99,dt.datetime.fromtimestamp(time.time()-200,dt.timezone.utc).isoformat(),boundary)
        self.quota(1,now(),dt.datetime.fromtimestamp(time.time()+18000,dt.timezone.utc).isoformat())
        self.assertEqual(engine.forecast(self.s)[0]['resetEvent']['kind'],'scheduled reset')
    def test_cumulative_token_reports_not_summed(self):
        fake={'sessions':[],'totals':{'totalTokens':123}}
        with patch('engine.subprocess.run',return_value=subprocess.CompletedProcess([],0,json.dumps(fake),'')):
            engine.sample_tokens(self.s);engine.sample_tokens(self.s)
        self.assertEqual(engine.latest(self.s,'tokens')['codex']['report']['totals']['totalTokens'],123)
    def test_limited_worker_resume_reserves_capacity(self):
        j=self.enqueue();self.s.patch(j['id'],state='usage_limited',thread_id=str(uuid.uuid4()))
        self.quota(10)
        decision=engine.plan(self.s)['decisions'][0]
        self.assertEqual(decision['action'],'resume');self.assertTrue(decision['admit'])
        self.assertEqual(claim(self.s,j['id'],resume=True)['state'],'running')
    def test_reported_future_reset_blocks_resume(self):
        j=self.enqueue();self.s.patch(j['id'],state='usage_limited',observation={'resetAt':dt.datetime.fromtimestamp(time.time()+600,dt.timezone.utc).isoformat()})
        self.quota(1)
        with self.assertRaises(Problem):claim(self.s,j['id'],resume=True)
    def test_checkpoint_pause_is_opt_in_and_keeps_a_worker_running(self):
        a=self.enqueue();self.s.patch(a['id'],state='running')
        self.payload['requestId']=str(uuid.uuid4());self.payload['groups'][0]['slug']='second-worker'
        b=self.enqueue();self.s.patch(b['id'],state='running')
        reset=dt.datetime.fromtimestamp(time.time()+3*3600,dt.timezone.utc).isoformat()
        for mins,used in [(-20,10),(-15,15),(-10,20),(-5,25),(0,30)]:
            self.quota(used,dt.datetime.fromtimestamp(time.time()+mins*60,dt.timezone.utc).isoformat(),reset)
        request=engine.plan(self.s)['checkpointRequests'][0]
        self.assertFalse(request['enabled']);self.assertEqual(request['jobId'],b['id'])
        conf=self.s.config();conf['checkpointPauses']=True;atomic_json(self.s.data/'config.json',conf)
        self.assertTrue(engine.plan(self.s)['checkpointRequests'][0]['enabled'])
        self.s.patch(b['id'],state='waiting',observation={'checkpointPaused':True,'resetAt':reset})
        self.assertEqual(engine.plan(self.s)['checkpointRequests'],[])
    def review_fixture(self):
        j=self.enqueue();dest=Path(j['request']['destination']);dest.mkdir(parents=True)
        def git(*args):return subprocess.run(['git',*args],cwd=dest,text=True,capture_output=True,check=True).stdout.strip()
        git('init','-q');(dest/'source.c').write_text('int main(void){return 0;}')
        git('add','source.c');git('-c','user.name=Test','-c','user.email=test@local.invalid','commit','-qm','Fixture')
        head=git('rev-parse','HEAD');self.s.patch(j['id'],thread_id=str(uuid.uuid4()),state='review_pending')
        log=self.root/'check.log';log.write_text('Independent check passed\n')
        with self.s.connect() as c:
            c.execute('INSERT INTO checks(id,job,time,command,exit_code,log_path,log_sha256,commit_sha) VALUES (?,?,?,?,?,?,?,?)',
                ('check',j['id'],now(),'["check"]',0,str(log),sha(log),head))
        review={'verdict':'verified_complete','summary':'Fixture source and browser audit evidence','commit':head,
                'areas':{k:'specific evidence path' for k in ['assignment','original','specification','exact','faithful','main','browser']},'findings':[]}
        return j,review,log,dest
    def test_complete_review_requires_current_clean_commit_and_log(self):
        j,r,log,dest=self.review_fixture()
        self.assertEqual(record_review(self.s,j['id'],r)['state'],'verified_complete')
    def test_stale_check_commit_rejected(self):
        j,r,log,dest=self.review_fixture()
        with self.s.connect() as c:c.execute("UPDATE checks SET commit_sha='old'")
        with self.assertRaises(Problem):record_review(self.s,j['id'],r)
    def test_tampered_check_log_rejected(self):
        j,r,log,dest=self.review_fixture();log.write_text('changed')
        with self.assertRaises(Problem):record_review(self.s,j['id'],r)
    def test_dirty_reviewed_source_rejected(self):
        j,r,log,dest=self.review_fixture();(dest/'source.c').write_text('int main(void){return 1;}')
        with self.assertRaises(Problem):record_review(self.s,j['id'],r)

    def test_rerun_check_captures_current_commit(self):
        j,r,log,dest=self.review_fixture();owner=str(uuid.uuid4())
        with self.s.connect() as c:c.execute("UPDATE watcher SET thread=?,epoch=1,phase='ready'",(owner,))
        result=run_check(self.s,j['id'],[sys.executable,'-c','print("independent test")'],owner,1)
        self.assertEqual(result['exitCode'],0)
        with self.s.connect() as c:
            check=c.execute('SELECT * FROM checks WHERE id=?',(result['checkId'],)).fetchone()
            self.assertEqual(check['commit_sha'],r['commit']);self.assertEqual(check['log_sha256'],sha(result['logPath']))
    def test_handover_during_check_prevents_old_owner_recording(self):
        j,r,log,dest=self.review_fixture();owner=str(uuid.uuid4());newowner=str(uuid.uuid4())
        with self.s.connect() as c:c.execute("UPDATE watcher SET thread=?,epoch=1,phase='ready'",(owner,))
        actual=subprocess.run
        def run(command,**kwargs):
            if command[0]=='simulate-handover':
                with self.s.connect() as c:c.execute('UPDATE watcher SET thread=?,epoch=2',(newowner,))
                return subprocess.CompletedProcess(command,0)
            return actual(command,**kwargs)
        with patch('controller.subprocess.run',side_effect=run):
            with self.assertRaises(Problem):run_check(self.s,j['id'],['simulate-handover'],owner,1)
        with self.s.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM checks').fetchone()[0],1)
    def test_simultaneous_request_retries_create_one_job(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids=list(pool.map(lambda _:self.enqueue()['id'],range(2)))
        self.assertEqual(ids[0],ids[1]);self.assertEqual(len(self.s.jobs()),1)

    def test_wizard_bootstraps_once_and_reuses_existing_watcher(self):
        from cli import wizard
        from contextlib import redirect_stdout
        from io import StringIO
        tid=str(uuid.uuid4());starts=[]
        def t3(*args,**kwargs):
            if args[1]=='start':starts.append(args);return json.dumps({'thread':{'threadId':tid}})
            if args[1]=='show':return json.dumps({'thread':{'threadId':tid}})
            if args[1]=='send':return json.dumps({'accepted':True})
            self.fail('Unexpected T3 operation')
        with patch.object(self.s,'cli',side_effect=t3):
            for n in (1,2):
                answers=['','','','','','',f'Wizard {n}',f'wizard-{n}','vendor/fw.bin','','','n','2','','240','','y']
                with patch('builtins.input',side_effect=answers),redirect_stdout(StringIO()):result=wizard(self.s)
                self.assertTrue(result['notification']['sent'])
        self.assertEqual(len(starts),1);self.assertEqual(len(self.s.jobs()),2)
        self.assertEqual(engine.owner(self.s)['thread'],tid)
        self.assertTrue(all(j['state']=='queued' for j in self.s.jobs()))
    def test_user_scheduling_pause_blocks_an_eligible_job(self):
        j=self.enqueue();self.quota(1);conf=self.s.config();conf['schedulingPaused']=True
        atomic_json(self.s.data/'config.json',conf)
        self.assertIn('paused',engine.plan(self.s)['decisions'][0]['reason'])
        with self.assertRaises(Problem):claim(self.s,j['id'])

    def test_quota_decrease_is_persisted_with_uncertainty(self):
        self.quota(80);self.quota(20)
        with self.s.connect() as c:events=c.execute("SELECT detail FROM events WHERE kind='quota_decreased'").fetchall()
        self.assertEqual(len(events),1);self.assertIn('possible provider replenishment',events[0][0])
    def test_voucher_count_changes_are_persisted(self):
        for count in (1,2):
            engine.ingest(self.s,{'checked_at':now(),'accounts':[{'provider':'codex','auth_index':'x','usage':{},'resets':{'available_count':count},'errors':[]}]})
        with self.s.connect() as c:events=c.execute("SELECT detail FROM events WHERE kind='reset_vouchers_changed'").fetchall()
        self.assertEqual(len(events),1);self.assertEqual(json.loads(events[0][0])['after'],2)

    def replacement_fixture(self):
        j=self.enqueue();old='mcp:'+str(uuid.uuid4());new=str(uuid.uuid4());watcher=str(uuid.uuid4())
        self.s.patch(j['id'],state='running',thread_id=old,project_id='p',observation={'stage':'Old stage','lastNudge':'Old context'},review={'summary':'Old finding'})
        with self.s.connect() as c:c.execute("UPDATE watcher SET thread=?,epoch=5,phase='ready'",(watcher,))
        replacement={'threadId':new,'projectId':'p','providerInstanceId':'codex','model':'test','status':'running','activeRunId':'new-run','worktreePath':None,'archived':False}
        retired={'threadId':old,'archived':True,'activeRunId':None,'pendingRequestCount':0}
        def cli(*args,**kwargs):
            self.assertEqual(args[:2],('thread','show'))
            self.assertEqual(args[3:],('--messages','0','--json'))
            return json.dumps({'thread':replacement if args[2]==new else retired})
        return j,old,new,watcher,replacement,retired,cli
    def test_worker_replacement_resets_context_and_never_messages_worker(self):
        j,old,new,watcher,replacement,retired,cli=self.replacement_fixture()
        with patch.object(self.s,'cli',side_effect=cli) as calls:
            result=engine.replace_worker(self.s,j['slug'],new,expected_old=old)
            again=engine.replace_worker(self.s,j['id'],new,expected_old=old)
            self.assertEqual(calls.call_count,2)
        saved=result['job'];self.assertEqual(saved['thread_id'],new);self.assertEqual(saved['state'],'running')
        self.assertEqual(saved['request'],j['request']);self.assertIsNone(saved['review'])
        self.assertNotIn('lastNudge',saved['observation']);self.assertNotIn('stage',saved['observation'])
        self.assertEqual(saved['observation']['contextPolicy'],'current_thread_and_project_only')
        self.assertFalse(result['job']['launch_result']['optionsVerified']);self.assertFalse(again['changed'])
        self.assertEqual(result['watcher']['epoch'],6);self.assertEqual(again['watcher']['epoch'],6)
        with self.assertRaises(Problem):engine.fence(self.s,watcher,5)
        engine.fence(self.s,watcher,6)
        self.assertEqual(len(self.s.jobs()),1)
    def test_worker_replacement_rejects_wrong_project_worktree_and_model(self):
        j,old,new,watcher,replacement,retired,cli=self.replacement_fixture()
        for key,value in [('projectId','wrong'),('worktreePath',str(self.root/'other')),('model','different'),('providerInstanceId','claudeAgent')]:
            previous=replacement[key];replacement[key]=value
            with self.subTest(key=key),patch.object(self.s,'cli',side_effect=cli):
                with self.assertRaises(Problem):engine.replace_worker(self.s,j['id'],new)
            replacement[key]=previous
            self.assertEqual(self.s.job(j['id'])['thread_id'],old);self.assertEqual(engine.owner(self.s)['epoch'],5)
    def test_worker_replacement_requires_retired_old_thread(self):
        j,old,new,watcher,replacement,retired,cli=self.replacement_fixture()
        retired['archived']=False
        with patch.object(self.s,'cli',side_effect=cli):
            with self.assertRaises(Problem):engine.replace_worker(self.s,j['id'],new)
        self.assertEqual(self.s.job(j['id'])['thread_id'],old)
    def test_worker_replacement_expected_old_prevents_stale_updates(self):
        j,old,new,watcher,replacement,retired,cli=self.replacement_fixture()
        with patch.object(self.s,'cli',side_effect=cli):
            with self.assertRaises(Problem):engine.replace_worker(self.s,j['id'],new,expected_old='stale')
        self.assertEqual(engine.owner(self.s)['epoch'],5)
    def test_worker_replacement_duplicate_binding_rolls_back(self):
        j,old,new,watcher,replacement,retired,cli=self.replacement_fixture()
        self.payload['requestId']=str(uuid.uuid4());self.payload['groups'][0]['slug']='other-device'
        other=self.enqueue();self.s.patch(other['id'],thread_id=new)
        with patch.object(self.s,'cli',side_effect=cli):
            with self.assertRaises(Problem):engine.replace_worker(self.s,j['id'],new)
        self.assertEqual(self.s.job(j['id'])['thread_id'],old);self.assertEqual(engine.owner(self.s)['epoch'],5)
    def test_old_checks_do_not_verify_replacement_worker(self):
        j,r,log,dest=self.review_fixture()
        old=self.s.job(j['id'])['thread_id'];new=str(uuid.uuid4());watcher=str(uuid.uuid4())
        self.s.patch(j['id'],project_id='p')
        with self.s.connect() as c:c.execute("UPDATE watcher SET thread=?,epoch=5,phase='ready'",(watcher,))
        def cli(*args,**kwargs):
            t={'threadId':new,'projectId':'p','providerInstanceId':'codex','model':'test','status':'running','activeRunId':'run'} if args[2]==new else {'threadId':old,'archived':True}
            return json.dumps({'thread':t})
        with patch.object(self.s,'cli',side_effect=cli):engine.replace_worker(self.s,j['id'],new)
        with self.assertRaises(Problem):record_review(self.s,j['id'],r)
        check=run_check(self.s,j['id'],[sys.executable,'-c','print("fresh independent check")'],watcher,6)
        self.assertEqual(check['exitCode'],0)
        self.assertEqual(record_review(self.s,j['id'],r)['state'],'verified_complete')
    def test_replacement_resets_forecast_calibration_without_erasing_usage(self):
        j,old,new,watcher,replacement,retired,cli=self.replacement_fixture()
        reset=dt.datetime.fromtimestamp(time.time()+3600,dt.timezone.utc).isoformat()
        for mins,used in [(-15,10),(-10,12),(-5,14),(0,16)]:self.quota(used,dt.datetime.fromtimestamp(time.time()+mins*60,dt.timezone.utc).isoformat(),reset)
        self.assertEqual(engine.forecast(self.s)[0]['intervals'],3)
        with patch.object(self.s,'cli',side_effect=cli):engine.replace_worker(self.s,j['id'],new)
        self.assertEqual(engine.forecast(self.s)[0]['intervals'],0)
        with self.s.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM quota').fetchone()[0],4)

    def test_dashboard_is_read_only_and_limits_hosts(self):
        static=self.root/'static';static.mkdir();(static/'index.html').write_text('dashboard')
        server=dashboard.make_server(self.s,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            c=http.client.HTTPConnection('127.0.0.1',server.server_port)
            c.request('GET','/api/status');r=c.getresponse();self.assertEqual(r.status,200);self.assertEqual(json.loads(r.read())['jobs'],[])
            c.request('POST','/api/launch',body='{}');r=c.getresponse();self.assertEqual(r.status,405);r.read()
            c.request('GET','/api/status',headers={'Host':'evil.example'});r=c.getresponse();self.assertEqual(r.status,403);r.read();c.close()
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
