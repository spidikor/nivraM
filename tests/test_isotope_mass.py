"""Explicit isotope mass-table availability, independent of nuclide existence."""
import copy
import http.client
import json
import threading
import unittest
from unittest.mock import patch
from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors
from nivram.chemistry import import_structure, graph_to_mol, ChemistryError
from nivram.properties import calculate_properties, input_identity
from nivram.mass_contract import MASS_MESSAGE, valid_mass_unavailable
from nivram.import_protocol import validate_prepared_result
from nivram.comparison import compare_request
from nivram.scene import DEFAULT_STYLE
from nivram.server import make_server


def prepared(smiles='[400CH4]'):
    # Controlled protocol fixture, NOT a rendered image or substitute font.
    graph = import_structure(smiles)
    props = calculate_properties(graph)
    mass = props['molecularWeight']['value']
    scene = {'svg': '<svg></svg>', 'positions': [dict(id=a['id'], x=0, y=0) for a in graph['atoms']],
             'transform': dict(x0=0, y0=0, xScale=60, yScale=-60), 'width': 300, 'height': 220,
             'formula': props['formula'], 'smiles': props['smiles'], 'mass': None if mass is None else round(mass, 3),
             'properties': props, 'style': dict(DEFAULT_STYLE)}
    return dict(graph=graph, scene=scene)


class IsotopeMassTests(unittest.TestCase):
    def test_original_reproducer_and_independent_table_sentinel(self):
        table = Chem.GetPeriodicTable()
        for smiles, z, isotope in [('[400CH4]', 6, 400), ('[1CH4]', 6, 1), ('[400Na+]', 11, 400), ('[268Db]', 105, 268)]:
            with self.subTest(smiles=smiles):
                self.assertEqual(table.GetMassForIsotope(z, isotope), 0)
                graph = import_structure(smiles)
                self.assertGreater(Descriptors.MolWt(graph_to_mol(graph)), 0)  # old fallback is numeric
                result = calculate_properties(graph)
                self.assertIsNone(result['molecularWeight']['value'])
                self.assertIsNone(result['exactMass']['value'])
                self.assertTrue(valid_mass_unavailable(result['massUnavailable'], graph))
                self.assertIn(MASS_MESSAGE, result['warnings'])
                self.assertNotIn(MASS_MESSAGE, result['logP']['warnings'])
                json.dumps(result, allow_nan=False)

    def test_supported_and_unlabeled_controls_keep_engine_values(self):
        for smiles in ['[13CH4]', '[2H]O[3H]', '[99Tc]', '[13CH3][NH3+]', 'CCO', '[Na+].[Cl-]', '[Db]', '[Sg]', '[Bh]', '[Hs]', '[Mt]']:
            with self.subTest(smiles=smiles):
                graph = import_structure(smiles); mol = graph_to_mol(graph)
                result = calculate_properties(graph)
                self.assertNotIn('massUnavailable', result)
                self.assertEqual(result['molecularWeight']['value'], Descriptors.MolWt(mol))
                self.assertEqual(result['exactMass']['value'], rdMolDescriptors.CalcExactMolWt(mol))
        self.assertAlmostEqual(calculate_properties(import_structure('[13CH4]'))['exactMass']['value'], 17.034654968, places=9)
        self.assertAlmostEqual(calculate_properties(import_structure('[Db]'))['exactMass']['value'], 268.12545, places=8)

    def test_charge_fragments_stereo_and_coordinates_are_not_changed(self):
        for smiles in ['[400CH3][C@@H](O)C(=O)[O-]', '[400Na+].[Cl-]', '[400CH3]', '[1CH4].[400CH4].[400CH4].[400Na+]']:
            graph = import_structure(smiles); graph['atoms'][0]['x'] += .123
            before = copy.deepcopy(graph); result = calculate_properties(graph, 19)
            self.assertEqual(graph, before)
            self.assertEqual(result['inputIdentity'], input_identity(graph, document_revision=19))
            self.assertEqual(result['formula'], rdMolDescriptors.CalcMolFormula(graph_to_mol(graph), separateIsotopes=True, abbreviateHIsotopes=False))
            self.assertEqual(result['transformations'], [])
            self.assertEqual(result['warnings'][:-1], result['logP']['warnings'])
            self.assertEqual(result['warnings'][-1], MASS_MESSAGE)
        self.assertEqual(len(result['massUnavailable']['isotopes']), 3)
        self.assertIsNone(result['logP']['value'])
        self.assertEqual(calculate_properties(import_structure('[400CH4]'))['logP'], calculate_properties(import_structure('C'))['logP'])

    def test_bounds_empty_many_labels_and_no_descriptor_fallback(self):
        empty = calculate_properties({'atoms': [], 'bonds': []})
        self.assertNotIn('massUnavailable', empty); self.assertEqual(empty['status'], 'empty')
        for isotope in [-1, 401, True, 1.5]:
            graph = import_structure('C'); graph['atoms'][0]['isotope'] = isotope
            with self.assertRaises(ChemistryError): calculate_properties(graph)
        graph = import_structure('.'.join(f'[{i}CH4]' for i in range(300, 401)))
        result = calculate_properties(graph)
        self.assertEqual(len(result['massUnavailable']['isotopes']), 101)
        self.assertLess(len(result['warnings']), 20)
        self.assertTrue(all(len(w) <= 2000 for w in result['warnings']))
        with patch('nivram.properties.Descriptors.MolWt', side_effect=AssertionError('fallback called')), patch('nivram.properties.rdMolDescriptors.CalcExactMolWt', side_effect=AssertionError('fallback called')):
            calculate_properties(import_structure('[400CH4]'))

    def test_protocol_nulls_require_complete_graph_bound_metadata(self):
        value = prepared(); self.assertIs(validate_prepared_result(value, mode='import'), value)
        mutations = [lambda p:p['scene']['properties'].pop('massUnavailable'),
                     lambda p:p['scene']['properties']['massUnavailable'].update(reason='unknown'),
                     lambda p:p['scene']['properties']['massUnavailable'].update(message='arbitrary'),
                     lambda p:p['scene']['properties']['massUnavailable'].update(isotopes=[]),
                     lambda p:p['scene']['properties']['massUnavailable']['isotopes'][0].update(isotope=399),
                     lambda p:p['scene']['properties']['massUnavailable']['isotopes'][0].update(isotope=True),
                     lambda p:p['scene']['properties']['massUnavailable']['isotopes'].append({'symbol':'C','isotope':400}),
                     lambda p:p['scene']['properties']['exactMass'].update(value=400),
                     lambda p:p['scene'].update(mass=400),
                     lambda p:p['scene']['properties']['inputIdentity'].update(graphHash='0'*64)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                bad = copy.deepcopy(value); mutate(bad)
                with self.assertRaises(ValueError): validate_prepared_result(bad, mode='import')
        normal = prepared('C'); validate_prepared_result(normal, mode='import')
        normal['scene']['mass'] = None
        with self.assertRaises(ValueError): validate_prepared_result(normal, mode='import')
        value['project'] = {'schema': 'nivram/1', 'graph': value['graph'], 'style': value['scene']['style']}
        validate_prepared_result(value, mode='batch')

    def test_comparison_each_side_and_both_keep_availability(self):
        for left,right in [('[400CH4]','C'),('C','[400Na+]'),('[400CH4]','[1CH4]')]:
            request = dict(requestId='a'*32, pinId='b'*32, documentRevision=8,
                           pinned=dict(graph=import_structure(left), style=dict(DEFAULT_STYLE)),
                           current=dict(graph=import_structure(right), style=dict(DEFAULT_STYLE)))
            before = copy.deepcopy(request)
            result = compare_request(request)  # actual renderer; Arial failure is an explicit unavailable preview
            self.assertEqual(request, before); self.assertEqual(result['schema'], 'nivram-comparison/2')
            for side in ('pinned', 'current'):
                expected = calculate_properties(request[side]['graph'], 8)
                self.assertEqual(result[side]['molecularWeight'], expected['molecularWeight']['value'])
                self.assertEqual(result[side]['exactMass'], expected['exactMass']['value'])
                self.assertEqual(result[side].get('massUnavailable'), expected.get('massUnavailable'))
                self.assertEqual(result[side]['identity'], expected['inputIdentity'])


class IsotopeMassHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(timeout=3)
    def post(self, path, body):
        port = self.server.server_port
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        connection.request('POST', path, json.dumps(body), {'Content-Type':'application/json', 'Origin':f'http://127.0.0.1:{port}', 'X-Nivram-Token':self.server.api_token})
        response = connection.getresponse(); result = json.loads(response.read()); connection.close()
        self.assertEqual(response.status, 200, result); return result
    def test_property_and_comparison_routes(self):
        graph = import_structure('[400CH4]'); before = copy.deepcopy(graph)
        result = self.post('/api/properties', dict(graph=graph, documentRevision=23))
        self.assertIsNone(result['exactMass']['value']); self.assertEqual(result['inputIdentity']['documentRevision'], 23)
        result = self.post('/api/comparison', dict(requestId='a'*32, pinId='b'*32, documentRevision=23,
                           pinned=dict(graph=graph, style=dict(DEFAULT_STYLE)), current=dict(graph=import_structure('[13CH4]'), style=dict(DEFAULT_STYLE))))
        self.assertIsNone(result['pinned']['molecularWeight']); self.assertGreater(result['current']['exactMass'], 17)
        self.assertEqual(graph, before)

if __name__ == '__main__': unittest.main()
