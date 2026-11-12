"""Actual JS/Python acceptance parity; no browser or external service."""
import copy
import json
from pathlib import Path
import subprocess
import unittest
from nivram.png_metadata import validate_3d_coordinates
from nivram.conformer import generate
from test_conformer import request
from test_coordinate_recovery import make_record, sha

ROOT=Path(__file__).resolve().parents[1]
NODE="""import {readFileSync} from 'node:fs';import {validateThreeDRecord} from './public/modules/three-d-record.mjs';try {await validateThreeDRecord(readFileSync(0,'utf8'));console.log(true)} catch {console.log(false)}"""

class ThreeDRecordParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.record=make_record(generate(request()))
    def assert_parity(self,r,expected):
        raw=json.dumps(r,ensure_ascii=False,separators=(',',':'))
        try:validate_3d_coordinates(raw.encode());actual=True
        except ValueError:actual=False
        result=subprocess.run(['node','--input-type=module','-e',NODE],input=raw,text=True,capture_output=True,cwd=ROOT,timeout=10,check=True)
        self.assertEqual((actual,json.loads(result.stdout)),(expected,expected))
    def mutate_provenance(self,change):
        r=copy.deepcopy(self.record);p=json.loads(r['provenanceJson']);change(p);r['provenanceJson']=json.dumps(p,ensure_ascii=False);r['provenanceSha256']=sha(r['provenanceJson']);return r
    def test_integer_json_number_semantics(self):
        for key in ('dimension','molBytes'):
            r=copy.deepcopy(self.record);r[key]=float(r[key]);self.assert_parity(r,True)
            r[key]=True;self.assert_parity(r,False)
        for key in ('seed','maxIterations','threads','sourceRevision'):
            self.assert_parity(self.mutate_provenance(lambda p:p.update({key:float(p[key])})),True)
            self.assert_parity(self.mutate_provenance(lambda p:p.update({key:True})),False)
    def test_utf16_string_limits(self):
        for n,expected in ((1024,True),(1025,False),(1500,False)):
            self.assert_parity(self.mutate_provenance(lambda p:p.update(limitations=['😀'*n])),expected)
        for n,expected in ((40,True),(41,False)):
            r=copy.deepcopy(self.record);r['sourceAtomIds'][0]='😀'*n;self.assert_parity(r,expected)
    def context_record(self,recorded_value,hashed_value=1):
        context=dict(schema='nivram-3d-source-context/1',currentGraphRelationship='unverified; prior image review does not verify the current graph',imageImport=dict(kind='png-optical-reconstruction',sourceSha256='a'*64,recognition=dict(confidence=hashed_value)))
        r=copy.deepcopy(self.record);r['sourceContextText']=json.dumps(context);r['sourceContextSha256']=sha(r['sourceContextText']);p=json.loads(r['provenanceJson']);p['sourceContext']=copy.deepcopy(context);p['sourceContext']['imageImport']['recognition']['confidence']=recorded_value;p['sourceContextSha256']=r['sourceContextSha256'];r['provenanceJson']=json.dumps(p);r['provenanceSha256']=sha(r['provenanceJson']);return r
    def test_context_equality_never_coerces_boolean_number(self):
        self.assert_parity(self.context_record(True,1),False)
        self.assert_parity(self.context_record(False,0),False)
        self.assert_parity(self.context_record(1.0,1),True)
        self.assert_parity(self.context_record(0.0,0),True)
        self.assert_parity(self.context_record(True,True),False)
    def test_context_utf16_and_integral_fields(self):
        for width,warning,expected in ((2.0,'😀'*512,True),(True,'ok',False),(2,'😀'*513,False)):
            r=self.context_record(1);c=json.loads(r['sourceContextText']);c['imageImport']['warnings']=[warning];c['imageImport']['reviewedRegion']={'width':width};r['sourceContextText']=json.dumps(c);r['sourceContextSha256']=sha(r['sourceContextText']);p=json.loads(r['provenanceJson']);p['sourceContext']=c;p['sourceContextSha256']=r['sourceContextSha256'];r['provenanceJson']=json.dumps(p);r['provenanceSha256']=sha(r['provenanceJson']);self.assert_parity(r,expected)
