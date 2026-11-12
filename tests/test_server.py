import unittest,threading,http.client,json
from unittest.mock import Mock
from nivram.server import make_server, Handler

class DisconnectedClientTests(unittest.TestCase):
    def test_disconnect_during_headers_or_body_is_contained(self):
        for stage in ('headers', 'body'):
            with self.subTest(stage=stage):
                handler = object.__new__(Handler)
                handler.send_response = Mock()
                handler.send_header = Mock()
                handler.end_headers = Mock()
                handler.wfile = Mock()
                target = handler.end_headers if stage == 'headers' else handler.wfile.write
                target.side_effect = ConnectionAbortedError('client closed')
                handler._send(200, {'status': 'ready'})

class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=make_server(0);cls.port=cls.server.server_port
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=3)
    def request(self,path='/',method='GET',obj=None,extra=None):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        headers={'Content-Type':'application/json','X-Nivram-Token':self.server.api_token,
                 'Origin':f'http://127.0.0.1:{self.port}'}
        headers.update(extra or {})
        c.request(method,path,body=json.dumps(obj) if obj is not None else None,headers=headers)
        r=c.getresponse();data=r.read();result=(r.status,dict(r.getheaders()),data);c.close();return result
    def test_render_valence_diagnostic(self):
        from tests.test_valence_feedback import star
        for symbol,count in [('C',5),('O',3)]:
            status,_,body=self.request('/api/render','POST',{'graph':star(symbol,count)})
            self.assertEqual(status,422)
            self.assertEqual(json.loads(body)['diagnostic'],{'code':'atom-valence','atomIds':['source-center']})
        graph=star();graph['bonds'][0]['order']=7
        status,_,body=self.request('/api/render','POST',{'graph':graph})
        self.assertEqual(status,422);self.assertNotIn('diagnostic',json.loads(body))

    def test_root_and_csp(self):
        status,headers,body=self.request();self.assertEqual(status,200)
        self.assertIn(b'nivraM',body);self.assertNotIn(b'__TOKEN__',body)
        self.assertIn("connect-src 'self'",headers['Content-Security-Policy'])
    def test_import(self):
        status,_,body=self.request('/api/import','POST',{'source':'CCO'})
        self.assertEqual(status,200);self.assertEqual(len(json.loads(body)['graph']['atoms']),3)
    def test_missing_token(self):
        self.assertEqual(self.request('/api/import','POST',{'source':'CCO'},{'X-Nivram-Token':''})[0],403)
    def test_foreign_origin(self):
        self.assertEqual(self.request('/api/import','POST',{'source':'CCO'},{'Origin':'https://example.invalid'})[0],403)
    def test_foreign_host(self):
        self.assertEqual(self.request(extra={'Host':'attacker.invalid'})[0],403)
    def test_traversal(self):
        self.assertEqual(self.request('/modules/../../nivram/chemistry.py')[0],404)
    def test_json_array(self):
        self.assertEqual(self.request('/api/import','POST',[])[0],400)
    def test_invalid_structure_error(self):
        self.assertEqual(self.request('/api/import','POST',{'source':'CO(C)C'})[0],422)
    def test_unknown_api(self):
        self.assertEqual(self.request('/api/unknown','POST',{})[0],404)
if __name__=='__main__':unittest.main()
