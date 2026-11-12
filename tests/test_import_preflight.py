"""Imports outside the editor's graph limits must not enter RDKit's parser."""
import unittest
from unittest.mock import patch

from rdkit import Chem
from rdkit.Chem import rdDepictor
from nivram.chemistry import ChemistryError, graph_to_mol, import_structure
from nivram.import_limits import ImportLimitError, check_smiles


class ImportPreflightTests(unittest.TestCase):
    def assert_parser_not_called(self, source, parser_name):
        with patch('nivram.chemistry.Chem.' + parser_name,
                   side_effect=RuntimeError('native parser sentinel')) as parser:
            with self.assertRaises(ChemistryError):
                import_structure(source)
            parser.assert_not_called()

    def test_oversized_smiles_rejected_before_native_parser(self):
        # The sentinel cannot allocate a molecule, even if the guard regresses.
        with patch('nivram.chemistry.Chem.MolFromSmiles',
                   side_effect=RuntimeError('native parser sentinel')) as parser:
            with self.assertRaises(ChemistryError):
                import_structure('C' * 513)
            parser.assert_not_called()

    def test_oversized_bracket_and_halogen_tokens_rejected_early(self):
        for token in ('[13CH4]', 'Cl', 'Br', '[se]'):
            with self.subTest(token=token):
                self.assert_parser_not_called('.'.join([token] * 513), 'MolFromSmiles')

    def test_exact_atom_limit_reaches_parser(self):
        with patch('nivram.chemistry.Chem.MolFromSmiles', return_value=None) as parser:
            with self.assertRaises(ChemistryError):
                import_structure('.'.join(['[13CH4:123]'] * 512))
            parser.assert_called_once()

    def test_explicit_hydrogens_count_toward_input_atom_limit(self):
        with patch('nivram.chemistry.Chem.MolFromSmiles', return_value=None) as parser:
            with self.assertRaises(ChemistryError):
                import_structure('C([H])' * 256)
            parser.assert_called_once()
        self.assert_parser_not_called('C([H])' * 257, 'MolFromSmiles')

    def test_small_supported_smiles_preserve_identity(self):
        sources = ('CC(Cl)Br', '[13CH3:7][C@@H](F)Cl', '[Na+].[Cl-]',
                   'c1ncc[nH]1', 'C1CC1C1CC1', 'C%12CC%12',
                   'C%(1000)OC%(1000)', '[#6]O', 'F/C=C/F',
                   'F/C=C\\F', 'C1.CC1', 'C(C)(O)N', 'c1cc[se]c1')
        for source in sources:
            with self.subTest(source=source):
                expected = Chem.MolToSmiles(Chem.MolFromSmiles(source), isomericSmiles=True)
                graph = import_structure(source)
                actual = Chem.MolToSmiles(graph_to_mol(graph), isomericSmiles=True)
                self.assertEqual(actual, expected)

    def test_ring_bonds_counted_with_branches_and_components(self):
        # Four atoms in two components connected by two ring edges: four bonds.
        for source in ('C1CC1C', 'C1.CC1C', 'C1(C)CC1', 'C%(1000)CC%(1000)C'):
            count = Chem.MolFromSmiles(source).GetNumBonds()
            check_smiles(source, 512, count)
            with self.subTest(source=source), self.assertRaises(ImportLimitError):
                check_smiles(source, 512, count - 1)

    def test_small_v2000_and_v3000_preserve_identity(self):
        for source in ('CC(Cl)Br', '[13CH3:7][C@@H](F)Cl', '[Na+].[Cl-]', 'F/C=C/F'):
            mol = Chem.MolFromSmiles(source)
            rdDepictor.Compute2DCoords(mol)
            for v3000 in (False, True):
                with self.subTest(source=source, v3000=v3000):
                    block = Chem.MolToMolBlock(mol, forceV3000=v3000)
                    actual = graph_to_mol(import_structure(block))
                    self.assertEqual(Chem.MolToSmiles(actual, True), Chem.MolToSmiles(mol, True))

    def test_v3000_underreported_actual_records_rejected_early(self):
        source = ('preflight\n  nivraM\n\n'
                  '  0  0  0     0  0            999 V3000\n'
                  'M  V30 BEGIN CTAB\nM  V30 COUNTS 1 0 0 0 0\nM  V30 BEGIN ATOM\n'
                  + ''.join(f'M  V30 {i} C 0 0 0 0\n' for i in range(1, 514))
                  + 'M  V30 END ATOM\nM  V30 END CTAB\nM  END\n')
        self.assert_parser_not_called(source, 'MolFromMolBlock')

    def test_v3000_continued_counts_rejected_early(self):
        source = ('preflight\n  nivraM\n\n'
                  '  0  0  0     0  0            999 V3000\n'
                  'M  V30 BEGIN CTAB\nM  V30 COUNTS 51-\nM  V30 3 0 0 0 0\n'
                  'M  V30 END CTAB\nM  END\n')
        self.assert_parser_not_called(source, 'MolFromMolBlock')

    def test_v3000_bond_count_rejected_early(self):
        source = ('preflight\n  nivraM\n\n'
                  '  0  0  0     0  0            999 V3000\n'
                  'M  V30 BEGIN CTAB\nM  V30 COUNTS 3 1025 0 0 0\n'
                  'M  V30 END CTAB\nM  END\n')
        self.assert_parser_not_called(source, 'MolFromMolBlock')

    def test_v3000_underreported_bond_records_rejected_early(self):
        mol = Chem.MolFromSmiles('CCO')
        rdDepictor.Compute2DCoords(mol)
        block = Chem.MolToMolBlock(mol, forceV3000=True)
        self.assertIn('M  V30 COUNTS 3 2 ', block)
        block = block.replace('M  V30 COUNTS 3 2 ', 'M  V30 COUNTS 3 1 ', 1)
        self.assert_parser_not_called(block, 'MolFromMolBlock')

    def test_valid_continued_v3000_atom_and_bond_records(self):
        mol = Chem.MolFromSmiles('CCO')
        rdDepictor.Compute2DCoords(mol)
        block = Chem.MolToMolBlock(mol, forceV3000=True)
        before, marker, bonds = block.partition('M  V30 BEGIN BOND\n')
        self.assertTrue(marker)
        before = before.replace('M  V30 1 C ', 'M  V30 1 -\nM  V30 C ', 1)
        bonds = bonds.replace('M  V30 1 1 ', 'M  V30 1 -\nM  V30 1 ', 1)
        actual = graph_to_mol(import_structure(before + marker + bonds))
        self.assertEqual(Chem.MolToSmiles(actual, True), Chem.MolToSmiles(mol, True))

    def test_oversized_v2000_counts_rejected_before_native_parser(self):
        source = ('preflight\n  nivraM\n\n'
                  '513  0  0  0  0  0            999 V2000\nM  END\n')
        with patch('nivram.chemistry.Chem.MolFromMolBlock',
                   side_effect=RuntimeError('native parser sentinel')) as parser:
            with self.assertRaises(ChemistryError):
                import_structure(source)
            parser.assert_not_called()

    def test_oversized_v3000_counts_rejected_before_native_parser(self):
        source = ('preflight\n  nivraM\n\n'
                  '  0  0  0     0  0            999 V3000\n'
                  'M  V30 BEGIN CTAB\nM  V30 COUNTS 513 0 0 0 0\n'
                  'M  V30 END CTAB\nM  END\n')
        with patch('nivram.chemistry.Chem.MolFromMolBlock',
                   side_effect=RuntimeError('native parser sentinel')) as parser:
            with self.assertRaises(ChemistryError):
                import_structure(source)
            parser.assert_not_called()


if __name__ == '__main__':
    unittest.main()
