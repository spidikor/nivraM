"""Parent I/O observations do not establish protocol or native success.

Controlled finite pipe fixtures isolate lifecycle boundaries. Real children use
the hosting interpreter and are owned/reaped by the existing direct supervisor.
No native dialog, model, personal profile, or descendant process is launched.
"""
import inspect
import io
import json
import subprocess
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nivram import native_dialog_fixture, worker_processes
from nivram.native_dialog_progress import ProgressCapture

try:
    from nivram.parent_io import ParentIoCapture, parent_io_diagnostic
except ModuleNotFoundError as error:
    if error.name != 'nivram.parent_io':
        raise
    ParentIoCapture = parent_io_diagnostic = None


MARKS = ('stdout_reader_entered', 'stderr_reader_entered', 'stdout_eof',
         'stderr_eof', 'stdin_writer_entered', 'stdin_write_completed',
         'stdin_close_completed')
FACTS = ('writer_expected', 'stdout_limit', 'stdout_retained_bytes', *MARKS)
FIELDS = {'available', 'status', *FACTS}
PRIVATE = 'PRIVATE_PARENT_IO_PATH_CAPTION_NONCE_INPUT_OUTPUT'
MAX_LIMIT = 16 * 1024 * 1024
WORKER_RUN_PARAMETERS = inspect.signature(worker_processes.WorkerSupervisor.run).parameters


def valid_packet(**changes):
    result = {'available': True, 'status': 'valid', 'writer_expected': False,
              'stdout_limit': 64, 'stdout_retained_bytes': 0,
              **dict.fromkeys(MARKS, False)}
    result.update(changes)
    return result


def unavailable_packet(status='unavailable'):
    return {'available': False, 'status': status, **dict.fromkeys(FACTS)}


class ParentIoAssertions:
    def capture(self):
        self.assertIsNotNone(ParentIoCapture, 'The optional parent I/O capture is missing.')
        return ParentIoCapture()

    def sanitize(self, value):
        self.assertTrue(callable(parent_io_diagnostic), 'The parent I/O diagnostic gate is missing.')
        result = parent_io_diagnostic(value)
        self.assertIs(type(result), dict)
        self.assertEqual(set(result), FIELDS)
        self.assertNotIn(PRIVATE, json.dumps(result, allow_nan=False))
        return result

    def assert_unavailable(self, value, status='unavailable'):
        self.assertEqual(self.sanitize(value), unavailable_packet(status))


class ParentIoSchemaTests(ParentIoAssertions, unittest.TestCase):
    def test_capture_is_unavailable_before_configuration(self):
        capture = self.capture()
        self.assert_unavailable(capture.snapshot())
        self.assert_unavailable(capture.freeze())

    def test_configured_snapshot_contains_only_bounded_independent_scalar_facts(self):
        capture = self.capture()
        capture.configure(writer_expected=True, stdout_limit=64)
        capture.mark('stdout_reader_entered')
        capture.mark('stdin_writer_entered')
        capture.retained(17)
        expected = valid_packet(writer_expected=True, stdout_reader_entered=True,
                                stdin_writer_entered=True, stdout_retained_bytes=17)
        first = capture.snapshot()
        self.assertEqual(self.sanitize(first), expected)
        first['stdout_retained_bytes'] = 64
        first['stdin_write_completed'] = True
        self.assertEqual(self.sanitize(capture.snapshot()), expected)

    def test_freeze_keeps_facts_despite_later_marks_bytes_and_caller_mutation(self):
        capture = self.capture()
        capture.configure(writer_expected=True, stdout_limit=64)
        capture.mark('stdout_reader_entered')
        capture.retained(7)
        frozen = capture.freeze()
        expected = valid_packet(writer_expected=True, stdout_reader_entered=True,
                                stdout_retained_bytes=7)
        self.assertEqual(self.sanitize(frozen), expected)
        for name in MARKS:
            capture.mark(name)
        capture.retained(64)
        frozen['stdout_retained_bytes'] = 64
        self.assertEqual(self.sanitize(capture.snapshot()), expected)
        self.assertEqual(self.sanitize(capture.freeze()), expected)

    def _while_lock_held(self, capture, operation):
        results = []
        thread = threading.Thread(target=lambda: results.append(operation()), daemon=True)
        capture._lock.acquire()
        try:
            thread.start()
            thread.join(0.5)
            self.assertFalse(thread.is_alive(), 'Observation waited for a held capture lock.')
            self.assertEqual(len(results), 1)
            return results[0]
        finally:
            capture._lock.release()
            thread.join(3)
            self.assertFalse(thread.is_alive(), 'The finite contention fixture did not exit.')

    def test_snapshot_contention_returns_busy_without_freezing_live_facts(self):
        capture = self.capture()
        capture.configure(writer_expected=False, stdout_limit=64)
        self.assert_unavailable(self._while_lock_held(capture, capture.snapshot), 'busy')
        capture.mark('stdout_reader_entered')
        self.assertEqual(self.sanitize(capture.snapshot()),
                         valid_packet(stdout_reader_entered=True))

    def test_freeze_contention_returns_busy_without_waiting_or_later_inventing_facts(self):
        capture = self.capture()
        capture.configure(writer_expected=False, stdout_limit=64)
        self.assert_unavailable(self._while_lock_held(capture, capture.freeze), 'busy')
        capture.mark('stdout_reader_entered')
        capture.retained(12)
        self.assert_unavailable(capture.snapshot(), 'busy')
        self.assert_unavailable(capture.freeze(), 'busy')

    def test_sanitizer_copies_valid_and_known_unavailable_packets(self):
        for raw in (valid_packet(), valid_packet(stdout_limit=MAX_LIMIT,
                        stdout_retained_bytes=MAX_LIMIT, stdout_reader_entered=True), unavailable_packet(),
                    unavailable_packet('busy')):
            with self.subTest(status=raw['status'], limit=raw['stdout_limit']):
                observed = self.sanitize(raw)
                expected = dict(raw)
                self.assertEqual(observed, expected)
                self.assertIsNot(observed, raw)
                raw['status'] = PRIVATE
                self.assertEqual(observed, expected)
                self.assertEqual(self.sanitize(observed), expected)

    def test_sanitizer_rejects_private_extra_missing_keys_and_non_packets(self):
        self.assert_unavailable({**valid_packet(), 'output': PRIVATE})
        self.assert_unavailable({**valid_packet(), 'selected': True})
        for name in FIELDS:
            raw = valid_packet()
            del raw[name]
            with self.subTest(missing=name):
                self.assert_unavailable(raw)
        for value in (None, [], PRIVATE, False, SimpleNamespace(**valid_packet())):
            with self.subTest(kind=type(value).__name__):
                self.assert_unavailable(value)

    def test_sanitizer_rejects_nonbuiltin_scalars_unbounded_counts_and_status_confusion(self):
        replacements = [('available', 1), ('available', False), ('status', PRIVATE),
                        ('status', None), ('writer_expected', 0), ('stdout_limit', True),
                        ('stdout_limit', 0), ('stdout_limit', MAX_LIMIT + 1),
                        ('stdout_limit', 64.0), ('stdout_retained_bytes', True),
                        ('stdout_retained_bytes', -1), ('stdout_retained_bytes', 65),
                        ('stdout_retained_bytes', float('nan')),
                        ('stdout_retained_bytes', float('inf'))]
        replacements.extend((name, 1) for name in MARKS)
        for name, value in replacements:
            with self.subTest(name=name, kind=type(value).__name__):
                self.assert_unavailable(valid_packet(**{name: value}))
        for status in ('busy', 'unavailable'):
            self.assert_unavailable({**unavailable_packet(status), 'stdout_retained_bytes': 0})
            self.assert_unavailable({**unavailable_packet(status), 'available': True})

    def test_sanitizer_never_executes_mapping_or_scalar_subclass_hooks(self):
        calls = []

        class HostileMapping(dict):
            def __iter__(self):
                calls.append('iteration')
                raise AssertionError(PRIVATE)

            def get(self, *args):
                calls.append('get')
                raise AssertionError(PRIVATE)

        class HostileInt(int):
            def __le__(self, other):
                calls.append('comparison')
                raise AssertionError(PRIVATE)

        class HostileString(str):
            def __eq__(self, other):
                calls.append('equality')
                raise AssertionError(PRIVATE)

            __hash__ = str.__hash__

        self.assert_unavailable(HostileMapping(valid_packet()))
        self.assert_unavailable(valid_packet(stdout_limit=HostileInt(64)))
        self.assert_unavailable(valid_packet(status=HostileString('valid')))
        self.assert_unavailable({**valid_packet(), HostileString('foreign'): PRIVATE})
        self.assertEqual(calls, [])

    def test_impossible_reader_and_writer_facts_reject_without_rejecting_close_after_broken_write(self):
        impossible = [valid_packet(stdout_retained_bytes=1),
                      valid_packet(stdout_eof=True), valid_packet(stderr_eof=True)]
        impossible.extend(valid_packet(**{name: True}) for name in
            ('stdin_writer_entered', 'stdin_write_completed', 'stdin_close_completed'))
        impossible.extend(valid_packet(writer_expected=True, **{name: True}) for name in
            ('stdin_write_completed', 'stdin_close_completed'))
        for raw in impossible:
            with self.subTest(raw=raw):
                self.assert_unavailable(raw)
        # An existing BrokenPipe path can close input without writing it all.
        raw = valid_packet(writer_expected=True, stdin_writer_entered=True,
                           stdin_close_completed=True)
        self.assertEqual(self.sanitize(raw), raw)
        raw = valid_packet(stdout_reader_entered=True, stdout_eof=True)
        self.assertEqual(self.sanitize(raw), raw)


class HeldReadPipe:
    """Return optional bytes, then hold EOF until this exact pipe is released."""
    def __init__(self, prefix=b'', *, late=b'', on_block=None):
        self.prefix = prefix
        self.late = late
        self.on_block = on_block
        self.blocked = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()
        self.late_delivered = False

    def read(self, size):
        if self.prefix:
            data, self.prefix = self.prefix, b''
            return data
        self.blocked.set()
        if self.on_block is not None:
            callback, self.on_block = self.on_block, None
            callback()
        if not self.release.wait(3):
            raise OSError('The finite owned test pipe was not released.')
        if self.late:
            data, self.late = self.late, b''
            self.late_delivered = True
            return data
        return b''

    def close(self):
        self.closed.set()


class HeldWritePipe:
    """Separate first short write, blocked remainder, and blocked close."""
    def __init__(self, stage, *, short_first=False):
        self.stage = stage
        self.short_first = short_first
        self.calls = 0
        self.accepted = 0
        self.blocked = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()

    def write(self, data):
        self.calls += 1
        if self.short_first and self.calls == 1:
            self.accepted += 2
            return 2
        if self.stage == 'write':
            self.blocked.set()
            if not self.release.wait(3):
                raise OSError('The finite owned test writer was not released.')
        self.accepted += len(data)
        return len(data)

    def close(self):
        if self.stage == 'close':
            self.blocked.set()
            if not self.release.wait(3):
                raise OSError('The finite owned test close was not released.')
        self.closed.set()


class OwnedFakeProcess:
    """Only this handle and its explicitly supplied pipes belong to the test."""
    def __init__(self, *, stdout=None, stderr=None, stdin=None, returncode=None):
        self.stdout = stdout if stdout is not None else io.BytesIO()
        self.stderr = stderr if stderr is not None else io.BytesIO()
        self.stdin = stdin
        self.returncode = returncode
        self.termination_calls = 0

    def release_pipes(self):
        for pipe in (self.stdout, self.stderr, self.stdin):
            release = getattr(pipe, 'release', None)
            if release is not None:
                release.set()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.termination_calls += 1
        self.returncode = 0
        self.release_pipes()

    kill = terminate


class ParentIoSupervisorTests(ParentIoAssertions, unittest.TestCase):
    def setUp(self):
        self.observations = self.capture()
        self.supervisor = worker_processes.WorkerSupervisor(max_workers=1)

    def tearDown(self):
        self.assertTrue(self.supervisor.shutdown(timeout_seconds=2))
        self.assertEqual(self.supervisor.active_count, 0)

    def _run(self, *arguments, **options):
        # On the preserved PRE integration source, exercise actual existing
        # lifecycle behavior before failing assertions on missing facts. The
        # new optional argument is always supplied when that API is present.
        if 'parent_io' not in WORKER_RUN_PARAMETERS:
            options.pop('parent_io', None)
        return self.supervisor.run(*arguments, **options)

    def _fake_timeout(self, process, ready, *, input_text='', progress=None):
        real_clock = time.monotonic
        fixture_deadline = real_clock() + 2
        expired = False

        def clock():
            nonlocal expired
            if expired:
                # The existing cleanup clock is read after both observations
                # have frozen. Release only this fixture's exact pipe objects.
                process.release_pipes()
                return 2.0
            if ready() or real_clock() >= fixture_deadline:
                expired = True
                return 2.0
            return 0.0

        try:
            with patch.object(worker_processes.subprocess, 'Popen', return_value=process), \
                    patch.object(worker_processes.time, 'monotonic', side_effect=clock):
                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                    self._run(['synthetic-owned-pipe-fixture'],
                        input_text=input_text, timeout_seconds=1, max_stdout_bytes=64,
                        parent_io=self.observations, progress=progress)
            self.assertTrue(ready(), 'The fixture never reached its intended boundary.')
            self.assertEqual(self.supervisor.active_count, 0)
            boundary = worker_processes.timeout_boundary_diagnostic(
                getattr(caught.exception, 'timeout_boundary', None))
            self.assertIs(boundary['available'], True)
            observed = self.sanitize(self.observations.snapshot())
            self.assertIs(observed['available'], True,
                          'Capture was unavailable while a stream operation was blocked.')
            return observed, boundary
        finally:
            process.release_pipes()

    def test_bytes_without_eof_are_distinct_from_complete_output(self):
        stdout = HeldReadPipe(b'prefix')
        process = OwnedFakeProcess(stdout=stdout)
        observed, boundary = self._fake_timeout(process, stdout.blocked.is_set)
        self.assertIs(observed['stdout_reader_entered'], True)
        self.assertEqual(observed['stdout_retained_bytes'], 6)
        self.assertIs(observed['stdout_eof'], False)
        self.assertIs(boundary['stdout_done'], False)
        self.assertIs(boundary['child_running'], True)
        self.assertIsNone(boundary['returncode'])
        self.assertIs(observed['writer_expected'], False)
        for name in ('stdin_writer_entered', 'stdin_write_completed', 'stdin_close_completed'):
            self.assertIs(observed[name], False)

    def test_eof_can_be_observed_while_the_child_is_alive(self):
        stdout, stderr = io.BytesIO(b'complete bytes'), io.BytesIO()
        process = OwnedFakeProcess(stdout=stdout, stderr=stderr)
        observed, boundary = self._fake_timeout(process, lambda: stdout.closed and stderr.closed)
        self.assertIs(observed['stdout_eof'], True)
        self.assertIs(observed['stderr_eof'], True)
        self.assertIs(boundary['stdout_done'], True)
        self.assertIs(boundary['stderr_done'], True)
        self.assertIs(boundary['child_running'], True)
        self.assertIsNone(boundary['returncode'])

    def test_exited_child_with_inherited_style_pipe_does_not_invent_eof(self):
        stdout = HeldReadPipe(b'prefix')
        process = OwnedFakeProcess(stdout=stdout, returncode=0)
        observed, boundary = self._fake_timeout(process, stdout.blocked.is_set)
        self.assertIs(boundary['child_running'], False)
        self.assertEqual(boundary['returncode'], 0)
        self.assertIs(boundary['stdout_done'], False)
        self.assertIs(observed['stdout_eof'], False)
        self.assertEqual(observed['stdout_retained_bytes'], 6)
        self.assertEqual(process.termination_calls, 0)

    def test_expected_writer_that_never_enters_is_not_reported_as_completed(self):
        process = OwnedFakeProcess(stdin=io.BytesIO())
        writer_scheduled = threading.Event()
        real_thread = threading.Thread

        class NotEnteredThread:
            ident = None

            def start(self):
                writer_scheduled.set()

            def is_alive(self):
                return False

        def thread_factory(*args, **kwargs):
            if kwargs.get('target').__name__ == 'write_input':
                return NotEnteredThread()
            return real_thread(*args, **kwargs)

        with patch.object(worker_processes.threading, 'Thread', side_effect=thread_factory):
            observed, boundary = self._fake_timeout(process, writer_scheduled.is_set,
                                                    input_text='request')
        self.assertIs(observed['writer_expected'], True)
        self.assertIs(boundary['stdin_done'], False)
        for name in ('stdin_writer_entered', 'stdin_write_completed', 'stdin_close_completed'):
            self.assertIs(observed[name], False)

    def test_partial_input_write_is_not_full_write_or_close_completion(self):
        stdin = HeldWritePipe('write', short_first=True)
        process = OwnedFakeProcess(stdin=stdin)
        observed, boundary = self._fake_timeout(process, stdin.blocked.is_set,
                                                input_text='request')
        self.assertGreaterEqual(stdin.calls, 2)
        self.assertIs(observed['stdin_writer_entered'], True)
        self.assertIs(observed['stdin_write_completed'], False)
        self.assertIs(observed['stdin_close_completed'], False)
        self.assertIs(boundary['stdin_done'], False)

    def test_completed_write_does_not_establish_completed_close(self):
        stdin = HeldWritePipe('close')
        process = OwnedFakeProcess(stdin=stdin)
        observed, boundary = self._fake_timeout(process, stdin.blocked.is_set,
                                                input_text='request')
        self.assertIs(observed['stdin_writer_entered'], True)
        self.assertIs(observed['stdin_write_completed'], True)
        self.assertIs(observed['stdin_close_completed'], False)
        self.assertIs(boundary['stdin_done'], False)

    def test_broken_input_pipe_preserves_existing_outcome_without_claiming_write_success(self):
        class BrokenInput:
            def write(self, data):
                raise BrokenPipeError(PRIVATE)

            def close(self):
                pass

        process = OwnedFakeProcess(stdin=BrokenInput(), returncode=0)
        with patch.object(worker_processes.subprocess, 'Popen', return_value=process):
            result = self._run(['synthetic-owned-broken-input'], input_text='request',
                timeout_seconds=1, parent_io=self.observations)
        self.assertEqual(result.returncode, 0)
        observed = self.sanitize(self.observations.snapshot())
        self.assertIs(observed['stdin_writer_entered'], True)
        self.assertIs(observed['stdin_write_completed'], False)
        self.assertIs(observed['stdin_close_completed'], True)

    def test_falsy_nonbyte_read_preserves_completion_without_inventing_eof(self):
        class NonEofRead:
            def read(self, size):
                return None

            def close(self):
                pass

        process = OwnedFakeProcess(stdout=NonEofRead(), returncode=0)
        with patch.object(worker_processes.subprocess, 'Popen', return_value=process):
            result = self._run(['synthetic-owned-falsy-read'], timeout_seconds=1,
                               parent_io=self.observations)
        # The preexisting falsy-read loop can finish; observation must not
        # relabel a malformed fake return as an actual binary EOF read.
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, '')
        observed = self.sanitize(self.observations.snapshot())
        self.assertIs(observed['stdout_reader_entered'], True)
        self.assertIs(observed['stdout_eof'], False)
        self.assertEqual(observed['stdout_retained_bytes'], 0)

    def test_read_error_finishes_reader_without_establishing_eof(self):
        class ErrorRead:
            def read(self, size):
                raise OSError(PRIVATE)

            def close(self):
                pass

        process = OwnedFakeProcess(stdout=ErrorRead())
        with patch.object(worker_processes.subprocess, 'Popen', return_value=process):
            with self.assertRaises(worker_processes.WorkerProcessError) as caught:
                self._run(['synthetic-owned-read-error'], timeout_seconds=1,
                                    parent_io=self.observations)
        observed = self.sanitize(self.observations.snapshot())
        self.assertIs(observed['stdout_reader_entered'], True)
        self.assertIs(observed['stdout_eof'], False)
        self.assertEqual(observed['stdout_retained_bytes'], 0)
        self.assertNotIn(PRIVATE, str(caught.exception))
        self.assertEqual(process.termination_calls, 1)

    def test_cancellation_freezes_io_before_owned_stop_releases_pipe(self):
        token = worker_processes.CancellationToken()
        stdout = HeldReadPipe(b'prefix', on_block=token.cancel)
        process = OwnedFakeProcess(stdout=stdout)
        with patch.object(worker_processes.subprocess, 'Popen', return_value=process):
            with self.assertRaises(worker_processes.WorkerCancelled):
                self._run(['synthetic-owned-cancel'], timeout_seconds=1,
                    cancellation_token=token, parent_io=self.observations)
        self.assertTrue(stdout.closed.is_set())
        observed = self.sanitize(self.observations.snapshot())
        self.assertEqual(observed['stdout_retained_bytes'], 6)
        self.assertIs(observed['stdout_eof'], False)
        self.assertEqual(process.termination_calls, 1)

    def test_overflow_keeps_retained_count_bounded_and_does_not_invent_eof(self):
        process = OwnedFakeProcess(stdout=io.BytesIO(b'x' * 65), returncode=0)
        with patch.object(worker_processes.subprocess, 'Popen', return_value=process):
            with self.assertRaises(worker_processes.WorkerOutputLimit):
                self._run(['synthetic-owned-overflow'], timeout_seconds=1,
                                    max_stdout_bytes=64, parent_io=self.observations)
        observed = self.sanitize(self.observations.snapshot())
        self.assertEqual(observed['stdout_retained_bytes'], 64)
        self.assertEqual(observed['stdout_limit'], 64)
        self.assertIs(observed['stdout_eof'], False)

    def test_malformed_utf8_is_rejected_even_after_eof_and_child_exit(self):
        process = OwnedFakeProcess(stdout=io.BytesIO(b'\xff'), returncode=0)
        with patch.object(worker_processes.subprocess, 'Popen', return_value=process):
            with self.assertRaises(UnicodeDecodeError):
                self._run(['synthetic-owned-malformed-output'], timeout_seconds=1,
                                    parent_io=self.observations)
        observed = self.sanitize(self.observations.snapshot())
        self.assertEqual(observed['stdout_retained_bytes'], 1)
        self.assertIs(observed['stdout_eof'], True)

    def test_bytes_eof_and_progress_arriving_during_cleanup_cannot_rewrite_frozen_facts(self):
        nonce = 'a' * 32
        progress = ProgressCapture(nonce)
        stdout = HeldReadPipe(b'prefix', late=PRIVATE.encode('ascii'))
        stderr = HeldReadPipe(f'NDP1|{nonce}|1|request_validated\n'.encode('ascii'),
                             late=f'NDP1|{nonce}|2|result_emit_enter\n'.encode('ascii'))
        process = OwnedFakeProcess(stdout=stdout, stderr=stderr)
        observed, boundary = self._fake_timeout(process,
            lambda: stdout.blocked.is_set() and stderr.blocked.is_set(), progress=progress)
        self.assertTrue(stdout.late_delivered)
        self.assertTrue(stderr.late_delivered)
        self.assertIs(observed['stdout_eof'], False)
        self.assertIs(observed['stderr_eof'], False)
        self.assertEqual(observed['stdout_retained_bytes'], 6)
        self.assertEqual(progress.snapshot()['last_event'], 'request_validated')
        self.assertEqual(progress.snapshot()['sequence'], 1)
        self.assertIs(boundary['child_running'], True)

    def _command(self, script):
        return [native_dialog_fixture._source_interpreter(), '-I', '-S', '-u', '-c', script]

    def test_real_owned_child_delivers_bytes_without_eof_before_timeout(self):
        script = 'import sys,time; sys.stdout.buffer.write(b"prefix"); sys.stdout.buffer.flush(); time.sleep(5)'
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            self._run(self._command(script), timeout_seconds=2,
                                parent_io=self.observations)
        observed = self.sanitize(self.observations.snapshot())
        boundary = worker_processes.timeout_boundary_diagnostic(caught.exception.timeout_boundary)
        self.assertEqual(observed['stdout_retained_bytes'], 6)
        self.assertIs(observed['stdout_eof'], False)
        self.assertIs(boundary['child_running'], True)
        self.assertIs(boundary['stdout_done'], False)
        self.assertEqual(self.supervisor.active_count, 0)

    def test_real_owned_child_echo_preserves_success_and_all_stream_boundaries(self):
        script = 'import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data); sys.stderr.buffer.write(b"ok")'
        result = self._run(self._command(script), input_text='synthetic request',
                                    timeout_seconds=5, parent_io=self.observations)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, 'synthetic request')
        self.assertEqual(result.stderr, 'ok')
        observed = self.sanitize(self.observations.snapshot())
        self.assertIs(observed['writer_expected'], True)
        self.assertEqual(observed['stdout_retained_bytes'], len(b'synthetic request'))
        for name in MARKS:
            self.assertIs(observed[name], True)
        self.assertEqual(self.supervisor.active_count, 0)


if __name__ == '__main__':
    unittest.main()
