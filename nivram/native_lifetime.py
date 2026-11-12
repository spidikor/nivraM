"""One window's actual-close receipt and a bounded WinForms callback lifetime.

The pinned pywebview WinForms backend calls Application.Exit inside FormClosed.
A hidden form cancels that exit until owned finalization finishes (or the native
watchdog expires). No vendor files, global classes, or other windows are patched.
Native calls run on the captured STA; workers only marshal release with BeginInvoke.
"""
from __future__ import annotations

import math
import threading
import time


def _timeout(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 < value <= 60):
        raise ValueError('The lifetime timeout must be finite, positive, and at most 60 seconds.')
    return value


def _native_runtime():
    from System import Action
    import System.Windows.Forms as Forms
    return Action, Forms


class FinalizationGroup:
    """Start independent owned cleanup stages once, keeping failures isolated.

    Each stage owns a retained daemon thread. A delayed stage cannot prevent
    another resource owner from receiving shutdown. Timeouts report incomplete
    work; they do not claim that a Python/native call was cancelled or retried.
    """
    def __init__(self, stages):
        self._stages = tuple(stages)
        names = [name for name, _ in self._stages]
        if len(names) != len(set(names)):
            raise ValueError('Finalization stage names must be unique.')
        self._lock = threading.RLock()
        self._started = False
        self._done = threading.Event()
        self._threads = []
        self._receipts = {name: {'status': 'pending'} for name in names}

    def snapshot(self):
        with self._lock:
            receipts = {name: dict(value) for name, value in self._receipts.items()}
            return {'started': self._started, 'completed': self._done.is_set(),
                    'stages': receipts,
                    'workers_alive': sum(thread.is_alive() for thread in self._threads)}

    def _settle(self, name, receipt):
        with self._lock:
            self._receipts[name] = receipt
            if all(value['status'] != 'pending' for value in self._receipts.values()):
                self._done.set()

    def _run_stage(self, name, callback):
        try:
            result = callback()
        except BaseException as error:
            self._settle(name, {'status': 'failed', 'error': type(error).__name__})
        else:
            self._settle(name, {'status': 'returned', 'result': result})

    def run(self, timeout_seconds=30):
        timeout_seconds = _timeout(timeout_seconds)
        with self._lock:
            if not self._started:
                self._started = True
                for name, callback in self._stages:
                    thread = threading.Thread(target=self._run_stage, args=(name, callback),
                        name='nivraM-finalize-' + name, daemon=True)
                    self._threads.append(thread)
                    try:
                        thread.start()
                    except BaseException as error:
                        self._settle(name, {'status': 'failed', 'error': type(error).__name__})
                if not self._stages:
                    self._done.set()
        self._done.wait(timeout_seconds)
        return self.snapshot()


class NativeLifetime:
    """Own a per-window STA guard, actual close evidence and one finalizer."""
    def __init__(self, window, *, on_closed, cancel_on_ui, dispose_on_ui, finalize,
                 timeout_seconds=35):
        self._timeout = _timeout(timeout_seconds)
        self._window = window
        self._on_closed = on_closed
        self._cancel_on_ui = cancel_on_ui
        self._dispose_on_ui = dispose_on_ui
        self._finalize = finalize
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._finished = threading.Event()
        self._worker = None
        self._sta = None
        self._guard = self._timer = None
        self._forms = self._action = None
        self._release_action = None
        self._allow_exit = False
        self._release_requested = False
        self._closed_at = None
        self._values = {'status': 'attached', 'closed_observed': False,
                        'guard_released': False, 'watchdog_release': False,
                        'guard_cancelled_exit': 0, 'finalizer_finished': False, 'errors': []}
        self._handlers = (self._before_show, self._guard_closing, self._tick)
        window.events.before_show += self._before_show

    def snapshot(self):
        with self._lock:
            return {**self._values, 'errors': list(self._values['errors']),
                    'finalizer_alive': bool(self._worker and self._worker.is_alive())}

    def _error(self, stage, error):
        with self._lock:
            if len(self._values['errors']) < 16:
                self._values['errors'].append({'stage': stage, 'error': type(error).__name__})

    def _assert_ui(self):
        if threading.get_ident() != self._sta:
            raise RuntimeError('The lifecycle callback must use its owned native UI thread.')

    def _before_show(self):
        Action, Forms = _native_runtime()
        self._sta = threading.get_ident()
        self._forms, self._action = Forms, Action
        native = self._window.native
        guard = Forms.Form()
        self._guard = guard
        try:
            guard.Text = 'nivraM runtime cleanup'
            guard.ShowInTaskbar = False
            guard.FormBorderStyle = getattr(Forms.FormBorderStyle, 'None')
            guard.Opacity = 0
            guard.Width = guard.Height = 1
            guard.FormClosing += self._guard_closing
            timer = Forms.Timer()
            self._timer = timer
            timer.Interval = 100
            timer.Tick += self._tick
            # Insert our receipt before vendor code on this instance only.
            # Even a vendor exception before its public closed event is proof
            # of actual closure when entered through this native FormClosed.
            original = native.on_close
            def closed(sender, args):
                self._native_closed(original, sender, args)
            native.FormClosed -= original
            try:
                native.FormClosed += closed
            except BaseException:
                native.FormClosed += original
                raise
            self._native_closed_handler = closed
            guard.Show()
            guard.Hide()
            timer.Start()
            self._values['status'] = 'armed'
        except BaseException as error:
            self._error('guard_setup', error)
            self._values['status'] = 'setup_failed'
            # Partial setup must not leave an invisible veto with no watchdog.
            self._release_on_ui(exit_application=False)

    def _guard_closing(self, sender, args):
        self._assert_ui()
        if not self._allow_exit:
            args.Cancel = True
            with self._lock:
                self._values['guard_cancelled_exit'] += 1

    def _native_closed(self, original, sender, args):
        self._assert_ui()
        with self._lock:
            if self._closed.is_set():
                return
            self._closed_at = time.monotonic()
            self._closed.set()
            self._values.update(status='finalizing', closed_observed=True)
        try:
            self._on_closed()
        except BaseException as error:
            self._error('closed_observer', error)
        try:
            self._cancel_on_ui()
        except BaseException as error:
            self._error('download_cancel', error)
        try:
            original(sender, args)
        except BaseException as error:
            # Native closure already happened. Do not resurrect an editor or
            # lose finalization because a later vendor callback raised.
            self._error('vendor_closed', error)
        finally:
            try:
                # Normally vendor clear_user_data already disposed it. This is
                # the idempotent fallback for an earlier vendor callback error.
                self._dispose_on_ui()
            except BaseException as error:
                self._error('native_dispose', error)
            self._start_finalizer()

    def _start_finalizer(self):
        with self._lock:
            if self._worker is not None:
                return
            self._worker = threading.Thread(target=self._run_finalizer,
                name='nivraM-native-finalizer', daemon=True)
            try:
                self._worker.start()
            except BaseException as error:
                self._error('finalizer_start', error)
                self._finished.set()
                self.request_release()

    def _run_finalizer(self):
        try:
            self._finalize()
        except BaseException as error:
            self._error('finalizer', error)
        finally:
            with self._lock:
                self._values['finalizer_finished'] = True
            self._finished.set()
            self.request_release()

    def _tick(self, sender, args):
        self._assert_ui()
        if self._closed_at is not None and time.monotonic() - self._closed_at >= self._timeout:
            self._values['watchdog_release'] = True
            self._release_on_ui()

    def _release_on_ui(self, *, exit_application=True):
        self._assert_ui()
        if self._allow_exit:
            return
        self._allow_exit = True
        self._values['guard_released'] = True
        try:
            if self._guard is not None:
                self._guard.Close()
        except BaseException as error:
            self._error('guard_close', error)
        finally:
            try:
                if self._timer is not None:
                    self._timer.Stop()
                    self._timer.Dispose()
            except BaseException as error:
                self._error('timer_dispose', error)
            finally:
                if exit_application and self._closed.is_set():
                    # No cancelling main window remains. ExitThread is a
                    # current-STA fallback if Application.Exit itself fails.
                    try:
                        self._forms.Application.Exit()
                    except BaseException as error:
                        self._error('application_exit', error)
                        self._forms.Application.ExitThread()

    def request_release(self):
        with self._lock:
            if self._release_requested or self._allow_exit or self._guard is None:
                return
            self._release_requested = True
        try:
            self._release_action = self._action(self._release_on_ui)
            self._guard.BeginInvoke(self._release_action)
        except BaseException as error:
            # Keep the native watchdog armed. Never wait synchronously here.
            self._error('release_dispatch', error)

    def finish(self, timeout_seconds=3):
        """Outer-finally safety: release any remaining guard and boundedly join."""
        timeout_seconds = _timeout(timeout_seconds)
        if self._sta == threading.get_ident():
            raise RuntimeError('Finalizer joins must not block the native UI thread.')
        self.request_release()
        worker = self._worker
        if worker is not None and worker.ident is not None:
            worker.join(timeout_seconds)
        return self.snapshot()
