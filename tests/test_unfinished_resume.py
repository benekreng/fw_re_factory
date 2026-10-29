import datetime as dt
import unittest
import uuid

import test_controller as fixtures
from app import Problem
from controller import atomic_json, claim
import engine


class UnfinishedResumeCase(unittest.TestCase):
    setUp = fixtures.Case.setUp
    tearDown = fixtures.Case.tearDown
    enqueue = fixtures.Case.enqueue
    quota = fixtures.Case.quota

    def test_partial_handoff_reserves_same_worker_with_normal_guards(self):
        engine.set_launch_mode(self.s, 'manual')
        job = self.enqueue()
        self.s.patch(job['id'], state='waiting', thread_id='existing-worker',
                     observation={'unfinishedContinuationRequested': True})
        with self.assertRaisesRegex(Problem, 'missing or stale'):
            claim(self.s, job['id'], resume=True)
        self.quota(99)
        with self.assertRaisesRegex(Problem, 'at its limit'):
            claim(self.s, job['id'], resume=True)
        self.quota(1)
        config = self.s.config()
        atomic_json(self.s.data/'config.json', config | {'schedulingPaused': True})
        with self.assertRaisesRegex(Problem, 'Scheduling is paused'):
            claim(self.s, job['id'], resume=True)
        atomic_json(self.s.data/'config.json', config)
        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()
        self.s.patch(job['id'], observation={'unfinishedContinuationRequested': True, 'resetAt': future})
        with self.assertRaisesRegex(Problem, 'future usage reset'):
            claim(self.s, job['id'], resume=True)
        self.s.patch(job['id'], observation={'unfinishedContinuationRequested': True})
        self.assertEqual(engine.plan(self.s)['decisions'][0]['action'], 'resume')
        self.assertEqual(claim(self.s, job['id'], resume=True)['thread_id'], 'existing-worker')

    def test_unmarked_unbound_or_stopped_workers_are_not_admitted(self):
        self.quota(1)
        for state, thread, observation in [
            ('waiting', 'worker', {}),
            ('waiting', None, {'unfinishedContinuationRequested': True}),
            ('stopped', 'worker', {'unfinishedContinuationRequested': True}),
            ('stop_requested', 'worker', {'unfinishedContinuationRequested': True}),
        ]:
            self.payload['requestId'] = str(uuid.uuid4())
            self.payload['groups'][0]['slug'] = 'worker-' + str(uuid.uuid4())
            job = self.enqueue()
            self.s.patch(job['id'], state=state, thread_id=(thread + job['id']) if thread else None,
                         observation=observation)
            self.assertFalse(any(d['jobId'] == job['id'] for d in engine.plan(self.s)['decisions']))
            with self.assertRaises(Problem):
                claim(self.s, job['id'], resume=True)


if __name__ == '__main__':
    unittest.main()
