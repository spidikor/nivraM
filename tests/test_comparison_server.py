import http.client,json,threading,unittest
from nivram.server import make_server
from test_comparison import payload

class ComparisonServerTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.server=make_server(0);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
 @classmethod
 def tearDownClass(cls):
  cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=3)
 def request(self,data,extra=None):
  port=self.server.server_port;c=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
  headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{port}','X-Nivram-Token':self.server.api_token};headers.update(extra or {})
  c.request('POST','/api/comparison',json.dumps(data),headers);r=c.getresponse();b=json.loads(r.read());c.close();return r.status,b
 def test_real_pair_properties_and_bound_identity(self):
  p=payload();status,r=self.request(p);self.assertEqual(status,200);self.assertEqual(r['requestId'],p['requestId']);self.assertEqual(r['pinId'],p['pinId']);self.assertEqual(r['pinned']['formula'],'C2H6O');self.assertEqual(r['current']['formula'],'C2H4O')
 def test_existing_security_admission(self):
  for h in [{'X-Nivram-Token':''},{'Origin':'https://example.invalid'},{'Host':'example.invalid'}]:self.assertEqual(self.request(payload(),h)[0],403)
 def test_malformed_identity_and_graph_reject(self):
  for mutate in [lambda p:p.update(documentRevision=True),lambda p:p.pop('pinId'),lambda p:p.update(requestId='bad'),lambda p:p['pinned']['graph']['bonds'][0].update(order=9)]:
   p=payload();mutate(p);status,r=self.request(p);self.assertEqual(status,422);self.assertNotIn('pinned',r)
 def test_empty_real_previews(self):
  p=payload();p['pinned']['graph']=p['current']['graph']={'atoms':[],'bonds':[]};status,r=self.request(p);self.assertEqual(status,200);self.assertEqual(r['pinned']['preview']['status'],'available');self.assertEqual(r['current']['preview']['status'],'available')

if __name__=='__main__':unittest.main()
