import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_controller as fixtures
from app import Problem, sha
from controller import AREAS, atomic_json, prepare, record_review
import completion_review as reviews
import engine


class CompletionReviewTests(unittest.TestCase):
    enqueue = fixtures.Case.enqueue

    def setUp(self):
        fixtures.Case.setUp(self)
        self.owner = 'mcp:' + str(uuid.uuid4())
        with patch.object(self.s, 'cli', return_value=json.dumps({'thread': {'threadId': self.owner}})):
            self.epoch = engine.set_owner(self.s, self.owner)['epoch']
        cfg = self.s.config()
        cfg['completionReviews'] = {'enabled': True, 'selection': self.selection,
                                     'maxConcurrent': 2, 'maxCorrectionRounds': 2}
        atomic_json(self.s.data / 'config.json', cfg)
        self.job = self.enqueue()
        self.s.patch(self.job['id'], state='preparing')
        prepare(self.s, self.job['id'])
        self.root = Path(self.job['request']['destination'])
        (self.root / 'voice.c').write_text('int voice(void) { return 0; }\n')
        self.git('init', '-q', '-b', 'main')
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@localhost', 'commit', '-qm', 'fixture')
        self.job = self.s.patch(self.job['id'], state='review_pending', thread_id='mcp:worker')
        self.actual = {'threadId': 'mcp:worker', 'latestRunId': 'run:1', 'status': 'completed',
                       'activeRunId': None, 'pendingRequestCount': 0}
        self.cli_patch = patch.object(self.s, 'cli', side_effect=lambda *a, **kw: json.dumps({'thread': self.actual}))
        self.cli_patch.start()

    def tearDown(self):
        self.cli_patch.stop()
        fixtures.Case.tearDown(self)

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root).decode().strip()

    def call(self, verb, identifier=None, payload=None, owner=None, epoch=None):
        return reviews.dispatch(self.s, owner or self.owner,
                                self.epoch if epoch is None else epoch, verb, identifier, payload)

    def start(self):
        return self.call('prepare', self.job['id'], {'workerRunId': self.actual['latestRunId'],
                         'completionClaim': {'messageId': 'claim:' + self.actual['latestRunId'], 'summary': 'Done'}})

    def bind(self, r, **changes):
        response = {'taskId': 'task:' + r['id'], 'childThreadId': 'mcp:review:' + r['id'],
                    'childRunId': 'review-run:' + r['id'], 'providerInstanceId': 'codex',
                    'model': 'test', 'options': self.selection['options']}
        response.update(changes)
        return self.call('record', r['id'], response)

    def finish(self, r, verdict='verified_complete', findings=None, **changes):
        output = Path(r['outputDirectory'])
        log = output / 'check.log'
        log.write_text('independent check passed\n')
        data = {'workerThreadId': r['workerThreadId'], 'workerRunId': r['workerRunId'],
                'commit': r['commit'], 'verdict': verdict, 'summary': 'Inspected fixture',
                'areas': {k: 'Fixture source and independent evidence for ' + k for k in AREAS},
                'findings': findings or [], 'suggestions': ['Optional naming improvement'],
                'workerAssessment': {'summary': 'Fixture evidence only',
                    'initialDelivery': 'Fixture source inspected',
                    'corrections': [{'reviewId': x['id'], 'outcome': 'not_assessed',
                        'obstacle': 'unknown', 'explanation': 'Fixture has no worker response',
                        'evidence': [x['followupSent']['messageId']]}
                        for x in r.get('previousRounds', []) if x.get('followupSent')],
                    'effort': {'conclusion': 'unknown', 'explanation': 'No runtime evidence', 'evidence': []},
                    'lessons': []},
                'checks': [{'command': ['fixture-check'], 'exitCode': 0,
                            'logPath': str(log), 'logSha256': sha(log)}]}
        data.update(changes)
        (output / 'RESULT.json').write_text(json.dumps(data))
        task = {'taskId': r['taskId'], 'childThreadId': r['childThreadId'],
                'status': 'completed', 'workState': 'result_available', 'hasPendingChildRuns': False}
        return self.call('result', r['id'], {'task': task})

    @staticmethod
    def finding():
        return {'area': 'fidelity', 'path': 'tests/compare.c:10', 'problem': 'Failed check is masked.',
                'impact': 'Broken processing can be reported as passing.',
                'acceptance': 'A planted failure exits nonzero; unchanged valid fixture passes.'}

    def test_prepare_deduplicates_and_survives_store_restart(self):
        r = self.start()
        self.assertEqual(self.start()['id'], r['id'])
        reopened = type(self.s)(self.s.root, firmware=self.s.firmware, projects=self.s.projects)
        self.assertEqual(reviews.rounds(reopened)[0]['clientRequestId'], r['clientRequestId'])
        self.assertEqual(r['delegation']['target']['options'], self.selection['options'])
        self.assertIn('Do not edit the worker project', Path(r['promptPath']).read_text())

    def test_busy_or_unknown_queue_cannot_begin_review(self):
        for change in [{'activeRunId': 'active'}, {'pendingRequestCount': 1},
                       {'pendingRequestCount': None}, {'status': 'waiting'}]:
            with self.subTest(change=change):
                old = dict(self.actual); self.actual.update(change)
                with self.assertRaises(Problem): self.start()
                self.actual = old

    def test_stop_and_stale_owner_block_delegation(self):
        with self.assertRaises(Problem): self.call('list', epoch=self.epoch - 1)
        self.s.patch(self.job['id'], state='stopped')
        with self.assertRaises(Problem): self.start()

    def test_settings_mismatch_retains_task_and_blocks_duplicate(self):
        r = self.start()
        with self.assertRaises(Problem): self.bind(r, model='wrong')
        saved = reviews.get(self.s, r['id'])
        self.assertEqual(saved['state'], 'attention')
        self.assertEqual(saved['taskId'], 'task:' + r['id'])
        with self.assertRaises(Problem): self.call('record', r['id'], {'taskId': 'duplicate'})

    def test_changed_source_rejects_late_result(self):
        r = self.bind(self.start())
        (self.root / 'voice.c').write_text('changed\n')
        with self.assertRaises(Problem): self.finish(r)
        self.assertEqual(reviews.get(self.s, r['id'])['state'], 'stale')

    def test_untracked_source_changes_are_also_detected(self):
        r = self.bind(self.start())
        (self.root / 'new_algorithm.c').write_text('new code\n')
        with self.assertRaises(Problem): self.finish(r)

    def test_replaced_worker_and_new_run_reject_old_result(self):
        r = self.bind(self.start())
        self.actual['latestRunId'] = 'run:2'
        with self.assertRaises(Problem): self.finish(r)
        self.assertEqual(reviews.get(self.s, r['id'])['state'], 'stale')

    def test_assignment_change_invalidates_result(self):
        r = self.bind(self.start())
        p = self.s.data / 'jobs' / self.job['id'] / 'START_PROMPT.txt'
        p.write_text(p.read_text() + '\nChanged assignment')
        with self.assertRaises(Problem): self.finish(r)

    def test_nonterminal_or_unrelated_task_cannot_publish(self):
        r = self.bind(self.start())
        for data in [{'taskId': 'different', 'childThreadId': r['childThreadId'], 'status': 'completed'},
                     {'taskId': r['taskId'], 'childThreadId': r['childThreadId'], 'status': 'running'},
                     {'taskId': r['taskId'], 'childThreadId': r['childThreadId'], 'status': 'completed',
                      'hasPendingChildRuns': True}]:
            with self.assertRaises(Problem): self.call('result', r['id'], {'task': data})

    def test_required_findings_need_consequence_and_acceptance(self):
        r = self.bind(self.start())
        with self.assertRaises(Problem): self.finish(r, 'incomplete', [{'area': 'library', 'problem': 'polish'}])
        with self.assertRaises(Problem): self.finish(r, 'incomplete')
        with self.assertRaises(Problem): self.finish(r, findings=[self.finding()])

    def test_acceptance_imports_logs_and_suggestions_do_not_block(self):
        r = self.finish(self.bind(self.start()))
        review = dict(r['result'], completionReviewId=r['id'])
        self.assertEqual(record_review(self.s, self.job['id'], review)['state'], 'verified_complete')
        self.assertEqual(self.start()['id'], r['id'])
        with self.s.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM checks').fetchone()[0], 1)
        self.call('result', r['id'], {})
        with self.s.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM checks').fetchone()[0], 1)

    def test_old_watcher_self_certification_no_longer_suffices(self):
        r = self.finish(self.bind(self.start()))
        with self.assertRaises(Problem): record_review(self.s, self.job['id'], r['result'])
        (Path(r['outputDirectory']) / 'check.log').write_text('tampered')
        with self.assertRaises(Problem):
            record_review(self.s, self.job['id'], dict(r['result'], completionReviewId=r['id']))

    def test_followup_is_stable_and_only_required_findings_are_sent(self):
        r = self.finish(self.bind(self.start()), 'incomplete', [self.finding()])
        first = self.call('followup', r['id']); second = self.call('followup', r['id'])
        self.assertEqual(first['delivery'], second['delivery'])
        self.assertNotIn('Optional naming', first['delivery']['message'])
        self.call('sent', r['id'], {'threadId': 'mcp:worker', 'messageId': 'correction:1'})
        self.assertFalse(self.call('followup', r['id'])['send'])

    def test_two_corrections_then_escalate(self):
        for n in range(3):
            self.actual['latestRunId'] = f'run:{n}'
            if n == 2:
                self.actual['threadId'] = 'mcp:replacement'
                self.s.patch(self.job['id'], thread_id='mcp:replacement')
            r = self.finish(self.bind(self.start()), 'incomplete', [self.finding()])
            d = self.call('followup', r['id'])
            self.assertEqual(d['send'], n < 2)
            if d['send']:
                self.call('sent', r['id'], {'threadId': 'mcp:worker', 'messageId': f'correction:{n}'})

    def test_handover_can_intake_exact_terminal_child(self):
        r = self.bind(self.start())
        # Create result file, then publish through the documented handover path.
        done = self.finish(r)
        reviews.save(self.s, done, state='running')
        child = {'threadId': r['childThreadId'], 'latestRunId': r['childRunId'],
                 'activeRunId': None, 'pendingRequestCount': 0, 'status': 'completed'}
        self.assertEqual(self.call('result', r['id'], {'childThread': child})['state'], 'completed')

    def test_unknown_task_on_handover_is_not_blindly_redelegated(self):
        r = self.start()
        reviews.save(self.s, r, ownerThreadId='mcp:former-owner')
        with self.assertRaises(Problem): self.start()

    def test_concurrency_and_overdue_are_durable(self):
        r = self.start()
        reviews.save(self.s, r, deadline=0)
        brief = self.call('list')['rounds'][0]
        self.assertTrue(brief['overdue'])
        self.assertNotIn('delegation', brief)
        with self.s.connect() as c:
            for n in range(2):
                c.execute('INSERT INTO completion_reviews VALUES (?,?,?,?,?,?)',
                          (str(n), 'other-job-' + str(n), 'running', '1', '1', '{}'))
        self.call('cancel', r['id'], {'reason': 'Prepared, not sent'})
        with self.assertRaises(Problem): self.start()

    def test_failed_reviewers_do_not_create_an_unbounded_retry_loop(self):
        for n in range(2):
            r = self.bind(self.start())
            self.call('result', r['id'], {'task': {'taskId': r['taskId'],
                      'childThreadId': r['childThreadId'], 'status': 'failed'}})
        with self.assertRaises(Problem): self.start()

    def test_operator_stop_blocks_an_already_prepared_correction(self):
        r = self.finish(self.bind(self.start()), 'incomplete', [self.finding()])
        self.call('followup', r['id'])
        self.s.patch(self.job['id'], state='stopped')
        with self.assertRaises(Problem): self.call('followup', r['id'])
        with self.assertRaises(Problem):
            self.call('sent', r['id'], {'threadId': 'mcp:worker', 'messageId': 'late'})

    def test_request_of_previously_verified_job_is_visible(self):
        self.s.patch(self.job['id'], state='verified_complete')
        reviews.request(self.s, self.job['id'], 'User requests independent review')
        listed = self.call('list')
        self.assertEqual(listed['requests'][0]['job'], self.job['id'])
        self.assertEqual(self.start()['state'], 'prepared')

    def test_explicit_reaudit_does_not_reuse_old_acceptance(self):
        old = self.finish(self.bind(self.start()))
        reviews.request(self.s, self.job['id'], 'New readability audit')
        fresh = self.start()
        self.assertNotEqual(fresh['id'], old['id'])
        self.assertEqual(len(reviews.rounds(self.s)), 2)
        self.assertEqual(self.start()['id'], fresh['id'])

    def test_newer_request_survives_previous_result_and_verdict(self):
        reviews.request(self.s, self.job['id'], 'First audit')
        r = self.bind(self.start())
        reviews.request(self.s, self.job['id'], 'A later audit')
        done = self.finish(r)
        record_review(self.s, self.job['id'], dict(done['result'], completionReviewId=done['id']))
        self.assertEqual(self.call('list')['requests'][0]['reason'], 'A later audit')

    def test_worker_assessment_is_required_and_persisted(self):
        r = self.bind(self.start())
        with self.assertRaises(Problem): self.finish(r, workerAssessment=None)
        done = self.finish(r)
        performance = self.call('performance', self.job['id'])
        self.assertEqual(performance['assessments'][0]['reviewId'], done['id'])
        path = self.s.data / 'jobs' / self.job['id'] / 'WORKER_PERFORMANCE.json'
        self.assertEqual(json.loads(path.read_text())['jobId'], self.job['id'])

    def test_correction_send_time_is_stable(self):
        r = self.finish(self.bind(self.start()), 'incomplete', [self.finding()])
        self.call('followup', r['id'])
        sent = {'threadId': 'mcp:worker', 'messageId': 'correction:1', 'runId': 'run:2'}
        first = self.call('sent', r['id'], sent)
        second = self.call('sent', r['id'], sent)
        self.assertEqual(first['followupSentAt'], second['followupSentAt'])

    def test_historical_corrections_do_not_hide_missing_or_duplicate_deliveries(self):
        prior = self.finish(self.bind(self.start()), 'incomplete', [self.finding()])
        self.call('followup', prior['id'])
        self.call('sent', prior['id'], {'threadId': 'mcp:worker',
                  'messageId': 'correction:1', 'runId': 'run:2'})
        self.actual['latestRunId'] = 'run:2'
        current = self.bind(self.start())

        def entry(review_id):
            return {'reviewId': review_id, 'outcome': 'resolved',
                    'obstacle': 'implementation', 'explanation': 'Verified correction',
                    'evidence': ['historical-delivery.log']}

        assessment = {'summary': 'Includes pre-registry correction evidence',
                      'initialDelivery': 'Fixture source inspected',
                      'corrections': [entry('historical-review')],
                      'effort': {'conclusion': 'unknown', 'explanation': 'No token mapping',
                                 'evidence': []}, 'lessons': []}
        with self.assertRaises(Problem):
            self.finish(current, workerAssessment=assessment)
        assessment['corrections'] += [entry(prior['id']), entry(prior['id'])]
        with self.assertRaises(Problem):
            self.finish(current, workerAssessment=assessment)
        assessment['corrections'].pop()
        done = self.finish(current, workerAssessment=assessment)
        self.assertEqual(done['result']['workerAssessment']['corrections'],
                         assessment['corrections'])

    def test_evidence_outside_round_and_no_checks_cannot_accept(self):
        r = self.bind(self.start())
        with self.assertRaises(Problem): self.finish(r, checks=[])
        external = self.s.root / 'external.log'; external.write_text('pass')
        with self.assertRaises(Problem): self.finish(r, checks=[{'command': ['check'], 'exitCode': 0,
                         'logPath': str(external), 'logSha256': sha(external)}])


if __name__ == '__main__':
    unittest.main()
