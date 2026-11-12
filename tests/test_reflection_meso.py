"""Meso reflection checks with independently identified stereo controls."""
import copy
import unittest

from rdkit import Chem

from nivram.chemistry import export_mol, graph_to_mol, import_structure
from nivram.reflection import reflect_graph


MESO = 'C[C@H](O)[C@H](O)C'
CHIRAL_CONTROL = 'C[C@H](O)[C@@H](O)C'


def identity(molecule):
    return Chem.MolToSmiles(molecule, isomericSmiles=True)


def centers(molecule):
    return dict(Chem.FindMolChiralCenters(molecule, force=True, includeUnassigned=True))


def invert_all(molecule):
    inverted = Chem.Mol(molecule)
    for atom in inverted.GetAtoms():
        if atom.GetChiralTag() != Chem.ChiralType.CHI_UNSPECIFIED:
            atom.InvertChirality()
    return inverted


class MesoReflectionTests(unittest.TestCase):
    def test_fixture_has_two_opposite_centers_but_its_full_inverse_is_identical(self):
        # These checks do not call nivraM's reflection implementation. The
        # chiral control ensures canonicalization distinguishes enantiomers.
        meso = Chem.MolFromSmiles(MESO)
        self.assertEqual(centers(meso), {1: 'S', 3: 'R'})
        self.assertEqual(identity(invert_all(meso)), identity(meso))
        control = Chem.MolFromSmiles(CHIRAL_CONTROL)
        self.assertEqual(centers(control), {1: 'S', 3: 'S'})
        self.assertNotEqual(identity(invert_all(control)), identity(control))
        self.assertNotEqual(identity(control), identity(meso))

    def test_meso_flip_and_mirror_preserve_identity_and_mol_stereo_on_both_axes(self):
        plain = import_structure(MESO)
        for graph in (plain, import_structure(export_mol(plain))):
            original = copy.deepcopy(graph)
            original_centers = centers(graph_to_mol(graph))
            self.assertEqual(original_centers, {1: 'S', 3: 'R'})
            expected_identity = identity(Chem.MolFromSmiles(MESO))
            for axis in ('horizontal', 'vertical'):
                coordinate = 'x' if axis == 'horizontal' else 'y'
                other = 'y' if axis == 'horizontal' else 'x'
                values = [atom[coordinate] for atom in graph['atoms']]
                extent = min(values) + max(values)
                for mode in ('flip', 'mirror'):
                    with self.subTest(axis=axis, mode=mode,
                                      explicit_wedges=any(b.get('direction') in (1, 2) for b in graph['bonds'])):
                        result = reflect_graph(graph, mode=mode, axis=axis)
                        molecule = graph_to_mol(result)
                        self.assertEqual(identity(molecule), expected_identity)
                        expected_centers = (original_centers if mode == 'flip' else
                                            {i: ('R' if value == 'S' else 'S')
                                             for i, value in original_centers.items()})
                        # Whole-molecule identity alone would miss a no-op
                        # mirror of this meso structure; compare each center.
                        self.assertEqual(centers(molecule), expected_centers)
                        restored = Chem.MolFromMolBlock(export_mol(result), removeHs=False)
                        self.assertIsNotNone(restored)
                        self.assertEqual(identity(restored), expected_identity)
                        self.assertEqual(centers(restored), expected_centers)
                        self.assertEqual([a['id'] for a in result['atoms']],
                                         [a['id'] for a in original['atoms']])
                        for before, after in zip(original['atoms'], result['atoms']):
                            self.assertAlmostEqual(after[coordinate], extent - before[coordinate], places=12)
                            self.assertEqual(after[other], before[other])
                        twice = reflect_graph(result, mode=mode, axis=axis)
                        self.assertEqual(centers(graph_to_mol(twice)), original_centers)
                        for before, after in zip(original['atoms'], twice['atoms']):
                            self.assertAlmostEqual(after['x'], before['x'], places=12)
                            self.assertAlmostEqual(after['y'], before['y'], places=12)
            self.assertEqual(graph, original, 'Reflection must not mutate its input.')


if __name__ == '__main__':
    unittest.main()
