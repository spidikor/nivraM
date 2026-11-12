"""First-entry field diagnostics with fake COM/CLR, never a native UI/runtime."""

import contextlib
import io
import json
import sys
import types
import unittest
from unittest.mock import Mock, patch

from nivram import native_dialog_worker as worker
from nivram.native_dialog_progress import (
    LIMIT_SEQUENCE, MAX_LINE_BYTES, MAX_NORMAL_RECORDS, MAX_PROGRESS_BYTES,
    NORMAL_EVENTS, ProgressCapture, ProgressEmitter,
)
from test_native_batch_dialog_fixture import Clock, FakeWindows


NONCE = '0123456789abcdef' * 2
PRIVATE = 'PRIVATE_PATH_OR_PROVIDER_CAPTION'
FIELD_EVENTS = (
    'controls_enum_enter', 'controls_enum_returned',
    'uia_bootstrap_enter', 'uia_bootstrap_returned',
    'idok_fromhandle_enter', 'idok_fromhandle_returned',
    'idok_name_enter', 'idok_name_returned',
    'edit_fromhandle_enter', 'edit_fromhandle_returned',
    'edit_name_enter', 'edit_name_returned',
)


def events(data):
    return [line.split(b'|')[-1].decode('ascii') for line in data.splitlines()]


class SyntheticBackend:
    """Exact WindowsBackend type with real label/_element and fake OS modules."""
    def __init__(self, *, failure=None, edit_label=None):
        self.stream = io.BytesIO()
        self.emitter = ProgressEmitter(NONCE, self.stream)
        self.fake = FakeWindows(123)
        self.fake.add_folder_button()
        self.fake.labels[21] = edit_label or worker._mode_labels('folder')[0]
        self.backend = worker.WindowsBackend.__new__(worker.WindowsBackend)
        self.backend.parent_pid = 123
        self.backend.automation = None
        self.backend.com = None
        self.backend._progress = self.emitter
        self.backend._progress_emitted = set()
        for name in ('alive', 'pid', 'window', 'visible', 'enabled', 'class_name',
                     'owner', 'parent', 'control_id', 'windows', 'set_text',
                     'read_text', 'submit'):
            setattr(self.backend, name, getattr(self.fake, name))
        self.request = {'schemaVersion': 2, 'dialogMode': 'folder', 'parentPid': 123,
                        'parentCreated': 1, 'ownerHwnd': 10, 'nonce': NONCE,
                        'timeout': 1, 'targets': [PRIVATE]}
        self.fromhandle_calls = []
        self.name_calls = []
        self.references = []
        outer = self

        class UnconvertibleName:
            def __str__(self):
                raise OSError(PRIVATE)

        class Element:
            def __init__(self, hwnd):
                self.hwnd = hwnd

            @property
            def Current(self):
                return self

            @property
            def Name(self):
                outer.name_calls.append(self.hwnd)
                if failure == 'name':
                    raise OSError(PRIVATE)
                if failure == 'conversion':
                    return UnconvertibleName()
                return outer.fake.labels[self.hwnd]

        class AutomationElement:
            @staticmethod
            def FromHandle(hwnd):
                outer.fromhandle_calls.append(hwnd)
                if failure == 'fromhandle':
                    raise OSError(PRIVATE)
                return Element(hwnd)

        def reference(name):
            self.references.append(name)
            if failure == 'bootstrap':
                raise OSError(PRIVATE)

        self.com = types.SimpleNamespace(CoInitializeEx=Mock(return_value=0),
                                         CoUninitialize=Mock())
        self.modules = {name: types.ModuleType(name) for name in (
            'clr', 'System', 'System.Runtime', 'System.Runtime.InteropServices',
            'System.Windows', 'System.Windows.Automation',
        )}
        for name in ('System', 'System.Runtime', 'System.Windows'):
            self.modules[name].__path__ = []
        self.modules['clr'].AddReference = reference
        self.modules['System'].IntPtr = lambda value: value
        self.modules['System.Runtime.InteropServices'].RuntimeEnvironment = types.SimpleNamespace(
            GetRuntimeDirectory=lambda: PRIVATE)
        self.modules['System.Windows.Automation'].AutomationElement = AutomationElement

    @contextlib.contextmanager
    def enabled(self):
        with patch.dict(sys.modules, self.modules), \
                patch.object(worker.ctypes, 'WinDLL', return_value=self.com, create=True), \
                patch.object(worker, 'validate_request', side_effect=lambda value, **kwargs: value):
            yield self.backend

    def packet(self):
        capture = ProgressCapture(NONCE)
        capture.feed(self.stream.getvalue())
        return capture.freeze()


class NativeFieldProgressTests(unittest.TestCase):
    def test_initial_idok_label_uses_real_backend_context_once(self):
        fixture = SyntheticBackend()
        with fixture.enabled() as backend:
            for _ in range(2):
                self.assertEqual(worker._idok_label_kind(
                    backend, fixture.fake.controls, 20, fixture.request), 'folder')
        self.assertEqual(events(fixture.stream.getvalue()), [
            'uia_bootstrap_enter', 'uia_bootstrap_returned',
            'idok_fromhandle_enter', 'idok_fromhandle_returned',
            'idok_name_enter', 'idok_name_returned',
        ])
        self.assertEqual(fixture.fromhandle_calls, [31, 31])
        self.assertEqual(fixture.name_calls, [31, 31])
        self.assertEqual(len(fixture.references), 2)
        self.assertEqual(fixture.fake.writes + fixture.fake.commands, [])
        self.assertEqual(fixture.packet()['last_event'], 'idok_name_returned')

    def test_initial_identification_separates_control_idok_and_edit_boundaries(self):
        fixture = SyntheticBackend(edit_label=PRIVATE)
        clock = Clock()
        with fixture.enabled() as backend:
            result = worker.select_fixture(fixture.request, backend, clock=clock,
                                           sleep=clock.sleep, progress=fixture.emitter)
        self.assertFalse(result['selected'])
        observed = events(fixture.stream.getvalue())
        self.assertEqual([event for event in observed if event in FIELD_EVENTS], list(FIELD_EVENTS))
        self.assertTrue(all(observed.count(event) == 1 for event in FIELD_EVENTS))
        self.assertGreater(fixture.name_calls.count(21), 1, 'Polling must still query fresh labels.')
        self.assertEqual(fixture.fake.writes + fixture.fake.commands, [])
        self.assertNotIn(PRIVATE.encode(), fixture.stream.getvalue())
        self.assertEqual(fixture.packet()['status'], 'valid')
        self.assertNotIn(NONCE, json.dumps(fixture.packet()))

    def test_control_enumeration_failure_has_enter_without_return(self):
        fixture = SyntheticBackend()
        original_windows = fixture.fake.windows

        def windows(parent=None):
            if parent is not None:
                raise OSError(PRIVATE)
            return original_windows()

        fixture.backend.windows = windows
        clock = Clock()
        with fixture.enabled() as backend:
            result = worker.select_fixture(fixture.request, backend, clock=clock,
                                           sleep=clock.sleep, progress=fixture.emitter)
        self.assertFalse(result['selected'])
        self.assertEqual(result['reason'], 'native_operation_failed')
        observed = events(fixture.stream.getvalue())
        self.assertIn('controls_enum_enter', observed)
        self.assertNotIn('controls_enum_returned', observed)
        self.assertEqual(fixture.packet()['last_event'], 'controls_enum_enter')
        self.assertEqual(fixture.fromhandle_calls + fixture.name_calls, [])
        self.assertNotIn(PRIVATE.encode(), fixture.stream.getvalue())

    def test_bootstrap_failure_does_not_report_bootstrap_completion(self):
        fixture = SyntheticBackend(failure='bootstrap')
        with fixture.enabled() as backend:
            self.assertEqual(worker._idok_label_kind(
                backend, fixture.fake.controls, 20, fixture.request), 'unavailable')
        self.assertEqual(events(fixture.stream.getvalue()), ['uia_bootstrap_enter'])
        self.assertEqual(fixture.packet()['last_event'], 'uia_bootstrap_enter')
        self.assertEqual(fixture.fromhandle_calls + fixture.name_calls, [])
        self.assertNotIn(PRIVATE.encode(), fixture.stream.getvalue())

    def test_fromhandle_failure_does_not_invent_name_read_or_return(self):
        fixture = SyntheticBackend(failure='fromhandle')
        with fixture.enabled() as backend:
            self.assertEqual(worker._idok_label_kind(
                backend, fixture.fake.controls, 20, fixture.request), 'unavailable')
        self.assertEqual(events(fixture.stream.getvalue()), [
            'uia_bootstrap_enter', 'uia_bootstrap_returned', 'idok_fromhandle_enter',
        ])
        self.assertEqual(fixture.name_calls, [])
        self.assertEqual(fixture.packet()['last_event'], 'idok_fromhandle_enter')
        self.assertNotIn(PRIVATE.encode(), fixture.stream.getvalue())

    def test_name_failure_preserves_fromhandle_return_without_name_return(self):
        fixture = SyntheticBackend(failure='name')
        with fixture.enabled() as backend:
            self.assertEqual(worker._idok_label_kind(
                backend, fixture.fake.controls, 20, fixture.request), 'unavailable')
        self.assertEqual(events(fixture.stream.getvalue()), [
            'uia_bootstrap_enter', 'uia_bootstrap_returned',
            'idok_fromhandle_enter', 'idok_fromhandle_returned', 'idok_name_enter',
        ])
        self.assertEqual(fixture.name_calls, [31])
        self.assertEqual(fixture.packet()['last_event'], 'idok_name_enter')
        self.assertNotIn(PRIVATE.encode(), fixture.stream.getvalue())

    def test_name_conversion_failure_does_not_emit_returned_for_either_context(self):
        for context, hwnd in (('idok', 31), ('edit', 21)):
            with self.subTest(context=context):
                fixture = SyntheticBackend(failure='conversion')
                with fixture.enabled() as backend:
                    with self.assertRaises(OSError):
                        backend.label(hwnd, diagnostic_context=context)
                self.assertEqual(events(fixture.stream.getvalue()), [
                    'uia_bootstrap_enter', 'uia_bootstrap_returned',
                    context + '_fromhandle_enter', context + '_fromhandle_returned',
                    context + '_name_enter',
                ])
                self.assertEqual(fixture.name_calls, [hwnd])
                self.assertEqual(fixture.packet()['last_event'], context + '_name_enter')
                self.assertNotIn(PRIVATE.encode(), fixture.stream.getvalue())

    def test_mutable_guards_use_default_context_without_telemetry(self):
        fixture = SyntheticBackend()
        with fixture.enabled() as backend:
            self.assertTrue(worker._mode_field_valid(backend, 21, 20, fixture.request, 'folder'))
            self.assertEqual(worker._folder_idok(backend, 20, fixture.request), (20, 31))
        self.assertEqual(fixture.stream.getvalue(), b'')
        self.assertEqual(fixture.name_calls, [21, 31])
        self.assertEqual(len(fixture.references), 2)
        self.assertEqual(fixture.fake.writes + fixture.fake.commands, [])

    def test_legacy_fake_backend_keeps_one_argument_label_contract(self):
        backend = FakeWindows(123)
        backend.add_folder_button()
        backend.labels[21] = worker._mode_labels('folder')[0]
        request = SyntheticBackend().request
        self.assertEqual(worker._idok_label_kind(backend, backend.controls, 20, request), 'folder')
        clock = Clock()
        stream = io.BytesIO()
        emitter = ProgressEmitter(NONCE, stream)
        with patch.object(worker, 'validate_request', side_effect=lambda value, **kwargs: value):
            result = worker.select_fixture(request, backend, clock=clock, sleep=clock.sleep,
                                           progress=emitter)
        self.assertTrue(result['selected'])
        self.assertEqual(backend.invocations, [(20, 31)])
        observed = events(stream.getvalue())
        self.assertNotIn('uia_bootstrap_enter', observed)
        self.assertNotIn('idok_name_enter', observed)

    def test_invalid_context_rejects_without_runtime_or_custom_equality(self):
        hooks = []

        class HostileString(str):
            def __eq__(self, other):
                hooks.append('equality')
                raise AssertionError(PRIVATE)

        fixture = SyntheticBackend()
        with fixture.enabled() as backend:
            for context in ('other', 1, {}, HostileString('idok')):
                with self.assertRaises((TypeError, ValueError)):
                    backend.label(31, diagnostic_context=context)
        self.assertEqual(hooks + fixture.references + fixture.fromhandle_calls + fixture.name_calls, [])
        self.assertEqual(fixture.stream.getvalue(), b'')

    def test_new_events_fit_unchanged_record_and_byte_caps(self):
        self.assertTrue(set(FIELD_EVENTS) <= NORMAL_EVENTS)
        self.assertEqual(len(NORMAL_EVENTS), 31)
        self.assertEqual((MAX_LINE_BYTES, MAX_NORMAL_RECORDS, LIMIT_SEQUENCE, MAX_PROGRESS_BYTES),
                         (96, 31, 32, 4096))
        stream = io.BytesIO()
        emitter = ProgressEmitter(NONCE, stream)
        for event in sorted(NORMAL_EVENTS):
            emitter.emit(event)
        emitter.emit('controls_enum_enter')
        data = stream.getvalue()
        self.assertEqual(len(data.splitlines()), 32)
        self.assertLessEqual(len(data), 3072)
        self.assertTrue(all(len(line) <= 96 for line in data.splitlines(keepends=True)))
        capture = ProgressCapture(NONCE)
        capture.feed(data)
        packet = capture.freeze()
        self.assertEqual(packet['status'], 'capped')
        self.assertEqual(packet['sequence'], 32)
        self.assertNotIn(NONCE, json.dumps(packet))


if __name__ == '__main__':
    unittest.main()
