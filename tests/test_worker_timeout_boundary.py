"""Bounded timeout facts must describe the existing supervise-loop boundary.

No native UI or model is launched. Two tests own finite sleeping Python children;
one uses an exited fake child with a blocked pipe to distinguish those states.
"""
import io
import json
import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from nivram import acceptance_batch_png, native_dialog_fixture, worker_processes


FIELDS = {'stage', 'child_created', 'child_running', 'returncode',
          'stdin_done', 'stdout_done', 'stderr_done'}
PRIVATE = 'PRIVATE_TIMEOUT_COMMAND_INPUT_OUTPUT_NONCE_CAPTION'


def packet(*, running=True, returncode=None, stdin=True, stdout=False, stderr=False):
    return {'stage': 'supervise', 'child_created': True, 'child_running': running,
            'returncode': returncode, 'stdin_done': stdin, 'stdout_done': stdout,
            'stderr_done': stderr}


def unavailable():
    return {'available': False, **{name: None for name in FIELDS}}


class BoundaryAssertions:
    def sanitize(self, value):
        function = getattr(worker_processes, 'timeout_boundary_diagnostic', None)
        self.assertTrue(callable(function), 'The bounded timeout diagnostic sanitizer is missing.')
        result = function(value)
        self.assertIs(type(result), dict)
        self.assertEqual(set(result), FIELDS | {'available'})
        return result

    def assert_unavailable(self, value):
        result = self.sanitize(value)
        self.assertEqual(result, unavailable())
        self.assertNotIn(PRIVATE, json.dumps(result, allow_nan=False))
        return result


class TimeoutBoundarySanitizerTests(BoundaryAssertions, unittest.TestCase):
    def test_valid_supervise_packets_and_already_sanitized_packets_are_independent(self):
        cases = [packet(), packet(stdin=False),
                 packet(running=False, returncode=0, stdout=False, stderr=True),
                 packet(running=False, returncode=-(2**31)),
                 packet(running=False, returncode=2**32-1)]
        for raw in cases:
            with self.subTest(raw=raw):
                expected = {'available': True, **raw}
                observed = self.sanitize(raw)
                self.assertEqual(observed, expected)
                self.assertIsNot(observed, raw)
                raw['stdin_done'] = not raw['stdin_done']
                self.assertEqual(observed, expected)
                again = self.sanitize(observed)
                self.assertEqual(again, expected)
                self.assertIsNot(again, observed)
        rejected = unavailable()
        observed = self.assert_unavailable(rejected)
        self.assertIsNot(observed, rejected)

    def test_invalid_incoherent_nonfinite_and_out_of_bounds_fields_reject(self):
        replacements = [
            ('stage', None), ('stage', PRIVATE), ('stage', 'before_spawn'),
            ('child_created', False), ('child_created', 1),
            ('child_running', 1), ('child_running', None), ('child_running', False),
            ('returncode', True), ('returncode', 0), ('returncode', 0.0),
            ('returncode', -(2**31)-1), ('returncode', 2**32),
            ('returncode', float('nan')), ('returncode', float('inf')),
            ('stdin_done', 1), ('stdout_done', None), ('stderr_done', 'false'),
        ]
        for name, value in replacements:
            raw = packet(); raw[name] = value
            with self.subTest(name=name, value=value):
                self.assert_unavailable(raw)
        raw = packet(running=False, returncode=0)
        raw['child_running'] = True
        self.assert_unavailable(raw)
        self.assert_unavailable({'stage': 'before_spawn', 'child_created': False,
            'child_running': None, 'returncode': None, 'stdin_done': None,
            'stdout_done': None, 'stderr_done': None})

    def test_subclasses_and_custom_mapping_methods_are_not_executed(self):
        class Hostile(dict):
            def get(self, *args, **kwargs):
                raise AssertionError('A custom mapping must not run inside the diagnostic gate.')

            def __iter__(self):
                raise AssertionError('A custom mapping must not run inside the diagnostic gate.')

            def keys(self):
                raise AssertionError('A custom mapping must not run inside the diagnostic gate.')

        self.assert_unavailable(Hostile(packet()))
        self.assert_unavailable(Hostile({'available': True, **packet()}))

    def test_private_extra_missing_keys_nonobjects_and_invalid_available_flag_reject(self):
        self.assert_unavailable({**packet(), 'nonce': PRIVATE})
        self.assert_unavailable({'available': True, **packet(), 'output': PRIVATE})
        for name in FIELDS:
            raw = packet(); del raw[name]
            self.assert_unavailable(raw)
        for value in (None, False, 4, PRIVATE, [], [packet()], SimpleNamespace(**packet())):
            self.assert_unavailable(value)
        for flag in (False, 1, None, PRIVATE):
            self.assert_unavailable({'available': flag, **packet()})


class WorkerTimeoutBoundaryTests(BoundaryAssertions, unittest.TestCase):
    def command(self):
        # Use the interpreter hosting this process, avoiding a Windows venv redirector.
        return [native_dialog_fixture._source_interpreter(), '-I', '-S', '-u', '-c',
                'import time; time.sleep(60)']

    def supervise_timeout(self, *, input_text, max_input_bytes=65536):
        supervisor = worker_processes.WorkerSupervisor(max_workers=1)
        try:
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                supervisor.run(self.command(), input_text=input_text, timeout_seconds=0.75,
                               max_input_bytes=max_input_bytes)
            self.assertEqual(supervisor.active_count, 0)
            error = caught.exception
            self.assertEqual(error.cmd, ['local-scientific-worker'])
            self.assertIsNone(error.output)
            self.assertIsNone(error.stderr)
            self.assertNotIn(PRIVATE, str(error))
            raw = getattr(error, 'timeout_boundary', None)
            self.assertIs(type(raw), dict,
                          'A supervise-loop timeout must retain its fixed deadline-boundary facts.')
            observed = self.sanitize(raw)
            self.assertIs(observed['available'], True)
            self.assertNotIn(PRIVATE, json.dumps(observed, allow_nan=False))
            return observed
        finally:
            self.assertTrue(supervisor.shutdown(timeout_seconds=2))
            self.assertEqual(supervisor.active_count, 0)

    def test_running_child_timeout_keeps_only_precleanup_boundary_facts(self):
        # With no input, completion is set synchronously before the loop. A
        # small payload still depends on scheduling its writer before expiry;
        # its size alone cannot establish completion at the deadline.
        observed = self.supervise_timeout(input_text='')
        self.assertEqual(observed, {'available': True, **packet()})
        # These are deadline facts, despite the direct child being reaped afterwards.
        self.assertIs(observed['child_running'], True)
        self.assertIsNone(observed['returncode'])

    def test_real_blocked_input_writer_timeout_is_distinguished_and_reaped(self):
        observed = self.supervise_timeout(input_text=PRIVATE * 8192,
                                          max_input_bytes=len((PRIVATE * 8192).encode('utf-8')))
        self.assertEqual(observed, {'available': True, **packet(stdin=False)})

    def test_early_before_start_timeout_has_no_invented_child_or_stdio_boundary(self):
        supervisor = worker_processes.WorkerSupervisor(max_workers=1)
        calls = 0

        def clock():
            nonlocal calls
            calls += 1
            return 0.0 if calls == 1 else 2.0

        try:
            with patch.object(worker_processes.time, 'monotonic', side_effect=clock), \
                    patch.object(worker_processes.subprocess, 'Popen') as create:
                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                    supervisor.run(['synthetic-unused-command'], timeout_seconds=1)
                create.assert_not_called()
            self.assertIsNone(getattr(caught.exception, 'timeout_boundary', None))
            self.assert_unavailable(getattr(caught.exception, 'timeout_boundary', None))
        finally:
            self.assertTrue(supervisor.shutdown(timeout_seconds=2))

    def test_exited_owned_child_with_unfinished_stdout_is_distinct_from_running_child(self):
        entered, release = threading.Event(), threading.Event()

        class HeldPipe:
            def read(self, size):
                entered.set()
                if not release.wait(3):
                    raise OSError('The owned test pipe was not released within its finite deadline.')
                return b''

            def close(self):
                pass

        class ExitedProcess:
            stdin = None

            def __init__(self):
                self.stdout = HeldPipe()
                self.stderr = io.BytesIO()
                self.termination_calls = 0

            def poll(self):
                return 0

            def terminate(self):
                self.termination_calls += 1

            def kill(self):
                self.termination_calls += 1

        process = ExitedProcess()
        expired = False

        def clock():
            nonlocal expired
            if expired:
                # The next clock read is cleanup's existing deadline: only now
                # release the reader, after the timeout snapshot was captured.
                release.set()
                return 2.0
            if entered.is_set():
                expired = True
                return 2.0
            return 0.0

        supervisor = worker_processes.WorkerSupervisor(max_workers=1)
        try:
            with patch.object(worker_processes.time, 'monotonic', side_effect=clock), \
                    patch.object(worker_processes.subprocess, 'Popen', return_value=process):
                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                    supervisor.run(['synthetic-owned-fake'], timeout_seconds=1)
            raw = getattr(caught.exception, 'timeout_boundary', None)
            self.assertIs(type(raw), dict, 'The exited-child pipe stall lost its timeout boundary.')
            observed = self.sanitize(raw)
            self.assertIs(observed['available'], True)
            self.assertIs(observed['child_created'], True)
            self.assertIs(observed['child_running'], False)
            self.assertEqual(observed['returncode'], 0)
            self.assertIs(observed['stdin_done'], True)
            self.assertIs(observed['stdout_done'], False)
            self.assertIs(type(observed['stderr_done']), bool)
            self.assertEqual(supervisor.active_count, 0)
            self.assertEqual(process.termination_calls, 0)
        finally:
            release.set()
            self.assertTrue(supervisor.shutdown(timeout_seconds=2))


class TimeoutBoundaryFixtureTests(BoundaryAssertions, unittest.TestCase):
    def receipt(self, error, *, cleanup=True):
        choice = native_dialog_fixture.NativeFileChoice.__new__(native_dialog_fixture.NativeFileChoice)
        choice.request = {'private-input': PRIVATE}
        choice.mode = 'folder'
        choice.timeout = 15
        choice.result = None
        choice._token = worker_processes.CancellationToken()
        choice._cleanup_confirmed = None
        choice._cleanup_error_kind = None
        choice._supervisor = SimpleNamespace(run=Mock(side_effect=error), shutdown=Mock(return_value=cleanup))
        with patch.object(native_dialog_fixture, 'worker_command', return_value=[PRIVATE]):
            choice._run()
        self.assertIs(choice.result['selected'], False)
        self.assertEqual(choice.result['parent_phase'], 'supervise')
        self.assertEqual(choice.result['error_kind'], 'timeout')
        self.assertNotIn(PRIVATE, json.dumps(choice.result, allow_nan=False))
        self.assertIn('timeout_boundary', choice.result,
                      'The fixture must preserve the sanitized supervisor timeout boundary.')
        return choice.result

    def test_fixture_preserves_sanitized_timeout_and_acceptance_resanitizes_without_success(self):
        error = subprocess.TimeoutExpired([PRIVATE], 15, output=PRIVATE, stderr=PRIVATE)
        error.timeout_boundary = packet()
        expected = {'available': True, **packet()}
        receipt = self.receipt(error)
        self.assertEqual(receipt['reason'], 'worker_timeout')
        self.assertIs(receipt['worker_exit_confirmed'], True)
        self.assertIsNone(receipt['worker_returncode'])
        self.assertEqual(receipt['timeout_boundary'], expected)
        observed = acceptance_batch_png.dialog_diagnostic(receipt)
        self.assertIs(observed['selected'], False)
        self.assertIn('timeout_boundary', observed)
        self.assertEqual(observed['timeout_boundary'], expected)
        error.timeout_boundary['stdin_done'] = False
        receipt['timeout_boundary']['child_running'] = False
        self.assertEqual(observed['timeout_boundary'], expected)
        self.assertNotIn(PRIVATE, json.dumps(observed, allow_nan=False))

    def test_timeout_metadata_property_failure_cannot_replace_timeout_or_cleanup(self):
        reads = []

        class PropertyTimeout(subprocess.TimeoutExpired):
            @property
            def timeout_boundary(self):
                reads.append(True)
                raise ValueError(PRIVATE)

        receipt = self.receipt(PropertyTimeout([PRIVATE], 15))
        self.assertEqual(reads, [], 'Timeout metadata must not execute an exception subclass property.')
        self.assertEqual(receipt['reason'], 'worker_timeout')
        self.assertIs(receipt['worker_exit_confirmed'], True)
        self.assertEqual(receipt['timeout_boundary'], unavailable())
        observed = acceptance_batch_png.dialog_diagnostic(receipt)
        self.assertEqual(observed.get('timeout_boundary'), unavailable())

    def test_valid_timeout_boundary_cannot_turn_unconfirmed_cleanup_into_success(self):
        error = subprocess.TimeoutExpired([PRIVATE], 15)
        error.timeout_boundary = packet()
        receipt = self.receipt(error, cleanup=False)
        self.assertEqual(receipt['reason'], 'worker_cleanup_unconfirmed')
        self.assertIs(receipt['worker_exit_confirmed'], False)
        self.assertEqual(receipt['timeout_boundary'], {'available': True, **packet()})
        observed = acceptance_batch_png.dialog_diagnostic(receipt)
        self.assertIs(observed['selected'], False)
        self.assertIs(observed['worker_exit_confirmed'], False)

    def test_private_or_authorizing_extra_timeout_metadata_is_unavailable(self):
        error = subprocess.TimeoutExpired([PRIVATE], 15)
        error.timeout_boundary = {**packet(), 'selected': True, 'nonce': PRIVATE}
        receipt = self.receipt(error)
        self.assertEqual(receipt['reason'], 'worker_timeout')
        self.assertEqual(receipt['timeout_boundary'], unavailable())
        observed = acceptance_batch_png.dialog_diagnostic(receipt)
        self.assertIs(observed['selected'], False)
        self.assertEqual(observed.get('timeout_boundary'), unavailable())


if __name__ == '__main__':
    unittest.main()
