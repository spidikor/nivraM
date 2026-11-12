import unittest
from unittest.mock import patch
from rdkit import Chem
from nivram.chemistry import graph_to_mol, AtomValenceError, ChemistryError

def star(symbol='C', count=5, charge=0):
    atoms=[{'id':'source-center','symbol':symbol,'charge':charge,'x':0,'y':0}]
    atoms += [{'id':f'leaf-{i}','symbol':'C','x':i+1,'y':1} for i in range(count)]
    return {'atoms':atoms,'bonds':[{'id':f'edge-{i}','begin':'source-center','end':f'leaf-{i}','order':1} for i in range(count)]}

class ValenceFeedbackTests(unittest.TestCase):
    def test_real_carbon_and_neutral_oxygen_rejection(self):
        for symbol,count in [('C',5),('O',3)]:
            with self.subTest(symbol=symbol), self.assertRaises(AtomValenceError) as caught:
                graph_to_mol(star(symbol,count))
            self.assertEqual(caught.exception.diagnostic,{'code':'atom-valence','atomIds':['source-center']})
    def test_mapping_uses_source_ids_not_indices(self):
        graph=star();graph['atoms'].reverse()
        with self.assertRaises(AtomValenceError) as caught:graph_to_mol(graph)
        self.assertEqual(caught.exception.diagnostic['atomIds'],['source-center'])
    def test_charge_sensitive_allowed_chemistry(self):
        for symbol,count,charge in [('O',3,1),('N',4,1),('C',4,0),('O',2,0)]:
            with self.subTest(symbol=symbol,charge=charge):
                self.assertEqual(graph_to_mol(star(symbol,count,charge)).GetNumBonds(),count)
    def test_nonvalence_error_is_not_classified_by_message(self):
        with patch.object(Chem,'SanitizeMol',side_effect=ValueError('Explicit valence for atom # 0 C, 5')):
            with self.assertRaises(ChemistryError) as caught:graph_to_mol(star(count=4))
        self.assertNotIsInstance(caught.exception,AtomValenceError)
        self.assertFalse(hasattr(caught.exception,'diagnostic'))
    def test_duplicate_id_has_no_valence_diagnostic(self):
        graph=star();graph['atoms'][1]['id']='source-center'
        with self.assertRaises(ChemistryError) as caught:graph_to_mol(graph)
        self.assertFalse(hasattr(caught.exception,'diagnostic'))

if __name__=='__main__':unittest.main()
