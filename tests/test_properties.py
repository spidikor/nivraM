import copy
import json
import unittest

from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors, rdMolDescriptors

from nivram.chemistry import ChemistryError, graph_to_mol, import_structure, mol_to_graph
from nivram.properties import calculate_properties, input_identity


class PropertyTests(unittest.TestCase):
    def test_pinned_engine_values_and_units(self):
        graph = import_structure('CCO')
        result = calculate_properties(graph, document_revision=7)
        mol = graph_to_mol(graph)
        self.assertEqual(result['status'], 'calculated')
        self.assertEqual(result['formula'], 'C2H6O')
        self.assertEqual(result['engine']['version'], rdBase.rdkitVersion)
        self.assertEqual(result['inputIdentity']['documentRevision'], 7)
        self.assertAlmostEqual(result['molecularWeight']['value'], Descriptors.MolWt(mol), places=12)
        self.assertAlmostEqual(result['exactMass']['value'], rdMolDescriptors.CalcExactMolWt(mol), places=12)
        self.assertEqual(result['molecularWeight']['unit'], 'g/mol')
        self.assertEqual(result['exactMass']['unit'], 'Da')
        self.assertAlmostEqual(result['logP']['value'], rdMolDescriptors.CalcCrippenDescriptors(mol, includeHs=True, force=True)[0], places=12)
        self.assertEqual(result['logP']['label'], 'Calculated LogP - RDKit Wildman-Crippen')
        self.assertNotEqual(result['molecularWeight']['value'], result['exactMass']['value'])
        self.assertEqual(result['transformations'], [])
        json.dumps(result, allow_nan=False)

    def test_calculation_does_not_mutate_coordinates_or_graph(self):
        graph = import_structure('[13CH3][C@@H](O)C(=O)[O-]')
        graph['atoms'][0]['x'] += 0.37
        original = copy.deepcopy(graph)
        calculate_properties(graph)
        self.assertEqual(graph, original)

    def test_explicit_implicit_hydrogen_equivalence(self):
        graph = import_structure('CCO')
        explicit = mol_to_graph(Chem.AddHs(graph_to_mol(graph), addCoords=True))
        a, b = calculate_properties(graph), calculate_properties(explicit)
        for key in ('molecularWeight', 'exactMass', 'logP'):
            self.assertAlmostEqual(a[key]['value'], b[key]['value'], places=10)

    def test_isotopes_remain_in_formula_and_mass(self):
        labeled = calculate_properties(import_structure('[13CH3]CO'))
        natural = calculate_properties(import_structure('CCO'))
        self.assertIn('13C', labeled['formula'])
        self.assertIn('[13CH3]', labeled['smiles'])
        self.assertGreater(labeled['exactMass']['value'], natural['exactMass']['value'])

    def test_charge_preserved_and_warned(self):
        result = calculate_properties(import_structure('C[NH2+]C'))
        self.assertEqual(result['formalCharge'], 1)
        self.assertIn('+', result['formula'])
        self.assertEqual(result['logP']['status'], 'calculated_with_warning')
        self.assertTrue(any('charged' in warning.lower() for warning in result['warnings']))

    def test_neutral_overall_salt_is_not_a_whole_mixture_logp(self):
        result = calculate_properties(import_structure('[Na+].[Cl-]'))
        self.assertEqual(result['formalCharge'], 0)
        self.assertEqual(result['fragmentCount'], 2)
        self.assertIsNone(result['logP']['value'])
        self.assertEqual(result['logP']['status'], 'not_applicable')
        self.assertTrue(any('fragment' in warning.lower() for warning in result['warnings']))

    def test_neutral_multicomponent_input_also_not_applicable(self):
        result = calculate_properties(import_structure('CCO.O'))
        self.assertIsNone(result['logP']['value'])
        self.assertEqual(result['fragmentCount'], 2)

    def test_radical_logp_is_explicitly_unsupported(self):
        result = calculate_properties(import_structure('[CH3]'))
        self.assertIsNone(result['logP']['value'])
        self.assertTrue(any('radical' in warning.lower() for warning in result['warnings']))

    def test_identity_distinguishes_stereo_isotope_charge_and_tautomer(self):
        for left, right in [('F/C=C/F', 'F/C=C\\F'),
                            ('C[C@H](O)F', 'C[C@@H](O)F'),
                            ('[13CH3]CO', 'CCO'), ('CN', 'C[NH3+]'),
                            ('CC=O', 'C=CO')]:
            with self.subTest(left=left, right=right):
                a = calculate_properties(import_structure(left))['inputIdentity']
                b = calculate_properties(import_structure(right))['inputIdentity']
                self.assertNotEqual(a['structureHash'], b['structureHash'])

    def test_exact_graph_identity_changes_for_coordinate_edit(self):
        graph = import_structure('CCO')
        before = calculate_properties(graph)['inputIdentity']
        graph['atoms'][0]['x'] += 0.01
        after = calculate_properties(graph)['inputIdentity']
        self.assertEqual(before['structureHash'], after['structureHash'])
        self.assertNotEqual(before['graphHash'], after['graphHash'])

    def test_empty_has_no_fabricated_numeric_properties(self):
        result = calculate_properties({'atoms': [], 'bonds': []})
        self.assertEqual(result['status'], 'empty')
        self.assertIsNone(result['exactMass']['value'])
        self.assertIsNone(result['logP']['value'])

    def test_invalid_graph_is_rejected(self):
        graph = import_structure('CCO')
        graph['atoms'][0]['x'] = float('nan')
        with self.assertRaises(ChemistryError):
            calculate_properties(graph)


if __name__ == '__main__':
    unittest.main()
