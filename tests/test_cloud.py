import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import cloud

class CloudTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config = root/'config.json'
        self.config.write_text(Path('config.json').read_text())
        self.center = cloud.Center(self.config, root/'bot.sqlite3')
    def tearDown(self):
        self.tmp.cleanup()
    def command(self, path='/api/scheduler', payload=None):
        return {'id':str(uuid.uuid4()),'created':1000000,'path':path,'payload':payload or {'action':'start'}}
    def test_replay_after_restart_does_not_execute(self):
        cmd = self.command()
        self.assertEqual(cloud.execute(self.center,cmd,1001)['status'],200)
        other = cloud.Center(self.config,self.center.state_path)
        with patch.object(other,'scheduler',side_effect=AssertionError('replayed')):
            self.assertEqual(cloud.execute(other,cmd,1002)['status'],200)
        with other.store() as store:
            self.assertTrue(store.get('cloud:scheduler_running'))
    def test_pending_command_is_not_replayed(self):
        cmd=self.command()
        with self.center.store() as store:store.put('cloud:command:'+cmd['id'],{'pending':True})
        self.assertEqual(cloud.execute(self.center,cmd,1001)['status'],409)
        self.assertFalse(self.center.running)
    def test_expired_and_future_commands_rejected(self):
        for now in (999,1091):
            self.assertEqual(cloud.execute(self.center,self.command(),now)['status'],409)
        self.assertFalse(self.center.running)
    def test_live_route_never_allowed(self):
        self.assertEqual(cloud.execute(self.center,self.command('/api/live'),1001)['status'],400)
        with self.assertRaises(cloud.bot.Refused):
            self.center.safe_transport('POST','https://api.x.com/2/tweets')
    def test_config_persists_and_revision_conflict_rejected(self):
        config=self.center.config()
        payload={'revision':cloud.bot.digest(config),'config':{'interval_hours':6}}
        self.assertEqual(cloud.execute(self.center,self.command('/api/config',payload),1001)['status'],200)
        self.assertEqual(json.loads(self.config.read_text())['interval_hours'],6)
        self.assertEqual(cloud.execute(self.center,self.command('/api/config',payload),1001)['status'],400)
    def test_snapshot_identifies_cloud_without_csrf(self):
        state=cloud.snapshot(self.center)
        self.assertEqual(state['runtime'],'render')
        self.assertFalse(state['live_enabled'])
        self.assertNotIn('csrf',state)

if __name__=='__main__':unittest.main()

class LiveCloudTests(unittest.TestCase):
    def setUp(self):
        CloudTests.setUp(self)
        self.center=cloud.CloudCenter(self.config,self.center.state_path)
        self.center.running=True
        self.env=patch.dict(cloud.os.environ,{},clear=True);self.env.start()
    def tearDown(self):self.env.stop();CloudTests.tearDown(self)
    def test_disabled_worker_only_previews(self):
        with patch('bot.slots',return_value=[('price','one')]),patch('bot.Bot.run') as run:
            self.center.tick(2000000000)
        self.assertEqual(run.call_args.kwargs,{'live':False,'preview':True})
    def test_missing_gates_prevent_live_scheduler_network(self):
        cloud.os.environ['CZR_LIVE_POSTING']='true'
        with patch('bot.request') as request,patch('bot.Bot.run') as run:
            self.center.tick(2000000000)
        request.assert_not_called();run.assert_not_called()
    def test_live_mode_durable_slots_and_pause(self):
        cloud.os.environ['CZR_LIVE_POSTING']='true'
        with patch('bot.Bot.gate'),patch('bot.slots',return_value=[('price','one')]),patch('bot.Bot.run') as run:
            self.center.tick(2000000000)
            other=cloud.CloudCenter(self.config,self.center.state_path);other.running=True
            other.tick(2000000001)
            self.assertEqual(run.call_count,1)
            self.assertEqual(run.call_args.kwargs,{'live':True,'preview':False})
            other.running=False
            with patch('bot.slots',return_value=[('price','two')]):other.tick(2000000002)
            self.assertEqual(run.call_count,1)
