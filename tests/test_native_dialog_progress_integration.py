"""Progress integration uses controlled pipes/backends, never native dialogs."""

import io
import json
import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from nivram import acceptance_batch_png, native_dialog_fixture as fixture
from nivram import native_dialog_worker as worker, worker_processes as workers
from nivram.native_dialog_progress import ProgressCapture, ProgressEmitter
from test_native_batch_dialog_fixture import Clock, FakeWindows


NONCE = '0123456789abcdef' * 2
PRIVATE = 'PRIVATE_COMMAND_PATH_PAYLOAD_CAPTION'


def record(sequence, event='backend_ready'):
    return f'NDP1|{NONCE}|{sequence}|{event}\n'.encode('ascii')


class ChunkPipe:
    def __init__(self, chunks, on_eof=None):
        self.chunks = list(chunks)
        self.on_eof = on_eof
        self.read_count = 0
        self.closed = False

    def read(self, size):
        self.read_count += 1
        if self.chunks:
            return self.chunks.pop(0)
        if self.on_eof is not None:
            self.on_eof()
        return b''

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, stderr, *, returncode=0, stdout=b'unchanged terminal stdout'):
        self.stdin = None
        self.stdout = io.BytesIO(stdout)
        self.stderr = stderr
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 1

    kill = terminate


class InlineThread:
    """Finite pipes run synchronously in tests that do not exercise concurrency."""
    def __init__(self, *, target, args=(), **kwargs):
        self.target = target
        self.args = args
        self.ident = None

    def start(self):
        self.ident = 1
        self.target(*self.args)

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False


class SupervisorProgressIntegrationTests(unittest.TestCase):
    def make_supervisor(self):
        supervisor = workers.WorkerSupervisor(max_workers=1)
        self.addCleanup(supervisor.shutdown)
        return supervisor

    def test_timeout_snapshot_precedes_late_stderr_and_cleanup_error(self):
        for cleanup_confirmed in (True, False):
            with self.subTest(cleanup_confirmed=cleanup_confirmed):
                awaiting_cleanup = threading.Event()
                release_late = threading.Event()
                late_read = threading.Event()

                class LatePipe:
                    reads = 0

                    def read(self, size):
                        self.reads += 1
                        if self.reads == 1:
                            return record(1, 'invoke_final_checks')
                        if self.reads == 2:
                            # Reaching this read proves record 1 was fed.
                            awaiting_cleanup.set()
                            if not release_late.wait(3):
                                raise OSError('Controlled cleanup release did not arrive.')
                            late_read.set()
                            return record(2, 'cancel_returned')
                        return b''

                    def close(self):
                        pass

                capture = ProgressCapture(NONCE)
                process = FakeProcess(LatePipe(), returncode=None)
                supervisor = self.make_supervisor()
                ticks = 0

                def clock():
                    nonlocal ticks
                    ticks += 1
                    if ticks <= 2:
                        return 0.0
                    if ticks == 3:
                        self.assertTrue(awaiting_cleanup.wait(2))
                    return 2.0

                def cleanup(*args):
                    self.assertEqual(capture.snapshot()['last_event'], 'invoke_final_checks')
                    release_late.set()
                    process.returncode = 1
                    return cleanup_confirmed

                expected_error = subprocess.TimeoutExpired if cleanup_confirmed else workers.WorkerProcessError
                try:
                    with patch.object(workers.subprocess, 'Popen', return_value=process), \
                            patch.object(workers.time, 'monotonic', side_effect=clock), \
                            patch.object(workers, '_stop_owned', side_effect=cleanup):
                        with self.assertRaises(expected_error) as caught:
                            supervisor.run([PRIVATE], timeout_seconds=1, progress=capture)
                finally:
                    release_late.set()
                self.assertTrue(late_read.is_set())
                self.assertEqual(capture.snapshot(), {
                    'available': True, 'status': 'valid', 'last_event': 'invoke_final_checks',
                    'sequence': 1, 'partial_record': False,
                })
                if not cleanup_confirmed:
                    self.assertEqual(caught.exception.worker_failure['primary_kind'], 'timeout')
                    self.assertEqual(caught.exception.worker_failure['category'], 'cleanup_incomplete')
                self.assertEqual(supervisor.active_count, 0)

    def test_invalid_progress_keeps_draining_and_preserves_terminal_result(self):
        pipe = ChunkPipe([b'private invalid protocol\n', record(1), b'last raw stderr'])
        process = FakeProcess(pipe)
        capture = ProgressCapture(NONCE)
        supervisor = self.make_supervisor()
        with patch.object(workers.subprocess, 'Popen', return_value=process), \
                patch.object(workers.threading, 'Thread', InlineThread):
            result = supervisor.run([PRIVATE], timeout_seconds=1, progress=capture)
        self.assertEqual(pipe.read_count, 4)
        self.assertTrue(pipe.closed)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, 'unchanged terminal stdout')
        self.assertEqual(capture.snapshot()['status'], 'invalid')
        self.assertNotIn('private', json.dumps(capture.snapshot()))

    def test_disabled_default_keeps_ordinary_stderr_behavior(self):
        supervisor = self.make_supervisor()
        process = FakeProcess(ChunkPipe([PRIVATE.encode()]))
        with patch.object(workers.subprocess, 'Popen', return_value=process), \
                patch.object(workers.threading, 'Thread', InlineThread):
            result = supervisor.run(['opaque'], timeout_seconds=1)
        self.assertEqual(result.stderr, PRIVATE)
        self.assertEqual(result.stdout, 'unchanged terminal stdout')

    def test_cancellation_after_checkpoint_stays_cancellation_and_freezes(self):
        token = workers.CancellationToken()
        pipe = ChunkPipe([record(1)], on_eof=token.cancel)
        process = FakeProcess(pipe, returncode=None)
        supervisor = self.make_supervisor()
        capture = ProgressCapture(NONCE)

        def cleanup(*args):
            capture.feed(record(2, 'cancel_returned'))
            process.returncode = 1
            return True

        with patch.object(workers.subprocess, 'Popen', return_value=process), \
                patch.object(workers.threading, 'Thread', InlineThread), \
                patch.object(workers, '_stop_owned', side_effect=cleanup):
            with self.assertRaises(workers.WorkerCancelled):
                supervisor.run(['opaque'], timeout_seconds=1, cancellation_token=token,
                               progress=capture)
        self.assertEqual(capture.snapshot()['sequence'], 1)
        self.assertEqual(capture.snapshot()['last_event'], 'backend_ready')

    def test_stderr_overflow_remains_failure_even_after_valid_progress(self):
        supervisor = self.make_supervisor()
        capture = ProgressCapture(NONCE)
        process = FakeProcess(ChunkPipe([record(1), b'x' * 4097]))
        with patch.object(workers.subprocess, 'Popen', return_value=process), \
                patch.object(workers.threading, 'Thread', InlineThread):
            with self.assertRaises(workers.WorkerOutputLimit):
                supervisor.run(['opaque'], timeout_seconds=1, max_stderr_bytes=4096,
                               progress=capture)
        before = capture.snapshot()
        capture.feed(record(2))
        self.assertEqual(capture.snapshot(), before)

    def test_nonzero_exit_cannot_be_overridden_by_valid_progress(self):
        supervisor = self.make_supervisor()
        capture = ProgressCapture(NONCE)
        process = FakeProcess(ChunkPipe([record(1)]), returncode=7)
        with patch.object(workers.subprocess, 'Popen', return_value=process), \
                patch.object(workers.threading, 'Thread', InlineThread):
            result = supervisor.run(['opaque'], timeout_seconds=1, progress=capture)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(capture.snapshot()['sequence'], 1)
        capture.feed(record(2))
        self.assertEqual(capture.snapshot()['sequence'], 1)

    def test_progress_feed_and_freeze_do_not_hold_supervisor_lock(self):
        supervisor = self.make_supervisor()
        capture = ProgressCapture(NONCE)
        process = FakeProcess(ChunkPipe([record(1)]))
        real_feed, real_freeze = ProgressCapture.feed, ProgressCapture.freeze
        calls = []

        def observed(method, name, instance, *args):
            acquired = supervisor._lock.acquire(blocking=False)
            self.assertTrue(acquired, f'Supervisor lock held during {name}.')
            if acquired:
                supervisor._lock.release()
            calls.append(name)
            return method(instance, *args)

        with patch.object(workers.subprocess, 'Popen', return_value=process), \
                patch.object(workers.threading, 'Thread', InlineThread), \
                patch.object(ProgressCapture, 'feed', lambda self, data: observed(real_feed, 'feed', self, data)), \
                patch.object(ProgressCapture, 'freeze', lambda self: observed(real_freeze, 'freeze', self)):
            supervisor.run(['opaque'], timeout_seconds=1, progress=capture)
        self.assertIn('feed', calls)
        self.assertIn('freeze', calls)

    def test_nonexact_capture_is_rejected_before_spawn_without_hooks(self):
        calls = []

        class HostileCapture(ProgressCapture):
            def feed(self, data):
                calls.append('feed')

            def freeze(self):
                calls.append('freeze')

        supervisor = self.make_supervisor()
        with patch.object(workers.subprocess, 'Popen') as spawn:
            with self.assertRaises((TypeError, ValueError)):
                supervisor.run(['opaque'], timeout_seconds=1, progress=HostileCapture(NONCE))
        spawn.assert_not_called()
        self.assertEqual(calls, [])


class FixtureProgressIntegrationTests(unittest.TestCase):
    def make_choice(self, *, progress=True):
        with patch.object(fixture, 'build_request', return_value={'nonce': NONCE}):
            return fixture.NativeFileChoice(None, None, None, progress=progress)

    def test_snapshot_is_independent_of_supervisor_lock_and_nonblocking_when_busy(self):
        choice = self.make_choice()
        choice._progress.feed(record(1))

        def take_snapshot_with_lock_held(lock):
            done = threading.Event()
            packets = []

            def observe():
                packets.append(choice.progress_snapshot())
                done.set()

            with lock:
                thread = threading.Thread(target=observe, daemon=True)
                thread.start()
                returned_without_lock = done.wait(1)
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertTrue(returned_without_lock, 'Snapshot blocked on an unrelated/held lock.')
            return packets[0]

        self.assertEqual(take_snapshot_with_lock_held(choice._supervisor._lock)['last_event'], 'backend_ready')
        packet = take_snapshot_with_lock_held(choice._progress._lock)
        self.assertFalse(packet['available'])
        self.assertEqual(packet['status'], 'busy')
        self.assertEqual(choice.progress_snapshot()['sequence'], 1)

    def test_disabled_fixture_defaults_to_no_progress(self):
        with patch.object(fixture, 'build_request', return_value={'nonce': NONCE}):
            choice = fixture.NativeFileChoice(None, None, None)
        self.assertIsNone(choice._progress)
        self.assertFalse(choice.progress_snapshot()['available'])

    def test_close_freezes_progress_before_cancel_and_shutdown_late_records(self):
        choice = self.make_choice()
        choice._progress.feed(record(1, 'invoke_final_checks'))
        expected = choice.progress_snapshot()
        original_cancel = choice._token.cancel

        def cancel():
            original_cancel()
            choice._progress.feed(record(2, 'cancel_enter'))

        def shutdown(**kwargs):
            choice._progress.feed(record(3, 'cancel_returned'))
            return True

        choice._supervisor = SimpleNamespace(shutdown=Mock(side_effect=shutdown))
        with patch.object(choice._token, 'cancel', side_effect=cancel), \
                patch.object(fixture, 'cancel_owned_dialog', return_value=False):
            cleanup = choice.close()
        self.assertTrue(cleanup['shutdownConfirmed'])
        self.assertTrue(cleanup['cancel_requested'])
        self.assertEqual(choice.progress_snapshot(), expected)
        choice._progress.feed(record(4, 'backend_closed'))
        self.assertEqual(choice.progress_snapshot(), expected)

    def test_fixture_and_acceptance_keep_progress_observational_on_failed_exit(self):
        for confirmed in (True, False):
            with self.subTest(cleanup_confirmed=confirmed):
                choice = self.make_choice()

                def run(*args, **kwargs):
                    self.assertIs(kwargs['progress'], choice._progress)
                    choice._progress.feed(record(1, 'invoke_final_checks'))
                    choice._progress.freeze()
                    return subprocess.CompletedProcess(['opaque'], 7, '', PRIVATE)

                def shutdown(**kwargs):
                    choice._progress.feed(record(2, 'cancel_returned'))
                    return confirmed

                choice._supervisor = SimpleNamespace(run=Mock(side_effect=run),
                                                     shutdown=Mock(side_effect=shutdown))
                with patch.object(fixture, 'worker_command', return_value=['opaque']):
                    choice._run()
                self.assertFalse(choice.result['selected'])
                self.assertEqual(choice.result['error_kind'], 'nonzero_exit')
                self.assertEqual(choice.result['worker_exit_confirmed'], confirmed)
                observed = acceptance_batch_png.dialog_diagnostic(choice.result)
                self.assertEqual(observed['child_progress']['last_event'], 'invoke_final_checks')
                self.assertEqual(observed['child_progress']['sequence'], 1)
                self.assertNotIn(PRIVATE, json.dumps(observed))
                self.assertNotIn(NONCE, json.dumps(observed))

    def test_worker_command_progress_flags_are_explicit_for_source_and_frozen(self):
        with patch.object(fixture.sys, 'frozen', True, create=True):
            self.assertEqual(fixture.worker_command()[-1], '--native-dialog-fixture')
            self.assertEqual(fixture.worker_command(progress=True)[-1], '--native-dialog-fixture-progress')
        with patch.object(fixture.sys, 'frozen', False, create=True), \
                patch.object(fixture, '_source_interpreter', return_value='controlled-interpreter'), \
                patch.object(fixture, '_source_packages', return_value='controlled-packages'):
            normal = fixture.worker_command()
            progress = fixture.worker_command(progress=True)
        self.assertNotIn('--worker-progress', ' '.join(normal))
        self.assertIn('--worker-progress', ' '.join(progress))
        self.assertEqual(normal[0], progress[0])

    def test_acceptance_rejects_hostile_or_private_progress_without_hooks(self):
        hooks = []

        class HostileDict(dict):
            def get(self, *args):
                hooks.append('get')
                raise AssertionError(PRIVATE)

            def __iter__(self):
                hooks.append('iteration')
                raise AssertionError(PRIVATE)

        class HostileKey:
            def __hash__(self):
                return hash('available')

            def __eq__(self, other):
                hooks.append('equality')
                raise AssertionError(PRIVATE)

        valid = {'available': True, 'status': 'valid', 'last_event': 'backend_ready',
                 'sequence': 1, 'partial_record': False}
        bad_key = {HostileKey(): True, 'status': 'valid', 'last_event': 'backend_ready',
                   'sequence': 1, 'partial_record': False}
        for metadata in (HostileDict(valid), bad_key, {**valid, 'path': PRIVATE},
                         {**valid, 'last_event': PRIVATE}, {**valid, 'sequence': True}):
            receipt = {'selected': False, 'reason': 'worker_failure', 'child_progress': metadata}
            observed = acceptance_batch_png.dialog_diagnostic(receipt)
            self.assertFalse(observed['child_progress']['available'])
            self.assertNotIn(PRIVATE, json.dumps(observed))
        self.assertEqual(hooks, [])


class SelectionCheckpointOrderingTests(unittest.TestCase):
    def select(self, *, mode='folder', on_event=None, no_dialogs=False):
        trace = []
        clock = Clock()

        class Backend(FakeWindows):
            def set_text(self, *args):
                trace.append('set_text')
                return super().set_text(*args)

            def invoke_prepared(self, *args):
                trace.append('invoke')
                return super().invoke_prepared(*args)

            def submit(self, hwnd, command):
                if command == 1:
                    trace.append('submit')
                return super().submit(hwnd, command)

        backend = Backend(123)
        if no_dialogs:
            backend.dialogs = []
        if mode == 'folder':
            backend.add_folder_button()
            backend.labels[21] = worker._mode_labels('folder')[0]

        class Stream(io.BytesIO):
            def write(self, data):
                event = data.rstrip(b'\n').split(b'|')[-1].decode('ascii')
                trace.append('telemetry:' + event)
                if on_event is not None:
                    on_event(event, backend, clock)
                return super().write(data)

        stream = Stream()
        emitter = ProgressEmitter(NONCE, stream)
        request = {'schemaVersion': 1 if mode == 'file' else 2, 'parentPid': 123,
                   'parentCreated': 1, 'ownerHwnd': 10, 'nonce': NONCE,
                   'timeout': 1, 'target': PRIVATE, 'targets': [PRIVATE]}
        if mode != 'file':
            request['dialogMode'] = mode

        def guard(original):
            def checked(*args, **kwargs):
                result = original(*args, **kwargs)
                trace.append('guard')
                return result
            return checked

        with patch.object(worker, 'validate_request', side_effect=lambda value, **kwargs: value), \
                patch.object(worker, '_mode_field_valid', side_effect=guard(worker._mode_field_valid)), \
                patch.object(worker, '_owned_field', side_effect=guard(worker._owned_field)):
            result = worker.select_fixture(request, backend, clock=clock, sleep=clock.sleep,
                                           progress=emitter)
        return result, backend, trace, stream.getvalue()

    def test_no_telemetry_between_final_ownership_guard_and_mutation(self):
        for mode in ('file', 'folder'):
            with self.subTest(mode=mode):
                result, backend, trace, data = self.select(mode=mode)
                self.assertTrue(result['selected'])
                actions = ['set_text', 'invoke' if mode == 'folder' else 'submit']
                for action in actions:
                    action_index = trace.index(action)
                    guard_index = max(i for i in range(action_index) if trace[i] == 'guard')
                    self.assertFalse(any(item.startswith('telemetry:')
                                         for item in trace[guard_index + 1:action_index]))

    def test_owner_changed_during_progress_write_prevents_field_write(self):
        def change_owner(event, backend, clock):
            if event == 'field_set_enter':
                backend.pids[10] += 1

        result, backend, trace, data = self.select(on_event=change_owner)
        self.assertFalse(result['selected'])
        self.assertEqual(backend.writes, [])
        self.assertEqual(backend.invocations, [])

    def test_deadline_crossed_during_progress_write_prevents_invoke(self):
        def expire(event, backend, clock):
            if event == 'invoke_final_checks':
                clock.now = 2

        result, backend, trace, data = self.select(on_event=expire)
        self.assertFalse(result['selected'])
        self.assertTrue(backend.writes)
        self.assertEqual(backend.invocations, [])
        self.assertNotIn((20, 1), backend.commands)

    def test_polling_emits_each_checkpoint_once_without_exhausting_budget(self):
        result, backend, trace, data = self.select(no_dialogs=True)
        self.assertFalse(result['selected'])
        self.assertEqual(trace.count('telemetry:find_dialog_enter'), 1)
        self.assertNotIn(b'diagnostic_limit', data)
        capture = ProgressCapture(NONCE)
        capture.feed(data)
        self.assertEqual(capture.freeze()['status'], 'valid')


class WorkerMainProgressIntegrationTests(unittest.TestCase):
    def run_main(self, payload, *, progress):
        stdout, stderr = io.BytesIO(), io.BytesIO()
        backend = SimpleNamespace(close=Mock())
        result = {'schemaVersion': 1, 'nonce': NONCE, 'selected': False,
                  'reason': 'supported_folder_control_not_found'}

        def select(request, backend, **kwargs):
            emitter = kwargs.get('progress')
            if progress:
                self.assertIs(type(emitter), ProgressEmitter)
                emitter.emit('find_dialog_enter')
            else:
                self.assertIsNone(emitter)
            return result

        with patch.object(worker.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(payload))), \
                patch.object(worker.sys, 'stdout', SimpleNamespace(buffer=stdout)), \
                patch.object(worker.sys, 'stderr', SimpleNamespace(buffer=stderr)), \
                patch.object(worker, 'validate_request', side_effect=lambda value, **kwargs: value), \
                patch.object(worker, 'WindowsBackend', return_value=backend) as create, \
                patch.object(worker, 'select_fixture', side_effect=select):
            exit_code = worker.worker_main(progress=progress)
        return exit_code, stdout.getvalue(), stderr.getvalue(), result, backend, create

    def test_opt_in_progress_uses_stderr_and_terminal_stdout_is_identical(self):
        payload = json.dumps({'nonce': NONCE}).encode()
        normal = self.run_main(payload, progress=False)
        enabled = self.run_main(payload, progress=True)
        self.assertEqual(normal[0], 0)
        self.assertEqual(enabled[0], 0)
        self.assertEqual(normal[1], enabled[1])
        self.assertEqual(enabled[1], json.dumps(enabled[3], allow_nan=False).encode())
        self.assertEqual(normal[2], b'')
        capture = ProgressCapture(NONCE)
        capture.feed(enabled[2])
        packet = capture.freeze()
        self.assertEqual(packet['status'], 'valid')
        self.assertEqual(packet['last_event'], 'result_emit_enter')
        self.assertGreater(packet['sequence'], 1)
        enabled[4].close.assert_called_once()
        self.assertNotIn(PRIVATE, json.dumps(packet))

    def test_bad_input_has_no_progress_or_raw_error_disclosure(self):
        for payload in (PRIVATE.encode(), b'{}', json.dumps({'nonce': 'INVALID'}).encode()):
            with self.subTest(payload_length=len(payload)):
                observed = self.run_main(payload, progress=True)
                self.assertEqual(observed[0], 1)
                self.assertEqual(observed[1], b'')
                self.assertEqual(observed[2], b'')
                observed[5].assert_not_called()


if __name__ == '__main__':
    unittest.main()
