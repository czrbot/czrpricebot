"""CZR price bot. Python 3.11+, standard library only; safe by default."""
import argparse
import base64
import hmac
import secrets
import contextlib
import datetime as dt
from decimal import Decimal, InvalidOperation
import email.utils
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import random
import sqlite3
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PAIR = 'czrtoken1858usdt1858'
LINK = 'https://www.czrex.com/en_US/trade/CZR_USDT?type=spot'
UTC = dt.timezone.utc

class Refused(Exception):
    pass

class RemoteError(Exception):
    def __init__(self, status=0, wait=0):
        self.status, self.wait = status, wait
        super().__init__(f'HTTP {status}' if status else 'transport or response failure')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def https(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment:
        raise Refused('HTTPS URL without embedded credentials required')
    return url

OAUTH_KEYS = ('X_API_KEY', 'X_API_KEY_SECRET', 'X_ACCESS_TOKEN', 'X_ACCESS_TOKEN_SECRET')

def x_auth():
    values = [os.environ.get(k) for k in OAUTH_KEYS]
    if all(values):
        return dict(zip(OAUTH_KEYS, values))
    if any(values):
        raise Refused('incomplete OAuth 1.0a credentials')
    if os.environ.get('X_USER_ACCESS_TOKEN'):
        return os.environ['X_USER_ACCESS_TOKEN']
    raise Refused('X user authorization missing')

def oauth_header(method, url, credentials, nonce=None, timestamp=None):
    enc = lambda x: urllib.parse.quote(str(x), safe='~-._')
    params = {
        'oauth_consumer_key': credentials['X_API_KEY'],
        'oauth_token': credentials['X_ACCESS_TOKEN'],
        'oauth_nonce': nonce or secrets.token_hex(16),
        'oauth_timestamp': str(int(time.time()) if timestamp is None else timestamp),
        'oauth_signature_method': 'HMAC-SHA1', 'oauth_version': '1.0'
    }
    parsed = urllib.parse.urlsplit(url)
    pairs = list(params.items()) + urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    normalized = '&'.join(k + '=' + v for k, v in sorted((enc(k), enc(v)) for k, v in pairs))
    base_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or '/', '', ''))
    base = '&'.join([method.upper(), enc(base_url), enc(normalized)])
    key = enc(credentials['X_API_KEY_SECRET']) + '&' + enc(credentials['X_ACCESS_TOKEN_SECRET'])
    params['oauth_signature'] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return 'OAuth ' + ', '.join(enc(k) + '="' + enc(v) + '"' for k, v in sorted(params.items()))

def request(method, url, payload=None, token=None):
    https(url)
    headers = {'Content-Type': 'application/json', 'User-Agent': 'czr-price-bot/1.0'}
    if token:
        headers['Authorization'] = oauth_header(method, url, token) if isinstance(token, dict) else 'Bearer ' + token
    req = urllib.request.Request(url, data=None if payload is None else json.dumps(payload).encode(), headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as r:
            raw = r.read(1_000_001)
            if len(raw) > 1_000_000:
                raise RemoteError()
            if method == 'POST' and urllib.parse.urlsplit(url).hostname != 'api.x.com':
                return {}  # Alert webhooks may acknowledge with plain text or 204.
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        delay = 0
        try:
            retry = e.headers.get('Retry-After', '0')
            try:
                delay = float(retry)
            except ValueError:
                delay = email.utils.parsedate_to_datetime(retry).timestamp() - time.time()
            delay = max(delay, float(e.headers.get('x-rate-limit-reset', '0')) - time.time())
        except (ValueError, TypeError, OverflowError):
            delay = 900
        raise RemoteError(e.code, max(0, delay)) from None
    except (OSError, ValueError, http.client.HTTPException):
        raise RemoteError() from None

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def stamp(ts):
    return dt.datetime.fromtimestamp(ts, UTC).strftime('%Y-%m-%d %H:%M UTC')

def number(value, positive=True):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise Refused('missing or invalid number')
    try:
        n = Decimal(str(value))
    except InvalidOperation:
        raise Refused('invalid number') from None
    if not n.is_finite() or n < 0 or (positive and n == 0) or n > Decimal('1e30') or n.as_tuple().exponent < -18:
        raise Refused('number outside accepted range')
    return n

def fmt(n):
    return format(n, 'f').rstrip('0').rstrip('.') if '.' in format(n, 'f') else format(n, 'f')

def launch_timestamp(c):
    value = c.get('start_at_utc')
    if value is None:
        return None
    try:
        launch = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if launch.tzinfo is None or launch.utcoffset() != dt.timedelta(0):
            raise ValueError()
        if c.get('launch_date') and launch.date().isoformat() < c['launch_date']:
            raise ValueError()
        return launch.timestamp()
    except (ValueError, TypeError, AttributeError):
        raise Refused('start_at_utc must be an explicit UTC launch timestamp on or after launch_date') from None

def launch_ready(c, now):
    if 'launch_date' not in c and 'start_at_utc' not in c:
        return True
    start = launch_timestamp(c)
    return start is not None and now >= start

def load_config(path):
    c = json.loads(Path(path).read_text())
    if c['pair'] != PAIR:
        raise Refused('incorrect pair')
    if not isinstance(c['interval_hours'], int) or c['interval_hours'] not in (1, 2, 3, 4, 6, 8, 12, 24):
        raise Refused('interval_hours must divide 24')
    dt.datetime.strptime(c['daily_utc'], '%H:%M')
    dt.datetime.strptime(c.get('price_anchor_utc','00:00'), '%H:%M')
    launch_timestamp(c)
    if type(c.get('max_posts_per_day', 7)) is not int or not 1 <= c.get('max_posts_per_day', 7) <= 10:
        raise Refused('max_posts_per_day must be between 1 and 10')
    for key in ('freshness_seconds', 'future_skew_seconds', 'schedule_grace_seconds', 'alert_after', 'alert_cooldown_seconds'):
        if not isinstance(c[key], int) or c[key] <= 0:
            raise Refused('invalid ' + key)
    for kind in ('price', 'daily'):
        fields = [f for _, f, _, _ in string.Formatter().parse(c['templates'][kind]) if f is not None]
        if not {'price', 'timestamp', 'link'} <= set(fields) or not set(fields) <= {'price', 'timestamp', 'link', 'stats'}:
            raise Refused('template placeholders invalid')
        if 'CZR Exchange' not in c['templates'][kind] or 'CZR/USDT' not in c['templates'][kind]:
            raise Refused('template must identify source and pair')
    return c

def verified(c, stats=False):
    v = c['provider_verification']
    required = ['base_url', 'symbol', 'last', 'timestamp']
    if stats:
        required += ['window', 'high', 'low', 'open', 'volume']
    return bool(v.get('reference') and v.get('confirmed_at') and all(v.get(k) for k in required))

def ticker(c, raw, now, fixture=False):
    if not fixture and not verified(c):
        raise Refused('API provider base URL, price and timestamp verification pending')
    if not isinstance(raw, dict) or raw.get('symbol', PAIR) != PAIR:
        raise Refused('missing or mismatched symbol')
    if 'code' in raw and str(raw['code']) not in ('0', '200'):
        raise Refused('ticker API error payload')
    price = number(raw.get(c['fields']['last']))
    ts = number(raw.get(c['fields']['timestamp']))
    ts = float(ts / (1000 if c['timestamp_unit'] == 'milliseconds' else 1))
    if ts > now + c['future_skew_seconds'] or now - ts > c['freshness_seconds']:
        raise Refused('stale or future ticker timestamp')
    data = {'price': fmt(price), 'timestamp': stamp(ts), 'ts': ts}
    if c['include_24h']:
        if not verified(c, True):
            raise Refused('24-hour calculation verification pending')
        high, low, opening, volume = [number(raw.get(c['fields'][k]), k != 'volume') for k in ('high', 'low', 'open', 'volume')]
        if not low <= price <= high or not low <= opening <= high:
            raise Refused('inconsistent 24-hour prices')
        unit = c['volume_unit']
        if unit not in ('CZR', 'USDT'):
            raise Refused('volume unit must be verified')
        change = (price - opening) / opening * 100
        data['stats'] = f'\n24h H/L: {fmt(high)}/{fmt(low)} USDT\n24h change: {change:+.2f}%\n24h volume: {fmt(volume)} {unit}'
    else:
        data['stats'] = ''
    return data

def render(c, kind, data):
    text = c['templates'][kind].format(price=data['price'], timestamp=data['timestamp'], link=LINK, stats=data['stats'])
    # Conservative Unicode weighting; URL has X's standard 23-character weight.
    weight = sum(1 if ord(x) < 128 else 2 for x in text.replace(LINK, '')) + 23
    if weight > 280 or text.count(LINK) != 1 or 'UTC' not in text:
        raise Refused('post exceeds 280-character limit or lacks required content')
    return text

def slots(c, now):
    if not launch_ready(c, now):
        return []
    date = dt.datetime.fromtimestamp(now, UTC)
    base = date.replace(hour=0, minute=0, second=0, microsecond=0)
    interval = c['interval_hours'] * 3600
    anchor_h, anchor_m = map(int, c.get('price_anchor_utc','00:00').split(':'))
    anchor = base.timestamp() + anchor_h * 3600 + anchor_m * 60
    price = anchor + int((now - anchor) // interval) * interval
    hour, minute = map(int, c['daily_utc'].split(':'))
    daily = base.replace(hour=hour, minute=minute).timestamp()
    return [(kind, str(int(start))) for kind, start in [('price', price), ('daily', daily)] if 0 <= now - start <= c['schedule_grace_seconds']]

class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS jobs(kind TEXT, slot TEXT, status TEXT, fingerprint TEXT, text TEXT, post_id TEXT, PRIMARY KEY(kind,slot));
        CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at TEXT, event TEXT, detail TEXT);
        ''')
    def get(self, key, default=None):
        r = self.db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
        return json.loads(r[0]) if r else default
    def put(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)', (key, json.dumps(value)))
    def log(self, event, **detail):
        record = {'at': dt.datetime.now(UTC).isoformat(), 'event': event, **detail}
        with self.db:
            self.db.execute('INSERT INTO events(at,event,detail) VALUES (?,?,?)', (record['at'], event, json.dumps(detail)))
        print(json.dumps(record), file=sys.stderr, flush=True)
    def job(self, kind, slot, status, fingerprint='', text='', post_id=None):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?)', (kind, slot, status, fingerprint, text, post_id))

class Bot:
    def __init__(self, c, store, transport=request, sleep=time.sleep, now=time.time):
        self.c, self.s, self.http, self.sleep, self.now = c, store, transport, sleep, now
    def failure(self, component, reason):
        key = 'failures:' + component
        count = self.s.get(key, 0) + 1
        self.s.put(key, count)
        self.s.log('failure', component=component, count=count, reason=reason)
        if count >= self.c['alert_after'] and self.now() >= self.s.get('alert_next:' + component, 0):
            self.s.log('alert_due', component=component, count=count)
            url = os.environ.get('TEAM_ALERT_WEBHOOK_URL')
            if url:
                try:
                    self.http('POST', url, {'text': f'CZR price bot: {component} failed {count} times. Inspect durable audit logs. Reason: {reason}'})
                    self.s.log('alert_sent', component=component)
                    self.s.put('alert_next:' + component, self.now() + self.c['alert_cooldown_seconds'])
                except Exception:
                    self.s.log('alert_delivery_failed', component=component)
            else:
                self.s.log('alert_unconfigured', component=component)
    def fetch(self, preview=False):
        if not preview and not verified(self.c):
            raise Refused('API provider verification pending')
        base = https(self.c['base_url']).rstrip('/')
        path = self.c['ticker_path']
        if not path.startswith('/') or '?' in path or '#' in path:
            raise Refused('invalid ticker path')
        url = base + path + '?' + urllib.parse.urlencode({'symbol': PAIR})
        for attempt in range(3):
            self.s.log('ticker_attempt', attempt=attempt + 1)
            try:
                result = self.http('GET', url)
                self.s.log('ticker_received', attempt=attempt + 1)
                return result
            except RemoteError as e:
                self.s.log('ticker_http_failure', status=e.status)
                if e.status in (410, 418, 429):
                    self.s.put('ticker_not_before', self.now() + max(900, e.wait))
                    raise
                if e.status not in (0, 500, 502, 503, 504) or attempt == 2:
                    raise
                self.sleep(2 ** attempt + random.random())
    def gate(self):
        if os.environ.get('CZR_LIVE_POSTING') != 'true':
            raise Refused('live posting disabled')
        if not verified(self.c, self.c['include_24h']):
            raise Refused('API provider verification pending')
        if os.environ.get('CZR_APPROVED_CONFIG_SHA256') != digest(self.c):
            raise Refused('final configuration and post formats require approval')
        x_auth()
        if not os.environ.get('X_EXPECTED_USER_ID'):
            raise Refused('X user authorization missing')
        if not os.environ.get('TEAM_ALERT_WEBHOOK_URL'):
            raise Refused('team alert destination missing')
    def run(self, kind, slot, live=False, raw=None, fixture=False, preview=False):
        mode = 'live' if live else ('fixture' if fixture else ('preview' if preview else 'dry'))
        keykind = mode + ':' + kind
        self.s.log('scheduled_attempt', kind=kind, slot=slot, mode=mode)
        try:
            if live:
                if not launch_ready(self.c, self.now()):
                    self.s.log('skip_before_launch', start_at_utc=self.c.get('start_at_utc'), launch_date=self.c.get('launch_date'))
                    return None
                midnight = dt.datetime.fromtimestamp(self.now(), UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                sent_today = self.s.db.execute("SELECT COUNT(*) FROM events WHERE event='posted' AND at>=?", (midnight,)).fetchone()[0]
                if sent_today >= self.c.get('max_posts_per_day', 7):
                    self.s.log('skip_daily_limit', limit=self.c.get('max_posts_per_day', 7))
                    return None
                if fixture or preview or raw is not None:
                    raise Refused('live posting cannot use fixture or injected data')
                self.gate()
            if self.s.db.execute("SELECT 1 FROM jobs WHERE status='pending' AND kind LIKE 'live:%'").fetchone():
                raise Refused('unresolved X submission: reconcile pending job before further publishing')
            if self.s.db.execute('SELECT 1 FROM jobs WHERE kind=? AND slot=?', (keykind, slot)).fetchone():
                self.s.log('skip_duplicate_slot', kind=kind, slot=slot)
                return None
            if self.now() < self.s.get('ticker_not_before', 0):
                raise Refused('ticker rate-limit cooldown')
            try:
                data = ticker(self.c, raw if raw is not None else self.fetch(preview), self.now(), fixture or preview)
                self.s.put('failures:ticker', 0)
            except (Refused, RemoteError) as e:
                self.failure('ticker', str(e))
                return None
            text = render(self.c, kind, data)
            fingerprint = digest({'price': data['price'], 'stats': data['stats'] if '{stats}' in self.c['templates'][kind] else ''})
            previous = self.s.get('previous:' + keykind)
            # Compare market values only: timestamp changes must not trigger posts.
            if previous == fingerprint:
                self.s.job(keykind, slot, 'unchanged', fingerprint)
                self.s.log('skip_unchanged', kind=kind, slot=slot)
                return None
            if not live:
                self.s.job(keykind, slot, 'dry_run', fingerprint, text)
                self.s.put('previous:' + keykind, fingerprint)
                self.s.log('dry_run', kind=kind, fixture=fixture, text=text)
                return text
            if self.now() < self.s.get('x_not_before', 0):
                raise Refused('X rate-limit cooldown')
            token = x_auth()
            self.s.log('x_identity_attempt')
            identity = self.http('GET', 'https://api.x.com/2/users/me', token=token)
            user = identity.get('data') if isinstance(identity, dict) else None
            if not isinstance(user, dict):
                raise RemoteError()
            if not isinstance(user.get('username'), str) or user['username'].lower() != 'czrpricebot' or user.get('id') != os.environ['X_EXPECTED_USER_ID']:
                raise Refused('authorized X account mismatch')
            if self.now() - data['ts'] > self.c['freshness_seconds']:
                raise Refused('ticker became stale before publishing')
            # Durable reservation BEFORE POST. Any ambiguous outcome stays pending.
            self.s.job(keykind, slot, 'pending', fingerprint, text)
            self.s.log('x_post_attempt', kind=kind, slot=slot)
            try:
                response = self.http('POST', 'https://api.x.com/2/tweets', {'text': text}, token)
                result = response.get('data') if isinstance(response, dict) else None
                post_id = result.get('id') if isinstance(result, dict) else None
                if not isinstance(post_id, str) or not post_id.isdigit():
                    raise RemoteError()
            except RemoteError as e:
                if 400 <= e.status < 500:
                    self.s.job(keykind, slot, 'rejected', fingerprint, text)
                if e.status == 429:
                    self.s.put('x_not_before', self.now() + max(900, e.wait))
                self.failure('posting', str(e))
                return None
            with self.s.db:
                self.s.db.execute('UPDATE jobs SET status=?,post_id=? WHERE kind=? AND slot=?', ('posted', post_id, keykind, slot))
                self.s.db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)', ('previous:' + keykind, json.dumps(fingerprint)))
            self.s.put('failures:posting', 0)
            self.s.log('posted', kind=kind, slot=slot, post_id=post_id)
            return text
        except (Refused, RemoteError) as e:
            if isinstance(e, RemoteError) and e.status == 429:
                self.s.put('x_not_before', self.now() + max(900, e.wait))
            self.failure('posting' if live else 'configuration', str(e))
            return None

@contextlib.contextmanager
def lock(path):
    with open(str(path) + '.lock', 'a') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('another worker holds the state lock') from None
        yield

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', default='config.json')
    p.add_argument('--state', default='state/bot.sqlite3')
    p.add_argument('--kind', choices=['price', 'daily'], default='price')
    p.add_argument('--live', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--daemon', action='store_true')
    p.add_argument('--fixture')
    p.add_argument('--preview-unverified', action='store_true', help='Read-only preview with provisional field semantics; never permits publishing')
    p.add_argument('--at', help='Fixture-only ISO timestamp')
    p.add_argument('--config-hash', action='store_true')
    a = p.parse_args()
    os.umask(0o077)
    c = load_config(a.config)
    if a.config_hash:
        print(digest(c)); return 0
    if (a.live and (a.dry_run or a.fixture or a.at or a.preview_unverified)) or (a.at and not a.fixture) or ((a.fixture or a.preview_unverified) and a.daemon):
        raise Refused('incompatible safety flags')
    if c['timestamp_unit'] not in ('milliseconds', 'seconds'):
        raise Refused('invalid timestamp unit')
    path = Path(a.state)
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock(path):
        s = Store(path)
        now = dt.datetime.fromisoformat(a.at.replace('Z', '+00:00')).timestamp() if a.at else time.time()
        bot = Bot(c, s, now=(lambda: now) if a.at else time.time)
        if a.live:
            bot.gate()
        if a.daemon:
            s.log('worker_started', live=a.live)
            while True:
                for kind, slot in slots(c, time.time()):
                    # Once per UTC slot, including failures; avoids retry storms.
                    marker = ('live:' if a.live else 'dry:') + kind + ':attempted:' + slot
                    if not s.get(marker):
                        s.put(marker, True)
                        bot.run(kind, slot, a.live)
                time.sleep(30)
        raw = json.loads(Path(a.fixture).read_text()) if a.fixture else None
        if a.fixture:
            print('FIXTURE TEST ONLY — synthetic values, not live CZR market data.', file=sys.stderr)
        current = dt.datetime.fromtimestamp(now, UTC)
        midnight = current.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        if a.kind == 'price':
            width = c['interval_hours'] * 3600
            ah, am = map(int, c.get('price_anchor_utc','00:00').split(':'))
            anchor = midnight + ah*3600 + am*60
            slot = str(int(anchor + int((now - anchor) // width) * width))
        else:
            h, m = map(int, c['daily_utc'].split(':'))
            slot = str(int(midnight + h * 3600 + m * 60))
        if a.preview_unverified:
            if c['include_24h']:
                raise Refused('unverified preview may only show price')
            print('UNVERIFIED LIVE-DATA PREVIEW — timestamp and field semantics await API provider confirmation; not publishable.', file=sys.stderr)
        result = bot.run(a.kind, slot, a.live, raw, bool(a.fixture), a.preview_unverified)
        if result:
            print(result)
            return 0
        return 2

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Refused, KeyError, ValueError) as e:
        print(json.dumps({'event': 'startup_refused', 'reason': str(e)}), file=sys.stderr)
        sys.exit(2)
