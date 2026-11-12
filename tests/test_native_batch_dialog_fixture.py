"""Fixed synthetic multi-open/folder policy; no native windows or child launch."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nivram import native_dialog_fixture as fixture
from nivram import native_dialog_worker as worker


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    def sleep(self, value): self.now += value


class FakeWindows:
    def __init__(self, pid):
        self.pids = {10: pid, 20: pid, 21: pid, 22: pid, 99: pid + 1}
        self.dialogs = [20, 99]
        self.controls = [21]
        self.parents = {20: 10, 21: 20, 22: 20}
        self.labels = {21: 'file name:', 22: 'file name:'}
        self.writes, self.commands, self.label_calls = [], [], []
        self.text = ''
        self.closed = False
        self.close_on_submit = True
        self.after_submit = None
        self.buttons = {}
        self.invocations = []
        self.pattern_fetches = []
    def alive(self): return True
    def pid(self, hwnd): return self.pids.get(hwnd, 0)
    def window(self, hwnd): return hwnd in self.pids and not (hwnd == 20 and self.closed)
    def visible(self, hwnd): return not (hwnd == 20 and self.closed)
    def enabled(self, hwnd): return True
    def class_name(self, hwnd):
        return 'Button' if hwnd in self.buttons else 'Edit' if hwnd in (21, 22) else '#32770'
    def owner(self, hwnd): return self.parents.get(hwnd)
    def parent(self, hwnd): return self.parents.get(hwnd)
    def control_id(self, hwnd): return 1 if hwnd in self.buttons else 0
    def windows(self, parent=None): return list(self.dialogs if parent is None else self.controls)
    def label(self, hwnd):
        self.label_calls.append(hwnd)
        return self.labels[hwnd]
    def set_text(self, hwnd, value, timeout_ms):
        self.writes.append((hwnd, value, timeout_ms)); self.text = value
    def read_text(self, hwnd, timeout_ms): return self.text
    def submit(self, hwnd, command):
        self.commands.append((hwnd, command))
        self._command_effect(command)
        return True
    def _command_effect(self, command):
        if command == 1:
            if self.close_on_submit:
                self.closed = True
            if self.after_submit:
                self.after_submit()
    def prepare_invoke(self, button):
        self.pattern_fetches.append(button)
        return button, self.pid(button)
    def invoke_ready(self, prepared, button):
        return prepared == (button, self.pid(button))
    def invoke_prepared(self, prepared):
        button, _ = prepared
        self.invocations.append((self.parent(button), button))
        self._command_effect(1)
        return True
    def add_folder_button(self, hwnd=31):
        self.buttons[hwnd] = True
        self.pids[hwnd] = self.pids[10]
        self.parents[hwnd] = 20
        self.labels[hwnd] = 'select folder'
        if hwnd not in self.controls:
            self.controls.append(hwnd)


class NativeBatchDialogFixtureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='nv-batch-dialog-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.directory = self.root / 'synthetic fixtures'
        self.directory.mkdir()
        (self.root / 'instance.lock').write_bytes(b'0')
        self.inputs = [self.directory / 'valid drawing ü.nivram', self.directory / 'malformed input β.mol']
        for path in self.inputs:
            path.write_text('synthetic only', encoding='utf-8')
        self.destination = self.directory / 'batch output ü'
        self.destination.mkdir()
        self.window = SimpleNamespace(native=SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda: 10)))
        self.clock = Clock()
        self.backend = FakeWindows(os.getpid())

    def request(self, mode='open-multiple', target=None):
        if mode == 'folder':
            self.backend.add_folder_button()
        selected = self.inputs if mode == 'open-multiple' else self.destination
        with patch.object(worker, 'process_created', return_value=123):
            return worker.build_request(self.window, selected if target is None else target,
                                        self.root, .2, nonce='a' * 32, mode=mode)

    def select(self, request):
        return worker.select_fixture(request, self.backend, clock=self.clock, sleep=self.clock.sleep)

    def test_default_single_file_request_shape_and_receipt_remain_compatible(self):
        with patch.object(worker, 'process_created', return_value=123):
            request = worker.build_request(self.window, self.inputs[0], self.root, .2, nonce='a' * 32)
        self.assertEqual(request['schemaVersion'], 1)
        self.assertEqual(set(request), worker.FIELDS)
        result = self.select(request)
        self.assertTrue(worker.validate_result(result, request['nonce'])['selected'])
        self.assertNotIn('selection_mode', result)
        self.assertEqual(self.backend.writes[0][1], str(self.inputs[0]))

    def test_two_inputs_use_exact_quoted_paths_and_mode_bound_receipt(self):
        request = self.request()
        self.assertEqual(request['schemaVersion'], 2)
        self.assertEqual(request['targets'], [str(path) for path in self.inputs])
        result = self.select(request)
        expected = ' '.join('"' + str(path) + '"' for path in self.inputs)
        self.assertEqual(self.backend.writes[0][1], expected)
        self.assertEqual(self.backend.commands, [(20, 1)])
        self.assertTrue(worker.validate_result(result, request['nonce'], mode='open-multiple')['selected'])
        self.assertEqual(result['selection_count'], 2)
        self.assertEqual(result['selection_mode'], 'open-multiple')
        self.assertIs(result['dialog_close_confirmed'], True)
        self.assertEqual(result['stage'], 'confirm-close')
        # This receipt never asserts what the native API subsequently returns.
        self.assertNotIn('returned_paths', result)
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_folder_uses_explicit_owned_folder_field_and_exact_existing_path(self):
        request = self.request('folder')
        self.backend.labels[21] = 'folder:'
        result = self.select(request)
        self.assertEqual(self.backend.writes[0][1], str(self.destination))
        self.assertEqual(self.backend.commands, [])
        self.assertTrue(worker.validate_result(result, request['nonce'], mode='folder')['selected'])
        self.assertEqual(result['selection_count'], 1)
        self.assertEqual(self.backend.invocations, [(20, 31)])

    def test_folder_never_guesses_filename_label_or_control_id(self):
        request = self.request('folder')
        self.backend.control_id = lambda _: 1152
        result = self.select(request)
        self.assertFalse(result['selected'])
        self.assertEqual(result['reason'], 'supported_folder_control_not_found')
        self.assertEqual(self.backend.writes, [])
        self.assertNotIn((20, 1), self.backend.commands)
        self.assertEqual(self.backend.commands, [])

    def test_multiple_folder_fields_fail_closed_before_any_write(self):
        request = self.request('folder')
        self.backend.controls = [21, 22]
        self.backend.labels = {21: 'folder:', 22: 'folder name:'}
        result = self.select(request)
        self.assertEqual(result['reason'], 'ambiguous_filename_field')
        self.assertEqual(self.backend.writes, [])
        self.assertEqual(self.backend.commands, [])

    def test_count_suffix_missing_file_and_duplicates_are_rejected(self):
        unwanted = self.directory / 'unexpected.smi'
        unwanted.write_text('C', encoding='utf-8')
        cases = ([self.inputs[0]], self.inputs + [self.inputs[0]],
                 [self.inputs[0], self.inputs[0]], [self.inputs[0], self.directory / 'missing.mol'],
                 [self.inputs[0], unwanted], [self.inputs[0], self.destination])
        for targets in cases:
            with self.subTest(targets=targets), self.assertRaises((ValueError, OSError)):
                self.request(target=targets)
        self.assertEqual(self.backend.writes + self.backend.commands, [])

    def test_foreign_nested_and_traversal_paths_are_rejected(self):
        outside = self.root / 'outside.mol'
        outside.write_text('synthetic only', encoding='utf-8')
        nested = self.directory / 'nested'
        nested.mkdir()
        (nested / 'input.mol').write_text('synthetic only', encoding='utf-8')
        cases = [outside, nested / 'input.mol', self.directory / '..' / 'outside.mol']
        for path in cases:
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.request(target=[self.inputs[0], path])
        for path in (self.root, nested / 'missing', self.inputs[0], self.root.parent):
            with self.subTest(folder=path), self.assertRaises((ValueError, OSError)):
                self.request('folder', path)

    def test_target_and_folder_reparse_metadata_rejected(self):
        real_stat = Path.stat
        for mode, target in (('open-multiple', self.inputs[1]), ('folder', self.destination)):
            with self.subTest(mode=mode):
                def redirected(path, *args, **kwargs):
                    info = real_stat(path, *args, **kwargs)
                    if path == target and kwargs.get('follow_symlinks') is False:
                        return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
                    return info
                with patch.object(Path, 'stat', redirected), self.assertRaises((ValueError, OSError)):
                    self.request(mode)

    def test_target_identity_changed_before_selection_prevents_write(self):
        request = self.request()
        request['targetIdentities'][1]['inode'] += 1
        result = self.select(request)
        self.assertFalse(result['selected'])
        self.assertEqual(self.backend.writes, [])
        self.assertNotIn((20, 1), self.backend.commands)

    def test_folder_replaced_after_text_readback_prevents_submit(self):
        request = self.request('folder')
        self.backend.labels[21] = 'folder:'
        def read(*_):
            request['targetIdentities'][0]['inode'] += 1
            return self.backend.text
        self.backend.read_text = read
        result = self.select(request)
        self.assertFalse(result['selected'])
        self.assertEqual(self.backend.commands, [(20, 2)])

    def test_mode_and_identity_schema_cannot_be_silently_switched(self):
        request = self.request()
        changes = ({'dialogMode': 'open'}, {'dialogMode': True}, {'schemaVersion': 1},
                   {'targetIdentities': [{'device': True, 'inode': 1}] * 2},
                   {'targets': [*request['targets'], request['targets'][0]]}, {'extra': 'private'})
        for change in changes:
            with self.subTest(change=change), self.assertRaises((ValueError, OSError)):
                worker.validate_request(request | change, expected_parent=os.getpid())

    def test_result_mode_count_and_unknown_fields_cannot_claim_success(self):
        request = self.request()
        result = self.select(request)
        for mode, change in (('file', {}), ('folder', {}), ('open-multiple', {'selection_count': True}),
                             ('open-multiple', {'selection_count': 3}),
                             ('open-multiple', {'private': 'private text'})):
            with self.subTest(mode=mode, change=change), self.assertRaises(ValueError):
                worker.validate_result(result | change, request['nonce'], mode=mode)

    def test_foreign_field_and_changed_owner_never_receive_selection(self):
        request = self.request()
        self.backend.pids[21] += 1
        result = self.select(request)
        self.assertFalse(result['selected'])
        self.assertEqual(self.backend.label_calls + self.backend.writes, [])
        self.backend = FakeWindows(os.getpid())
        self.backend.labels[21] = 'folder:'
        request = self.request('folder')
        def label(_):
            self.backend.pids[10] += 1
            return 'folder:'
        self.backend.label = label
        result = self.select(request)
        self.assertFalse(result['selected'])
        self.assertEqual(self.backend.writes + self.backend.commands, [])

    def test_readback_mismatch_and_expired_deadline_never_submit(self):
        request = self.request()
        self.backend.read_text = lambda *_: 'incorrect selection'
        result = self.select(request)
        self.assertEqual(result['reason'], 'filename_readback_mismatch')
        self.assertEqual(self.backend.commands, [(20, 2)])
        self.backend = FakeWindows(os.getpid())
        self.clock = Clock()
        request = self.request('folder')
        def label(_):
            self.clock.sleep(1)
            return 'folder:'
        self.backend.label = label
        result = self.select(request)
        self.assertFalse(result['selected'])
        self.assertEqual(self.backend.writes, [])
        self.assertNotIn((20, 1), self.backend.commands)

    def test_fixture_forwards_explicit_mode_and_defaults_to_file(self):
        for mode in ('file', 'open-multiple', 'folder'):
            request = self.request() if mode == 'open-multiple' else self.request('folder')
            with self.subTest(mode=mode), patch.object(fixture, 'build_request', return_value=request) as build, \
                    patch.object(fixture, 'cancel_owned_dialog', return_value=False):
                choice = fixture.NativeFileChoice(None, None, None, timeout=.2, mode=mode)
                self.assertEqual(choice.mode, mode)
                self.assertEqual(build.call_args.kwargs['mode'], mode)
                self.assertFalse(choice._started)
                self.assertTrue(choice.close()['shutdownConfirmed'])

    def test_folder_worker_waits_for_earlier_open_without_cancel_or_write(self):
        request = self.request('folder')
        def sleep(seconds):
            self.assertEqual(self.backend.writes + self.backend.commands, [])
            self.clock.sleep(seconds)
            self.backend.labels[21] = 'folder name:'
        result = worker.select_fixture(request, self.backend, clock=self.clock, sleep=sleep)
        self.assertTrue(result['selected'])
        self.assertEqual(self.backend.writes[0][1], str(self.destination))
        self.assertEqual(self.backend.commands, [])
        self.assertEqual(self.backend.invocations, [(20, 31)])

    def test_open_worker_never_uses_folder_label_or_generic_filename_id(self):
        request = self.request()
        self.backend.labels[21] = 'folder:'
        self.backend.control_id = lambda _: 1152
        result = self.select(request)
        self.assertFalse(result['selected'])
        self.assertEqual(self.backend.writes + self.backend.commands, [])

    def test_parent_cancel_refuses_new_mode_without_loading_native_backend(self):
        for mode in ('open-multiple', 'folder'):
            with self.subTest(mode=mode), patch.object(worker, 'WindowsBackend') as backend:
                self.assertFalse(worker.cancel_owned_dialog(self.request(mode)))
                backend.assert_not_called()

    def test_changed_mode_before_cancel_cannot_close_earlier_dialog(self):
        request = self.request('folder')
        self.backend.labels[21] = 'folder:'
        def read(*_):
            self.backend.labels[21] = 'file name:'
            return 'incorrect path'
        self.backend.read_text = read
        result = self.select(request)
        self.assertFalse(result['selected'])
        self.assertEqual(self.backend.commands, [])

    def test_invoked_folder_idok_that_only_navigates_fails_and_cancels_matched_mode(self):
        request = self.request('folder')
        self.backend.labels[21] = 'folder:'
        self.backend.close_on_submit = False
        result = self.select(request)
        self.assertFalse(result['selected'])
        self.assertIs(result['dialog_close_confirmed'], False)
        self.assertEqual(result['reason'], 'dialog_close_unconfirmed')
        self.assertEqual(result['stage'], 'confirm-close')
        self.assertEqual(self.backend.commands, [(20, 2)])
        self.assertEqual(self.backend.invocations, [(20, 31)])
        self.assertFalse(worker.validate_result(result, request['nonce'], mode='folder')['selected'])

    def test_delayed_actual_dialog_close_is_required_before_success(self):
        request = self.request()
        self.backend.close_on_submit = False
        def sleep(seconds):
            self.assertFalse(self.backend.closed)
            self.assertEqual(self.backend.commands, [(20, 1)])
            self.clock.sleep(seconds)
            self.backend.closed = True
        result = worker.select_fixture(request, self.backend, clock=self.clock, sleep=sleep)
        self.assertTrue(result['selected'])
        self.assertIs(result['dialog_close_confirmed'], True)
        self.assertEqual(self.backend.commands, [(20, 1)])

    def test_changed_owner_or_foreign_dialog_during_close_cannot_pass_or_cancel(self):
        cases = (lambda: self.backend.pids.update({10: os.getpid() + 1}),
                 lambda: self.backend.pids.update({20: os.getpid() + 1}),
                 lambda: self.backend.parents.update({20: 99}))
        for change in cases:
            with self.subTest(change=change):
                self.backend = FakeWindows(os.getpid())
                self.clock = Clock()
                self.backend.close_on_submit = False
                self.backend.after_submit = change
                result = self.select(self.request())
                self.assertFalse(result['selected'])
                self.assertIs(result['dialog_close_confirmed'], False)
                self.assertEqual(self.backend.commands, [(20, 1)])
                self.assertIn(result['reason'], ('parent_or_owner_changed', 'owned_dialog_changed_during_close'))

    def test_changed_target_metadata_during_close_cannot_pass(self):
        request = self.request('folder')
        self.backend.labels[21] = 'folder:'
        self.backend.close_on_submit = False
        self.backend.after_submit = lambda: request['targetIdentities'][0].update(inode=0)
        result = self.select(request)
        self.assertFalse(result['selected'])
        self.assertEqual(result['reason'], 'selection_identity_changed_during_close')
        self.assertEqual(self.backend.commands, [(20, 2)])
        self.assertEqual(self.backend.invocations, [(20, 31)])

    def test_new_mode_success_without_close_confirmation_is_rejected(self):
        request = self.request()
        result = self.select(request)
        for change in ({'dialog_close_confirmed': False}, {'dialog_close_confirmed': 1}, {'stage': 'submit'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                worker.validate_result(result | change, request['nonce'], mode='open-multiple')


if __name__ == '__main__':
    unittest.main()
