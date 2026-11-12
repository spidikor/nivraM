"""Explicit V2000 declaration subset, shared JS/Python fixtures and real PNG bridge."""
import json
from pathlib import Path
import unittest
from rdkit import Chem, rdBase
from nivram.png_metadata import _strict_supplied
from tests.test_three_d_png_roundtrip import ThreeDPngCrossLanguageTests

FIXTURES=json.loads((Path(__file__).parent/'fixtures/supplied-v2000-properties.json').read_text())

class V2000PropertyTests(unittest.TestCase):
    def test_shared_positive_and_negative_grammar(self):
        for f in FIXTURES['valid']:
            for nl in ('\n','\r\n'):
                with self.subTest(name=f['name'],newline=repr(nl)):
                    self.assertEqual(_strict_supplied(f['text'].replace('\n',nl)),(2,0))
        for f in FIXTURES['invalid']:
            with self.subTest(name=f['name']):
                with self.assertRaises(ValueError):_strict_supplied(f['text'])

    def test_actual_rdkit_effective_declarations_without_sanitization(self):
        self.assertEqual(rdBase.rdkitVersion,'2025.09.4')
        for f in FIXTURES['valid']:
            with self.subTest(name=f['name']):
                m=Chem.MolFromMolBlock(f['text'],sanitize=False,removeHs=False,strictParsing=True)
                self.assertIsNotNone(m)
                self.assertEqual([a.GetFormalCharge() for a in m.GetAtoms()],f['charges'])
                self.assertEqual([a.GetIsotope() for a in m.GetAtoms()],f['isotopes'])

    def test_real_png_writer_reopen_exact_bytes(self):
        ThreeDPngCrossLanguageTests.setUpClass()
        bridge=ThreeDPngCrossLanguageTests()
        for f in FIXTURES['valid']:
            for nl in ('\n','\r\n'):
                with self.subTest(name=f['name'],newline=repr(nl)):
                    text=f['text'].replace('\n',nl)
                    record,p=bridge.bridge(molText=text)
                    self.assertEqual(record['molText'].encode(),text.encode())
                    self.assertFalse(p['chemistryVerified']);self.assertFalse(p['generated'])
