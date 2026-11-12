"""Managed cleanup contracts; actual SDK/process exit is a separate native gate."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from nivram.webview_cleanup import CleanupError, ManagedWebViewSession


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self, *args):
        for handler in tuple(self.handlers):
            handler(*args)


class Control:
    def __init__(self, path):
        environment = SimpleNamespace(UserDataFolder=str(path), BrowserProcessExited=Event())
        self.CoreWebView2 = SimpleNamespace(Environment=environment, BrowserProcessId=123)
        self.CoreWebView2InitializationCompleted = Event()
        self.InvokeRequired = True
        self.disposal_count = 0
        self.on_dispose = None

    def BeginInvoke(self, action):
        action()

    def Dispose(self):
        self.disposal_count += 1
        if self.on_dispose:
            self.on_dispose()


class ManagedSessionTests(unittest.TestCase):
    def finish_on_worker(self, session, timeout=.1):
        values, errors = [], []
        def run():
            try:
                values.append(session.finish_shutdown(timeout))
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=run)
        worker.start(); worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        return values[0]

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.profile = Path(self.temporary.name) / 'profile'
        self.action_patch = patch('nivram.webview_cleanup._ui_action', side_effect=lambda callback: callback)
        self.action_patch.start()

    def tearDown(self):
        self.action_patch.stop()
        self.temporary.cleanup()

    def fixture(self, **options):
        session = ManagedWebViewSession(self.profile, **options)
        control = Control(session.storage_path)
        browser = SimpleNamespace(webview=control, clear_user_data=lambda: None)
        window = SimpleNamespace(native=SimpleNamespace(webview=control, browser=browser),
                                 events=SimpleNamespace(before_show=Event()))
        session.attach(window)
        window.events.before_show.fire()
        return session, control, window

    def exit(self, control, pid=123, folder=None):
        environment = control.CoreWebView2.Environment
        sender = environment if folder is None else SimpleNamespace(UserDataFolder=str(folder))
        environment.BrowserProcessExited.fire(sender, SimpleNamespace(BrowserProcessId=pid, BrowserProcessExitKind='Normal'))

    def test_unique_session_paths_never_equal_profile_or_managed_root(self):
        first = ManagedWebViewSession(self.profile)
        second = ManagedWebViewSession(self.profile)
        self.assertNotEqual(first.storage_path, second.storage_path)
        self.assertEqual(first.storage_path.parent, self.profile / 'webview-sessions')
        self.assertEqual(len(first.storage_path.name), 32)

    def test_matching_collection_exit_allows_only_owned_session_deletion(self):
        session, control, _ = self.fixture()
        other = ManagedWebViewSession(self.profile)
        recovery = self.profile / 'recovery'
        recovery.mkdir()
        (recovery / 'keep.txt').write_text('unsaved work')
        (session.storage_path / 'cache').write_text('test cache')
        control.on_dispose = lambda: self.exit(control)
        result = session.shutdown(1)
        self.assertEqual(result['status'], 'removed')
        self.assertTrue(result['runtime_cache_removed'])
        self.assertTrue(result['collection_identity_matched'])
        self.assertFalse(session.storage_path.exists())
        self.assertTrue(other.storage_path.exists())
        self.assertEqual((recovery / 'keep.txt').read_text(), 'unsaved work')
        control.InvokeRequired = False  # A completed receipt needs no UI wait.
        self.assertEqual(session.shutdown(.1), result)

    def test_after_closed_cleanup_never_dispatches_to_disposed_control(self):
        session, control, _ = self.fixture()
        control.on_dispose = lambda: self.exit(control)
        session.dispose_on_ui()
        del control.InvokeRequired  # Reading this disposed control is forbidden.
        with patch.object(control, 'BeginInvoke', side_effect=AssertionError('dead dispatcher')):
            result = self.finish_on_worker(session)
            again = self.finish_on_worker(session)
        self.assertEqual(result['status'], 'removed')
        self.assertEqual(result, again)
        self.assertEqual(control.disposal_count, 1)
        self.assertEqual(result['deletion_attempts'], 1)
        self.assertTrue(result['collection_exit_observed'])
        self.assertTrue(result['collection_identity_matched'])

    def test_after_closed_without_collection_proof_retains_owned_session(self):
        session, control, _ = self.fixture()
        session.dispose_on_ui()
        self.exit(control, pid=456)
        result = self.finish_on_worker(session, .01)
        self.assertEqual(result['reason'], 'runtime_collection_exit_unconfirmed')
        self.assertEqual(result['deletion_attempts'], 0)
        self.assertTrue(session.storage_path.exists())

    def test_split_cleanup_enforces_affinity_and_cannot_assume_disposal(self):
        session, control, _ = self.fixture()
        with self.assertRaises(CleanupError):
            session.finish_shutdown(.01)
        errors = []
        def invalid_dispose():
            try:
                session.dispose_on_ui()
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=invalid_dispose)
        worker.start(); worker.join(1)
        self.assertIsInstance(errors[0], CleanupError)
        result = self.finish_on_worker(session, .01)
        self.assertEqual(result['reason'], 'native_disposal_unconfirmed')
        self.assertEqual(control.disposal_count, 0)
        self.assertTrue(session.storage_path.exists())

    def test_no_collection_event_retains_residue_after_bounded_wait(self):
        session, _, _ = self.fixture()
        result = session.shutdown(.01)
        self.assertEqual(result['reason'], 'runtime_collection_exit_unconfirmed')
        self.assertEqual(result['deletion_attempts'], 0)
        self.assertTrue(session.storage_path.exists())

    def test_owned_fallback_runs_after_disposal_and_still_requires_collection_receipt(self):
        calls = []
        def terminate(*, timeout_seconds):
            calls.append(timeout_seconds)
            self.assertEqual(control.disposal_count, 1)
            self.exit(control)
            return {'status': 'confirmed', 'forced_count': 2, 'remaining_children': 0}
        session, control, _ = self.fixture(terminate_owned_processes=terminate)
        (session.storage_path / 'cache').write_text('owned')
        result = session.shutdown(.2)
        self.assertEqual(len(calls), 1)
        self.assertGreater(calls[0], 0)
        self.assertLessEqual(calls[0], .2)
        self.assertEqual(result['status'], 'removed')
        self.assertTrue(result['collection_exit_observed'])
        self.assertTrue(result['collection_identity_matched'])
        self.assertTrue(result['owned_fallback_attempted'])
        self.assertEqual(result['owned_process_cleanup']['forced_count'], 2)

    def test_owned_fallback_confirmation_alone_never_authorizes_deletion(self):
        calls, waits = [], []
        clock = SimpleNamespace(now=0.0)
        def terminate(*, timeout_seconds):
            calls.append((timeout_seconds, control.disposal_count))
            return {'status': 'confirmed', 'forced_count': 2, 'remaining_children': 0}
        session, control, _ = self.fixture(terminate_owned_processes=terminate)
        def absent_collection_exit(timeout):
            self.assertFalse(session._exit.is_set())
            waits.append(timeout)
            clock.now += timeout
            return False
        # Preserve time for the fallback, then exhaust the final collection
        # wait deterministically; scheduler delay is not the behavior at issue.
        with patch('nivram.webview_cleanup.time', SimpleNamespace(monotonic=lambda: clock.now)), \
                patch.object(session._exit, 'wait', side_effect=absent_collection_exit):
            result = session.shutdown(.04)
        self.assertEqual(len(waits), 2)
        self.assertEqual(len(calls), 1)
        self.assertGreater(calls[0][0], 0)
        self.assertLessEqual(calls[0][0], .04)
        self.assertEqual(calls[0][1], 1)
        self.assertEqual(result['reason'], 'runtime_collection_exit_unconfirmed')
        self.assertTrue(result['owned_fallback_attempted'])
        self.assertEqual(result['owned_process_cleanup'],
                         {'status': 'confirmed', 'forced_count': 2, 'remaining_children': 0})
        self.assertTrue(result['control_disposed'])
        self.assertFalse(result['collection_exit_observed'])
        self.assertFalse(result['collection_identity_matched'])
        self.assertFalse(result['runtime_cache_removed'])
        self.assertEqual(result['deletion_attempts'], 0)
        self.assertTrue(session.storage_path.exists())

    def test_normal_collection_exit_does_not_request_forced_cleanup(self):
        def terminate(**kwargs):
            self.fail('Graceful collection exit must not request termination.')
        session, control, _ = self.fixture(terminate_owned_processes=terminate)
        control.on_dispose = lambda: self.exit(control)
        result = session.shutdown(.2)
        self.assertEqual(result['status'], 'removed')
        self.assertFalse(result['owned_fallback_attempted'])

    def test_fallback_exception_retains_the_session(self):
        calls, waits = [], []
        clock = SimpleNamespace(now=0.0)
        def terminate(*, timeout_seconds):
            calls.append((timeout_seconds, control.disposal_count))
            raise OSError('Unavailable ownership handle')
        session, control, _ = self.fixture(terminate_owned_processes=terminate)
        def absent_collection_exit(timeout):
            self.assertFalse(session._exit.is_set())
            waits.append(timeout)
            clock.now += timeout
            return False
        # Test the fallback exception with a deterministic remaining budget.
        # A real 40 ms deadline can expire during scheduler overshoot, correctly
        # skipping the fallback instead of reaching the exception under test.
        # Replace only this module's clock, not threading's shared time module.
        with patch('nivram.webview_cleanup.time', SimpleNamespace(monotonic=lambda: clock.now)), \
                patch.object(session._exit, 'wait', side_effect=absent_collection_exit):
            result = session.shutdown(.04)
        self.assertEqual(len(waits), 1)
        self.assertEqual(len(calls), 1)
        self.assertGreater(calls[0][0], 0)
        self.assertLessEqual(calls[0][0], .04)
        self.assertEqual(calls[0][1], 1)
        self.assertEqual(result['reason'], 'owned_process_cleanup_failed')
        self.assertTrue(result['owned_fallback_attempted'])
        self.assertTrue(result['control_disposed'])
        self.assertFalse(result['collection_exit_observed'])
        self.assertFalse(result['runtime_cache_removed'])
        self.assertIsNone(result['owned_process_cleanup'])
        self.assertEqual(result['deletion_attempts'], 0)
        self.assertTrue(session.storage_path.exists())

    def test_main_process_identity_mismatch_cannot_authorize_deletion(self):
        session, control, _ = self.fixture()
        control.on_dispose = lambda: self.exit(control, pid=456)
        result = session.shutdown(.01)
        self.assertFalse(result['collection_exit_observed'])
        self.assertTrue(session.storage_path.exists())

    def test_profile_identity_mismatch_in_exit_event_cannot_authorize_deletion(self):
        session, control, _ = self.fixture()
        control.on_dispose = lambda: self.exit(control, folder=self.profile)
        result = session.shutdown(.01)
        self.assertFalse(result['collection_identity_matched'])
        self.assertTrue(session.storage_path.exists())

    def test_runtime_using_other_profile_is_never_deleted(self):
        session = ManagedWebViewSession(self.profile)
        control = Control(self.profile)
        browser = SimpleNamespace(webview=control, clear_user_data=lambda: None)
        window = SimpleNamespace(native=SimpleNamespace(webview=control, browser=browser),
                                 events=SimpleNamespace(before_show=Event()))
        session.attach(window)
        window.events.before_show.fire()
        result = session.shutdown(.01)
        self.assertFalse(result['native_profile_matched'])
        self.assertEqual(result['deletion_attempts'], 0)
        self.assertTrue(session.storage_path.exists())

    def test_ui_thread_call_is_rejected_before_disposal_or_waiting(self):
        session, control, _ = self.fixture()
        control.InvokeRequired = False
        with self.assertRaises(CleanupError):
            session.shutdown(.1)
        self.assertEqual(control.disposal_count, 0)

    def test_ui_dispatch_failure_retains_and_never_deletes(self):
        session, control, _ = self.fixture()
        with patch.object(control, 'BeginInvoke', side_effect=RuntimeError('test failure')):
            result = session.shutdown(.1)
        self.assertEqual(result['reason'], 'native_disposal_dispatch_failed')
        self.assertTrue(session.storage_path.exists())

    def test_unprocessed_ui_queue_is_bounded_and_retains(self):
        session, control, _ = self.fixture()
        with patch.object(control, 'BeginInvoke', return_value=None):
            result = session.shutdown(.01)
        self.assertEqual(result['reason'], 'native_disposal_unconfirmed')
        self.assertEqual(result['deletion_attempts'], 0)

    def test_library_close_callback_disposes_without_deleting(self):
        session, control, window = self.fixture()
        window.native.browser.clear_user_data()
        window.native.browser.clear_user_data()
        self.assertEqual(control.disposal_count, 1)
        self.assertTrue(session.storage_path.exists())
        self.assertFalse(session.snapshot()['runtime_cache_removed'])

    def test_delete_error_reports_retained_instead_of_claiming_cleanup(self):
        session, control, _ = self.fixture()
        control.on_dispose = lambda: self.exit(control)
        with patch('nivram.webview_cleanup.shutil.rmtree', side_effect=PermissionError('file in use')):
            result = session.shutdown(.1)
        self.assertEqual(result['status'], 'retained')
        self.assertEqual(result['reason'], 'owned_session_deletion_failed')
        self.assertFalse(result['runtime_cache_removed'])

    def test_stalled_filesystem_deletion_cannot_block_close_indefinitely(self):
        session, control, _ = self.fixture()
        control.on_dispose = lambda: self.exit(control)
        release = threading.Event()
        def delayed_remove(_target):
            release.wait(1)
            raise PermissionError('still retained')
        with patch('nivram.webview_cleanup.shutil.rmtree', side_effect=delayed_remove):
            try:
                result = session.shutdown(.02)
                self.assertEqual(result['status'], 'unconfirmed')
                self.assertIsNone(result['runtime_cache_removed'])
                self.assertEqual(result['reason'], 'owned_session_deletion_timeout')
            finally:
                release.set()
                session._delete_finished.wait(1)

    def test_reassigned_target_is_rejected_before_recursive_delete(self):
        session, control, _ = self.fixture()
        original = session.storage_path
        session.storage_path = self.profile
        control.on_dispose = lambda: self.exit(control)
        with patch('nivram.webview_cleanup.shutil.rmtree') as remove:
            result = session.shutdown(.1)
        remove.assert_not_called()
        self.assertEqual(result['reason'], 'owned_session_deletion_failed')
        self.assertTrue(original.exists())

    def test_uninitialized_startup_failure_retains_without_waiting_for_browser(self):
        session = ManagedWebViewSession(self.profile)
        result = session.shutdown(.01)
        self.assertEqual(result['reason'], 'native_control_unavailable')
        self.assertTrue(session.storage_path.exists())

    def test_invalid_timeout_and_other_window_attachment_are_rejected(self):
        session, _, _ = self.fixture()
        for timeout in (0, -1, 61, float('nan'), float('inf'), True, '20'):
            with self.subTest(timeout=timeout), self.assertRaises(CleanupError):
                session.shutdown(timeout)
        with self.assertRaises(CleanupError):
            session.attach(SimpleNamespace())


if __name__ == '__main__':
    unittest.main()
