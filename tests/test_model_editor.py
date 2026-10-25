import copy
import http.client
import json
import threading
import unittest
from unittest.mock import patch

import test_controller as fixtures
from app import Problem, Store, now, sha
from controller import atomic_json, claim, prepare, reserve_launch
import dashboard
import engine
import model_editor as models


class ModelCase(unittest.TestCase):
    setUp = fixtures.Case.setUp
    tearDown = fixtures.Case.tearDown
    enqueue = fixtures.Case.enqueue
    quota = fixtures.Case.quota

    def change(self, job, **values):
        return {'expectedRevision': job['modelRevision'],
                'selection': copy.deepcopy(self.selection) | {'options': {'effort': 'max', 'fast': True}},
                'quotaPool': 'codex'} | values

    def test_edit_preserves_assignment_and_enqueue_idempotency(self):
        job = self.enqueue()
        changed = models.update_model(self.s, job['id'], self.change(job))
        expected = copy.deepcopy(job['request'])
        expected.update(selection=self.change(job)['selection'], modelGeneration=1)
        self.assertEqual(changed['request'], expected)
        self.assertNotEqual(changed['modelRevision'], job['modelRevision'])
        self.assertEqual(self.enqueue()['request'], expected)
        with self.s.connect() as c:
            event = c.execute("SELECT detail FROM events WHERE kind='model_changed'").fetchone()
        self.assertEqual(json.loads(event[0])['previous']['selection'], job['request']['selection'])

    def test_stale_editor_and_aba_revision_rejected(self):
        job = self.enqueue()
        changed = models.update_model(self.s, job['id'], self.change(job))
        with self.assertRaisesRegex(Problem, 'changed elsewhere'):
            models.update_model(self.s, job['id'], self.change(job))
        restored = models.update_model(self.s, job['id'], self.change(changed, selection=self.selection))
        self.assertNotEqual(restored['modelRevision'], job['modelRevision'])

    def test_invalid_models_options_pools_and_disabled_providers(self):
        job = self.enqueue()
        for value in ({'selection': self.selection | {'model': 'invented'}},
                      {'selection': self.selection | {'options': {'effort': 'max'}}},
                      {'selection': self.selection | {'options': {'effort': 'max', 'fast': 'false'}}},
                      {'quotaPool': 'invented'}):
            with self.assertRaises(Problem):models.update_model(self.s, job['id'], self.change(job, **value))
        catalog = self.s.capabilities();catalog['providers'][0]['constraints'] = ['Disabled']
        atomic_json(self.s.data/'capabilities.json', catalog)
        self.assertEqual(models.catalog_snapshot(self.s)['providers'], [])
        with self.assertRaisesRegex(Problem, 'Disabled'):
            models.update_model(self.s, job['id'], self.change(job))

    def test_pool_change_and_dynamic_provider_options(self):
        job = self.enqueue()
        custom = {'instanceId': 'new-provider', 'model': 'custom-opus', 'options': {'effort': 'xhigh'}}
        catalog = self.s.capabilities()
        catalog['providers'].append({'providerInstanceId': 'new-provider', 'constraints': [], 'models': [
            {'id': 'custom-opus', 'options': [{'id': 'effort', 'type': 'select', 'options': [{'id': 'xhigh'}]}]}]})
        atomic_json(self.s.data/'capabilities.json', catalog)
        changed = models.update_model(self.s, job['id'], self.change(job, selection=custom, quotaPool='claude'))
        self.assertEqual(changed['request']['selection'], custom)
        self.assertEqual(changed['request']['quotaPool'], 'claude')
        self.quota(provider='codex')
        with self.assertRaises(Problem):claim(self.s, job['id'])

    def test_prepared_edit_requeues_archives_and_clears_approval(self):
        job = self.enqueue();self.quota();claim(self.s, job['id']);prepare(self.s, job['id'])
        self.s.patch(job['id'], project_id='project')
        engine.request_launch(self.s, job['id'])
        prepared = self.s.job(job['id']);original = prepared['request']['destination']+'/originals/vendor/fw.bin'
        original_hash = sha(original)
        changed = models.update_model(self.s, job['id'], self.change(prepared))
        self.assertEqual(changed['state'], 'queued');self.assertEqual(changed['project_id'], 'project')
        self.assertIsNone(engine.launch_approval(self.s, changed))
        self.assertEqual(sha(original), original_hash)
        archive = self.s.data/'jobs'/job['id']/'model-history'/job['modelRevision']
        self.assertTrue((archive/'provenance.json').is_file())
        with self.assertRaises(Problem):claim(self.s, job['id']) # old sample consumed
        self.quota();claim(self.s, job['id']);prepare(self.s, job['id'])
        settings = json.loads((self.s.projects/job['slug']/'LAUNCH_SETTINGS.json').read_text())
        self.assertEqual(settings['selection'], changed['request']['selection'])

    def test_launch_reservation_rejects_stale_model_and_freezes_edits(self):
        job = self.enqueue();self.quota();claim(self.s, job['id']);prepare(self.s, job['id'])
        self.s.patch(job['id'], project_id='project')
        changed = models.update_model(self.s, job['id'], self.change(job))
        self.quota();claim(self.s, job['id']);prepare(self.s, job['id'])
        with self.assertRaisesRegex(Problem, 'settings changed'):
            reserve_launch(self.s, job['id'], job['modelRevision'])
        launched = reserve_launch(self.s, job['id'], changed['modelRevision'])
        self.assertEqual(launched['state'], 'launching');self.assertFalse(launched['modelEditable'])
        with self.assertRaisesRegex(Problem, 'before a job begins launching'):
            models.update_model(self.s, job['id'], self.change(launched))

    def test_reject_all_existing_worker_and_terminal_states(self):
        job = self.enqueue()
        for state in ('launching', 'running', 'usage_limited', 'waiting', 'review_pending', 'incomplete',
                      'attention', 'recovery_pending', 'replacement_ready', 'replacement_launching',
                      'verified_complete', 'cancelled'):
            current = self.s.patch(job['id'], state=state)
            self.assertFalse(current['modelEditable'])
            with self.assertRaises(Problem):models.update_model(self.s, job['id'], self.change(current))
        current = self.s.patch(job['id'], state='queued', thread_id='worker')
        with self.assertRaises(Problem):models.update_model(self.s, job['id'], self.change(current))

    def test_catalog_refresh_survives_busy_watcher_and_pause(self):
        config = self.s.config() | {'watcherMessagesPaused': True}
        atomic_json(self.s.data/'config.json', config)
        with self.s.connect() as c:c.execute("UPDATE watcher SET thread='watcher',epoch=1,phase='ready'")
        with patch.object(self.s, 'cli', return_value=json.dumps({'thread': {'activeRunId': 'busy'}})):
            result = models.request_catalog(self.s)
        self.assertTrue(result['refreshPending']);self.assertFalse(result['notification']['sent'])
        restarted = Store(self.root, firmware=self.s.firmware, projects=self.s.projects)
        with patch.object(restarted, 'cli', side_effect=[json.dumps({'thread': {}}), '{}']) as cli:
            self.assertTrue(engine.wake(restarted)['sent'])
            self.assertIn('refresh the live model catalog', cli.call_args.kwargs['input'])
        with patch.object(restarted, 'cli') as cli:
            self.assertFalse(engine.wake(restarted)['sent']);cli.assert_not_called()
        catalog = self.s.capabilities() | {'fetchedAt': now()}
        atomic_json(self.s.data/'capabilities.json', catalog)
        self.assertFalse(models.catalog_snapshot(self.s)['refreshPending'])
        self.assertFalse(models.catalog_snapshot(self.s)['stale'])
        self.assertTrue(self.s.config()['watcherMessagesPaused'])

    def test_missing_catalog_reports_unknown(self):
        (self.s.data/'capabilities.json').unlink()
        value = models.catalog_snapshot(self.s)
        self.assertEqual(value['providers'], []);self.assertTrue(value['stale']);self.assertIsNone(value['fetchedAt'])

    def test_http_model_actions_require_same_origin_and_revision(self):
        job = self.enqueue()
        server = dashboard.make_server(self.s, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        try:
            client = http.client.HTTPConnection('127.0.0.1', server.server_port)
            client.request('GET', '/api/status');r = client.getresponse();token = json.loads(r.read())['csrfToken']
            client.request('GET', '/api/models');r = client.getresponse()
            self.assertEqual(r.status, 200);self.assertEqual(len(json.loads(r.read())['providers']), 1)
            headers = {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{server.server_port}', 'X-CSRF-Token': token}
            path = '/api/jobs/'+job['id']+'/model';body = json.dumps(self.change(job))
            for bad in ({}, headers | {'Origin': 'http://evil.example'}, headers | {'X-CSRF-Token': 'wrong'}):
                client.request('POST', path, body, bad);r = client.getresponse();self.assertEqual(r.status, 403);r.read()
            client.request('POST', path, body, headers);r = client.getresponse();self.assertEqual(r.status, 200);r.read()
            client.request('POST', path, body, headers);r = client.getresponse();self.assertEqual(r.status, 409);r.read()
            with patch.object(self.s, 'cli'):
                client.request('POST', '/api/models/refresh', '{}', headers);r = client.getresponse()
                self.assertEqual(r.status, 200);self.assertTrue(json.loads(r.read())['refreshPending'])
            client.close()
        finally:server.shutdown();server.server_close();thread.join()
