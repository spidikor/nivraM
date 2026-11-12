"""Coordinate reflection checked against real chemical and MOL round-trip identity."""
import copy
import unittest
from unittest.mock import patch

from rdkit import Chem
from rdkit.Chem import rdDepictor

from nivram.chemistry import ChemistryError, export_mol, graph_to_mol, import_structure
from nivram.reflection import reflect_graph


def smiles(graph):
    return Chem.MolToSmiles(graph_to_mol(graph), isomericSmiles=True)


def canonical(source):
    return Chem.MolToSmiles(Chem.MolFromSmiles(source), isomericSmiles=True)


class ReflectionTests(unittest.TestCase):
    def assert_identity(self, graph, expected):
        self.assertEqual(smiles(graph), canonical(expected))
        restored = Chem.MolFromMolBlock(export_mol(graph), removeHs=False)
        self.assertIsNotNone(restored)
        self.assertEqual(Chem.MolToSmiles(restored, isomericSmiles=True), canonical(expected))

    def test_axes_use_selected_bounds_and_do_not_change_unselected_atoms(self):
        graph = import_structure('CCCC')
        for atom, point in zip(graph['atoms'], [(1, 2), (5, 8), (9, 4), (30, -7)]):
            atom['x'], atom['y'] = point
        before = copy.deepcopy(graph)
        for axis, expected in [('horizontal', [(9, 2), (5, 8), (1, 4), (30, -7)]),
                               ('vertical', [(1, 8), (5, 2), (9, 6), (30, -7)])]:
            with self.subTest(axis=axis):
                out = reflect_graph(graph, axis=axis, selected=['a0', 'a1', 'a2'])
                self.assertEqual([(a['x'], a['y']) for a in out['atoms']], expected)
                self.assertEqual(out['atoms'][3], before['atoms'][3])
                self.assertEqual(out['bonds'], before['bonds'])
        self.assertEqual(graph, before)

    def test_default_and_empty_selection_reflect_whole_graph(self):
        graph = import_structure('CCO')
        self.assertEqual(reflect_graph(graph), reflect_graph(graph, selected=[]))
        self.assertEqual(reflect_graph(graph), reflect_graph(graph, selected=[a['id'] for a in graph['atoms']]))
        self.assertIsNot(reflect_graph(graph), graph)

    def test_full_flip_preserves_defined_tetrahedral_and_double_bond_stereo(self):
        for source in ('F[C@](Cl)(Br)I', 'N[C@@H](C)C(=O)O', 'C[C@H](O)[C@H](F)Cl',
                       'F/C=C/F', 'F/C=C\\F', 'C/C=N/O', 'C/C=C/C=C/C'):
            plain = import_structure(source)
            for graph in (plain, import_structure(export_mol(plain))):
                for axis in ('horizontal', 'vertical'):
                    with self.subTest(source=source, axis=axis):
                        self.assert_identity(reflect_graph(graph, mode='flip', axis=axis), source)

    def test_full_mirror_inverts_defined_centers_and_preserves_ez(self):
        for source, expected in [('F[C@](Cl)(Br)I', 'F[C@@](Cl)(Br)I'),
                                 ('C[C@H](O)[C@H](F)Cl', 'C[C@@H](O)[C@@H](F)Cl'),
                                 ('C[C@H](O)/C=C/F', 'C[C@@H](O)/C=C/F'),
                                 ('F/C=C/F', 'F/C=C/F'), ('F/C=C\\F', 'F/C=C\\F')]:
            plain = import_structure(source)
            for graph in (plain, import_structure(export_mol(plain))):
                for axis in ('horizontal', 'vertical'):
                    with self.subTest(source=source, axis=axis):
                        self.assert_identity(reflect_graph(graph, mode='mirror', axis=axis), expected)

    def test_explicit_wedges_are_corrected_without_reversing_bond_endpoints(self):
        source = 'F[C@](Cl)(Br)I'
        graph = import_structure(export_mol(import_structure(source)))
        wedges = [b for b in graph['bonds'] if b['direction'] in (1, 2)]
        self.assertTrue(wedges)
        for axis in ('horizontal', 'vertical'):
            for mode, expected in [('flip', source), ('mirror', 'F[C@@](Cl)(Br)I')]:
                with self.subTest(axis=axis, mode=mode):
                    out = reflect_graph(graph, mode=mode, axis=axis)
                    self.assert_identity(out, expected)
                    self.assertEqual([(b['id'], b['begin'], b['end']) for b in out['bonds']],
                                     [(b['id'], b['begin'], b['end']) for b in graph['bonds']])
                    for before, after in zip(graph['bonds'], out['bonds']):
                        if before['direction'] in (1, 2):
                            self.assertEqual(after['direction'], 3 - before['direction'] if mode == 'flip' else before['direction'])
                    # Independent depiction check: existing wedges alone must
                    # encode the target, without stored tags masking a conflict.
                    wedge_only = copy.deepcopy(out)
                    for atom in wedge_only['atoms']:
                        atom['chiralTag'] = 0
                    self.assertEqual(smiles(wedge_only), canonical(expected))

    def test_wedge_only_defined_configuration_is_not_lost(self):
        graph = import_structure(export_mol(import_structure('F[C@](Cl)(Br)I')))
        for atom in graph['atoms']:
            atom['chiralTag'] = 0
        self.assert_identity(reflect_graph(graph), 'F[C@](Cl)(Br)I')
        self.assert_identity(reflect_graph(graph, mode='mirror'), 'F[C@@](Cl)(Br)I')

    def test_partial_mirror_changes_only_the_selected_defined_center(self):
        source = 'C[C@H](O)[C@H](F)Cl'
        graph = import_structure(export_mol(import_structure(source)))
        out = reflect_graph(graph, mode='mirror', selected=['a1'])
        self.assert_identity(out, 'C[C@@H](O)[C@H](F)Cl')
        self.assertEqual([(a['x'], a['y']) for a in out['atoms']],
                         [(a['x'], a['y']) for a in graph['atoms']])

    def test_selected_neighbors_preserve_an_unselected_centers_configuration(self):
        graph = import_structure('F[C@](Cl)(Br)I')
        positions = {'F': (-1, 0), 'C': (0, 0), 'Cl': (0, 1), 'Br': (1, 0), 'I': (0, -1)}
        for atom in graph['atoms']:
            atom['x'], atom['y'] = positions[atom['symbol']]
        graph = import_structure(export_mol(graph))
        self.assertTrue(any(b['direction'] in (1, 2) for b in graph['bonds']))
        for mode in ('flip', 'mirror'):
            with self.subTest(mode=mode):
                out = reflect_graph(graph, mode=mode, selected=['a0', 'a3'])
                self.assert_identity(out, 'F[C@](Cl)(Br)I')
                self.assertEqual(out['atoms'][1], graph['atoms'][1])
                wedge_only = copy.deepcopy(out)
                for atom in wedge_only['atoms']:
                    atom['chiralTag'] = 0
                self.assertEqual(smiles(wedge_only), canonical('F[C@](Cl)(Br)I'))

    def test_partial_double_bond_reflection_is_allowed_when_ez_remains_valid(self):
        graph = import_structure('F/C=C/F')
        for atom, point in zip(graph['atoms'], [(-1, 1), (0, 0), (2, 0), (3, -1)]):
            atom['x'], atom['y'] = point
        for mode in ('flip', 'mirror'):
            out = reflect_graph(graph, mode=mode, axis='vertical', selected=['a0', 'a3'])
            self.assert_identity(out, 'F/C=C/F')
            self.assertEqual(out['atoms'][1:3], graph['atoms'][1:3])

    def test_partial_double_bond_conflict_is_rejected_without_mutation(self):
        graph = import_structure('F/C=C/F.[He]')
        for atom, point in zip(graph['atoms'], [(-1, 1), (0, 0), (2, 0), (3, -1), (-2, -1)]):
            atom['x'], atom['y'] = point
        before = copy.deepcopy(graph)
        for mode in ('flip', 'mirror'):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ChemistryError, 'stereo|configuration'):
                    reflect_graph(graph, mode=mode, axis='vertical', selected=['a0', 'a4'])
        self.assertEqual(graph, before)

    def test_unknown_stereo_is_not_invented_or_erased(self):
        mol = Chem.MolFromSmiles('FC(Cl)Br.FC=CF')
        rdDepictor.Compute2DCoords(mol)
        mol.GetBondWithIdx(0).SetBondDir(Chem.BondDir.UNKNOWN)
        next(bond for bond in mol.GetBonds() if bond.GetBondType() == Chem.BondType.DOUBLE).SetStereo(Chem.BondStereo.STEREOANY)
        graph = import_structure(Chem.MolToMolBlock(mol, forceV3000=True))
        before = copy.deepcopy(graph)
        for mode in ('flip', 'mirror'):
            for axis in ('horizontal', 'vertical'):
                out = reflect_graph(graph, mode=mode, axis=axis)
                self.assert_identity(out, 'FC(Cl)Br.FC=CF')
                self.assertTrue(all(a['chiralTag'] == 0 for a in out['atoms']))
                self.assertEqual(out['bonds'], graph['bonds'])
        self.assertEqual(graph, before)

    def test_each_operation_is_an_involution(self):
        for source in ('F[C@](Cl)(Br)I', 'C[C@H](O)/C=C/F', 'CCO.[Na+].[Cl-]'):
            graph = import_structure(export_mol(import_structure(source)))
            for mode in ('flip', 'mirror'):
                for axis in ('horizontal', 'vertical'):
                    with self.subTest(source=source, mode=mode, axis=axis):
                        twice = reflect_graph(reflect_graph(graph, mode=mode, axis=axis), mode=mode, axis=axis)
                        self.assert_identity(twice, source)
                        for initial, final in zip(graph['atoms'], twice['atoms']):
                            self.assertAlmostEqual(final['x'], initial['x'], places=12)
                            self.assertAlmostEqual(final['y'], initial['y'], places=12)
                            self.assertEqual({k: v for k, v in final.items() if k not in ('x', 'y')},
                                             {k: v for k, v in initial.items() if k not in ('x', 'y')})
                        self.assertEqual(twice['bonds'], graph['bonds'])

    def test_charges_isotopes_maps_radicals_ids_and_metadata_are_preserved(self):
        source = '[13CH3:7][C@@H:8](O)C(=O)[O-].[Na+].[CH3]'
        graph = import_structure(source)
        graph['displayNote'] = {'synthetic': True}
        before = copy.deepcopy(graph)
        out = reflect_graph(graph)
        self.assert_identity(out, source)
        for old, new in zip(before['atoms'], out['atoms']):
            self.assertEqual({k: v for k, v in old.items() if k not in ('x', 'y')},
                             {k: v for k, v in new.items() if k not in ('x', 'y')})
        self.assertEqual(out['bonds'], before['bonds'])
        out['displayNote']['synthetic'] = False
        self.assertEqual(graph, before)

    def test_reflection_does_not_regenerate_layout(self):
        graph = import_structure('C[C@H](O)CC')
        with patch('rdkit.Chem.rdDepictor.Compute2DCoords', side_effect=AssertionError('Layout regeneration forbidden')):
            self.assert_identity(reflect_graph(graph), 'C[C@H](O)CC')

    def test_invalid_options_selection_and_empty_graph_are_rejected(self):
        graph = import_structure('CCO')
        for options in ({'mode': 'rotate'}, {'axis': 'diagonal'}, {'selected': 'a0'},
                        {'selected': ['absent']}, {'selected': ['a0', 'a0']},
                        {'selected': [True]}, {'selected': [{}]}):
            with self.subTest(options=options):
                with self.assertRaises(ChemistryError):
                    reflect_graph(graph, **options)
        with self.assertRaises(ChemistryError):
            reflect_graph({'atoms': [], 'bonds': []})


if __name__ == '__main__':
    unittest.main()
