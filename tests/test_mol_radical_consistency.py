"""Positive raw radical constraints, not multiplicity or full MOL fidelity."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from rdkit import Chem
from nivram.chemistry import import_structure, export_mol, InconsistentMolRadicalError
from nivram.import_limits import check_mol
from nivram.optical_import import optical_candidate, OpticalImportError
from test_mol_radical_declarations import V2, V3, payload

FIXTURES = json.loads((Path(__file__).parent/'fixtures/mol-radical-consistency.json').read_text())
MISMATCHES = {
    'v2000_rad2_val2', 'v2000_rad3_val3', 'v2000_rad2_val4', 'v2000_rad3_val4',
    'v3000_rad2_val2', 'v3000_rad3_val3', 'v3000_rad2_val4', 'v3000_rad3_val4',
    'code4_val2_bare', 'code4_val3_rad3', 'code4_val4_bare', 'code4_val4_rad3',
    'code4_precedence_chg_then_rad2', 'code4_precedence_rad2_then_chg',
    'code4_twoatom_bare', 'v3000_nonsequential_explicitH',
    'code4_val0_bare',
}

class RadicalConsistencyTests(unittest.TestCase):
    def test_frozen_assessment_cases(self):
        for c in FIXTURES:
            with self.subTest(case=c['name']):
                raw = payload(c['block'], c['optical_smiles']); before = copy.deepcopy(raw)
                if c['name'] in MISMATCHES:
                    with self.assertRaises(InconsistentMolRadicalError): import_structure(c['block'])
                    with self.assertRaises(OpticalImportError) as caught: optical_candidate(raw)
                    self.assertEqual(caught.exception.code, 'inconsistent_mol_radical')
                else:
                    graph = import_structure(c['block'])
                    if c['name'] in ('code4_val0_rad3', 'code4_precedence_chgplus'):
                        # Assessment fixtures deliberately have conflicting companion SMILES.
                        with self.assertRaises(OpticalImportError) as caught: optical_candidate(raw)
                        self.assertEqual(caught.exception.code, 'inconsistent_prediction')
                    else:
                        self.assertEqual(optical_candidate(raw)['status'], 'supported')
                    native = Chem.MolFromMolBlock(c['block'], sanitize=True, removeHs=False)
                    restored = Chem.MolFromMolBlock(export_mol(graph), sanitize=True, removeHs=False)
                    self.assertEqual(Chem.MolToSmiles(restored), Chem.MolToSmiles(native))
                    if 'expected_atoms' in c:
                        self.assertEqual([(a['radicals'],a['mapNum'],a['isotope']) for a in graph['atoms']],
                                         [(a['rad'],a['map'],a['isotope']) for a in c['expected_atoms']])
                self.assertEqual(raw, before)

    def test_preflight_returns_retained_record_indices(self):
        c = next(c for c in FIXTURES if c['name']=='v3000_nonsequential_explicitH')
        self.assertEqual(check_mol(c['block'],512,1024), {1:1})
        for label in ('RAD=2', '"RAD=2"', 'rad=02', 'RA-\nM  V30 D=2', 'X="a"RAD=2'):
            self.assertEqual(check_mol(V3.replace('RAD=1',label),512,1024), {0:1})

    def test_property_presence_and_skip_semantics(self):
        c=next(c for c in FIXTURES if c['name']=='code4_val2_bare')
        b=c['block']
        for prop in ('M  CHG  0','M  RAD  0','M  CHG  1   1   0','M  RAD  1   1   0'):
            self.assertEqual(check_mol(b.replace('M  END',prop+'\nM  END'),512,1024), {})
        for prop in ('M  ISO  1   1  13', 'A    1\nM  CHG  0', 'G    1\nM  RAD  0', 'S  SKP  1\nM  CHG  0'):
            self.assertEqual(check_mol(b.replace('M  END',prop+'\nM  END'),512,1024), {0:1})
        self.assertEqual(check_mol(b.replace('Synthetic','M  CHG  0'),512,1024), {0:1})

    def test_legacy_numeric_spelling_and_duplicate_atom_labels(self):
        c = next(c for c in FIXTURES if c['name']=='code4_val2_bare')
        for field in ('  4', ' 04', '004', ' +4'):
            lines=c['block'].splitlines();lines[4]=lines[4][:36]+field+lines[4][39:]
            block='\n'.join(lines)+'\n'
            self.assertEqual(check_mol(block,512,1024), {0:1})
            with self.assertRaises(InconsistentMolRadicalError):import_structure(block)
        c = next(c for c in FIXTURES if c['name']=='v3000_nonsequential_explicitH')
        for label in ('99', '0'):
            with self.assertRaisesRegex(ValueError, 'atom label'):
                check_mol(c['block'].replace('7 C',label+' C'),512,1024)

    def test_original_native_lifecycle_and_no_hydrogen_removal(self):
        block=V3.replace('RAD=1 VAL=2', 'RAD=2 VAL=3')
        native=Chem.MolFromMolBlock
        with patch('nivram.chemistry.Chem.MolFromMolBlock', wraps=native) as parser:
            import_structure(block)
        self.assertEqual(parser.call_args.kwargs, dict(sanitize=True,removeHs=False,strictParsing=True))

    def test_preparation_batch_and_metadata_specific_errors(self):
        from nivram.import_preparation import _prepare,SCHEMA,ImportPreparationError
        from nivram.image_import import inspect_png_metadata
        from test_image_import import png
        for block in (V2.replace('1   1   1','1   1   2'),V3.replace('RAD=1','RAD=2')):
            requests=[dict(schema=SCHEMA,mode='import',source=block,style={}),
                      dict(schema=SCHEMA,mode='batch',source=block,extension='.mol',settings={'style':{},'export':{}})]
            for request in requests:
                with self.assertRaises(ImportPreparationError) as caught:_prepare(request)
                self.assertEqual(caught.exception.code,'inconsistent_mol_radical')
            result=inspect_png_metadata(png([(b'tEXt',b'MOL rdkit 2025.09.4\0'+block.encode())]))
            self.assertFalse(result['candidates'])
            self.assertIn('Declared radical',str(result['unsupportedSources']))

if __name__=='__main__':unittest.main()
