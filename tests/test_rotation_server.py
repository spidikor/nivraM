"""Rotation uses the same authenticated, bounded local API as other edits."""
import http.client
import json
import threading
import unittest
from unittest.mock import patch

from nivram.chemistry import import_structure
from nivram.rotation import rotate_graph
from nivram.server import make_server


class RotationServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(0)
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(3)
        if cls.thread.is_alive():
            raise AssertionError('Owned rotation API server did not exit.')

    def request(self, data, extra=None):
        port = self.server.server_port
        connection = http.client.HTTPConnection('127.0.0.1',port,timeout=5)
        headers = {'Content-Type':'application/json','X-Nivram-Token':self.server.api_token,
                   'Origin':f'http://127.0.0.1:{port}'}
        headers.update(extra or {})
        try:
            connection.request('POST','/api/rotate',json.dumps(data),headers)
            response=connection.getresponse()
            return response.status,json.loads(response.read())
        finally:
            connection.close()

    def test_rotation_returns_checked_graph_and_scene(self):
        graph=import_structure('C[C@H](O)CC')
        for degrees in (-30,30):
            status,result=self.request({'graph':graph,'degrees':degrees,'selected':[]})
            self.assertEqual(status,200)
            self.assertEqual(result['graph'],rotate_graph(graph,degrees=degrees))
            self.assertEqual(len(result['scene']['positions']),len(graph['atoms']))
            self.assertIn('<svg',result['scene']['svg'])

    def test_validation_rejects_invalid_degree_selection_or_missing_graph(self):
        graph=import_structure('CCO')
        for data in ({'graph':graph,'degrees':360},{'graph':graph,'degrees':'30'},
                     {'graph':graph,'degrees':True},{'graph':graph},
                     {'graph':graph,'degrees':30,'selected':['missing']},{'degrees':30}):
            with self.subTest(data=data):
                status,result=self.request(data)
                self.assertEqual(status,422)
                self.assertIn('error',result)
                self.assertNotIn('graph',result)

    def test_authentication_host_and_origin_fail_before_rotation(self):
        graph=import_structure('CCO')
        for headers in ({'X-Nivram-Token':''},{'Origin':'https://example.invalid'}, {'Host':'example.invalid'}):
            with self.subTest(headers=headers), patch('nivram.rotation.rotate_graph') as rotate:
                status,_=self.request({'graph':graph,'degrees':30},headers)
                self.assertEqual(status,403)
                rotate.assert_not_called()


if __name__=='__main__':
    unittest.main()
