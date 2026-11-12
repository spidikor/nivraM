"""Fixed synthetic close-review controls, without replacing native decisions.

The pinned vendor creates an OK/Cancel MessageBox on the calling review thread,
without supplying an owner. Scope discovery to that exact thread in the pinned
current process, then require its fixed title, body, class and both controls.
No global input or unrelated-window enumeration is used.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import threading
import time

from .acceptance_click import _WindowsBackend

TITLE = 'Unsaved project'
MESSAGE = 'A recovery copy was saved. Close without saving this project to its chosen file?'


def require(value, code):
    if not value:
        raise AssertionError(code)


def validate_dialog(value, *, pid, thread, owner):
    require(type(value) is dict and value.get('pid') == pid and value.get('thread') == thread
        and value.get('class') == '#32770' and value.get('title') == TITLE
        and value.get('owner') in (0, owner) and value.get('visible') is True
        and value.get('enabled') is True and value.get('bodyMatched') is True,
        'close_confirmation_identity_mismatch')
    buttons = value.get('buttons')
    require(type(buttons) is list and len(buttons) == 2, 'close_confirmation_controls_missing')
    for button, identifier, text in zip(buttons, (1, 2), ('OK', 'Cancel')):
        require(type(button) is dict and button.get('id') == identifier
            and type(button.get('handle')) is int and button['handle'] > 0
            and button.get('parent') == value.get('handle') and button.get('pid') == pid
            and button.get('thread') == thread and button.get('class') == 'Button'
            and button.get('text') == text and button.get('visible') is True
            and button.get('enabled') is True, 'close_confirmation_control_mismatch')
    require(buttons[0]['handle'] != buttons[1]['handle'], 'close_confirmation_control_alias')


class Windows(_WindowsBackend):
    def __init__(self):
        super().__init__(os.getpid())
        self.callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        for name in ('EnumThreadWindows', 'EnumChildWindows'):
            getattr(self.u, name).restype = wintypes.BOOL
        self.u.EnumThreadWindows.argtypes = [wintypes.DWORD, self.callback, wintypes.LPARAM]
        self.u.EnumChildWindows.argtypes = [wintypes.HWND, self.callback, wintypes.LPARAM]
        self.u.GetParent.argtypes = [wintypes.HWND]
        self.u.GetParent.restype = wintypes.HWND
        self.u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        self.u.GetWindow.restype = wintypes.HWND
        self.u.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
        self.u.GetDlgItem.restype = wintypes.HWND
        self.u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.u.PostMessageW.restype = wintypes.BOOL
        self.k.GetCurrentThreadId.restype = wintypes.DWORD

    def thread(self, handle):
        pid = wintypes.DWORD()
        result = int(self.u.GetWindowThreadProcessId(handle, ctypes.byref(pid)))
        require(result > 0 and pid.value == self.pid, 'close_confirmation_foreign_window')
        return result

    def text(self, handle):
        buffer = ctypes.create_unicode_buffer(512)
        require(self.send(handle, 0x000D, len(buffer), ctypes.addressof(buffer), 250),
                'close_confirmation_text_unavailable')
        return buffer.value

    def class_name(self, handle):
        buffer = ctypes.create_unicode_buffer(128)
        require(self.u.GetClassNameW(handle, buffer, len(buffer)) > 0, 'close_confirmation_class_unavailable')
        return buffer.value

    def enumerate(self, thread=None, parent=None):
        handles = []
        def collect(handle, _):
            handles.append(int(handle))
            return len(handles) <= 32
        callback = self.callback(collect)
        if parent is None:
            self.u.EnumThreadWindows(thread, callback, 0)
        else:
            self.u.EnumChildWindows(parent, callback, 0)
        require(len(handles) <= 32, 'close_confirmation_window_bound_exceeded')
        return handles

    def snapshot_dialog(self, handle, thread):
        require(self.process_pinned(self.pid) and self.window(handle)
                and self.thread(handle) == thread, 'close_confirmation_process_changed')
        body = False
        for child in self.enumerate(parent=handle):
            require(self.window_pid(child) == self.pid and self.thread(child) == thread,
                    'close_confirmation_foreign_child')
            if self.class_name(child) == 'Static' and self.text(child) == MESSAGE:
                body = True
        buttons = []
        for identifier in (1, 2):
            button = int(self.u.GetDlgItem(handle, identifier) or 0)
            require(button and self.window(button), 'close_confirmation_controls_missing')
            buttons.append({'id': identifier, 'handle': button, 'parent': int(self.u.GetParent(button) or 0),
                'pid': self.window_pid(button), 'thread': self.thread(button), 'class': self.class_name(button),
                'text': self.text(button).replace('&', ''), 'visible': self.visible(button), 'enabled': self.enabled(button)})
        return {'handle': handle, 'pid': self.window_pid(handle), 'thread': self.thread(handle),
            'class': self.class_name(handle), 'title': self.text(handle),
            'owner': int(self.u.GetWindow(handle, 4) or 0), 'visible': self.visible(handle),
            'enabled': self.enabled(handle), 'bodyMatched': body, 'buttons': buttons}


def post_close(window, *, backend_factory=Windows):
    backend = backend_factory()
    try:
        owner = int(window.native.Handle.ToInt64())
        require(backend.process_pinned(backend.pid) and backend.window(owner)
            and backend.window_pid(owner) == backend.pid and backend.root(owner) == owner
            and backend.visible(owner) and backend.enabled(owner), 'close_request_owner_unconfirmed')
        require(backend.u.PostMessageW(owner, 0x0010, 0, 0), 'close_request_not_posted')
        return {'message': 'WM_CLOSE', 'ownedWindowVerified': True, 'posted': True}
    finally:
        backend.close()


class ConfirmationObserver:
    """One exact OK/Cancel interaction per real confirmation call, never a retry."""
    def __init__(self, window, decisions=(False, True), *, backend_factory=Windows):
        require(type(decisions) is tuple and decisions and all(type(x) is bool for x in decisions),
                'close_confirmation_decisions_invalid')
        self.window, self.decisions, self.backend_factory = window, decisions, backend_factory
        self.original = window.create_confirmation_dialog
        self.previous = window.__dict__.get('create_confirmation_dialog')
        self.lock = threading.Lock()
        self.rows = []
        self.active = 0
        self.attached = self.closing = False

        def observed(title, message):
            require(title == TITLE and message == MESSAGE, 'close_confirmation_prompt_unexpected')
            with self.lock:
                require(not self.closing and self.active == 0 and len(self.rows) < len(self.decisions),
                        'close_confirmation_call_unexpected')
                decision = self.decisions[len(self.rows)]
                row = {'decision': decision, 'originalCalled': False, 'originalReturned': False,
                       'actualReturnVerified': False, 'buttonPosted': False, 'workerExited': False}
                self.rows.append(row)
                self.active += 1
            stop = threading.Event()
            backend = worker = None
            try:
                backend = self.backend_factory()
                thread = int(backend.k.GetCurrentThreadId())
                owner = int(window.native.Handle.ToInt64())
                worker = threading.Thread(target=self._choose, args=(backend, thread, owner, decision, stop, row),
                                          name='nivraM-close-confirmation', daemon=True)
                worker.start()
                row['originalCalled'] = True
                result = self.original(title, message)
                row['originalReturned'] = True
                row['actualReturnVerified'] = type(result) is bool and result is decision
                require(row['actualReturnVerified'], 'close_confirmation_original_return_mismatch')
                return result
            finally:
                stop.set()
                if worker is not None and worker.ident is not None:
                    worker.join(2)
                row['workerExited'] = worker is None or not worker.is_alive()
                if backend is not None and row['workerExited']:
                    backend.close()
                with self.lock:
                    self.active -= 1
                require(row['workerExited'] and row['buttonPosted'] and not row.get('error'),
                        'close_confirmation_worker_unconfirmed')

        self.observed = observed

    @staticmethod
    def _choose(backend, thread, owner, decision, stop, row):
        deadline = time.monotonic() + 15
        try:
            while not stop.is_set() and time.monotonic() < deadline:
                candidates = [h for h in backend.enumerate(thread=thread)
                    if backend.window(h) and backend.window_pid(h) == backend.pid
                    and backend.visible(h) and backend.class_name(h) == '#32770']
                require(len(candidates) <= 1, 'close_confirmation_ambiguous')
                if not candidates:
                    stop.wait(.03)
                    continue
                first = backend.snapshot_dialog(candidates[0], thread)
                validate_dialog(first, pid=backend.pid, thread=thread, owner=owner)
                second = backend.snapshot_dialog(candidates[0], thread)
                validate_dialog(second, pid=backend.pid, thread=thread, owner=owner)
                require(first == second and backend.process_pinned(backend.pid), 'close_confirmation_changed')
                require(not stop.is_set() and time.monotonic() < deadline, 'close_confirmation_deadline')
                button = second['buttons'][0 if decision else 1]['handle']
                require(backend.u.PostMessageW(button, 0x00F5, 0, 0), 'close_confirmation_click_not_posted')
                row['buttonPosted'] = True
                return
            raise TimeoutError('The fixed native confirmation did not appear in time.')
        except Exception as error:
            row['error'] = type(error).__name__

    def attach(self):
        with self.lock:
            require(not self.attached and not self.closing, 'close_confirmation_already_attached')
            self.window.create_confirmation_dialog = self.observed
            self.attached = True

    def snapshot(self):
        with self.lock:
            return [dict(row) for row in self.rows]

    def assert_count(self, count):
        rows = self.snapshot()
        require(len(rows) == count and all(all(row.get(k) is True for k in (
            'originalCalled', 'originalReturned', 'actualReturnVerified', 'buttonPosted', 'workerExited'))
            and not row.get('error') for row in rows), 'close_confirmation_incomplete')

    def close(self):
        with self.lock:
            self.closing = True
            require(self.active == 0, 'close_confirmation_call_still_active')
            if self.attached:
                require(self.window.create_confirmation_dialog is self.observed, 'close_confirmation_observer_replaced')
                if self.previous is None:
                    delattr(self.window, 'create_confirmation_dialog')
                else:
                    self.window.create_confirmation_dialog = self.previous
                self.attached = False
            return {'shutdownConfirmed': True, 'methodsRestored': True}
