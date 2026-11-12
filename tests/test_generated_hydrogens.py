"""New attachment integration and adversarial checks; no browser or sockets."""
import copy
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from rdkit import Chem
from rdkit.Chem import rdDistGeom
from nivram.conformer import generate
from nivram.coordinate_recovery import recover, run_request
from nivram.generated_hydrogens import validate_attachment
from nivram.png_metadata import validate_3d_coordinates
from nivram.worker_processes import WorkerSupervisor, CancellationToken
from tests.test_conformer import request as generation_request
from tests.test_coordinate_recovery import make_record, request, sha, altered

ROOT=Path(__file__).resolve().parents[1]


def record_of(result):
    r=make_record(result)
    r['schema']='nivram-png-coordinates/6'
    r['generatedHydrogensJson']=json.dumps(result['scene']['generatedHydrogens'],separators=(',',':'))
    r['generatedHydrogensSha256']=sha(r['generatedHydrogensJson'])
    return r


def change_attachment(r, change):
    r=copy.deepcopy(r);h=json.loads(r['generatedHydrogensJson']);change(h)
    r['generatedHydrogensJson']=json.dumps(h,separators=(',',':'));r['generatedHydrogensSha256']=sha(r['generatedHydrogensJson']);return r


class GeneratedHydrogensTests(unittest.TestCase):
    def test_supported_roundtrip_existing_h_and_no_embedding_on_recovery(self):
        for smiles in ['CCO','c1ccccc1','[13CH3][NH3+]','C[C@H](O)C(=O)O','[2H]OC','[H:7]OC','FC(F)(F)F','[H+]']:
            with self.subTest(smiles=smiles):
                data=generation_request(smiles);before=copy.deepcopy(data);result=generate(data)
                self.assertEqual(data,before)
                scene=result['scene'];h=scene['generatedHydrogens'];mol=Chem.MolFromMolBlock(scene['sourceText'],removeHs=False)
                self.assertEqual(len(h['hydrogens']),Chem.AddHs(mol).GetNumAtoms()-mol.GetNumAtoms())
                if smiles=='FC(F)(F)F':self.assertEqual(h['hydrogens'],[])
                for atom in scene['atoms']:
                    pos=mol.GetConformer().GetAtomPosition(atom['id']-1)
                    self.assertEqual((atom['x'],atom['y'],atom['z']),(pos.x,pos.y,pos.z))
                with patch.object(rdDistGeom,'EmbedMolecule',side_effect=AssertionError('no embedding')):
                    recovered=recover(request(record_of(result)))
                self.assertEqual(recovered['scene'],dict(scene,kind='recovered_coordinates'))
                self.assertFalse(recovered['validation']['generationClaimsVerified'])
                self.assertFalse(recovered['validation']['sourceBindingReverified'])
                legacy=recover(request(make_record(result)))
                self.assertNotIn('generatedHydrogens',legacy['scene'])

    def test_exact_embedding_positions_are_retained(self):
        real=rdDistGeom.EmbedMolecule;captured=[]
        def embed(mol,params):
            status=real(mol,params);captured.append(Chem.Mol(mol));return status
        with patch.object(rdDistGeom,'EmbedMolecule',side_effect=embed):result=generate(generation_request())
        count=len(result['scene']['atoms'])
        for i,h in enumerate(result['scene']['generatedHydrogens']['hydrogens'],count):
            p=captured[0].GetConformer().GetAtomPosition(i)
            self.assertEqual((h['x'],h['y'],h['z']),tuple(round(x,6) for x in (p.x,p.y,p.z)))

    def test_structural_parity_and_recovery_rejection(self):
        r=record_of(generate(generation_request()))
        node="import {readFileSync} from 'node:fs';import {validateThreeDRecord} from './public/modules/three-d-record.mjs';try{await validateThreeDRecord(readFileSync(0,'utf8'));console.log(true)}catch{console.log(false)}"
        changes=[(lambda h:h.update(schema='other'),False),(lambda h:h.update(canonicalMolSha256='0'*64),False),
            (lambda h:h['hydrogens'][0].update(parentId=True),False),(lambda h:h['hydrogens'][0].update(parentId='1'),False),
            (lambda h:h['hydrogens'][0].update(parentId=1.0),True),(lambda h:h['hydrogens'][0].update(parentId=0),False),
            (lambda h:h['hydrogens'][0].update(x=True),False),(lambda h:h['hydrogens'][0].update(x='1'),False),
            (lambda h:h['hydrogens'][0].update(x=10001),False),(lambda h:h['hydrogens'][0].update(id='a1'),False),
            (lambda h:h['hydrogens'][0].update(sourceId='a1'),False),(lambda h:h.update(hydrogens=h['hydrogens']*100),False)]
        for change,expected in changes:
            bad=change_attachment(r,change);raw=json.dumps(bad)
            try:validate_3d_coordinates(raw.encode());actual=True
            except ValueError:actual=False
            completed=subprocess.run(['node','--input-type=module','-e',node],input=raw,text=True,capture_output=True,cwd=ROOT,check=True)
            self.assertEqual((actual,json.loads(completed.stdout)),(expected,expected))
            if not expected:
                with self.assertRaises(ValueError):recover(request(bad))
        for change in [lambda h:h['hydrogens'].pop(),lambda h:h['hydrogens'][0].update(parentId=3)]:
            with self.assertRaises(ValueError):recover(request(change_attachment(r,change)))
        for key in ['generatedHydrogensSha256','molSha256']:
            bad=copy.deepcopy(r);bad[key]='0'*64
            with self.assertRaises(ValueError):recover(request(bad))
        bad=copy.deepcopy(r);bad['sourceAtomIds'].pop()
        with self.assertRaises(ValueError):recover(request(bad))
        for origin,schema in [('supplied_coordinates','nivram-png-coordinates/6'),('generated_coordinates','nivram-png-coordinates/2')]:
            bad=copy.deepcopy(r);bad.update(recordedOrigin=origin,schema=schema)
            with self.assertRaises(ValueError):validate_3d_coordinates(json.dumps(bad).encode())
        # Changing canonical coordinates without replacing attachment's canonical hash rejects.
        bad=altered(r,lambda s:s.replace('M  V30 1 C ', 'M  V30 1 C ',1)+'\n')
        with self.assertRaises(ValueError):recover(request(bad))
        for x in [float('nan'),float('inf')]:
            h=json.loads(r['generatedHydrogensJson']);h['hydrogens'][0]['x']=x
            with self.assertRaises(ValueError):validate_attachment(h,r['molText'],3)
        bad=copy.deepcopy(r);bad['generatedHydrogensJson']=r['generatedHydrogensJson'].replace('"parentId":1','"parentId":1,"parentId":1',1);bad['generatedHydrogensSha256']=sha(bad['generatedHydrogensJson'])
        with self.assertRaises(ValueError):recover(request(bad))

    def test_schema6_routes_away_from_2d_adoption(self):
        from nivram.image_import import inspect_png_metadata
        from nivram.png_metadata import KEYWORD
        from tests.test_image_import import png
        record=record_of(generate(generation_request()))
        image=png([(b'iTXt',KEYWORD.encode()+b'\0\0\0\0\0'+json.dumps(record).encode())])
        result=inspect_png_metadata(image)
        self.assertEqual(result['status'],'three_d_coordinates')
        self.assertEqual(result['candidates'],[])
        self.assertEqual(result['recognition']['status'],'not_run')

    def test_real_worker(self):
        result=generate(generation_request());r=record_of(result);supervisor=WorkerSupervisor(max_workers=1)
        try:self.assertEqual(run_request(request(r),supervisor,CancellationToken())['scene']['generatedHydrogens'],result['scene']['generatedHydrogens'])
        finally:self.assertTrue(supervisor.shutdown())

if __name__=='__main__':unittest.main()
