import copy
import json
import unittest
from unittest.mock import patch
from rdkit import Chem
from nivram.chemistry import import_structure, graph_to_mol, ChemistryError
from nivram.stereo_inspector import inspect_stereochemistry

class StereoInspectorTests(unittest.TestCase):
    def inspect(self, smiles):
        graph=import_structure(smiles); before=copy.deepcopy(graph)
        result=inspect_stereochemistry(graph,7)
        self.assertEqual(graph,before)
        self.assertEqual(result['inputIdentity']['documentRevision'],7)
        self.assertEqual(result['transformations'],[])
        json.dumps(result,allow_nan=False)
        return result
    def test_known_enantiomers(self):
        for smiles,label in [('C[C@H](O)F','R'),('C[C@@H](O)F','S')]:
            with self.subTest(smiles=smiles):
                result=self.inspect(smiles); self.assertEqual([(s['id'],s['label'],s['status'])for s in result['sites']],[('a1',label,'assigned')])
    def test_known_alkene_pair(self):
        for smiles,label in [('F/C=C/F','E'),('F/C=C\\F','Z')]:
            with self.subTest(smiles=smiles):
                self.assertEqual([(s['id'],s['label'])for s in self.inspect(smiles)['sites']],[('b1',label)])
    def test_unspecified_never_assigned(self):
        for smiles,kind in [('CC(O)F','atom'),('FC=CF','bond')]:
            with self.subTest(smiles=smiles):
                result=self.inspect(smiles); self.assertEqual(len(result['sites']),1)
                self.assertEqual(result['sites'][0]['status'],'unresolved');self.assertIsNone(result['sites'][0]['label']);self.assertEqual(result['sites'][0]['kind'],kind)
    def test_cumulenes_are_not_reported_as_ordinary_ez(self):
        result=self.inspect('CC=C=C(C)F')
        self.assertEqual(len(result['sites']),2)
        self.assertTrue(all(s['status']=='unsupported' and s['label'] is None and s['reason']=='unsupported_type' for s in result['sites']))
    def test_achiral_and_empty(self):
        self.assertEqual(self.inspect('CC(O)C')['sites'],[])
        self.assertEqual(inspect_stereochemistry({'atoms':[],'bonds':[]},0)['status'],'empty')
    def test_isotope_priority(self):
        self.assertEqual(self.inspect('[2H][C@](F)(Cl)Br')['sites'][0]['label'],'S')
        self.assertEqual(len(self.inspect('[2H]C(F)Cl')['sites']),1)
    def test_multiple_disconnected_and_mixed(self):
        result=self.inspect('C[C@H](O)F.FC=CF.CCO')
        self.assertEqual([s['status']for s in result['sites']],['assigned','unresolved'])
    def test_ids_and_order_retained(self):
        graph=import_structure('C[C@H](O)F'); ids={a['id']:'custom-'+a['id']for a in graph['atoms']}
        for a in graph['atoms']: a['id']=ids[a['id']]
        for b in graph['bonds']: b['begin']=ids[b['begin']];b['end']=ids[b['end']];b['stereoAtoms']=[ids[x]for x in b['stereoAtoms']]
        self.assertEqual(inspect_stereochemistry(graph)['sites'][0]['id'],'custom-a1')
    def test_unknown_double(self):
        graph=import_structure('FC=CF');graph['bonds'][1]['stereo']=1
        site=inspect_stereochemistry(graph)['sites'][0]
        self.assertEqual(site['status'],'unresolved');self.assertEqual(site['reason'],'unknown');self.assertIsNone(site['label'])
    def test_map_labels_do_not_make_identical_ligands_different(self):
        self.assertEqual(self.inspect('[CH3:1]C(O)[CH3:2]')['sites'],[])
    def test_invalid_input_and_revision_reject(self):
        graph=import_structure('CCO');graph['atoms'][0]['x']=float('nan')
        with self.assertRaises(ChemistryError):inspect_stereochemistry(graph)
        with self.assertRaises(ValueError):inspect_stereochemistry(import_structure('CC'),True)
    def test_cip_limit_failure_is_not_empty_success(self):
        with patch('nivram.stereo_inspector.rdCIPLabeler.AssignCIPLabels',side_effect=RuntimeError('limit')):
            with self.assertRaisesRegex(ChemistryError,'could not complete'):self.inspect('C[C@H](O)F')
    def test_cleaned_away_declaration_warned(self):
        graph=import_structure('CC(O)C');graph['atoms'][1]['chiralTag']=1
        result=inspect_stereochemistry(graph)
        self.assertTrue(any('not retained' in w for w in result['warnings']))

if __name__=='__main__':unittest.main()
