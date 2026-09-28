"""Render worker and outbound private dashboard connector. Scheduled publishing requires explicit runtime approval."""
import json
import os
from pathlib import Path
import shutil
import signal
import threading
import time
import urllib.request

import bot
from control import Center

SITE = 'https://czr-bot-command-center.charlierothkopf384.chatgpt.site'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def remote(path, payload):
    request = urllib.request.Request(SITE + path, data=json.dumps(payload).encode(), headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + os.environ['CZR_AGENT_TOKEN'],
        'OAI-Sites-Authorization': 'Bearer ' + os.environ['CZR_SITE_ACCESS_TOKEN'],
    })
    with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
        return json.load(response)

def snapshot(center):
    state = center.snapshot()
    state.pop('csrf', None)
    state['runtime'] = 'render'
    return state

def execute(center, command, now=None):
    now = time.time() if now is None else now
    ident = command.get('id', '')
    import uuid
    if str(uuid.UUID(ident)) != ident:
        raise ValueError('Invalid command identifier')
    created = command.get('created', 0) / 1000
    if not 0 <= now-created <= 90:
        return {'status': 409, 'result': {'error': 'Command expired'}}
    with center.mutex, center.store() as store:
        key = 'cloud:command:' + ident
        old = store.get(key)
        if old:
            return old if old.get('status') else {'status':409,'result':{'error':'Prior command outcome requires review'}}
        # Persist before execution: an interrupted request cannot be replayed.
        store.put(key, {'pending': True})
        store.log('cloud_command_attempt', command_id=ident, path=command.get('path'))
        try:
            path, payload = command.get('path'), command.get('payload', {})
            if not isinstance(payload, dict):
                raise ValueError('Invalid command payload')
            if path == '/api/preview':
                result = center.preview()
            elif path == '/api/config':
                center.save(payload)
                result = {'saved': True}
            elif path == '/api/scheduler':
                result = {'running': center.scheduler(payload.get('action'))}
                store.put('cloud:scheduler_running', result['running'])
            else:
                raise bot.Refused('Action unavailable; live posting is locked')
            response = {'status': 200, 'result': result}
        except (bot.Refused, bot.RemoteError, ValueError, KeyError, TypeError) as error:
            response = {'status': 400, 'result': {'error': str(error)}}
        except Exception:
            response = {'status': 500, 'result': {'error': 'Operation failed; inspect worker logs'}}
        store.put(key, response)
        store.log('cloud_command_result', command_id=ident, status=response['status'])
        return response

class CloudCenter(Center):
    """Only the scheduled path may publish; browser commands cannot enable it."""
    def live_requested(self):
        return os.environ.get('CZR_LIVE_POSTING') == 'true'

    def snapshot(self):
        state = super().snapshot()
        state['live_requested'] = self.live_requested()
        state['live_enabled'] = False
        with self.store() as store:
            try:
                bot.Bot(self.config(), store).gate()
                state['live_enabled'] = True
            except bot.Refused:
                pass
        return state

    def tick(self, now=None):
        clock = time.time if now is None else lambda: now
        now = time.time() if now is None else now
        with self.mutex:
            if not self.running:
                return
            config = self.config()
            live = self.live_requested()
            with self.store() as store:
                runner = bot.Bot(config, store, transport=bot.request if live else self.safe_transport, now=clock)
                if live:
                    # Do not consume slots before prerequisites pass.
                    try:
                        runner.gate()
                    except bot.Refused as error:
                        if now >= store.get('cloud:gate_retry', 0):
                            runner.failure('configuration', str(error))
                            store.put('cloud:gate_retry', now + 300)
                        return
                for kind, slot in bot.slots(config, now):
                    mode = 'live' if live else 'preview'
                    key = f'cloud:attempted:{mode}:{kind}:{slot}'
                    if store.get(key):
                        continue
                    # Reserve the slot durably before work; never replay on restart.
                    store.put(key, True)
                    runner.run(kind, slot, live=live, preview=not live)

    def loop(self):
        while not self.stop.wait(5):
            try:
                self.tick()
            except Exception as error:
                with self.store() as store:
                    store.log('scheduler_error', reason=type(error).__name__)
                    bot.Bot(self.config(), store).failure('scheduler', type(error).__name__)

def main():
    os.umask(0o077)
    directory = Path(os.environ.get('CZR_STATE_DIR', '/state'))
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / 'config.json'
    if not config.exists():
        shutil.copyfile(Path(__file__).with_name('config.json'), config)
    with bot.lock(directory / 'bot.sqlite3'):
        center = CloudCenter(config, directory / 'bot.sqlite3')
        with center.store() as store:
            center.running = bool(store.get('cloud:scheduler_running', True))
            store.log('cloud_worker_started', live=center.live_requested(), runtime='render')
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: center.stop.set())
        thread = threading.Thread(target=center.loop, daemon=True)
        thread.start()
        failures = 0
        while not center.stop.is_set():
            try:
                if not all(os.environ.get(key) for key in ('CZR_AGENT_TOKEN', 'CZR_SITE_ACCESS_TOKEN')):
                    raise bot.Refused('Dashboard connection secrets not configured')
                response = remote('/agent/poll', {'state': snapshot(center)})
                for command in response.get('commands', []):
                    result = execute(center, command)
                    remote('/agent/poll', {'state': snapshot(center)})
                    remote('/agent/result', {'id': command['id'], **result})
                if failures:
                    with center.store() as store:
                        store.log('cloud_connection_restored')
                        store.put('failures:dashboard_connection', 0)
                failures = 0
            except Exception as error:
                failures += 1
                if failures == 1 or failures % 20 == 0:
                    with center.store() as store:
                        store.log('cloud_connection_failed', count=failures, reason=type(error).__name__)
                with center.store() as store:
                    bot.Bot(center.config(), store).failure('dashboard_connection', type(error).__name__)
                # No exception bodies or URLs: they might contain secrets.
            center.stop.wait(min(30, 3 * max(1, failures)))
        thread.join(timeout=5)

if __name__ == '__main__':
    main()
