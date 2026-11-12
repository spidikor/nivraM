import unittest,copy
from rdkit import Chem
from rdkit.Chem import rdDepictor
from nivram.chemistry import import_structure,graph_to_mol,ChemistryError,clean_graph

class LimitTests(unittest.TestCase):
    def test_flags_require_real_booleans(self):
        for prop in ['noImplicit','aromatic']:
            g=import_structure('CCO');g['atoms'][0][prop]='false'
            with self.subTest(prop=prop):
                with self.assertRaises(ChemistryError):graph_to_mol(g)
    def test_wildcard_rejected(self):
        with self.assertRaises(ChemistryError):import_structure('C*')
    def test_coordinate_bond_rejected(self):
        with self.assertRaises(ChemistryError):import_structure('N->[Cu]')
    def test_reaction_rejected(self):
        with self.assertRaises(ChemistryError):import_structure('CCO>>CC=O')
    def test_3d_rejected(self):
        m=Chem.MolFromSmiles('CCO');rdDepictor.Compute2DCoords(m);m.GetConformer().Set3D(True)
        with self.assertRaises(ChemistryError):import_structure(Chem.MolToMolBlock(m))
    def test_atom_limit(self):
        with self.assertRaises(ChemistryError):import_structure('.'.join(['C']*513))
    def test_full_clean_changes_distorted_coordinates(self):
        g=import_structure('CCCO');g['atoms'][0]['y']+=1.4;before=copy.deepcopy(g)
        out=clean_graph(g,[])
        self.assertNotEqual([(a['x'],a['y']) for a in out['atoms']],[(a['x'],a['y']) for a in before['atoms']])
        self.assertEqual(g,before)
        self.assertEqual(Chem.MolToSmiles(graph_to_mol(g)),Chem.MolToSmiles(graph_to_mol(out)))
    def test_ez_clean_identity(self):
        for s in ['F/C=C/F','F/C=C\\F']:
            g=import_structure(s);out=clean_graph(g,[])
            self.assertEqual(Chem.MolToSmiles(graph_to_mol(g),True),Chem.MolToSmiles(graph_to_mol(out),True))
