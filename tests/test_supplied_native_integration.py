"""Cross-layer HTTP/worker/JS-viewer/PNG checks with controlled DOM, not browser."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import threading
import unittest
from PIL import Image
from unittest.mock import patch
from nivram import __version__
from nivram.batch_export import _decode_png
from nivram.image_import import inspect_png_metadata
from nivram.conformer import generate
from nivram.server import LocalServer, Handler
from nivram.png_metadata import validate_coordinates, validate_3d_coordinates, _strict_supplied
from tests.test_conformer import request

ROOT=Path(__file__).resolve().parents[1]


class SuppliedNativeIntegrationTests(unittest.TestCase):
    def test_native_preflight_grammar_matches_js(self):
        text=generate(request('CCO'))['scene']['sourceText']
        line=next(x for x in text.splitlines() if x.startswith('M  V30 1 C '))
        bond=text.split('M  V30 BEGIN BOND\n')[1].splitlines()[0]
        cases=[text,text.replace('\n','\r\n'),text+'\n',text.replace('M  END','M  END\n$$$$')]
        cases += [text.replace(line,line+' '+p) for p in ['CFG=1','CFG=01','CFG=2','CFG=3','VAL=-1','VAL=0','VAL=8','VAL=9','VAL=1 VAL=1','RAD=1','CHG=9','MASS=0','MASS=18 CFG=1']]
        cases += [text.replace(bond,bond+' '+p) for p in ['CFG=1','CFG=01','CFG=3','CFG=2','CFG=0','VAL=1','CFG=1 CFG=3']]
        script="import {readFileSync} from 'node:fs';import {parseSuppliedMol} from './public/modules/supplied-3d.mjs';console.log(JSON.stringify(JSON.parse(readFileSync(0,'utf8')).map(s=>{try{parseSuppliedMol(new TextEncoder().encode(s),{allowNativeDeclarations:true});return true}catch{return false}})))"
        completed=subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(cases),text=True,capture_output=True,cwd=ROOT,timeout=10)
        self.assertEqual(completed.returncode,0,completed.stderr)
        observed=json.loads(completed.stdout)
        for text,js in zip(cases,observed):
            try:_strict_supplied(text,allow_native_declarations=True,allow_radicals=True);python=True
            except ValueError:python=False
            with self.subTest(text=text):self.assertEqual(python,js)

    def test_actual_native_viewer_png_roundtrips_and_races(self):
        sources=[generate(request(s))['scene']['sourceText'] for s in ['C[C@H](O)C(=O)O','[H+]','[SiH3-]']]
        sources.append(sources[0].replace('\n','\r\n'))
        contradiction=re.sub(r'CFG=([12])',lambda m:'CFG='+('2' if m[1]=='1' else '1'),sources[0])
        out=io.BytesIO();Image.new('RGB',(8,5),(245,248,252)).save(out,format='PNG');png=out.getvalue()
        server=LocalServer(('127.0.0.1',0),Handler);server.api_token='test-only-token'
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            data=dict(origin=f'http://127.0.0.1:{server.server_port}',token=server.api_token,version=__version__,sources=sources,contradiction=contradiction,plain=generate(request('CCO'))['scene']['sourceText'],png=base64.b64encode(png).decode())
            completed=subprocess.run(['node','tests/supplied-native-integration-runner.mjs'],input=json.dumps(data),text=True,capture_output=True,cwd=ROOT,timeout=45)
            self.assertEqual(completed.returncode,0,completed.stderr)
            result=json.loads(completed.stdout)
            self.assertGreaterEqual(result['requests'],20)
            self.assertGreaterEqual(result['cancellations'],4)
            for text,record,encoded in zip(sources,result['records'],result['pngs']):
                with self.subTest(text=text[:30]):
                    raw=base64.b64decode(encoded)
                    with patch('rdkit.Chem.MolFromMolBlock',side_effect=AssertionError('metadata validation is structural only')):
                        validated,provenance=validate_3d_coordinates(json.dumps(record).encode());validate_coordinates(json.dumps(record).encode())
                        saved,_=_decode_png(encoded,{'width':8,'marginMode':'pixels','margin':0},threading.Event())
                        self.assertEqual(saved,raw)
                        routed=inspect_png_metadata(raw)
                        self.assertEqual(routed['status'],'three_d_coordinates');self.assertEqual(routed['candidates'],[])
                    self.assertEqual(validated['molText'].encode(),text.encode())
                    self.assertEqual(validated['molSha256'],hashlib.sha256(text.encode()).hexdigest())
                    self.assertFalse(provenance['generated']);self.assertFalse(provenance['chemistryVerified'])
                    self.assertFalse(provenance['validation']['experimentalOriginVerified'])
                    with Image.open(io.BytesIO(raw)) as image:
                        self.assertEqual(image.tobytes(),Image.open(io.BytesIO(png)).tobytes())
                        embedded=json.loads(image.info['nivraM Coordinates']);self.assertEqual(embedded,record)
            # Python validates historical structure only, never authenticates a saved claim.
            validate_3d_coordinates(json.dumps(result['forged']).encode())
            for bad in result['invalidRecords']:
                with self.assertRaises(ValueError):validate_3d_coordinates(json.dumps(bad).encode())
            record=result['records'][0]
            for change in [lambda p:p['validation'].update(generationClaimsVerified=True),lambda p:p['validation'].update(declarationsPreserved=1),lambda p:p['validation'].update(engineDerivedIsomericSmiles=''),lambda p:p.update(extra=True)]:
                bad=copy.deepcopy(record);p=json.loads(bad['provenanceJson']);change(p);bad['provenanceJson']=json.dumps(p);bad['provenanceSha256']=hashlib.sha256(bad['provenanceJson'].encode()).hexdigest()
                with self.assertRaises(ValueError):validate_3d_coordinates(json.dumps(bad).encode())
        finally:
            server.shutdown();thread.join(timeout=3);server.server_close()
        self.assertEqual(server.worker_shutdown_receipt['status'],'confirmed')


if __name__=='__main__':unittest.main()
