import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Problem
import review_performance as p


class PerformanceTests(unittest.TestCase):
    def test_elapsed_rejects_unknown_negative_and_naive_dates(self):
        for start, end in [(None, None), ('bad', 'bad'),
                ('2026-10-28T01:00:00Z', '2026-10-28T00:00:00Z'),
                ('2026-10-28T00:00:00', '2026-10-28T01:00:00')]:
            self.assertIsNone(p.elapsed(start, end))

    def test_runs_are_deduplicated_and_missing_time_is_unknown(self):
        run = {'runId': 'a', 'startedAt': '2026-10-28T00:00:00Z',
               'completedAt': '2026-10-28T00:02:00Z'}
        r = p.timeline_summary([run, run, {'runId': 'b'}])
        self.assertEqual(r['observedRuns'], 2)
        self.assertEqual(r['summedRunElapsedSeconds'], 120)
        self.assertEqual(r['unknownDurationRunIds'], ['b'])
        self.assertIsNone(p.timeline_summary([])['summedRunElapsedSeconds'])

    def test_correction_duration_is_tied_to_actual_delivery_run(self):
        job = {'id': 'j', 'name': 'Device', 'request': {}}
        rounds = [{'id': 'first', 'workerThreadId': 'w',
                   'followupSentAt': '2026-10-28T00:00:00Z',
                   'followupSent': {'messageId': 'm', 'runId': 'fix'}},
                  {'id': 'second', 'workerThreadId': 'w', 'workerRunId': 'fix',
                   'state': 'completed', 'result': {'verdict': 'verified_complete'},
                   'workerTimeline': [{'runId': 'unrelated', 'startedAt': '2026-10-27T00:00:00Z'},
                       {'runId': 'fix', 'startedAt': '2026-10-28T00:01:00Z',
                        'completedAt': '2026-10-28T00:03:00Z'}]}]
        r = p.build(job, rounds)['corrections'][0]
        self.assertEqual(r['responseWallSeconds'], 180)
        self.assertEqual(r['runElapsedSeconds'], 120)
        self.assertEqual(r['nextReviewVerdict'], 'verified_complete')

    def test_unsupported_effort_judgments_are_rejected(self):
        assessment = {'summary': 'Observed outcome', 'initialDelivery': 'Evidence',
                      'corrections': [], 'lessons': [], 'effort': {
                          'conclusion': 'unknown', 'explanation': 'No history', 'evidence': []}}
        p.validate(assessment)
        unsupported = copy.deepcopy(assessment)
        unsupported['effort']['conclusion'] = 'potentially_excessive'
        with self.assertRaises(Problem): p.validate(unsupported)


if __name__ == '__main__':
    unittest.main()
