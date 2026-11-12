import io
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from nivram import optical_validation as validation
from nivram.worker_processes import CancellationToken, WorkerCancelled


INPUT_DIGEST = 'a' * 64
CO_MOL = '''CO
     RDKit          2D

  2  1  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.5000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0  0  0  0
M  END
'''


def prediction(**changes):
    return {'schemaVersion': 1, 'method': 'MolScribe', 'modelSha256': validation.MODEL_SHA256,
            'inputSha256': INPUT_DIGEST, 'smiles': 'CO', 'molfile': CO_MOL,
            'confidence': 0.75, **changes}


def encoded_prediction(**changes):
    return json.dumps(prediction(**changes), ensure_ascii=False).encode('utf-8')


def candidate_fixture():
    atoms = [{'id': 'optical-1-a0', 'symbol': 'C', 'x': 0.0, 'y': 0.0},
             {'id': 'optical-1-a1', 'symbol': 'O', 'x': 1.5, 'y': 0.0}]
    for atom in atoms:
        atom.update(charge=0, isotope=0, explicitHs=0, noImplicit=False,
                    aromatic=False, chiralTag=0, radicals=0, mapNum=0)
    return {'id': 'optical-1', 'status': 'supported', 'kind': 'optical_candidate',
        'graph': {'atoms': atoms, 'bonds': [{'id': 'optical-1-b0', 'begin': 'optical-1-a0',
            'end': 'optical-1-a1', 'order': 1.0, 'direction': 0, 'stereo': 0, 'stereoAtoms': []}]},
        'label': 'Optically recognized structure (2 atoms)', 'source': 'optical-recognition',
        'requiresReview': True, 'atomCount': 2, 'formalCharge': 0,
        'warnings': ['Review the proposal against the source image.'], 'unsupportedFeatures': [],
        'recognition': {'method': 'MolScribe', 'modelSha256': validation.MODEL_SHA256,
            'inputSha256': INPUT_DIGEST, 'confidence': 0.75,
            'confidenceMeaning': 'uncalibrated model score',
            'coordinates': 'preserved-from-recognized-molfile'}}


def envelope_fixture():
    return {'schemaVersion': 1, 'inputSha256': INPUT_DIGEST, 'candidate': candidate_fixture()}


class OpticalValidationWorkerTests(unittest.TestCase):
    def invoke(self, raw):
        output = io.BytesIO()
        code = validation.worker_main(stdin=io.BytesIO(raw), stdout=output)
        encoded = output.getvalue()
        self.assertLessEqual(len(encoded), validation.MAX_OUTPUT_BYTES)
        return code, json.loads(encoded), encoded

    def assert_generic_failure(self, raw):
        code, result, encoded = self.invoke(raw)
        self.assertNotEqual(code, 0)
        self.assertEqual(result, {'schemaVersion': 1, 'error': {
            'code': 'invalid_prediction', 'message': validation.SAFE_FAILURE}})
        self.assertNotIn(b'private', encoded)
        self.assertNotIn(b'C:\\', encoded)

    def test_small_co_result_uses_native_chemistry_and_binds_exact_crop_hash(self):
        code, result, _ = self.invoke(encoded_prediction())
        self.assertEqual(code, 0)
        self.assertEqual(set(result), {'schemaVersion', 'inputSha256', 'candidate'})
        self.assertEqual(result['schemaVersion'], 1)
        self.assertEqual(result['inputSha256'], INPUT_DIGEST)
        candidate = result['candidate']
        self.assertEqual(candidate['source'], 'optical-recognition')
        self.assertIs(candidate['requiresReview'], True)
        self.assertEqual([atom['symbol'] for atom in candidate['graph']['atoms']], ['C', 'O'])
        self.assertEqual([atom['x'] for atom in candidate['graph']['atoms']], [0.0, 1.5])
        self.assertEqual(candidate['graph']['bonds'][0]['order'], 1.0)
        self.assertEqual(candidate['recognition']['inputSha256'], INPUT_DIGEST)
        self.assertEqual(candidate['recognition']['confidence'], 0.75)
        self.assertEqual(candidate['recognition']['coordinates'], 'preserved-from-recognized-molfile')

    def test_input_at_byte_limit_is_accepted_and_one_extra_byte_rejects(self):
        source = encoded_prediction()
        padded = source + b' ' * (validation.MAX_INPUT_BYTES - len(source))
        self.assertEqual(self.invoke(padded)[0], 0)
        self.assert_generic_failure(padded + b' ')

    def test_size_sentinel_is_read_before_json_or_native_validation(self):
        stream, output = Mock(), io.BytesIO()
        stream.read.return_value = b'x' * (validation.MAX_INPUT_BYTES + 1)
        with patch.object(validation, 'strict_json_loads') as parse:
            self.assertEqual(validation.worker_main(stdin=stream, stdout=output), 2)
        stream.read.assert_called_once_with(validation.MAX_INPUT_BYTES + 1)
        parse.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['error']['message'], validation.SAFE_FAILURE)

    def test_malformed_duplicate_nonfinite_and_deep_json_reject_before_chemistry(self):
        malformed = [b'', b'\xff', b'{', b'{"schemaVersion":1,"schemaVersion":1}',
                     b'{"confidence":NaN}', b'{"value":' + b'[' * 65 + b'0' + b']' * 65 + b'}']
        with patch('nivram.optical_import.optical_candidate') as native:
            for raw in malformed:
                with self.subTest(raw=raw[:60]):
                    self.assert_generic_failure(raw)
            native.assert_not_called()

    def test_invalid_contract_chemistry_and_boolean_numbers_fail_generically(self):
        for raw in (b'[]', b'null', b'{}', encoded_prediction(schemaVersion=True),
                    encoded_prediction(confidence=True), encoded_prediction(smiles='CO(C)C'),
                    encoded_prediction(molfile='private C:\\secret\\structure.mol'),
                    encoded_prediction(inputSha256='A' * 64), encoded_prediction(extra=True)):
            with self.subTest(raw=raw[:100]):
                self.assert_generic_failure(raw)

    def test_unexpected_validator_exception_cannot_disclose_input_or_paths(self):
        with patch('nivram.optical_import.optical_candidate',
                   side_effect=RuntimeError('private C:\\secret\\source.png CO')):
            self.assert_generic_failure(encoded_prediction())

    def test_chemistry_rejections_are_fixed_messages_bound_to_the_crop(self):
        cases = [('inconsistent_prediction', encoded_prediction(smiles='CN')),
                 ('unsupported_abbreviation', encoded_prediction(
                     molfile=CO_MOL.replace('M  END', 'A    2\nprivate C:\\secret\\alias\nM  END')))]
        for reason, raw in cases:
            with self.subTest(reason=reason):
                code, result, encoded = self.invoke(raw)
                self.assertEqual(code, 2)
                self.assertEqual(result, {'schemaVersion': 1, 'inputSha256': INPUT_DIGEST,
                    'error': {'code': reason, 'message': validation.REJECTION_MESSAGES[reason]}})
                self.assertNotIn(b'private', encoded)
                self.assertNotIn(b'C:\\', encoded)
                self.assertNotIn('candidate', result)

    def test_unknown_optical_error_code_cannot_disclose_exception_text(self):
        from nivram.optical_import import OpticalImportError
        with patch('nivram.optical_import.optical_candidate', side_effect=OpticalImportError(
                'private C:\\secret\\source.png', code='unrecognized-code')):
            self.assert_generic_failure(encoded_prediction())

    def test_oversized_candidate_output_becomes_bounded_generic_failure(self):
        with patch('nivram.optical_import.optical_candidate',
                   return_value={'private': 'x' * validation.MAX_OUTPUT_BYTES}):
            self.assert_generic_failure(encoded_prediction())


class OpticalValidationSubprocessTests(unittest.TestCase):
    def test_trusted_validator_runs_from_foreign_cwd_and_ignores_decoy_packages(self):
        with tempfile.TemporaryDirectory(prefix='nivram validator launch ') as directory:
            root = Path(directory)
            foreign_cwd = root / 'foreign cwd'
            foreign_cwd.mkdir()
            decoy_path = root / 'environment packages'
            decoy_path.mkdir()
            markers = []
            for decoys in (False, True):
                with self.subTest(decoys=decoys):
                    if decoys:
                        for location in (foreign_cwd, decoy_path):
                            package = location / 'nivram'
                            package.mkdir()
                            marker = location / 'decoy-executed.txt'
                            markers.append(marker)
                            (package / '__init__.py').write_text(
                                'from pathlib import Path\n'
                                f'Path({str(marker)!r}).write_text("decoy executed")\n'
                                'raise SystemExit(89)\n', encoding='utf-8')
                    environment = dict(os.environ, PYTHONPATH=str(decoy_path))
                    with patch.object(validation.sys, 'frozen', False, create=True):
                        completed = subprocess.run(validation._command(),
                            input=encoded_prediction(), capture_output=True,
                            cwd=foreign_cwd, env=environment, timeout=30, check=False)
                    self.assertFalse(any(marker.exists() for marker in markers),
                                     'The validator executed a foreign nivram package.')
                    self.assertEqual(completed.returncode, 0,
                                     completed.stderr.decode('utf-8', errors='replace'))
                    result = json.loads(completed.stdout)
                    self.assertEqual(result['inputSha256'], INPUT_DIGEST)
                    candidate = validation._checked_candidate(result['candidate'], INPUT_DIGEST)
                    self.assertEqual([atom['symbol'] for atom in candidate['graph']['atoms']], ['C', 'O'])
                    self.assertEqual(candidate['graph']['bonds'][0]['order'], 1.0)


class OpticalValidationParentTests(unittest.TestCase):
    def setUp(self):
        self.supervisor = Mock(spec=['run', 'check_cancelled'])
        self.supervisor.run.return_value = subprocess.CompletedProcess(
            [], 0, json.dumps(envelope_fixture()), 'private stderr must not appear')
        self.token = CancellationToken()
        self.remaining = Mock(return_value=12.5)
        self.raw = encoded_prediction().decode('utf-8') + '\n '

    def validate(self, raw=None, digest=INPUT_DIGEST):
        return validation.validate_optical_result(self.raw if raw is None else raw,
            digest, self.supervisor, self.remaining, self.token)

    def test_forwarding_uses_owned_supervisor_remaining_budget_and_no_parent_chemistry(self):
        with patch('nivram.optical_import.optical_candidate', side_effect=AssertionError('Parent chemistry')):
            result = self.validate()
        self.assertEqual(result, candidate_fixture())
        self.supervisor.run.assert_called_once_with(validation._command(), input_text=self.raw,
            timeout_seconds=12.5, cancellation_token=self.token,
            max_input_bytes=validation.MAX_INPUT_BYTES, max_stdout_bytes=validation.MAX_OUTPUT_BYTES,
            max_stderr_bytes=validation.MAX_STDERR_BYTES)
        self.assertGreaterEqual(self.remaining.call_count, 2)

    def test_oversized_multibyte_invalid_text_and_digest_never_dispatch(self):
        for raw, digest in (('', INPUT_DIGEST), (None, INPUT_DIGEST),
                            ('x' * (validation.MAX_INPUT_BYTES + 1), INPUT_DIGEST),
                            ('\u00e9' * (validation.MAX_INPUT_BYTES // 2 + 1), INPUT_DIGEST),
                            ('\ud800', INPUT_DIGEST), (self.raw, 'f' * 63), (self.raw, True)):
            with self.subTest(raw_type=type(raw).__name__, digest=digest):
                with self.assertRaisesRegex(ValueError, validation.SAFE_FAILURE):
                    validation.validate_optical_result(raw, digest, self.supervisor, self.remaining, self.token)
        self.supervisor.run.assert_not_called()

    def test_wrong_crop_in_either_envelope_or_candidate_cannot_be_returned(self):
        for location in ('envelope', 'candidate'):
            response = envelope_fixture()
            if location == 'envelope':
                response['inputSha256'] = 'b' * 64
            else:
                response['candidate']['recognition']['inputSha256'] = 'b' * 64
            self.supervisor.run.return_value.stdout = json.dumps(response)
            with self.subTest(location=location), self.assertRaisesRegex(ValueError, validation.SAFE_FAILURE):
                self.validate()

    def test_plain_candidate_contract_rejects_boolean_counts_and_unknown_fields(self):
        mutations = [lambda r: r.update(schemaVersion=True), lambda r: r.update(extra=True),
                     lambda r: r['candidate'].update(atomCount=True),
                     lambda r: r['candidate'].update(formalCharge=False),
                     lambda r: r['candidate'].update(requiresReview=1),
                     lambda r: r['candidate'].update(extra='private'),
                     lambda r: r['candidate']['recognition'].update(confidence=True),
                     lambda r: r['candidate']['recognition'].update(coordinates='regenerated-from-recognized-smiles'),
                     lambda r: r['candidate']['graph']['atoms'][0].update(charge=True),
                     lambda r: r['candidate']['graph']['atoms'][0].update(extra='private')]
        for mutate in mutations:
            response = envelope_fixture()
            mutate(response)
            self.supervisor.run.return_value.stdout = json.dumps(response)
            with self.assertRaisesRegex(ValueError, validation.SAFE_FAILURE):
                self.validate()

    def test_only_exact_crop_bound_rejections_can_surface_actionable_messages(self):
        for reason, message in validation.REJECTION_MESSAGES.items():
            response = {'schemaVersion': 1, 'inputSha256': INPUT_DIGEST,
                        'error': {'code': reason, 'message': message}}
            self.supervisor.run.return_value = subprocess.CompletedProcess([], 2, json.dumps(response), 'private')
            with self.subTest(reason=reason), self.assertRaises(ValueError) as caught:
                self.validate()
            self.assertEqual(str(caught.exception), message)
            self.assertGreaterEqual(self.remaining.call_count, 2)
            mutations = [lambda r: r.update(inputSha256='b' * 64),
                         lambda r: r.update(schemaVersion=True), lambda r: r.update(extra=True),
                         lambda r: r['error'].update(code='unrecognized-code'),
                         lambda r: r['error'].update(message='private C:\\secret\\source.png'),
                         lambda r: r['error'].update(extra='private')]
            for mutate in mutations:
                bad = copy.deepcopy(response)
                mutate(bad)
                self.supervisor.run.return_value.stdout = json.dumps(bad)
                with self.assertRaises(ValueError) as caught:
                    self.validate()
                self.assertEqual(str(caught.exception), validation.SAFE_FAILURE)
            for exit_code in (0, True, 1, -1):
                self.supervisor.run.return_value = subprocess.CompletedProcess([], exit_code, json.dumps(response), '')
                with self.assertRaises(ValueError) as caught:
                    self.validate()
                self.assertEqual(str(caught.exception), validation.SAFE_FAILURE)

    def test_cancellation_and_deadline_win_over_a_valid_rejection(self):
        response = {'schemaVersion': 1, 'inputSha256': INPUT_DIGEST, 'error': {
            'code': 'inconsistent_prediction', 'message': validation.REJECTION_MESSAGES['inconsistent_prediction']}}
        self.supervisor.run.return_value = subprocess.CompletedProcess([], 2, json.dumps(response), '')
        self.remaining.side_effect = [12.5, 0]
        with self.assertRaises(subprocess.TimeoutExpired):
            self.validate()
        self.remaining.side_effect = None
        self.supervisor.check_cancelled.side_effect = [None, None, WorkerCancelled('Cancelled')]
        with self.assertRaises(WorkerCancelled):
            self.validate()

    def test_invalid_json_depth_result_size_and_nonzero_exit_are_generic(self):
        for output in ('private not JSON', '{"schemaVersion":1,"schemaVersion":1}',
                       '{"value":' + '[' * 65 + '0' + ']' * 65 + '}',
                       'x' * (validation.MAX_OUTPUT_BYTES + 1)):
            self.supervisor.run.return_value.stdout = output
            with self.subTest(output=output[:40]), self.assertRaisesRegex(ValueError, validation.SAFE_FAILURE):
                self.validate()
        self.supervisor.run.return_value = subprocess.CompletedProcess([], 2, 'private raw input', 'private path')
        with self.assertRaisesRegex(ValueError, validation.SAFE_FAILURE) as caught:
            self.validate()
        self.assertNotIn('private', str(caught.exception))

    def test_cancellation_and_expired_budget_cannot_launch_or_publish(self):
        self.supervisor.check_cancelled.side_effect = WorkerCancelled('Cancelled')
        with self.assertRaises(WorkerCancelled):
            self.validate()
        self.supervisor.run.assert_not_called()
        self.supervisor.check_cancelled.side_effect = None
        for value in (0, -1, False, float('nan')):
            self.remaining.return_value = value
            with self.subTest(value=value), self.assertRaises(subprocess.TimeoutExpired):
                self.validate()
        self.supervisor.run.assert_not_called()
        self.remaining.return_value = 12.5
        self.supervisor.check_cancelled.side_effect = [None, WorkerCancelled('Cancelled after validation')]
        with self.assertRaises(WorkerCancelled):
            self.validate()
        self.assertEqual(self.supervisor.run.call_count, 1)

    def test_core_interpreter_and_frozen_sibling_commands(self):
        with patch.object(validation.sys, 'frozen', False, create=True):
            self.assertEqual(validation._command(), [sys.executable, '-I', '-B', '-c',
                validation._SOURCE_WORKER_BOOTSTRAP, str(Path(validation.__file__).resolve().parent.parent)])
        with patch.object(validation.sys, 'frozen', True, create=True):
            self.assertEqual(validation._command(),
                [str(Path(sys.executable).with_name('nivram-worker.exe')), '--validate-optical'])


if __name__ == '__main__':
    unittest.main()
