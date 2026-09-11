import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import tempfile,threading,unittest
from solution_runtime.registration import BoundRegistration

class RegistrationTest(unittest.TestCase):
    def test_actual_http_distinct_tokens_rotation_and_no_registration_on_exchange_denial(self):
        calls=[];issued=[];denied=[False]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append((self.path,dict(self.headers),body))
                if self.path.endswith('_registration-token'):
                    if denied[0]:self.send_response(403);self.end_headers();return
                    token='signed-'+str(len(issued));issued.append(token);raw=json.dumps({'token':token}).encode()
                else:
                    assert self.headers.get('X-Codefly-Solution-Registration')==issued[-1]
                    assert not self.headers.get('X-Codefly-Internal-Token')
                    assert not self.headers.get('X-Codefly-Solution-Secret')
                    raw=b'{}'
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            with tempfile.TemporaryDirectory() as d:
                secret=Path(d)/'secret';internal=Path(d)/'internal';secret.write_text('first');internal.write_text('perimeter')
                base='http://127.0.0.1:'+str(server.server_port);host=base+'/host';gateway=base+'/gateway'
                r=BoundRegistration('lastlogin-python',base+'/solutions/_registration-token',secret,internal,[host,gateway])
                self.assertIsNone(r.post(host,{'id':'lastlogin-python'}));secret.write_text('rotated')
                self.assertIsNone(r.post(gateway,{'id':'lastlogin-python'}))
                self.assertEqual(issued,['signed-0','signed-1']);self.assertEqual(calls[2][1]['X-Codefly-Solution-Secret'],'rotated')
                denied[0]=True;self.assertIsNotNone(r.post(host,{'id':'lastlogin-python'}));self.assertEqual(len(calls),5)
                self.assertIsNotNone(r.post(host,{'id':'other'}));self.assertEqual(len(calls),5)
        finally:server.shutdown();server.server_close();thread.join()
    def test_rejects_public_plaintext_endpoint(self):
        with self.assertRaises(ValueError):BoundRegistration('s','http://public.example/token','a','b',['https://host/register'])
