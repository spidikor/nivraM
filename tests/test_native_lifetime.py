"""Ordering and failure contracts; actual STA/WebView behavior has a native gate."""
from collections import deque
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import Mock, patch

from nivram.desktop import _finalization_group
from nivram.lifecycle import CloseCoordinator
from nivram.native_lifetime import FinalizationGroup, NativeLifetime


class Event:
    def __init__(self):
        self.handlers = []
    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self
    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self
    def fire(self, *args):
        for handler in tuple(self.handlers):
            handler(*args)


class Form:
    def __init__(self):
        self.FormClosed, self.FormClosing = Event(), Event()
        self.pending = deque()
        self.closed = False
        self.dispatch_error = False
        self.visible = False
    def Show(self):
        self.visible = True
    def Hide(self):
        self.visible = False
    def Close(self):
        if self.closed:
            return
        args = SimpleNamespace(Cancel=False)
        self.FormClosing.fire(self, args)
        if not args.Cancel:
            self.closed = True
            self.FormClosed.fire(self, None)
    def BeginInvoke(self, action):
        if self.dispatch_error:
            raise OSError('dispatcher rejected action')
        self.pending.append(action)
    def pump(self):
        while self.pending:
            self.pending.popleft()()


class Timer:
    def __init__(self):
        self.Tick = Event()
        self.running = self.disposed = False
    def Start(self):
        self.running = True
    def Stop(self):
        self.running = False
    def Dispose(self):
        self.disposed = True


class LifetimeTests(unittest.TestCase):
    def setUp(self):
        self.native = Form()
        self.calls = []
        self.window = SimpleNamespace(native=self.native,
            events=SimpleNamespace(before_show=Event(), closed=threading.Event()))
        self.exit_calls = 0
        def application_exit():
            self.exit_calls += 1
            self.lifetime._guard.Close()
        self.forms = SimpleNamespace(Form=Form, Timer=Timer,
            FormBorderStyle=SimpleNamespace(**{'None': 0}),
            Application=SimpleNamespace(Exit=application_exit, ExitThread=Mock()))
        self.runtime = patch('nivram.native_lifetime._native_runtime',
            return_value=(lambda callback: callback, self.forms))
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        def vendor(sender, args):
            self.calls.append('vendor')
            self.window.events.closed.set()
            application_exit()
        self.native.on_close = vendor
        self.native.FormClosed += vendor

    def arm(self, *, finalize=None, on_closed=None, timeout=.1):
        self.lifetime = NativeLifetime(self.window,
            on_closed=on_closed or (lambda: self.calls.append('observed')),
            cancel_on_ui=lambda: self.calls.append('cancel'),
            dispose_on_ui=lambda: self.calls.append('dispose'),
            finalize=finalize or (lambda: self.calls.append('finalize')),
            timeout_seconds=timeout)
        self.window.events.before_show.fire()
        self.assertEqual(self.lifetime.snapshot()['status'], 'armed')
        return self.lifetime

    def settle(self):
        self.assertTrue(self.lifetime._finished.wait(1))
        self.lifetime._worker.join(1)
        self.lifetime._guard.pump()

    def test_actual_close_observed_before_vendor_and_disposal_before_finalization(self):
        lifetime = self.arm()
        self.native.Close()
        self.settle()
        self.assertEqual(self.calls, ['observed', 'cancel', 'vendor', 'dispose', 'finalize'])
        self.assertEqual(lifetime.snapshot()['guard_cancelled_exit'], 1)
        self.assertTrue(lifetime.snapshot()['guard_released'])
        self.assertTrue(lifetime._guard.closed)
        self.assertTrue(lifetime._timer.disposed)
        self.assertFalse(lifetime.snapshot()['finalizer_alive'])

    def _assert_services_survive(self, failure):
        images, setup, batch, reference_exports, population_exports, server = [Mock() for _ in range(6)]
        group = _finalization_group(SimpleNamespace(_images=images, _optical_setup=setup, _batch=batch, _reference_exports=reference_exports, _population_exports=population_exports),
                                     server, None, None, None)
        self.window.evaluate_js = lambda expression: {'dirty': False, 'revision': 1}
        self.window.create_confirmation_dialog = lambda *args: False
        def destroy():
            if failure == 'raise':
                raise OSError('native close failed')
            return None
        self.window.destroy = destroy
        coordinator = CloseCoordinator(self.window, Mock())
        self.arm(finalize=lambda: group.run(.1), on_closed=coordinator.observe_closed)
        if failure == 'raise':
            with self.assertRaises(OSError):
                coordinator._review_close()
        else:
            coordinator._review_close()
        self.assertFalse(group.snapshot()['started'])
        images.close.assert_not_called()
        setup.close.assert_not_called()
        batch.close.assert_not_called()
        reference_exports.close.assert_not_called()
        population_exports.close.assert_not_called()
        server.server_close.assert_not_called()
        self.assertFalse(coordinator._approved)
        self.assertFalse(self.lifetime._closed.is_set())
        # Explicit synthetic test teardown; does not represent app close.
        self.lifetime._release_on_ui(exit_application=False)

    def test_failed_destroy_keeps_real_service_owners_live(self):
        self._assert_services_survive('raise')

    def test_vetoed_destroy_keeps_real_service_owners_live(self):
        self._assert_services_survive('veto')

    def test_vendor_exception_before_public_event_still_closes_and_finalizes(self):
        original = self.native.on_close
        self.native.FormClosed -= original
        def failed_vendor(*args):
            self.calls.append('vendor-failed')
            raise OSError('before public event')
        self.native.on_close = failed_vendor
        self.native.FormClosed += failed_vendor
        observed = threading.Event()
        lifetime = self.arm(on_closed=observed.set)
        self.native.Close()
        self.settle()
        self.assertTrue(observed.is_set())
        self.assertFalse(self.window.events.closed.is_set())
        self.assertEqual(self.calls, ['cancel', 'vendor-failed', 'dispose', 'finalize'])
        self.assertEqual(lifetime.snapshot()['errors'], [{'stage': 'vendor_closed', 'error': 'OSError'}])

    def test_duplicate_native_events_do_not_repeat_vendor_or_finalization(self):
        self.arm()
        self.native.Close()
        self.native.FormClosed.fire(self.native, None)
        self.settle()
        self.assertEqual(self.calls.count('vendor'), 1)
        self.assertEqual(self.calls.count('dispose'), 1)
        self.assertEqual(self.calls.count('finalize'), 1)

    def test_callback_errors_do_not_skip_following_cleanup_or_release(self):
        def fail():
            raise ValueError('injected')
        lifetime = self.arm(finalize=fail, on_closed=fail)
        lifetime._cancel_on_ui = fail
        lifetime._dispose_on_ui = fail
        self.native.Close()
        self.settle()
        self.assertEqual([entry['stage'] for entry in lifetime.snapshot()['errors']],
                         ['closed_observer', 'download_cancel', 'native_dispose', 'finalizer'])
        self.assertTrue(lifetime._guard.closed)

    def test_stalled_finalizer_watchdog_releases_guard_without_claiming_completion(self):
        release = threading.Event()
        lifetime = self.arm(finalize=lambda: release.wait(1))
        try:
            self.native.Close()
            with patch('nivram.native_lifetime.time.monotonic', return_value=lifetime._closed_at + 1):
                lifetime._tick(None, None)
            self.assertTrue(lifetime.snapshot()['watchdog_release'])
            self.assertTrue(lifetime._guard.closed)
            self.assertFalse(lifetime.snapshot()['finalizer_finished'])
        finally:
            release.set()
            self.settle()

    def test_failed_release_dispatch_keeps_watchdog_able_to_exit(self):
        release = threading.Event()
        lifetime = self.arm(finalize=lambda: release.wait(1))
        try:
            self.native.Close()
            lifetime._guard.dispatch_error = True
            release.set()
            self.assertTrue(lifetime._finished.wait(1))
            lifetime._worker.join(1)
            self.assertTrue(lifetime._timer.running)
            with patch('nivram.native_lifetime.time.monotonic', return_value=lifetime._closed_at + 1):
                lifetime._tick(None, None)
            self.assertTrue(lifetime._guard.closed)
        finally:
            release.set()
            lifetime._worker.join(1)

    def test_native_access_and_join_enforce_thread_affinity(self):
        lifetime = self.arm()
        errors = []
        def misuse():
            try:
                lifetime._tick(None, None)
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=misuse)
        worker.start(); worker.join(1)
        self.assertIsInstance(errors[0], RuntimeError)
        with self.assertRaises(RuntimeError):
            lifetime.finish(.01)
        lifetime._release_on_ui(exit_application=False)

    def test_partial_guard_setup_cannot_leave_permanent_veto(self):
        with patch.object(Form, 'Show', side_effect=OSError('show failed')):
            lifetime = NativeLifetime(self.window, on_closed=lambda: None,
                cancel_on_ui=lambda: None, dispose_on_ui=lambda: None, finalize=lambda: None)
            self.lifetime = lifetime
            self.window.events.before_show.fire()
        self.assertEqual(lifetime.snapshot()['status'], 'setup_failed')
        self.assertTrue(lifetime._guard.closed)
        self.assertTrue(lifetime._timer.disposed)
        self.assertTrue(lifetime._allow_exit)

    def test_outer_finally_release_is_marshaled_and_bounded(self):
        release = threading.Event()
        lifetime = self.arm(finalize=lambda: release.wait(1))
        try:
            self.native.Close()
            receipt = []
            worker = threading.Thread(target=lambda: receipt.append(lifetime.finish(.01)))
            worker.start(); worker.join(.5)
            self.assertFalse(worker.is_alive())
            self.assertTrue(receipt[0]['finalizer_alive'])
            lifetime._guard.pump()
            self.assertTrue(lifetime._guard.closed)
        finally:
            release.set()
            self.settle()


class FinalizationTests(unittest.TestCase):
    def test_failed_and_stalled_stages_do_not_prevent_other_owners_shutdown(self):
        release, visited = threading.Event(), []
        def fail():
            raise OSError('injected')
        group = FinalizationGroup([('stalled', lambda: release.wait(1)), ('failed', fail),
                                   ('scientific', lambda: visited.append('scientific'))])
        try:
            receipt = group.run(.02)
            self.assertFalse(receipt['completed'])
            self.assertEqual(receipt['stages']['stalled']['status'], 'pending')
            self.assertEqual(receipt['stages']['failed']['status'], 'failed')
            self.assertEqual(visited, ['scientific'])
            group.run(.01)
            self.assertEqual(visited, ['scientific'])
        finally:
            release.set()
            group.run(1)
            for worker in group._threads:
                worker.join(1)

    def test_desktop_ownership_runs_each_resource_once_even_after_error(self):
        images, setup, batch, reference_exports, population_exports, server, downloads, session = [Mock() for _ in range(8)]
        images.close.side_effect = ValueError('one stage fails')
        group = _finalization_group(SimpleNamespace(_images=images, _optical_setup=setup, _batch=batch, _reference_exports=reference_exports, _population_exports=population_exports),
                                     server, None, downloads, session)
        self.assertTrue(group.run(1)['completed'])
        group.run(.01)
        images.close.assert_called_once_with()
        setup.close.assert_called_once_with()
        batch.close.assert_called_once_with()
        reference_exports.close.assert_called_once_with()
        population_exports.close.assert_called_once_with()
        server.server_close.assert_called_once_with()
        server.shutdown_scientific_jobs.assert_not_called()  # LocalServer.server_close owns it.
        downloads.finish_shutdown.assert_called_once_with(timeout_seconds=5)
        session.finish_shutdown.assert_called_once_with(timeout_seconds=20)
        session.shutdown.assert_not_called()

    def test_empty_group_and_invalid_budgets(self):
        self.assertTrue(FinalizationGroup([]).run(.01)['completed'])
        for value in (0, -1, 61, True, float('nan'), float('inf'), '1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                FinalizationGroup([]).run(value)


if __name__ == '__main__':
    unittest.main()
