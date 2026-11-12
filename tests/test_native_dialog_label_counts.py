"""Allowlisted owned-Edit label counts; no native UI or child-process launch."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from nivram import native_dialog_worker as worker
import test_native_batch_dialog_fixture as fixtures


class FakeInvokePattern:
    Pattern = object()
    def __init__(self):
        self.Invoke = Mock()


class NativeDialogLabelCountTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='nv-label-count-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        directory = self.root / 'synthetic fixtures'
        directory.mkdir()
        (self.root / 'instance.lock').write_bytes(b'0')
        self.inputs = [directory / 'input ü.nivram', directory / 'invalid β.mol']
        for path in self.inputs:
            path.write_text('synthetic only', encoding='utf-8')
        self.folder = directory / 'destination ü'
        self.folder.mkdir()
        self.window = SimpleNamespace(native=SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda: 10)))
        self.backend = fixtures.FakeWindows(os.getpid())
        self.clock = fixtures.Clock()

    def request(self, mode='open-multiple'):
        if mode == 'folder':
            self.backend.add_folder_button()
        target = self.inputs if mode == 'open-multiple' else self.folder
        with patch.object(worker, 'process_created', return_value=123):
            return worker.build_request(self.window, target, self.root, .2, nonce='a' * 32, mode=mode)

    def select(self, request):
        return worker.select_fixture(request, self.backend, clock=self.clock, sleep=self.clock.sleep)

    def test_filename_and_other_counts_use_existing_owned_reads_without_raw_text(self):
        request = self.request()
        self.backend.controls = [21, 22]
        self.backend.labels[22] = 'PRIVATE CAPTION / secret synthetic example'
        result = self.select(request)
        self.assertTrue(result['selected'])
        self.assertEqual(result['label_counts'], {'filename': 1, 'folder': 0, 'other': 1})
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertTrue(worker.validate_result(result, request['nonce'], mode='open-multiple')['selected'])

    def test_folder_wait_with_filename_label_reports_count_without_new_fallback(self):
        request = self.request('folder')
        result = self.select(request)
        self.assertFalse(result['selected'])
        self.assertEqual(result['reason'], 'supported_folder_control_not_found')
        self.assertEqual(result['label_counts'], {'filename': 1, 'folder': 0, 'other': 0})
        self.assertEqual(self.backend.writes + self.backend.commands, [])
        # Repeated polling reports the current inspected controls, not accumulated reads.
        self.assertGreater(len(self.backend.label_calls), 1)

    def test_explicit_folder_count_and_unowned_control_exclusion(self):
        request = self.request('folder')
        self.backend.controls = [21, 22, 31]
        self.backend.labels[21] = 'folder name:'
        self.backend.pids[22] += 1
        result = self.select(request)
        self.assertTrue(result['selected'])
        self.assertEqual(result['label_counts'], {'filename': 0, 'folder': 1, 'other': 0})
        self.assertNotIn(22, self.backend.label_calls)
        self.assertEqual(self.backend.commands, [])
        self.assertEqual(self.backend.invocations, [(20, 31)])

    def test_count_validation_rejects_wrong_types_keys_bounds_and_total(self):
        request = self.request()
        result = self.select(request)
        invalid = (None, {'filename': True, 'folder': 0, 'other': 0},
                   {'filename': -1, 'folder': 0, 'other': 0},
                   {'filename': 257, 'folder': 0, 'other': 0},
                   {'filename': 1, 'folder': 1, 'other': 0},
                   {'filename': 1, 'folder': 0},
                   {'filename': 1, 'folder': 0, 'other': 0, 'caption': 'PRIVATE'})
        for counts in invalid:
            with self.subTest(counts=counts), self.assertRaises(ValueError):
                worker.validate_result(result | {'label_counts': counts}, request['nonce'], mode='open-multiple')
        missing = copy.deepcopy(result)
        del missing['label_counts']
        with self.assertRaises(ValueError):
            worker.validate_result(missing, request['nonce'], mode='open-multiple')

    def test_single_file_result_shape_and_control_reads_remain_unchanged(self):
        with patch.object(worker, 'process_created', return_value=123):
            request = worker.build_request(self.window, self.inputs[0], self.root, .2, nonce='a' * 32)
        result = self.select(request)
        self.assertTrue(result['selected'])
        self.assertNotIn('label_counts', result)
        self.assertNotIn('idok_label_kind', result)
        self.assertEqual(self.backend.label_calls, [21])
        self.assertTrue(worker.validate_result(result, request['nonce'])['selected'])
        with self.assertRaises(ValueError):
            worker.validate_result(result | {'label_counts': {'filename': 1, 'folder': 0, 'other': 0}}, request['nonce'])

    def install_buttons(self, labels):
        original_class, original_id = self.backend.class_name, self.backend.control_id
        for hwnd, label in labels.items():
            self.backend.pids[hwnd] = os.getpid()
            self.backend.parents[hwnd] = 20
            self.backend.labels[hwnd] = label
            self.backend.controls.append(hwnd)
        self.backend.class_name = lambda hwnd: 'Button' if hwnd in labels else original_class(hwnd)
        self.backend.control_id = lambda hwnd: 1 if hwnd in labels else original_id(hwnd)

    def test_idok_categories_are_fixed_and_captions_never_leave_result(self):
        for caption, expected in (('select folder', 'folder'), ('choose a folder', 'folder'),
                                  ('&open', 'open'), ('save', 'save'), ('PRIVATE BUTTON TEXT', 'other')):
            with self.subTest(caption=caption):
                self.backend = fixtures.FakeWindows(os.getpid())
                self.clock = fixtures.Clock()
                self.install_buttons({31: caption})
                result = self.select(self.request())
                self.assertEqual(result['idok_label_kind'], expected)
                self.assertNotIn('PRIVATE', json.dumps(result))
                self.assertEqual(result['label_counts'], {'filename': 1, 'folder': 0, 'other': 0})

    def test_foreign_ambiguous_and_no_idok_are_unavailable_without_caption_read(self):
        for case in ('foreign', 'ambiguous', 'no-id', 'hidden', 'disabled'):
            with self.subTest(case=case):
                self.backend = fixtures.FakeWindows(os.getpid())
                self.clock = fixtures.Clock()
                self.install_buttons({31: 'select folder'})
                if case == 'foreign':
                    self.backend.pids[31] += 1
                elif case == 'ambiguous':
                    self.install_buttons({32: 'select folder'})
                elif case == 'no-id':
                    self.backend.control_id = lambda _: 0
                elif case == 'hidden':
                    visible = self.backend.visible
                    self.backend.visible = lambda hwnd: hwnd != 31 and visible(hwnd)
                else:
                    self.backend.enabled = lambda hwnd: hwnd != 31
                result = self.select(self.request())
                self.assertEqual(result['idok_label_kind'], 'unavailable')
                self.assertNotIn(31, self.backend.label_calls)
                self.assertNotIn(32, self.backend.label_calls)

    def test_idok_owner_change_after_label_read_is_unavailable_and_never_writes(self):
        self.install_buttons({31: 'select folder'})
        label = self.backend.label
        def changing(hwnd):
            value = label(hwnd)
            if hwnd == 31:
                self.backend.pids[10] += 1
            return value
        self.backend.label = changing
        result = self.select(self.request())
        self.assertEqual(result['idok_label_kind'], 'unavailable')
        self.assertEqual(self.backend.writes + self.backend.commands, [])

    def test_idok_enum_rejects_unbounded_values_or_missing_category(self):
        request = self.request()
        result = self.select(request)
        for kind in ('Select Folder', 'PRIVATE CAPTION', None, True, 1, {}):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                worker.validate_result(result | {'idok_label_kind': kind}, request['nonce'], mode='open-multiple')
        missing = copy.deepcopy(result)
        del missing['idok_label_kind']
        with self.assertRaises(ValueError):
            worker.validate_result(missing, request['nonce'], mode='open-multiple')

    def test_older_schema_two_receipt_without_either_diagnostic_remains_valid(self):
        for mode in ('open-multiple', 'folder'):
            with self.subTest(mode=mode):
                self.backend = fixtures.FakeWindows(os.getpid())
                self.clock = fixtures.Clock()
                if mode == 'folder':
                    self.backend.labels[21] = 'folder:'
                request = self.request(mode)
                result = self.select(request)
                del result['label_counts']
                del result['idok_label_kind']
                validated = worker.validate_result(result, request['nonce'], mode=mode)
                self.assertTrue(validated['selected'])
                self.assertIs(validated['dialog_close_confirmed'], True)
                self.assertNotIn('label_counts', validated)
                self.assertNotIn('idok_label_kind', validated)

    def test_one_sided_diagnostic_presence_is_rejected(self):
        request = self.request()
        result = self.select(request)
        for missing in ('label_counts', 'idok_label_kind'):
            with self.subTest(missing=missing):
                partial = copy.deepcopy(result)
                del partial[missing]
                with self.assertRaises(ValueError):
                    worker.validate_result(partial, request['nonce'], mode='open-multiple')

    def invoke_objects(self):
        native = object.__new__(worker.WindowsBackend)
        native.parent_pid = os.getpid()
        pattern = FakeInvokePattern()
        element = SimpleNamespace(Current=SimpleNamespace(NativeWindowHandle=31,
            ProcessId=os.getpid(), IsEnabled=True, IsOffscreen=False, Name='Select Folder'),
            GetRuntimeId=Mock(return_value=[42, 31]),
            GetCurrentPattern=Mock(return_value=pattern))
        native._element = Mock(return_value=element)
        return native, element, pattern

    def prepare(self, native):
        with patch.dict(sys.modules, {'System.Windows.Automation': SimpleNamespace(InvokePattern=FakeInvokePattern)}):
            return native.prepare_invoke(31)

    def test_folder_invoke_uses_pattern_from_exact_hwnd_once_without_legacy_message(self):
        request = self.request('folder')
        self.backend.labels[21] = 'folder:'
        with patch.object(self.backend, 'submit', wraps=self.backend.submit) as legacy_submit:
            result = self.select(request)
        self.assertTrue(result['selected'])
        self.assertTrue(result['dialog_close_confirmed'])
        self.assertEqual(self.backend.invocations, [(20, 31)])
        legacy_submit.assert_not_called()
        native, element, pattern = self.invoke_objects()
        prepared = self.prepare(native)
        native._element.assert_called_once_with(31)
        element.GetCurrentPattern.assert_called_once_with(FakeInvokePattern.Pattern)
        self.assertTrue(native.invoke_ready(prepared, 31))
        self.assertTrue(native.invoke_prepared(prepared))
        pattern.Invoke.assert_called_once_with()

    def test_pattern_retrieval_rejects_foreign_hwnd_pid_disabled_hidden_and_wrong_button(self):
        for key, value in (('NativeWindowHandle', 32), ('ProcessId', os.getpid()+1),
                           ('IsEnabled', False), ('IsOffscreen', True), ('Name', 'Open')):
            with self.subTest(key=key):
                native, element, pattern = self.invoke_objects()
                setattr(element.Current, key, value)
                with self.assertRaises(ValueError):
                    self.prepare(native)
                element.GetCurrentPattern.assert_not_called()
                pattern.Invoke.assert_not_called()

    def test_unsupported_pattern_type_is_never_invoked(self):
        native, element, pattern = self.invoke_objects()
        element.GetCurrentPattern.return_value = object()
        with self.assertRaises(ValueError):
            self.prepare(native)
        pattern.Invoke.assert_not_called()

    def test_ui_automation_identity_is_rechecked_after_pattern_retrieval(self):
        for change in ('hwnd', 'pid', 'disabled', 'hidden', 'label', 'stale-runtime'):
            with self.subTest(change=change):
                native, element, pattern = self.invoke_objects()
                def fetch(_):
                    if change == 'hwnd': element.Current.NativeWindowHandle = 32
                    elif change == 'pid': element.Current.ProcessId += 1
                    elif change == 'disabled': element.Current.IsEnabled = False
                    elif change == 'hidden': element.Current.IsOffscreen = True
                    elif change == 'label': element.Current.Name = 'Open'
                    else: element.GetRuntimeId.return_value = [42, 32]
                    return pattern
                element.GetCurrentPattern.side_effect = fetch
                prepared = self.prepare(native)
                self.assertFalse(native.invoke_ready(prepared, 31))
                pattern.Invoke.assert_not_called()

    def test_hwnd_owner_field_target_and_deadline_changes_after_pattern_fetch_block_invoke(self):
        for change in ('button-pid', 'parent', 'owner', 'disabled', 'field', 'target', 'deadline'):
            with self.subTest(change=change):
                self.backend = fixtures.FakeWindows(os.getpid())
                self.clock = fixtures.Clock()
                request = self.request('folder')
                self.backend.labels[21] = 'folder:'
                prepare = self.backend.prepare_invoke
                def changed(button):
                    prepared = prepare(button)
                    if change == 'button-pid': self.backend.pids[31] += 1
                    elif change == 'parent': self.backend.parents[31] = 99
                    elif change == 'owner': self.backend.pids[10] += 1
                    elif change == 'disabled': self.backend.enabled = lambda hwnd: hwnd != 31
                    elif change == 'field': self.backend.labels[21] = 'file name:'
                    elif change == 'target': request['targetIdentities'][0]['inode'] += 1
                    else: self.clock.now = 1
                    return prepared
                self.backend.prepare_invoke = changed
                result = self.select(request)
                self.assertFalse(result['selected'])
                self.assertEqual(self.backend.pattern_fetches, [31])
                self.assertEqual(self.backend.invocations, [])
                self.assertNotIn((20, 1), self.backend.commands)

    def test_target_change_during_last_uia_identity_read_blocks_invoke(self):
        request = self.request('folder')
        self.backend.labels[21] = 'folder:'
        def changed(*_):
            request['targetIdentities'][0]['inode'] += 1
            return True
        self.backend.invoke_ready = changed
        result = self.select(request)
        self.assertFalse(result['selected'])
        self.assertEqual(self.backend.pattern_fetches, [31])
        self.assertEqual(self.backend.invocations, [])

    def test_pattern_fetch_or_invoke_exception_has_no_activation_retry(self):
        for phase in ('fetch', 'invoke'):
            with self.subTest(phase=phase):
                self.backend = fixtures.FakeWindows(os.getpid())
                self.clock = fixtures.Clock()
                request = self.request('folder')
                self.backend.labels[21] = 'folder:'
                name = 'prepare_invoke' if phase == 'fetch' else 'invoke_prepared'
                operation = Mock(side_effect=RuntimeError('private provider error'))
                setattr(self.backend, name, operation)
                result = self.select(request)
                self.assertFalse(result['selected'])
                self.assertEqual(result['reason'], 'native_operation_failed')
                operation.assert_called_once()
                self.assertNotIn('private', json.dumps(result))
                self.assertNotIn((20, 1), self.backend.commands)

    def test_folder_requires_unique_positive_owned_idok_before_any_path_write(self):
        for change in ('foreign', 'open', 'ambiguous', 'disabled', 'missing'):
            with self.subTest(change=change):
                self.backend = fixtures.FakeWindows(os.getpid())
                self.clock = fixtures.Clock()
                request = self.request('folder')
                self.backend.labels[21] = 'folder:'
                if change == 'foreign':
                    self.backend.pids[31] += 1
                elif change == 'open':
                    self.backend.labels[31] = 'open'
                elif change == 'ambiguous':
                    self.backend.add_folder_button(32)
                elif change == 'disabled':
                    self.backend.enabled = lambda hwnd: hwnd != 31
                else:
                    self.backend.controls.remove(31)
                result = self.select(request)
                self.assertFalse(result['selected'])
                self.assertEqual(self.backend.writes, [])
                self.assertEqual(self.backend.invocations, [])
                self.assertNotIn((20, 1), self.backend.commands)

    def test_folder_button_replacement_after_path_readback_never_receives_invoke(self):
        request = self.request('folder')
        self.backend.labels[21] = 'folder:'
        def read(*_):
            self.backend.controls.remove(31)
            self.backend.add_folder_button(32)
            return self.backend.text
        self.backend.read_text = read
        result = self.select(request)
        self.assertFalse(result['selected'])
        self.assertEqual(self.backend.invocations, [])
        self.assertNotIn((20, 1), self.backend.commands)

    def test_folder_guard_rechecks_owner_field_and_targets_after_button_accessibility_read(self):
        for change in ('owner', 'field', 'target'):
            with self.subTest(change=change):
                self.backend = fixtures.FakeWindows(os.getpid())
                self.clock = fixtures.Clock()
                request = self.request('folder')
                self.backend.labels[21] = 'folder:'
                label, reads = self.backend.label, []
                def changing(hwnd):
                    value = label(hwnd)
                    if hwnd == 31:
                        reads.append(hwnd)
                        # The first read is observational; the second pins the submit route.
                        if len(reads) == 2:
                            if change == 'owner':
                                self.backend.pids[10] += 1
                            elif change == 'field':
                                self.backend.labels[21] = 'file name:'
                            else:
                                request['targetIdentities'][0]['inode'] += 1
                    return value
                self.backend.label = changing
                result = self.select(request)
                self.assertFalse(result['selected'])
                self.assertEqual(self.backend.writes, [])
                self.assertEqual(self.backend.invocations, [])
                self.assertNotIn((20, 1), self.backend.commands)

    def test_folder_only_navigation_still_times_out_without_retry_or_false_success(self):
        request = self.request('folder')
        self.backend.labels[21] = 'folder:'
        self.backend.close_on_submit = False
        result = self.select(request)
        self.assertFalse(result['selected'])
        self.assertEqual(result['reason'], 'dialog_close_unconfirmed')
        self.assertFalse(result['dialog_close_confirmed'])
        self.assertEqual(self.backend.invocations, [(20, 31)])
        self.assertEqual(self.backend.commands, [(20, 2)])

    def test_proven_open_multiple_does_not_require_or_use_folder_invoke(self):
        result = self.select(self.request())
        self.assertTrue(result['selected'])
        self.assertEqual(self.backend.invocations, [])
        self.assertEqual(self.backend.commands, [(20, 1)])

    def test_target_pin_change_during_final_field_label_read_blocks_write_or_invoke(self):
        for phase in ('before-write', 'before-invoke'):
            with self.subTest(phase=phase):
                self.backend = fixtures.FakeWindows(os.getpid())
                self.clock = fixtures.Clock()
                request = self.request('folder')
                self.backend.labels[21] = 'folder:'
                label = self.backend.label
                seen_field, attested_button = [], []
                def changing(hwnd):
                    value = label(hwnd)
                    if hwnd == 31 and seen_field:
                        if (phase == 'before-write' and not self.backend.writes
                                or phase == 'before-invoke' and self.backend.writes):
                            attested_button.append(True)
                    if hwnd == 21:
                        seen_field.append(True)
                        if attested_button:
                            request['targetIdentities'][0]['inode'] += 1
                    return value
                self.backend.label = changing
                result = self.select(request)
                self.assertFalse(result['selected'])
                self.assertTrue(attested_button)
                self.assertEqual(self.backend.invocations, [])
                if phase == 'before-write':
                    self.assertEqual(self.backend.writes, [])
                else:
                    self.assertEqual(len(self.backend.writes), 1)


if __name__ == '__main__':
    unittest.main()
