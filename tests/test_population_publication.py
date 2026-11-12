"""Stdlib-only service-return regressions with real owned child processes.

Events deliberately pause registry finalization, not the worker mathematics.
These checks do not start HTTP, RDKit, a browser, or a Windows native runtime.
Run from the source root: python -B -m unittest discover -s tests -p test_population_publication.py
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from nivram import population_jobs as jobs
from nivram.worker_processes import (CancellationToken, WorkerCancelled,
                                     WorkerProcessError, WorkerSupervisor)


OBSERVATIONS = []


def request(request_id='publication_test_001', *, text=None):
    if text is None:
        fixture = Path(jobs.__file__).resolve().parents[1] / 'tests/fixtures/population-analytical.json'
        model = json.loads(fixture.read_bytes())['complete']['envelope']['model']
        text = json.dumps(model, ensure_ascii=False, separators=(',', ':'))
    return dict(schemaVersion=1, requestId=request_id, documentRevision=7,
                graphHash='a' * 64, modelText=text,
                modelInputHash=hashlib.sha256(text.encode('utf-8')).hexdigest())


class ObservedSupervisor(WorkerSupervisor):
    def __init__(self, *, cleanup_error=False):
        super().__init__(max_workers=1)
        self.handles = []
        self.wires = []
        self.options = []
        self.cleanup_error = cleanup_error

    def _spawn(self, *args, **kwargs):
        handle = super()._spawn(*args, **kwargs)
        self.handles.append(handle)
        return handle

    def run(self, *args, **kwargs):
        self.options.append(kwargs.copy())
        result = super().run(*args, **kwargs)
        self.wires.append(result.stdout.encode('utf-8'))
        return result

    def _release(self, process):
        super()._release(process)
        if self.cleanup_error:
            # Fault injection after actual owned-child cleanup, not a claim that
            # the portable supervisor spontaneously failed to close this child.
            raise WorkerProcessError('Synthetic release failure.')


class FinalizerSchedule:
    """Pause a selected thread's second registry-lock use, once only."""
    def __init__(self, lock, *, phase):
        self.lock = lock
        self.phase = phase
        self.target = None
        self.entries = 0
        self.entered = threading.Event()
        self.resume = threading.Event()

    def _pause(self):
        self.entered.set()
        if not self.resume.wait(5):
            raise RuntimeError('Controlled finalizer gate was not released.')

    def __enter__(self):
        if threading.current_thread() is self.target:
            self.entries += 1
            if self.entries == 2 and self.phase == 'before':
                self._pause()
        return self.lock.__enter__()

    def __exit__(self, *args):
        result = self.lock.__exit__(*args)
        if (threading.current_thread() is self.target and self.entries == 2
                and self.phase == 'after'):
            self._pause()
        return result


class PopulationPublicationTests(unittest.TestCase):
    def assert_stopped(self, supervisor, *, expected_count=1, successful_worker=False):
        self.assertEqual(len(supervisor.handles), expected_count)
        self.assertEqual(supervisor.active_count, 0)
        for handle in supervisor.handles:
            self.assertIsNotNone(handle.poll())
            if successful_worker:
                self.assertEqual(handle.poll(), 0)
            self.assertTrue(all(pipe is None or pipe.closed for pipe in
                                (handle.stdin, handle.stdout, handle.stderr)))

    def gated(self, action, *, phase='before', worker='normal'):
        supervisor = ObservedSupervisor(cleanup_error=worker == 'cleanup_error')
        command = None
        timeout = jobs.DEFAULT_TIMEOUT
        if worker == 'malformed':
            command = lambda: [sys.executable, '-B', '-c',
                'import sys; sys.stdin.buffer.read(); print("invalid JSON")']
        elif worker == 'timeout':
            command = lambda: [sys.executable, '-B', '-c',
                'import sys,time; sys.stdin.buffer.read(); time.sleep(30)']
            timeout = .08
        elif worker == 'overflow':
            command = lambda: [sys.executable, '-B', '-c',
                'import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b"x"*1025)']
        service = jobs.PopulationJobs(supervisor=supervisor, command_factory=command)
        token = CancellationToken()
        value = request(text='{}' if worker == 'invalid_model' else None)
        original_request = copy.deepcopy(value)
        schedule = FinalizerSchedule(service._lock, phase=phase)
        service._lock = schedule
        outcomes = []

        def evaluate():
            try:
                outcomes.append(service.evaluate(value, cancellation_token=token, timeout=timeout))
            except BaseException as error:
                outcomes.append(error)

        thread = threading.Thread(target=evaluate, name='population-publication')
        schedule.target = thread
        started = time.perf_counter()
        thread.start()
        action_result = None
        try:
            self.assertTrue(schedule.entered.wait(5), 'Owned worker did not reach finalization.')
            self.assert_stopped(supervisor, successful_worker=worker == 'normal')
            self.assertEqual(service.snapshot()['activeJobs'], int(phase == 'before'))
            if action == 'service_cancel':
                action_result = service.cancel({'schemaVersion': 1, 'requestId': value['requestId']})
                self.assertEqual(action_result['status'], 'cancellation_requested')
            elif action == 'token_cancel':
                token.cancel()
                self.assertTrue(token.cancelled)
            elif action == 'shutdown':
                self.assertTrue(service.shutdown())
            elif action == 'supervisor_shutdown':
                self.assertTrue(supervisor.shutdown())
                self.assertFalse(token.cancelled)
                self.assertFalse(service._closed)
            elif action != 'control':
                raise AssertionError(action)
        finally:
            schedule.resume.set()
            thread.join(5)
            shutdown_confirmed = service.shutdown()
        self.assertFalse(thread.is_alive())
        self.assertTrue(shutdown_confirmed)
        self.assertEqual(len(outcomes), 1)
        self.assert_stopped(supervisor, successful_worker=worker == 'normal')
        self.assertEqual(value, original_request)
        self.assertIsNone(service._active)
        self.assertEqual(supervisor.options[0]['timeout_seconds'], timeout)
        self.assertEqual(supervisor.options[0]['max_input_bytes'], jobs.MAX_TRANSPORT_BYTES)
        self.assertEqual(supervisor.options[0]['max_stdout_bytes'], jobs.MAX_OUTPUT_BYTES)
        self.assertEqual(supervisor.options[0]['max_stderr_bytes'], 8192)
        self.assertIs(supervisor.options[0]['cancellation_token'], token)
        result = outcomes[0]
        history = service._history[value['requestId']][0]
        expected_history = ('cancelled' if phase == 'before' and action in
                            ('service_cancel', 'token_cancel', 'shutdown') else 'finished')
        self.assertEqual(history, expected_history)
        wire = supervisor.wires[0] if supervisor.wires else None
        OBSERVATIONS.append(dict(action=action, phase=phase, worker=worker,
            result_type=type(result).__name__, error_code=getattr(result, 'code', None),
            published_bytes=isinstance(result, bytes), exact_wire=result == wire if isinstance(result, bytes) else False,
            wire_bytes=len(wire) if wire is not None else None,
            wire_sha256=hashlib.sha256(wire).hexdigest() if wire is not None else None,
            history_status=history, action_result=action_result, child_count=len(supervisor.handles),
            child_returncodes=[handle.poll() for handle in supervisor.handles],
            active_children_after=supervisor.active_count, exited_and_pipes_closed=True,
            shutdown_confirmed=shutdown_confirmed, elapsed_seconds=time.perf_counter() - started))
        return result, wire

    def test_exact_byte_control_before_and_after_registry_cleanup(self):
        for phase in ('before', 'after'):
            with self.subTest(phase=phase):
                result, wire = self.gated('control', phase=phase)
                self.assertIs(type(result), bytes)
                self.assertEqual(result, wire)

    def test_api_cancel_before_finalizer_blocks_publication(self):
        result, _ = self.gated('service_cancel')
        self.assertTrue(isinstance(result, WorkerCancelled), 'Cancelled worker bytes must not be published.')

    def test_original_token_cancel_at_either_finalizer_boundary_blocks_publication(self):
        for phase in ('before', 'after'):
            with self.subTest(phase=phase):
                result, _ = self.gated('token_cancel', phase=phase)
                self.assertTrue(isinstance(result, WorkerCancelled), 'Cancelled worker bytes must not be published.')

    def test_service_shutdown_at_either_finalizer_boundary_blocks_publication(self):
        for phase in ('before', 'after'):
            with self.subTest(phase=phase):
                result, _ = self.gated('shutdown', phase=phase)
                self.assertTrue(isinstance(result, WorkerCancelled), 'Closed-service bytes must not be published.')

    def test_supervisor_shutdown_at_either_finalizer_boundary_blocks_publication(self):
        for phase in ('before', 'after'):
            with self.subTest(phase=phase):
                result, _ = self.gated('supervisor_shutdown', phase=phase)
                self.assertTrue(isinstance(result, WorkerCancelled), 'Closed-supervisor bytes must not be published.')

    def test_primary_errors_keep_precedence_over_finalizer_cancellation(self):
        for worker, code in (('invalid_model', 'invalid_model'), ('malformed', 'worker_failed'),
                             ('timeout', 'timed_out'), ('cleanup_error', 'worker_failed'),
                             ('overflow', 'output_too_large')):
            with self.subTest(worker=worker), patch.object(jobs, 'MAX_OUTPUT_BYTES',
                    1024 if worker == 'overflow' else jobs.MAX_OUTPUT_BYTES):
                result, _ = self.gated('token_cancel', worker=worker)
                self.assertIsInstance(result, jobs.PopulationJobError)
                self.assertEqual(result.code, code)

    def test_later_cancelled_request_does_not_cancel_completed_original_attempt(self):
        supervisor = ObservedSupervisor()
        service = jobs.PopulationJobs(supervisor=supervisor)
        schedule = FinalizerSchedule(service._lock, phase='after')
        service._lock = schedule
        original_token = CancellationToken()
        second_token = CancellationToken()
        outcomes = []
        second_registered = threading.Event()
        release_second = threading.Event()
        original = request('publication_original')
        second = request('publication_second')
        original_before, second_before = copy.deepcopy(original), copy.deepcopy(second)

        def evaluate(value, token, results):
            try:
                results.append(service.evaluate(value, cancellation_token=token))
            except BaseException as error:
                results.append(error)

        first = threading.Thread(target=evaluate, args=(original, original_token, outcomes))
        schedule.target = first
        second_outcomes = []
        other = threading.Thread(target=evaluate, args=(second, second_token, second_outcomes))
        # A command factory runs only after the attempt is registered. Blocking
        # here controls admission without spawning an extra waiting process.
        def second_command():
            second_registered.set()
            if not release_second.wait(5):
                raise RuntimeError('Second request was not released.')
            return jobs.worker_command()

        first.start()
        try:
            self.assertTrue(schedule.entered.wait(5))
            self.assert_stopped(supervisor, successful_worker=True)
            self.assertEqual(service.cancel({'schemaVersion': 1, 'requestId': original['requestId']}),
                             {'status': 'finished'})
            service._command_factory = second_command
            other.start()
            self.assertTrue(second_registered.wait(5))
            self.assertEqual(service.cancel({'schemaVersion': 1, 'requestId': second['requestId']}),
                             {'status': 'cancellation_requested'})
            self.assertTrue(second_token.cancelled)
            self.assertFalse(original_token.cancelled)
            # Resume the first return while the second, cancelled token is still
            # the active registry entry. Publication must use the original token.
            schedule.resume.set()
            first.join(5)
            self.assertFalse(first.is_alive())
            self.assertEqual(len(outcomes), 1)
            self.assertIs(type(outcomes[0]), bytes)
            self.assertEqual(outcomes[0], supervisor.wires[0])
            self.assertIs(service._active[1], second_token)
        finally:
            schedule.resume.set()
            release_second.set()
            first.join(5)
            if other.ident is not None:
                other.join(5)
            shutdown_confirmed = service.shutdown()
        self.assertFalse(first.is_alive())
        self.assertFalse(other.is_alive())
        self.assertTrue(shutdown_confirmed)
        self.assertEqual(len(second_outcomes), 1)
        self.assertIsInstance(second_outcomes[0], WorkerCancelled)
        self.assertEqual(service._history[original['requestId']][0], 'finished')
        self.assertEqual(service._history[second['requestId']][0], 'cancelled')
        self.assertEqual(original, original_before)
        self.assertEqual(second, second_before)
        self.assert_stopped(supervisor, successful_worker=True)

    def test_injected_supervisor_requires_no_new_closed_property(self):
        value = request('publication_injected')
        token = CancellationToken()
        real = ObservedSupervisor()
        try:
            completed = real.run(jobs.worker_command(), input_text=json.dumps(value),
                timeout_seconds=jobs.DEFAULT_TIMEOUT, max_input_bytes=jobs.MAX_TRANSPORT_BYTES,
                max_stdout_bytes=jobs.MAX_OUTPUT_BYTES)
        finally:
            self.assertTrue(real.shutdown())
        self.assert_stopped(real, successful_worker=True)
        supervisor = Mock(spec=['run', 'check_cancelled', 'shutdown'])
        supervisor.run.return_value = completed
        service = jobs.PopulationJobs(supervisor=supervisor)
        self.assertEqual(service.evaluate(value, cancellation_token=token), real.wires[0])
        self.assertTrue(supervisor.check_cancelled.call_args_list)
        self.assertTrue(all(call.args == (token,) for call in
                            supervisor.check_cancelled.call_args_list))
        service.shutdown()

    def test_caps_timeout_and_parent_import_boundary_remain_unchanged(self):
        self.assertEqual(jobs.DEFAULT_TIMEOUT, 60.0)
        self.assertEqual((jobs.MAX_MODEL_BYTES, jobs.MAX_TRANSPORT_BYTES, jobs.MAX_OUTPUT_BYTES),
                         (2 * 1024**2, 8 * 1024**2, 16 * 1024**2))
        self.assertEqual((jobs.MAX_HISTORY, jobs.HISTORY_SECONDS), (128, 120.0))
        self.assertFalse(any(name == 'nivram.server' or name.startswith(('rdkit', 'torch', 'numpy'))
                             for name in sys.modules))
        self.assertNotIn('nivram.populations', sys.modules)


if __name__ == '__main__':
    unittest.main()
