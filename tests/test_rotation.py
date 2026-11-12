"""Rotation checks against molecular identity, depicted wedges and MOL export."""
import copy
import math
import unittest
from unittest.mock import patch

from rdkit import Chem
from rdkit.Chem import rdDepictor

from nivram.chemistry import ChemistryError, export_mol, graph_to_mol, import_structure
from nivram.rotation import rotate_graph


def identity(graph):
    return Chem.MolToSmiles(graph_to_mol(graph), isomericSmiles=True)


class RotationTests(unittest.TestCase):
    def assert_identity(self, graph, source):
        expected = Chem.MolToSmiles(Chem.MolFromSmiles(source), isomericSmiles=True)
        self.assertEqual(identity(graph), expected)
        restored = Chem.MolFromMolBlock(export_mol(graph), removeHs=False, strictParsing=True)
        self.assertIsNotNone(restored)
        self.assertEqual(Chem.MolToSmiles(restored, isomericSmiles=True), expected)

    def test_centroid_exact_selection_direction_and_unselected_coordinates(self):
        graph = import_structure('CCCC')
        for atom, point in zip(graph['atoms'], [(1, 2), (5, 8), (9, 2), (30, -7)]):
            atom['x'], atom['y'] = point
        before = copy.deepcopy(graph)
        for degrees in (-30, 30):
            out = rotate_graph(graph, degrees=degrees, selected=['a0', 'a1', 'a2'])
            centroid = (5, 4)
            c, s = math.sqrt(3)/2, degrees/60
            for old, new in zip(graph['atoms'][:3], out['atoms'][:3]):
                self.assertAlmostEqual(new['x'], 5+c*(old['x']-5)-s*(old['y']-4), places=12)
                self.assertAlmostEqual(new['y'], 4+s*(old['x']-5)+c*(old['y']-4), places=12)
            self.assertEqual(out['atoms'][3], before['atoms'][3])
            self.assertEqual(out['bonds'], before['bonds'])
            for field, expected in zip(('x', 'y'), centroid):
                self.assertAlmostEqual(sum(a[field] for a in out['atoms'][:3])/3, expected, places=12)
        self.assertEqual(graph, before)

    def test_full_turn_has_no_cumulative_distortion_and_preserves_stereo(self):
        sources = ('CCO', 'F[C@](Cl)(Br)I', 'C[C@H](O)[C@@H](F)Cl', 'F/C=C/F',
                   'F/C=C\\F', 'C/C=N/O', '[13CH3][C@@H](O)C(=O)[O-].[Na+]')
        for source in sources:
            plain = import_structure(source)
            for initial in (plain, import_structure(export_mol(plain))):
                for degrees in (-30, 30):
                    with self.subTest(source=source, degrees=degrees):
                        current = initial
                        for _ in range(12):
                            current = rotate_graph(current, degrees=degrees)
                            self.assert_identity(current, source)
                        for a, b in zip(initial['atoms'], current['atoms']):
                            self.assertLessEqual(math.hypot(a['x']-b['x'], a['y']-b['y']), 1e-6)
                            self.assertEqual({k:v for k,v in a.items() if k not in ('x','y')},
                                             {k:v for k,v in b.items() if k not in ('x','y')})
                        self.assertEqual(current['bonds'], initial['bonds'])

    def test_default_empty_selection_and_single_atom_noop(self):
        graph = import_structure('CCO')
        whole = rotate_graph(graph, degrees=30)
        self.assertEqual(whole, rotate_graph(graph, degrees=30, selected=[]))
        self.assertEqual(whole, rotate_graph(graph, degrees=30, selected=[a['id'] for a in graph['atoms']]))
        for source in ('CCO', '[He]'):
            initial = import_structure(source)
            out = rotate_graph(initial, degrees=30, selected=['a0'])
            self.assertEqual(out, initial)
            self.assertIsNot(out, initial)

    def test_wedges_preserve_endpoints_and_independent_depiction(self):
        source = 'F[C@](Cl)(Br)I'
        graph = import_structure(export_mol(import_structure(source)))
        self.assertTrue(any(b['direction'] in (1,2) for b in graph['bonds']))
        for wedge_only_input in (False, True):
            before = copy.deepcopy(graph)
            if wedge_only_input:
                for atom in before['atoms']:
                    atom['chiralTag'] = 0
            for degrees in (-30,30):
                out = rotate_graph(before, degrees=degrees)
                self.assert_identity(out, source)
                self.assertEqual([(b['id'],b['begin'],b['end']) for b in out['bonds']],
                                 [(b['id'],b['begin'],b['end']) for b in before['bonds']])
                for atom in out['atoms']:
                    atom['chiralTag'] = 0
                self.assert_identity(out, source)

    def test_stored_tags_do_not_mask_a_wrong_visible_wedge(self):
        graph = import_structure(export_mol(import_structure('F[C@](Cl)(Br)I')))
        original_wedge = Chem.WedgeBond
        def wrong_wedge(bond, begin, conformer):
            original_wedge(bond, begin, conformer)
            bond.SetBondDir(Chem.BondDir.BEGINDASH if bond.GetBondDir()==Chem.BondDir.BEGINWEDGE else Chem.BondDir.BEGINWEDGE)
        with patch('nivram.rotation.Chem.WedgeBond', side_effect=wrong_wedge):
            with self.assertRaisesRegex(ChemistryError, 'wedge geometry'):
                rotate_graph(graph, degrees=30)

    def test_boundary_ez_conflict_rejects_atomically(self):
        graph = import_structure('F/C=C/F.[He]')
        for atom, point in zip(graph['atoms'], [(-1,.2),(0,0),(2,0),(3,-.2),(-2,-3)]):
            atom['x'], atom['y'] = point
        before = copy.deepcopy(graph)
        with self.assertRaisesRegex(ChemistryError, 'stereo|configuration'):
            rotate_graph(graph, degrees=-30, selected=['a0','a4'])
        self.assertEqual(graph, before)
        valid = rotate_graph(graph, degrees=30, selected=['a0','a4'])
        self.assert_identity(valid, 'F/C=C/F.[He]')
        self.assertEqual(valid['atoms'][1:4], graph['atoms'][1:4])

    def test_boundary_bond_collapse_is_rejected(self):
        graph = import_structure('CC.[He]')
        for atom, point in zip(graph['atoms'], [(1,0),(math.sqrt(3)/2,.5),(-1,0)]):
            atom['x'], atom['y'] = point
        before = copy.deepcopy(graph)
        with self.assertRaisesRegex(ChemistryError, 'collapse'):
            rotate_graph(graph, degrees=30, selected=['a0','a2'])
        self.assertEqual(graph, before)

    def test_properties_unknown_stereo_and_metadata_survive(self):
        source = '[13CH3:7][C@@H:8](O)C(=O)[O-].[Na+].[CH3]'
        graph = import_structure(source)
        graph['note'] = {'fixture':True}
        before = copy.deepcopy(graph)
        out = rotate_graph(graph, degrees=30)
        self.assert_identity(out, source)
        for a,b in zip(graph['atoms'],out['atoms']):
            self.assertEqual({k:v for k,v in a.items() if k not in ('x','y')},
                             {k:v for k,v in b.items() if k not in ('x','y')})
        out['note']['fixture'] = False
        self.assertEqual(graph, before)
        mol = Chem.MolFromSmiles('FC(Cl)Br.FC=CF')
        rdDepictor.Compute2DCoords(mol)
        mol.GetBondWithIdx(0).SetBondDir(Chem.BondDir.UNKNOWN)
        next(b for b in mol.GetBonds() if b.GetBondType()==Chem.BondType.DOUBLE).SetStereo(Chem.BondStereo.STEREOANY)
        unknown = import_structure(Chem.MolToMolBlock(mol,forceV3000=True))
        rotated = rotate_graph(unknown,degrees=30)
        self.assertEqual(rotated['bonds'],unknown['bonds'])
        self.assertTrue(all(a['chiralTag']==0 for a in rotated['atoms']))

    def test_no_layout_regeneration(self):
        graph = import_structure('C[C@H](O)CC')
        with patch('rdkit.Chem.rdDepictor.Compute2DCoords',side_effect=AssertionError('No layout regeneration')):
            self.assert_identity(rotate_graph(graph,degrees=30),'C[C@H](O)CC')

    def test_invalid_options_and_coordinates_reject_without_mutation(self):
        graph = import_structure('CCO')
        before = copy.deepcopy(graph)
        for degrees in (None,True,False,'30',0,60,360,30.1,float('nan'),float('inf')):
            with self.subTest(degrees=degrees), self.assertRaises(ChemistryError):
                rotate_graph(graph,degrees=degrees)
        for selected in ('a0',['missing'],['a0','a0'],[True],[{}],set(['a0'])):
            with self.subTest(selected=selected), self.assertRaises(ChemistryError):
                rotate_graph(graph,degrees=30,selected=selected)
        self.assertEqual(graph,before)
        with self.assertRaises(ChemistryError):
            rotate_graph({'atoms':[],'bonds':[]},degrees=30)
        graph['atoms'][0]['x']=float('nan')
        with self.assertRaises(ChemistryError):
            rotate_graph(graph,degrees=30)


if __name__=='__main__':
    unittest.main()
