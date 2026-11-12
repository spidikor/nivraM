"""Actual JS/Python schema5 parity and PNG bridge; no browser qualification."""
import hashlib,json,os,subprocess,tempfile,unittest
from pathlib import Path
from nivram.png_metadata import _strict_supplied,validate_coordinates
from nivram.coordinate_recovery import _v3000
from tests.test_three_d_png_roundtrip import ThreeDPngCrossLanguageTests, ROOT
class RadicalDeclarations(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  with tempfile.TemporaryDirectory() as td:
   file=Path(td)/'cases.json'
   subprocess.run(['node','tests/supplied-radicals.test.mjs'],cwd=ROOT,env={**os.environ,'RADICAL_FIXTURES':str(file)},check=True,capture_output=True)
   cls.cases=json.loads(file.read_text())
 def test_grammar_and_native_boundary(self):
  for text in self.cases['valid']:
   self.assertEqual(_strict_supplied(text,allow_radicals=True),(2,1))
   with self.assertRaises(ValueError):_strict_supplied(text)
   if 'V3000' in text:
    with self.assertRaises(ValueError):_v3000(text)
  for text in self.cases['invalid']:
   with self.assertRaises(ValueError,msg=text):_strict_supplied(text,allow_radicals=True)
 def test_metadata_and_tampering(self):
  for record in self.cases['records']:
   validate_coordinates(json.dumps(record).encode())
   for patch in [{'schema':'nivram-png-coordinates/2'},{'schema':'nivram-png-coordinates/3'},{'schema':'nivram-png-coordinates/4'},{'molText':record['molText']+' '}]:
    with self.assertRaises(ValueError):validate_coordinates(json.dumps({**record,**patch}).encode())
 def test_real_png_roundtrip(self):
  ThreeDPngCrossLanguageTests.setUpClass();bridge=ThreeDPngCrossLanguageTests()
  for text in self.cases['valid']:
   record,p=bridge.bridge(molText=text)
   self.assertEqual(record['schema'],'nivram-png-coordinates/5');self.assertEqual(record['molText'],text)
   validate_coordinates(json.dumps(record).encode())

 def test_radical_presence_comes_from_parsed_records(self):
  records=json.loads((ROOT/'tests/fixtures/supplied-radical-presence-records.json').read_text())
  for case in records:
   with self.subTest(name=case['name']):
    record=case['record'];validate_coordinates(json.dumps(record).encode())
    self.assertEqual(_strict_supplied(record['molText'],allow_radicals=True,include_radical_presence=True)[2],case['name']=='leading_space_atom')
    if case['name']=='legacy_title':
     p=json.loads(record['provenanceJson']);p['schema']='nivram-supplied-3d-view/3'
     provenance=json.dumps(p);forged={**record,'schema':'nivram-png-coordinates/5','provenanceJson':provenance,'provenanceSha256':hashlib.sha256(provenance.encode()).hexdigest()}
     with self.assertRaises(ValueError):validate_coordinates(json.dumps(forged).encode())
