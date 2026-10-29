"""Instruction updates preserve provenance and cannot weaken completion guards."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_controller as fixtures
from app import Problem
from controller import (AREAS, REVIEW_POLICY, claim, prepare, record_review,
                        require_current_prompt, reserve_launch)


class PromptPolicyCase(unittest.TestCase):
    setUp = fixtures.Case.setUp
    tearDown = fixtures.Case.tearDown
    enqueue = fixtures.Case.enqueue
    quota = fixtures.Case.quota
    review_fixture = fixtures.Case.review_fixture

    def sources(self):
        directory = self.root/'instructions'
        directory.mkdir()
        template = directory/'START_PROMPT.md'
        template.write_text('Use the assigned algorithms skill.\n')
        (directory/'SKILL.md').write_text('Original synthetic algorithm assignment.\n')
        return template

    def prepared(self):
        job = self.enqueue()
        self.quota()
        claim(self.s, job['id'])
        prepare(self.s, job['id'])
        self.s.patch(job['id'], project_id='project')
        return self.s.job(job['id']), self.s.data/'jobs'/job['id']

    def test_instruction_refresh_is_fenced_and_preserves_assigned_snapshot(self):
        template = self.sources()
        with patch('controller.START_PROMPT', template):
            job, artifacts = self.prepared()
            original_prompt = (artifacts/'START_PROMPT.txt').read_bytes()
            provenance = json.loads((artifacts/'provenance.json').read_text())
            assigned = Path(provenance['assignedSkillPath'])
            original_skill = assigned.read_bytes()
            self.assertIn(str(assigned), original_prompt.decode())
            template.with_name('SKILL.md').write_text('Revised synthetic algorithm assignment.\n')
            with self.assertRaises(Problem):
                reserve_launch(self.s, job['id'], job['modelRevision'])
            self.assertEqual(self.s.job(job['id'])['state'], 'prepared')
            self.assertEqual(assigned.read_bytes(), original_skill)
            prepare(self.s, job['id'])
            history = list((artifacts/'instruction-history').iterdir())
            self.assertEqual(len(history), 1)
            self.assertEqual((history[0]/'START_PROMPT.txt').read_bytes(), original_prompt)
            self.assertEqual((history[0]/'SKILL.md').read_bytes(), original_skill)
            self.assertEqual(json.loads((history[0]/'provenance.json').read_text()), provenance)
            self.assertEqual(assigned.read_bytes(), original_skill)
            require_current_prompt(artifacts)
            self.assertEqual(reserve_launch(self.s, job['id'], job['modelRevision'])['state'], 'launching')
            saved_prompt = (artifacts/'START_PROMPT.txt').read_bytes()
            with self.assertRaises(Problem):prepare(self.s, job['id'])
            self.assertEqual((artifacts/'START_PROMPT.txt').read_bytes(), saved_prompt)

    def test_template_change_requires_repreparation_without_consuming_launch(self):
        template = self.sources()
        with patch('controller.START_PROMPT', template):
            job, artifacts = self.prepared()
            template.write_text('Revised synthetic task.\n')
            with self.assertRaises(Problem):reserve_launch(self.s, job['id'], job['modelRevision'])
            self.assertEqual(self.s.job(job['id'])['state'], 'prepared')
            prepare(self.s, job['id'])
            self.assertEqual(reserve_launch(self.s, job['id'], job['modelRevision'])['state'], 'launching')

    def test_modified_generated_prompt_or_snapshot_cannot_launch(self):
        template = self.sources()
        with patch('controller.START_PROMPT', template):
            job, artifacts = self.prepared()
            prompt = artifacts/'START_PROMPT.txt'
            original_prompt = prompt.read_bytes()
            prompt.write_text('Altered after preparation')
            with self.assertRaises(Problem):reserve_launch(self.s, job['id'], job['modelRevision'])
            prompt.write_bytes(original_prompt)
            provenance = json.loads((artifacts/'provenance.json').read_text())
            snapshot = Path(provenance['assignedSkillPath'])
            snapshot.write_text('Altered assigned skill')
            with self.assertRaises(Problem):reserve_launch(self.s, job['id'], job['modelRevision'])
            with self.assertRaises(Problem):prepare(self.s, job['id'])
            self.assertEqual(snapshot.read_text(), 'Altered assigned skill')

    def test_pre_policy_artifacts_require_refresh_and_remain_archived(self):
        template = self.sources()
        with patch('controller.START_PROMPT', template):
            job, artifacts = self.prepared()
            old_prompt = b'Historical task with mandatory versions.\n'
            (artifacts/'START_PROMPT.txt').write_bytes(old_prompt)
            (artifacts/'provenance.json').write_text('{"historical": true}')
            with self.assertRaises(Problem):reserve_launch(self.s, job['id'], job['modelRevision'])
            prepare(self.s, job['id'])
            history = next((artifacts/'instruction-history').iterdir())
            self.assertEqual((history/'START_PROMPT.txt').read_bytes(), old_prompt)
            self.assertEqual(json.loads((history/'provenance.json').read_text()), {'historical': True})
            require_current_prompt(artifacts)

    def test_outcome_review_does_not_require_preservation_branches(self):
        job, review, _, destination = self.review_fixture()
        self.assertFalse((destination/'exact').exists())
        self.assertFalse((destination/'faithful').exists())
        saved = record_review(self.s, job['id'], review)
        self.assertEqual(saved['state'], 'verified_complete')
        self.assertEqual(saved['review']['reviewPolicy'], REVIEW_POLICY)
        self.assertEqual(set(saved['review']['areas']), AREAS)

    def test_obsolete_review_areas_cannot_substitute_for_new_outcomes(self):
        job, review, _, _ = self.review_fixture()
        review['areas'] = {key:'legacy evidence' for key in
                          ('assignment','original','specification','exact','faithful','main','browser')}
        with self.assertRaises(Problem):record_review(self.s, job['id'], review)
        self.assertEqual(self.s.job(job['id'])['state'], 'review_pending')

    def test_library_only_review_cannot_establish_compiled_device_completion(self):
        job, review, _, _ = self.review_fixture()
        library_only = copy.deepcopy(review)
        del library_only['areas']['device']
        with self.assertRaises(Problem):record_review(self.s, job['id'], library_only)
        previous_policy = copy.deepcopy(review)
        previous_policy['reviewPolicy'] = 'algorithm-library-v1'
        with self.assertRaises(Problem):record_review(self.s, job['id'], previous_policy)
        self.assertEqual(self.s.job(job['id'])['state'], 'review_pending')

    def test_each_outcome_requires_specific_text_evidence(self):
        job, review, _, _ = self.review_fixture()
        for area in AREAS:
            for value in (None, True, [], {}, '', '  '):
                invalid = copy.deepcopy(review)
                invalid['areas'][area] = value
                with self.subTest(area=area, value=value):
                    with self.assertRaises(Problem):record_review(self.s, job['id'], invalid)
            invalid = copy.deepcopy(review)
            del invalid['areas'][area]
            with self.assertRaises(Problem):record_review(self.s, job['id'], invalid)

    def test_new_outcomes_do_not_bypass_findings_or_policy_version(self):
        job, review, _, _ = self.review_fixture()
        invalid = copy.deepcopy(review)
        invalid['findings'] = ['Required processing is missing.']
        with self.assertRaises(Problem):record_review(self.s, job['id'], invalid)
        invalid = copy.deepcopy(review)
        invalid['reviewPolicy'] = 'obsolete-policy'
        with self.assertRaises(Problem):record_review(self.s, job['id'], invalid)
