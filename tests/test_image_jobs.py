import io
import sys
import threading
import time
import unittest
from PIL import Image, PngImagePlugin
from nivram.image_jobs import ImageJobs, ImageJobCancelled


class ImageJobTests(unittest.TestCase):
    def test_real_worker_recovers_public_smiles_metadata(self):
        data = io.BytesIO()
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('SMILES', 'CCO')
        Image.new('RGB', (4, 4), 'white').save(data, format='PNG', pnginfo=metadata)
        result = ImageJobs().inspect(data.getvalue(), 'public-ethanol')
        self.assertEqual(result['status'], 'metadata_available')
        self.assertEqual(len(result['candidates'][0]['graph']['atoms']), 3)
        self.assertEqual(result['recognition']['status'], 'not_run')

    def test_worker_failure_is_contained(self):
        with self.assertRaisesRegex(ValueError, 'could not be inspected'):
            ImageJobs().inspect(b'not a png', 'invalid-png')

    def test_timeout_stops_worker_and_allows_next_job(self):
        jobs = ImageJobs()
        with self.assertRaisesRegex(ValueError, 'time limit'):
            jobs.inspect(b'', 'timeout-test', timeout=0.15,
                _command=[sys.executable, '-c', 'import time; time.sleep(20)'])
        self.assertIsNone(jobs._active)

    def test_cancel_stops_only_matching_job_and_releases_slot(self):
        jobs, errors = ImageJobs(), []
        def task():
            try:
                jobs.inspect(b'', 'cancel-target', _command=[sys.executable, '-c', 'import time; time.sleep(20)'])
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=task)
        thread.start()
        deadline = time.monotonic() + 5
        process = None
        while time.monotonic() < deadline:
            with jobs._lock:
                process = jobs._active and jobs._active['process']
            if process is not None:
                break
            time.sleep(0.01)
        self.assertIsNotNone(process)
        self.assertEqual(jobs.cancel('another-id')['status'], 'not_running')
        self.assertIsNone(process.poll())
        with self.assertRaisesRegex(ValueError, 'already running'):
            jobs.inspect(b'', 'second-job')
        self.assertEqual(jobs.cancel('cancel-target')['status'], 'cancelled')
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertIsNotNone(process.poll())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ImageJobCancelled)
        self.assertIsNone(jobs._active)


if __name__ == '__main__':
    unittest.main()
