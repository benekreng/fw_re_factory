import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_controller as fixtures
from app import Problem
from controller import atomic_json,record_review
import engine
import recovery


class RecoveryCase(unittest.TestCase):
    enqueue=fixtures.Case.enqueue
    quota=fixtures.Case.quota
    def setUp(self):
        fixtures.Case.setUp(self)
        self.j,self.old,self.new,self.watcher,self.actual,self.retired,self.cli=fixtures.Case.replacement_fixture(self)
        self.dest=Path(self.j['request']['destination']);self.dest.mkdir(parents=True)
        atomic_json(self.dest/'LAUNCH_REQUEST.json',self.j['request'])
        (self.dest/'STATUS.md').write_text('Current project evidence\n')
        original=self.dest/'originals/vendor/fw.bin';original.parent.mkdir(parents=True);original.write_bytes(self.file.read_bytes())
        self.p={'enabled':True,'source':'Synthetic user policy','maxReplacementsPerJob':3,'cooldownSeconds':0,
                'rules':[{'id':'synthetic-rule','when':'Synthetic observed protocol failure','action':'repair_then_replace',
                          'repairSteps':['Synthetic repair step'],'verify':'Synthetic successful response','maxRepairAttempts':1}]}
    def tearDown(self):fixtures.Case.tearDown(self)
    def call(self,action,identifier=None,payload=None,epoch=None):
        return recovery.dispatch(self.s,self.watcher,engine.owner(self.s)['epoch'] if epoch is None else epoch,action,identifier,payload)
    def enable(self,action='repair_then_replace'):
        self.p['rules'][0]['action']=action
        if action=='replace':self.p['rules'][0].update(repairSteps=[],verify='',maxRepairAttempts=0)
        with engine.lock(self.s):recovery.set_policy(self.s,self.p)
    def report(self):
        return self.call('report',self.j['id'],{'ruleId':'synthetic-rule','expectedThreadId':self.old,'evidence':'OLD_CONVERSATION_SENTINEL: observed test symptom'})
    def ready(self):
        self.enable('replace');ticket=self.report()
        with patch.object(self.s,'cli',side_effect=self.cli):return self.call('prepare',ticket['id'])
    def launch(self):
        ticket=self.ready();self.quota(1)
        with patch.object(self.s,'cli',side_effect=self.cli):self.call('launch',ticket['id'])
        return ticket
    def result(self):
        return {'threadId':self.new,'projectId':'p','modelSelection':self.selection,'status':'preparing'}
    def test_disabled_policy_cannot_start_recovery(self):
        with self.assertRaises(Problem):self.report()
        self.assertEqual(recovery.records(self.s),[])
    def test_enabled_policy_requires_explicit_criteria_and_repair_steps(self):
        with self.assertRaises(Problem):recovery.validate_policy({**self.p,'rules':[]})
        p=copy.deepcopy(self.p);p['rules'][0]['repairSteps']=[]
        with self.assertRaises(Problem):recovery.validate_policy(p)
    def test_one_open_incident_and_policy_snapshot(self):
        self.enable();first=self.report();second=self.report()
        self.assertEqual(first['id'],second['id']);self.assertEqual(len(recovery.records(self.s)),1)
        self.assertEqual(first['policy']['source'],'Synthetic user policy')
        self.assertEqual(self.s.job(self.j['id'])['state'],'recovery_pending')
    def test_unknown_rule_and_stale_observation_rejected(self):
        self.enable()
        for payload in [{'ruleId':'unknown','expectedThreadId':self.old,'evidence':'x'},
                        {'ruleId':'synthetic-rule','expectedThreadId':'stale','evidence':'x'}]:
            with self.assertRaises(Problem):self.call('report',self.j['id'],payload)
        self.assertEqual(recovery.records(self.s),[])
    def test_stale_watcher_cannot_open_incident(self):
        self.enable()
        with self.assertRaises(Problem):self.call('report',self.j['id'],{'ruleId':'synthetic-rule','expectedThreadId':self.old,'evidence':'x'},epoch=4)
    def test_repair_attempt_is_reserved_and_cannot_repeat_uncertain_step(self):
        self.enable();r=self.report();begun=self.call('repair_begin',r['id'])
        self.assertEqual(begun['repair_attempts'],1)
        with self.assertRaises(Problem):self.call('repair_begin',r['id'])
        failed=self.call('repair_result',r['id'],{'working':False,'evidence':'Test verification still fails'})
        self.assertEqual(failed['phase'],'retire_pending')
        with self.assertRaises(Problem):self.call('repair_begin',r['id'])
    def test_successful_repair_closes_without_replacement(self):
        self.enable();r=self.report();self.call('repair_begin',r['id']);self.retired['archived']=False
        with patch.object(self.s,'cli',side_effect=self.cli):
            done=self.call('repair_result',r['id'],{'working':True,'evidence':'Prescribed verification succeeded'})
        self.assertEqual(done['phase'],'recovered');self.assertIsNone(done['replacement_thread'])
        self.assertEqual(self.s.job(self.j['id'])['thread_id'],self.old)
    def test_policy_change_blocks_further_starts_but_allows_outcome_record(self):
        self.enable();r=self.report();self.call('repair_begin',r['id'])
        with engine.lock(self.s):recovery.set_policy(self.s,{**self.p,'enabled':False})
        failed=self.call('repair_result',r['id'],{'working':False,'evidence':'Observed unsuccessful outcome'})
        self.assertEqual(failed['phase'],'retire_pending')
        with self.assertRaises(Problem):self.call('prepare',r['id'])
    def test_clean_prompt_contains_no_retired_thread_or_error_context(self):
        r=self.ready();prompt=Path(r['promptPath']).read_text()
        self.assertNotIn(self.old,prompt);self.assertNotIn('OLD_CONVERSATION_SENTINEL',prompt)
        self.assertNotIn('Synthetic repair step',prompt);self.assertNotIn('Job ID:',prompt)
        from controller import render_worker_prompt
        normal,base,_=render_worker_prompt(self.s.job(self.j['id']))
        task=base.read_text()
        self.assertIn('Another agent has already worked on this project',prompt)
        self.assertIn('Inspect and verify what is already there',prompt)
        self.assertEqual(prompt.split('--- BEGIN TASK PROMPT ---\n',1)[1].split('\n--- END TASK PROMPT ---',1)[0],task)
        details='\n\nRun details (these settings override the defaults above):\n'
        self.assertEqual(prompt.split(details,1)[1],normal.split(details,1)[1])
        self.assertEqual(prompt.count(details),1)
        self.assertGreater(prompt.index(details),prompt.index('--- END TASK PROMPT ---'))
        self.assertNotIn('Another agent has already worked on this project',normal)
        self.assertEqual((self.dest/'STATUS.md').read_text(),'Current project evidence\n')
    def test_old_thread_must_be_retired_before_preparing(self):
        self.enable('replace');r=self.report();self.retired['archived']=False
        with patch.object(self.s,'cli',side_effect=self.cli):
            with self.assertRaises(Problem):self.call('prepare',r['id'])
    def test_changed_original_blocks_replacement_without_overwriting_it(self):
        self.enable('replace');r=self.report();original=self.dest/'originals/vendor/fw.bin';original.write_bytes(b'changed')
        with patch.object(self.s,'cli',side_effect=self.cli):
            with self.assertRaises(Problem):self.call('prepare',r['id'])
        self.assertEqual(original.read_bytes(),b'changed')
    def test_retired_worker_cannot_be_recorded_as_its_own_replacement(self):
        r=self.launch();result=self.result();result['threadId']=self.old
        with self.assertRaises(Problem):self.call('record',r['id'],result)
        self.assertEqual(recovery.get(self.s,r['id'])['phase'],'launching')

    def test_unknown_quota_blocks_external_launch_intent(self):
        r=self.ready()
        with patch.object(self.s,'cli',side_effect=self.cli):
            with self.assertRaises(Problem):self.call('launch',r['id'])
        self.assertEqual(recovery.get(self.s,r['id'])['phase'],'replacement_ready')
    def test_launch_intent_cannot_be_repeated_after_lost_response(self):
        r=self.launch()
        with patch.object(self.s,'cli',side_effect=self.cli) as calls:
            with self.assertRaises(Problem):self.call('launch',r['id'])
            calls.assert_not_called()
        self.assertEqual(recovery.get(self.s,r['id'])['phase'],'launching')
        self.assertEqual(self.s.job(self.j['id'])['state'],'replacement_launching')
    def test_record_binds_exact_candidate_and_is_idempotent(self):
        r=self.launch()
        with patch.object(self.s,'cli',side_effect=self.cli):done=self.call('record',r['id'],self.result())
        self.assertEqual(done['phase'],'replaced');self.assertEqual(done['watcher']['epoch'],6)
        job=self.s.job(self.j['id']);self.assertEqual(job['thread_id'],self.new)
        self.assertTrue(job['launch_result']['optionsVerified']);self.assertIsNone(job['review'])
        self.assertEqual(job['observation']['contextPolicy'],'current_thread_and_project_only')
        self.assertEqual(self.call('record',r['id'],self.result())['phase'],'replaced')
        self.assertEqual(len(self.s.jobs()),1)
    def test_wrong_options_retain_candidate_and_block_another_incident(self):
        r=self.launch();result=self.result();result['modelSelection']=copy.deepcopy(self.selection)
        result['modelSelection']['options']['fast']=True
        with self.assertRaises(Problem):self.call('record',r['id'],result)
        saved=recovery.get(self.s,r['id']);self.assertEqual(saved['phase'],'attention');self.assertEqual(saved['replacement_thread'],self.new)
        with self.assertRaises(Problem):self.report()
    def test_crash_after_binding_can_be_reconciled_without_new_launch(self):
        r=self.launch();original=recovery._update
        def update(s,identifier,**values):
            if values.get('phase')=='replaced':raise OSError('Simulated lost close acknowledgment')
            return original(s,identifier,**values)
        with patch.object(self.s,'cli',side_effect=self.cli),patch('recovery._update',side_effect=update):
            with self.assertRaises(OSError):self.call('record',r['id'],self.result())
        self.assertEqual(self.s.job(self.j['id'])['thread_id'],self.new)
        self.assertEqual(recovery.get(self.s,r['id'])['phase'],'launching')
        self.assertEqual(self.call('record',r['id'],self.result())['phase'],'replaced')
    def test_replacement_cap_and_cooldown_prevent_loops(self):
        r=self.launch()
        with patch.object(self.s,'cli',side_effect=self.cli):self.call('record',r['id'],self.result())
        for cap,cooldown in [(1,0),(3,900)]:
            p=copy.deepcopy(self.p);p.update(maxReplacementsPerJob=cap,cooldownSeconds=cooldown)
            with engine.lock(self.s):recovery.set_policy(self.s,p)
            with self.assertRaises(Problem):self.call('report',self.j['id'],{'ruleId':'synthetic-rule','expectedThreadId':self.new,'evidence':'New matched symptom'})
    def test_policy_updates_never_unpause_or_message_watcher(self):
        config=self.s.config();config['watcherMessagesPaused']=True;atomic_json(self.s.data/'config.json',config)
        with patch.object(self.s,'cli') as calls:
            self.enable();self.assertFalse(engine.wake(self.s,force=True)['sent']);calls.assert_not_called()
        self.assertTrue(self.s.config()['watcherMessagesPaused'])
    def test_launch_intent_survives_restart_and_cannot_launch_twice(self):
        from app import Store
        r=self.launch();saved=recovery.get(self.s,r['id'])
        self.assertEqual(saved['result']['launchIntent']['projectId'],'p')
        self.assertIn(r['id'][:8],saved['result']['launchIntent']['title'])
        restarted=Store(self.root,firmware=self.s.firmware,projects=self.s.projects)
        with patch.object(restarted,'cli') as calls:
            with self.assertRaises(Problem):recovery.dispatch(restarted,self.watcher,5,'launch',r['id'])
            calls.assert_not_called()
    def test_native_option_array_is_verified(self):
        r=self.launch();result=self.result()
        result['modelSelection']={**self.selection,'options':[{'id':k,'value':v} for k,v in self.selection['options'].items()]}
        with patch.object(self.s,'cli',side_effect=self.cli):done=self.call('record',r['id'],result)
        self.assertEqual(done['phase'],'replaced')
    def test_quota_exhaustion_and_scheduling_pause_hold_replacement(self):
        r=self.ready();self.quota(100)
        with patch.object(self.s,'cli',side_effect=self.cli):
            with self.assertRaises(Problem):self.call('launch',r['id'])
        self.quota(1);config=self.s.config();config['schedulingPaused']=True;atomic_json(self.s.data/'config.json',config)
        with patch.object(self.s,'cli',side_effect=self.cli):
            with self.assertRaises(Problem):self.call('launch',r['id'])
        self.assertEqual(recovery.get(self.s,r['id'])['phase'],'replacement_ready')
    def test_manual_adoption_cannot_hide_a_different_uncertain_candidate(self):
        r=self.launch();result=self.result();result['modelSelection']=copy.deepcopy(self.selection);result['modelSelection']['options']['fast']=True
        with self.assertRaises(Problem):self.call('record',r['id'],result)
        with self.assertRaises(Problem):engine.replace_worker(self.s,self.j['id'],'another-thread')

    def test_pending_recovery_cannot_be_mistaken_for_completion(self):
        self.enable();self.report()
        with self.assertRaises(Problem):record_review(self.s,self.j['id'],{})


if __name__=='__main__':unittest.main()
