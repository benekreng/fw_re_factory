import http.client
import json
import threading
import unittest
import uuid
from unittest.mock import patch

import test_controller as fixtures
from app import Problem, Store
from controller import atomic_json, claim
import dashboard
import engine


class ManualCase(unittest.TestCase):
    setUp=fixtures.Case.setUp
    tearDown=fixtures.Case.tearDown
    enqueue=fixtures.Case.enqueue
    quota=fixtures.Case.quota

    def manual(self):engine.set_launch_mode(self.s,'manual')

    def test_default_is_manual_and_enqueue_does_not_approve(self):
        config=self.s.config();config.pop('launchMode');atomic_json(self.s.data/'config.json',config)
        job=self.enqueue();self.quota()
        self.assertEqual(engine.plan(self.s)['launchMode'],'manual')
        with self.assertRaisesRegex(Problem,'Manual mode'):claim(self.s,job['id'])
        self.assertIsNone(engine.launch_approval(self.s,job))
        self.assertFalse((self.s.projects/job['slug']).exists())

    def test_only_selected_job_is_admitted_and_duplicate_click_cannot_relaunch(self):
        self.manual();first=self.enqueue()
        self.payload['requestId']=str(uuid.uuid4());self.payload['groups'][0]['slug']='second'
        second=self.enqueue();self.quota()
        engine.request_launch(self.s,second['slug']);engine.request_launch(self.s,second['id'])
        decisions={d['jobId']:d for d in engine.plan(self.s)['decisions']}
        self.assertFalse(decisions[first['id']]['admit']);self.assertTrue(decisions[second['id']]['admit'])
        claim(self.s,second['id'])
        with self.assertRaises(Problem):claim(self.s,second['id'])
        self.s.patch(second['id'],state='running',thread_id='existing-worker')
        with self.assertRaises(Problem):engine.request_launch(self.s,second['id'])

    def test_approval_survives_restart_but_never_bypasses_usage_or_pause(self):
        self.manual();job=self.enqueue();engine.request_launch(self.s,job['id'])
        with self.assertRaisesRegex(Problem,'missing or stale'):claim(self.s,job['id'])
        self.quota(99)
        with self.assertRaisesRegex(Problem,'at its limit'):claim(self.s,job['id'])
        restarted=Store(self.root,firmware=self.s.firmware,projects=self.s.projects)
        self.assertIsNotNone(engine.launch_approval(restarted,job))
        config=self.s.config();config['schedulingPaused']=True;atomic_json(self.s.data/'config.json',config)
        self.quota(1)
        with self.assertRaisesRegex(Problem,'Scheduling is paused'):claim(self.s,job['id'])

    def test_existing_worker_resumes_without_new_launch_approval(self):
        self.manual();job=self.enqueue()
        self.s.patch(job['id'],state='usage_limited',thread_id='existing-worker');self.quota(1)
        self.assertEqual(claim(self.s,job['id'],resume=True)['state'],'running')

    def test_explicit_launch_wakes_once_while_periodic_messages_stay_paused(self):
        self.manual();job=self.enqueue();engine.request_launch(self.s,job['id'])
        config=self.s.config();config['watcherMessagesPaused']=True;atomic_json(self.s.data/'config.json',config)
        with self.s.connect() as c:c.execute("UPDATE watcher SET thread='watcher',epoch=1,phase='ready'")
        with patch.object(self.s,'cli',return_value=json.dumps({'thread':{'activeRunId':'busy'}})) as calls:
            self.assertFalse(engine.wake(self.s)['sent']);self.assertEqual(calls.call_count,1)
        with patch.object(self.s,'cli',side_effect=[json.dumps({'thread':{}}),'{}']) as calls:
            self.assertTrue(engine.wake(self.s)['sent']);self.assertEqual(calls.call_count,2)
            self.assertIn('user requested launch',calls.call_args.kwargs['input'])
        with patch.object(self.s,'cli') as calls:
            self.assertFalse(engine.wake(self.s,force=True)['sent']);calls.assert_not_called()
        self.assertTrue(self.s.config()['watcherMessagesPaused'])

    def test_changing_mode_does_not_grant_approval_or_resume_messages(self):
        config=self.s.config();config['watcherMessagesPaused']=True;atomic_json(self.s.data/'config.json',config)
        job=self.enqueue();self.quota();self.manual()
        self.assertFalse(engine.plan(self.s)['decisions'][0]['admit'])
        engine.set_launch_mode(self.s,'automatic')
        self.assertTrue(engine.plan(self.s)['decisions'][0]['admit'])
        self.assertIsNone(engine.launch_approval(self.s,job));self.assertTrue(self.s.config()['watcherMessagesPaused'])

    def test_dashboard_actions_require_same_origin_and_token(self):
        self.manual();job=self.enqueue();server=dashboard.make_server(self.s,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            c=http.client.HTTPConnection('127.0.0.1',server.server_port)
            c.request('GET','/api/status');response=c.getresponse();data=json.loads(response.read())
            headers={'Content-Type':'application/json','Origin':'http://127.0.0.1:'+str(server.server_port),'X-CSRF-Token':data['csrfToken']}
            path='/api/jobs/'+job['id']+'/launch'
            for bad in [{},dict(headers,Origin='http://evil.example'),dict(headers,**{'X-CSRF-Token':'wrong'})]:
                c.request('POST',path,body='{}',headers=bad);response=c.getresponse()
                self.assertEqual(response.status,403);response.read()
            self.assertIsNone(engine.launch_approval(self.s,job))
            with patch('engine.notify_launch',return_value={'sent':False,'reason':'no ready watcher'}):
                c.request('POST',path,body='{}',headers=headers);response=c.getresponse()
                self.assertEqual(response.status,200);self.assertEqual(json.loads(response.read())['approval']['job'],job['id'])
            c.request('POST','/api/launch-mode',body='{"mode":"automatic"}',headers=headers)
            response=c.getresponse();self.assertEqual(response.status,200);response.read()
            self.assertEqual(self.s.config()['launchMode'],'automatic')
            c.request('POST','/api/launch-mode',body='{"mode":"invalid"}',headers=headers)
            response=c.getresponse();self.assertEqual(response.status,400);response.read()
            c.close()
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
