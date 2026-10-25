"""Read-only dashboard bound to a selected network address."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import engine


def make_server(store, port=8787, host='127.0.0.1'):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get('Host','').split(':')[0] not in (host,'127.0.0.1','localhost'):
                self.send_error(403,'Dashboard host required');return
            path=urlparse(self.path).path
            allowed={'/':('index.html','text/html'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css')}
            try:
                if path=='/api/status':
                    data=json.dumps(engine.dashboard(store)).encode();mime='application/json';code=200
                elif path in allowed:
                    filename,mime=allowed[path];data=(store.root/'static'/filename).read_bytes();code=200
                else:data=b'{"error":"Not found"}';mime='application/json';code=404
            except Exception:
                data=b'{"error":"Dashboard data unavailable; check service logs."}';mime='application/json';code=500
            self.send_response(code)
            for key,value in {'Content-Type':mime+'; charset=utf-8','Content-Length':str(len(data)),
              'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
              'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"}.items():self.send_header(key,value)
            self.end_headers();self.wfile.write(data)
        def do_POST(self):self.send_error(405,'Dashboard is read-only')
        do_PUT=do_POST
        do_DELETE=do_POST
        do_PATCH=do_POST
    return ThreadingHTTPServer((host,port),Handler)


def serve(store, port=8787, host='127.0.0.1'):
    server=make_server(store,port,host)
    print(f'Firmware watch dashboard http://{host}:{port}',flush=True)
    server.serve_forever()
