"""Internal, synthetic-only dialog worker. UI Automation never runs in the app."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

from .native_dialog_progress import ProgressEmitter

MAX_INPUT = 4096
MAX_OUTPUT = 8192
MAX_CONTROLS = 256
SCOPE = 'exact parent process and its owned dialog only'
FIELDS = {'schemaVersion', 'parentPid', 'parentCreated', 'ownerHwnd', 'root',
          'rootDevice', 'rootInode', 'target', 'timeout', 'nonce'}
MULTI_FIELDS = (FIELDS - {'target'}) | {'dialogMode', 'targets', 'targetIdentities'}
CANCEL_FIELDS = MULTI_FIELDS | {'dialogAction'}
SELECTION_MODES = frozenset({'file', 'open-multiple', 'folder', 'save'})
OPEN_EXTENSIONS = frozenset({'.nivram', '.mol'})


def _fixture_paths(root, target, *, kind='file'):
    """Inspect metadata only; restrict targets to direct synthetic fixtures."""
    original_target = str(target)
    root, target = Path(root), Path(target)
    if not root.is_absolute() or not target.is_absolute():
        raise ValueError('Native fixture paths must be absolute.')
    if (root != root.resolve(strict=True) or target != target.resolve(strict=False)
            or target.parent != root / 'synthetic fixtures'
            or (kind == 'file' and target.suffix.lower() not in ('.nivram', '.png', '.json', '.csv'))
            or (kind == 'open-multiple' and target.suffix.lower() not in OPEN_EXTENSIONS)
            or len(str(target).encode('utf-16-le')) // 2 > 240
            or any(':' in part or part.endswith((' ', '.')) for part in target.parts[1:])
            or '\x00' in str(target)):
        raise ValueError('Native fixture target must stay in the isolated synthetic directory.')
    for item in (root, target.parent):
        info = item.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Native fixture directories must not be redirected.')
    lock = (root / 'instance.lock').stat(follow_symlinks=False)
    if not stat.S_ISREG(lock.st_mode) or getattr(lock, 'st_file_attributes', 0) & 0x400:
        raise ValueError('An isolated native acceptance profile is required.')
    if kind != 'file' and (str(target) != original_target or not os.path.lexists(target)
                           or any(c in str(target) for c in ('"', '\r', '\n'))):
        raise ValueError('An existing ordinary synthetic selection is required.')
    if os.path.lexists(target):
        info = target.stat(follow_symlinks=False)
        expected = stat.S_ISDIR if kind == 'folder' else stat.S_ISREG
        if not expected(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Native fixture targets must be ordinary selections.')
    return root, target


def _selection_paths(root, targets, mode):
    count = 2 if mode == 'open-multiple' else 1
    if (mode not in ('open-multiple', 'folder', 'save') or type(targets) is not list or len(targets) != count
            or any(type(value) is not str for value in targets)):
        raise ValueError('Invalid fixed native selection mode or count.')
    pairs = [_fixture_paths(root, target, kind='file' if mode == 'save' else mode) for target in targets]
    paths = [target for _, target in pairs]
    identities = []
    for target in paths:
        if mode == 'save' and (target.suffix.lower() != '.nivram' or os.path.lexists(target)):
            raise ValueError('Save cancellation requires an absent synthetic project name.')
        # No target is created for Cancel. Bind the ordinary fixture parent and
        # recheck target absence whenever the request identity is validated.
        info = (target.parent if mode == 'save' else target).stat(follow_symlinks=False)
        identities.append({'device': info.st_dev, 'inode': info.st_ino})
    if len(set(paths)) != count or len({(i['device'], i['inode']) for i in identities}) != count:
        raise ValueError('Native fixture selections must be distinct ordinary targets.')
    return pairs[0][0], paths, identities


def _kernel():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    return kernel


def _created(kernel, handle):
    values = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
        raise OSError('Native fixture parent identity could not be pinned.')
    return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime


def process_created(pid):
    kernel = _kernel()
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        raise OSError('Native fixture parent is unavailable.')
    try:
        return _created(kernel, handle)
    finally:
        kernel.CloseHandle(handle)


def build_request(window, target, allowed_root, timeout, *, nonce, mode='file', action='select'):
    if type(mode) is not str or mode not in SELECTION_MODES:
        raise ValueError('Invalid fixed native selection mode.')
    if type(action) is not str or action not in ('select', 'cancel') or (action == 'cancel' and mode == 'file'):
        raise ValueError('Intentional cancellation requires an explicit batch chooser mode.')
    if mode == 'save' and action != 'cancel':
        raise ValueError('The explicit Save mode only permits intentional cancellation.')
    if mode == 'file':
        root, target = _fixture_paths(allowed_root, target)
        selection = {'schemaVersion': 1, 'target': str(target)}
    else:
        targets = [str(p) for p in target] if mode == 'open-multiple' and type(target) in (list, tuple) else [str(target)]
        root, paths, identities = _selection_paths(allowed_root, targets, mode)
        selection = {'schemaVersion': 2, 'dialogMode': mode, 'targets': [str(p) for p in paths],
                     'targetIdentities': identities}
        if action == 'cancel':
            selection.update(schemaVersion=3, dialogAction='cancel')
    info = root.stat(follow_symlinks=False)
    request = {**selection, 'parentPid': os.getpid(),
               'parentCreated': process_created(os.getpid()),
               'ownerHwnd': int(window.native.Handle.ToInt64()),
               'root': str(root), 'rootDevice': info.st_dev, 'rootInode': info.st_ino,
               'timeout': timeout, 'nonce': nonce}
    return validate_request(request, expected_parent=os.getpid())


def validate_request(value, *, expected_parent):
    if (not isinstance(value, dict) or type(value.get('schemaVersion')) is not int
            or value.get('schemaVersion') not in (1, 2, 3)
            or set(value) != {1: FIELDS, 2: MULTI_FIELDS, 3: CANCEL_FIELDS}[value['schemaVersion']]
            or (value['schemaVersion'] == 3 and value.get('dialogAction') != 'cancel')
            or (value.get('dialogMode') == 'save' and value['schemaVersion'] != 3)
            or any(type(value[k]) is not int or not 0 < value[k] < 2**64
                   for k in ('parentPid', 'parentCreated', 'ownerHwnd'))
            or value['parentPid'] != expected_parent
            or any(type(value[k]) is not int or value[k] < 0 for k in ('rootDevice', 'rootInode'))
            or not isinstance(value['nonce'], str) or not re.fullmatch('[0-9a-f]{32}', value['nonce'])
            or not isinstance(value['root'], str)
            or (value['schemaVersion'] == 1 and not isinstance(value['target'], str))
            or isinstance(value['timeout'], bool) or not isinstance(value['timeout'], (int, float))
            or not math.isfinite(value['timeout']) or not 0 < value['timeout'] <= 35):
        raise ValueError('Invalid bounded native fixture request.')
    if value['schemaVersion'] == 1:
        root, _ = _fixture_paths(value['root'], value['target'])
    else:
        if type(value['dialogMode']) is not str:
            raise ValueError('Invalid fixed native selection mode.')
        root, _, identities = _selection_paths(value['root'], value['targets'], value['dialogMode'])
        pins = value['targetIdentities']
        if (type(pins) is not list or len(pins) != len(identities)
                or any(type(pin) is not dict or set(pin) != {'device', 'inode'}
                       or any(type(pin[key]) is not int or pin[key] < 0 for key in ('device', 'inode')) for pin in pins)
                or pins != identities):
            raise ValueError('Native fixture selection identity changed.')
    info = root.stat(follow_symlinks=False)
    if (info.st_dev, info.st_ino) != (value['rootDevice'], value['rootInode']):
        raise ValueError('Native fixture profile identity changed.')
    return dict(value)


from .native_exception_diagnostic import capture_exception_detail, validate_exception_detail


OPERATION_LOCATIONS = frozenset({
    'outside-set-filename', 'set-text', 'read-text', 'validate-target',
    'validate-field', 'validate-folder-route', 'validate-submit-owner',
    'prepare-invoke', 'validate-invoke', 'final-validate-target',
    'final-validate-owner', 'invoke', 'submit', 'confirm-close',
})
OPERATION_ERRORS = frozenset({'native-message', 'timeout', 'os', 'value', 'type', 'other'})


class NativeMessageFailure(RuntimeError):
    def __init__(self, code):
        super().__init__('Native filename message did not complete.')
        self.native_winerror = code


def operation_failure_diagnostic(value):
    if (type(value) is not dict or set(value) != {'operation', 'kind', 'winerror'}
            or type(value['operation']) is not str or value['operation'] not in OPERATION_LOCATIONS
            or type(value['kind']) is not str or value['kind'] not in OPERATION_ERRORS):
        return None
    code = value['winerror']
    if value['kind'] == 'native-message':
        if type(code) is not int or not 0 <= code <= 2**32 - 1:
            return None
    elif code is not None:
        return None
    return dict(value)


def validate_result(value, nonce, *, mode='file', action='select'):
    allowed = {'schemaVersion', 'nonce', 'selected', 'reason', 'filename_field_verified',
               'scope', 'stage', 'control_count', 'dialog_count', 'operation_failure', 'exception_detail'}
    if type(mode) is not str or mode not in SELECTION_MODES:
        raise ValueError('Invalid fixed native result mode.')
    if type(action) is not str or action not in ('select', 'cancel') or (action == 'cancel' and mode == 'file'):
        raise ValueError('Invalid native result action.')
    if mode == 'save' and action != 'cancel':
        raise ValueError('Save mode cannot acknowledge selection.')
    if action == 'cancel':
        allowed |= {'dialog_action', 'cancelled', 'cancel_posted'}
    if mode != 'file':
        allowed |= {'selection_mode', 'selection_count', 'dialog_close_confirmed', 'label_counts', 'idok_label_kind'}
    if (not isinstance(value, dict) or set(value) - allowed
            or value.get('schemaVersion') != (2 if action == 'cancel' else 1) or type(value.get('schemaVersion')) is not int
            or value.get('nonce') != nonce or type(value.get('selected')) is not bool
            or value.get('scope') != SCOPE
            or value.get('stage') not in (('validate', 'find-dialog', 'identify-field', 'set-filename', 'submit')
                                         + (('confirm-close',) if mode != 'file' else ()))
            or any(type(value.get(k)) is not int or not 0 <= value[k] <= MAX_CONTROLS
                   for k in ('control_count', 'dialog_count'))
            or value['selected'] and (value.get('filename_field_verified') is not True
                                     or value['stage'] != ('submit' if mode == 'file' else 'confirm-close')
                                     or value.get('reason') is not None)
            or not value['selected'] and (not isinstance(value.get('reason'), str)
                                         or not re.fullmatch('[a-z_]{1,64}', value['reason']))):
        raise ValueError('Invalid native fixture worker result.')
    if action == 'cancel':
        if (value.get('dialog_action') != 'cancel' or value['selected'] is not False
                or type(value.get('cancelled')) is not bool or type(value.get('cancel_posted')) is not bool
                or value.get('filename_field_verified') is not False
                or value.get('dialog_close_confirmed') is not value['cancelled']
                or (value['cancelled'] and (value['cancel_posted'] is not True
                    or value['stage'] != 'confirm-close' or value['reason'] != 'intentional_cancel'))
                or (not value['cancelled'] and value['reason'] == 'intentional_cancel')):
            raise ValueError('Invalid intentional native cancellation receipt.')
    if mode != 'file' and (value.get('selection_mode') != mode or
                           type(value.get('selection_count')) is not int or
                           value['selection_count'] != (2 if mode == 'open-multiple' else 1) or
                           type(value.get('dialog_close_confirmed')) is not bool or
                           (value['selected'] and value['dialog_close_confirmed'] is not True)):
        raise ValueError('Native fixture result selection mode changed.')
    if mode != 'file':
        has_counts, has_kind = 'label_counts' in value, 'idok_label_kind' in value
        if has_counts != has_kind:
            raise ValueError('Partial native label diagnostics are unsupported.')
        # Observational telemetry is optional for old schema2 receipts. Its
        # absence means unobserved, never positive evidence of a folder picker.
        if has_counts:
            if type(value['idok_label_kind']) is not str or value['idok_label_kind'] not in (
                    'folder', 'open', 'save', 'other', 'unavailable'):
                raise ValueError('Invalid fixed native submit-button category.')
            counts = value['label_counts']
            if (type(counts) is not dict or set(counts) != {'filename', 'folder', 'other'}
                    or any(type(number) is not int or not 0 <= number <= MAX_CONTROLS for number in counts.values())
                    or sum(counts.values()) > value['control_count']):
                raise ValueError('Invalid bounded native label counts.')
    if 'exception_detail' in value:
        if (value['selected'] or value['reason'] != 'native_operation_failed'
                or validate_exception_detail(value['exception_detail']) is None):
            raise ValueError('Invalid bounded native exception detail.')
    if 'operation_failure' in value:
        if (value['selected'] or value['reason'] != 'native_operation_failed'
                or operation_failure_diagnostic(value['operation_failure']) is None):
            raise ValueError('Invalid bounded native-operation diagnostic.')
    return {k: v for k, v in value.items() if k not in ('schemaVersion', 'nonce')}


class WindowsBackend:
    """Small Win32 surface; lazy UI Automation is used only by the child."""
    def __init__(self, request, *, progress=None):
        if progress is not None and type(progress) is not ProgressEmitter:
            raise ValueError('Invalid native progress emitter.')
        self._progress = progress
        self._progress_emitted = set()
        self.parent_pid = request['parentPid']
        self.kernel = _kernel()
        self.process = self.kernel.OpenProcess(0x1000 | 0x100000, False, request['parentPid'])
        self.automation = self.com = None
        if not self.process:
            raise OSError('Native fixture parent is unavailable.')
        try:
            if _created(self.kernel, self.process) != request['parentCreated']:
                raise ValueError('Native fixture parent identity changed.')
            self.user = ctypes.WinDLL('user32', use_last_error=True)
            self.callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            self.user.EnumWindows.argtypes = [self.callback, wintypes.LPARAM]
            self.user.EnumChildWindows.argtypes = [wintypes.HWND, self.callback, wintypes.LPARAM]
            self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            self.user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            for name in ('GetParent', 'IsWindow', 'IsWindowVisible', 'IsWindowEnabled', 'GetDlgCtrlID'):
                getattr(self.user, name).argtypes = [wintypes.HWND]
            self.user.GetParent.restype = wintypes.HWND
            self.user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
            self.user.GetWindow.restype = wintypes.HWND
            self.user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            self.user.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
            self.user.SendMessageTimeoutW.restype = ctypes.c_ssize_t
        except Exception:
            self.kernel.CloseHandle(self.process)
            self.process = None
            raise

    def close(self):
        if self.com is not None:
            self.com.CoUninitialize()
            self.com = None
        if self.process:
            self.kernel.CloseHandle(self.process)
            self.process = None

    def alive(self):
        return bool(self.process) and self.kernel.WaitForSingleObject(self.process, 0) == 0x102

    def pid(self, hwnd):
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def class_name(self, hwnd):
        text = ctypes.create_unicode_buffer(256)
        self.user.GetClassNameW(hwnd, text, len(text))
        return text.value

    def owner(self, hwnd):
        return self.user.GetWindow(hwnd, 4)

    def parent(self, hwnd):
        return self.user.GetParent(hwnd)

    def control_id(self, hwnd):
        return self.user.GetDlgCtrlID(hwnd)

    def visible(self, hwnd):
        return bool(self.user.IsWindowVisible(hwnd))

    def window(self, hwnd):
        return bool(self.user.IsWindow(hwnd))

    def enabled(self, hwnd):
        return bool(self.user.IsWindowEnabled(hwnd))

    def windows(self, parent=None):
        result = []
        visited = 0
        @self.callback
        def visit(hwnd, _):
            nonlocal visited
            visited += 1
            if self.pid(hwnd) == self.parent_pid:
                result.append(hwnd)
            return len(result) <= MAX_CONTROLS and visited <= 4096
        if parent is None:
            self.user.EnumWindows(visit, 0)
        else:
            self.user.EnumChildWindows(parent, visit, 0)
        if len(result) > MAX_CONTROLS or visited > 4096:
            raise ValueError('Native fixture control enumeration exceeded its bound.')
        return result

    def _progress_checkpoint(self, event):
        # Initial classification only. Repeated polls cannot consume the budget.
        if self._progress is not None and event not in self._progress_emitted:
            self._progress_emitted.add(event)
            ProgressEmitter.emit(self._progress, event)

    def _element(self, hwnd, *, diagnostic_context=None):
        if diagnostic_context is not None and (
                type(diagnostic_context) is not str or diagnostic_context not in ('idok', 'edit')):
            raise ValueError('Invalid native label diagnostic context.')
        if self.automation is None:
            if diagnostic_context is not None:
                self._progress_checkpoint('uia_bootstrap_enter')
            com = ctypes.WinDLL('ole32', use_last_error=True)
            com.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
            com.CoInitializeEx.restype = ctypes.c_long
            if com.CoInitializeEx(None, 0) not in (0, 1):  # MTA with no owned windows.
                raise OSError('Native fixture requires an MTA thread.')
            self.com = com
            import clr
            from System.Runtime.InteropServices import RuntimeEnvironment
            framework = Path(str(RuntimeEnvironment.GetRuntimeDirectory())) / 'WPF'
            clr.AddReference(str(framework / 'UIAutomationTypes.dll'))
            clr.AddReference(str(framework / 'UIAutomationClient.dll'))
            from System import IntPtr
            from System.Windows.Automation import AutomationElement
            self.automation = (IntPtr, AutomationElement)
            if diagnostic_context is not None:
                self._progress_checkpoint('uia_bootstrap_returned')
        IntPtr, AutomationElement = self.automation
        if diagnostic_context is not None:
            self._progress_checkpoint(diagnostic_context + '_fromhandle_enter')
        element = AutomationElement.FromHandle(IntPtr(hwnd))
        if diagnostic_context is not None:
            self._progress_checkpoint(diagnostic_context + '_fromhandle_returned')
        return element

    def label(self, hwnd, *, diagnostic_context=None):
        # Guard and submit-route reads retain the original uninstrumented path.
        if diagnostic_context is None:
            return str(self._element(hwnd).Current.Name).strip().lower()
        element = self._element(hwnd, diagnostic_context=diagnostic_context)
        self._progress_checkpoint(diagnostic_context + '_name_enter')
        name = str(element.Current.Name).strip().lower()
        self._progress_checkpoint(diagnostic_context + '_name_returned')
        return name

    def _message(self, hwnd, message, size, buffer, timeout_ms):
        result = ctypes.c_size_t()
        ctypes.set_last_error(0)
        success = self.user.SendMessageTimeoutW(hwnd, message, size, ctypes.addressof(buffer),
                    0x0001 | 0x0002 | 0x0020, timeout_ms, ctypes.byref(result))
        if not success:
            raise NativeMessageFailure(ctypes.get_last_error())
        return result.value

    def set_text(self, hwnd, value, timeout_ms):
        buffer = ctypes.create_unicode_buffer(value)
        if not self._message(hwnd, 0x000C, 0, buffer, timeout_ms):
            raise ValueError('Native filename field rejected its synthetic target.')

    def read_text(self, hwnd, timeout_ms):
        # At most two 240-unit absolute paths, quotes and one separator.
        buffer = ctypes.create_unicode_buffer(1025)
        self._message(hwnd, 0x000D, len(buffer), buffer, timeout_ms)
        return buffer.value

    def submit(self, hwnd, command):
        return bool(self.user.PostMessageW(hwnd, 0x0111, command, 0))

    @staticmethod
    def _invoke_identity(element):
        current = element.Current
        runtime = tuple(int(value) for value in element.GetRuntimeId())
        if not 0 < len(runtime) <= 32 or any(not -2**31 <= value < 2**31 for value in runtime):
            raise ValueError('The owned button accessibility identity is unsupported.')
        label = str(current.Name).strip().lower().replace('&', '')
        return (int(current.NativeWindowHandle), int(current.ProcessId), bool(current.IsEnabled),
                bool(current.IsOffscreen), label in ('select folder', 'choose folder',
                'select a folder', 'choose a folder'), runtime)

    def prepare_invoke(self, button):
        # Retrieve a pattern only from this exact HWND, never from a desktop search.
        # COM/provider calls remain in the existing bounded owned child process.
        element = self._element(button)
        identity = self._invoke_identity(element)
        if identity[:5] != (button, self.parent_pid, True, False, True):
            raise ValueError('The owned folder button accessibility identity changed.')
        from System.Windows.Automation import InvokePattern
        pattern = element.GetCurrentPattern(InvokePattern.Pattern)
        if not isinstance(pattern, InvokePattern):
            raise ValueError('The exact folder button has no supported Invoke pattern.')
        return element, pattern, identity

    def invoke_ready(self, prepared, button):
        identity = self._invoke_identity(prepared[0])
        return (identity == prepared[2]
                and identity[:5] == (button, self.parent_pid, True, False, True))

    def invoke_prepared(self, prepared):
        # Exactly one action, without SetFocus or synthetic global input. An
        # Invoke return is not success: the caller must still observe closure.
        prepared[1].Invoke()
        return True


def _owner_valid(backend, request):
    return backend.alive() and backend.pid(request['ownerHwnd']) == request['parentPid']


def _owned_dialog(backend, hwnd, request, *, require_visible=True):
    if (not _owner_valid(backend, request) or backend.pid(hwnd) != request['parentPid']
            or (require_visible and not backend.visible(hwnd)) or backend.class_name(hwnd) != '#32770'):
        return False
    ancestor = backend.owner(hwnd)
    for _ in range(8):
        if ancestor == request['ownerHwnd']:
            return True
        if not ancestor or backend.pid(ancestor) != request['parentPid']:
            return False
        ancestor = backend.owner(ancestor)
    return False


def _owned_field(backend, field, dialog, request):
    if (not _owned_dialog(backend, dialog, request) or backend.pid(field) != request['parentPid']
            or not backend.visible(field) or not backend.enabled(field) or backend.class_name(field) != 'Edit'):
        return False
    ancestor = backend.parent(field)
    for _ in range(12):
        if ancestor == dialog:
            return True
        if not ancestor or backend.pid(ancestor) != request['parentPid']:
            return False
        ancestor = backend.parent(ancestor)
    return False


def _mode_labels(mode):
    return (('folder:', 'folder', 'folder name:', 'folder name') if mode == 'folder'
            else ('file name:', 'file name', 'filename:', 'filename'))


def _mode_field_valid(backend, field, dialog, request, mode):
    if not _owned_field(backend, field, dialog, request):
        return False
    if mode == 'file':
        return True
    # UIA stays in the worker. Recheck ownership after the potentially slow read.
    return backend.label(field) in _mode_labels(mode) and _owned_field(backend, field, dialog, request)


def _owned_idok(backend, button, dialog, request):
    if (not _owned_dialog(backend, dialog, request) or not backend.enabled(dialog)
            or backend.pid(button) != request['parentPid'] or backend.control_id(button) != 1
            or not backend.visible(button) or not backend.enabled(button) or backend.class_name(button) != 'Button'):
        return False
    ancestor = backend.parent(button)
    for _ in range(12):
        if ancestor == dialog:
            return True
        if not ancestor or backend.pid(ancestor) != request['parentPid']:
            return False
        ancestor = backend.parent(ancestor)
    return False


def _idok_label_kind(backend, controls, dialog, request):
    """Classify one exact owned ID1 Button; never return its raw caption."""
    try:
        buttons = [hwnd for hwnd in controls if _owned_idok(backend, hwnd, dialog, request)]
        if len(buttons) != 1:
            return 'unavailable'
        button = buttons[0]
        if not _owned_idok(backend, button, dialog, request):
            return 'unavailable'
        label = (backend.label(button, diagnostic_context='idok') if type(backend) is WindowsBackend
                 else backend.label(button))
        if not _owned_idok(backend, button, dialog, request):
            return 'unavailable'
        normalized = label.replace('&', '').strip()
        if normalized in ('select folder', 'choose folder', 'select a folder', 'choose a folder'):
            return 'folder'
        if normalized == 'open':
            return 'open'
        if normalized == 'save':
            return 'save'
        return 'other'
    except Exception:
        return 'unavailable'


def _folder_idok(backend, dialog, request):
    """Pin one positive folder button and its actual owned parent."""
    if not _owned_dialog(backend, dialog, request):
        raise ValueError('The owned folder dialog changed.')
    buttons = [hwnd for hwnd in backend.windows(dialog) if _owned_idok(backend, hwnd, dialog, request)]
    if len(buttons) != 1:
        raise ValueError('A unique owned folder submit button is required.')
    button = buttons[0]
    parent = backend.parent(button)
    if (not _owned_idok(backend, button, dialog, request)
            or backend.pid(parent) != request['parentPid'] or not backend.visible(parent) or not backend.enabled(parent)):
        raise ValueError('The owned folder submit route changed.')
    label = backend.label(button).replace('&', '').strip()
    if (label not in ('select folder', 'choose folder', 'select a folder', 'choose a folder')
            or not _owned_idok(backend, button, dialog, request) or backend.parent(button) != parent
            or backend.pid(parent) != request['parentPid'] or not backend.visible(parent) or not backend.enabled(parent)):
        raise ValueError('The positive owned folder submit button changed.')
    return parent, button


def _confirm_dialog_closed(request, backend, dialog, deadline, *, clock, sleep):
    """An IDOK activation may merely navigate; require observed owned-dialog closure."""
    while clock() < deadline:
        if not _owner_valid(backend, request):
            return False, 'parent_or_owner_changed'
        try:
            validate_request(request, expected_parent=request['parentPid'])
        except (ValueError, OSError):
            return False, 'selection_identity_changed_during_close'
        if not backend.window(dialog):
            return True, None
        # A foreign/reparented replacement is not evidence that this dialog closed.
        if not _owned_dialog(backend, dialog, request, require_visible=False):
            return False, 'owned_dialog_changed_during_close'
        if not backend.visible(dialog):
            return True, None
        sleep(min(.05, max(0, deadline-clock())))
    return False, 'dialog_close_unconfirmed'


def select_fixture(request, backend, *, clock=time.monotonic, sleep=time.sleep, progress=None):
    if progress is not None and type(progress) is not ProgressEmitter:
        raise ValueError('Invalid native progress emitter.')
    if request.get('schemaVersion') == 3:
        from .native_dialog_cancel import cancel_fixture
        return cancel_fixture(request, backend, clock=clock, sleep=sleep, progress=progress)
    emitted = set()

    def checkpoint(event):
        # First entry only: polling cannot consume the diagnostic budget.
        # A checkpoint is an observation, never evidence of current location.
        if progress is not None and event not in emitted:
            emitted.add(event)
            ProgressEmitter.emit(progress, event)

    mode = request.get('dialogMode', 'file')
    result = {'schemaVersion': 1, 'nonce': request['nonce'], 'selected': False,
              'reason': 'supported_folder_control_not_found' if mode == 'folder' else 'supported_filename_control_not_found', 'scope': SCOPE,
              'stage': 'validate', 'control_count': 0, 'dialog_count': 0}
    if mode != 'file':
        result.update(selection_mode=mode, selection_count=2 if mode == 'open-multiple' else 1,
                      dialog_close_confirmed=False, label_counts={'filename': 0, 'folder': 0, 'other': 0},
                      idok_label_kind='unavailable')
    # Opt-in modes leave a small part of the existing parent cap for cancel and
    # receipt emission. The legacy single-file deadline remains unchanged.
    reserve = min(1, request['timeout'] * .1) if mode != 'file' else 0
    deadline = clock() + request['timeout'] - reserve
    active = active_field = None
    operation = 'outside-set-filename'
    try:
        while clock() < deadline:
            if not _owner_valid(backend, request):
                result['reason'] = 'parent_or_owner_changed'
                return result
            result['stage'] = 'find-dialog'
            checkpoint('find_dialog_enter')
            dialogs = [hwnd for hwnd in backend.windows() if _owned_dialog(backend, hwnd, request)]
            result['dialog_count'] = len(dialogs)
            if len(dialogs) > 1:
                result['reason'] = 'ambiguous_owned_dialog'
                return result
            if dialogs:
                dialog = dialogs[0]
                if mode == 'file':
                    active = dialog
                result['stage'] = 'identify-field'
                checkpoint('identify_field_enter')
                fields = []
                checkpoint('controls_enum_enter')
                controls = backend.windows(dialog)
                checkpoint('controls_enum_returned')
                result['control_count'] = len(controls)
                if mode != 'file':
                    result['label_counts'] = {'filename': 0, 'folder': 0, 'other': 0}
                    result['idok_label_kind'] = _idok_label_kind(backend, controls, dialog, request)
                for hwnd in controls:
                    if not _owned_field(backend, hwnd, dialog, request):
                        continue
                    # Keep COM calls outside native enumeration callbacks.
                    label = (backend.label(hwnd, diagnostic_context='edit') if type(backend) is WindowsBackend
                             else backend.label(hwnd))
                    if mode != 'file':
                        category = ('filename' if label in _mode_labels('open-multiple') else
                                    'folder' if label in _mode_labels('folder') else 'other')
                        result['label_counts'][category] += 1
                    if label in _mode_labels(mode):
                        fields.append(hwnd)
                        continue
                    # Opt-in modes require their explicit semantic label.
                    # Wrong-mode earlier dialogs must stay untouched.
                    if mode != 'file':
                        continue
                    ancestor = hwnd
                    for _ in range(5):
                        if backend.control_id(ancestor) in (1148, 1152):
                            fields.append(hwnd)
                            break
                        ancestor = backend.parent(ancestor)
                        if not ancestor or ancestor == dialog:
                            break
                if len(fields) > 1:
                    result['reason'] = 'ambiguous_filename_field'
                    return result
                if len(fields) == 1:
                    field = fields[0]
                    active, active_field = dialog, field
                    if mode != 'folder':
                        checkpoint('field_set_enter')
                    validate_request(request, expected_parent=request['parentPid'])
                    if not _mode_field_valid(backend, field, active, request, mode) or clock() >= deadline:
                        result['reason'] = 'parent_or_owner_changed'
                        return result
                    folder_route = None
                    if mode == 'folder':
                        checkpoint('folder_route_enter')
                        folder_route = _folder_idok(backend, active, request)
                        checkpoint('field_set_enter')
                        validate_request(request, expected_parent=request['parentPid'])
                        if not _mode_field_valid(backend, field, active, request, mode) or clock() >= deadline:
                            result['reason'] = 'parent_or_owner_changed'
                            return result
                        validate_request(request, expected_parent=request['parentPid'])
                        if (not _owned_field(backend, field, active, request)
                                or not _owned_idok(backend, folder_route[1], active, request)
                                or backend.parent(folder_route[1]) != folder_route[0]
                                or not backend.visible(folder_route[0]) or not backend.enabled(folder_route[0])
                                or clock() >= deadline):
                            result['reason'] = 'parent_or_owner_changed'
                            return result
                    result['stage'] = 'set-filename'
                    target_text = (request['target'] if mode == 'file' else request['targets'][0] if mode == 'folder'
                                   else ' '.join('"' + path + '"' for path in request['targets']))
                    operation = 'set-text'
                    backend.set_text(field, target_text, max(1, min(750, int((deadline-clock())*1000))))
                    checkpoint('field_readback_enter')
                    operation = 'read-text'
                    if backend.read_text(field, max(1, min(750, int((deadline-clock())*1000)))) != target_text:
                        result['reason'] = 'filename_readback_mismatch'
                        return result
                    operation = 'validate-target'
                    validate_request(request, expected_parent=request['parentPid'])
                    if not _mode_field_valid(backend, field, active, request, mode) or clock() >= deadline:
                        result['reason'] = 'parent_or_owner_changed'
                        return result
                    if mode == 'folder':
                        operation = 'validate-folder-route'
                        if _folder_idok(backend, active, request) != folder_route:
                            result['reason'] = 'folder_submit_route_changed'
                            return result
                        operation = 'validate-submit-owner'
                        validate_request(request, expected_parent=request['parentPid'])
                        if (not _mode_field_valid(backend, field, active, request, mode)
                                or not _owned_idok(backend, folder_route[1], active, request)
                                or backend.parent(folder_route[1]) != folder_route[0]
                                or not backend.visible(folder_route[0]) or not backend.enabled(folder_route[0])
                                or clock() >= deadline):
                            result['reason'] = 'parent_or_owner_changed'
                            return result
                        validate_request(request, expected_parent=request['parentPid'])
                        if (not _owned_field(backend, field, active, request)
                                or not _owned_idok(backend, folder_route[1], active, request)
                                or backend.parent(folder_route[1]) != folder_route[0]
                                or not backend.visible(folder_route[0]) or not backend.enabled(folder_route[0])
                                or clock() >= deadline):
                            result['reason'] = 'parent_or_owner_changed'
                            return result
                        checkpoint('invoke_prepare_enter')
                        operation = 'prepare-invoke'
                        prepared = backend.prepare_invoke(folder_route[1])
                        operation = 'validate-invoke'
                        if (not _mode_field_valid(backend, field, active, request, mode)
                                or not backend.invoke_ready(prepared, folder_route[1])):
                            result['reason'] = 'folder_submit_route_changed'
                            return result
                        # Pattern retrieval and UIA identity reads may be slow.
                        # Revalidate targets and fast HWND metadata afterwards;
                        # no other accessibility call precedes the one Invoke.
                        operation = 'final-validate-target'
                        checkpoint('invoke_final_checks')
                        validate_request(request, expected_parent=request['parentPid'])
                        if (not _owned_field(backend, field, active, request)
                                or not _owned_idok(backend, folder_route[1], active, request)
                                or backend.parent(folder_route[1]) != folder_route[0]
                                or not backend.visible(folder_route[0]) or not backend.enabled(folder_route[0])
                                or clock() >= deadline):
                            result['reason'] = 'parent_or_owner_changed'
                            return result
                        operation = 'invoke'
                        submitted = backend.invoke_prepared(prepared)
                    else:
                        operation = 'submit'
                        submitted = backend.submit(active, 1)
                    checkpoint('invoke_returned')
                    if not submitted:
                        result['reason'] = 'submission_failed'
                        return result
                    if mode != 'file':
                        result.update(filename_field_verified=True, stage='confirm-close')
                        checkpoint('confirm_close_enter')
                        operation = 'confirm-close'
                        closed, reason = _confirm_dialog_closed(request, backend, active, deadline,
                                                               clock=clock, sleep=sleep)
                        checkpoint('confirm_close_returned')
                        if not closed:
                            result['reason'] = reason
                            return result
                        result.update(selected=True, reason=None, dialog_close_confirmed=True)
                        return result
                    result.update(selected=True, reason=None, filename_field_verified=True, stage='submit')
                    return result
            sleep(min(.05, max(0, deadline-clock())))
        return result
    except Exception as error:
        result['reason'] = 'native_operation_failed'
        result['exception_detail'] = capture_exception_detail(error)
        kind = ('native-message' if isinstance(error, NativeMessageFailure) else
                'timeout' if isinstance(error, TimeoutError) else
                'os' if isinstance(error, OSError) else
                'value' if isinstance(error, ValueError) else
                'type' if isinstance(error, TypeError) else 'other')
        result['operation_failure'] = {'operation': operation, 'kind': kind,
            'winerror': error.native_winerror if isinstance(error, NativeMessageFailure) else None}
        return result
    finally:
        if active and not result['selected']:
            checkpoint('cancel_enter')
        if active and not result['selected'] and _owned_dialog(backend, active, request):
            if mode == 'file' or (active_field and _mode_field_valid(backend, active_field, active, request, mode)):
                backend.submit(active, 2)
                checkpoint('cancel_returned')


def cancel_owned_dialog(request):
    # Parent cleanup uses Win32 metadata only, never COM or UIA.
    # Metadata alone cannot distinguish an earlier OPEN from a pending FOLDER.
    # Only the child that positively matched a schema2 mode can cancel it.
    if request.get('schemaVersion') in (2, 3):
        return False
    if request['parentPid'] != os.getpid():
        return False
    backend = WindowsBackend(request)
    try:
        dialogs = [h for h in backend.windows() if _owned_dialog(backend, h, request)]
        return len(dialogs) == 1 and backend.submit(dialogs[0], 2)
    finally:
        backend.close()


def worker_main(*, progress=False):
    try:
        data = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(data) > MAX_INPUT:
            raise ValueError('Native fixture input limit exceeded.')
        decoded = json.loads(data)
        emitter = ProgressEmitter(decoded.get('nonce'), sys.stderr.buffer) if progress and type(decoded) is dict else None
        if emitter is not None:
            emitter.emit('request_validate_enter')
        request = validate_request(decoded, expected_parent=os.getppid())
        if emitter is not None:
            emitter.emit('request_validated')
            emitter.emit('backend_init_enter')
        backend = WindowsBackend(request, **({'progress': emitter} if emitter is not None else {}))
        if emitter is not None:
            emitter.emit('backend_ready')
        try:
            result = select_fixture(request, backend, **({'progress': emitter} if emitter is not None else {}))
        finally:
            if emitter is not None:
                emitter.emit('backend_close_enter')
            backend.close()
            if emitter is not None:
                emitter.emit('backend_closed')
        encoded = json.dumps(result, allow_nan=False).encode('utf-8')
        if len(encoded) > MAX_OUTPUT:
            raise ValueError('Native fixture output limit exceeded.')
        if emitter is not None:
            emitter.emit('result_emit_enter')
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        return 1  # No paths or arbitrary exception messages leave this process.


if __name__ == '__main__':
    raise SystemExit(worker_main(progress=sys.argv[1:] == ['--worker-progress'])
                     if sys.argv[1:] in (['--worker'], ['--worker-progress']) else 2)
