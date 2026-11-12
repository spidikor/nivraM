"""Bounded controlled-engine regressions; no NISPO inference or Java execution."""
import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nivram.chemistry import import_structure
from nivram.naming import OpsinConfig, name_structure, validate_name_round_trip
from nivram.worker_processes import CancellationToken, WorkerCancelled, WorkerOutputLimit, WorkerSupervisor

ENGINE = {'name': 'OPSIN', 'version': '2.9.0'}
CASES = [('[13CH3]CO', True), ('[CH3]', True), ('CCO', False)]


class RequiredVerificationTests(unittest.TestCase):
    def run_case(self, smiles, *, parsed=None, error=None, config=True, token=None):
        graph = import_structure(smiles)
        before = copy.deepcopy(graph)
        generated = {'status': 'generated', 'name': 'candidate', 'engine': {'name': 'NISPO', 'version': '0.1.7'}}
        with patch('nivram.naming._run_nispo', return_value=generated), patch(
                'nivram.naming._run_opsin', return_value=parsed, side_effect=error):
            result = name_structure(graph, document_revision=17,
                                    opsin_config=object() if config else None,
                                    cancellation_token=token)
        self.assertEqual(graph, before)
        self.assertEqual(result['inputIdentity']['documentRevision'], 17)
        self.assertFalse(result['preferredIUPACName'])
        self.assertEqual(result['transformations'], [])
        return result

    def assert_suppressed(self, result):
        self.assertIsNone(result['name'])
        self.assertIsNone(result['generatedName'])
        self.assertNotIn('candidate', str(result))
        self.assertTrue(any('verification' in w and 'no unverified name' in w for w in result['warnings']))

    def test_nonmatched_verifier_statuses(self):
        for smiles, mandatory in CASES:
            for status in ('unavailable', 'warning', 'failed', 'unexpected'):
                with self.subTest(smiles=smiles, status=status):
                    parsed = {'status': status, 'smiles': smiles, 'warnings': ['diagnostic retained'], 'engine': ENGINE}
                    result = self.run_case(smiles, parsed=parsed)
                    self.assertEqual(result['roundTrip'], parsed)
                    self.assertIn('diagnostic retained', result['warnings'])
                    self.assertEqual(result['status'], 'unsupported' if mandatory else 'generated_unverified')
                    if mandatory:
                        self.assert_suppressed(result)
                    else:
                        self.assertEqual(result['name'], 'candidate')

    def test_invalid_valid_different_and_exact_chemistry(self):
        for smiles, mandatory in CASES:
            for parsed_smiles, status in [('not a SMILES', 'failed'), ('CO', 'mismatch'), (smiles, 'matched')]:
                with self.subTest(smiles=smiles, status=status):
                    result = self.run_case(smiles, parsed={'status': 'parsed', 'smiles': parsed_smiles, 'engine': ENGINE})
                    self.assertEqual(result['roundTrip']['status'], status)
                    expected = 'round_trip_matched' if status == 'matched' else 'mismatch' if status == 'mismatch' else 'unsupported' if mandatory else 'generated_unverified'
                    self.assertEqual(result['status'], expected)
                    if mandatory and status != 'matched':
                        self.assert_suppressed(result)
                    else:
                        self.assertEqual(result['generatedName'], 'candidate')

    def test_errors_after_candidate_generation(self):
        errors = [(OSError(), 'failed'), (ValueError(), 'failed'), (TypeError(), 'failed'),
                  (KeyError(), 'failed'), (WorkerOutputLimit(), 'failed'),
                  (subprocess.TimeoutExpired('controlled', 1), 'timeout'), (WorkerCancelled(), 'cancelled')]
        for smiles, mandatory in CASES:
            for error, status in errors:
                with self.subTest(smiles=smiles, error=type(error).__name__):
                    result = self.run_case(smiles, error=error)
                    self.assertEqual(result['status'], status)
                    self.assertIsNone(result['name'])
                    if mandatory:
                        self.assert_suppressed(result)
                    else:
                        self.assertEqual(result['generatedName'], None if status == 'cancelled' else 'candidate')

    def test_no_configuration_and_pre_cancel(self):
        for smiles, mandatory in CASES:
            result = self.run_case(smiles, config=False)
            self.assertEqual(result['status'], 'unsupported' if mandatory else 'generated_unverified')
            if mandatory:
                self.assert_suppressed(result)
            token = CancellationToken(); token.cancel()
            result = self.run_case(smiles, token=token)
            self.assertEqual(result['status'], 'cancelled')
            self.assertIsNone(result['generatedName'])

    def test_cancel_after_matching_dominates(self):
        for smiles, mandatory in CASES:
            token = CancellationToken()
            def compare(graph, parsed):
                result = validate_name_round_trip(graph, parsed)
                token.cancel()
                return result
            with patch('nivram.naming.validate_name_round_trip', side_effect=compare):
                result = self.run_case(smiles, parsed={'status': 'parsed', 'smiles': smiles, 'engine': ENGINE}, token=token)
            self.assertEqual(result['status'], 'cancelled')
            self.assertIsNone(result['name'])
            self.assertIsNone(result['generatedName'])

    def test_generation_failures_and_malformed_verifier_are_suppressed(self):
        for smiles, mandatory in CASES:
            for status in ('unavailable', 'unsupported', 'failed'):
                with self.subTest(smiles=smiles, status=status), patch(
                        'nivram.naming._run_nispo', return_value={'status': status, 'message': 'generation diagnostic'}):
                    result = name_structure(import_structure(smiles), opsin_config=object())
                self.assertEqual(result['status'], status)
                self.assertIsNone(result['name'])
                self.assertIsNone(result['generatedName'])
            for malformed in ({}, {'smiles': smiles}):
                result = self.run_case(smiles, parsed=malformed)
                self.assertEqual(result['status'], 'failed')
                if mandatory: self.assert_suppressed(result)
            for name in ('', 'bad\nname', 'x' * 16385, None):
                with patch('nivram.naming._run_nispo', return_value={'status':'generated', 'name': name}):
                    result = name_structure(import_structure(smiles), opsin_config=object())
                self.assertEqual(result['status'], 'failed')
                self.assertIsNone(result['name'])
                self.assertIsNone(result['generatedName'])

    def test_snapshot_identity_and_cancel_during_verification(self):
        for smiles, mandatory in CASES:
            graph = import_structure(smiles)
            before = copy.deepcopy(graph)
            def generate(*args):
                graph['atoms'][0]['symbol'] = 'N'
                return {'status': 'generated', 'name': 'candidate'}
            with patch('nivram.naming._run_nispo', side_effect=generate), patch(
                    'nivram.naming._run_opsin', return_value={'status':'parsed', 'smiles':smiles, 'engine':ENGINE}):
                result = name_structure(graph, document_revision=19, opsin_config=object())
            self.assertEqual(result['status'], 'round_trip_matched')
            self.assertEqual(result['inputIdentity']['documentRevision'], 19)
            self.assertNotEqual(graph, before)
            token = CancellationToken()
            def cancelled(*args):
                token.cancel()
                return {'status':'parsed', 'smiles':smiles, 'engine':ENGINE}
            result = self.run_case(smiles, error=cancelled, token=token)
            self.assertEqual(result['status'], 'cancelled')
            self.assertIsNone(result['name'])
            self.assertIsNone(result['generatedName'])

    def test_actual_missing_paths_checksum_and_controlled_process_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            java, jar = root/'java', root/'opsin.jar'
            scenarios = [('missing', OpsinConfig(java, jar), None)]
            for smiles, mandatory in CASES:
                with patch('nivram.naming._run_nispo', return_value={'status':'generated','name':'candidate'}):
                    result = name_structure(import_structure(smiles), opsin_config=scenarios[0][1])
                self.assertEqual(result['roundTrip']['status'], 'unavailable')
                if mandatory: self.assert_suppressed(result)
            java.write_text('controlled placeholder, never executed'); jar.write_text('controlled archive')
            cases = [('checksum', OpsinConfig(java, jar, '0'*64), [], 'failed'),
                     ('version', OpsinConfig(java, jar), [SimpleNamespace(returncode=0, stdout='opsin-1.0.0-help', stderr='')], 'unavailable'),
                     ('multi', OpsinConfig(java, jar), [SimpleNamespace(returncode=0, stdout='opsin-2.9.0-help', stderr=''), SimpleNamespace(returncode=0, stdout='C\nC\n', stderr='')], 'failed'),
                     ('empty', OpsinConfig(java, jar), [SimpleNamespace(returncode=0, stdout='opsin-2.9.0-help', stderr=''), SimpleNamespace(returncode=0, stdout='', stderr='')], 'failed')]
            for smiles, mandatory in CASES:
                for label, config, outputs, status in cases:
                    with self.subTest(smiles=smiles, case=label), patch('nivram.naming._run_nispo', return_value={'status':'generated','name':'candidate'}), patch.object(WorkerSupervisor, 'run', side_effect=outputs):
                        result = name_structure(import_structure(smiles), opsin_config=config)
                    self.assertEqual(result['roundTrip']['status'], status)
                    if mandatory: self.assert_suppressed(result)
                    else: self.assertEqual(result['name'], 'candidate')


if __name__ == '__main__':
    unittest.main()
