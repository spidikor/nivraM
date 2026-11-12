import copy
import unittest
from unittest.mock import patch

from rdkit import Chem
from rdkit.Chem import rdDepictor
from rdkit.Geometry import Point3D

from nivram.chemistry import graph_to_mol, import_structure
from nivram.optical_import import (MODEL_SHA256, MAX_OPTICAL_MOLFILE,
                                    MAX_OPTICAL_SMILES, OpticalImportError,
                                    optical_candidate)


def worker_result(smiles='CCO', mol=None, **changes):
    if mol is None:
        mol = graph_to_mol(import_structure(smiles))
    result = {'schemaVersion': 1, 'method': 'MolScribe', 'modelSha256': MODEL_SHA256,
              'inputSha256': 'a' * 64, 'smiles': smiles,
              'molfile': Chem.MolToMolBlock(mol), 'confidence': .82}
    result.update(changes)
    return result


def depicted_molecule(smiles):
    """Build a MOL fixture independently of the editable graph conversion."""
    parser = Chem.SmilesParserParams()
    parser.removeHs = False
    mol = Chem.MolFromSmiles(smiles, parser)
    if mol is None:
        raise AssertionError('Invalid test fixture SMILES: ' + smiles)
    rdDepictor.Compute2DCoords(mol)
    return mol


class OpticalImportTests(unittest.TestCase):
    def test_matching_identity_preserves_source_coordinates_and_input(self):
        mol = graph_to_mol(import_structure('CCO'))
        for index, (x, y) in enumerate([(2.1234, 3.5678), (3.2345, 3.8765), (4.4321, 4.1234)]):
            mol.GetConformer().SetAtomPosition(index, Point3D(x, y, 0))
        result = worker_result('CCO', mol)
        before = copy.deepcopy(result)
        candidate = optical_candidate(result)
        self.assertEqual(candidate['id'], 'optical-1')
        self.assertEqual(candidate['source'], 'optical-recognition')
        self.assertTrue(candidate['requiresReview'])
        self.assertEqual(candidate['recognition']['coordinates'], 'preserved-from-recognized-molfile')
        self.assertEqual(candidate['recognition']['inputSha256'], 'a' * 64)
        self.assertEqual(candidate['recognition']['confidence'], .82)
        self.assertEqual(candidate['recognition']['confidenceMeaning'], 'uncalibrated model score')
        for atom, point in zip(candidate['graph']['atoms'], mol.GetConformer().GetPositions()):
            self.assertAlmostEqual(atom['x'], point[0], places=4)
            self.assertAlmostEqual(atom['y'], point[1], places=4)
            self.assertTrue(atom['id'].startswith('optical-1-'))
        self.assertEqual(Chem.MolToSmiles(graph_to_mol(candidate['graph'])), 'CCO')
        self.assertEqual(result, before)

    def test_matching_stereo_charge_isotope_and_radicals_survive(self):
        for smiles in ['[13CH3][C@@H](O)C(=O)[O-]', 'F/C=C/F', 'F/C=C\\F', '[CH3]']:
            with self.subTest(smiles=smiles):
                candidate = optical_candidate(worker_result(smiles))
                self.assertEqual(candidate['recognition']['coordinates'], 'preserved-from-recognized-molfile')
                self.assertEqual(Chem.MolToSmiles(graph_to_mol(candidate['graph']), isomericSmiles=True),
                                 Chem.MolToSmiles(Chem.MolFromSmiles(smiles), isomericSmiles=True))

    def test_atom_aliases_are_rejected_without_inferring_an_expansion(self):
        # Reject a dummy abbreviation with an expanded SMILES, and an alias on
        # an ordinary atom even when the underlying chemical identities agree.
        for depicted, alias, predicted in [('C*', 'OMe', 'COC'), ('CO', 'OH', 'CO')]:
            with self.subTest(depicted=depicted, alias=alias):
                source = depicted_molecule(depicted)
                Chem.SetAtomAlias(source.GetAtomWithIdx(1), alias)
                result = worker_result(predicted, source)
                before = copy.deepcopy(result)
                with self.assertRaises(OpticalImportError) as raised:
                    optical_candidate(result)
                self.assertEqual(raised.exception.code, 'unsupported_abbreviation')
                self.assertEqual(result, before)

    def test_valid_differing_depiction_is_rejected_instead_of_replaced(self):
        source = graph_to_mol(import_structure('CCO'))
        with self.assertRaises(OpticalImportError) as raised:
            optical_candidate(worker_result('CCN', source))
        self.assertEqual(raised.exception.code, 'inconsistent_prediction')

    def test_chemistry_differences_between_valid_representations_are_rejected(self):
        pairs = [
            ('connectivity', 'CCCO', 'CC(O)C'),
            ('bond order', 'CCO', 'C=CO'),
            ('formal charge', 'C[NH3+]', 'CN'),
            ('carbon isotope', '[13CH3]CO', 'CCO'),
            ('hydrogen isotope', '[2H]CO', 'CO'),
            ('tetrahedral inversion', 'F[C@](Cl)(Br)I', 'F[C@@](Cl)(Br)I'),
            ('missing tetrahedral stereo', 'F[C@](Cl)(Br)I', 'FC(Cl)(Br)I'),
            ('double bond stereo', 'F/C=C/F', 'F/C=C\\F'),
            ('atom mapping', '[CH3:1][OH:2]', '[CH3:2][OH:1]'),
            ('mapped hydrogen', '[H:9]CO', 'CO'),
            ('radical electrons', '[CH2]C', 'CC'),
        ]
        for feature, depicted, predicted in pairs:
            with self.subTest(feature=feature):
                result = worker_result(predicted, depicted_molecule(depicted))
                before = copy.deepcopy(result)
                with self.assertRaises(OpticalImportError) as raised:
                    optical_candidate(result)
                self.assertEqual(raised.exception.code, 'inconsistent_prediction')
                self.assertEqual(result, before)

    def test_equivalent_reordered_and_aromatic_representations_retain_mol_coordinates(self):
        pairs = [
            ('OCC', 'CCO'),
            ('C1=CC=CC=C1', 'c1ccccc1'),
            ('[OH:2][CH2:3][CH3:7]', '[CH3:7][CH2:3][OH:2]'),
        ]
        for depicted, predicted in pairs:
            with self.subTest(depicted=depicted, predicted=predicted):
                source = depicted_molecule(depicted)
                candidate = optical_candidate(worker_result(predicted, source))
                self.assertEqual(candidate['recognition']['coordinates'], 'preserved-from-recognized-molfile')
                self.assertEqual(Chem.MolToSmiles(graph_to_mol(candidate['graph']), isomericSmiles=True),
                                 Chem.MolToSmiles(Chem.MolFromSmiles(predicted), isomericSmiles=True))
                for atom, point in zip(candidate['graph']['atoms'], source.GetConformer().GetPositions()):
                    self.assertAlmostEqual(atom['x'], point[0], places=4)
                    self.assertAlmostEqual(atom['y'], point[1], places=4)

    def test_ordinary_explicit_hydrogen_equivalence_retains_mol_coordinates(self):
        mol = Chem.AddHs(Chem.MolFromSmiles('CO'))
        rdDepictor.Compute2DCoords(mol)
        candidate = optical_candidate(worker_result('CO', mol))
        self.assertEqual(candidate['recognition']['coordinates'], 'preserved-from-recognized-molfile')
        self.assertEqual(len(candidate['graph']['atoms']), mol.GetNumAtoms())
        for atom, point in zip(candidate['graph']['atoms'], mol.GetConformer().GetPositions()):
            self.assertAlmostEqual(atom['x'], point[0], places=4)
            self.assertAlmostEqual(atom['y'], point[1], places=4)

    def test_disconnected_fragments_are_retained_together_and_disclosed(self):
        candidate = optical_candidate(worker_result('[Na+].[Cl-]', confidence=None))
        self.assertIsNone(candidate['recognition']['confidence'])
        self.assertEqual(len(candidate['graph']['atoms']), 2)
        self.assertTrue(any('disconnected' in warning for warning in candidate['warnings']))

    def test_worker_contract_and_identity_are_strict(self):
        baseline = worker_result()
        bad = [None, [], {}, {**baseline, 'extra': True}]
        for field, value in [('schemaVersion', True), ('schemaVersion', 2), ('method', 'other'),
                             ('modelSha256', '0' * 64), ('inputSha256', 'A' * 64),
                             ('inputSha256', 'a' * 63), ('inputSha256', None)]:
            bad.append({**baseline, field: value})
        for result in bad:
            with self.subTest(result=result):
                with self.assertRaises(OpticalImportError) as raised:
                    optical_candidate(result)
                self.assertEqual(raised.exception.code, 'invalid_prediction')

    def test_invalid_scores_are_rejected_including_nan_infinity_and_boolean(self):
        baseline = worker_result()
        for score in [float('nan'), float('inf'), -float('inf'), -.01, 1.01, 10**400, True, '0.9', {}, []]:
            with self.subTest(score=score), self.assertRaises(OpticalImportError):
                optical_candidate({**baseline, 'confidence': score})

    def test_oversized_text_is_rejected_before_native_parser(self):
        baseline = worker_result()
        for field, text in [('smiles', 'C' * (MAX_OPTICAL_SMILES + 1)),
                            ('molfile', 'X' * (MAX_OPTICAL_MOLFILE + 1)),
                            ('molfile', '\u2603' * (MAX_OPTICAL_MOLFILE // 2))]:
            with self.subTest(field=field), patch('nivram.optical_import.Chem.MolFromSmiles') as parse:
                with self.assertRaises(OpticalImportError):
                    optical_candidate({**baseline, field: text})
                parse.assert_not_called()

    def test_atom_count_preflight_rejects_before_native_parser(self):
        result = worker_result(smiles='C' * 513, mol=graph_to_mol(import_structure('CC')))
        with patch('nivram.optical_import.Chem.MolFromSmiles') as parse:
            with self.assertRaises(OpticalImportError):
                optical_candidate(result)
            parse.assert_not_called()

    def test_malformed_records_cannot_fall_back_silently(self):
        baseline = worker_result()
        malformed = [('smiles', ''), ('smiles', None), ('smiles', 'not-smiles'),
                     ('smiles', 'CCO name'), ('smiles', 'CCO\nCCN'), ('smiles', 'CCO |atomProp:0.x.y|'),
                     ('smiles', 'CC>>CO'), ('molfile', ''), ('molfile', 'garbage'),
                     ('molfile', baseline['molfile'] + '$$$$\n'),
                     ('molfile', baseline['molfile'] + baseline['molfile']),
                     ('molfile', baseline['molfile'] + '\0')]
        for field, value in malformed:
            with self.subTest(field=field, value=value), self.assertRaises(OpticalImportError):
                optical_candidate({**baseline, field: value})

    def test_unresolved_wildcards_queries_and_invalid_valence_are_rejected(self):
        baseline = worker_result()
        for smiles in ['C*', 'C[1*]', '[C,N]', 'CO(C)C', 'C[C@H](C)C', '[Pt@SP1](Cl)(F)(Br)I']:
            with self.subTest(smiles=smiles), self.assertRaises(OpticalImportError):
                optical_candidate({**baseline, 'smiles': smiles})
        dummy = Chem.MolFromSmiles('C*')
        rdDepictor.Compute2DCoords(dummy)
        with self.assertRaises(OpticalImportError):
            optical_candidate(worker_result('CO', dummy))

    def test_query_mol_is_rejected_even_when_smiles_is_valid(self):
        query = Chem.MolFromSmarts('[#6,#7]~[#8]')
        rdDepictor.Compute2DCoords(query)
        with self.assertRaises(OpticalImportError):
            optical_candidate(worker_result('CO', query))

    def test_explicit_unknown_stereochemistry_cannot_disappear_in_fallback(self):
        source = Chem.MolFromSmiles('FC(Cl)Br')
        rdDepictor.Compute2DCoords(source)
        source.GetBondWithIdx(0).SetBondDir(Chem.BondDir.UNKNOWN)
        for v3000 in [False, True]:
            with self.subTest(v3000=v3000), self.assertRaises(OpticalImportError):
                optical_candidate(worker_result('CCC', source, molfile=Chem.MolToMolBlock(source, forceV3000=v3000)))

    def test_3d_depiction_cannot_be_silently_flattened(self):
        source = graph_to_mol(import_structure('CCO'))
        source.GetConformer().Set3D(True)
        source.GetConformer().SetAtomPosition(0, Point3D(0, 0, 1))
        with self.assertRaises(OpticalImportError):
            optical_candidate(worker_result('CCO', source))


if __name__ == '__main__':
    unittest.main()
