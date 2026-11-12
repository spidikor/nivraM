"""Raw source-declaration preservation, not physical spin-state prediction."""
from tests.import_binding_fixtures import prepare_fixture
import copy
import unittest
from unittest.mock import patch
from rdkit import Chem
from nivram.chemistry import ChemistryError, import_structure, export_mol
from nivram.import_limits import check_mol
from nivram.optical_import import optical_candidate, MODEL_SHA256

V2 = '''Methylene
     independent    2D

  1  0  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  2  0  0  0  0  0  0
M  RAD  1   1   1
M  END
'''
V3 = '''Methylene
     independent    2D

  0  0  0  0  0  0  0  0  0  0999 V3000
M  V30 BEGIN CTAB
M  V30 COUNTS 1 0 0 0 0
M  V30 BEGIN ATOM
M  V30 1 C 0 0 0 0 RAD=1 VAL=2
M  V30 END ATOM
M  V30 END CTAB
M  END
'''
def payload(block, smiles='[CH2]'):
    return dict(schemaVersion=1, method='MolScribe', modelSha256=MODEL_SHA256,
                inputSha256='a'*64, smiles=smiles, molfile=block, confidence=.8)

class MolRadicalDeclarationTests(unittest.TestCase):
    def test_raw_singlets_reject_before_native_parse(self):
        for block in (V2, V3, V3.replace('RAD=1', 'RA-\nM  V30 D=1'),
                      V3.replace('RAD=1', 'rad=01'), V3.replace('RAD=1', '"RAD=1"'),
                      V3.replace('RAD=1', 'X="a"RAD=1')):
            with self.subTest(block=block):
                with patch('nivram.chemistry.Chem.MolFromMolBlock') as parser:
                    with self.assertRaisesRegex(ChemistryError, 'singlet'):
                        import_structure(block)
                    parser.assert_not_called()

    def test_real_optical_singlets_reject_and_input_unchanged(self):
        for block in (V2, V3):
            raw=payload(block); before=copy.deepcopy(raw)
            with self.assertRaisesRegex(ValueError, 'singlet'):
                optical_candidate(raw)
            self.assertEqual(raw,before)

    def test_triplet_doublet_and_ordinary_controls(self):
        for smiles in ('C', '[CH3]', '[CH2]', '[13CH3]', '[H:7]C', 'C[NH3+]', 'F[C@](Cl)(Br)I', 'F/C=C/F'):
            params=Chem.SmilesParserParams();params.removeHs=False
            mol=Chem.MolFromSmiles(smiles,params)
            for v3 in (False,True):
                block=Chem.MolToMolBlock(mol,forceV3000=v3)
                graph=import_structure(block)
                restored=Chem.MolFromMolBlock(export_mol(graph),removeHs=False)
                self.assertEqual(Chem.MolToSmiles(mol),Chem.MolToSmiles(restored))
                self.assertEqual(optical_candidate(payload(block,smiles))['status'],'supported')

    def test_malformed_and_duplicate_radicals_reject(self):
        for block in (V2.replace('M  RAD  1   1   1','M  RAD  2   1   3   1   3'),
                      V2.replace('M  RAD  1   1   1','M  RAD  1   1   3\nM  RAD  1   1   3'),
                      V2.replace('M  RAD  1   1   1','M  RAD  2   1   3'),
                      V2.replace('M  RAD  1   1   1','M  RAD  1   1   9'),
                      V3.replace('RAD=1','RAD=3 RAD=3'),
                      V3.replace('RAD=1','RAD=3 RAD=1'),
                      V3.replace('RAD=1','RAD=1 RAD=3'),
                      V3.replace('RAD=1','RAD=1-'),
                      V3.replace('RAD=1','RAD="1"')):
            with self.subTest(block=block),self.assertRaises(ValueError):
                check_mol(block,512,1024)

    def test_physical_line_separator_mismatch_fails_closed(self):
        for separator in ('\v', '\f', '\r', '\x1c', '\x1d', '\x1e', '\x85', '\u2028', '\u2029'):
            block = V3.replace('RAD=1', 'X="a"' + separator + 'RAD=0 RAD=1')
            with self.subTest(separator=repr(separator)):
                with patch('nivram.chemistry.Chem.MolFromMolBlock') as parser:
                    with self.assertRaisesRegex(ChemistryError, 'line separator'):
                        import_structure(block)
                    parser.assert_not_called()
                with self.assertRaisesRegex(ValueError, 'line separator'):
                    optical_candidate(payload(block))
        for block in (V2, V3):
            with self.assertRaisesRegex(ChemistryError, 'singlet'):
                import_structure(block.replace('\n', '\r\n'))
        for block in (V2.replace('1   1   1', '1   1   3'), V3.replace('RAD=1', 'RAD=3')):
            self.assertIn('RAD=3', export_mol(import_structure(block.replace('\n', '\r\n'))))

    def test_preparation_and_metadata_report_specific_rejection(self):
        from nivram.import_preparation import _prepare, SCHEMA, ImportPreparationError
        from nivram.image_import import inspect_png_metadata
        from test_image_import import png
        for block in (V2, V3):
            with self.assertRaises(ImportPreparationError) as caught:
                prepare_fixture(dict(schema=SCHEMA, mode='import', source=block, style={}))
            self.assertEqual(caught.exception.code, 'unsupported_singlet_radical')
            result = inspect_png_metadata(png([(b'tEXt', b'MOL rdkit 2025.09.4\0' + block.encode())]))
            self.assertFalse(result['candidates'])
            self.assertIn('singlet', str(result['unsupportedSources']))

    def test_non_declarations_are_not_singlets(self):
        v2=V2.replace('M  RAD  1   1   1\n','')
        v3=V3.replace(' RAD=1','')
        for block in (v2.replace('Methylene','RAD=1 M  RAD  1   1   1'),
                      v2.replace('\n\n','\nRAD=1\n',1),
                      v2.replace('M  END','G    1\nM  RAD  1   1   1\nM  END'),
                      v2.replace('M  END','S  SKP  1\nM  RAD  1   1   1\nM  END'),
                      v2.replace('M  END','A    1\nM  RAD  1   1   1\nM  END'),
                      v3.replace('VAL=2','VAL=2 LABEL="unrelated RAD=1"'),
                      v3.replace('VAL=2','VAL=2 LABEL=(2 RAD=1 other)'),
                      v3.replace('Methylene','RAD=1')):
            with self.subTest(block=block):check_mol(block,512,1024)

if __name__=='__main__':unittest.main()
