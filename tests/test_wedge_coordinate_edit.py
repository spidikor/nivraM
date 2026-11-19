"""An ordinary drag may move atoms, not invert a separately declared wedge."""
import copy
import unittest

from rdkit import Chem
from rdkit.Chem import rdDepictor
from nivram.chemistry import ChemistryError, export_mol, graph_to_mol, import_structure
from nivram.scene import render_scene


def drawn_center(smiles, v3000):
    source = Chem.MolFromSmiles(smiles)
    rdDepictor.Compute2DCoords(source)
    return import_structure(Chem.MolToMolBlock(source, forceV3000=v3000))


class WedgeCoordinateTests(unittest.TestCase):
    def test_single_atom_drag_cannot_commit_a_conflicting_depiction(self):
        for smiles in ('C[C@H](F)Cl', 'C[C@@H](F)Cl'):
            for v3000 in (False, True):
                with self.subTest(smiles=smiles, v3000=v3000):
                    graph = drawn_center(smiles, v3000)
                    baseline = copy.deepcopy(graph)
                    center = next(a for a in graph['atoms'] if a['chiralTag'])
                    self.assertTrue(any(b['begin'] == center['id'] and b['direction'] in (1, 2)
                                        for b in graph['bonds']))
                    candidate = copy.deepcopy(graph)
                    chlorine = next(a for a in candidate['atoms'] if a['symbol'] == 'Cl')
                    chlorine['y'] = center['y'] - (chlorine['y'] - center['y']) * 4 / 3
                    prior_candidate = copy.deepcopy(candidate)
                    # Remove *only* the atom tag on an independent copy: the wedge depicts
                    # the other enantiomer after this edit, not just “different coordinates.”
                    as_drawn = copy.deepcopy(candidate)
                    next(a for a in as_drawn['atoms'] if a['id'] == center['id'])['chiralTag'] = 0
                    self.assertNotEqual(Chem.MolToSmiles(graph_to_mol(as_drawn)), smiles)
                    for route in (graph_to_mol, render_scene, export_mol):
                        with self.subTest(route=route.__name__), self.assertRaisesRegex(ChemistryError, 'wedge'):
                            route(candidate)
                    self.assertEqual(candidate, prior_candidate)
                    self.assertEqual(graph, baseline)

    def test_consistent_drag_keeps_original_wedge_and_isomer(self):
        for smiles in ('C[C@H](F)Cl', 'C[C@@H](F)Cl'):
            with self.subTest(smiles=smiles):
                graph = drawn_center(smiles, True)
                marks = copy.deepcopy(graph['bonds'])
                chlorine = next(a for a in graph['atoms'] if a['symbol'] == 'Cl')
                chlorine['x'] += .15
                self.assertEqual(Chem.MolToSmiles(graph_to_mol(graph)), smiles)
                self.assertEqual(Chem.MolToSmiles(Chem.MolFromMolBlock(export_mol(graph))), smiles)
                self.assertEqual(graph['bonds'], marks)

    def test_wedge_on_an_alkene_neighbor_bond_preserves_both_stereo_sites(self):
        # On public main, native double-bond directions are regenerated after
        # validating the graph. One bond can also be the stereocenter's wedge.
        smiles = 'F/C=C/[C@H](F)Cl'
        mol = Chem.MolFromSmiles(smiles)
        rdDepictor.Compute2DCoords(mol)
        block = Chem.MolToMolBlock(mol, forceV3000=True)
        self.assertIn('M  V30 4 1 4 5 CFG=1\n', block)
        self.assertIn('M  V30 3 1 3 4\n', block)
        block = block.replace('M  V30 4 1 4 5 CFG=1\n', 'M  V30 4 1 4 5\n')
        for mark, expected in ((1, smiles), (3, smiles.replace('@H', '@@H'))):
            with self.subTest(mark=mark):
                graph = import_structure(block.replace('M  V30 3 1 3 4\n',
                                                       f'M  V30 3 1 4 3 CFG={mark}\n'))
                self.assertEqual(Chem.MolToSmiles(graph_to_mol(graph)), expected)
                self.assertEqual(Chem.MolToSmiles(Chem.MolFromMolBlock(export_mol(graph))), expected)
                candidate = copy.deepcopy(graph)
                next(a for a in candidate['atoms'] if a['symbol'] == 'Cl')['y'] = -2.9
                with self.assertRaisesRegex(ChemistryError, 'wedge geometry'):
                    graph_to_mol(candidate)

    def test_local_wedge_comparison_is_not_hidden_by_meso_symmetry(self):
        graph = drawn_center('C[C@H](O)[C@H](O)C', False)
        original = graph_to_mol(graph)
        reflected = copy.deepcopy(graph)
        for atom in reflected['atoms']:
            atom['x'] = -atom['x']
        wedge_only = copy.deepcopy(reflected)
        for atom in wedge_only['atoms']:
            atom['chiralTag'] = 0
        depicted = graph_to_mol(wedge_only)
        self.assertEqual(Chem.MolToSmiles(depicted), Chem.MolToSmiles(original))
        tagged = [i for i, atom in enumerate(graph['atoms']) if atom['chiralTag']]
        self.assertEqual(len(tagged), 2)
        for i in tagged:
            self.assertNotEqual(original.GetAtomWithIdx(i).GetChiralTag(),
                                depicted.GetAtomWithIdx(i).GetChiralTag())
        with self.assertRaisesRegex(ChemistryError, 'wedge geometry'):
            graph_to_mol(reflected)

    def test_one_contradictory_wedge_cannot_hide_behind_a_second_correct_mark(self):
        mol = Chem.MolFromSmiles('N[C@](F)(Br)I')
        rdDepictor.Compute2DCoords(mol)
        block = Chem.MolToMolBlock(mol, forceV3000=True)
        self.assertIn('M  V30 1 1 2 1 CFG=3\n', block)
        self.assertIn('M  V30 3 1 2 4\n', block)
        graph = import_structure(block.replace('M  V30 3 1 2 4\n', 'M  V30 3 1 2 4 CFG=1\n'))
        expected = Chem.MolToSmiles(mol)
        self.assertEqual(Chem.MolToSmiles(graph_to_mol(graph)), expected)
        self.assertEqual(Chem.MolToSmiles(Chem.MolFromMolBlock(export_mol(graph))), expected)
        candidate = copy.deepcopy(graph)
        candidate['atoms'][0].update(x=-.7, y=-2.)
        with self.assertRaisesRegex(ChemistryError, 'wedge geometry'):
            graph_to_mol(candidate)
