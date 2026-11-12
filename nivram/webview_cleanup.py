"""Own one private WebView2 session and delete it only after verified runtime exit.

Create ManagedWebViewSession(profile_dir), attach(window) before webview.start,
and pass str(session.storage_path) to webview.start(storage_path=...). After the
actual FormClosed event, dispose_on_ui() runs on its native thread and a worker
calls finish_shutdown() while the lifetime guard keeps that UI loop alive.
shutdown() retains the explicit pre-disposal API for isolated acceptance callers.
All waits are bounded. An optional host-owned process job can stop remaining
children after a graceful wait. Deletion still requires the SDK collection-exit
receipt; no PID/name discovery or another session/recovery file is involved.

pywebview 6.2.1's instance cleanup callback disposes the control, ignores the
result of a 3-second main-process wait, then deletes the UDF. Replace that one
instance callback with Dispose only; no vendor files or global classes change.

Primary contracts reviewed 2026-09-19:
https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder#wait-for-browser-processes-to-exit-before-deleting-the-udf
https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2environment.browserprocessexited
"""
from __future__ import annotations

import math
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

from .webview_parent_observations import ParentWebViewCapture


class CleanupError(RuntimeError):
    """The lifecycle API was called from an unsupported state/thread."""


def _ui_action(callback):
    from System import Action
    return Action(callback)


class ManagedWebViewSession:
    """Unique session ownership; a collection exit receipt is required to delete."""
    def __init__(self, profile_dir, *, terminate_owned_processes=None, parent_observations=None):
        if terminate_owned_processes is not None and not callable(terminate_owned_processes):
            raise CleanupError('Owned process cleanup must be callable.')
        if parent_observations is not None:
            if type(parent_observations) is not ParentWebViewCapture:
                raise CleanupError('Parent WebView observations require their exact capture type.')
            try:
                ParentWebViewCapture.configure(parent_observations)
            except ValueError:
                raise CleanupError('Parent WebView observations already belong to a session.') from None
        self._parent_observations = parent_observations
        self._terminate_owned_processes = terminate_owned_processes
        self._profile = Path(profile_dir).resolve()
        self._root = self._profile / 'webview-sessions'
        self._root.mkdir(parents=True, exist_ok=True)
        # Resolve before creating a session, and reject a redirected managed root.
        if self._root.resolve() != self._root:
            raise CleanupError('The managed WebView directory must not be redirected.')
        self._session_id = uuid.uuid4().hex
        self.storage_path = self._root / self._session_id
        self.storage_path.mkdir()
        self._created_path = self.storage_path.resolve()
        self._lock = threading.RLock()
        self._exit = threading.Event()
        self._disposed = threading.Event()
        self._window = None
        self._control = None
        self._ui_thread_id = None
        self._environment = None
        self._browser_pid = None
        self._shutdown_started = False
        self._dispose_started = False
        self._action = None
        self._observation_form = None
        self._pulse_action = None
        self._delete_finished = threading.Event()
        self._delete_thread = None
        self._values = {
            'status': 'new', 'session_id': self._session_id,
            'native_profile_matched': False, 'collection_exit_observed': False,
            'collection_identity_matched': False, 'browser_exit_kind': None,
            'control_disposed': False, 'runtime_cache_removed': False,
            'deletion_attempts': 0, 'reason': None,
            'owned_fallback_attempted': False, 'owned_process_cleanup': None,
        }
        self._handlers = (self._before_show, self._on_initialized, self._on_process_exited)
        if parent_observations is not None:
            self._handlers += (self._on_form_closed_observation, self._on_post_dispose_pulse)

    def _observe(self, method, *args, **kwargs):
        """Optional fixed marks cannot replace a primary cleanup operation."""
        if self._parent_observations is None:
            return
        try:
            getattr(ParentWebViewCapture, method)(self._parent_observations, *args, **kwargs)
        except Exception:
            # No native calls or arbitrary exception text enter the capture.
            # Unknown/missing diagnostics cannot authorize deletion or success.
            pass

    def _attach_form_observation(self, native):
        self._observation_form = native
        try:
            native.FormClosed += self._on_form_closed_observation
        except Exception:
            self._observe('form_closed_hook', 'failed')
        else:
            self._observe('form_closed_hook', 'installed')

    def _on_form_closed_observation(self, _sender, _args):
        # Registration belongs only to the captured app Form. No native reads,
        # teardown dispatch, lifetime veto or process discovery happen here.
        self._observe('form_closed')

    def _on_post_dispose_pulse(self):
        self._observe('pulse_seen', on_ui=self._ui_thread_id == threading.get_ident())

    def _queue_post_dispose_pulse(self):
        """One optional UI mark on the Form that survives control disposal."""
        self._observe('pulse_dispatch', 'requested')
        try:
            self._pulse_action = _ui_action(self._on_post_dispose_pulse)
            self._observation_form.BeginInvoke(self._pulse_action)
        except Exception:
            self._observe('pulse_dispatch', 'failed')
        else:
            # Dispatch acceptance is distinct from the mark actually running.
            self._observe('pulse_dispatch', 'queued')

    def snapshot(self):
        with self._lock:
            return dict(self._values)

    def _update(self, **values):
        with self._lock:
            self._values.update(values)

    def _retain(self, reason):
        self._update(status='retained', runtime_cache_removed=False, reason=reason)

    def attach(self, window):
        if self._window is not None:
            if self._window is window:
                return self
            raise CleanupError('The session already belongs to another window.')
        if getattr(window, '_nivram_profile_cleanup', None) is not None:
            raise CleanupError('This window already owns a managed session.')
        self._window = window
        window.events.before_show += self._before_show
        window._nivram_profile_cleanup = self
        self._update(status='attached')
        return self

    def _before_show(self):
        try:
            native = self._window.native
            self._ui_thread_id = threading.get_ident()
            self._control = native.webview
            if native.browser.webview is not self._control:
                raise CleanupError('Native control identity mismatch.')
            # This particular app window uses its own unique storage directory.
            # Keep InPrivate mode while preventing vendor premature rmtree.
            native.browser.clear_user_data = self._dispose_control_on_ui
            self._control.CoreWebView2InitializationCompleted += self._on_initialized
            self._update(status='armed')
            if self._control.CoreWebView2 is not None:
                self._track_core()
            if self._parent_observations is not None:
                self._attach_form_observation(native)
        except Exception:
            self._retain('native_cleanup_setup_failed')

    def _on_initialized(self, sender, args):
        try:
            if not args.IsSuccess:
                self._retain('native_initialization_failed')
                return
            self._track_core()
        except Exception:
            self._retain('runtime_exit_observer_setup_failed')

    def _track_core(self):
        if self._environment is not None:
            return
        core = self._control.CoreWebView2
        environment = core.Environment
        if Path(str(environment.UserDataFolder)).resolve() != self._created_path:
            self._retain('native_profile_directory_mismatch')
            return
        pid = int(core.BrowserProcessId)
        if pid <= 0:
            raise CleanupError('Invalid native process identity.')
        environment.BrowserProcessExited += self._on_process_exited
        self._environment = environment
        self._browser_pid = pid
        self._update(status='tracking', native_profile_matched=True)

    def _on_process_exited(self, sender, args):
        self._observe('callback_entered')
        failure_code = 'browser_identity_read_failed'
        try:
            # The event's browser identity ties its whole process collection to
            # this session, rather than an unrelated process exit notification.
            browser_match = int(args.BrowserProcessId) == self._browser_pid
            self._observe('callback_browser_checked', browser_match)
            if not browser_match:
                return
            failure_code = 'profile_identity_read_failed'
            profile_match = Path(str(sender.UserDataFolder)).resolve() == self._created_path
            self._observe('callback_profile_checked', profile_match)
            if not profile_match:
                return
            failure_code = 'exit_kind_read_failed'
            exit_kind = str(args.BrowserProcessExitKind)
            failure_code = 'receipt_publish_failed'
            self._update(collection_exit_observed=True, collection_identity_matched=True,
                         browser_exit_kind=exit_kind)
            self._exit.set()
            self._observe('callback_verified')
        except Exception:
            self._observe('callback_failed', failure_code)
            # No event proof means no deletion. Keep diagnostics bounded.
            self._update(reason='runtime_exit_identity_unconfirmed')

    def _dispose_control_on_ui(self):
        if self._dispose_started:
            return
        self._dispose_started = True
        self._observe('dispose_entered', on_ui=self._ui_thread_id == threading.get_ident())
        try:
            self._control.Dispose()
            self._observe('dispose_returned')
            self._update(control_disposed=True)
            if self._parent_observations is not None:
                self._queue_post_dispose_pulse()
        except Exception:
            self._retain('native_control_disposal_failed')
        finally:
            self._disposed.set()

    def dispose_on_ui(self):
        """Idempotent disposal after actual close; never wait on the UI thread."""
        if self._ui_thread_id != threading.get_ident():
            raise CleanupError('Native disposal must run on the owned UI thread.')
        self._dispose_control_on_ui()

    def _verified_deletion_target(self):
        # Revalidate immediately before every recursive deletion, including root
        # and leaf redirects. Never remove the profile or the managed root itself.
        if not re.fullmatch(r'[0-9a-f]{32}', self._session_id):
            raise CleanupError('Invalid managed session identity.')
        expected = self._profile / 'webview-sessions' / self._session_id
        root = (self._profile / 'webview-sessions').resolve()
        target = self.storage_path.resolve()
        if (root != self._root or self.storage_path != expected or target != self._created_path or
                target.parent != root or not target.is_relative_to(root) or target == root or
                target.name != self._session_id):
            raise CleanupError('The cleanup target left its owned session directory.')
        return target

    def shutdown(self, timeout_seconds=20):
        """On a worker, dispose on UI then wait for collection exit and clean.

        Return the final receipt even on timeout/failure; callers may always close
        their window afterward. UI-thread calls are rejected before any wait.
        Do not call this after stopping the application's UI event loop.
        """
        return self._shutdown(timeout_seconds, dispatch_disposal=True)

    def finish_shutdown(self, timeout_seconds=20):
        """Wait for already-requested disposal and verified collection exit.

        No native control access or dispatch occurs here: FormClosed may have
        disposed that control. The independently owned STA guard pumps callbacks.
        """
        if self._ui_thread_id == threading.get_ident():
            raise CleanupError('Managed shutdown must run on a worker, never the native UI thread.')
        return self._shutdown(timeout_seconds, dispatch_disposal=False)

    def _shutdown(self, timeout_seconds, *, dispatch_disposal):
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or
                not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60):
            raise CleanupError('Cleanup timeout must be finite and at most 60 seconds.')
        with self._lock:
            if self._shutdown_started and self._values['status'] in ('removed', 'retained', 'unconfirmed'):
                return self.snapshot()
        if dispatch_disposal and self._control is not None and not self._control.InvokeRequired:
            raise CleanupError('Managed shutdown must run on a worker, never the native UI thread.')
        with self._lock:
            if self._shutdown_started:
                if self._values['status'] in ('removed', 'retained', 'unconfirmed'):
                    return self.snapshot()
                raise CleanupError('Managed shutdown is already running.')
            self._shutdown_started = True
        deadline = time.monotonic() + timeout_seconds
        if self._control is None:
            self._retain('native_control_unavailable')
            return self.snapshot()
        if dispatch_disposal:
            self._observe('dispose_dispatch', 'requested')
            try:
                self._action = _ui_action(self._dispose_control_on_ui)
                self._update(status='closing')
                self._control.BeginInvoke(self._action)
                self._observe('dispose_dispatch', 'queued')
            except Exception:
                self._observe('dispose_dispatch', 'failed')
                self._retain('native_disposal_dispatch_failed')
                return self.snapshot()
        if not self._disposed.wait(max(0, deadline-time.monotonic())):
            self._retain('native_disposal_unconfirmed')
            return self.snapshot()
        if not self.snapshot()['control_disposed']:
            self._retain('native_control_disposal_failed')
            return self.snapshot()
        if self._environment is None or not self.snapshot()['native_profile_matched']:
            self._retain('runtime_exit_observer_unavailable')
            return self.snapshot()
        remaining = max(0, deadline-time.monotonic())
        graceful = min(5.0, remaining / 2) if self._terminate_owned_processes else remaining
        if not self._exit.wait(graceful) and self._terminate_owned_processes:
            # The desktop host acquired this authority before spawning any
            # children. Only actual disposal plus the matched private profile
            # reaches this fallback. A successful kill receipt is not the SDK
            # collection receipt and cannot authorize directory deletion.
            remaining = max(0, deadline-time.monotonic())
            if remaining > 0:
                self._update(owned_fallback_attempted=True)
                try:
                    cleanup = self._terminate_owned_processes(timeout_seconds=min(5.0, remaining / 2))
                    self._update(owned_process_cleanup=cleanup)
                except Exception:
                    self._retain('owned_process_cleanup_failed')
                    return self.snapshot()
        if not self._exit.wait(max(0, deadline-time.monotonic())):
            self._retain('runtime_collection_exit_unconfirmed')
            return self.snapshot()
        if time.monotonic() >= deadline:
            self._retain('cleanup_budget_exhausted')
            return self.snapshot()
        # Filesystem operations themselves can stall (for example, a filter
        # driver can delay deletion). Never let that keep the window open forever.
        # A timeout is explicitly unconfirmed: deletion may finish later, so do
        # not assert either full retention or successful removal on that receipt.
        self._delete_thread = threading.Thread(target=self._delete_owned_session,
                                               name='nivraM-profile-cleanup', daemon=True)
        self._delete_thread.start()
        if not self._delete_finished.wait(max(0, deadline-time.monotonic())):
            with self._lock:
                if self._values['status'] not in ('removed', 'retained'):
                    self._values.update(status='unconfirmed', runtime_cache_removed=None,
                                        reason='owned_session_deletion_timeout')
        return self.snapshot()

    def _delete_owned_session(self):
        try:
            target = self._verified_deletion_target()
            with self._lock:
                self._values['deletion_attempts'] += 1
            if target.exists():
                shutil.rmtree(target)
            if target.exists():
                raise CleanupError('The session directory still exists.')
            self._update(status='removed', runtime_cache_removed=True, reason=None)
        except Exception:
            self._retain('owned_session_deletion_failed')
        finally:
            self._delete_finished.set()
