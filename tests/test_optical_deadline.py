"""Recognition shares one deadline across preprocessing and both owned workers.

Synthetic predictions exercise scheduling and publication, not model inference.
The only real children below are deliberately sleeping validation fixtures.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_image_optical_session as session_fixtures
import test_optical_jobs as job_fixtures

from nivram import image_session, optical_jobs, optical_validation
from nivram.optical_import import optical_candidate
from nivram.optical_processes import OpticalSupervisor
from nivram.worker_processes import CancellationToken, WorkerCancelled


class ControlledClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds

    def module(self):
        # Replacing one module's reference does not alter time in worker threads.
        return SimpleNamespace(monotonic=self.monotonic)


class OpticalDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.jobs = optical_jobs.OpticalJobs(
            Path(sys.executable).resolve(), Path(self.temp.name) / 'model.pth')
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.jobs.close)
        self.data = job_fixtures.png()
        self.raw = job_fixtures.prediction(self.data)
        self.raw_text = json.dumps(self.raw)
        self.candidate = optical_candidate(self.raw)
        self.clock = ControlledClock()
        clock_patch = patch.object(optical_jobs, 'time', self.clock.module())
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        availability_patch = patch.object(self.jobs, 'availability', return_value={'status': 'configured'})
        availability_patch.start()
        self.addCleanup(availability_patch.stop)

    def engine_result(self):
        return subprocess.CompletedProcess(['synthetic-optical-engine'], 0, self.raw_text, '')

    def validation_result(self):
        envelope = {'schemaVersion': 1, 'inputSha256': self.raw['inputSha256'],
                    'candidate': copy.deepcopy(self.candidate)}
        return subprocess.CompletedProcess(['synthetic-optical-validation'], 0,
                                           json.dumps(envelope), '')

    def test_default_budget_covers_preprocessing_inference_cleanup_and_validation(self):
        checked_png = optical_jobs._validate_derivative_png

        def preprocess(*args):
            checked_png(*args)
            self.clock.advance(12)

        budgets, tokens = [], []

        def run(command, **kwargs):
            budgets.append(kwargs['timeout_seconds'])
            tokens.append(kwargs['cancellation_token'])
            if len(budgets) == 1:
                self.assertEqual(json.loads(kwargs['input_text'])['inputSha256'],
                                 self.raw['inputSha256'])
                self.clock.advance(25)  # Inference and process completion.
                self.clock.advance(3)   # Cleanup before run() returns to its caller.
                return self.engine_result()
            self.assertEqual(len(budgets), 2, 'Recognition must use sequential stages.')
            self.assertEqual(kwargs['input_text'], self.raw_text)
            self.clock.advance(6)
            return self.validation_result()

        token = CancellationToken()
        with patch.object(optical_jobs, '_validate_derivative_png', side_effect=preprocess), \
                patch.object(self.jobs._supervisor, 'run', side_effect=run):
            result = self.jobs.recognize(self.data, 'deadline_default_1',
                                         _cancellation_token=token)
        self.assertEqual(budgets, [78.0, 50.0])
        self.assertEqual(tokens, [token, token])
        self.assertEqual(result['candidate'], self.candidate)
        self.assertEqual(result['inputSha256'], hashlib.sha256(self.data).hexdigest())
        self.assertIsNone(self.jobs._active)

    def test_invalid_timeout_rejected_before_preprocessing_or_spawn(self):
        for timeout in (0, -1, 90.001, 300, True, float('inf'), float('nan'), '90'):
            with self.subTest(timeout=timeout), \
                    patch.object(optical_jobs, '_validate_derivative_png') as preprocess, \
                    patch.object(self.jobs._supervisor, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'time limit'):
                    self.jobs.recognize(self.data, 'deadline_invalid_1', timeout=timeout)
                preprocess.assert_not_called()
                run.assert_not_called()
                self.assertIsNone(self.jobs._active)

    def test_exhausted_preprocessing_budget_starts_neither_worker(self):
        def preprocess(*args):
            self.clock.advance(9)

        with patch.object(optical_jobs, '_validate_derivative_png', side_effect=preprocess), \
                patch.object(self.jobs._supervisor, 'run') as run, \
                patch.object(optical_jobs, 'validate_optical_result') as validate:
            with self.assertRaisesRegex(ValueError, 'time limit'):
                self.jobs.recognize(self.data, 'deadline_preprocess_1', timeout=9)
            run.assert_not_called()
            validate.assert_not_called()
        self.assertIsNone(self.jobs._active)

    def test_inference_exhaustion_does_not_start_validation_worker(self):
        def run(*args, **kwargs):
            self.clock.advance(10)
            return self.engine_result()

        with patch.object(self.jobs._supervisor, 'run', side_effect=run) as run_mock:
            with self.assertRaisesRegex(ValueError, 'time limit'):
                self.jobs.recognize(self.data, 'deadline_engine_1', timeout=10)
        self.assertEqual(run_mock.call_count, 1)
        self.assertIsNone(self.jobs._active)

    def test_late_validation_worker_response_is_rejected(self):
        def run(*args, **kwargs):
            if kwargs['input_text'] == self.raw_text:
                self.clock.advance(kwargs['timeout_seconds'])
                return self.validation_result()
            self.clock.advance(2)
            return self.engine_result()

        with patch.object(self.jobs._supervisor, 'run', side_effect=run) as run_mock:
            with self.assertRaisesRegex(ValueError, 'time limit'):
                self.jobs.recognize(self.data, 'deadline_validation_1', timeout=10)
        self.assertEqual(run_mock.call_count, 2)
        self.assertIsNone(self.jobs._active)

    def test_final_owner_check_rejects_validator_return_after_deadline(self):
        def validate(raw, digest, supervisor, remaining_time, token):
            self.clock.advance(remaining_time())
            return copy.deepcopy(self.candidate)

        with patch.object(self.jobs._supervisor, 'run', return_value=self.engine_result()), \
                patch.object(optical_jobs, 'validate_optical_result', side_effect=validate):
            with self.assertRaisesRegex(ValueError, 'time limit'):
                self.jobs.recognize(self.data, 'deadline_final_1', timeout=10)
        self.assertIsNone(self.jobs._active)


class ImageOpticalDeadlineTests(unittest.TestCase):
    # Reuse setup and fixture builders, without inheriting/rerunning their tests.
    JOB = session_fixtures.ImageOpticalSessionTests.JOB
    REQUEST = session_fixtures.ImageOpticalSessionTests.REQUEST
    tearDown = session_fixtures.ImageOpticalSessionTests.tearDown
    region = session_fixtures.ImageOpticalSessionTests.region
    recognize = session_fixtures.ImageOpticalSessionTests.recognize
    assert_unpublished = session_fixtures.ImageOpticalSessionTests.assert_unpublished

    def setUp(self):
        session_fixtures.ImageOpticalSessionTests.setUp(self)
        self.clock = ControlledClock()
        self.clock_patch = patch.object(image_session, 'time', self.clock.module())
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)

    def timed_preview(self, seconds):
        def region(*args, **kwargs):
            result = self.region(*args, **kwargs)
            self.clock.advance(seconds)
            return result
        self.session._preview_jobs.preview.side_effect = region

    def test_preview_time_reduces_remaining_recognition_budget(self):
        self.timed_preview(24)
        observed = []

        def recognize(data, request, *, timeout, _cancellation_token):
            observed.append(timeout)
            result = self.optical.result(data, request, timeout=timeout,
                                           _cancellation_token=_cancellation_token)
            self.clock.advance(4)
            return result

        self.optical.recognize.side_effect = recognize
        result = self.recognize()
        self.assertEqual(observed, [66.0])
        self.assertEqual(result['status'], 'recognized')
        self.assertEqual(result['selectedComponents'], ['optical-1'])
        self.assertFalse((self.root / 'preserved').exists())

    def test_late_preview_never_dispatches_optical_recognition(self):
        self.timed_preview(90)
        with self.assertRaisesRegex(ValueError, 'time limit'):
            self.recognize()
        self.optical.recognize.assert_not_called()
        self.assert_unpublished()
        self.assertIsNone(self.session._current['opticalRequest'])
        self.assertIsNone(self.session._current['opticalToken'])

    def test_candidate_returned_at_deadline_is_never_published(self):
        self.timed_preview(12)
        original = copy.deepcopy(self.session._current['result'])

        def recognize(data, request, *, timeout, _cancellation_token):
            self.assertEqual(timeout, 78.0)
            result = self.optical.result(data, request, timeout=timeout,
                                           _cancellation_token=_cancellation_token)
            self.clock.advance(timeout)
            return result

        self.optical.recognize.side_effect = recognize
        with self.assertRaisesRegex(ValueError, 'time limit'):
            self.recognize()
        self.optical.recognize.assert_called_once()
        self.assertEqual(self.session._current['result'], original)
        self.assertIsNone(self.session._current['opticalRegion'])
        self.assertIsNone(self.session._current['opticalRequest'])
        self.assertIsNone(self.session._current['opticalToken'])
        self.assert_unpublished()


@unittest.skipUnless(os.name == 'nt' and struct.calcsize('P') == 8,
                     'Protected optical children require 64-bit Windows.')
class OpticalValidationDeadlineProcessTests(unittest.TestCase):
    def setUp(self):
        self.supervisor = OpticalSupervisor()
        self.addCleanup(self.supervisor.shutdown)
        self.data = job_fixtures.png()
        self.raw = json.dumps(job_fixtures.prediction(self.data))
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.command = [str(Path(sys.executable).resolve()), '-I', '-B', '-u', '-c',
                        'import time; time.sleep(60)']

    def test_hung_validation_child_obeys_remaining_timeout(self):
        token = CancellationToken()
        started = time.monotonic()
        deadline = started + .3
        with patch.object(optical_validation, '_command', return_value=self.command):
            with self.assertRaises(subprocess.TimeoutExpired):
                optical_validation.validate_optical_result(self.raw, self.digest,
                    self.supervisor, lambda: deadline - time.monotonic(), token)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(self.supervisor.active_count, 0)

    def test_hung_validation_child_is_cancelled_and_reaped(self):
        token = CancellationToken()
        outcome = {}
        deadline = time.monotonic() + 10

        def validate():
            try:
                outcome['result'] = optical_validation.validate_optical_result(
                    self.raw, self.digest, self.supervisor,
                    lambda: deadline - time.monotonic(), token)
            except BaseException as error:
                outcome['error'] = error

        with patch.object(optical_validation, '_command', return_value=self.command):
            thread = threading.Thread(target=validate)
            thread.start()
            try:
                launch_deadline = time.monotonic() + 5
                while self.supervisor.active_count != 1 and time.monotonic() < launch_deadline:
                    if not thread.is_alive():
                        break
                    time.sleep(.01)
                self.assertEqual(self.supervisor.active_count, 1,
                                 'The synthetic validation child must start before cancellation.')
                token.cancel()
                thread.join(5)
            finally:
                token.cancel()
                if thread.is_alive():
                    self.supervisor.shutdown()
                    thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertIsInstance(outcome.get('error'), WorkerCancelled)
        self.assertNotIn('result', outcome)
        self.assertEqual(self.supervisor.active_count, 0)


if __name__ == '__main__':
    unittest.main()
