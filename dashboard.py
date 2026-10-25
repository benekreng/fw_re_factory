"""Dashboard with same-origin operator launch controls."""
import hmac
import json
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import engine
import recovery
import model_editor
from app import Problem


def make_server(store, port=8787, host='127.0.0.1'):
    csrf_token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def valid_host(self):
            return self.headers.get('Host','').split(':')[0] in (host,'127.0.0.1','localhost')
        def respond(self,data,mime='application/json',code=200):
            if not isinstance(data,bytes):data=json.dumps(data).encode()
            self.send_response(code)
            for key,value in {'Content-Type':mime+'; charset=utf-8','Content-Length':str(len(data)),
              'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
              'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"}.items():self.send_header(key,value)
            self.end_headers();self.wfile.write(data)
        def do_GET(self):
            if not self.valid_host():
                self.send_error(403,'Dashboard host required');return
            path=urlparse(self.path).path
            allowed={'/':('index.html','text/html'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css')}
            try:
                if path=='/api/status':
                    data=json.dumps(engine.dashboard(store)|{'csrfToken':csrf_token}).encode();mime='application/json';code=200
                elif path=='/api/models':
                    data=json.dumps(model_editor.catalog_snapshot(store)).encode();mime='application/json';code=200
                elif path in allowed:
                    filename,mime=allowed[path];data=(store.root/'static'/filename).read_bytes();code=200
                else:data=b'{"error":"Not found"}';mime='application/json';code=404
            except Exception:
                data=b'{"error":"Dashboard data unavailable; check service logs."}';mime='application/json';code=500
            self.respond(data,mime,code)
        def do_POST(self):
            path=urlparse(self.path).path
            match=re.fullmatch(r'/api/jobs/([a-zA-Z0-9_-]{1,64})/(launch|model|settings)',path)
            if path not in ('/api/launch-mode','/api/recovery-policy','/api/recovery-policy/notify','/api/models/refresh') and not match:
                self.respond({'error':'Unsupported dashboard action'},code=405);return
            if not self.valid_host() or self.headers.get('Origin')!='http://'+self.headers.get('Host','') or not hmac.compare_digest(self.headers.get('X-CSRF-Token','').encode(),csrf_token.encode()):
                self.respond({'error':'Use the launch controls from this dashboard page.'},code=403);return
            try:
                length=int(self.headers.get('Content-Length','0'))
                maximum=262144 if path=='/api/recovery-policy' else 4096
                if not 0<length<=maximum or self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise Problem('Send a small JSON object.')
                payload=json.loads(self.rfile.read(length))
                if not isinstance(payload,dict):raise Problem('Expected a JSON object.')
                if match and match[2] in ('model','settings'):
                    result={'job':model_editor.update_model(store,match[1],payload)}
                elif path=='/api/models/refresh':
                    result=model_editor.request_catalog(store)
                elif match:
                    result={'approval':engine.request_launch(store,match[1]),'notification':engine.notify_launch(store)}
                elif path=='/api/recovery-policy':
                    result=recovery.update_dashboard_policy(store,payload.get('policy'),payload.get('expectedRevision'))
                elif path=='/api/recovery-policy/notify':
                    revision=payload.get('expectedRevision')
                    if not isinstance(revision,str):raise Problem('A saved policy revision is required.')
                    recovery.notify_policy_update(store,retry_revision=revision)
                    result=recovery.policy_snapshot(store)
                else:result=engine.set_launch_mode(store,payload.get('mode'))
                self.respond(result)
            except Problem as error:self.respond({'error':str(error)},code=error.status)
            except (ValueError,UnicodeDecodeError):self.respond({'error':'Invalid JSON request.'},code=400)
            except Exception:self.respond({'error':'Dashboard action failed; check controller records before retrying.'},code=500)
        def unsupported(self):self.respond({'error':'Unsupported dashboard action'},code=405)
        do_PUT=unsupported
        do_DELETE=unsupported
        do_PATCH=unsupported
    return ThreadingHTTPServer((host,port),Handler)


def serve(store, port=8787, host='127.0.0.1'):
    server=make_server(store,port,host)
    print(f'Firmware watch dashboard http://{host}:{port}',flush=True)
    server.serve_forever()
