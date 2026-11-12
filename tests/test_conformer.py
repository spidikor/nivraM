import copy, hashlib, json, time, unittest
from unittest.mock import Mock
from rdkit import Chem
from nivram.chemistry import import_structure
from nivram.conformer import generate, run_request
from nivram.worker_processes import WorkerSupervisor, CancellationToken, WorkerCancelled
from nivram.server import NamingJobs


def request(smiles='CCO'):
    graph=import_structure(smiles)
    text=json.dumps(graph,separators=(',',':'),ensure_ascii=False)
    return dict(sourceContextText='null',sourceContextHash=hashlib.sha256(b'null').hexdigest(),graphText=text,sourceRevision=7,sourceHash=hashlib.sha256(text.encode()).hexdigest(),clientId='a'*32,jobId='b'*32,expiresAt=int(time.time()*1000)+30000)

class ConformerTests(unittest.TestCase):
    def test_real_stereo_isotope_charge_roundtrip_and_nonmutation(self):
        for smiles in ['CCO','C[C@H](O)C(=O)O','F/C=C/F','[13CH3][NH3+]','c1ccccc1','[2H]OC']:
            with self.subTest(smiles=smiles):
                data=request(smiles);before=copy.deepcopy(data);result=generate(data)
                self.assertEqual(data,before)
                restored=Chem.MolFromMolBlock(result['scene']['sourceText'],removeHs=False)
                self.assertEqual(Chem.MolToSmiles(restored),result['provenance']['canonicalIsomericSmiles'])
                self.assertEqual(result['sourceHash'],data['sourceHash'])
                self.assertEqual([a['sourceId'] for a in result['scene']['atoms']],[a['id'] for a in json.loads(data['graphText'])['atoms']])
                self.assertTrue(result['provenance']['generated'])
                self.assertEqual(result['scene']['kind'],'generated_coordinates')
    def test_safe_image_context_roundtrip_and_limits(self):
        from nivram.conformer_context import checked_context,SCHEMA,RELATIONSHIP
        context={'schema':SCHEMA,'currentGraphRelationship':RELATIONSHIP,'imageImport':{
            'kind':'png-optical-reconstruction','sourceSha256':'c'*64,'warnings':['Recognized graph needs review.'],
            'imageCorrespondence':'user-reviewed; not independently verified',
            'recognition':{'method':'MolScribe','inputSha256':'d'*64,'modelSha256':'e'*64,'confidence':.75,'confidenceMeaning':'uncalibrated model score'},
            'reviewedRegion':{'kind':'crop','sourceWidth':100,'sourceHeight':100,'width':20,'height':20,'rect':{'x':1,'y':2,'width':20,'height':20}},
            'correction':{'kind':'user-edited-graph','imageCorrespondence':'user-reviewed; not independently verified'}}}
        data=request();data['sourceContextText']=json.dumps(context,separators=(',',':'));data['sourceContextHash']=hashlib.sha256(data['sourceContextText'].encode()).hexdigest()
        result=generate(data)
        self.assertEqual(result['provenance']['sourceContext'],context)
        self.assertEqual(result['provenance']['sourceContextSha256'],data['sourceContextHash'])
        supervisor=WorkerSupervisor(max_workers=1)
        try:self.assertEqual(run_request(data,supervisor,CancellationToken())['provenance']['sourceContext'],context)
        finally:supervisor.shutdown()
        for change in ('path','graph','warning','context'):
            bad=copy.deepcopy(context)
            if change=='path':bad['imageImport']['sourceName']='/private/source.png'
            if change=='graph':bad['imageImport']['correction']['reviewedGraph']={}
            if change=='warning':bad['imageImport']['warnings']=['x']*33
            if change=='context':bad['imageImport']['recognition']['confidence']=2
            text=json.dumps(bad)
            with self.subTest(change=change),self.assertRaises(ValueError):checked_context(text,hashlib.sha256(text.encode()).hexdigest())
        with self.assertRaises(ValueError):checked_context(data['sourceContextText'],'0'*64)
        with self.assertRaises(ValueError):checked_context(' '*49153,'0'*64)
        self.assertIsNone(generate(request())['provenance']['sourceContext'])

    def test_determinism_within_engine(self):
        data=request('CCCO');self.assertEqual(generate(data),generate(data))
    def test_reject_ambiguity_disconnected_radical_and_metal(self):
        for smiles in ['CC(O)C(=O)O','FC=CF','CC.O','[CH3]','[Na+]']:
            with self.subTest(smiles=smiles),self.assertRaises(ValueError):generate(request(smiles))
    def test_bad_hash_and_oversized_graph(self):
        data=request();data['sourceHash']='0'*64
        with self.assertRaises(ValueError):generate(data)
        data=request('C'*129)
        with self.assertRaises(ValueError):generate(data)
    def test_actual_worker_entrypoint(self):
        supervisor=WorkerSupervisor(max_workers=1)
        try:
            result=run_request(request(),supervisor,CancellationToken())
            self.assertEqual(result['provenance']['method'],'RDKit ETKDGv3')
            token=CancellationToken();token.cancel()
            with self.assertRaises(WorkerCancelled):run_request(request(),supervisor,token)
        finally:self.assertTrue(supervisor.shutdown())
    def test_cancellation_tombstone_and_reused_id(self):
        registry=NamingJobs();data=request();cancel={k:data[k] for k in ('clientId','jobId','expiresAt')}
        registry.cancel(cancel)
        with self.assertRaises(ValueError):registry.register(data)
        registry=NamingJobs();key,token=registry.register(data);registry.cancel(cancel)
        self.assertTrue(token.cancelled);registry.finish(key,token)
        with self.assertRaises(ValueError):registry.register(data)
    def test_actual_handler_route_without_socket(self):
        from email.message import Message
        from types import SimpleNamespace
        from nivram.server import Handler
        supervisor=WorkerSupervisor(max_workers=1)
        try:
            data=request();body=json.dumps(data).encode()
            handler=object.__new__(Handler)
            handler.server=SimpleNamespace(api_token='token',server_port=32100,conformer_jobs=NamingJobs(),worker_supervisor=supervisor)
            handler.path='/api/conformer';handler._local=Mock(return_value=True)
            handler.headers=Message()
            for key,value in [('Content-Type','application/json'),('Content-Length',str(len(body))),('X-Nivram-Token','token')]:handler.headers[key]=value
            handler._read_body=Mock(return_value=body);handler._send=Mock();handler._reject_post=Mock()
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0],200)
            self.assertEqual(handler._send.call_args.args[1]['scene']['kind'],'generated_coordinates')
            self.assertEqual(handler.server.conformer_jobs.snapshot()['activeJobs'],0)
            handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0],409)
        finally:self.assertTrue(supervisor.shutdown())
    def test_worker_binding_and_deadline(self):
        supervisor=Mock();supervisor.run.return_value=Mock(returncode=0,stdout=json.dumps({'sourceHash':'wrong','sourceRevision':7}))
        with self.assertRaises(ValueError):run_request(request(),supervisor,CancellationToken())
        data=request();data['expiresAt']=1
        with self.assertRaises(ValueError):run_request(data,supervisor,CancellationToken())

if __name__=='__main__':unittest.main()
