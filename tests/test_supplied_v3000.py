"""Plain V3000 interoperability with actual generated exports and PNG metadata."""
import json
import subprocess
import unittest
from nivram.conformer import generate
from nivram.png_metadata import _strict_supplied, validate_coordinates
from test_conformer import request
from test_three_d_png_roundtrip import ThreeDPngCrossLanguageTests, ROOT

class SuppliedV3000Tests(ThreeDPngCrossLanguageTests):
    # Run the inherited integration checks too; new coverage below treats generated
    # bytes only as supplied input, never as authenticated generation provenance.
    def test_plain_generated_exports_reopen_as_supplied(self):
        for smiles in ('O','CCO','CC(=O)Oc1ccccc1C(=O)O','[NH4+]','[13CH4]','[CH3:7][OH:9]','[13CH3][NH3+]','F/C=C/F'):
            with self.subTest(smiles=smiles):
                result=generate(request(smiles));text=result['scene']['sourceText']
                record,p=self.bridge(molText=text)
                self.assertEqual(record['schema'],'nivram-png-coordinates/3')
                self.assertEqual(p['origin'],'supplied_coordinates')
                self.assertFalse(p['generated']);self.assertFalse(p['chemistryVerified'])
                self.assertIsNone(record['sourceAtomIds']);self.assertIsNone(record['sourceContextText'])
                validate_coordinates(json.dumps(record).encode())
    def test_unsupported_generated_declarations_rejected(self):
        for smiles in ('C[C@H](O)C(=O)O',):
            with self.subTest(smiles=smiles):
                text=generate(request(smiles))['scene']['sourceText']
                with self.assertRaises(ValueError):_strict_supplied(text)
                script="import {parseSuppliedMol} from './public/modules/supplied-3d.mjs';import {readFileSync} from 'node:fs';try{parseSuppliedMol(new Uint8Array(readFileSync(0)));process.exit(1)}catch{process.exit(0)}"
                self.assertEqual(subprocess.run(['node','--input-type=module','-e',script],input=text,text=True,cwd=ROOT).returncode,0)

class SuppliedV3000GrammarParityTests(unittest.TestCase):
    def test_plain_grammar_rejections_match_js(self):
        raw='\n'.join(['plain',' '*20+'3D','','  0  0  0  0  0  0  0  0  0  0999 V3000','M  V30 BEGIN CTAB','M  V30 COUNTS 1 0 0 0 0','M  V30 BEGIN ATOM','M  V30 1 O -0.0000 0.1 0.2 0','M  V30 END ATOM','M  V30 END CTAB','M  END'])
        cases=[(raw,True),(raw+'\n',True),(raw.replace('\n','\r\n')+'\r\n',True),(raw+'\n\n',False),(raw+'\n$$$$',False)]
        for old,new in [('0.2 0','0.2 00'),('0.2 0','0.2 0 CFG=1'),('0.2 0','NaN 0'),('0.2 0','10001 0'),('1 O','1 *'),('3D','2D'),('COUNTS 1','COUNTS 129')]:cases.append((raw.replace(old,new),False))
        script="import {parseSuppliedMol} from './public/modules/supplied-3d.mjs';import {readFileSync} from 'node:fs';console.log(JSON.stringify(JSON.parse(readFileSync(0,'utf8')).map(s=>{try{parseSuppliedMol(new TextEncoder().encode(s));return true}catch{return false}})))"
        for tail in ['7','999999 CHG=-8 MASS=400','0 MASS=18 CHG=8','0 CHG=0','0 RAD=1']:cases.append((raw.replace('0.2 0','0.2 '+tail),True))
        for tail in ['1000000','01','-1','0 CHG=9','0 CHG=-9','0 MASS=0','0 MASS=401','0 CHG=1 CHG=1','0 MASS=18 MASS=18','0 VAL=1','0 CFG=2','0 CHG=1.2']:cases.append((raw.replace('0.2 0','0.2 '+tail),False))
        js=json.loads(subprocess.run(['node','--input-type=module','-e',script],input=json.dumps([t for t,_ in cases]),text=True,capture_output=True,cwd=ROOT,check=True).stdout)
        for (text,expected),actual_js in zip(cases,js):
            try:_strict_supplied(text,allow_radicals=True);actual_py=True
            except ValueError:actual_py=False
            self.assertEqual((actual_py,actual_js),(expected,expected))
