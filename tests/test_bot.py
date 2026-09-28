import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import bot

NOW = 1790812800.0
BASE = Path(__file__).resolve().parents[1]

class BotTests(unittest.TestCase):
    def setUp(self):
        self.c = bot.load_config(BASE / 'config.json')
        self.c['templates']['price']='CZR/USDT: {price} USDT\nSource: CZR Exchange\nTicker time: {timestamp}\n{link}'
        self.c.pop('launch_date', None)
        self.c.pop('start_at_utc', None)
        self.c['price_anchor_utc']='00:00'
        self.c['daily_utc']='00:10'
        self.raw = json.loads((BASE / 'examples/synthetic-ticker.json').read_text())
        self.tmp = tempfile.TemporaryDirectory()
        self.s = bot.Store(Path(self.tmp.name) / 'test.sqlite3')
        self.calls = []
        self.env = patch.dict(os.environ, {'CZR_LIVE_POSTING': 'true', 'X_USER_ACCESS_TOKEN': 'TEST_TOKEN', 'X_EXPECTED_USER_ID': '123', 'TEAM_ALERT_WEBHOOK_URL': 'https://alerts.invalid/hook'}, clear=True)
        self.env.start()
    def tearDown(self):
        self.env.stop(); self.s.db.close(); self.tmp.cleanup()
    def verify(self):
        self.c['provider_verification'] = dict.fromkeys(self.c['provider_verification'], 'test-only confirmation')
        os.environ['CZR_APPROVED_CONFIG_SHA256'] = bot.digest(self.c)
    def http(self, method, url, payload=None, token=None):
        self.calls.append((method, url, payload))
        if 'ticker?' in url: return self.raw
        if url.endswith('/users/me'): return {'data': {'id': '123', 'username': 'czrpricebot'}}
        if url.endswith('/tweets'): return {'data': {'id': '456'}}
        return {}
    def runner(self, transport=None):
        return bot.Bot(self.c, self.s, transport or self.http, sleep=lambda _: None, now=lambda: NOW)
    def status(self):
        return self.s.db.execute('SELECT status FROM jobs ORDER BY rowid DESC LIMIT 1').fetchone()[0]
    def test_fresh_exact_render(self):
        t=bot.render(self.c,'price',bot.ticker(self.c,self.raw,NOW,True))
        for expected in ['0.01234 USDT','2026-10-01 00:00 UTC',bot.LINK,'CZR Exchange']: self.assertIn(expected,t)
        self.assertNotIn('24h',t)
    def test_missing_invalid_stale_future(self):
        for field,value in [('last',None),('last','NaN'),('last','Infinity'),('last',-1),('last',True),('time',(NOW-301)*1000),('time',(NOW+31)*1000),('symbol','other')]:
            with self.subTest(field=field,value=value), self.assertRaises(bot.Refused):
                bot.ticker(self.c,dict(self.raw,**{field:value}),NOW,True)
    def test_unverified_fields_block(self):
        with self.assertRaises(bot.Refused): bot.ticker(self.c,self.raw,NOW)
        self.c['include_24h']=True
        with self.assertRaises(bot.Refused): bot.ticker(self.c,self.raw,NOW,True)
    def test_percent_independent_of_rose(self):
        self.verify(); self.c['include_24h']=True; self.raw['rose']='999'
        d=bot.ticker(self.c,self.raw,NOW)
        self.assertIn('+2.83%',d['stats']); self.assertIn('1000 CZR',d['stats'])
    def test_inconsistent_prices(self):
        self.verify(); self.c['include_24h']=True; self.raw['high']='0.001'
        with self.assertRaises(bot.Refused): bot.ticker(self.c,self.raw,NOW)
    def test_https_required(self):
        for url in ['http://example.org','https://user:pass@example.org','file:///tmp/a']:
            with self.assertRaises(bot.Refused): bot.https(url)
    def test_utc_schedules(self):
        for offset,expected in [(0,['price']),(600,['daily']),(14400,['price']),(1000,[])]:
            self.assertEqual([k for k,_ in bot.slots(self.c,NOW+offset)],expected)
    def test_dry_duplicate_unchanged_no_network(self):
        b=self.runner()
        self.assertIsNotNone(b.run('price','1',raw=self.raw,fixture=True))
        self.assertIsNone(b.run('price','1',raw=self.raw,fixture=True))
        self.assertIsNone(b.run('price','2',raw=dict(self.raw,time=(NOW+1)*1000),fixture=True))
        self.assertEqual(self.status(),'unchanged'); self.assertEqual(self.calls,[])
    def test_daily_independent(self):
        b=self.runner(); b.run('price','1',raw=self.raw,fixture=True)
        self.assertIsNotNone(b.run('daily','1',raw=self.raw,fixture=True))
    def test_default_live_refused(self):
        self.assertIsNone(self.runner().run('price','1',live=True)); self.assertEqual(self.calls,[])
    def test_approval_invalidated_by_change(self):
        self.verify(); self.c['templates']['price']+='!'
        self.assertIsNone(self.runner().run('price','1',live=True)); self.assertEqual(self.calls,[])
    def test_live_cannot_use_fixture_or_preview(self):
        self.verify()
        self.assertIsNone(self.runner().run('price','1',live=True,raw=self.raw,fixture=True))
        self.assertIsNone(self.runner().run('price','1',live=True,preview=True))
        self.assertEqual(self.calls,[])
    def test_durable_success_id_and_restart_dedupe(self):
        self.verify()
        self.assertIsNotNone(self.runner().run('price','1',live=True)); self.assertEqual(self.status(),'posted')
        self.assertEqual(self.s.db.execute('SELECT post_id FROM jobs').fetchone()[0],'456')
        self.s.db.close()
        self.s = bot.Store(Path(self.tmp.name) / 'test.sqlite3')
        self.assertIsNone(self.runner().run('price','1',live=True))
        self.assertIsNone(self.runner().run('price','2',live=True))
        self.assertEqual(sum(u.endswith('/tweets') for _,u,_ in self.calls),1)
    def test_wrong_account(self):
        self.verify()
        def http(m,u,p=None,token=None):
            return {'data':{'id':'999','username':'other'}} if u.endswith('/users/me') else self.http(m,u,p,token)
        self.assertIsNone(self.runner(http).run('price','1',live=True))
        self.assertFalse(any(u.endswith('/tweets') for _,u,_ in self.calls))
    def test_ambiguous_submission_blocks_and_alerts(self):
        self.verify()
        def http(m,u,p=None,token=None):
            if u.endswith('/tweets'):
                self.calls.append((m,u,p)); raise bot.RemoteError(504)
            return self.http(m,u,p,token)
        b=self.runner(http); b.run('price','1',live=True)
        self.assertEqual(self.status(),'pending')
        b.run('daily','2',live=True); b.run('price','3',live=True)
        self.assertEqual(sum(u.endswith('/tweets') for _,u,_ in self.calls),1)
        self.assertTrue(any('alerts.invalid' in u for _,u,_ in self.calls))
    def test_x_429_cooldown(self):
        self.verify()
        def http(m,u,p=None,token=None):
            if u.endswith('/tweets'): raise bot.RemoteError(429,9999)
            return self.http(m,u,p,token)
        self.runner(http).run('price','1',live=True)
        self.assertEqual(self.status(),'rejected'); self.assertEqual(self.s.get('x_not_before'),NOW+9999)
    def test_ticker_429_no_retry(self):
        self.verify(); calls=[]
        def http(*a,**kw): calls.append(1); raise bot.RemoteError(429,9999)
        self.runner(http).run('price','1')
        self.assertEqual(len(calls),1); self.assertEqual(self.s.get('ticker_not_before'),NOW+9999)
    def test_transient_get_bounded_retries(self):
        self.verify(); calls=[]
        def http(*a,**kw): calls.append(1); raise bot.RemoteError(503)
        self.runner(http).run('price','1'); self.assertEqual(len(calls),3)
    def test_alert_threshold_and_cooldown(self):
        b=self.runner()
        for _ in range(4): b.failure('ticker','unavailable')
        self.assertEqual(sum('alerts.invalid' in u for _,u,_ in self.calls),1)
    def test_lock_excludes_second_worker(self):
        path=Path(self.tmp.name)/'lock'
        with bot.lock(path):
            with self.assertRaises(bot.Refused):
                with bot.lock(path): pass
    def test_post_length(self):
        self.c['templates']['price']+='a'*300
        with self.assertRaises(bot.Refused): bot.render(self.c,'price',bot.ticker(self.c,self.raw,NOW,True))
    def test_read_only_live_preview_without_symbol_field(self):
        self.raw.pop('symbol')
        self.assertIsNotNone(self.runner().run('price','1',preview=True))
        self.assertEqual(len(self.calls),1); self.assertIn('symbol=czrtoken1858usdt1858',self.calls[0][1])

    def test_oauth1_published_reference_signature(self):
        credentials = {'X_API_KEY':'dpf43f3p2l4k3l03', 'X_API_KEY_SECRET':'kd94hf93k423kf44', 'X_ACCESS_TOKEN':'nnch734d00sl2jdk', 'X_ACCESS_TOKEN_SECRET':'pfkkdhi9sl3r4s00'}
        header = bot.oauth_header('GET', 'http://photos.example.net/photos?file=vacation.jpg&size=original', credentials, nonce='kllo9940pd9333jh', timestamp=1191242096)
        self.assertIn('oauth_signature="tR3%2BTy81lMeYAr%2FFid0kMTYa%2FWM%3D"', header)
    def test_malformed_x_response_stays_pending(self):
        self.verify()
        def http(m,u,p=None,token=None):
            return {'data':None} if u.endswith('/tweets') else self.http(m,u,p,token)
        self.runner(http).run('price','1',live=True)
        self.assertEqual(self.status(),'pending')

    def test_changed_price_posts_in_later_slot(self):
        self.verify()
        b = self.runner(); b.run('price','1',live=True)
        self.raw['last'] = '0.01235'
        self.assertIsNotNone(b.run('price','2',live=True))
        self.assertEqual(sum(u.endswith('/tweets') for _,u,_ in self.calls),2)
    def test_alert_delivery_failure_is_logged_without_secret(self):
        def http(*a,**kw): raise bot.RemoteError(503)
        b = self.runner(http)
        for _ in range(3): b.failure('ticker','unavailable')
        rows = self.s.db.execute('SELECT event,detail FROM events').fetchall()
        self.assertTrue(any(e=='alert_delivery_failed' for e,_ in rows))
        self.assertNotIn('TEST_TOKEN', str(rows))
        self.assertNotIn('alerts.invalid', str(rows))

    def test_unconfirmed_launch_blocks_all_scheduled_slots(self):
        self.c.update(launch_date='2026-10-01', start_at_utc=None)
        self.assertEqual(bot.slots(self.c, NOW), [])
        self.assertIsNone(self.runner().run('price','1',live=True))
        self.assertEqual(self.calls, [])
    def test_no_post_or_slot_before_launch(self):
        self.c.update(launch_date='2026-10-01',start_at_utc='2026-10-01T00:00:00Z')
        self.assertEqual(bot.slots(self.c, NOW-1), [])
        self.assertFalse(bot.launch_ready(self.c,NOW-1))
        self.assertTrue(bot.launch_ready(self.c,NOW))
    def test_seven_price_only_slots_per_full_utc_day(self):
        self.c.update(launch_date='2026-10-01',start_at_utc='2026-10-01T00:00:00Z')
        slots=set()
        for minute in range(1440):
            slots.update(bot.slots(self.c,NOW+minute*60))
        self.assertEqual(len(slots),7)
        self.assertEqual(sum(k=='price' for k,_ in slots),6)
    def test_singapore_launch_anchor(self):
        self.c.update(launch_date='2026-10-01',start_at_utc='2026-10-01T03:00:00Z',price_anchor_utc='03:00',daily_utc='15:10')
        self.assertEqual(bot.slots(self.c,NOW+3*3600), [('price',str(int(NOW+3*3600)))])
        self.assertEqual(bot.slots(self.c,NOW+4*3600), [])
        self.assertEqual(bot.slots(self.c,NOW+3*3600-1), [])
    def test_launch_timestamp_must_have_utc_offset(self):
        self.c['start_at_utc']='2026-10-01T00:00:00'
        with self.assertRaises(bot.Refused):bot.launch_timestamp(self.c)
    def test_daily_cap_blocks_before_api_requests(self):
        self.verify()
        with self.s.db:
            for i in range(7):
                self.s.db.execute('INSERT INTO events(at,event,detail) VALUES (?,?,?)',('2026-10-01T00:00:00+00:00','posted','{}'))
        self.assertIsNone(self.runner().run('price','8',live=True))
        self.assertEqual(self.calls,[])

if __name__=='__main__': unittest.main()
