"""Deterministic transport nesting checks, not parser recursion-limit assumptions."""
import http.client
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_server_admission as admission
from nivram.server import Handler

LIMIT=64

def nested(depth,kind='object'):
    if kind=='object':return b'{"x":'*depth+b'0'+b'}'*depth
    if kind=='array':return b'{"x":'+b'['*(depth-1)+b'0'+b']'*(depth-1)+b'}'
    opens=[b'{"x":' if i%2==0 else b'[' for i in range(depth)]
    return b''.join(opens)+b'0'+b''.join(b'}' if i%2==0 else b']' for i in reversed(range(depth)))

def request(body,path='/api/unknown',server=None):
    handler=object.__new__(Handler);handler.server=server or SimpleNamespace(server_port=12345,api_token='synthetic-token')
    handler.command,handler.path='POST',path
    handler.headers=http.client.HTTPMessage()
    for name,value in {'Host':'127.0.0.1:12345','Origin':'http://127.0.0.1:12345','X-Nivram-Token':'synthetic-token','Content-Type':'application/json','Content-Length':str(len(body))}.items():handler.headers[name]=value
    handler.rfile,handler.wfile=io.BytesIO(body),io.BytesIO();handler.connection=admission.BoundarySocket();receipts=[]
    handler._send=lambda status,payload,**kwargs:receipts.append((status,payload));handler.do_POST()
    assert len(receipts)==1
    return receipts[0]

class RequestJsonDepthTests(unittest.TestCase):
    def test_limit_exact_and_plus_one_for_object_array_and_mixed(self):
        for kind in ('object','array','mixed'):
            for depth in (1,2,LIMIT-1,LIMIT,LIMIT+1,4000):
                with self.subTest(kind=kind,depth=depth):
                    self.assertEqual(request(nested(depth,kind))[0],404 if depth<=LIMIT else 400)

    def test_over_limit_rejected_before_json_decoder_even_if_duplicate_key_would_hide_it(self):
        bodies=[nested(LIMIT+1),b'{"x":'+nested(LIMIT)+b',"x":0}',b'{"x":['*10000+b'0'+b']}'*10000]
        for body in bodies:
            with self.subTest(bytes=len(body)),patch('nivram.server.json.loads',side_effect=AssertionError('Deep body reached parser')):
                self.assertEqual(request(body),(400,{'error':'Invalid JSON.'}))

    def test_quoted_brackets_unicode_escaped_quotes_backslashes_and_encoded_source_are_not_nesting(self):
        for text in ('['*10000+'}'*10000,'\\"[{}]','\\\\\\"[{}]', '\\'*129+'"}','α😀[]{}', '{"x":'*4000+'0'+'}'*4000):
            with self.subTest(text_length=len(text)):
                body=json.dumps({'source':text},ensure_ascii=False).encode()
                self.assertEqual(request(body)[0],404)
        for escaped in (b'{"x":"\\u005b\\u005d\\u007b\\u007d"}',b'{"x":"\\\"}[[["}',b'{"x":"\\\\"}'):
            self.assertEqual(request(escaped)[0],404)

    def test_hidden_deep_branch_after_escaped_text_is_still_counted(self):
        for prefix in ('\\"[[{{','\\\\','😀"}',['[]']):
            body=b'{"source":'+json.dumps(prefix,ensure_ascii=False).encode()+b',"deep":'+nested(LIMIT)+b'}'
            self.assertEqual(request(body)[0],400)

    def test_decoder_retains_grammar_utf8_nonfinite_and_object_root_rules(self):
        for body in (b'{"x":}',b'{"x":[}',b'{"x":"unterminated}',b'{"x":"\\q"}',b'{"x":"\xff"}',b'{"x":NaN}',b'[]',b'0',b'null',b'{"x":1} {"y":2}'):
            with self.subTest(body=body):self.assertEqual(request(body)[0],400)
        self.assertEqual(request(b' {"x":1} \r\n')[0],404)

    def test_accepted_named_route_receives_original_values_and_overdeep_never_dispatches(self):
        seen=[]
        service=SimpleNamespace(evaluate=lambda data:seen.append(data) or {'ok':True})
        server=SimpleNamespace(server_port=12345,api_token='synthetic-token',population_jobs=service)
        body=json.dumps({'source':'[{}]','x':{'y':[1,2]},'unicode':'α😀'},ensure_ascii=False).encode()
        self.assertEqual(request(body,'/api/populations/evaluate',server),(200,{'ok':True}))
        self.assertEqual(seen,[json.loads(body)])
        self.assertEqual(request(nested(LIMIT+1),'/api/populations/evaluate',server)[0],400)
        self.assertEqual(len(seen),1)

class RequestJsonDepthSocketTests(unittest.TestCase):
    # Reuse only the owned loopback harness, not inherited regression methods.
    setUp=admission.AdmissionTests.setUp
    stop_server=admission.AdmissionTests.stop_server
    connect=admission.AdmissionTests.connect
    read_response=admission.AdmissionTests.read_response
    request=admission.AdmissionTests.request

    def test_exact_limit_and_rejection_on_real_loopback_socket(self):
        for kind in ('object','array','mixed'):
            self.assertEqual(self.request(body=nested(LIMIT,kind))[0],404)
            self.assertEqual(self.request(body=nested(LIMIT+1,kind))[0],400)
        self.assertEqual(self.server.handler_errors,[])

    def test_quoted_contents_do_not_reduce_real_socket_limit(self):
        body=b'{"source":'+json.dumps('"\\[]{}'*1000).encode()+b',"value":'+nested(LIMIT-1)+b'}'
        self.assertEqual(self.request(body=body)[0],404)
        self.assertEqual(self.server.handler_errors,[])

if __name__=='__main__':unittest.main()
