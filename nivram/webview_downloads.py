"""Per-window, deferred WebView2 downloads with bounded lifecycle receipts.

Attach before webview.start, alongside webview_policy. The pinned pywebview
6.2.1 ready callback reads its instance's on_download_starting attribute; replacing
that attribute before initialization avoids its synchronous modal event handler.
The original callback is never subscribed for this window.

All COM access and native dialogs run on the native UI thread. Worker methods
wait only for cached, data-free receipts, never for COM calls on that thread.
See docs/webview-downloads.md for contracts and the native validation boundary.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import ntpath
import re
import threading
import time

from .webview_policy import UrlPolicy


class DownloadError(RuntimeError):
    """A bounded download lifecycle call could not be made safely."""


_FORMATS = {
    'image/svg+xml': (('.svg',), 'SVG artwork', 'SVG artwork (*.svg)|*.svg'),
    'image/png': (('.png',), 'PNG image', 'PNG images (*.png)|*.png'),
    'chemical/x-mdl-molfile': (('.mol',), 'MOL structure', 'MOL structures (*.mol)|*.mol'),
    'application/json': (('.nivram', '.json'), 'project', 'nivraM projects (*.nivram)|*.nivram|JSON files (*.json)|*.json'),
    'text/csv': (('.csv',), 'CSV table', 'CSV tables (*.csv)|*.csv'),
}
_RESERVED = re.compile(r'(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)')
DIAGNOSTIC_LIMIT = 1_000_000_000
DIAGNOSTIC_TRACE_LIMIT = 16
DIAGNOSTIC_EVENTS = frozenset(('start', 'admitted', 'blocked', 'dispatch_requested', 'dispatch_returned',
    'choose_entered', 'dialog_show_entered', 'dialog_returned', 'deferral_completed', 'state_observed', 'settled', 'failure'))
DIAGNOSTIC_FORMATS = frozenset(('png', 'svg', 'mol', 'json', 'csv', 'unrecognized'))
DIAGNOSTIC_FAILURES = frozenset(('download_cancel_dispatch_failed', 'download_cancellation_unconfirmed',
    'download_deferral_completion_failed', 'download_destination_rejected', 'download_dialog_cancellation_unconfirmed',
    'download_dialog_disposal_failed', 'download_dialog_failed', 'download_initialization_failed',
    'download_rejection_failed', 'download_setup_failed', 'download_start_failed', 'download_state_unconfirmed',
    'download_subscription_failed', 'download_wait_unconfirmed', 'unclassified'))
DIAGNOSTIC_REASONS = DIAGNOSTIC_FAILURES | frozenset(('closing', 'format_or_origin', 'active_download', 'selected', 'cancelled'))
DIAGNOSTIC_COUNTERS = ('last_id', 'accepted', 'blocked', 'completed', 'interrupted', 'failures', 'active', 'trace_count', 'trace_dropped')
PERMISSION_TRACE_LIMIT = 4
PERMISSION_STATUSES = frozenset(('pending', 'observing', 'detached', 'subscribe_failed', 'detach_failed'))
PERMISSION_STATES = frozenset(('Default', 'Allow', 'Deny', 'Unrecognized'))
_PERMISSION_KINDS = {'UnknownPermission': 0, 'Microphone': 1, 'Camera': 2, 'Geolocation': 3,
    'Notifications': 4, 'OtherSensors': 5, 'ClipboardRead': 6, 'MultipleAutomaticDownloads': 7,
    'FileReadWrite': 8, 'Autoplay': 9, 'LocalFonts': 10, 'MidiSystemExclusiveMessageAccess': 11,
    'MidiSystemExclusiveMessages': 11, 'WindowManagement': 12, 'PersistentStorage': 13}


def _ui_action(callback):
    from System import Action
    return Action(callback)


def _timeout(value):
    if (isinstance(value, bool) or not isinstance(value, (float, int)) or
            not math.isfinite(value) or not 0 < value <= 60):
        raise DownloadError('The timeout must be finite, positive, and at most 60 seconds.')
    return value


def _format(mime):
    if not isinstance(mime, str) or len(mime) > 128:
        return None
    parts = [part.strip().lower() for part in mime.split(';')]
    if any(part not in ('charset=utf-8', 'charset=us-ascii') for part in parts[1:]):
        return None
    return _FORMATS.get(parts[0])


def _suggested_name(value, extensions):
    leaf = ntpath.basename(str(value))[:160]
    stem, suffix = ntpath.splitext(leaf)
    suffix = suffix.lower() if suffix.lower() in extensions else extensions[0]
    stem = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', '_', stem).strip(' .')[:120]
    if not stem or _RESERVED.match(stem):
        stem = 'structure'
    return stem + suffix


def _valid_destination(value, extensions):
    # The native picker is the only source of this value; never accept a path
    # from web content or silently append an extension after overwrite consent.
    if not isinstance(value, str) or not 0 < len(value) <= 32767:
        return False
    leaf = ntpath.basename(value)
    return (ntpath.isabs(value) and bool(ntpath.splitdrive(value)[0]) and
            bool(leaf) and leaf == leaf.rstrip(' .') and not _RESERVED.match(leaf) and
            not any(ord(c) < 32 or ord(c) == 127 for c in value) and
            not any(c in '<>:"/\\|?*' for c in leaf) and
            ntpath.splitext(leaf)[1].lower() in extensions)


def _cancel_owned_popup(owner, title):
    """Post Cancel only to the exact owner's matching native save dialog.

    No global window/process enumeration, broad keystrokes, or destruction.
    A nested overwrite prompt or changed ownership produces False, not a guess.
    """
    import ctypes
    import os
    from ctypes import wintypes
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetLastActivePopup.argtypes = [wintypes.HWND]
    user.GetLastActivePopup.restype = wintypes.HWND
    user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW.restype = wintypes.BOOL
    hwnd = user.GetLastActivePopup(owner)
    if not hwnd or hwnd == owner or user.GetWindow(hwnd, 4) != owner:  # GW_OWNER
        return False
    owner_pid, popup_pid = wintypes.DWORD(), wintypes.DWORD()
    owner_thread = user.GetWindowThreadProcessId(owner, ctypes.byref(owner_pid))
    popup_thread = user.GetWindowThreadProcessId(hwnd, ctypes.byref(popup_pid))
    caption, class_name = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(64)
    user.GetWindowTextW(hwnd, caption, len(caption))
    user.GetClassNameW(hwnd, class_name, len(class_name))
    if (not owner_thread or popup_thread != owner_thread or owner_pid.value != os.getpid() or
            popup_pid.value != owner_pid.value or caption.value != title or class_name.value != '#32770'):
        return False
    return bool(user.PostMessageW(hwnd, 0x0111, 2, 0))  # WM_COMMAND / IDCANCEL


class _SaveDialog:
    def __init__(self, owner, suggested, format_info):
        import System.Windows.Forms as Forms
        self._forms = Forms
        self._owner = owner
        self._dialog = Forms.SaveFileDialog()
        self._title = 'nivraM - Export ' + format_info[1]
        self._dialog.Title = self._title
        self._dialog.Filter = format_info[2]
        self._dialog.FileName = suggested
        self._dialog.DefaultExt = ntpath.splitext(suggested)[1][1:]
        self._dialog.AddExtension = True
        self._dialog.CheckPathExists = True
        self._dialog.OverwritePrompt = True
        self._dialog.ValidateNames = True
        self._dialog.RestoreDirectory = True
        self._showing = False

    def choose(self):
        self._showing = True
        try:
            if self._dialog.ShowDialog(self._owner) == self._forms.DialogResult.OK:
                return str(self._dialog.FileName)
            return None
        finally:
            self._showing = False

    def cancel(self):
        if not self._showing:
            return True
        return _cancel_owned_popup(int(self._owner.Handle.ToInt64()), self._title)

    def dispose(self):
        self._dialog.Dispose()


@dataclass
class _Download:
    id: int
    operation: object
    args: object
    deferral: object
    handler: object = None
    action: object = None
    dialog: object = None
    state: str = 'InProgress'
    failure_code: str | None = None
    cancel_requested: bool = False
    settled: bool = False
    format_kind: str = 'unrecognized'

    def receipt(self):
        return {'id': self.id, 'state': self.state, 'settled': self.settled,
                'cancel_requested': self.cancel_requested, 'failure_code': self.failure_code}


class DownloadAdapter:
    def __init__(self, window, base_url):
        self._window = window
        self._urls = UrlPolicy(base_url)
        self._condition = threading.Condition(threading.RLock())
        self._records = deque()
        self._control = None
        self._core = None
        self._original = None
        self._closing = False
        self._close_cancel_started = False
        self._ui_thread_id = None
        self._actions = []
        self._trace_entries = deque(maxlen=DIAGNOSTIC_TRACE_LIMIT)
        self._trace_count = 0
        self._trace_dropped = 0
        self._permission_values = {'status': 'pending', 'seen': 0, 'read_failures': 0, 'dropped': 0}
        self._permission_events = deque(maxlen=PERMISSION_TRACE_LIMIT)
        self._permission_handler = self._on_permission_requested
        self._permission_attach_attempted = False
        self._permission_subscribed = False
        self._permission_detach_attempted = False
        self._values = {'status': 'attached', 'last_id': 0, 'last_state': None,
                        'accepted': 0, 'blocked': 0, 'completed': 0, 'interrupted': 0,
                        'failures': 0, 'failure_code': None, 'shutdown_confirmed': False}
        self._handlers = (self._before_show, self._on_initialized, self._on_download_starting)

    def snapshot(self):
        with self._condition:
            values = {**self._values, 'active': sum(not item.settled for item in self._records),
                      'trace_count': self._trace_count, 'trace_dropped': self._trace_dropped,
                      'trace': [dict(item) for item in self._trace_entries]}
            for key in DIAGNOSTIC_COUNTERS:
                values[key] = min(DIAGNOSTIC_LIMIT, values[key])
            values['permission_observer'] = {**self._permission_values,
                'events': [dict(item) for item in self._permission_events]}
            return values

    def _subscribe_permissions_on_ui(self):
        """Observe only; never choose a permission, defer it, or show native UI."""
        if self._permission_attach_attempted or self._closing:
            return
        self._permission_attach_attempted = True
        try:
            if threading.get_ident() != self._ui_thread_id:
                raise DownloadError('Permission observation must attach on the owned UI thread.')
            self._core.PermissionRequested += self._permission_handler
            self._permission_subscribed = True
            with self._condition:
                self._permission_values['status'] = 'observing'
        except Exception:
            # Diagnostics must not change browser permission policy or download
            # behavior when observation is unavailable. Never retain error text.
            with self._condition:
                self._permission_values['status'] = 'subscribe_failed'

    def _detach_permissions_on_ui(self):
        if not self._permission_subscribed or self._permission_detach_attempted:
            return
        self._permission_detach_attempted = True
        try:
            if threading.get_ident() != self._ui_thread_id:
                raise DownloadError('Permission observation must detach on the owned UI thread.')
            self._core.PermissionRequested -= self._permission_handler
            self._permission_subscribed = False
            with self._condition:
                self._permission_values['status'] = 'detached'
        except Exception:
            with self._condition:
                self._permission_values['status'] = 'detach_failed'

    def _on_permission_requested(self, sender, args):
        with self._condition:
            if self._closing or self._permission_values['status'] != 'observing':
                return
            self._permission_values['seen'] = min(DIAGNOSTIC_LIMIT, self._permission_values['seen'] + 1)
            try:
                if threading.get_ident() != self._ui_thread_id:
                    raise DownloadError('Permission arguments require the owned UI thread.')
                kind = _PERMISSION_KINDS.get(str(args.PermissionKind), -1)
                user = args.IsUserInitiated
                state = str(args.State)
                uri = str(args.Uri)
                if type(user) is not bool:
                    raise DownloadError('The permission gesture flag is not a boolean.')
                row = {'sequence': self._permission_values['seen'], 'download_sequence': self._trace_count,
                    'kind': kind, 'is_user_initiated': user,
                    'state': state if state in PERMISSION_STATES else 'Unrecognized',
                    'origin_matches': len(uri) <= 4096 and self._urls.allows_navigation(uri)}
            except Exception:
                self._permission_values['read_failures'] = min(DIAGNOSTIC_LIMIT, self._permission_values['read_failures'] + 1)
                return
            if len(self._permission_events) == PERMISSION_TRACE_LIMIT:
                self._permission_values['dropped'] = min(DIAGNOSTIC_LIMIT, self._permission_values['dropped'] + 1)
            self._permission_events.append(row)

    def _trace(self, event, record=None, *, reason=None, format_kind='unrecognized'):
        """Copy only cached Python state; never read native properties for diagnostics."""
        with self._condition:
            if event not in DIAGNOSTIC_EVENTS:
                return
            self._trace_count = min(DIAGNOSTIC_LIMIT, self._trace_count + 1)
            if len(self._trace_entries) == DIAGNOSTIC_TRACE_LIMIT:
                self._trace_dropped = min(DIAGNOSTIC_LIMIT, self._trace_dropped + 1)
            kind = record.format_kind if record is not None else format_kind
            self._trace_entries.append({'sequence': self._trace_count, 'event': event,
                'format': kind if kind in DIAGNOSTIC_FORMATS else 'unrecognized',
                'download_id': min(DIAGNOSTIC_LIMIT, record.id) if record is not None else 0,
                'reason': reason if reason in DIAGNOSTIC_REASONS else None,
                'active': min(32, sum(not item.settled for item in self._records)),
                'state': record.state if record is not None and record.state in ('InProgress', 'Completed', 'Interrupted', 'Unknown') else None,
                'args_pending': record is not None and record.args is not None,
                'dialog_pending': record is not None and record.dialog is not None,
                'deferral_pending': record is not None and record.deferral is not None})

    def _fail(self, code):
        with self._condition:
            code = code if code in DIAGNOSTIC_FAILURES else 'unclassified'
            self._values.update(failure_code=code)
            self._values['failures'] += 1
            self._trace('failure', reason=code)
            self._condition.notify_all()

    def _before_show(self):
        if self._values['status'] != 'attached':
            return
        try:
            self._ui_thread_id = threading.get_ident()
            native = self._window.native
            self._control = native.webview
            if native.browser.webview is not self._control or self._control.CoreWebView2 is not None:
                raise DownloadError('Downloads must attach before native initialization.')
            self._original = native.browser.on_download_starting
            # The vendor ready callback has not subscribed it yet. Assignment is
            # local to this browser object and leaves every other window alone.
            native.browser.on_download_starting = self._on_download_starting
            self._control.CoreWebView2InitializationCompleted += self._on_initialized
            self._values['status'] = 'armed'
        except Exception:
            self._values['status'] = 'failed'
            self._fail('download_setup_failed')
            # Do not leave the vendor download handler active after partial setup.
            if self._control is not None:
                try:
                    self._control.Dispose()
                except Exception:
                    pass

    def _on_initialized(self, sender, args):
        if not args.IsSuccess:
            self._values['status'] = 'failed'
            self._fail('download_initialization_failed')
            return
        self._core = sender.CoreWebView2
        # Policy posts the vendor-ready callback before this action. Do not try
        # to unsubscribe the replaced original: pythonnet raises ValueError for
        # a callback that was never registered, unlike a C# event subtraction.
        def finish():
            try:
                if self._window.native.browser.on_download_starting != self._on_download_starting:
                    raise DownloadError('The owned download callback changed.')
                self._subscribe_permissions_on_ui()
                self._values['status'] = 'installed'
            except Exception:
                self._values['status'] = 'failed'
                self._fail('download_subscription_failed')
                self._control.Dispose()
        self._post(finish)

    def _post(self, callback):
        def run():
            try:
                callback()
            finally:
                self._actions.remove(action)
        action = _ui_action(run)
        self._actions.append(action)
        try:
            self._control.BeginInvoke(action)
        except Exception:
            self._actions.remove(action)
            raise

    def _allows(self, operation):
        uri = str(operation.Uri)
        # Reuse the strict origin/UUID checks, but never accept data URLs or HTTP.
        return (len(uri) <= 256 and uri.startswith('blob:') and
                self._urls.allows_resource(uri, 'Image') and _format(str(operation.MimeType)))

    def _cancel_args(self, args):
        try:
            args.Handled = True
            args.Cancel = True
            return True
        except Exception:
            # Without a trustworthy Cancel setter, completing the event could
            # save to the browser's default path without native authorization.
            self._fail('download_rejection_failed')
            try:
                self._control.Dispose()
            except Exception:
                pass
            return False

    def _on_download_starting(self, sender, args):
        record = None
        self._trace('start')
        try:
            args.Handled = True
            operation = args.DownloadOperation
            format_info = self._allows(operation)
            format_kind = {'.svg': 'svg', '.png': 'png', '.mol': 'mol', '.nivram': 'json', '.csv': 'csv'}.get(format_info[0][0], 'unrecognized') if format_info else 'unrecognized'
            with self._condition:
                if not self._closing and format_info:
                    # Completion can precede delivery of StateChanged. Refresh
                    # the bounded retained records on this native UI thread;
                    # _observe still requires the dialog and deferral to end.
                    for prior in tuple(self._records):
                        if not prior.settled:
                            self._observe(prior)
                if (self._closing or not format_info or
                        any(not item.settled for item in self._records)):
                    self._cancel_args(args)
                    self._values['blocked'] += 1
                    self._trace('blocked', reason='closing' if self._closing else 'format_or_origin' if not format_info else 'active_download', format_kind=format_kind)
                    self._condition.notify_all()
                    return
                suggested = _suggested_name(args.ResultFilePath, format_info[0])
                deferral = args.GetDeferral()
                self._values['last_id'] += 1
                record = _Download(self._values['last_id'], operation, args, deferral)
                record.format_kind = format_kind
                self._records.append(record)
                self._values['accepted'] += 1
                self._trace('admitted', record)
                while len(self._records) > 32 and self._records[0].settled:
                    self._records.popleft()
            record.handler = lambda _sender, _args: self._observe(record)
            operation.StateChanged += record.handler
            record.action = _ui_action(lambda: self._choose(record, suggested, format_info))
            self._trace('dispatch_requested', record)
            self._control.BeginInvoke(record.action)
            self._trace('dispatch_returned', record)
        except Exception:
            self._fail('download_start_failed')
            self._cancel_args(args)
            if record is not None:
                record.failure_code = 'download_start_failed'
                self._complete_deferral(record)
                self._observe(record)

    def _complete_deferral(self, record):
        deferral = record.deferral
        if deferral is None:
            return
        try:
            deferral.Complete()
        except Exception:
            record.failure_code = 'download_deferral_completion_failed'
            self._fail(record.failure_code)
        else:
            record.deferral = None
            record.args = None
            self._trace('deferral_completed', record)

    def _choose(self, record, suggested, format_info):
        self._trace('choose_entered', record)
        try:
            if record.args is None:
                return
            if self._closing or record.cancel_requested:
                self._cancel_args(record.args)
                record.cancel_requested = True
                return
            record.dialog = _SaveDialog(self._window.native, suggested, format_info)
            self._trace('dialog_show_entered', record)
            destination = record.dialog.choose()
            self._trace('dialog_returned', record, reason='selected' if destination is not None else 'cancelled')
            if self._closing or record.cancel_requested or destination is None:
                if record.args is not None:
                    self._cancel_args(record.args)
                record.cancel_requested = True
            elif not _valid_destination(destination, format_info[0]):
                self._cancel_args(record.args)
                record.failure_code = 'download_destination_rejected'
                self._fail(record.failure_code)
            elif record.args is not None:
                record.args.ResultFilePath = destination
        except Exception:
            record.failure_code = 'download_dialog_failed'
            self._fail(record.failure_code)
            if record.args is not None:
                self._cancel_args(record.args)
        finally:
            if record.dialog is not None:
                try:
                    record.dialog.dispose()
                except Exception:
                    self._fail('download_dialog_disposal_failed')
                record.dialog = None
            self._complete_deferral(record)
            record.action = None
            self._observe(record)

    def _observe(self, record):
        with self._condition:
            if record.settled or record.operation is None:
                return
            try:
                state = str(record.operation.State)
                previous_state = record.state
                record.state = state if state in ('InProgress', 'Completed', 'Interrupted') else 'Unknown'
                if previous_state != record.state:
                    self._trace('state_observed', record)
                self._values['last_state'] = record.state
                if record.state in ('Completed', 'Interrupted') and record.args is None and record.dialog is None:
                    # Interrupted downloads can automatically resume. Explicitly
                    # cancel this owned operation before releasing it.
                    if record.state == 'Interrupted' and not record.cancel_requested:
                        record.cancel_requested = True
                        record.operation.Cancel()
                    if record.settled:  # A synchronous native callback can settle it.
                        return
                    record.operation.StateChanged -= record.handler
                    record.operation = None
                    record.handler = None
                    record.settled = True
                    self._trace('settled', record)
                    self._values['completed' if record.state == 'Completed' else 'interrupted'] += 1
            except Exception:
                record.failure_code = 'download_state_unconfirmed'
                self._fail(record.failure_code)
            self._condition.notify_all()

    def _assert_worker(self):
        if self._control is not None and not self._control.InvokeRequired:
            raise DownloadError('Download waits must run on a worker, never on the native UI thread.')

    def wait_for_download(self, after_id=0, timeout_seconds=30):
        self._assert_worker()
        if isinstance(after_id, bool) or not isinstance(after_id, int) or after_id < 0:
            raise DownloadError('The prior download identity must be a nonnegative integer.')
        deadline = time.monotonic() + _timeout(timeout_seconds)
        with self._condition:
            while True:
                record = next((item for item in self._records if item.id > after_id), None)
                if record is not None and record.settled:
                    return record.receipt()
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._closing or self._values['status'] == 'failed':
                    return {**(record.receipt() if record else {'id': None, 'state': None, 'settled': False}),
                            'failure_code': 'download_wait_unconfirmed'}
                self._condition.wait(remaining)

    def _cancel_on_ui(self, identity=None):
        if self._closing:
            self._detach_permissions_on_ui()
        for record in tuple(self._records):
            if record.settled or (identity is not None and record.id != identity):
                continue
            try:
                record.cancel_requested = True
                if record.dialog is not None:
                    if not record.dialog.cancel():
                        record.failure_code = 'download_dialog_cancellation_unconfirmed'
                if record.args is not None:
                    self._cancel_args(record.args)
                    self._complete_deferral(record)
                if record.operation is not None and str(record.operation.State) != 'Completed':
                    record.operation.Cancel()
                self._observe(record)
            except Exception:
                record.failure_code = 'download_cancellation_unconfirmed'
                self._fail(record.failure_code)
        with self._condition:
            self._condition.notify_all()

    def _cancel_and_wait(self, identity, timeout_seconds):
        self._assert_worker()
        deadline = time.monotonic() + _timeout(timeout_seconds)
        if self._control is None:
            return not any(not item.settled for item in self._records)
        dispatched = threading.Event()
        def cancel():
            try:
                self._cancel_on_ui(identity)
            finally:
                dispatched.set()
        try:
            self._post(cancel)
        except Exception:
            self._fail('download_cancel_dispatch_failed')
            return False
        if not dispatched.wait(max(0, deadline-time.monotonic())):
            return False
        with self._condition:
            while any(not item.settled and (identity is None or item.id == identity) for item in self._records):
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def cancel(self, download_id, timeout_seconds=5):
        if isinstance(download_id, bool) or not isinstance(download_id, int) or download_id <= 0:
            raise DownloadError('A positive download identity is required.')
        return self._cancel_and_wait(download_id, timeout_seconds)

    def shutdown(self, timeout_seconds=5):
        self._assert_worker()
        _timeout(timeout_seconds)
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        confirmed = self._cancel_and_wait(None, timeout_seconds)
        with self._condition:
            self._values.update(status='closed' if confirmed else 'shutdown_unconfirmed',
                                shutdown_confirmed=confirmed)
        return self.snapshot()

    def begin_shutdown_on_ui(self):
        """Cancel owned downloads at actual FormClosed, before WebView disposal."""
        if self._ui_thread_id != threading.get_ident():
            raise DownloadError('Download cancellation must run on the owned UI thread.')
        with self._condition:
            if self._close_cancel_started:
                return
            self._close_cancel_started = True
            self._closing = True
            self._condition.notify_all()
        self._cancel_on_ui()

    def finish_shutdown(self, timeout_seconds=5):
        """Wait only on cached receipts after close; never dispatch to a dead control."""
        if self._ui_thread_id == threading.get_ident():
            raise DownloadError('Download waits must run on a worker, never on the native UI thread.')
        deadline = time.monotonic() + _timeout(timeout_seconds)
        with self._condition:
            if not self._close_cancel_started:
                self._values.update(status='shutdown_unconfirmed', shutdown_confirmed=False)
                return self.snapshot()
            while any(not item.settled for item in self._records):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            confirmed = not any(not item.settled for item in self._records)
            self._values.update(status='closed' if confirmed else 'shutdown_unconfirmed',
                                shutdown_confirmed=confirmed)
            return self.snapshot()


def attach_downloads(window, base_url):
    if getattr(window, '_nivram_downloads', None) is not None:
        raise DownloadError('This window already owns a download adapter.')
    adapter = DownloadAdapter(window, base_url)
    window._nivram_downloads = adapter
    window.events.before_show += adapter._before_show
    return adapter
