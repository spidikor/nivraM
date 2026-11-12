import copy
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from nivram import optical_setup_worker as setup
from nivram.worker_processes import CancellationToken, WorkerCancelled, WorkerProcessError


class OpticalSetupWorkerTests(unittest.TestCase):
    def invoke(self, raw):
        output = io.BytesIO()
        code = setup.worker_main(stdin=io.BytesIO(raw), stdout=output)
        encoded = output.getvalue()
        self.assertLessEqual(len(encoded), setup.MAX_OUTPUT_BYTES)
        return code, json.loads(encoded)

    def test_small_real_file_verifies_exact_hash_size_and_closes_without_ml(self):
        data = b'bounded synthetic model bytes'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'checkpoint.pth'
            path.write_bytes(data)
            raw = json.dumps({'schemaVersion': 1, 'model': str(path)}).encode()
            with patch.object(setup.optical_worker, 'MODEL_BYTES', len(data)), \
                    patch.object(setup.optical_worker, 'MODEL_SHA256', hashlib.sha256(data).hexdigest()), \
                    patch.object(setup.optical_worker, 'load_predictor', side_effect=AssertionError('No ML loading')):
                code, result = self.invoke(raw)
                self.assertEqual(code, 0)
                self.assertEqual(result, {'schemaVersion': 1, 'requestSha256': hashlib.sha256(raw).hexdigest(),
                    'status': 'verified', 'modelSha256': hashlib.sha256(data).hexdigest(), 'modelBytes': len(data)})
                for bad in (b'x', b'x' * len(data)):
                    path.write_bytes(bad)
                    self.assertEqual(self.invoke(raw)[1]['error']['code'], 'model_mismatch')
            path.rename(path.with_suffix('.closed'))
            self.assertEqual(self.invoke(raw)[1]['error']['code'], 'model_unavailable')

    def test_invalid_and_oversized_requests_never_open_a_model(self):
        invalid = (b'', b'\xff', b'{', b'[]', b'{"schemaVersion":NaN}',
            b'{"schemaVersion":1,"schemaVersion":1,"model":"x"}',
            json.dumps({'schemaVersion': True, 'model': str(Path.cwd())}).encode(),
            json.dumps({'schemaVersion': 1, 'model': 'relative.pth'}).encode(),
            json.dumps({'schemaVersion': 1, 'model': None}).encode(),
            json.dumps({'schemaVersion': 1, 'model': str(Path.cwd()), 'extra': True}).encode(),
            b'x' * (setup.MAX_INPUT_BYTES + 1))
        with patch.object(setup.optical_worker, 'verified_model') as verify:
            for raw in invalid:
                with self.subTest(raw=raw[:60]):
                    code, result = self.invoke(raw)
                    self.assertEqual(code, 2)
                    self.assertEqual(result['error'], {'code': 'invalid_request', 'message': setup.SAFE_FAILURE})
            verify.assert_not_called()
        stream = Mock()
        stream.read.return_value = b'x' * (setup.MAX_INPUT_BYTES + 1)
        self.assertEqual(setup.worker_main(stdin=stream, stdout=io.BytesIO()), 2)
        stream.read.assert_called_once_with(setup.MAX_INPUT_BYTES + 1)

    def test_unexpected_file_error_does_not_disclose_path_or_exception(self):
        raw = json.dumps({'schemaVersion': 1, 'model': str(Path.cwd() / 'private.pth')}).encode()
        with patch.object(setup.optical_worker, 'verified_model', side_effect=RuntimeError('private secret')):
            code, result = self.invoke(raw)
        self.assertEqual(code, 2)
        self.assertEqual(result['error'], {'code': 'verification_failed', 'message': setup.SAFE_FAILURE})
        self.assertNotIn('private', json.dumps(result))

    def test_isolated_source_bootstrap_ignores_foreign_cwd_and_pythonpath(self):
        with tempfile.TemporaryDirectory(prefix='nivram setup ') as directory:
            root = Path(directory)
            markers = []
            for location in (root, root / 'environment'):
                package = location / 'nivram'
                package.mkdir(parents=True)
                marker = location / 'executed.txt'
                markers.append(marker)
                (package / '__init__.py').write_text(
                    f'from pathlib import Path\nPath({str(marker)!r}).write_text("wrong package")\nraise SystemExit(89)\n')
            raw = json.dumps({'schemaVersion': 1, 'model': str(root / 'missing.pth')}).encode()
            with patch.object(setup.sys, 'frozen', False, create=True):
                result = subprocess.run(setup._command(), input=raw, capture_output=True, cwd=root,
                    env=dict(os.environ, PYTHONPATH=str(root / 'environment')), timeout=15, check=False)
            self.assertEqual(result.returncode, 2, result.stderr)
            reply = json.loads(result.stdout)
            self.assertEqual(reply['error']['code'], 'model_unavailable')
            self.assertEqual(reply['requestSha256'], hashlib.sha256(raw).hexdigest())
            self.assertFalse(any(marker.exists() for marker in markers))

    def test_frozen_command_and_exact_entrypoint_dispatch(self):
        with patch.object(setup.sys, 'frozen', True, create=True):
            self.assertEqual(setup._command(),
                [str(Path(sys.executable).with_name('nivram-worker.exe')), '--verify-optical-model'])
        script = Path(__file__).resolve().parents[1] / 'run_worker.py'
        with patch.object(sys, 'argv', [str(script), '--verify-optical-model']), \
                patch('multiprocessing.freeze_support'), patch.object(setup, 'worker_main', return_value=17) as worker:
            with self.assertRaises(SystemExit) as caught:
                runpy.run_path(str(script), run_name='__main__')
            self.assertEqual(caught.exception.code, 17)
            worker.assert_called_once_with()
        with patch.object(sys, 'argv', [str(script), '--verify-optical-model', 'extra']), \
                patch('multiprocessing.freeze_support'), patch.object(setup, 'worker_main') as worker:
            with self.assertRaisesRegex(SystemExit, 'Unsupported'):
                runpy.run_path(str(script), run_name='__main__')
            worker.assert_not_called()


class OpticalSetupParentTests(unittest.TestCase):
    def setUp(self):
        self.model = Path.cwd() / 'selected-model.pth'
        self.token = CancellationToken()
        self.supervisor = Mock(spec=['run', 'check_cancelled'])
        payload = json.dumps({'schemaVersion': 1, 'model': str(self.model)}, ensure_ascii=True, separators=(',', ':'))
        self.digest = hashlib.sha256(payload.encode()).hexdigest()
        self.response = {'schemaVersion': 1, 'requestSha256': self.digest, 'status': 'verified',
            'modelSha256': setup.optical_worker.MODEL_SHA256, 'modelBytes': setup.optical_worker.MODEL_BYTES}
        self.supervisor.run.return_value = subprocess.CompletedProcess([], 0, json.dumps(self.response), 'private stderr')

    def verify(self):
        return setup.verify_optical_model(self.model, token=self.token, supervisor=self.supervisor)

    def test_bounded_trusted_dispatch_has_no_parent_hash_or_selected_interpreter(self):
        with patch.object(setup.optical_worker, 'verified_model', side_effect=AssertionError('No parent model access')):
            self.assertIsNone(self.verify())
        self.supervisor.run.assert_called_once_with(setup._command(),
            input_text=json.dumps({'schemaVersion': 1, 'model': str(self.model)}, ensure_ascii=True, separators=(',', ':')),
            timeout_seconds=90.0, cancellation_token=self.token, max_input_bytes=16384,
            max_stdout_bytes=4096, max_stderr_bytes=4096)
        self.assertEqual(self.supervisor.check_cancelled.call_count, 3)
        self.assertEqual(setup._command()[1:4], ['-I', '-B', '-c'])

    def test_invalid_paths_and_oversized_transport_do_not_dispatch(self):
        for value in (None, True, b'bytes', '', 'relative.pth', 'C:\x00private', str(self.model) + '\ud800',
                      str(self.model) + '\u00e9' * 3000, str(self.model) + '\u00e9' * setup.MAX_INPUT_BYTES):
            with self.subTest(value_type=type(value).__name__), self.assertRaisesRegex(ValueError, setup.SAFE_FAILURE):
                setup.verify_optical_model(value, token=self.token, supervisor=self.supervisor)
        self.supervisor.run.assert_not_called()

    def test_response_identity_shape_size_and_numeric_types_are_strict(self):
        mutations = (lambda r: r.update(schemaVersion=True), lambda r: r.update(requestSha256='0' * 64),
            lambda r: r.update(modelSha256='0' * 64), lambda r: r.update(modelBytes=True),
            lambda r: r.update(modelBytes=1), lambda r: r.update(status='ready'), lambda r: r.update(extra=True))
        for mutate in mutations:
            response = copy.deepcopy(self.response)
            mutate(response)
            self.supervisor.run.return_value.stdout = json.dumps(response)
            with self.assertRaisesRegex(ValueError, setup.SAFE_FAILURE):
                self.verify()
        for raw in ('', 'not JSON', '{"schemaVersion":1,"schemaVersion":1}',
                    '{"x":NaN}', 'x' * (setup.MAX_OUTPUT_BYTES + 1), '\ud800'):
            self.supervisor.run.return_value.stdout = raw
            with self.assertRaisesRegex(ValueError, setup.SAFE_FAILURE):
                self.verify()
        for code in (True, 1, -1):
            self.supervisor.run.return_value = subprocess.CompletedProcess([], code, json.dumps(self.response), '')
            with self.assertRaisesRegex(ValueError, setup.SAFE_FAILURE):
                self.verify()

    def test_only_fixed_bound_errors_surface(self):
        for code, message in setup.ERROR_MESSAGES.items():
            response = {'schemaVersion': 1, 'requestSha256': self.digest, 'error': {'code': code, 'message': message}}
            self.supervisor.run.return_value = subprocess.CompletedProcess([], 2, json.dumps(response), 'private')
            with self.assertRaises(ValueError) as caught:
                self.verify()
            self.assertEqual(str(caught.exception), message)
            response['error']['message'] = 'private path and raw error'
            self.supervisor.run.return_value.stdout = json.dumps(response)
            with self.assertRaises(ValueError) as caught:
                self.verify()
            self.assertEqual(str(caught.exception), setup.SAFE_FAILURE)

    def test_cancel_timeout_and_owner_failure_cannot_publish_success(self):
        self.supervisor.check_cancelled.side_effect = WorkerCancelled('cancelled')
        with self.assertRaises(WorkerCancelled):
            self.verify()
        self.supervisor.run.assert_not_called()
        self.supervisor.check_cancelled.side_effect = None
        for error in (subprocess.TimeoutExpired(['trusted worker'], 90), WorkerProcessError('owner failure')):
            self.supervisor.run.side_effect = error
            with self.assertRaises(type(error)):
                self.verify()
        self.supervisor.run.side_effect = None
        self.supervisor.check_cancelled.side_effect = [None, None, WorkerCancelled('cancelled after checking')]
        with self.assertRaises(WorkerCancelled):
            self.verify()


if __name__ == '__main__':
    unittest.main()
