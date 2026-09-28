"""Local-only CZR command center. No route can publish to X."""
import argparse
import contextlib
import copy
import datetime as dt
import functools
import http.server
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
BOT_DIR = ROOT
sys.path.insert(0, str(BOT_DIR))
import bot

class Center:
    def __init__(self, config, state):
        self.config_path, self.state_path = Path(config), Path(state)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.mutex = threading.RLock()
        self.csrf = secrets.token_urlsafe(32)
        self.running = False
        self.stop = threading.Event()
        self.last_manual = 0
        self.started = time.time()
        with self.store() as s:
            s.log('command_center_started', live=False)
    @contextlib.contextmanager
    def store(self):
        s = bot.Store(self.state_path)
        try: yield s
        finally: s.db.close()
    def config(self): return bot.load_config(self.config_path)
    def safe_transport(self, method, url, payload=None, token=None):
        # Hard boundary independent of browser state, approval variables or X keys.
        if method == 'POST' and url == os.environ.get('TEAM_ALERT_WEBHOOK_URL') and not token:
            return bot.request(method, url, payload)
        if method != 'GET' or url != 'https://openapi.czrex.com/sapi/v2/ticker?symbol=' + bot.PAIR or token:
            raise bot.Refused('Command center only allows the public CZR ticker request')
        return bot.request(method, url)
    def snapshot(self):
        with self.mutex, self.store() as s:
            c = self.config()
            events = [{'id':r[0], 'at':r[1], 'event':r[2], 'detail':json.loads(r[3])} for r in s.db.execute('SELECT id,at,event,detail FROM events ORDER BY id DESC LIMIT 120')]
            jobs = [{'kind':r[0], 'slot':r[1], 'status':r[2], 'text':r[3], 'post_id':r[4]} for r in s.db.execute('SELECT kind,slot,status,text,post_id FROM jobs ORDER BY rowid DESC LIMIT 20')]
            latest = s.get('center:last_preview')
            now = time.time()
            if latest:
                latest['fresh'] = -c['future_skew_seconds'] <= now-latest['ts'] <= c['freshness_seconds'] and latest['config_hash']==bot.digest(c)
            date = dt.datetime.fromtimestamp(now, bot.UTC)
            midnight = date.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
            width = c['interval_hours'] * 3600
            ah,am=map(int,c.get('price_anchor_utc','00:00').split(':'))
            anchor=midnight+ah*3600+am*60
            price_next = anchor + (int((now-anchor)//width)+1)*width
            launch=bot.launch_timestamp(c)
            if launch is not None: price_next=max(price_next,launch)
            h,m=map(int,c['daily_utc'].split(':'))
            daily_next=midnight+h*3600+m*60
            if daily_next<=now: daily_next+=86400
            while launch is not None and daily_next<launch: daily_next+=86400
            return {'csrf':self.csrf,'config':c,'revision':bot.digest(c),'scheduler_running':self.running,
                'live_enabled':False,'connected':True,'server_time':now,'next_price':price_next,'next_daily':daily_next,
                'preview':latest,'events':events,'jobs':jobs,
                'counts':{'previews':s.db.execute("SELECT COUNT(*) FROM events WHERE event='manual_preview'").fetchone()[0],
                    'posted':s.db.execute("SELECT COUNT(*) FROM jobs WHERE status='posted'").fetchone()[0],
                    'skipped':s.db.execute("SELECT COUNT(*) FROM events WHERE event LIKE 'skip_%'").fetchone()[0]},
                'checks':{'provider':bot.verified(c,c['include_24h']), 'format':os.environ.get('CZR_APPROVED_CONFIG_SHA256')==bot.digest(c),
                    'x_credentials':bool(os.environ.get('X_USER_ACCESS_TOKEN') or all(os.environ.get(k) for k in bot.OAUTH_KEYS)),
                    'alerts':bot.alerts.configured()}}
    def preview(self):
        with self.mutex, self.store() as s:
            if time.time()-self.last_manual<10:
                raise bot.Refused('Please wait 10 seconds between ticker requests')
            self.last_manual=time.time()
            c=self.config()
            s.log('manual_preview_attempt',live=False)
            try:
                if time.time()<s.get('ticker_not_before',0):
                    raise bot.Refused('Ticker rate-limit cooldown is active')
                runner=bot.Bot(c,s,transport=self.safe_transport)
                raw=runner.fetch(preview=True)
                data=bot.ticker(c,raw,time.time(),fixture=True)
                result={**data,'price_text':bot.render(c,'price',data),'daily_text':bot.render(c,'daily',data),
                    'fetched_at':time.time(),'config_hash':bot.digest(c),'verified':bot.verified(c,c['include_24h'])}
                s.put('center:last_preview',result)
                s.put('failures:ticker',0)
                s.log('manual_preview',price=data['price'],timestamp=data['timestamp'],price_text=result['price_text'],daily_text=result['daily_text'],live=False)
                return result
            except (bot.Refused,bot.RemoteError) as e:
                bot.Bot(c,s,transport=self.safe_transport).failure('ticker',str(e))
                raise
    def save(self, payload):
        with self.mutex:
            original=self.config()
            if payload.get('revision')!=bot.digest(original):
                raise bot.Refused('Configuration changed. Refresh before saving again.')
            updates=payload.get('config')
            allowed={'interval_hours','daily_utc','freshness_seconds','future_skew_seconds','schedule_grace_seconds','alert_after','alert_cooldown_seconds','templates'}
            if not isinstance(updates,dict) or set(updates)-allowed:
                raise bot.Refused('Unsupported configuration field')
            c=copy.deepcopy(original); c.update(updates)
            for key in allowed-{'templates','daily_utc'}:
                if type(c[key]) is not int or not 1<=c[key]<=86400: raise bot.Refused('Invalid numeric configuration')
            if not isinstance(c['templates'],dict) or set(c['templates'])!={'price','daily'}:
                raise bot.Refused('Both post formats are required')
            sample={'price':'0.12345678','timestamp':'2026-10-01 00:00 UTC','stats':''}
            for kind,template in c['templates'].items():
                if not isinstance(template,str) or len(template)>600: raise bot.Refused('Post format is too long')
                for _,field,spec,conversion in bot.string.Formatter().parse(template):
                    if spec or conversion: raise bot.Refused('Use plain placeholders without formatting modifiers')
                bot.render(c,kind,sample)
            if self.running:
                raise bot.Refused('Pause the preview scheduler before changing configuration')
            fd,path=tempfile.mkstemp(prefix='.config-',suffix='.json',dir=self.config_path.parent)
            try:
                with os.fdopen(fd,'w') as f:
                    json.dump(c,f,indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
                bot.load_config(path)
                os.replace(path,self.config_path)
            finally:
                if os.path.exists(path): os.unlink(path)
            with self.store() as s:
                s.put('center:last_preview',None)
                s.log('configuration_saved',changed=[k for k in allowed if original[k]!=c[k]],approval_required=True)
    def scheduler(self, action):
        if action not in ('start','pause'): raise bot.Refused('Unknown scheduler action')
        with self.mutex,self.store() as s:
            self.running=action=='start'
            s.log('preview_scheduler_started' if self.running else 'preview_scheduler_paused',live=False)
        return self.running
    def loop(self):
        while not self.stop.wait(5):
            with self.mutex:
                if not self.running: continue
                try:
                    c=self.config()
                    with self.store() as s:
                        for kind,slot in bot.slots(c,time.time()):
                            key=f'center:attempted:{kind}:{slot}'
                            if s.get(key): continue
                            s.put(key,True)
                            bot.Bot(c,s,transport=self.safe_transport).run(kind,slot,preview=True)
                except Exception as e:
                    with self.store() as s: s.log('scheduler_error',reason=type(e).__name__)

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,status,value,ctype='application/json'):
        body=json.dumps(value).encode() if ctype=='application/json' else value
        self.send_response(status)
        for key,val in {'Content-Type':ctype,'Content-Length':str(len(body)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'X-Frame-Options':'DENY','Referrer-Policy':'no-referrer',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items(): self.send_header(key,val)
        self.end_headers(); self.wfile.write(body)
    def valid_host(self):
        port=self.server.server_port
        return self.headers.get('Host') in (f'127.0.0.1:{port}',f'localhost:{port}') and self.headers.get('Sec-Fetch-Site')!='cross-site'
    def do_GET(self):
        if not self.valid_host(): return self.send(403,{'error':'Local same-origin access required'})
        path=urlsplit(self.path).path
        try:
            if path=='/api/state': return self.send(200,self.server.center.snapshot())
            if path=='/api/export': return self.send(200,self.server.center.config())
            files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8'),'/favicon.svg':('favicon.svg','image/svg+xml')}
            if path not in files: return self.send(404,{'error':'Not found'})
            name,ctype=files[path]; return self.send(200,(ROOT/'dist'/name).read_bytes(),ctype)
        except Exception as e: return self.send(500,{'error':'Unable to load local bot state. Check the command-center terminal.'})
    def do_POST(self):
        origin='http://'+self.headers.get('Host','')
        if not self.valid_host() or self.headers.get('Origin')!=origin or not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),self.server.center.csrf):
            return self.send(403,{'error':'Invalid origin or session. Reload the command center.'})
        if self.headers.get('Content-Type')!='application/json': return self.send(415,{'error':'JSON required'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=32768: return self.send(413,{'error':'Invalid request size'})
            payload=json.loads(self.rfile.read(length))
            path=urlsplit(self.path).path
            if path=='/api/preview': result=self.server.center.preview()
            elif path=='/api/config': self.server.center.save(payload); result={'saved':True}
            elif path=='/api/scheduler': result={'running':self.server.center.scheduler(payload.get('action'))}
            else: return self.send(403,{'error':'Action unavailable. Live publishing is locked.'})
            self.send(200,result)
        except (bot.Refused,bot.RemoteError,ValueError,KeyError,TypeError) as e: self.send(400,{'error':str(e)})
        except Exception: self.send(500,{'error':'Operation failed. No post was published.'})

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--config',default=str(BOT_DIR/'config.json'))
    parser.add_argument('--state',default=str(BOT_DIR/'state'/'bot.sqlite3'))
    args=parser.parse_args(); os.umask(0o077)
    Path(args.state).parent.mkdir(parents=True,exist_ok=True)
    with bot.lock(args.state):
        center=Center(args.config,args.state)
        server=http.server.ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
        server.center=center
        threading.Thread(target=center.loop,daemon=True).start()
        print(f'CZR command center: http://127.0.0.1:{args.port} — live publishing locked',flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: center.stop.set(); server.server_close()

if __name__=='__main__': main()
