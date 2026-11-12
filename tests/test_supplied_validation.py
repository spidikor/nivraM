"""Real RDKit and disposable Linux worker checks; no browser/Windows claim."""
import copy
import hashlib
import json
import subprocess
import time
import unittest
from unittest.mock import Mock, patch
from nivram.supplied_validation import validate, validate_request, validate_result, run_request, ENGINE_VERSION
from nivram.conformer import generate
from nivram.worker_processes import WorkerSupervisor, CancellationToken, WorkerCancelled
from tests.test_conformer import request as generation_request


def request(text):
    return dict(molText=text, molSha256=hashlib.sha256(text.encode()).hexdigest(),
                clientId='a'*32, jobId='b'*32, expiresAt=int(time.time()*1000)+30000)


class SuppliedValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texts = {s: generate(generation_request(s))['scene']['sourceText'] for s in
                     ['C', 'CCO', 'C[C@H](O)C(=O)O', 'F/C=C/F', 'F/C=C\\F', '[H+]', '[SiH3-]', '[13CH3][NH3+]', '[CH3:7][OH:9]']}
        cls.text = cls.texts['CCO']

    def test_real_rdkit_supported_inputs_no_generation_during_parse(self):
        from rdkit import rdBase
        from rdkit.Chem import rdDistGeom, AllChem
        self.assertEqual(rdBase.rdkitVersion, ENGINE_VERSION)
        self.assertIn('VAL=-1', self.texts['[H+]'])
        self.assertIn('VAL=3', self.texts['[SiH3-]'])
        for s, text in self.texts.items():
            with self.subTest(smiles=s), patch.object(rdDistGeom, 'EmbedMolecule', side_effect=AssertionError('no embedding')), patch.object(AllChem, 'MMFFOptimizeMolecule', side_effect=AssertionError('no optimization')), patch.object(AllChem, 'UFFOptimizeMolecule', side_effect=AssertionError('no optimization')):
                result = validate(request(text))
                self.assertEqual(result['scene']['sourceText'].encode(), text.encode())
                self.assertEqual(result['scene']['kind'], 'supplied_coordinates')
                self.assertNotIn('provenance', result)
                self.assertFalse(result['validation']['generationClaimsVerified'])
                self.assertFalse(result['validation']['experimentalOriginVerified'])
                self.assertFalse(result['validation']['sourceBindingReverified'])

    def test_atom_cfg_flip_and_geometry_reflection_rejected(self):
        import re
        text = self.texts['C[C@H](O)C(=O)O']
        flipped = re.sub(r'CFG=([12])', lambda m: 'CFG='+('1' if m[1]=='2' else '2'), text)
        lines = text.splitlines(keepends=True)
        reflected = ''.join(re.sub(r'^(M  V30 \d+ [A-Z][a-z]? )(-?\d+\.\d+)', lambda m: m[1]+str(-float(m[2])), line) for line in lines)
        for bad in [flipped, reflected]:
            with self.assertRaisesRegex(ValueError, 'declarations change or conflict'):
                validate(request(bad))

    def test_bond_cfg_cannot_be_silently_ignored(self):
        text = self.text
        line = text.split('M  V30 BEGIN BOND\n')[1].splitlines()[0]
        for prop in ['CFG=1', 'CFG=2', 'CFG=3']:
            with self.subTest(prop=prop), self.assertRaises(ValueError):
                validate(request(text.replace(line, line+' '+prop)))

    def test_val_redundant_default_and_conflicting_normalization_rejected(self):
        line = next(x for x in self.text.splitlines() if x.startswith('M  V30 1 C '))
        for prop in ['VAL=0', 'VAL=4', 'VAL=-1', 'VAL=8', 'VAL=3']:
            with self.subTest(prop=prop), self.assertRaises(ValueError):
                validate(request(self.text.replace(line, line+' '+prop)))
        for value in ['0', '1', '2', '4']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate(request(self.texts['[SiH3-]'].replace('VAL=3', 'VAL='+value)))

    def test_planar_ez_is_engine_interpretation_not_declared_cfg(self):
        from rdkit import Chem
        from rdkit.Chem import rdDepictor
        outputs=[]
        for smiles in ['F/C=C/F', 'F/C=C\\F']:
            mol=Chem.MolFromSmiles(smiles); rdDepictor.Compute2DCoords(mol)
            mol.GetConformer().Set3D(True)
            text=Chem.MolToMolBlock(mol, forceV3000=True)
            self.assertNotIn('CFG=', text)
            result=validate(request(text));outputs.append(result['validation']['engineDerivedIsomericSmiles'])
            self.assertIn('engine-derived', result['validation']['interpretation'])
        self.assertNotEqual(*outputs)

    def test_unspecified_planar_tetrahedral_rejected(self):
        from rdkit import Chem
        from rdkit.Chem import rdDepictor
        mol=Chem.MolFromSmiles('CC(O)C(=O)O');rdDepictor.Compute2DCoords(mol);mol.GetConformer().Set3D(True)
        text=Chem.MolToMolBlock(mol, forceV3000=True)
        with self.assertRaisesRegex(ValueError, 'unspecified'):
            validate(request(text))

    def test_unsupported_features_rejected_before_native(self):
        from rdkit import Chem
        line=next(x for x in self.text.splitlines() if x.startswith('M  V30 1 C '))
        mutations=[self.text.replace(line,line+' '+p) for p in ['RAD=1','HCOUNT=1','CFG=3','VAL=3 VAL=4','ATTCHPT=1','RGROUPS=(1 2)']]
        mutations += [self.text.replace('M  V30 END CTAB', 'M  V30 BEGIN COLLECTION\nM  V30 END CTAB'),self.text+'$$$$\n']
        for text in mutations:
            with self.subTest(text=text), patch.object(Chem, 'MolFromMolBlock', side_effect=AssertionError('native parser must not run')), self.assertRaises(ValueError):
                validate(request(text))

    def test_request_bounds_no_forged_generation_fields(self):
        for field,value in [('expiresAt',True),('expiresAt',1),('expiresAt',int(time.time()*1000)+120000),('clientId','bad'),('molSha256','0'*64),('molText','x'*262145),('provenance',{}),('canonicalIsomericSmiles','C')]:
            bad=request(self.text);bad[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):validate_request(bad)

    def test_full_result_validation_stale_schema_source_coordinates(self):
        data=request(self.text); good=validate(data)
        cases=[]
        for key,value in [('jobId','c'*32),('clientId','c'*32),('molSha256','0'*64),('extra',True)]:
            bad=copy.deepcopy(good);bad[key]=value;cases.append(bad)
        for key,value in [('x',99.0),('id',True),('declarations',{'VAL':4}),('sourceId','a1')]:
            bad=copy.deepcopy(good);bad['scene']['atoms'][0][key]=value;cases.append(bad)
        for key,value in [('sourceText',self.text+'\n'),('kind','generated_coordinates')]:
            bad=copy.deepcopy(good);bad['scene'][key]=value;cases.append(bad)
        for key,value in [('generationClaimsVerified',True),('declarationsPreserved',1),('engineVersion','other'),('engineDerivedIsomericSmiles',''),('extra','bad')]:
            bad=copy.deepcopy(good);bad['validation'][key]=value;cases.append(bad)
        for bad in cases:
            with self.assertRaises(ValueError):validate_result(bad,data)

    def test_real_worker_and_cancel(self):
        supervisor=WorkerSupervisor(max_workers=1)
        try:
            data=request(self.texts['C[C@H](O)C(=O)O'])
            result=run_request(data,supervisor,CancellationToken())
            self.assertEqual(result,validate(data))
            token=CancellationToken();token.cancel()
            with self.assertRaises(WorkerCancelled):run_request(request(self.text),supervisor,token)
        finally:self.assertTrue(supervisor.shutdown())

    def test_worker_errors_late_return_and_transport_contract(self):
        supervisor=Mock();data=request(self.text);result=validate(data)
        supervisor.run.return_value=Mock(returncode=0,stdout=json.dumps(result))
        run_request(data,supervisor,CancellationToken())
        call=supervisor.run.call_args.kwargs
        self.assertLessEqual(call['timeout_seconds'],15)
        self.assertEqual((call['max_input_bytes'],call['max_stdout_bytes'],call['max_stderr_bytes']),(524288,524288,8192))
        for output in ['{}','[]','{"jobId":1,"jobId":2}',json.dumps({**result,'jobId':'c'*32})]:
            supervisor.run.return_value=Mock(returncode=0,stdout=output)
            with self.assertRaises(ValueError):run_request(data,supervisor,CancellationToken())
        supervisor.run.side_effect=subprocess.TimeoutExpired('worker',15)
        with self.assertRaisesRegex(ValueError,'15-second'):run_request(data,supervisor,CancellationToken())
        supervisor.run.side_effect=lambda *a,**kw: (data.update(expiresAt=1) or Mock(returncode=0,stdout=json.dumps(result)))
        with self.assertRaisesRegex(ValueError,'expired'):run_request(data,supervisor,CancellationToken())

    def test_actual_http_boundary_and_duplicate_request(self):
        import http.client
        import threading
        from nivram.server import LocalServer, Handler
        server=LocalServer(('127.0.0.1',0),Handler);server.api_token='token'
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            def post(body, token='token'):
                connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
                try:
                    connection.request('POST','/api/supplied-validation',body,{'Content-Type':'application/json','X-Nivram-Token':token})
                    response=connection.getresponse();return response.status,json.loads(response.read())
                finally:connection.close()
            data=request(self.text)
            self.assertEqual(post(json.dumps(data),'wrong')[0],403)
            duplicate=json.dumps(data)[:-1]+',"jobId":"'+data['jobId']+'"}'
            self.assertEqual(post(duplicate)[0],422)
            status,result=post(json.dumps(data));self.assertEqual(status,200)
            validate_result(result,data)
        finally:
            server.shutdown();thread.join(timeout=3);server.server_close()
        self.assertFalse(server.supplied_validation_jobs.snapshot()['acceptingJobs'])
        self.assertEqual(server.worker_shutdown_receipt['status'],'confirmed')

    def test_cancel_during_worker_completion(self):
        data=request(self.text);result=validate(data);token=CancellationToken()
        supervisor=Mock()
        supervisor.check_cancelled.side_effect=lambda t: (_ for _ in ()).throw(WorkerCancelled()) if t.cancelled else None
        supervisor.run.side_effect=lambda *a,**kw: (token.cancel() or Mock(returncode=0,stdout=json.dumps(result)))
        with self.assertRaises(WorkerCancelled):run_request(data,supervisor,token)

    def test_authenticated_route_registry_cancel_and_bad_origin(self):
        from email.message import Message
        from types import SimpleNamespace
        from nivram.server import Handler, NamingJobs
        supervisor=WorkerSupervisor(max_workers=1)
        try:
            registry=NamingJobs()
            def handler(data, route='/api/supplied-validation', token='token',origin=None):
                h=object.__new__(Handler);body=json.dumps(data).encode()
                h.server=SimpleNamespace(api_token='token',server_port=32100,supplied_validation_jobs=registry,worker_supervisor=supervisor)
                h.path=route;h._local=Mock(return_value=True);h.headers=Message()
                for key,value in [('Content-Type','application/json'),('Content-Length',str(len(body))),('X-Nivram-Token',token)]:h.headers[key]=value
                if origin is not None:h.headers['Origin']=origin
                h._read_body=Mock(return_value=body);h._send=Mock();h._reject_post=Mock()
                h.do_POST();return h
            data=request(self.text)
            for token,origin in [('wrong',None),('token','http://evil.test')]:
                h=handler(data,token=token,origin=origin);self.assertEqual(h._reject_post.call_args.args[0],403);h._read_body.assert_not_called()
            h=handler(data);self.assertEqual(h._send.call_args.args[0],200)
            self.assertEqual(h._send.call_args.args[1]['scene']['sourceText'],self.text)
            self.assertEqual(registry.snapshot()['activeJobs'],0)
            h=handler(data);self.assertEqual(h._send.call_args.args[0],409)
            data['jobId']='c'*32
            cancel={k:data[k] for k in ['clientId','jobId','expiresAt']}
            h=handler(cancel,'/api/supplied-validation/cancel');self.assertEqual(h._send.call_args.args[1]['status'],'cancelled_before_registration')
            h=handler(data);self.assertEqual(h._send.call_args.args[0],409)
            registry.close();data['jobId']='d'*32
            h=handler(data);self.assertEqual(h._send.call_args.args[0],503)
        finally:self.assertTrue(supervisor.shutdown())


if __name__=='__main__':unittest.main()
