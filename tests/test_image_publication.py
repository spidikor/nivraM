"""Service publication schedules with synthetic PNGs and no native chemistry/ML.

Worker execution and optical chemistry validation are controlled collaborators.
Real service registration, cancellation, PNG validation, snapshot cleanup and
retirement run; no scientific validity is inferred from the synthetic results.
Lock gates schedule the original calling thread before or after retirement, not
inside cancellation logic. Two tests also use real owned Python JSON children.
"""
import base64
from contextlib import ExitStack
import hashlib
import json
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
import zlib

from nivram import image_jobs, optical_jobs, png_preview_jobs
from nivram.worker_processes import CancellationToken, WorkerCancelled, WorkerProcessError


def chunk(kind, data):
    return (struct.pack('>I', len(data)) + kind + data
            + struct.pack('>I', zlib.crc32(kind + data)))


PNG = (b'\x89PNG\r\n\x1a\n'
       + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
       + chunk(b'IDAT', zlib.compress(b'\x00\xff\xff\xff')) + chunk(b'IEND', b''))
DIGEST = hashlib.sha256(PNG).hexdigest()
RECT = {'x': 0, 'y': 0, 'width': 1, 'height': 1}
CANDIDATE = {'id': 'synthetic-candidate', 'graph': {'atoms': [], 'bonds': []}}
METADATA = {'schemaVersion': 1, 'candidates': [CANDIDATE]}
PREVIEW = dict(schemaVersion=1, sourceSha256=DIGEST, sourceWidth=1, sourceHeight=1,
               mode='crop', rect=RECT, width=1, height=1,
               pngBase64=base64.b64encode(PNG).decode('ascii'), pngSha256=DIGEST,
               engine={}, bufferAccounting={}, navigationOnly=False, resampling='none')
OPTICAL = {'schemaVersion': 1, 'inputSha256': DIGEST, 'candidate': CANDIDATE}
SERVICES = ('metadata', 'preview', 'optical')


class LockGate:
    """Pause a named calling thread at one real service-lock boundary."""
    def __init__(self, lock):
        self.lock = lock
        self.counts = {}
        self.gates = {}

    def add(self, name, count, phase='before'):
        entered, release = threading.Event(), threading.Event()
        self.gates[name, count, phase] = entered, release
        return entered, release

    def pause(self, count, phase):
        gate = self.gates.get((threading.current_thread().name, count, phase))
        if gate:
            gate[0].set()
            if not gate[1].wait(5):
                raise AssertionError('Service lock gate was not released.')

    def __enter__(self):
        name = threading.current_thread().name
        count = self.counts[name] = self.counts.get(name, 0) + 1
        self.pause(count, 'before')
        self.lock.acquire()
        return self

    def __exit__(self, *args):
        count = self.counts[threading.current_thread().name]
        self.lock.release()
        self.pause(count, 'after')


class ProcessDouble:
    def __init__(self, output):
        self.output, self.returncode = output, 0
        self.timeouts = []

    def communicate(self, data=None, timeout=None):
        self.timeouts.append(timeout)
        return self.output, b''

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9

    def terminate(self):
        self.returncode = -15


class ServiceCase:
    def __init__(self, kind, *, real_child=False):
        self.kind, self.real_child = kind, real_child
        self.stack = ExitStack()
        self.event, self.token = threading.Event(), CancellationToken()
        self.now = 0.0
        self.calls = []

    def __enter__(self):
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        if self.kind == 'metadata':
            self.jobs = image_jobs.ImageJobs()
            self.process = ProcessDouble(json.dumps(METADATA).encode())
            if not self.real_child:
                self.stack.enter_context(patch.object(image_jobs.subprocess, 'Popen', return_value=self.process))
            self.target, self.expected = 4, METADATA
        elif self.kind == 'preview':
            self.jobs = png_preview_jobs.ImagePreviewJobs(self.folder / 'cache')
            if not self.real_child:
                self.stack.enter_context(patch.object(self.jobs._supervisor, 'run', side_effect=self.run_worker))
            self.target, self.expected = 2, PREVIEW
        else:
            self.jobs = optical_jobs.OpticalJobs(Path(sys.executable).resolve(), self.folder / 'absent-model')
            self.stack.enter_context(patch.object(self.jobs, 'availability', return_value={'status': 'configured'}))
            self.stack.enter_context(patch.object(self.jobs._supervisor, 'run', side_effect=self.run_worker))
            self.stack.enter_context(patch.object(optical_jobs, 'validate_optical_result', return_value=CANDIDATE))
            self.stack.enter_context(patch.object(optical_jobs, 'time', SimpleNamespace(monotonic=lambda: self.now)))
            self.target, self.expected = 3, OPTICAL
        self.gate = LockGate(self.jobs._lock)
        self.jobs._lock = self.gate
        return self

    def run_worker(self, command, **options):
        self.calls.append(options)
        output = PREVIEW if self.kind == 'preview' else {'inputSha256': DIGEST}
        return subprocess.CompletedProcess(command, 0, json.dumps(output), '')

    def invoke(self, name='original_job', timeout=45, external=False):
        command = None
        if self.real_child:
            reply = METADATA if self.kind == 'metadata' else PREVIEW
            command = [sys.executable, '-I', '-B', '-c',
                       'import sys; sys.stdin.buffer.read(); print(' + repr(json.dumps(reply)) + ')']
        if self.kind == 'metadata':
            return self.jobs.inspect(PNG, name, timeout=timeout, _command=command,
                                     _cancel_event=self.event if external else None)
        if self.kind == 'preview':
            return self.jobs.preview(PNG, name, name + '_region', 'crop', RECT,
                                     timeout=timeout, _command=command)
        return self.jobs.recognize(PNG, name, timeout=timeout,
                                  _cancellation_token=self.token if external else None)

    def cancel(self, name='original_job'):
        if self.kind == 'preview':
            return self.jobs.cancel(name, name + '_region')
        return self.jobs.cancel(name)

    def external_cancel(self):
        if self.kind == 'metadata':
            self.event.set()
        elif self.kind == 'optical':
            self.token.cancel()
        else:
            self.jobs._active['token'].cancel()

    def __exit__(self, *args):
        try:
            self.jobs.close()
        finally:
            self.stack.close()


class ImagePublicationTests(unittest.TestCase):
    def start(self, case, *, count=None, phase='before', name='original_job', **options):
        entered, release = case.gate.add(name, case.target if count is None else count, phase)
        outcome = {}
        def run():
            try:
                outcome['result'] = case.invoke(name, **options)
            except Exception as error:
                outcome['error'] = error
        thread = threading.Thread(target=run, name=name)
        thread.start()
        self.addCleanup(self.release_and_join, release, thread)
        self.assertTrue(entered.wait(5), outcome)
        self.assertTrue(thread.is_alive())
        return release, thread, outcome

    def release_and_join(self, release, thread):
        release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive(), 'Owned test caller did not finish.')

    def finish(self, pending):
        release, thread, outcome = pending
        self.release_and_join(release, thread)
        return outcome

    def assert_cancelled(self, case, outcome):
        self.assertNotIn('result', outcome)
        expected = image_jobs.ImageJobCancelled if case.kind == 'metadata' else WorkerCancelled
        self.assertIsInstance(outcome.get('error'), expected, outcome)

    def test_success_bytes_and_shapes_survive_retirement(self):
        for kind in SERVICES:
            with self.subTest(service=kind), ServiceCase(kind) as case:
                outcome = self.finish(self.start(case))
                self.assertEqual(outcome, {'result': case.expected})
                self.assertEqual(json.dumps(outcome['result']), json.dumps(case.expected))
                self.assertIsNone(case.jobs._active)
                if kind == 'preview':
                    self.assertEqual(list(case.jobs.directory.iterdir()), [])

    def test_acknowledged_cancel_while_retirement_waits_rejects_success(self):
        for kind in SERVICES:
            with self.subTest(service=kind), ServiceCase(kind) as case:
                pending = self.start(case)
                self.assertEqual(case.cancel(), {'status': 'cancelled'})
                self.assert_cancelled(case, self.finish(pending))
                self.assertIsNone(case.jobs._active)
                self.assertEqual(case.invoke('following_job'), case.expected)

    def test_close_while_retirement_waits_rejects_success(self):
        for kind in SERVICES:
            with self.subTest(service=kind), ServiceCase(kind) as case:
                pending = self.start(case)
                receipt = case.jobs.close()
                self.assertIs(receipt, None if kind == 'metadata' else True)
                self.assert_cancelled(case, self.finish(pending))
                self.assertIsNone(case.jobs._active)

    def test_nonmatching_cancel_does_not_revoke_original_success(self):
        for kind in SERVICES:
            with self.subTest(service=kind), ServiceCase(kind) as case:
                pending = self.start(case)
                self.assertEqual(case.cancel('unrelated_job'), {'status': 'not_running'})
                self.assertEqual(self.finish(pending), {'result': case.expected})

    def test_original_external_event_or_token_during_retirement_rejects_success(self):
        for kind in SERVICES:
            with self.subTest(service=kind), ServiceCase(kind) as case:
                pending = self.start(case, external=True)
                case.external_cancel()
                self.assert_cancelled(case, self.finish(pending))

    def test_cancel_after_retirement_is_not_acknowledged_and_does_not_revoke_success(self):
        for kind in SERVICES:
            with self.subTest(service=kind), ServiceCase(kind) as case:
                pending = self.start(case, phase='after')
                self.assertIsNone(case.jobs._active)
                self.assertEqual(case.cancel(), {'status': 'not_running'})
                self.assertEqual(self.finish(pending), {'result': case.expected})

    def test_external_original_event_or_token_after_retirement_rejects_success(self):
        # Preview has no external token input; its token is still captured below.
        for kind in SERVICES:
            with self.subTest(service=kind), ServiceCase(kind) as case:
                before = self.start(case, external=True)
                original = case.jobs._active
                entered, release = case.gate.add('original_job', case.target, 'after')
                self.addCleanup(self.release_and_join, release, before[1])
                before[0].set()
                self.assertTrue(entered.wait(5))
                self.assertIsNone(case.jobs._active)
                if kind == 'preview':
                    original['token'].cancel()
                else:
                    case.external_cancel()
                self.assert_cancelled(case, self.finish((release, before[1], before[2])))

    def test_close_after_retirement_rejects_supervised_success_only(self):
        for kind in SERVICES:
            with self.subTest(service=kind), ServiceCase(kind) as case:
                pending = self.start(case, phase='after')
                self.assertIsNone(case.jobs._active)
                receipt = case.jobs.close()
                self.assertIs(receipt, None if kind == 'metadata' else True)
                outcome = self.finish(pending)
                if kind == 'metadata':
                    # This service has no lasting close state or shutdown receipt.
                    self.assertEqual(outcome, {'result': case.expected})
                else:
                    self.assert_cancelled(case, outcome)

    def test_cancelled_successor_cannot_revoke_or_replace_original_result(self):
        for kind in SERVICES:
            with self.subTest(service=kind), ServiceCase(kind) as case:
                original = self.start(case, phase='after')
                successor = self.start(case, name='successor_job')
                successor_state = case.jobs._active
                self.assertEqual(case.cancel('successor_job'), {'status': 'cancelled'})
                self.assertEqual(self.finish(original), {'result': case.expected})
                self.assertIs(case.jobs._active, successor_state)
                self.assert_cancelled(case, self.finish(successor))

    def test_original_cancellation_cannot_be_cleared_by_successor_registration(self):
        for kind in SERVICES:
            with self.subTest(service=kind), ServiceCase(kind) as case:
                original = self.start(case)
                self.assertEqual(case.cancel(), {'status': 'cancelled'})
                entered, release = case.gate.add('original_job', case.target, 'after')
                self.addCleanup(self.release_and_join, release, original[1])
                original[0].set()
                self.assertTrue(entered.wait(5))
                successor = self.start(case, name='successor_job')
                successor_state = case.jobs._active
                self.assert_cancelled(case, self.finish((release, original[1], original[2])))
                self.assertIs(case.jobs._active, successor_state)
                self.assertEqual(self.finish(successor), {'result': case.expected})

    def test_existing_optical_total_deadline_includes_retirement_and_maps_error(self):
        with ServiceCase('optical') as case:
            pending = self.start(case, timeout=0.1)
            case.now = 0.1
            outcome = self.finish(pending)
            error = outcome.get('error')
            self.assertNotIn('result', outcome)
            self.assertIs(type(error), ValueError)
            self.assertEqual(str(error), 'Local structure recognition reached its time limit. The drawing is unchanged.')
            self.assertIsInstance(error.__cause__, subprocess.TimeoutExpired)
            self.assertEqual(case.invoke('following_job', timeout=0.1), OPTICAL)

    def test_optical_cancellation_retains_precedence_over_expired_total_deadline(self):
        with ServiceCase('optical') as case:
            pending = self.start(case, timeout=0.1)
            case.now = 1
            case.cancel()
            self.assert_cancelled(case, self.finish(pending))

    def test_metadata_and_preview_timeout_remains_worker_only(self):
        for kind in ('metadata', 'preview'):
            with self.subTest(service=kind), ServiceCase(kind) as case:
                pending = self.start(case, timeout=0.01)
                time.sleep(0.03)
                self.assertEqual(self.finish(pending), {'result': case.expected})
                actual = case.process.timeouts[0] if kind == 'metadata' else case.calls[0]['timeout_seconds']
                self.assertEqual(actual, 0.01)

    def test_validation_error_is_not_replaced_by_late_cancellation(self):
        for kind in SERVICES:
            with self.subTest(service=kind), ServiceCase(kind) as case:
                original = ValueError('controlled validation failure')
                if kind == 'metadata':
                    context = patch.object(image_jobs.json, 'loads', side_effect=original)
                    count = case.target
                elif kind == 'preview':
                    context = patch.object(png_preview_jobs, 'validate_preview_result', side_effect=original)
                    count = case.target
                else:
                    context = patch.object(optical_jobs, 'validate_optical_result', side_effect=original)
                    count = 2
                with context:
                    pending = self.start(case, count=count)
                    case.cancel()
                    case.now = 100
                    self.assertIs(self.finish(pending).get('error'), original)

    def test_metadata_invalid_worker_result_is_not_replaced_by_cancel(self):
        for output in (b'[]', b'not-json'):
            with self.subTest(output=output), ServiceCase('metadata') as case:
                case.process.output = output
                pending = self.start(case)
                case.cancel()
                error = self.finish(pending).get('error')
                self.assertIsInstance(error, ValueError)
                self.assertNotIsInstance(error, image_jobs.ImageJobCancelled)

    def test_worker_failure_is_not_replaced_by_late_cancellation(self):
        for kind in SERVICES:
            with self.subTest(service=kind), ServiceCase(kind) as case:
                if kind == 'metadata':
                    case.process.returncode = 2
                    context, count = ExitStack(), case.target
                else:
                    output = '{}' if kind == 'optical' else ''
                    context = patch.object(case.jobs._supervisor, 'run', return_value=subprocess.CompletedProcess([], 2, output, ''))
                    count = 2
                with context:
                    pending = self.start(case, count=count)
                    case.cancel()
                    error = self.finish(pending).get('error')
                    self.assertIs(type(error), ValueError)
                    self.assertIn('could not', str(error))

    def test_worker_timeout_mapping_is_not_replaced_by_late_cancellation(self):
        for kind in SERVICES:
            with self.subTest(service=kind), ServiceCase(kind) as case:
                failure = subprocess.TimeoutExpired(['controlled'], 1)
                if kind == 'metadata':
                    context = patch.object(case.process, 'communicate', side_effect=[failure, (b'', b'')])
                    count = 3
                else:
                    context = patch.object(case.jobs._supervisor, 'run', side_effect=failure)
                    count = 2
                with context:
                    pending = self.start(case, count=count)
                    case.cancel()
                    error = self.finish(pending).get('error')
                    if kind == 'preview':
                        self.assertIs(error, failure)
                    else:
                        self.assertIs(type(error), ValueError)
                        self.assertIn('time limit', str(error))
                        self.assertIs(error.__cause__, failure)

    def test_preview_cleanup_failure_is_not_replaced_by_cancel(self):
        with ServiceCase('preview') as case:
            failure = OSError('controlled retained snapshot')
            with patch.object(Path, 'unlink', side_effect=failure):
                pending = self.start(case)
                case.cancel()
                error = self.finish(pending).get('error')
            self.assertIs(type(error), ValueError)
            self.assertIn('cleanup could not be confirmed', str(error))
            self.assertIs(error.__cause__, failure)
            snapshots = list(case.jobs.directory.glob('png-*/source.png'))
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0].read_bytes(), PNG)
            self.assertIsNone(case.jobs._active)

    def test_metadata_cleanup_failure_is_not_replaced_by_external_cancel(self):
        with ServiceCase('metadata') as case:
            failure = OSError('controlled cleanup failure')
            def uncertain_poll():
                case.event.set()
                raise failure
            with patch.object(case.process, 'poll', side_effect=uncertain_poll):
                with self.assertRaises(OSError) as caught:
                    case.invoke(external=True)
            self.assertIs(caught.exception, failure)

    def test_optical_uncertain_worker_cleanup_closes_owner_and_preserves_failure(self):
        with ServiceCase('optical') as case:
            failure = WorkerProcessError('controlled uncertain cleanup')
            with patch.object(case.jobs._supervisor, 'run', side_effect=failure):
                with self.assertRaises(WorkerProcessError) as caught:
                    case.invoke(external=True)
            self.assertIs(caught.exception, failure)
            self.assertTrue(case.jobs._closed)
            self.assertTrue(case.jobs._supervisor.closed)
            self.assertTrue(case.token.cancelled)
            self.assertIsNone(case.jobs._active)
            with self.assertRaises(WorkerCancelled):
                case.invoke('following_job')

    def test_real_owned_json_children_cannot_publish_after_acknowledged_cancel(self):
        for kind in ('metadata', 'preview'):
            with self.subTest(service=kind), ServiceCase(kind, real_child=True) as case:
                pending = self.start(case)
                state = case.jobs._active
                self.assertEqual(case.cancel(), {'status': 'cancelled'})
                self.assert_cancelled(case, self.finish(pending))
                if kind == 'metadata':
                    self.assertEqual(state['process'].poll(), 0)
                else:
                    self.assertEqual(case.jobs._supervisor.active_count, 0)
                    self.assertEqual(list(case.jobs.directory.iterdir()), [])

    def test_real_owned_json_children_preserve_success(self):
        for kind in ('metadata', 'preview'):
            with self.subTest(service=kind), ServiceCase(kind, real_child=True) as case:
                self.assertEqual(self.finish(self.start(case)), {'result': case.expected})
                self.assertIsNone(case.jobs._active)


if __name__ == '__main__':
    unittest.main()
