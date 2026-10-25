import datetime as dt
import http.client
import json
import threading
import unittest
from unittest.mock import patch

import test_controller as fixtures
import dashboard
import engine


class UsageRatesCase(unittest.TestCase):
    setUp=fixtures.Case.setUp
    tearDown=fixtures.Case.tearDown
    quota=fixtures.Case.quota

    def at(self,minutes=0):
        return (dt.datetime(2026,10,25,16,tzinfo=dt.timezone.utc)+dt.timedelta(minutes=minutes)).isoformat()

    def test_claude_boundary_jitter_keeps_rate_and_account_history(self):
        boundary=dt.datetime(2026,10,25,20,tzinfo=dt.timezone.utc)
        for i,jitter in enumerate((.05,.91,.10,.77)):
            self.quota(10+i*2,self.at(i*5),(boundary+dt.timedelta(seconds=jitter)).isoformat(),'claude')
        prediction=engine.forecast(self.s)[0]
        self.assertAlmostEqual(prediction['ratePerHour'],24)
        self.assertEqual(prediction['intervals'],3)
        self.assertEqual(prediction['confidence'],'measured')
        self.assertIsNone(prediction['rateReason'])
        self.assertEqual(prediction['reset_at'],(boundary+dt.timedelta(seconds=.77)).isoformat())

    def test_equivalent_timestamp_formats_keep_baseline(self):
        self.quota(10,self.at(),self.at(240),'claude')
        self.quota(12,self.at(5),self.at(240),'claude')
        # A historical record may use an equivalent timezone/precision format.
        with self.s.connect() as c:
            c.execute('UPDATE quota SET reset_at=? WHERE id=1',('2026-10-25T21:00:00.000+01:00',))
        self.assertAlmostEqual(engine.forecast(self.s)[0]['ratePerHour'],24)

    def test_real_boundary_change_discards_preceding_rate(self):
        self.quota(10,self.at(),self.at(240),'claude')
        self.quota(12,self.at(5),self.at(240),'claude')
        self.quota(14,self.at(10),self.at(540),'claude')
        prediction=engine.forecast(self.s)[0]
        self.assertIsNone(prediction['ratePerHour'])
        self.assertIn('boundary changed',prediction['rateReason'])
        self.quota(15,self.at(15),self.at(540),'claude')
        prediction=engine.forecast(self.s)[0]
        self.assertEqual(prediction['intervals'],1)
        self.assertAlmostEqual(prediction['ratePerHour'],12)

    def test_usage_drop_with_jitter_still_restarts_rate(self):
        for minutes,used,jitter in ((0,50,0),(5,60,.2),(10,20,.4),(15,21,.6)):
            self.quota(used,self.at(minutes),(dt.datetime.fromisoformat(self.at(240))+dt.timedelta(seconds=jitter)).isoformat(),'claude')
        prediction=engine.forecast(self.s)[0]
        self.assertEqual(prediction['intervals'],1)
        self.assertAlmostEqual(prediction['ratePerHour'],12)
        self.assertIn('possible provider replenishment',prediction['resetEvent']['kind'])

    def test_short_sample_spacing_does_not_invent_rate(self):
        self.quota(10,self.at(),self.at(240),'claude')
        self.quota(11,self.at(.2),self.at(240),'claude')
        prediction=engine.forecast(self.s)[0]
        self.assertIsNone(prediction['ratePerHour'])
        self.assertEqual(prediction['intervals'],0)
        self.assertIn('30 seconds',prediction['rateReason'])

    def test_added_account_has_independent_rate(self):
        for i in range(4):
            accounts=[{'provider':'claude','auth_index':'old','usage':{'five_hour':{'utilization':100,'resets_at':self.at(240)}}}]
            if i>=2:
                accounts.append({'provider':'claude','auth_index':'new','usage':{'five_hour':{'utilization':10+(i-2)*5,'resets_at':self.at(300)}}})
            engine.ingest(self.s,{'ok':True,'checked_at':self.at(i*5),'accounts':accounts})
        predictions=engine.forecast(self.s)
        self.assertEqual(len(engine.latest(self.s,'quota')['accounts']),2)
        self.assertEqual(len({p['account'] for p in predictions}),2)
        by_usage={p['used']:p for p in predictions}
        self.assertEqual(by_usage[100]['ratePerHour'],0)
        self.assertEqual(by_usage[100]['intervals'],3)
        self.assertEqual(by_usage[15]['ratePerHour'],60)
        self.assertEqual(by_usage[15]['intervals'],1)
        self.assertEqual(by_usage[15]['confidence'],'low')

    def test_usage_refresh_is_protected_and_does_not_notify_or_launch(self):
        server=dashboard.make_server(self.s,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=http.client.HTTPConnection('127.0.0.1',server.server_port)
            client.request('GET','/api/status');response=client.getresponse()
            token=json.loads(response.read())['csrfToken']
            headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{server.server_port}','X-CSRF-Token':token}
            with patch('engine.sample_usage',side_effect=lambda store:self.quota(17,provider='claude')) as sample,patch.object(self.s,'cli') as cli:
                for bad in ({},headers|{'Origin':'http://evil.example'},headers|{'X-CSRF-Token':'wrong'}):
                    client.request('POST','/api/usage/refresh','{}',bad);response=client.getresponse()
                    self.assertEqual(response.status,403);response.read()
                sample.assert_not_called()
                client.request('POST','/api/usage/refresh','{}',headers);response=client.getresponse()
                self.assertEqual(response.status,200)
                self.assertEqual(json.loads(response.read())['accounts'][0]['windows'][0]['used'],17)
                sample.assert_called_once_with(self.s);cli.assert_not_called()
            self.assertEqual(self.s.jobs(),[])
            client.request('GET','/api/status');response=client.getresponse()
            self.assertEqual(json.loads(response.read())['usage']['accounts'][0]['windows'][0]['used'],17)
            client.close()
        finally:
            server.shutdown();server.server_close();thread.join()
