"""Parent pipe packets are optional and cannot authorize a native selection.

These checks exercise real capture, fixture and observer code with finite owned
threads and controlled supervisors. They launch no native chooser or process
and establish no real Windows acceptance result.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from nivram import acceptance_batch_png as batch
from nivram import native_dialog_fixture as fixture
from nivram.parent_io import ParentIoCapture, parent_io_diagnostic


NONCE = '1' * 32
PRIVATE = r'C:\private-synthetic-only\caption-nonce-command'


def configure(capture, *, retained=2):
    ParentIoCapture.configure(capture, writer_expected=True, stdout_limit=128)
    ParentIoCapture.mark(capture, 'stdout_reader_entered')
    ParentIoCapture.mark(capture, 'stderr_reader_entered')
    ParentIoCapture.mark(capture, 'stdin_writer_entered')
    ParentIoCapture.retained(capture, retained)


def packet():
    capture = ParentIoCapture()
    configure(capture)
    return ParentIoCapture.snapshot(capture)


class FixtureParentIoTests(unittest.TestCase):
    def make_choice(self, *, parent_io=True, progress=False):
        with patch.object(fixture, 'build_request', return_value={'nonce': NONCE}):
            return fixture.NativeFileChoice(None, None, None, parent_io=parent_io, progress=progress)

    def run_choice(self, choice, run, shutdown):
        choice._supervisor = SimpleNamespace(run=Mock(side_effect=run),
                                             shutdown=Mock(side_effect=shutdown))
        with patch.object(fixture, 'worker_command', return_value=['controlled-worker']) as command, \
                patch.object(fixture, 'validate_result', return_value={'selected': True, 'reason': None}):
            choice._run()
        return command

    def test_default_fixture_passes_no_capture_and_omits_parent_packet(self):
        choice = self.make_choice(parent_io=False)
        kwargs_seen = []

        def run(*args, **kwargs):
            kwargs_seen.append(kwargs)
            return subprocess.CompletedProcess(args[0], 0, '{}', '')

        command = self.run_choice(choice, run, lambda **kwargs: True)
        self.assertIsNone(choice._parent_io)
        self.assertNotIn('parent_io', kwargs_seen[0])
        self.assertNotIn('parent_io', choice.result)
        self.assertEqual(choice.parent_io_snapshot(), parent_io_diagnostic(None))
        command.assert_called_once_with()

    def test_opt_in_requires_exact_boolean(self):
        for value in (1, 0, 'true', None, [], {}):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(ValueError, 'explicitly enabled or disabled'):
                    self.make_choice(parent_io=value)

    def test_capture_is_separate_from_child_progress_and_command_flags(self):
        for progress in (False, True):
            with self.subTest(progress=progress):
                choice = self.make_choice(progress=progress)

                def run(*args, **kwargs):
                    self.assertIs(kwargs['parent_io'], choice._parent_io)
                    self.assertEqual(kwargs['max_stdout_bytes'], fixture.MAX_OUTPUT)
                    self.assertEqual(kwargs['timeout_seconds'], choice.timeout)
                    if progress:
                        self.assertIs(kwargs['progress'], choice._progress)
                    else:
                        self.assertNotIn('progress', kwargs)
                    configure(kwargs['parent_io'])
                    return subprocess.CompletedProcess(args[0], 0, '{}', '')

                command = self.run_choice(choice, run, lambda **kwargs: True)
                command.assert_called_once_with(**({'progress': True} if progress else {}))
                self.assertTrue(choice.result['selected'])
                self.assertEqual(choice.result['parent_io']['stdout_retained_bytes'], 2)
                self.assertFalse(choice.result['parent_io']['stdout_eof'])

    def test_finally_freezes_before_shutdown_tail_and_returns_fresh_copies(self):
        choice = self.make_choice()

        def run(*args, **kwargs):
            configure(choice._parent_io)
            ParentIoCapture.mark(choice._parent_io, 'stdin_write_completed')
            return subprocess.CompletedProcess(args[0], 0, '{}', '')

        def shutdown(**kwargs):
            ParentIoCapture.mark(choice._parent_io, 'stdout_eof')
            ParentIoCapture.mark(choice._parent_io, 'stdin_close_completed')
            ParentIoCapture.retained(choice._parent_io, 3)
            return True

        self.run_choice(choice, run, shutdown)
        expected = choice.parent_io_snapshot()
        self.assertTrue(expected['stdin_write_completed'])
        self.assertFalse(expected['stdin_close_completed'])
        self.assertFalse(expected['stdout_eof'])
        self.assertEqual(expected['stdout_retained_bytes'], 2)
        choice.result['parent_io']['stdout_retained_bytes'] = 100
        expected['stdout_retained_bytes'] = 99
        self.assertEqual(choice.parent_io_snapshot()['stdout_retained_bytes'], 2)

    def test_nonzero_exit_observations_cannot_promote_failure_or_cleanup(self):
        for confirmed in (False, True):
            with self.subTest(cleanup_confirmed=confirmed):
                choice = self.make_choice()

                def run(*args, **kwargs):
                    configure(choice._parent_io)
                    ParentIoCapture.mark(choice._parent_io, 'stdout_eof')
                    ParentIoCapture.mark(choice._parent_io, 'stderr_eof')
                    return subprocess.CompletedProcess(args[0], 7, '{}', PRIVATE)

                self.run_choice(choice, run, lambda **kwargs: confirmed)
                observed = batch.dialog_diagnostic(choice.result)
                self.assertFalse(observed['selected'])
                self.assertEqual(observed['error_kind'], 'nonzero_exit')
                self.assertEqual(observed['worker_returncode'], 7)
                self.assertEqual(observed['worker_exit_confirmed'], confirmed)
                self.assertTrue(observed['parent_io']['stdout_eof'])
                self.assertNotIn(PRIVATE, json.dumps(observed))

    def test_timeout_retains_bytes_without_eof_and_no_result_authority(self):
        choice = self.make_choice()

        def run(*args, **kwargs):
            configure(choice._parent_io, retained=17)
            raise subprocess.TimeoutExpired(args[0], choice.timeout, output=PRIVATE, stderr=PRIVATE)

        self.run_choice(choice, run, lambda **kwargs: True)
        observed = batch.dialog_diagnostic(choice.result)
        self.assertFalse(observed['selected'])
        self.assertEqual(observed['reason'], 'worker_timeout')
        self.assertIsNone(observed['worker_returncode'])
        self.assertEqual(observed['parent_io']['stdout_retained_bytes'], 17)
        self.assertFalse(observed['parent_io']['stdout_eof'])
        self.assertNotIn(PRIVATE, json.dumps(observed))

    def test_cancel_and_output_limit_packets_preserve_failure_reason(self):
        for error_type, reason in ((fixture.WorkerCancelled, 'worker_cancelled'),
                                   (fixture.WorkerOutputLimit, 'worker_output_limit')):
            with self.subTest(reason=reason):
                choice = self.make_choice()

                def run(*args, **kwargs):
                    configure(choice._parent_io, retained=128)
                    raise error_type(PRIVATE)

                self.run_choice(choice, run, lambda **kwargs: True)
                observed = batch.dialog_diagnostic(choice.result)
                self.assertFalse(observed['selected'])
                self.assertEqual(observed['reason'], reason)
                self.assertIsNone(observed['worker_returncode'])
                self.assertEqual(observed['parent_io']['stdout_retained_bytes'], 128)
                self.assertFalse(observed['parent_io']['stdout_eof'])
                self.assertNotIn(PRIVATE, json.dumps(observed))

    def test_malformed_output_does_not_become_success_with_eof_observations(self):
        choice = self.make_choice()

        def run(*args, **kwargs):
            configure(choice._parent_io)
            ParentIoCapture.mark(choice._parent_io, 'stdout_eof')
            ParentIoCapture.mark(choice._parent_io, 'stderr_eof')
            return subprocess.CompletedProcess(args[0], 0, PRIVATE, '')

        self.run_choice(choice, run, lambda **kwargs: True)
        observed = batch.dialog_diagnostic(choice.result)
        self.assertFalse(observed['selected'])
        self.assertEqual(observed['parent_phase'], 'result_decode')
        self.assertEqual(observed['error_kind'], 'json_decode_error')
        self.assertEqual(observed['worker_returncode'], 0)
        self.assertTrue(observed['parent_io']['stdout_eof'])
        self.assertNotIn(PRIVATE, json.dumps(observed))

    def test_close_freezes_before_cancellation_and_cleanup_marks(self):
        choice = self.make_choice()
        configure(choice._parent_io)
        expected = choice.parent_io_snapshot()
        cancel = choice._token.cancel

        def cancelling():
            cancel()
            ParentIoCapture.mark(choice._parent_io, 'stdin_write_completed')

        def shutdown(**kwargs):
            ParentIoCapture.mark(choice._parent_io, 'stdin_close_completed')
            ParentIoCapture.mark(choice._parent_io, 'stdout_eof')
            return True

        choice._supervisor = SimpleNamespace(shutdown=Mock(side_effect=shutdown))
        with patch.object(choice._token, 'cancel', side_effect=cancelling), \
                patch.object(fixture, 'cancel_owned_dialog', return_value=False):
            cleanup = choice.close()
        self.assertTrue(cleanup['shutdownConfirmed'])
        self.assertTrue(cleanup['cancel_requested'])
        self.assertEqual(choice.parent_io_snapshot(), expected)

    def test_snapshot_and_contended_close_do_not_wait_for_capture_lock(self):
        choice = self.make_choice()
        configure(choice._parent_io)
        choice._supervisor = SimpleNamespace(shutdown=Mock(return_value=True))
        done = threading.Event()
        observed = []

        def close_and_observe():
            observed.append(choice.parent_io_snapshot())
            observed.append(choice.close())
            done.set()

        with patch.object(fixture, 'cancel_owned_dialog', return_value=False):
            with choice._parent_io._lock:
                thread = threading.Thread(target=close_and_observe, name='owned-parent-snapshot-test', daemon=True)
                thread.start()
                completed = done.wait(1)
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(completed, 'Parent observation blocked on the capture lock.')
        self.assertEqual(observed[0], parent_io_diagnostic({'available': False, 'status': 'busy',
            **{key: None for key in packet() if key not in ('available', 'status')}}))
        frozen = choice.parent_io_snapshot()
        self.assertEqual(frozen['status'], 'busy')
        self.assertFalse(frozen['available'])
        ParentIoCapture.mark(choice._parent_io, 'stdout_eof')
        self.assertEqual(choice.parent_io_snapshot(), frozen)

    def test_finish_without_final_result_keeps_frozen_parent_packet(self):
        choice = self.make_choice()
        configure(choice._parent_io, retained=17)
        choice._started = True
        choice._deadline = 0
        choice.thread = SimpleNamespace(join=Mock(), is_alive=Mock(return_value=True))
        choice._supervisor = SimpleNamespace(shutdown=Mock(return_value=True))
        with patch.object(fixture, 'cancel_owned_dialog', return_value=False):
            with self.assertRaises(RuntimeError) as caught:
                choice.finish()
        observed = batch.dialog_diagnostic(caught.exception.dialog_result)
        self.assertFalse(observed['selected'])
        self.assertEqual(observed['reason'], 'supervisor_incomplete')
        self.assertFalse(observed['cleanup']['shutdownConfirmed'])
        self.assertEqual(observed['parent_io']['stdout_retained_bytes'], 17)
        self.assertFalse(observed['parent_io']['stdout_eof'])


class ObserverParentIoTests(unittest.TestCase):
    def make_observer(self, choice, *, returned=False):
        window = SimpleNamespace(create_file_dialog=lambda *args, **kwargs: None)
        observer = batch.NativeDialogObserver(window, Mock(), root=Path.cwd(), inputs=(),
            folder=Path.cwd(), open_kind=1, folder_kind=3, timeout=lambda: 15)
        observer.records = [{'mode': 'folder', 'originalCalled': True, 'originalReturned': returned}]
        observer._fixtures = [choice]
        observer.count = 1
        return observer

    def exact_choice(self):
        choice = fixture.NativeFileChoice.__new__(fixture.NativeFileChoice)
        choice._parent_io = ParentIoCapture()
        configure(choice._parent_io, retained=9)
        choice.result = None
        return choice

    def test_pending_original_exports_independent_packet_without_return_authority(self):
        choice = self.exact_choice()
        observer = self.make_observer(choice)
        record = observer.snapshot()[0]
        self.assertFalse(record['originalReturned'])
        self.assertNotIn('actualReturnVerified', record)
        self.assertEqual(record['parentIoAtSnapshot']['stdout_retained_bytes'], 9)
        self.assertFalse(record['parentIoAtSnapshot']['stdout_eof'])
        self.assertFalse(record['childProgressAtSnapshot']['available'])
        record['parentIoAtSnapshot']['stdout_retained_bytes'] = 100
        self.assertEqual(observer.snapshot()[0]['parentIoAtSnapshot']['stdout_retained_bytes'], 9)
        with self.assertRaisesRegex(AssertionError, 'batch_native_dialog_returns_incomplete'):
            observer.assert_complete()

    def test_returned_original_still_exports_packet_while_fixture_finish_is_pending(self):
        observer = self.make_observer(self.exact_choice(), returned=True)
        record = observer.snapshot()[0]
        self.assertTrue(record['originalReturned'])
        self.assertNotIn('fixture', record)
        self.assertNotIn('fixtureFailure', record)
        self.assertNotIn('actualReturnVerified', record)
        self.assertTrue(record['parentIoAtSnapshot']['available'])
        with self.assertRaisesRegex(AssertionError, 'batch_native_dialog_returns_incomplete'):
            observer.assert_complete()

    def test_unknown_fixture_snapshot_hooks_are_not_called(self):
        hooks = []

        class UnknownChoice:
            result = None

            def parent_io_snapshot(self):
                hooks.append('snapshot')
                raise AssertionError(PRIVATE)

        record = self.make_observer(UnknownChoice()).snapshot()[0]
        self.assertEqual(record['parentIoAtSnapshot'], parent_io_diagnostic(None))
        self.assertEqual(hooks, [])
        self.assertNotIn(PRIVATE, json.dumps(record))

    def test_final_receipt_sanitizes_parent_packet_without_private_hooks(self):
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

        valid = packet()
        bad_key = {**{key: value for key, value in valid.items() if key != 'available'}, HostileKey(): True}
        for value in (HostileDict(valid), bad_key, {**valid, 'path': PRIVATE},
                      {**valid, 'status': PRIVATE}, {**valid, 'stdout_retained_bytes': 129},
                      {**valid, 'stdout_limit': True}):
            result = batch.dialog_diagnostic({'selected': False, 'reason': 'worker_failure', 'parent_io': value})
            self.assertFalse(result['parent_io']['available'])
            self.assertNotIn(PRIVATE, json.dumps(result))
        observed = batch.dialog_diagnostic({'parent_io': valid})
        self.assertEqual(observed['parent_io'], valid)
        observed['parent_io']['stdout_retained_bytes'] = 100
        self.assertEqual(valid['stdout_retained_bytes'], 2)
        self.assertEqual(hooks, [])

    def test_batch_opt_in_keeps_original_returns_and_unknown_final_packet_fixed(self):
        with tempfile.TemporaryDirectory(prefix='nv-parent-observer-') as temporary:
            root = Path(temporary)
            inputs = tuple(root / name for name in ('first.nivram', 'second.mol'))
            for path in inputs:
                path.write_bytes(b'owned synthetic input')
            folder = root / 'outputs'
            folder.mkdir()
            actual_open = tuple(str(path) for path in reversed(inputs))
            actual_folder = (str(folder),)
            requested = []

            class Window:
                def create_file_dialog(self, kind, **kwargs):
                    return actual_open if kind == 1 else actual_folder

            class Choice:
                def finish(self):
                    return {'selected': True, 'worker_exit_confirmed': True}

            def choose(target, *, mode, timeout, parent_io):
                requested.append((mode, timeout, parent_io))
                return Choice()

            window = Window()
            observer = batch.NativeDialogObserver(window, choose, root=root, inputs=inputs,
                folder=folder, open_kind=1, folder_kind=3, timeout=lambda: 15)
            observer.attach()
            try:
                self.assertIs(window.create_file_dialog(1, allow_multiple=True, file_types=batch.INPUT_FILTER), actual_open)
                self.assertIs(window.create_file_dialog(3), actual_folder)
                observer.assert_complete()
                self.assertEqual(requested, [('open-multiple', 15, True), ('folder', 15, True)])
                for record in observer.snapshot():
                    self.assertEqual(record['fixture']['parent_io'], parent_io_diagnostic(None))
                    self.assertNotIn('parentIoAtSnapshot', record)
            finally:
                observer.close()


if __name__ == '__main__':
    unittest.main()
