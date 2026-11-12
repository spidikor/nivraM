"""Real parse-only worker checks. These do not qualify Windows/browser delivery."""
import copy
import hashlib
import json
import subprocess
import time
import unittest
from unittest.mock import Mock, patch

from nivram.coordinate_recovery import recover, run_request, validate_request, _v3000
from nivram.conformer import generate
from nivram.worker_processes import WorkerSupervisor, CancellationToken, WorkerCancelled
from tests.test_conformer import request as generation_request


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def make_record(generated):
    text=generated['scene']['sourceText']
    provenance=json.dumps(generated['provenance'],separators=(',',':'))
    return dict(schema='nivram-png-coordinates/2',producer='nivraM',version='1.0.0',dimension=3,
        recordedOrigin='generated_coordinates',format='MOL V3000',units='angstrom',molText=text,
        molSha256=sha(text),molBytes=len(text.encode()),provenanceJson=provenance,
        provenanceSha256=sha(provenance),sourceContextText='null',sourceContextSha256=sha('null'),
        sourceAtomIds=[a['sourceId'] for a in generated['scene']['atoms']])


def request(record):
    text=json.dumps(record,separators=(',',':'))
    return dict(recordText=text,recordSha256=sha(text),clientId='a'*32,jobId='b'*32,
                expiresAt=int(time.time()*1000)+30000)


def altered(record, transform):
    result=copy.deepcopy(record)
    result['molText']=transform(result['molText'])
    result['molSha256']=sha(result['molText']);result['molBytes']=len(result['molText'].encode())
    p=json.loads(result['provenanceJson']);p['outputSha256']=result['molSha256']
    result['provenanceJson']=json.dumps(p);result['provenanceSha256']=sha(result['provenanceJson'])
    return result


class CoordinateRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated=generate(generation_request())
        cls.record=make_record(cls.generated)

    def test_real_generated_supported_subset_parse_only(self):
        from rdkit.Chem import rdDistGeom, AllChem
        for smiles in ['C','O','[NH4+]','Cl','CCO','C[C@H](O)C(=O)O','F/C=C/F','[13CH3][NH3+]','c1ccccc1','[2H]OC','[CH3:7][OH:9]']:
            generated=generate(generation_request(smiles))
            with self.subTest(smiles=smiles), patch.object(rdDistGeom,'EmbedMolecule',side_effect=AssertionError('must not embed')), patch.object(AllChem,'MMFFOptimizeMolecule',side_effect=AssertionError('must not minimize')), patch.object(AllChem,'UFFOptimizeMolecule',side_effect=AssertionError('must not minimize')):
                result=recover(request(make_record(generated)))
                self.assertEqual(result['scene']['sourceText'],generated['scene']['sourceText'])
                self.assertEqual(result['scene']['atoms'],generated['scene']['atoms'])
                self.assertEqual(result['scene']['bonds'],generated['scene']['bonds'])
                self.assertEqual(result['scene']['kind'],'recovered_coordinates')
                self.assertFalse(result['validation']['generationClaimsVerified'])
                self.assertFalse(result['validation']['sourceBindingReverified'])

    def test_actual_isolated_worker_and_cancel(self):
        supervisor=WorkerSupervisor(max_workers=1)
        try:
            result=run_request(request(self.record),supervisor,CancellationToken())
            self.assertEqual(result['scene']['atoms'],self.generated['scene']['atoms'])
            token=CancellationToken();token.cancel()
            with self.assertRaises(WorkerCancelled):run_request(request(self.record),supervisor,token)
        finally:self.assertTrue(supervisor.shutdown())

    def test_reject_features_before_native_parse(self):
        cases=[lambda s:s.replace('M  V30 END ATOM','M  V30 END ATOM\nM  V30 BEGIN SGROUP'),
               lambda s:s.replace('M  END','M  END\n$$$$'),
               lambda s:s.replace('M  V30 1 C ','M  V30 1 Q '),
               lambda s:s.replace('M  V30 END CTAB','M  V30 BEGIN COLLECTION\nM  V30 END CTAB')]
        atomline=next(l for l in self.record['molText'].splitlines() if l.startswith('M  V30 1 C '))
        for prop in ['RAD=1','HCOUNT=2','ATTCHPT=1','CFG=3','MASS=999','CHG=9','VAL=1 VAL=2','LABEL=evil']:
            cases.append(lambda s,p=prop:s.replace(atomline,atomline+' '+p))
        from rdkit import Chem
        for transform in cases:
            with self.subTest(transform=transform),patch.object(Chem,'MolFromMolBlock',side_effect=AssertionError('native parser must not run')):
                with self.assertRaises(ValueError):recover(request(altered(self.record,transform)))

    def test_contradictory_stereo_is_rejected(self):
        record=make_record(generate(generation_request('C[C@H](O)C(=O)O')))
        self.assertIn('CFG=',record['molText'])
        def flip(text):
            import re
            return re.sub(r'CFG=([12])',lambda m:'CFG='+('2' if m[1]=='1' else '1'),text)
        with self.assertRaises(ValueError):recover(request(altered(record,flip)))

    def test_atom_ids_identity_and_record_integrity(self):
        for field,value in [('sourceAtomIds',['a1']),('molSha256','0'*64),('privatePath','/private')]:
            bad=copy.deepcopy(self.record);bad[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):recover(request(bad))
        bad=copy.deepcopy(self.record);p=json.loads(bad['provenanceJson']);p['canonicalIsomericSmiles']='CCN';bad['provenanceJson']=json.dumps(p);bad['provenanceSha256']=sha(bad['provenanceJson'])
        with self.assertRaises(ValueError):recover(request(bad))

    def test_forged_history_does_not_gain_live_trust(self):
        bad=copy.deepcopy(self.record);p=json.loads(bad['provenanceJson']);p['method']='Historical unverified claim';p['chemicalIdentityRoundtripVerified']=True
        bad['provenanceJson']=json.dumps(p);bad['provenanceSha256']=sha(bad['provenanceJson'])
        result=recover(request(bad))
        self.assertFalse(result['validation']['generationClaimsVerified'])
        self.assertFalse(result['validation']['sourceBindingReverified'])

    def test_request_and_worker_response_bounds(self):
        for field,value in [('recordSha256','0'*64),('clientId','bad'),('jobId','bad'),('expiresAt',True),('recordText','x'*1048577)]:
            bad=request(self.record);bad[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):validate_request(bad)
        supervisor=Mock();supervisor.run.return_value=Mock(returncode=0,stdout='[]')
        with self.assertRaises(ValueError):run_request(request(self.record),supervisor,CancellationToken())
        supervisor.run.return_value=Mock(returncode=0,stdout='{}')
        with self.assertRaises(ValueError):run_request(request(self.record),supervisor,CancellationToken())
        supervisor.run.side_effect=subprocess.TimeoutExpired('worker',15)
        with self.assertRaisesRegex(ValueError,'15-second'):run_request(request(self.record),supervisor,CancellationToken())
        bad=request(self.record);bad['expiresAt']=1
        with self.assertRaisesRegex(ValueError,'expired'):run_request(bad,supervisor,CancellationToken())

    def test_authenticated_route_registry_reuse(self):
        from email.message import Message
        from types import SimpleNamespace
        from nivram.server import Handler, NamingJobs
        supervisor=WorkerSupervisor(max_workers=1)
        try:
            body=json.dumps(request(self.record)).encode()
            handler=object.__new__(Handler)
            handler.server=SimpleNamespace(api_token='token',server_port=32100,coordinate_recovery_jobs=NamingJobs(),worker_supervisor=supervisor)
            handler.path='/api/coordinate-recovery';handler._local=Mock(return_value=True)
            handler.headers=Message()
            for key,value in [('Content-Type','application/json'),('Content-Length',str(len(body))),('X-Nivram-Token','token')]:handler.headers[key]=value
            handler._read_body=Mock(return_value=body);handler._send=Mock();handler._reject_post=Mock()
            handler.do_POST();self.assertEqual(handler._send.call_args.args[0],200)
            self.assertEqual(handler._send.call_args.args[1]['scene']['kind'],'recovered_coordinates')
            self.assertEqual(handler.server.coordinate_recovery_jobs.snapshot()['activeJobs'],0)
            handler.do_POST();self.assertEqual(handler._send.call_args.args[0],409)
        finally:self.assertTrue(supervisor.shutdown())

if __name__=='__main__':unittest.main()
