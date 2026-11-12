import http.client
import json
import threading
import unittest
from nivram.chemistry import import_structure
from nivram.server import make_server

class StereoInspectorServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=make_server(0);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=3)
    def request(self,data,headers=None):
        port=self.server.server_port;c=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
        h={'Content-Type':'application/json','X-Nivram-Token':self.server.api_token,'Origin':f'http://127.0.0.1:{port}'};h.update(headers or {})
        c.request('POST','/api/stereo-inspector',json.dumps(data),h);r=c.getresponse();body=json.loads(r.read());c.close();return r.status,body
    def payload(self):return {'graph':import_structure('C[C@H](O)F'),'documentRevision':5,'requestId':'1'*32}
    def test_real_local_http_assignment_without_rendering(self):
        request=self.payload();status,body=self.request(request)
        self.assertEqual(status,200);self.assertEqual(body['sites'][0]['label'],'R');self.assertEqual(body['requestId'],request['requestId']);self.assertEqual(body['inputIdentity']['documentRevision'],5)
    def test_requires_existing_token_and_origin(self):
        for headers in [{'X-Nivram-Token':''},{'Origin':'https://example.invalid'}]:
            self.assertEqual(self.request(self.payload(),headers)[0],403)
    def test_malformed_envelope_rejected(self):
        for mutate in [lambda p:p.pop('requestId'),lambda p:p.update(requestId='wrong'),lambda p:p.update(documentRevision=True),lambda p:p.update(documentRevision=-1),lambda p:p.update(extra=True)]:
            p=self.payload();mutate(p);self.assertEqual(self.request(p)[0],422)
    def test_invalid_chemistry_rejected_not_empty_report(self):
        p=self.payload();p['graph']['bonds'][0]['order']=7;status,body=self.request(p)
        self.assertEqual(status,422);self.assertNotIn('sites',body)
    def test_empty_is_explicit(self):
        p=self.payload();p['graph']={'atoms':[],'bonds':[]};status,body=self.request(p)
        self.assertEqual(status,200);self.assertEqual(body['status'],'empty');self.assertEqual(body['sites'],[])

if __name__=='__main__':unittest.main()
