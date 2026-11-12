"""Independent reviewer fixtures for quoted MASS spacing and continuations."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from rdkit import Chem
from nivram.chemistry import import_structure, graph_to_mol
from nivram.import_limits import prepare_mol
from nivram.optical_import import optical_candidate, MODEL_SHA256
from nivram.optical_validation import _command

ROOT = Path(__file__).parent
CASES = sum((json.loads((ROOT / name).read_text()) for name in
             ('quoted_mass_space_fixtures.json', 'quoted_mass_continuation_fixtures.json')), [])


def payload(block, smiles):
    return dict(schemaVersion=1, method='MolScribe', modelSha256=MODEL_SHA256,
                inputSha256='0'*64, confidence=None, smiles=smiles, molfile=block)


class QuotedMassSpacingTests(unittest.TestCase):
    def test_independent_identity_and_exact_signed_unsigned_graphs(self):
        for case in CASES:
            block, expected = case['molfile'], case['expected_smiles']
            with self.subTest(case=case['name']):
                self.assertEqual(hashlib.sha256(block.encode()).hexdigest(), case['source_sha256'])
                if expected is None:
                    with patch('nivram.chemistry.Chem.MolFromMolBlock') as parser:
                        with self.assertRaises(ValueError): import_structure(block)
                        parser.assert_not_called()
                    with self.assertRaises(ValueError): optical_candidate(payload(block, 'C'))
                    continue
                ordinary = import_structure(block)
                mol = graph_to_mol(ordinary)
                self.assertEqual(Chem.MolToSmiles(mol), expected)
                self.assertEqual(mol.GetNumAtoms(), 1)
                self.assertEqual(mol.GetNumBonds(), 0)
                atom = mol.GetAtomWithIdx(0)
                self.assertEqual((atom.GetAtomicNum(), atom.GetIsotope(), atom.GetFormalCharge(),
                                  atom.GetNumRadicalElectrons(), atom.GetAtomMapNum(), atom.GetTotalNumHs()),
                                 (6, 13 if expected == '[13CH4]' else 0, 0, 0, 0, 4))
                raw = payload(block, expected); original = copy.deepcopy(raw)
                optical = optical_candidate(raw)['graph']
                self.assertEqual(raw, original)
                self.assertEqual(Chem.MolToSmiles(graph_to_mol(optical)), expected)
                # All positive fixtures have at most one plus, exclusively in
                # their frozen MASS assignment, including split digit records.
                self.assertLessEqual(block.count('+'), 1)
                unsigned = block.replace('+', '')
                self.assertEqual(ordinary, import_structure(unsigned))
                self.assertEqual(optical, optical_candidate(payload(unsigned, expected))['graph'])
                if case.get('wrong_smiles'):
                    with self.assertRaises(ValueError):
                        optical_candidate(payload(block, case['wrong_smiles']))

    def test_only_validated_sign_changes_and_preparation_is_idempotent(self):
        for case in CASES:
            if case['expected_smiles'] is None: continue
            for newline in ('\n', '\r\n'):
                raw = case['molfile'].replace('\n', newline)
                with self.subTest(case=case['name'], newline=repr(newline)):
                    prepared, guards = prepare_mol(raw, 512, 1024)
                    self.assertEqual(prepared, raw.replace('+', ''))
                    self.assertEqual(prepare_mol(prepared, 512, 1024), (prepared, guards))
                    self.assertEqual(prepared.count(newline), raw.count(newline))

    def test_real_isolation_correct_wrong_and_malformed(self):
        for case in CASES:
            expected = case['expected_smiles']
            checks = [(expected or 'C', expected is not None)]
            if case.get('wrong_smiles'): checks.append((case['wrong_smiles'], False))
            for smiles, success in checks:
                with self.subTest(case=case['name'], smiles=smiles):
                    raw = json.dumps(payload(case['molfile'], smiles))
                    cp = subprocess.run(_command(), input=raw, text=True, capture_output=True, timeout=15)
                    self.assertEqual(cp.returncode, 0 if success else 2, cp.stdout)
                    envelope = json.loads(cp.stdout)
                    self.assertEqual('candidate' in envelope, success)
                    if success:
                        self.assertEqual(Chem.MolToSmiles(graph_to_mol(envelope['candidate']['graph'])), expected)
                    self.assertEqual(json.loads(raw)['molfile'], case['molfile'])


if __name__ == '__main__': unittest.main()
