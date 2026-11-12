import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image
from rdkit import Chem
from rdkit.Chem import rdDepictor

from nivram.optical_jobs import OpticalJobs, configured_optical_jobs, MODEL_SHA256
from nivram.worker_processes import WorkerCancelled, WorkerOutputLimit


def png(size=(12, 9)):
    output = io.BytesIO()
    Image.new('RGB', size, 'white').save(output, format='PNG')
    return output.getvalue()


def prediction(data):
    mol = Chem.MolFromSmiles('CO')
    rdDepictor.Compute2DCoords(mol)
    return {'schemaVersion': 1, 'method': 'MolScribe',
        'modelSha256': '32821fa4ba8d17f9a92e374601438c5e757947dbf1141e9ad0ef51cccfa1c501',
        'inputSha256': hashlib.sha256(data).hexdigest(),
        'smiles': 'CO', 'molfile': Chem.MolToMolBlock(mol), 'confidence': 0.75}


class OpticalJobTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.jobs = OpticalJobs(Path(sys.executable).resolve(), Path(self.directory.name) / 'model.pth')
        self.data = png()

    def tearDown(self):
        self.assertTrue(self.jobs.close())
        self.directory.cleanup()

    def child(self, code):
        return patch.object(self.jobs, '_command', return_value=[sys.executable, '-I', '-B', '-u', '-c', code])

    def configured(self):
        return patch.object(self.jobs, 'availability', return_value={'status': 'configured'})

    def test_missing_model_has_actionable_state_and_starts_no_process(self):
        self.assertEqual(self.jobs.availability()['status'], 'missing-model')
        with patch.object(self.jobs._supervisor, '_spawn') as start:
            with self.assertRaisesRegex(ValueError, 'has not been downloaded'):
                self.jobs.recognize(self.data, 'optical_test_1')
            start.assert_not_called()

    def test_actual_standalone_worker_missing_model_is_actionable(self):
        with self.configured():
            with self.assertRaisesRegex(ValueError, 'model could not be read'):
                self.jobs.recognize(self.data, 'optical_test_1', timeout=10)
        self.assertEqual(self.jobs._supervisor.active_count, 0)

    def test_real_pipe_exchange_returns_graph_bound_to_exact_crop(self):
        raw = prediction(self.data)
        code = 'import json,sys; request=json.load(sys.stdin); response=' + repr(raw) + '; assert request["inputSha256"]==response["inputSha256"]; print(json.dumps(response))'
        with self.configured(), self.child(code):
            result = self.jobs.recognize(self.data, 'optical_test_1', timeout=10)
        self.assertEqual(result['inputSha256'], hashlib.sha256(self.data).hexdigest())
        self.assertEqual([atom['symbol'] for atom in result['candidate']['graph']['atoms']], ['C', 'O'])
        self.assertEqual(result['candidate']['recognition']['confidence'], 0.75)
        self.assertEqual(self.jobs._supervisor.active_count, 0)

    def test_other_crop_receipt_and_malformed_json_are_rejected(self):
        raw = prediction(self.data)
        raw['inputSha256'] = 'f' * 64
        for code in ('print(' + repr(json.dumps(raw)) + ')', 'print("not json")'):
            with self.subTest(code=code[:20]), self.configured(), self.child(code):
                with self.assertRaises(ValueError):
                    self.jobs.recognize(self.data, 'optical_test_1', timeout=10)

    def test_worker_failure_does_not_disclose_stderr_or_private_paths(self):
        with self.configured(), self.child('import sys; print("private path and molecule",file=sys.stderr); sys.exit(2)'):
            with self.assertRaises(ValueError) as caught:
                self.jobs.recognize(self.data, 'optical_test_1', timeout=10)
        self.assertNotIn('private', str(caught.exception))
        self.assertNotIn(self.directory.name, str(caught.exception))

    def test_invalid_crop_never_starts_worker(self):
        for data in (b'not png', png((2049, 1)), png((1001, 1000)), self.data + b'trailing bytes'):
            with self.subTest(bytes=len(data)), patch.object(self.jobs._supervisor, '_spawn') as start:
                with self.assertRaises(ValueError):
                    self.jobs.recognize(data, 'optical_test_1')
                start.assert_not_called()

    def test_output_limit_and_timeout_stop_owned_child(self):
        with self.configured(), self.child('import sys; sys.stdout.write("x"*1100000)'):
            with self.assertRaises(WorkerOutputLimit):
                self.jobs.recognize(self.data, 'optical_test_1', timeout=10)
        with self.configured(), self.child('import time; time.sleep(60)'):
            with self.assertRaisesRegex(ValueError, 'time limit'):
                self.jobs.recognize(self.data, 'optical_test_2', timeout=0.3)
        self.assertEqual(self.jobs._supervisor.active_count, 0)

    def test_cancel_before_dispatch_cannot_start_a_late_job(self):
        self.jobs.cancel('optical_test_1')
        with patch.object(self.jobs._supervisor, '_spawn') as start:
            with self.assertRaises(WorkerCancelled):
                self.jobs.recognize(self.data, 'optical_test_1')
            start.assert_not_called()

    def test_cancel_running_job_joins_before_next_result(self):
        outcome = {}
        def recognize():
            try:
                outcome['result'] = self.jobs.recognize(self.data, 'optical_test_1', timeout=10)
            except Exception as error:
                outcome['error'] = error
        with self.configured(), self.child('import time; time.sleep(60)'):
            thread = threading.Thread(target=recognize)
            thread.start()
            try:
                deadline = time.monotonic() + 5
                while self.jobs._supervisor.active_count != 1 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(self.jobs._supervisor.active_count, 1)
                self.assertEqual(self.jobs.cancel('optical_test_1')['status'], 'cancelled')
            finally:
                self.jobs.cancel('optical_test_1')
                thread.join(5)
                if thread.is_alive():
                    self.jobs.close()
                    thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertIsInstance(outcome.get('error'), WorkerCancelled)
        self.assertNotIn('result', outcome)
        self.assertEqual(self.jobs._supervisor.active_count, 0)


class OpticalConfigurationTests(unittest.TestCase):
    def test_only_local_explicit_complete_pinned_configuration_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            jobs, status = configured_optical_jobs(folder)
            self.assertIsNone(jobs)
            self.assertEqual(status['status'], 'not-configured')
            setting = {'schemaVersion': 1, 'method': 'MolScribe', 'modelSha256': MODEL_SHA256,
                       'python': str(Path(sys.executable).resolve()), 'model': str(folder / 'model.pth')}
            (folder / 'runtime.json').write_text(json.dumps(setting), encoding='utf-8')
            jobs, status = configured_optical_jobs(folder)
            try:
                self.assertIsInstance(jobs, OpticalJobs)
                self.assertEqual(status['status'], 'missing-model')
            finally:
                jobs.close()
            for changes in ({'modelSha256': 'f' * 64}, {'python': 'python.exe'},
                            {'schemaVersion': True}, {'model': 42}, {'extra': 'not allowed'}):
                with self.subTest(changes=changes):
                    (folder / 'runtime.json').write_text(json.dumps({**setting, **changes}), encoding='utf-8')
                    jobs, status = configured_optical_jobs(folder)
                    self.assertIsNone(jobs)
                    self.assertEqual(status['status'], 'invalid-configuration')
            (folder / 'runtime.json').write_bytes(b'{' * 16_385)
            self.assertEqual(configured_optical_jobs(folder)[1]['status'], 'invalid-configuration')
            (folder / 'runtime.json').write_bytes(b'[' * 2000 + b'0' + b']' * 2000)
            self.assertEqual(configured_optical_jobs(folder)[1]['status'], 'invalid-configuration')


if __name__ == '__main__':
    unittest.main()
