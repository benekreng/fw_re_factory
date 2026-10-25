import datetime as dt
import json
import unittest
import uuid
from unittest.mock import patch

import test_controller as fixtures
from app import Problem, Store, now
from controller import atomic_json, claim
import engine
import usage_retry
import worker_control


class UsageRetryCase(unittest.TestCase):
    setUp_fixture=fixtures.Case.setUp
    tearDown=fixtures.Case.tearDown
    enqueue=fixtures.Case.enqueue
    quota=fixtures.Case.quota

    def ago(self,seconds):
        return (dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=seconds)).isoformat()

    def setUp(self):
        self.setUp_fixture();engine.set_launch_mode(self.s,'manual')
        self.job=self.enqueue()
        self.s.patch(self.job['id'],state='usage_limited',thread_id='worker',project_id='project',observation={
            'providerError':'API Error: Request rejected (429): rate_limit_error; all credentials cooling down.',
            'threadStatus':'failed','lastRunId':'failed-run-1','usageLimitedAt':self.ago(1500)})
        with self.s.connect() as c:c.execute("UPDATE watcher SET thread='watcher',epoch=7,phase='ready'")
        self.actual={'threadId':'worker','status':'failed','activeRunId':None,'pendingRequestCount':0,'latestRunId':'failed-run-1'}

    def prepare(self,store=None,owner='watcher',epoch=7):
        store=store or self.s
        with patch.object(store,'cli',return_value=json.dumps({'thread':self.actual})) as cli:
            result=usage_retry.prepare(store,owner,epoch,self.job['id'])
            if cli.called:self.assertEqual(cli.call_args.args,('thread','show','worker','--messages','0','--json'))
            return result

    def decision(self):
        return next(d for d in engine.plan(self.s)['decisions'] if d['jobId']==self.job['id'])

    def test_missing_telemetry_allows_due_existing_worker_probe_only(self):
        self.assertTrue(self.decision()['usageProbe'])
        with self.assertRaisesRegex(Problem,'usage-retry'):claim(self.s,self.job['id'],resume=True)
        result=self.prepare()
        self.assertTrue(result['send'])
        self.assertEqual(result['delivery']['threadId'],'worker')
        self.assertEqual(result['delivery']['mode'],'auto')
        self.assertIn('subagent',result['delivery']['message'])
        saved=self.s.job(self.job['id'])
        self.assertEqual(saved['state'],'running')
        self.assertEqual(saved['observation']['usageResume']['clientRequestId'],result['delivery']['clientRequestId'])
        self.assertEqual(saved['request'],self.job['request'])
        self.assertFalse(self.s.projects.exists())
        self.payload['requestId']=str(uuid.uuid4());self.payload['groups'][0]['slug']='new'
        new=self.enqueue();engine.request_launch(self.s,new['id'])
        with self.assertRaisesRegex(Problem,'missing or stale'):claim(self.s,new['id'])

    def test_exhausted_account_in_pool_does_not_park_worker_forever(self):
        self.quota(100)
        self.assertTrue(self.decision()['usageProbe'])
        self.assertTrue(self.prepare()['send'])

    def test_telemetry_error_is_not_worker_inference_limit_evidence(self):
        job=self.s.job(self.job['id']);observation=job['observation']|{'providerError':'Provider usage request failed: HTTP 429'}
        self.s.patch(job['id'],observation=observation)
        self.assertFalse(self.decision()['admit'])
        with self.assertRaises(Problem):self.prepare()

    def test_not_before_twenty_minutes_without_positive_quota(self):
        self.s.patch(self.job['id'],observation=self.s.job(self.job['id'])['observation']|{'usageLimitedAt':now()})
        self.assertFalse(self.decision()['admit'])
        with self.assertRaises(Problem):self.prepare()

    def test_positive_quota_allows_first_retry_then_attempt_cooldown_survives_failure(self):
        observation=self.s.job(self.job['id'])['observation']|{'usageLimitedAt':now()}
        self.s.patch(self.job['id'],observation=observation);self.quota(10)
        self.assertTrue(self.decision()['admit']);self.assertFalse(self.decision()['usageProbe'])
        self.prepare()
        usage_retry.observe(self.s,self.job['id'],'usage_limited',{'providerError':observation['providerError'],
                            'threadStatus':'failed','lastRunId':'failed-run-2'})
        self.actual['latestRunId']='failed-run-2'
        self.assertFalse(self.decision()['admit'])
        self.assertIn('cooldown',self.decision()['reason'])
        with self.assertRaises(Problem):self.prepare()
        job=self.s.job(self.job['id']);observation=job['observation']
        observation['usageResume']['requestedAt']=self.ago(1300)
        self.s.patch(job['id'],observation=observation)
        result=self.prepare()
        self.assertNotEqual(result['delivery']['clientRequestId'],observation['usageResume']['clientRequestId'])

    def test_reset_and_scheduling_pause_take_precedence(self):
        self.quota(10)
        job=self.s.job(self.job['id']);self.s.patch(job['id'],observation=job['observation']|{'resetAt':self.ago(-3600)})
        self.assertFalse(self.decision()['admit'])
        with self.assertRaisesRegex(Problem,'reset'):self.prepare()
        self.s.patch(job['id'],observation=job['observation'])
        atomic_json(self.s.data/'config.json',self.s.config()|{'schedulingPaused':True})
        with self.assertRaisesRegex(Problem,'paused'):self.prepare()

    def test_explicit_reset_allows_retry_at_boundary_instead_of_waiting_twenty_more_minutes(self):
        observation=self.s.job(self.job['id'])['observation']|{'usageLimitedAt':self.ago(120),'resetAt':self.ago(30)}
        self.s.patch(self.job['id'],observation=observation)
        self.assertTrue(self.decision()['usageProbe'])
        self.assertTrue(self.prepare()['send'])

    def test_same_uncertain_delivery_survives_restart_and_watcher_handover(self):
        first=self.prepare()
        restarted=Store(self.root,firmware=self.s.firmware,projects=self.s.projects)
        again=self.prepare(restarted)
        self.assertEqual(again['delivery'],first['delivery']);self.assertTrue(again['reconcile'])
        with self.s.connect() as c:c.execute("UPDATE watcher SET thread='new-watcher',epoch=8")
        with self.assertRaises(Problem):self.prepare(restarted)
        adopted=self.prepare(restarted,'new-watcher',8)
        self.assertEqual(adopted['delivery'],first['delivery'])

    def test_busy_queued_unknown_queue_or_completed_worker_never_gets_duplicate_turn(self):
        for values in ({'activeRunId':'active'}, {'pendingRequestCount':1}, {'status':'starting'}):
            original=dict(self.actual);self.actual.update(values)
            self.assertFalse(self.prepare()['send']);self.actual=original
        for values in ({'pendingRequestCount':None},{'status':'completed'},{'threadId':'other'},{'archived':True}):
            original=dict(self.actual);self.actual.update(values)
            with self.assertRaises(Problem):self.prepare()
            self.actual=original
        self.assertEqual(self.s.job(self.job['id'])['state'],'usage_limited')

    def test_new_run_needs_fresh_evidence_before_retry(self):
        self.actual['latestRunId']='unread-new-run'
        with self.assertRaisesRegex(Problem,'new run'):self.prepare()

    def test_stop_blocks_reserved_retry_and_invalidates_old_owner(self):
        self.prepare();worker_control.request_stop(self.s,self.job['id'],'worker')
        with self.assertRaises(Problem):self.prepare()
        with self.assertRaisesRegex(Problem,'usage-limited'):self.prepare(owner='watcher',epoch=8)
        self.assertNotIn(self.job['id'],[d['jobId'] for d in engine.plan(self.s)['decisions']])

    def test_observations_do_not_slide_retry_deadline_on_every_wake(self):
        before=self.decision()['retryAt']
        usage_retry.observe(self.s,self.job['id'],'usage_limited',{'summary':'Still waiting, no new worker error.'})
        self.assertEqual(self.decision()['retryAt'],before)

    def test_automatic_probe_can_use_old_sample_but_preserves_worker_cap(self):
        engine.set_launch_mode(self.s,'automatic');self.quota(100)
        with self.s.connect() as c:
            sample=c.execute("SELECT id FROM samples WHERE kind='quota' ORDER BY id DESC LIMIT 1").fetchone()[0]
            c.execute("INSERT OR REPLACE INTO metadata VALUES ('admission_sample',?)",(str(sample),))
        self.assertTrue(self.decision()['usageProbe'])
        self.payload['requestId']=str(uuid.uuid4());self.payload['groups'][0]['slug']='other'
        other=self.enqueue();self.s.patch(other['id'],state='running',thread_id='other')
        atomic_json(self.s.data/'config.json',self.s.config()|{'maxParallel':1})
        self.assertFalse(self.decision()['admit'])

    def test_explicitly_disabled_pool_never_gets_probe(self):
        engine.ingest(self.s,{'ok':True,'checked_at':now(),'accounts':[{'provider':'codex','auth_index':'stable','disabled':True,
            'usage':{'rate_limit':{'primary_window':{'used_percent':10}}}}]})
        self.assertFalse(self.decision()['admit'])
