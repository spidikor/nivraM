import copy
import subprocess
import unittest
from unittest.mock import patch

from nivram.chemistry import import_structure
from nivram.naming import name_structure, validate_name_round_trip


def generated(name='ethanol'):
    return {'status': 'generated', 'name': name,
            'engine': {'name': 'NISPO', 'version': '0.1.7'}}


class NamingTests(unittest.TestCase):
    def test_generated_name_is_unverified_without_parser(self):
        with patch('nivram.naming._run_nispo', return_value=generated()):
            result = name_structure(import_structure('CCO'), document_revision=3)
        self.assertEqual(result['status'], 'generated_unverified')
        self.assertEqual(result['name'], 'ethanol')
        self.assertFalse(result['preferredIUPACName'])
        self.assertEqual(result['roundTrip']['status'], 'unavailable')
        self.assertEqual(result['inputIdentity']['documentRevision'], 3)

    def test_empty_name_is_not_success(self):
        with patch('nivram.naming._run_nispo', return_value=generated('  ')):
            result = name_structure(import_structure('CCO'))
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['name'])

    def test_observed_isotope_and_radical_failures_need_verification(self):
        for smiles, incorrect in [('[13CH3]CO', 'ethanol'), ('[CH3]', 'carbon')]:
            with self.subTest(smiles=smiles), \
                 patch('nivram.naming._run_nispo', return_value=generated(incorrect)):
                result = name_structure(import_structure(smiles))
            self.assertEqual(result['status'], 'unsupported')
            self.assertIsNone(result['name'])
            self.assertTrue(result['warnings'])

    def test_missing_dependency_is_honest_and_does_not_mutate(self):
        graph = import_structure('C[C@@H](O)C(=O)[O-]')
        before = copy.deepcopy(graph)
        with patch('nivram.naming._run_nispo', return_value={
                'status': 'unavailable', 'name': None,
                'engine': {'name': 'NISPO', 'version': None},
                'message': 'NISPO is not installed.'}):
            result = name_structure(graph)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['name'])
        self.assertEqual(graph, before)

    def test_timeout_is_separate_from_failed_generation(self):
        with patch('nivram.naming._run_nispo', side_effect=subprocess.TimeoutExpired(['worker'], 1)):
            result = name_structure(import_structure('CCO'), timeout_seconds=1)
        self.assertEqual(result['status'], 'timeout')
        self.assertIsNone(result['name'])

    def test_round_trip_matches_full_salt_and_preserves_input(self):
        graph = import_structure('[Na+].[Cl-]')
        before = copy.deepcopy(graph)
        result = validate_name_round_trip(graph, '[Cl-].[Na+]')
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(graph, before)

    def test_round_trip_distinguishes_stereo_isotope_charge_tautomer_and_fragments(self):
        for original, parsed in [('F/C=C/F', 'F/C=C\\F'),
                                 ('C[C@H](O)F', 'C[C@@H](O)F'),
                                 ('C[C@H](O)F', 'CC(O)F'),
                                 ('[13CH3]CO', 'CCO'),
                                 ('C[NH3+]', 'CN'),
                                 ('CC=O', 'C=CO'),
                                 ('CCO.O', 'CCO')]:
            with self.subTest(original=original, parsed=parsed):
                self.assertEqual(validate_name_round_trip(import_structure(original), parsed)['status'], 'mismatch')

    def test_invalid_parser_structure_is_not_a_match(self):
        self.assertEqual(validate_name_round_trip(import_structure('CCO'), 'not a SMILES')['status'], 'failed')

    def test_parser_output_cannot_hide_trailing_content_as_a_name(self):
        self.assertEqual(validate_name_round_trip(import_structure('CCO'), 'CCO unexpected-content')['status'], 'failed')

    def test_atom_maps_are_reported_as_outside_naming_semantics(self):
        result = validate_name_round_trip(import_structure('[CH3:7]CO'), 'CCO')
        self.assertEqual(result['status'], 'matched')
        self.assertTrue(any('map' in item.lower() for item in result['warnings']))

    def test_parser_warning_never_becomes_verified_name(self):
        with patch('nivram.naming._run_nispo', return_value=generated()), \
             patch('nivram.naming._run_opsin', return_value={
                 'status': 'warning', 'smiles': 'CCO', 'warnings': ['Ambiguous name.'],
                 'engine': {'name': 'OPSIN', 'version': '2.9.0'}}):
            result = name_structure(import_structure('CCO'), opsin_config=object())
        self.assertEqual(result['status'], 'generated_unverified')
        self.assertEqual(result['roundTrip']['status'], 'warning')

    def test_mismatched_parser_structure_is_rejected(self):
        with patch('nivram.naming._run_nispo', return_value=generated()), \
             patch('nivram.naming._run_opsin', return_value={
                 'status': 'parsed', 'smiles': 'CO', 'warnings': [],
                 'engine': {'name': 'OPSIN', 'version': '2.9.0'}}):
            result = name_structure(import_structure('CCO'), opsin_config=object())
        self.assertEqual(result['status'], 'mismatch')
        self.assertIsNone(result['name'])
        self.assertEqual(result['generatedName'], 'ethanol')

    def test_round_trip_match_is_not_a_pin_claim(self):
        with patch('nivram.naming._run_nispo', return_value=generated()), \
             patch('nivram.naming._run_opsin', return_value={
                 'status': 'parsed', 'smiles': 'CCO', 'warnings': [],
                 'engine': {'name': 'OPSIN', 'version': '2.9.0'}}):
            result = name_structure(import_structure('CCO'), opsin_config=object())
        self.assertEqual(result['status'], 'round_trip_matched')
        self.assertEqual(result['roundTrip']['status'], 'matched')
        self.assertFalse(result['preferredIUPACName'])

    def test_multiline_name_cannot_inject_second_cli_request(self):
        with patch('nivram.naming._run_nispo', return_value=generated('ethanol\nmethanol')):
            result = name_structure(import_structure('CCO'))
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['name'])

    def test_late_naming_result_belongs_to_its_original_snapshot(self):
        graph = import_structure('CCO')
        def mutate_during_job(*args):
            graph['atoms'][2]['symbol'] = 'N'
            return generated()
        with patch('nivram.naming._run_nispo', side_effect=mutate_during_job), \
             patch('nivram.naming._run_opsin', return_value={
                 'status': 'parsed', 'smiles': 'CCO', 'warnings': [],
                 'engine': {'name': 'OPSIN', 'version': '2.9.0'}}):
            result = name_structure(graph, opsin_config=object())
        self.assertEqual(result['status'], 'round_trip_matched')
        self.assertEqual(result['inputIdentity']['smiles'], 'CCO')


if __name__ == '__main__':
    unittest.main()
