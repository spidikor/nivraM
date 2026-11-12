"""Real cross-language PNG metadata roundtrip, not browser/Windows qualification."""
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import time
import unittest

from PIL import Image
from rdkit import Chem
from nivram import __version__
from nivram.chemistry import import_structure
from nivram.conformer import run_request as generate_request
from nivram.worker_processes import WorkerSupervisor, CancellationToken

ROOT = Path(__file__).resolve().parents[1]
NODE = r'''
import {webcrypto} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {createThreeDRecord} from './public/modules/three-d-record.mjs';
import {stampPng,extractThreeDRecord} from './public/modules/png-metadata.mjs';
import {parseSuppliedMol,suppliedProvenance,projectSupplied} from './public/modules/supplied-3d.mjs';
globalThis.crypto??=webcrypto;
const data=JSON.parse(readFileSync(0,'utf8'));
if(!data.provenance){const m=parseSuppliedMol(new TextEncoder().encode(data.molText));data.provenance=await suppliedProvenance(m);const before=m.sourceText;projectSupplied(m,{yaw:1.2,pitch:.7,zoom:1.8});if(before!==m.sourceText)throw Error('Camera mutated MOL');}
const model={sourceText:data.molText,atoms:data.sourceAtomIds?.map(sourceId=>({sourceId}))};
const record=await createThreeDRecord({...data,model},data.version);
const source=new Blob([Buffer.from(data.png,'base64')],{type:'image/png'});
const output=await stampPng(source,{coordinateRecord:record,version:data.version});
const extracted=await extractThreeDRecord(output);
const stripped=await stampPng(source,{coordinateRecord:record,version:data.version,removeChemistryMetadata:true});
process.stdout.write(JSON.stringify({stripped:Buffer.from(await stripped.arrayBuffer()).toString('base64'),record:extracted.record??extracted,png:Buffer.from(await output.arrayBuffer()).toString('base64')}));
'''


def supplied(newline='\n', ending=''):
    def atom(x,y,z,element):
        return ''.join(str(v).rjust(10) for v in (x,y,z))+' '+element.ljust(3)+' 0  0  0  0  0  0  0  0  0  0  0  0'
    return newline.join(['exact supplied coordinates',' '*20+'3D','','  3  2  0  0  0  0            999 V2000',atom('-0.0000','+0.0000','0.0000','O'),atom('0.7001','0.5123','-0.2020','H'),atom('-0.7001','0.5123','+0.2020','H'),'  1  2  1  0  0  0  0','  1  3  1  0  0  0  0','M  END'])+ending


@unittest.skipUnless(shutil.which('node'), 'Node required for actual cross-language bridge')
class ThreeDPngCrossLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out=io.BytesIO(); Image.new('RGB',(8,5),(245,248,252)).save(out,format='PNG');cls.png=out.getvalue()

    def bridge(self, **data):
        data.update(png=base64.b64encode(self.png).decode(),version=__version__)
        result=subprocess.run(['node','--input-type=module','-e',NODE],input=json.dumps(data),text=True,capture_output=True,cwd=ROOT,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        output=json.loads(result.stdout); encoded=base64.b64decode(output['png'])
        stripped=base64.b64decode(output['stripped'])
        with Image.open(io.BytesIO(stripped)) as image:
            self.assertEqual(image.tobytes(),Image.open(io.BytesIO(self.png)).tobytes())
            self.assertEqual(image.info,{'Software':'nivraM '+__version__})
        from nivram.image_import import inspect_png_metadata
        self.assertEqual(inspect_png_metadata(stripped)['candidates'],[])
        with Image.open(io.BytesIO(encoded)) as image:
            self.assertEqual(image.size,(8,5));self.assertEqual(image.tobytes(),Image.open(io.BytesIO(self.png)).tobytes())
            self.assertEqual(image.info['Software'],'nivraM '+__version__)
        # All original PNG chunks, including compressed image data, are byte-identical.
        def chunks(raw):
            offset=8;result=[]
            while offset<len(raw):
                size=int.from_bytes(raw[offset:offset+4],'big');result.append(raw[offset:offset+size+12]);offset+=size+12
            return result
        output_chunks=chunks(encoded)
        self.assertEqual([c for c in chunks(stripped) if c[4:8] != b'tEXt'],chunks(self.png))
        self.assertEqual([c for c in output_chunks if c[4:8] not in (b'tEXt',b'iTXt')],chunks(self.png))
        from nivram.png_metadata import validate_3d_coordinates
        record,provenance=validate_3d_coordinates(json.dumps(output['record']).encode())
        self.assertEqual(record['molText'],data['molText'])
        self.assertEqual(record['molSha256'],hashlib.sha256(data['molText'].encode()).hexdigest())
        return record,provenance

    def test_supplied_exact_line_endings_signed_zero_and_whitespace(self):
        for nl,ending in [('\n',''),('\n','\n  \t\n'),('\r\n','\r\n')]:
            with self.subTest(newline=repr(nl),ending=repr(ending)):
                raw=supplied(nl,ending);record,provenance=self.bridge(molText=raw)
                self.assertEqual(record['molText'].encode(),raw.encode())
                self.assertFalse(provenance['generated']);self.assertFalse(provenance['chemistryVerified'])

    def test_real_generated_identity_and_artifact_coordinate_precision(self):
        supervisor=WorkerSupervisor(max_workers=1)
        try:
            for index,smiles in enumerate(['C','O','[NH4+]','Cl','CCO','C[C@H](O)C(=O)O','F/C=C/F','[13CH3][NH3+]','c1ccccc1','[2H]OC','[CH3:7][OH:9]']):
                with self.subTest(smiles=smiles):
                    graph=import_structure(smiles);graph_text=json.dumps(graph,separators=(',',':'))
                    request=dict(graphText=graph_text,sourceRevision=17,sourceHash=hashlib.sha256(graph_text.encode()).hexdigest(),sourceContextText='null',sourceContextHash=hashlib.sha256(b'null').hexdigest(),clientId='a'*32,jobId=f'{index+1:032x}',expiresAt=int(time.time()*1000)+30000)
                    generated=generate_request(request,supervisor,CancellationToken())
                    record,provenance=self.bridge(molText=generated['scene']['sourceText'],provenance=generated['provenance'],sourceContextText='null',sourceAtomIds=[a['sourceId'] for a in generated['scene']['atoms']])
                    mol=Chem.MolFromMolBlock(record['molText'],removeHs=False,strictParsing=True)
                    self.assertEqual(Chem.MolToSmiles(mol,isomericSmiles=True),provenance['canonicalIsomericSmiles'])
                    for atom in generated['scene']['atoms']:
                        point=mol.GetConformer().GetAtomPosition(atom['id']-1)
                        self.assertEqual((point.x,point.y,point.z),(atom['x'],atom['y'],atom['z']))
                    self.recover_record(record,generated,supervisor,index)
        finally:
            self.assertTrue(supervisor.shutdown())

    def recover_record(self,record,generated,supervisor,index):
        # Invoke the explicit parse-only disposable-worker contract.
        from nivram.coordinate_recovery import run_request
        text=json.dumps(record,separators=(',',':'))
        request=dict(recordText=text,recordSha256=hashlib.sha256(text.encode()).hexdigest(),clientId='c'*32,jobId=f'{index+100:032x}',expiresAt=int(time.time()*1000)+30000)
        recovered=run_request(request,supervisor,CancellationToken())
        self.assertEqual(recovered['scene']['sourceText'],generated['scene']['sourceText'])
        self.assertEqual(recovered['scene']['atoms'],generated['scene']['atoms'])
        self.assertEqual(recovered['scene']['bonds'],generated['scene']['bonds'])
        self.assertNotEqual(recovered['scene']['kind'],'generated_coordinates')

if __name__=='__main__':unittest.main()
