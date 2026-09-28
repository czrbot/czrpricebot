import json
from pathlib import Path
import unittest
import bot

class RoutineFormatTests(unittest.TestCase):
    def setUp(self):
        self.config=bot.load_config('config.json')
        self.now=1790812800.0
        self.raw={'last':'0.053535','open':'0.05','amount':'12345.67','time':self.now*1000}
    def verified(self):
        self.config['provider_verification']=dict.fromkeys(self.config['provider_verification'],'synthetic test confirmation only')
        self.config['fields']['quote_volume']='amount'
    def test_exact_user_format_five_decimal_price_and_utc(self):
        self.verified()
        data=bot.ticker(self.config,self.raw,self.now)
        text=bot.render(self.config,'price',data)
        self.assertEqual(text,'$CZR / USDT 📊\n\nPrice: $0.05354\n24h change: +7.07%\n24h volume: 12345.67 USDT\n\nUpdated 2026-10-01 00:00 UTC · @czrexchange\nTrade $CZR: '+bot.LINK+'\n\n🤖 Automated price update')
    def test_verification_blocks_even_unverified_preview(self):
        with self.assertRaises(bot.Refused):bot.ticker(self.config,self.raw,self.now,fixture=True)
    def test_no_estimated_volume_from_base_quantity(self):
        self.verified();raw=dict(self.raw);del raw['amount'];raw['vol']='999'
        with self.assertRaises(bot.Refused):bot.ticker(self.config,raw,self.now)
    def test_quote_volume_mapping_must_be_explicit(self):
        self.verified();self.config['fields']['quote_volume']=None
        with self.assertRaises(bot.Refused):bot.ticker(self.config,self.raw,self.now)
    def test_invalid_stats_stale_data_and_missing_fields(self):
        self.verified()
        for key,value in [('amount',None),('amount','NaN'),('amount',-1),('open',0),('open','Infinity'),('time',(self.now-301)*1000)]:
            with self.subTest(key=key,value=value),self.assertRaises(bot.Refused):bot.ticker(self.config,{**self.raw,key:value},self.now)
    def test_render_never_leaves_placeholders(self):
        with self.assertRaises(bot.Refused):bot.render(self.config,'price',{'price_5dp':'0.05354','timestamp':'2026-10-01 00:00 UTC'})
    def test_rounding_to_zero_skipped(self):
        self.verified()
        with self.assertRaises(bot.Refused):bot.ticker(self.config,{**self.raw,'last':'0.000001'},self.now)
    def test_price_trailing_zeroes_and_zero_volume(self):
        self.verified();data=bot.ticker(self.config,{**self.raw,'last':'0.05','amount':'0'},self.now)
        self.assertEqual(data['price_5dp'],'0.05000');self.assertEqual(data['volume_usdt'],'0')
