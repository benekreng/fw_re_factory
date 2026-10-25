import http.client
import json
import threading
import unittest
from unittest.mock import patch

import test_controller as fixtures
from app import Problem, Store, now
from controller import atomic_json, claim
import dashboard
import engine
import recovery
import worker_control as controls


class WorkerControlCase(unittest.TestCase):
    tearDown = fixtures.Case.tearDown
    enqueue = fixtures.Case.enqueue
    quota = fixtures.Case.quota

    def setUp(self):
        fixtures.Case.setUp(self)
        self.job = self.enqueue()
        self.job = self.s.patch(self.job['id'], state='running', thread_id='worker', project_id='project',
                                observation={'stage':'Stage 4', 'resetAt':'2099-01-01T00:00:00+00:00'})
        with self.s.connect() as c:
            c.execute("UPDATE watcher SET thread='watcher',epoch=7,phase='ready'")
        self.actual = {'threadId':'worker', 'activeRunId':'run-1', 'status':'running', 'pendingRequestCount':0}

    def stop(self):return controls.request_stop(self.s, self.job['id'], 'worker')
    def call(self, action, payload=None, epoch=None):
        return controls.dispatch(self.s, 'watcher', epoch or engine.owner(self.s)['epoch'], action, self.job['id'], payload)
    def idle(self):self.actual.update(activeRunId=None, status='interrupted', pendingRequestCount=0)
    def complete(self):
        self.stop();self.idle()
        with patch.object(self.s, 'cli', return_value=json.dumps({'thread':self.actual})):
            return self.call('complete', {'requestId':controls.get(self.s,self.job['id'])['id'], 'evidence':'Synthetic native interruption, empty queue, no active child work.'})

    def test_stop_is_durable_blocks_resumes_and_invalidates_stale_watcher(self):
        stopped = self.stop()
        self.assertEqual(stopped['state'], 'stop_requested')
        self.assertEqual(stopped['request'], self.job['request'])
        self.assertEqual(engine.owner(self.s)['epoch'], 8)
        with self.assertRaises(Problem):engine.fence(self.s,'watcher',7)
        with self.assertRaises(Problem):self.s.patch(self.job['id'],state='running')
        with self.assertRaises(Problem):self.s.patch(self.job['id'],state='attention')
        with self.assertRaises(Problem):engine.replace_worker(self.s,self.job['id'],'new-worker')
        self.quota(1)
        self.assertEqual(engine.plan(self.s)['decisions'], [])
        with self.assertRaises(Problem):claim(self.s,self.job['id'],resume=True)
        restarted = Store(self.root, firmware=self.s.firmware, projects=self.s.projects)
        self.assertEqual(restarted.job(self.job['id'])['state'],'stop_requested')
        self.assertEqual(controls.get(restarted,self.job['id'])['phase'],'pending')

    def test_stop_confirmation_requires_actual_idle_and_empty_queue(self):
        self.stop();request=controls.get(self.s,self.job['id'])['id']
        with patch.object(self.s,'cli',side_effect=lambda *a,**k:json.dumps({'thread':self.actual})):
            first=self.call('begin');second=self.call('begin')
            self.assertEqual(first['clientRequestId'],second['clientRequestId'])
            self.assertEqual(first['runId'],'run-1')
            self.actual['activeRunId']='late-run'
            late=self.call('begin')
            self.assertNotEqual(first['clientRequestId'],late['clientRequestId'])
            for actual in [dict(self.actual), dict(self.actual,activeRunId=None,status='interrupted',pendingRequestCount=1),
                           dict(self.actual,activeRunId=None,status='interrupted',pendingRequestCount=None),
                           dict(self.actual,threadId='different',activeRunId=None,status='interrupted')]:
                self.actual=actual
                with self.assertRaises(Problem):self.call('complete',{'requestId':request,'evidence':'checked'})
            self.actual={'threadId':'worker','activeRunId':None,'status':'interrupted','pendingRequestCount':0}
            with self.assertRaises(Problem):self.call('complete',{'requestId':'stale','evidence':'checked'})
            with self.assertRaises(Problem):self.call('complete',{'requestId':request})
            done=self.call('complete',{'requestId':request,'evidence':'Interrupted; queue cleared; child work stopped.'})
        self.assertEqual(done['phase'],'stopped')
        self.assertEqual(self.s.job(self.job['id'])['state'],'stopped')
        self.quota(1)
        self.assertEqual(engine.plan(self.s)['active'],0)
        with self.assertRaises(Problem):claim(self.s,self.job['id'],resume=True)

    def test_no_resume_until_stop_confirmed_then_same_thread_uses_admission(self):
        self.stop()
        with self.assertRaises(Problem):controls.request_resume(self.s,self.job['id'],'worker')
        self.complete()
        atomic_json(self.s.data/'config.json',self.s.config()|{'launchMode':'manual','watcherMessagesPaused':True})
        resumed=controls.request_resume(self.s,self.job['id'],'worker')
        self.assertEqual(resumed['state'],'waiting');self.assertEqual(resumed['thread_id'],'worker')
        self.assertTrue(resumed['observation']['operatorResumeRequested'])
        self.assertNotIn('resetAt',resumed['observation'])
        self.assertIsNone(engine.launch_approval(self.s,resumed))
        with self.assertRaises(Problem):claim(self.s,self.job['id'],resume=True)
        self.quota(99)
        with self.assertRaises(Problem):claim(self.s,self.job['id'],resume=True)
        self.quota(1)
        self.assertEqual(claim(self.s,self.job['id'],resume=True)['thread_id'],'worker')
        self.assertEqual(self.s.job(self.job['id'])['state'],'running')
        self.assertEqual(self.s.config()['launchMode'],'manual')
        self.assertTrue(self.s.config()['watcherMessagesPaused'])

    def test_duplicate_stop_resume_and_new_stop_keep_history(self):
        self.stop();first=controls.get(self.s,self.job['id'])
        self.stop();self.assertEqual(controls.get(self.s,self.job['id'])['id'],first['id'])
        self.complete();controls.request_resume(self.s,self.job['id'],'worker')
        epoch=engine.owner(self.s)['epoch']
        repeated=controls.request_resume(self.s,self.job['id'],'worker')
        self.assertTrue(repeated['observation']['operatorResumeMessage'])
        self.assertEqual(engine.owner(self.s)['epoch'],epoch)
        self.stop();self.assertNotEqual(controls.get(self.s,self.job['id'])['id'],first['id'])
        with self.s.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM events WHERE kind='worker_stop_requested'").fetchone()[0],2)

    def test_stop_and_resume_target_exact_current_thread(self):
        for thread in (None,'old-worker'):
            with self.assertRaises(Problem):controls.request_stop(self.s,self.job['id'],thread)
        self.complete()
        with self.assertRaises(Problem):controls.request_resume(self.s,self.job['id'],'old-worker')
        self.assertEqual(self.s.job(self.job['id'])['state'],'stopped')

    def test_stop_notification_bypasses_periodic_pause_and_busy_watcher_once(self):
        atomic_json(self.s.data/'config.json',self.s.config()|{'watcherMessagesPaused':True})
        self.stop()
        with patch.object(self.s,'cli',return_value='{}') as cli:
            sent=controls.notify(self.s)
            self.assertEqual(cli.call_args.args[:3],('thread','send','watcher'))
            self.assertIn('worker stop',cli.call_args.kwargs['input'])
            self.assertIn('epoch=8',cli.call_args.kwargs['input'])
            self.assertEqual(sent[0]['state'],'sent')
            controls.notify(self.s);self.assertEqual(cli.call_count,1)
        self.assertTrue(self.s.config()['watcherMessagesPaused'])

    def test_unavailable_watcher_retry_and_uncertain_notification_do_not_lose_hold(self):
        self.stop()
        with self.s.connect() as c:c.execute("UPDATE watcher SET phase='unconfigured'")
        with patch.object(self.s,'cli') as cli:
            controls.notify(self.s);cli.assert_not_called()
        with self.s.connect() as c:c.execute("UPDATE watcher SET phase='ready'")
        with patch.object(self.s,'cli',side_effect=Problem('Delivery uncertain')) as cli:
            self.assertEqual(controls.notify(self.s)[0]['state'],'uncertain')
            controls.notify(self.s);self.assertEqual(cli.call_count,1)
        self.assertEqual(self.s.job(self.job['id'])['state'],'stop_requested')
        self.stop() # explicit retry allows a new notification; interrupt request ID is retained
        with patch.object(self.s,'cli',return_value='{}'):
            self.assertEqual(controls.notify(self.s)[0]['state'],'sent')

    def test_watcher_handover_notifies_new_owner_and_stale_ack_fails(self):
        self.stop()
        with patch.object(self.s,'cli',return_value='{}'):controls.notify(self.s)
        with self.s.connect() as c:c.execute("UPDATE watcher SET thread='new-watcher',epoch=epoch+1")
        with patch.object(self.s,'cli',return_value='{}') as cli:
            controls.notify(self.s);self.assertEqual(cli.call_args.args[2],'new-watcher')
        with self.assertRaises(Problem):self.call('begin',epoch=8)

    def test_stopped_worker_cannot_enter_recovery(self):
        self.stop()
        policy={'enabled':True,'source':'test','maxReplacementsPerJob':2,'cooldownSeconds':0,
                'rules':[{'id':'error','when':'test symptom','action':'replace'}]}
        recovery.set_policy(self.s,policy)
        with self.assertRaises(Problem):recovery.dispatch(self.s,'watcher',engine.owner(self.s)['epoch'],'report',self.job['id'],
            {'expectedThreadId':'worker','ruleId':'error','evidence':'interrupted'})
        self.assertEqual(recovery.records(self.s),[])

    def test_cancels_pending_recovery_but_rejects_inflight_replacement(self):
        policy={'enabled':True,'source':'test','maxReplacementsPerJob':2,'cooldownSeconds':0,
                'rules':[{'id':'error','when':'test symptom','action':'replace'}]}
        recovery.set_policy(self.s,policy)
        ticket=recovery.dispatch(self.s,'watcher',7,'report',self.job['id'],
            {'expectedThreadId':'worker','ruleId':'error','evidence':'test symptom'})
        with self.s.connect() as c:c.execute("UPDATE recoveries SET phase='launching' WHERE id=?",(ticket['id'],))
        with self.assertRaises(Problem):self.stop()
        self.assertIsNone(controls.get(self.s,self.job['id']))
        with self.s.connect() as c:c.execute("UPDATE recoveries SET phase='retire_pending' WHERE id=?",(ticket['id'],))
        self.stop();self.assertEqual(recovery.get(self.s,ticket['id'])['phase'],'cancelled')
        with self.assertRaises(Problem):recovery.dispatch(self.s,'watcher',8,'prepare',ticket['id'])

    def test_pending_stop_keeps_capacity_until_idle_confirmed(self):
        config=self.s.config()|{'maxParallel':1};atomic_json(self.s.data/'config.json',config)
        self.stop();self.quota(1)
        import uuid
        self.payload['requestId']=str(uuid.uuid4());self.payload['groups'][0]['slug']='other-device'
        other=self.enqueue()
        decision=next(d for d in engine.plan(self.s)['decisions'] if d['jobId']==other['id'])
        self.assertFalse(decision['admit'])
        self.complete();self.quota(1)
        decision=next(d for d in engine.plan(self.s)['decisions'] if d['jobId']==other['id'])
        self.assertTrue(decision['admit'])

    def test_http_stop_resume_require_csrf_origin_and_exact_thread(self):
        server=dashboard.make_server(self.s,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=http.client.HTTPConnection('127.0.0.1',server.server_port)
            client.request('GET','/api/status');r=client.getresponse();token=json.loads(r.read())['csrfToken']
            headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{server.server_port}','X-CSRF-Token':token}
            path='/api/jobs/'+self.job['id']+'/stop';body=json.dumps({'expectedThreadId':'worker'})
            for bad in ({},headers|{'Origin':'http://evil.example'},headers|{'X-CSRF-Token':'wrong'}):
                client.request('POST',path,body,bad);r=client.getresponse();self.assertEqual(r.status,403);r.read()
            client.request('POST',path,json.dumps({'expectedThreadId':'stale'}),headers)
            r=client.getresponse();self.assertEqual(r.status,409);r.read()
            with patch.object(self.s,'cli',return_value='{}'):
                client.request('POST',path,body,headers);r=client.getresponse()
                self.assertEqual(r.status,200);self.assertEqual(json.loads(r.read())['job']['state'],'stop_requested')
                self.complete()
                client.request('POST',path.replace('/stop','/resume'),body,headers);r=client.getresponse()
                self.assertEqual(r.status,200);self.assertEqual(json.loads(r.read())['job']['state'],'waiting')
            client.close()
        finally:server.shutdown();server.server_close();thread.join()
