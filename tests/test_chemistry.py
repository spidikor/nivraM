import unittest, copy, math
from rdkit import Chem
from rdkit.Chem import rdDepictor
from nivram.chemistry import import_structure, graph_to_mol, export_mol, clean_graph, ChemistryError

class ChemistryTests(unittest.TestCase):
    def test_explicit_unknown_stereo_survives_mol_import_and_export(self):
        source=Chem.MolFromSmiles('FC(Cl)Br')
        rdDepictor.Compute2DCoords(source)
        source.GetBondWithIdx(0).SetBondDir(Chem.BondDir.UNKNOWN)
        for v3000 in (False,True):
            with self.subTest(v3000=v3000):
                graph=import_structure(Chem.MolToMolBlock(source,forceV3000=v3000))
                self.assertEqual(graph['bonds'][0]['direction'],int(Chem.BondDir.UNKNOWN))
                again=import_structure(export_mol(graph))
                self.assertEqual(again['bonds'][0]['direction'],int(Chem.BondDir.UNKNOWN))
                self.assertTrue(all(atom['chiralTag']==0 for atom in again['atoms']))

    def test_import_smiles(self):
        g=import_structure('CCO')
        self.assertEqual(len(g['atoms']),3)
        self.assertEqual(len(g['bonds']),2)
        self.assertTrue(all(math.isfinite(a['x']) and math.isfinite(a['y']) for a in g['atoms']))
    def test_invalid_smiles(self):
        with self.assertRaises(ChemistryError): import_structure('not a molecule')
    def test_stereo_charge_isotope_roundtrip(self):
        for s in ['[13CH3][C@@H](O)C(=O)[O-]','F/C=C/F','F/C=C\\F','C[NH2+]C','[Na+].[Cl-]','c1cc[nH]c1','[CH3]']:
            with self.subTest(s=s):
                g=import_structure(s)
                expected=Chem.MolToSmiles(Chem.MolFromSmiles(s),isomericSmiles=True)
                actual=Chem.MolToSmiles(graph_to_mol(g),isomericSmiles=True)
                self.assertEqual(actual, expected)
                rebuilt=Chem.MolFromMolBlock(export_mol(g), removeHs=False)
                self.assertIsNotNone(rebuilt)
                self.assertEqual(Chem.MolToSmiles(rebuilt,isomericSmiles=True),expected)
    def test_export_does_not_clean_or_mutate(self):
        g=import_structure('CCCO'); g['atoms'][0]['x']+=0.22
        before=copy.deepcopy(g); export_mol(g)
        self.assertEqual(g,before)
        mol=Chem.MolFromMolBlock(export_mol(g),removeHs=False)
        for i,a in enumerate(g['atoms']):
            p=mol.GetConformer().GetAtomPosition(i)
            self.assertAlmostEqual(a['x'],p.x,places=4)
            self.assertAlmostEqual(a['y'],p.y,places=4)
    def test_clean_preserves_chemistry(self):
        g=import_structure('[13CH3][C@@H](O)C(=O)[O-]')
        g['atoms'][0]['x']+=0.6
        expected=Chem.MolToSmiles(graph_to_mol(g),isomericSmiles=True)
        cleaned=clean_graph(g,[])
        self.assertEqual(Chem.MolToSmiles(graph_to_mol(cleaned),isomericSmiles=True),expected)
        self.assertEqual([a['id'] for a in g['atoms']],[a['id'] for a in cleaned['atoms']])
    def test_partial_clean_preserves_unselected(self):
        g=import_structure('CCCO.C1CCCCC1'); chosen=[a['id'] for a in g['atoms'][:4]]
        g['atoms'][0]['x']+=0.3
        before=copy.deepcopy(g)
        out=clean_graph(g,chosen)
        for a,b in zip(before['atoms'],out['atoms']):
            if a['id'] not in chosen:self.assertEqual((a['x'],a['y']),(b['x'],b['y']))
    def test_reject_nonfinite(self):
        g=import_structure('CCO');g['atoms'][0]['x']=float('nan')
        with self.assertRaises(ChemistryError):graph_to_mol(g)
    def test_reject_duplicate_ids(self):
        g=import_structure('CCO');g['atoms'][1]['id']=g['atoms'][0]['id']
        with self.assertRaises(ChemistryError):graph_to_mol(g)
    def test_reject_invalid_bond_endpoint(self):
        g=import_structure('CCO');g['bonds'][0]['end']='missing'
        with self.assertRaises(ChemistryError):graph_to_mol(g)
    def test_reject_invalid_valence(self):
        with self.assertRaises(ChemistryError):import_structure('CO(C)C')
    def test_empty_clean_error(self):
        with self.assertRaises(ChemistryError):clean_graph({'atoms':[],'bonds':[]},[])
    def test_reject_unknown_selection(self):
        with self.assertRaises(ChemistryError):clean_graph(import_structure('CCO'),['missing'])
if __name__=='__main__':unittest.main()
