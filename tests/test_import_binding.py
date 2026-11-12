"""Selected graph/controlled-worker checks. No renderer, fonts or real quotas."""
import copy
import json
import subprocess
import unittest
from unittest.mock import patch
from test_isotope_mass import prepared
from test_import_preparation import limit_receipt
from nivram import import_preparation as preparation

class Supervisor:
    def __init__(self, make): self.make, self.requests = make, []
    def shutdown(self, **kwargs): return True
    def check_cancelled(self, token):
        if token.cancelled: raise preparation.WorkerCancelled()
    def run(self, command, **kw):
        request = json.loads(kw['input_text']); self.requests.append(request)
        return subprocess.CompletedProcess(command, 0, json.dumps(self.make(request)), '')

def envelope(result):
    return dict(schema=preparation.SCHEMA,status='ok',limits=limit_receipt(),result=result)

def service(make):
    supervisor = Supervisor(make)
    return preparation.ImportPreparationService(supervisor=supervisor,command_factory=lambda:['controlled']),supervisor

class ImportBindingTests(unittest.TestCase):
    def test_foreign_unbound_response_rejected(self):
        for smiles, style in [('CCN',None),('CCO',{'color':False})]:
            with self.subTest(smiles=smiles,style=style):
                api,_=service(lambda request:envelope(prepared(smiles)))
                with self.assertRaises(preparation.ImportPreparationError) as caught:api.prepare_import('CCO',style)
                self.assertEqual(caught.exception.code,'worker_failed')


from nivram import import_binding as binding
from nivram.properties import input_identity, calculate_properties
from nivram.chemistry import import_structure, export_mol

def controlled_scene(graph,style):
    # Real graph descriptors; synthetic scene, never call render_scene.
    props=calculate_properties(graph); mass=props['molecularWeight']['value']
    return dict(svg='<svg></svg>',positions=[dict(id=a['id'],x=0,y=0) for a in graph['atoms']],
                transform=dict(x0=0,y0=0,xScale=60,yScale=-60),width=300,height=220,
                formula=props['formula'],smiles=props['smiles'],mass=None if mass is None else round(mass,3),
                properties=props,style=copy.deepcopy(style))

def bound_reply(request):
    with patch('nivram.scene.render_scene',side_effect=controlled_scene): result,receipt=preparation._prepare(request)
    return {**envelope(result),'importReceipt':receipt}

class BoundImportTests(unittest.TestCase):
    def test_positive_raw_and_modern_and_unique_invocations(self):
        api,sup=service(bound_reply)
        raw=api.prepare_import('CCO'); self.assertEqual(set(raw),{'graph','scene'})
        req=binding.begin_http(dict(sourceBinding=binding.SCHEMA,requestId='a'*32,documentRevision=3,source='CCO'))
        modern=binding.finish_http(req,api.prepare_import(req['source'],with_receipt=True))
        self.assertEqual(modern['source']['bytes'],3); self.assertEqual(modern['requestId'],'a'*32)
        self.assertEqual(modern['outputIdentity'],raw['scene']['properties']['inputIdentity'])
        self.assertNotEqual(sup.requests[0]['importBinding']['invocationId'],sup.requests[1]['importBinding']['invocationId'])
        raw['graph']['atoms'][0]['symbol']='N';self.assertEqual(modern['graph']['atoms'][0]['symbol'],'C')

    def test_receipt_mutations_fail_closed(self):
        mutations=[lambda r:r.pop('importReceipt'),lambda r:r['importReceipt'].update(invocationId='f'*32),
            lambda r:r['importReceipt'].update(sourceSha256='0'*64),lambda r:r['importReceipt'].update(sourceBytes=True),
            lambda r:r['importReceipt'].update(sourceBytes=4),lambda r:r['importReceipt']['style'].update(color=False),
            lambda r:r['importReceipt'].update(extra=True),lambda r:r.update(extra=True),
            lambda r:r['importReceipt'].update(outputGraphJson='{}'),
            lambda r:r['importReceipt']['outputIdentity'].update(structureHash='0'*64),
            lambda r:r['importReceipt']['outputIdentity'].update(documentRevision=0),
            lambda r:r['result']['graph']['atoms'][0].update(x=True),
            lambda r:r['result']['scene']['style'].update(color=False),
            lambda r:r['result']['scene']['properties']['inputIdentity'].update(smiles='CCN')]
        for mutation in mutations:
            def make(request):
                reply=bound_reply(request);mutation(reply);return reply
            with self.subTest(mutation=mutation):
                api,_=service(make)
                with self.assertRaises(preparation.ImportPreparationError) as caught:api.prepare_import('CCO')
                self.assertEqual(caught.exception.code,'worker_failed')

    def test_coherent_foreign_and_replayed_child_receipts_rejected(self):
        def foreign(request):
            other=copy.deepcopy(request);other['source']='CCN';other['importBinding']=binding.capture('CCN',other['style'])
            return bound_reply(other)
        api,_=service(foreign)
        with self.assertRaises(preparation.ImportPreparationError):api.prepare_import('CCO')
        saved=[]
        def replay(request):
            if not saved:saved.append(bound_reply(request))
            return saved[0]
        api,_=service(replay);api.prepare_import('CCO')
        with self.assertRaises(preparation.ImportPreparationError):api.prepare_import('CCO')

    def test_child_binding_verified_before_parser_and_producer_mutation_rejected(self):
        req=dict(schema=preparation.SCHEMA,mode='import',source='CCO',style=binding.checked_style(None))
        req['importBinding']=binding.capture('CCN',None)
        with patch('nivram.chemistry.import_structure',side_effect=AssertionError('parser called')):
            with self.assertRaises(preparation.ImportPreparationError):preparation._prepare(req)
        req['importBinding']=binding.capture('CCO',None)
        for field in ('graph','style','report'):
            def producer(graph,style):
                scene=controlled_scene(graph,style)
                if field=='graph':graph['atoms'][0]['x']=True
                if field=='style':style['labelSize']=36.0
                if field=='report':scene['properties']['inputIdentity']['smiles']='CCN'
                return scene
            with self.subTest(field=field),patch('nivram.scene.render_scene',side_effect=producer):
                with self.assertRaises(preparation.ImportPreparationError) as caught:preparation._prepare(req)
                self.assertEqual(caught.exception.code,'render_failed')

    def test_exact_unicode_bytes_limits_and_closed_modern_request(self):
        self.assertNotEqual(binding.capture('CCO\n',None)['sourceSha256'],binding.capture('CCO\r\n',None)['sourceSha256'])
        self.assertEqual(binding.capture('é',None)['sourceBytes'],2)
        for source in ('','\ud800','x'*(preparation.MAX_SOURCE_BYTES+1)):
            api,sup=service(bound_reply)
            with self.assertRaises(preparation.ImportPreparationError):api.prepare_import(source)
            self.assertEqual(sup.requests,[])
        good=dict(sourceBinding=binding.SCHEMA,requestId='a'*32,documentRevision=0,source='CCO')
        for change in ({'sourceBinding':'wrong'},{'requestId':'A'*32},{'documentRevision':True},{'documentRevision':2**53},{'extra':1}):
            with self.assertRaises(ValueError):binding.begin_http({**good,**change})
        for field in ('sourceBinding','requestId','documentRevision','source'):
            bad=dict(good);bad.pop(field)
            with self.assertRaises(ValueError):binding.begin_http(bad)
        self.assertIsNone(binding.begin_http({'source':'CCO'}))

    def test_graph_only_supported_normalizations_and_mol_coordinates(self):
        api,_=service(bound_reply)
        for source in ('OCC','C1=CC=CC=C1','c1ccccc1','[13CH3][C@@H](O)C(=O)[O-]','[CH3]','[Na+].[Cl-]'):
            with self.subTest(source=source):
                result=api.prepare_import(source)
                self.assertEqual(result['scene']['properties']['inputIdentity'],input_identity(result['graph']))
        graph=import_structure('CCO');graph['atoms'][0]['x']+=.1234
        mol=export_mol(graph);expected=import_structure(mol)
        self.assertEqual(api.prepare_import(mol)['graph'],expected)

    def test_error_and_cancellation_and_batch_shape_unchanged(self):
        api,_=service(lambda req:dict(schema=preparation.SCHEMA,status='error',code='unavailable'))
        with self.assertRaises(preparation.ImportPreparationError) as caught:api.prepare_import('CCO')
        self.assertEqual(caught.exception.code,'unavailable')
        token=preparation.CancellationToken()
        def cancelled(request):
            reply=bound_reply(request);token.cancel();return reply
        api,_=service(cancelled)
        with self.assertRaises(preparation.WorkerCancelled):api.prepare_import('CCO',cancellation_token=token)
        result=prepared('CCO');result['project']=dict(schema='nivram/1',graph=result['graph'],style=result['scene']['style'])
        api,sup=service(lambda req:envelope(result))
        actual=api.prepare_batch('fixture','.mol',{})
        self.assertEqual(actual,result);self.assertNotIn('importBinding',sup.requests[0])

    def test_parent_modules_are_native_free(self):
        import sys
        from pathlib import Path
        root=str(Path(__file__).resolve().parents[1])
        code=f'import sys;sys.path.insert(0,{root!r});import nivram.import_binding,nivram.import_preparation;assert not any(k=="rdkit" or k.startswith("rdkit.") for k in sys.modules)'
        done=subprocess.run([sys.executable,'-I','-S','-B','-c',code],capture_output=True,timeout=10)
        self.assertEqual(done.returncode,0,done.stderr)

class ImportHttpTests(unittest.TestCase):
    def test_modern_legacy_invalid_and_foreign_on_actual_route(self):
        import http.client
        import threading
        from nivram import server as module
        server=module.make_server(0);original=server.import_preparation
        api,sup=service(bound_reply);server.import_preparation=api
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        def post(data):
            connection.request('POST','/api/import',json.dumps(data),{'Content-Type':'application/json','X-Nivram-Token':server.api_token})
            response=connection.getresponse();return response.status,json.loads(response.read())
        try:
            with patch.object(module,'render_scene',side_effect=AssertionError('host render')),patch.object(module,'LOCK',new=unittest.mock.Mock(__enter__=unittest.mock.Mock(side_effect=AssertionError('lock')),__exit__=unittest.mock.Mock())):
                status,raw=post({'source':'CCO'});self.assertEqual(status,200);self.assertEqual(set(raw),{'graph','scene'})
                modern=dict(sourceBinding=binding.SCHEMA,requestId='b'*32,documentRevision=7,source='CCO',style={'color':False})
                status,result=post(modern);self.assertEqual(status,200);self.assertEqual(result['schema'],binding.SCHEMA)
                self.assertFalse(result['style']['color']);self.assertEqual(result['documentRevision'],7)
                count=len(sup.requests)
                for invalid in ({'source':'CCO','requestId':'b'*32},{**modern,'documentRevision':True},{**modern,'extra':True}):
                    status,result=post(invalid);self.assertNotEqual(status,200)
                self.assertEqual(len(sup.requests),count)
                server.import_preparation=service(lambda req:envelope(prepared('CCN')))[0]
                status,result=post(modern);self.assertEqual(status,502);self.assertNotIn('graph',result)
        finally:
            connection.close();server.shutdown();server.server_close();thread.join(3);original.shutdown()

if __name__=='__main__': unittest.main()
