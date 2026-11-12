"""One bounded owned-WebView mouse gesture for synthetic acceptance.

This is not exposed through the application bridge. Only the fixed acceptance
buttons below are eligible. Windows message delivery alone never proves a trusted
browser click; the page must observe both isTrusted and active user activation.
No global input, pointer movement, focus changes or permission overrides occur.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import math
import os
import secrets
import threading
import time


_FAILURE_CODES = frozenset(['native_click_client_bounds_changed', 'native_click_client_bounds_invalid', 'native_click_control_unrecognized', 'native_click_coordinates_invalid', 'native_click_dom_target_changed', 'native_click_handles_invalid', 'native_click_kind_unrecognized', 'native_click_message_indeterminate', 'native_click_process_identity_changed', 'native_click_process_pin_failed', 'native_click_scale_unrecognized', 'native_click_trusted_activation_unconfirmed', 'native_click_trusted_event_unobserved', 'native_click_unclassified', 'native_click_window_ancestry_changed', 'native_click_window_identity_changed', 'native_click_windows_required'])
_FAILURE_CODES = _FAILURE_CODES | frozenset({
    'native_click_browser_pid_unavailable', 'native_click_browser_pid_indeterminate',
    'native_click_foreign_target_process', 'native_click_target_process_changed',
    'native_click_descendant_route_unavailable', 'native_click_descendant_depth_exceeded',
    'native_click_descendant_route_changed', 'native_click_coordinate_mapping_failed',
})


class NativeClickRejected(RuntimeError):
    def __init__(self, code):
        self.code = code if type(code) is str and code in _FAILURE_CODES else 'native_click_unclassified'
        super().__init__(self.code)


_SELECTORS = {
    'save-project': "document.getElementById('save-button')",
    'resize-style': "document.getElementById('apply-style')",
    'resize-fit': "document.getElementById('fit-button')",
    'resize-zoomout': "document.getElementById('zoom-out')",
    'resize-keyboard': "document.getElementById('keyboard-summary')",
    'resize-pan-right': "document.getElementById('keyboard-pan-right')",
    'edit-import': "document.getElementById('import-button')",
    'edit-load': "document.getElementById('load-structure')",
    'edit-rotate-cw': "document.getElementById('rotate-cw')",
    'edit-undo': "document.getElementById('undo-button')",
    'edit-redo': "document.getElementById('redo-button')",
    'edit-save-as': "document.getElementById('save-as-button')",
    'edit-open': "document.getElementById('open-button')",
    'recover-project': "document.getElementById('recovery-button')",
    'main': "document.getElementById('export-button')",
    'batch': "document.getElementById('batch-export-button')",
    'batch-cancel': "document.getElementById('batch-export-cancel')",
    'preview': "(() => {const d=document.getElementById('png-preview-dialog');"
               "if(!d?.open)return null;const a=[...d.querySelectorAll('button')]"
               ".filter(b=>b.textContent==='Save this PNG');return a.length===1?a[0]:null;})()",
}

_ARM = """(() => {
    const button=__BUTTON__, nonce=__NONCE__;
    if(!button?.isConnected || button.disabled)return null;
    button.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
    const r=button.getBoundingClientRect(), x=r.left+r.width/2, y=r.top+r.height/2;
    const v=[x,y,r.width,r.height,innerWidth,innerHeight];
    if(!v.every(Number.isFinite) || r.width<=0 || r.height<=0 ||
       x<0 || y<0 || x>=innerWidth || y>=innerHeight)return null;
    const hit=document.elementFromPoint(x,y);
    if(hit!==button && !button.contains(hit))return null;
    const old=window.__nivramAcceptanceClick;
    if(old?.button && old.listener)old.button.removeEventListener('click',old.listener,true);
    const state={nonce,button,seen:false,trusted:false,activation:false,x,y,
        viewportWidth:innerWidth,viewportHeight:innerHeight};
    state.listener=event=>{state.seen=true;state.trusted=event.isTrusted===true;
        state.activation=navigator.userActivation?.isActive===true;};
    button.addEventListener('click',state.listener,{capture:true,once:true});
    window.__nivramAcceptanceClick=state;
    return {x,y,viewportWidth:innerWidth,viewportHeight:innerHeight};
})()"""

_STABLE = """(() => {
    const s=window.__nivramAcceptanceClick;
    if(s?.nonce!==__NONCE__ || !s.button?.isConnected || s.button.disabled || s.seen)return false;
    const r=s.button.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;
    const hit=document.elementFromPoint(x,y);
    return r.width>0 && r.height>0 && Math.abs(x-s.x)<.1 && Math.abs(y-s.y)<.1 &&
        innerWidth===s.viewportWidth && innerHeight===s.viewportHeight &&
        (hit===s.button || s.button.contains(hit));
})()"""

_OBSERVE = """(() => {const s=window.__nivramAcceptanceClick;
    return s?.nonce===__NONCE__ && s.seen ?
        {trusted:s.trusted===true,activation:s.activation===true}:null;})()"""

_CLEAR = """(() => {const s=window.__nivramAcceptanceClick;if(s?.nonce===__NONCE__){
    s.button?.removeEventListener('click',s.listener,true);delete window.__nivramAcceptanceClick;
    }return true;})()"""


def _require(value, code):
    if not value:
        raise NativeClickRejected(code)


def _point(value, size):
    keys = {'x', 'y', 'viewportWidth', 'viewportHeight'}
    _require(type(value) is dict and set(value) == keys, 'native_click_coordinates_invalid')
    _require(all(type(value[key]) in (int, float) and math.isfinite(value[key]) for key in keys),
             'native_click_coordinates_invalid')
    width, height = size
    _require(type(width) is int and type(height) is int and 0 < width <= 32767 and 0 < height <= 32767,
             'native_click_client_bounds_invalid')
    vw, vh = value['viewportWidth'], value['viewportHeight']
    _require(0 < vw <= 32767 and 0 < vh <= 32767 and
             0 <= value['x'] < vw and 0 <= value['y'] < vh, 'native_click_coordinates_invalid')
    sx, sy = width / vw, height / vh
    _require(.25 <= sx <= 8 and .25 <= sy <= 8 and abs(sx-sy) <= max(.01, .01*max(sx, sy)),
             'native_click_scale_unrecognized')
    x, y = math.floor(value['x'] * sx), math.floor(value['y'] * sy)
    _require(0 <= x < width and 0 <= y < height, 'native_click_coordinates_invalid')
    return x, y


def _owned(backend, owner, control):
    _require(backend.pid == os.getpid() and backend.alive() and
             backend.creation() == backend.created, 'native_click_process_identity_changed')
    _require(type(owner) is int and type(control) is int and 0 < owner < 2**64 and 0 < control < 2**64 and
             owner != control, 'native_click_handles_invalid')
    _require(all(backend.window(hwnd) and backend.window_pid(hwnd) == backend.pid and
                 backend.visible(hwnd) and backend.enabled(hwnd) for hwnd in (owner, control)),
             'native_click_window_identity_changed')
    _require(backend.root(owner) == owner and backend.root(control) == owner and
             backend.child(owner, control), 'native_click_window_ancestry_changed')


def _inside(point, size):
    _require(type(point) is tuple and len(point) == 2 and all(type(n) is int for n in point),
             'native_click_coordinate_mapping_failed')
    width, height = size
    _require(type(width) is int and type(height) is int and 0 < width <= 32767 and 0 < height <= 32767,
             'native_click_client_bounds_invalid')
    _require(0 <= point[0] < width and 0 <= point[1] < height, 'native_click_coordinate_mapping_failed')


def _target_owned(backend, owner, control, target):
    _require(type(target) is int and 0 < target < 2**64 and target != control,
             'native_click_descendant_route_unavailable')
    _require(backend.window(target) and backend.visible(target) and backend.enabled(target),
             'native_click_window_identity_changed')
    _require(backend.root(target) == owner and backend.child(control, target),
             'native_click_window_ancestry_changed')
    pid = backend.window_pid(target)
    _require(pid in (backend.pid, backend.browser_pid), 'native_click_foreign_target_process')
    _require(backend.process_pinned(pid), 'native_click_target_process_changed')
    return pid


def _descendant_route(backend, owner, control, point):
    """Descend only the host's child at this fixed client point; never enumerate."""
    target, mapped, seen = control, point, {control}
    for depth in range(17):
        size = backend.client_size(target)
        _inside(mapped, size)
        child = backend.child_from_point(target, mapped)
        if child == 0 or child == target:
            pid = _target_owned(backend, owner, control, target)
            return target, mapped, size, depth, pid
        _require(depth < 16, 'native_click_descendant_depth_exceeded')
        _require(child not in seen, 'native_click_descendant_route_changed')
        _require(backend.child(target, child), 'native_click_window_ancestry_changed')
        _target_owned(backend, owner, control, child)
        mapped = backend.map_point(target, child, mapped)
        target = child
        seen.add(child)
    raise NativeClickRejected('native_click_descendant_depth_exceeded')


def _send_pair(backend, owner, control, coordinates):
    _owned(backend, owner, control)
    size = backend.client_size(control)
    point = _point(coordinates, size)
    route = _descendant_route(backend, owner, control, point)
    target, (x, y), target_size, depth, target_pid = route
    parameter = (y << 16) | x
    # Re-derive the exact point route and process pins before press AND release.
    for message, buttons in ((0x0201, 1), (0x0202, 0)):
        _owned(backend, owner, control)
        _require(backend.client_size(control) == size, 'native_click_client_bounds_changed')
        _require(_descendant_route(backend, owner, control, point) == route,
                 'native_click_descendant_route_changed')
        _target_owned(backend, owner, control, target)
        _require(backend.send(target, message, buttons, parameter, 1000), 'native_click_message_indeterminate')
        if message == 0x0201:
            time.sleep(.02)
    return {'clientX': x, 'clientY': y, 'clientWidth': target_size[0], 'clientHeight': target_size[1],
            'messageTimeoutMs': 1000, 'route': 'owned-webview-point-descendant', 'descendantDepth': depth,
            'sameProcessOwnership': target_pid == backend.pid, 'ownershipVerified': True,
            'sdkBrowserProcessPinned': target_pid == backend.browser_pid and target_pid != backend.pid}


def _browser_pid(control):
    """Read only the SDK browser PID on its UI thread, with a finite wait."""
    done, result = threading.Event(), {}

    def read():
        try:
            value = control.CoreWebView2.BrowserProcessId
            _require(not isinstance(value, bool), 'native_click_browser_pid_unavailable')
            pid = int(value)
            _require(0 < pid < 2**32 and pid == value, 'native_click_browser_pid_unavailable')
            result['pid'] = pid
        except Exception:
            result['failed'] = True
        finally:
            done.set()

    try:
        if control.InvokeRequired:
            from System import Action
            control.BeginInvoke(Action(read))
        else:
            read()
    except Exception:
        raise NativeClickRejected('native_click_browser_pid_unavailable') from None
    _require(done.wait(2), 'native_click_browser_pid_indeterminate')
    _require('pid' in result and 'failed' not in result, 'native_click_browser_pid_unavailable')
    return result['pid']


class _WindowsBackend:
    def __init__(self, browser_pid):
        _require(os.name == 'nt', 'native_click_windows_required')
        self.pid = os.getpid()
        _require(type(browser_pid) is int and 0 < browser_pid < 2**32,
                 'native_click_browser_pid_unavailable')
        self.browser_pid = browser_pid
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        self.u = ctypes.WinDLL('user32', use_last_error=True)
        self.k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.k.OpenProcess.restype = wintypes.HANDLE
        self.k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        self.k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.k.WaitForSingleObject.restype = wintypes.DWORD
        self.process = None
        self.browser_process = None
        self.process = self.k.OpenProcess(0x1000 | 0x100000, False, self.pid)
        _require(bool(self.process), 'native_click_process_pin_failed')
        try:
            self.created = self.creation()
            if browser_pid != self.pid:
                self.browser_process = self.k.OpenProcess(0x1000 | 0x100000, False, browser_pid)
                _require(bool(self.browser_process), 'native_click_process_pin_failed')
                self.browser_created = self._creation(self.browser_process)
                _require(self.process_pinned(browser_pid), 'native_click_target_process_changed')
            for name in ('IsWindow', 'IsWindowVisible', 'IsWindowEnabled'):
                getattr(self.u, name).argtypes = [wintypes.HWND]
                getattr(self.u, name).restype = wintypes.BOOL
            self.u.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
            self.u.IsChild.restype = wintypes.BOOL
            self.u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            self.u.GetAncestor.restype = wintypes.HWND
            self.u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            self.u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            self.u.GetClientRect.restype = wintypes.BOOL
            self.u.ChildWindowFromPointEx.argtypes = [wintypes.HWND, wintypes.POINT, wintypes.UINT]
            self.u.ChildWindowFromPointEx.restype = wintypes.HWND
            self.u.MapWindowPoints.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.POINTER(wintypes.POINT), wintypes.UINT]
            self.u.MapWindowPoints.restype = ctypes.c_int
            self.u.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
            self.u.SendMessageTimeoutW.restype = ctypes.c_ssize_t
        except BaseException:
            self.close()
            raise

    def creation(self):
        return self._creation(self.process)

    def _creation(self, process):
        values = [wintypes.FILETIME() for _ in range(4)]
        _require(self.k.GetProcessTimes(process, *(ctypes.byref(value) for value in values)),
                 'native_click_process_pin_failed')
        return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime

    def alive(self):
        return self.k.WaitForSingleObject(self.process, 0) == 258

    def process_pinned(self, pid):
        if pid == self.pid:
            return self.alive() and self.creation() == self.created
        if pid == self.browser_pid and self.browser_process:
            return self.k.WaitForSingleObject(self.browser_process, 0) == 258 and \
                self._creation(self.browser_process) == self.browser_created
        return False

    def window(self, hwnd):
        return bool(self.u.IsWindow(hwnd))

    def visible(self, hwnd):
        return bool(self.u.IsWindowVisible(hwnd))

    def enabled(self, hwnd):
        return bool(self.u.IsWindowEnabled(hwnd))

    def window_pid(self, hwnd):
        pid = wintypes.DWORD()
        _require(self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)), 'native_click_window_identity_changed')
        return pid.value

    def root(self, hwnd):
        return int(self.u.GetAncestor(hwnd, 2) or 0)

    def child(self, owner, control):
        return bool(self.u.IsChild(owner, control))

    def client_size(self, hwnd):
        rect = wintypes.RECT()
        _require(self.u.GetClientRect(hwnd, ctypes.byref(rect)), 'native_click_client_bounds_invalid')
        return rect.right - rect.left, rect.bottom - rect.top

    def child_from_point(self, hwnd, point):
        # Only immediate children; skip invisible, disabled and transparent.
        return int(self.u.ChildWindowFromPointEx(hwnd, wintypes.POINT(*point), 7) or 0)

    def map_point(self, source, target, point):
        mapped = wintypes.POINT(*point)
        ctypes.set_last_error(0)
        displacement = self.u.MapWindowPoints(source, target, ctypes.byref(mapped), 1)
        _require(displacement != 0 or ctypes.get_last_error() == 0, 'native_click_coordinate_mapping_failed')
        return mapped.x, mapped.y

    def send(self, hwnd, message, buttons, parameter, timeout):
        result = ctypes.c_size_t()
        # SMTO_BLOCK | SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT; no unbounded SendMessage.
        return bool(self.u.SendMessageTimeoutW(hwnd, message, buttons, parameter, 0x23, timeout, ctypes.byref(result)))

    def close(self):
        if self.browser_process:
            self.k.CloseHandle(self.browser_process)
            self.browser_process = None
        if self.process:
            self.k.CloseHandle(self.process)
            self.process = None


def click_png_button(window, evaluate, kind):
    """Send one click to this acceptance window; require a trusted DOM event.

    ``evaluate`` is the existing bounded host evaluator. Callers must start their
    exact-owned save-dialog fixture first, then require actual download/readback.
    This helper neither chooses a path nor changes native download permission.
    """
    _require(type(kind) is str and kind in _SELECTORS, 'native_click_kind_unrecognized')
    control = window.native.webview
    _require(control.GetType().FullName == 'Microsoft.Web.WebView2.WinForms.WebView2' and
             control.IsHandleCreated and not control.IsDisposed, 'native_click_control_unrecognized')
    owner = int(window.native.Handle.ToInt64())
    handle = int(control.Handle.ToInt64())
    nonce = json.dumps(secrets.token_hex(16))
    backend = _WindowsBackend(_browser_pid(control))
    try:
        _owned(backend, owner, handle)
        coordinates = evaluate(_ARM.replace('__BUTTON__', _SELECTORS[kind]).replace('__NONCE__', nonce),
                               timeout=2, stage='native-click-target')
        _require(evaluate(_STABLE.replace('__NONCE__', nonce), timeout=2, stage='native-click-target-stable') is True,
                 'native_click_dom_target_changed')
        metadata = _send_pair(backend, owner, handle, coordinates)
        # A real native save dialog may temporarily occupy the WebView UI thread.
        # Its already-started owned fixture resolves it; a 500 ms evaluation cap
        # would incorrectly poison the evaluator while that valid action settles.
        deadline = time.monotonic() + 10
        while (remaining := deadline - time.monotonic()) >= .25:
            observed = evaluate(_OBSERVE.replace('__NONCE__', nonce), timeout=min(10, remaining), stage='native-click-observation')
            if observed is not None:
                _require(type(observed) is dict and set(observed) == {'trusted', 'activation'} and
                         observed['trusted'] is True and observed['activation'] is True,
                         'native_click_trusted_activation_unconfirmed')
                return {'schemaVersion': 1, 'status': 'passed', 'kind': kind, 'isTrusted': True,
                        'userActivation': True, **metadata}
            time.sleep(.1)
        raise NativeClickRejected('native_click_trusted_event_unobserved')
    finally:
        backend.close()
        # The bounded evaluator rejects submission if it was poisoned by a
        # timeout. Cleanup cannot turn an indeterminate click into success.
        try:
            evaluate(_CLEAR.replace('__NONCE__', nonce), timeout=10, stage='native-click-listener-cleanup')
        except Exception:
            pass
