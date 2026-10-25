import copy
import http.client
import json
import threading
import unittest
from unittest.mock import patch

import test_controller as fixtures
from app import Problem, Store
from controller import atomic_json
import dashboard
import engine
import recovery


class PolicyCase(unittest.TestCase):
    setUp=fixtures.Case.setUp
    tearDown=fixtures.Case.tearDown

    def draft(self):
        return {'enabled':True,'maxReplacementsPerJob':3,'cooldownSeconds':900,
                'rules':[{'id':'synthetic-failure','when':'Synthetic protocol failure observed twice','action':'replace'}]}

    def owner(self):
        with self.s.connect() as c:c.execute("UPDATE watcher SET thread='watcher',epoch=4,phase='ready'")

    def save(self,value=None,revision=None):
        return recovery.update_dashboard_policy(self.s,value or self.draft(),revision or recovery.policy_snapshot(self.s)['revision'])

    def test_save_posts_immediately_even_when_busy_paused_and_manual(self):
        self.owner();config=self.s.config();config.update(launchMode='manual',watcherMessagesPaused=True)
        atomic_json(self.s.data/'config.json',config)
        with patch.object(self.s,'cli',return_value='{"accepted":true}') as calls:
            result=self.save()
            self.assertEqual(calls.call_count,1)
            self.assertEqual(calls.call_args.args,('thread','send','watcher','-','--json'))
            self.assertIn('Synthetic protocol failure observed twice',calls.call_args.kwargs['input'])
            self.assertIn('epoch=4',calls.call_args.kwargs['input'])
            self.assertEqual(recovery.policy_notification(self.s)['state'],'sent')
        self.assertEqual(result['policy']['source'],'Dashboard')
        self.assertEqual(self.s.config()['launchMode'],'manual');self.assertTrue(self.s.config()['watcherMessagesPaused'])

    def test_stale_editor_does_not_overwrite_newer_rules(self):
        old=recovery.policy_snapshot(self.s)['revision'];self.save()
        other=self.draft();other['rules'][0]['when']='Different synthetic condition'
        with self.assertRaisesRegex(Problem,'changed elsewhere'):self.save(other,old)
        self.assertEqual(recovery.policy(self.s)['rules'][0]['when'],self.draft()['rules'][0]['when'])

    def test_invalid_rules_do_not_change_policy_or_notify(self):
        old=recovery.policy(self.s)
        bad=self.draft();bad['rules']=[]
        with patch.object(self.s,'cli') as calls:
            with self.assertRaises(Problem):self.save(bad)
            bad=self.draft();bad['rules'][0]['action']='repair_then_replace'
            with self.assertRaises(Problem):self.save(bad)
            calls.assert_not_called()
        self.assertEqual(old,recovery.policy(self.s));self.assertIsNone(recovery.policy_notification(self.s))

    def test_pending_notification_survives_restart_until_watcher_is_ready(self):
        result=self.save();self.assertEqual(result['notification']['state'],'pending')
        self.owner();restarted=Store(self.root,firmware=self.s.firmware,projects=self.s.projects)
        with patch.object(restarted,'cli',return_value='{}') as calls:
            self.assertEqual(recovery.notify_policy_update(restarted)['state'],'sent')
            recovery.notify_policy_update(restarted);self.assertEqual(calls.call_count,1)

    def test_uncertain_delivery_is_not_retried_automatically(self):
        self.owner()
        with patch.object(self.s,'cli',side_effect=Problem('timeout; outcome unknown')):
            result=self.save()
        self.assertEqual(result['notification']['state'],'uncertain')
        with patch.object(self.s,'cli',return_value='{}') as calls:
            recovery.notify_policy_update(self.s);calls.assert_not_called()
            self.assertEqual(recovery.notify_policy_update(self.s,retry_revision=result['revision'])['state'],'sent')
            self.assertEqual(calls.call_count,1)

    def test_repeated_save_after_lost_response_does_not_duplicate_notification(self):
        self.owner();revision=recovery.policy_snapshot(self.s)['revision']
        with patch.object(self.s,'cli',return_value='{}') as calls:
            first=self.save(revision=revision);second=self.save(revision=revision)
            self.assertEqual(first['revision'],second['revision']);self.assertEqual(calls.call_count,1)

    def test_changed_policy_supersedes_unsent_notification(self):
        self.save();other=copy.deepcopy(recovery.policy(self.s));other['enabled']=False
        with engine.lock(self.s):recovery.set_policy(self.s,other)
        self.owner()
        with patch.object(self.s,'cli') as calls:
            self.assertEqual(recovery.notify_policy_update(self.s)['state'],'superseded');calls.assert_not_called()

    def test_dashboard_policy_api_checks_origin_token_and_validation(self):
        server=dashboard.make_server(self.s,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            c=http.client.HTTPConnection('127.0.0.1',server.server_port)
            c.request('GET','/api/status');r=c.getresponse();data=json.loads(r.read())
            self.assertIn('hostUsage',data)
            headers={'Origin':'http://127.0.0.1:'+str(server.server_port),'Content-Type':'application/json','X-CSRF-Token':data['csrfToken']}
            body=json.dumps({'policy':self.draft(),'expectedRevision':data['recoveryPolicyRevision']})
            for bad in [{},dict(headers,Origin='http://evil.example')]:
                c.request('POST','/api/recovery-policy',body=body,headers=bad);r=c.getresponse()
                self.assertEqual(r.status,403);r.read()
            c.request('POST','/api/recovery-policy',body=body,headers=headers);r=c.getresponse()
            self.assertEqual(r.status,200);saved=json.loads(r.read());self.assertTrue(saved['policy']['enabled'])
            c.request('POST','/api/recovery-policy/notify',body='{"expectedRevision":"outdated"}',headers=headers)
            r=c.getresponse();self.assertEqual(r.status,409);r.read();c.close()
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
