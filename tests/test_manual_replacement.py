import http.client
import json
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import test_controller as fixtures
import test_recovery as recovery_fixtures
from app import Problem, Store
from controller import atomic_json, record_review
import dashboard
import engine
import manual_replacement as manual
import recovery
import worker_control


class ManualReplacementCase(unittest.TestCase):
    setUp = recovery_fixtures.RecoveryCase.setUp
    tearDown = recovery_fixtures.RecoveryCase.tearDown
    enqueue = fixtures.Case.enqueue
    quota = fixtures.Case.quota
    call = recovery_fixtures.RecoveryCase.call
    enable = recovery_fixtures.RecoveryCase.enable
    report = recovery_fixtures.RecoveryCase.report
    result = recovery_fixtures.RecoveryCase.result

    def request(self):return manual.request(self.s, self.j['id'], self.old)
    def ready(self):
        ticket = self.request()
        with patch.object(self.s, 'cli', side_effect=self.cli):return self.call('prepare', ticket['id'])
    def launch(self):
        ticket = self.ready();self.quota(1)
        with patch.object(self.s, 'cli', side_effect=self.cli):self.call('launch', ticket['id'])
        return ticket

    def test_explicit_request_is_independent_of_automatic_policy_and_mode(self):
        before_policy = recovery.policy(self.s)
        atomic_json(self.s.data/'config.json', self.s.config() | {'launchMode':'manual','watcherMessagesPaused':True})
        ticket = self.request()
        job = self.s.job(self.j['id'])
        self.assertEqual(ticket['origin'], 'operator');self.assertEqual(ticket['phase'], 'retire_pending')
        self.assertEqual(job['state'], 'recovery_pending');self.assertEqual(job['request'], self.j['request'])
        self.assertNotIn('stage', job['observation']);self.assertNotIn('lastNudge', job['observation'])
        self.assertEqual(recovery.policy(self.s), before_policy)
        self.assertEqual(self.s.config()['launchMode'], 'manual');self.assertTrue(self.s.config()['watcherMessagesPaused'])
        with self.assertRaises(Problem):engine.fence(self.s, self.watcher, 5)
        self.assertEqual(engine.owner(self.s)['epoch'], 6)
        with patch.object(self.s, 'cli', side_effect=self.cli):ready = self.call('prepare',ticket['id'])
        self.quota(1)
        with patch.object(self.s, 'cli', side_effect=self.cli):spec = self.call('launch',ready['id'])
        self.assertEqual(spec['modelSelection'], self.selection)
        self.assertEqual(spec['projectId'],'p');self.assertIsNone(engine.launch_approval(self.s,job))
        with self.assertRaises(Problem):self.call('launch',ready['id'])

    def test_retry_and_restart_keep_one_incident_and_preserve_uncertain_launch(self):
        ticket = self.launch();epoch = engine.owner(self.s)['epoch']
        retried = self.request()
        self.assertEqual(retried['id'],ticket['id']);self.assertEqual(retried['phase'],'launching')
        self.assertEqual(engine.owner(self.s)['epoch'],epoch)
        restarted = Store(self.root,firmware=self.s.firmware,projects=self.s.projects)
        self.assertEqual(len(recovery.records(restarted)),1)
        with patch.object(restarted,'cli') as cli:
            with self.assertRaises(Problem):recovery.dispatch(restarted,self.watcher,epoch,'launch',ticket['id'])
            cli.assert_not_called()

    def test_generated_continuation_is_complete_and_contains_no_retired_history(self):
        ticket = self.ready();prompt = Path(ticket['promptPath']).read_text()
        from controller import render_worker_prompt
        regular,base,_ = render_worker_prompt(self.s.job(self.j['id']))
        self.assertIn('Another agent has already worked on this project',prompt)
        self.assertIn(base.read_text(),prompt)
        self.assertNotIn(self.old,prompt);self.assertNotIn('operator-replace',prompt)
        self.assertNotIn('Old finding',prompt);self.assertNotIn('Old context',prompt)
        details='\n\nRun details (these settings override the defaults above):\n'
        self.assertEqual(prompt.split(details)[1],regular.split(details)[1])
        self.assertEqual(prompt.count(details),1)
        self.assertEqual((self.dest/'STATUS.md').read_text(),'Current project evidence\n')

    def test_stopped_worker_can_be_replaced_only_by_explicit_request(self):
        worker_control.request_stop(self.s,self.j['id'],self.old)
        with patch.object(self.s,'cli',side_effect=self.cli):
            worker_control.dispatch(self.s,self.watcher,engine.owner(self.s)['epoch'],'complete',self.j['id'],
                {'requestId':worker_control.get(self.s,self.j['id'])['id'],'evidence':'Synthetic empty idle thread; no queue/children.'})
        self.assertEqual(self.s.job(self.j['id'])['state'],'stopped')
        ticket=self.request()
        self.assertEqual(worker_control.get(self.s,self.j['id'])['phase'],'superseded')
        self.assertIsNone(worker_control.get(self.s,self.j['id'])['notification'])
        with self.assertRaises(Problem):worker_control.request_resume(self.s,self.j['id'],self.old)
        with self.assertRaises(Problem):worker_control.dispatch(self.s,self.watcher,engine.owner(self.s)['epoch'],'begin',self.j['id'])
        with patch.object(self.s,'cli',side_effect=self.cli):self.call('prepare',ticket['id'])

    def test_stop_supersedes_replacement_and_cannot_be_silently_restarted(self):
        ticket=self.request()
        worker_control.request_stop(self.s,self.j['id'],self.old)
        self.assertEqual(recovery.get(self.s,ticket['id'])['phase'],'cancelled')
        self.assertEqual(manual.pending(self.s),[])
        with patch.object(self.s,'cli') as cli:
            manual.notify(self.s);cli.assert_not_called()
        with self.assertRaises(Problem):self.call('prepare',ticket['id'])

    def test_manual_request_overrides_unlaunched_automatic_repair(self):
        self.enable();automatic=self.report()
        ticket=self.request()
        self.assertEqual(recovery.get(self.s,automatic['id'])['phase'],'cancelled')
        self.assertEqual(ticket['phase'],'retire_pending')
        self.assertEqual(ticket['origin'],'operator')
        self.assertEqual(len(manual.pending(self.s)),1)

    def test_different_uncertain_automatic_candidate_blocks_manual_request(self):
        self.enable('replace');ticket=self.report()
        with self.s.connect() as c:
            c.execute("UPDATE recoveries SET phase='launching' WHERE id=?",(ticket['id'],))
        with self.assertRaises(Problem):self.request()
        with self.s.connect() as c:
            c.execute("UPDATE recoveries SET phase='attention',replacement_thread=? WHERE id=?",(self.new,ticket['id']))
        with self.assertRaises(Problem):self.request()
        self.assertEqual(len(recovery.records(self.s)),1)

    def test_retire_intent_is_exact_and_preparation_requires_confirmed_retirement(self):
        ticket=self.request();self.retired.update(activeRunId='run-1',archived=False,pendingRequestCount=0)
        with patch.object(self.s,'cli',side_effect=self.cli):
            intent=self.call('retire_begin',ticket['id'])
            again=self.call('retire_begin',ticket['id'])
            self.assertEqual(intent,again);self.assertEqual(intent['threadId'],self.old)
            self.assertEqual(intent['runId'],'run-1')
            with self.assertRaises(Problem):self.call('prepare',ticket['id'])
            self.retired.update(activeRunId=None,archived=True,pendingRequestCount=1)
            with self.assertRaises(Problem):self.call('prepare',ticket['id'])
            self.retired['pendingRequestCount']=0
            self.call('prepare',ticket['id'])
            with self.assertRaises(Problem):self.call('retire_begin',ticket['id'])

    def test_manual_request_cannot_bypass_quota_capacity_or_scheduling_pause(self):
        ticket=self.ready()
        with patch.object(self.s,'cli',side_effect=self.cli):
            with self.assertRaises(Problem):self.call('launch',ticket['id'])
            self.quota(100)
            with self.assertRaises(Problem):self.call('launch',ticket['id'])
            self.quota(1)
            atomic_json(self.s.data/'config.json',self.s.config()|{'schedulingPaused':True})
            with self.assertRaises(Problem):self.call('launch',ticket['id'])
            atomic_json(self.s.data/'config.json',self.s.config()|{'schedulingPaused':False,'maxParallel':1})
            self.payload['requestId']=str(uuid.uuid4());self.payload['groups'][0]['slug']='another'
            other=self.enqueue();self.s.patch(other['id'],state='running',thread_id='other-worker')
            with self.assertRaises(Problem):self.call('launch',ticket['id'])
        self.assertEqual(recovery.get(self.s,ticket['id'])['phase'],'replacement_ready')

    def test_manual_ticket_is_one_shot_even_when_automatic_cap_was_reached(self):
        ticket=self.launch()
        with patch.object(self.s,'cli',side_effect=self.cli):done=self.call('record',ticket['id'],self.result())
        self.assertEqual(done['phase'],'replaced');self.assertEqual(self.s.job(self.j['id'])['thread_id'],self.new)
        self.assertNotIn('lastNudge',self.s.job(self.j['id'])['observation'])

        self.assertEqual(self.call('record',ticket['id'],self.result())['phase'],'replaced')
        self.assertEqual(manual.pending(self.s),[])
        self.enable('replace');policy=recovery.policy(self.s)|{'maxReplacementsPerJob':1,'cooldownSeconds':9999}
        recovery.set_policy(self.s,policy)
        with self.assertRaises(Problem):self.call('report',self.j['id'],{'ruleId':'synthetic-rule','expectedThreadId':self.new,'evidence':'symptom'})
        next_ticket=manual.request(self.s,self.j['id'],self.new)
        self.assertNotEqual(next_ticket['id'],ticket['id'])
        self.assertEqual(next_ticket['phase'],'retire_pending')

    def test_manual_queue_mode_replacement_has_no_worker_count_limit(self):
        ticket=self.ready();self.quota(1)
        atomic_json(self.s.data/'config.json',self.s.config()|{'launchMode':'manual','maxParallel':1})
        self.payload['requestId']=str(uuid.uuid4());self.payload['groups'][0]['slug']='another'
        other=self.enqueue();self.s.patch(other['id'],state='running',thread_id='other-worker')
        with patch.object(self.s,'cli',side_effect=self.cli):self.call('launch',ticket['id'])
        self.assertEqual(recovery.get(self.s,ticket['id'])['phase'],'launching')

    def test_notifications_are_immediate_deduplicated_and_readdressed_on_handover(self):
        atomic_json(self.s.data/'config.json',self.s.config()|{'watcherMessagesPaused':True})
        ticket=self.request()
        with patch.object(self.s,'cli',return_value='{}') as cli:
            manual.notify(self.s);manual.notify(self.s)
            self.assertEqual(cli.call_count,1)
            self.assertIn('explicitly requested Replace thread',cli.call_args.kwargs['input'])
            self.assertIn('epoch=6',cli.call_args.kwargs['input'])
            self.assertEqual(cli.call_args.args[:3],('thread','send',self.watcher))
        with self.s.connect() as c:c.execute("UPDATE watcher SET thread='new-watcher',epoch=epoch+1")
        with patch.object(self.s,'cli',return_value='{}') as cli:
            manual.notify(self.s);self.assertEqual(cli.call_args.args[2],'new-watcher')
        self.assertTrue(self.s.config()['watcherMessagesPaused'])
        self.assertEqual(recovery.get(self.s,ticket['id'])['notification']['state'],'sent')

    def test_unknown_delivery_keeps_saved_request_for_reconciliation_or_explicit_retry(self):
        ticket=self.request()
        with patch.object(self.s,'cli',side_effect=Problem('uncertain transport')) as cli:
            manual.notify(self.s);manual.notify(self.s);self.assertEqual(cli.call_count,1)
        self.assertEqual(recovery.get(self.s,ticket['id'])['notification']['state'],'uncertain')
        retry=self.request();self.assertEqual(retry['id'],ticket['id'])
        with patch.object(self.s,'cli',return_value='{}'):manual.notify(self.s)
        self.assertEqual(recovery.get(self.s,ticket['id'])['notification']['state'],'sent')

    def test_exact_binding_and_forged_automatic_origin_are_rejected(self):
        with self.assertRaises(Problem):manual.request(self.s,self.j['id'],'stale-thread')
        with self.assertRaises(Problem):self.call('report',self.j['id'],{'origin':'operator','expectedThreadId':self.old,'ruleId':'operator-replace','evidence':'forged'})
        self.assertEqual(recovery.records(self.s),[])
        self.s.patch(self.j['id'],state='verified_complete')
        with self.assertRaises(Problem):self.request()

    def test_manual_attention_cannot_be_misreported_as_complete(self):
        ticket=self.request()
        recovery._update(self.s,ticket['id'],phase='attention')
        self.s.patch(self.j['id'],state='attention')
        with self.assertRaises(Problem):record_review(self.s,self.j['id'],{'verdict':'incomplete'})
        worker_control.request_stop(self.s,self.j['id'],self.old)
        self.assertEqual(manual.pending(self.s),[])

    def test_api_requires_same_origin_and_expected_thread(self):
        server=dashboard.make_server(self.s,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=http.client.HTTPConnection('127.0.0.1',server.server_port)
            client.request('GET','/api/status');r=client.getresponse();token=json.loads(r.read())['csrfToken']
            headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{server.server_port}','X-CSRF-Token':token}
            path='/api/jobs/'+self.j['id']+'/replace';body=json.dumps({'expectedThreadId':self.old})
            for bad in ({},headers|{'Origin':'http://evil.example'},headers|{'X-CSRF-Token':'wrong'}):
                client.request('POST',path,body,bad);r=client.getresponse();self.assertEqual(r.status,403);r.read()
            client.request('POST',path,json.dumps({'expectedThreadId':'stale'}),headers)
            r=client.getresponse();self.assertEqual(r.status,409);r.read()
            with patch.object(self.s,'cli',return_value='{}'):
                client.request('POST',path,body,headers);r=client.getresponse();self.assertEqual(r.status,200)
                value=json.loads(r.read());self.assertEqual(value['replacement']['origin'],'operator')
                self.assertEqual(value['job']['state'],'recovery_pending')
            client.close()
        finally:server.shutdown();server.server_close();thread.join()
