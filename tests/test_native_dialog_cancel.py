"""Bounded intentional chooser cancellation; fake UI, no native qualification."""
import copy
import io
import json
import os
import time
import unittest
from unittest.mock import Mock, patch

from nivram import native_dialog_fixture as fixture
from nivram import native_dialog_worker as worker
import test_native_batch_dialog_fixture as support


class IntentionalCancelTests(unittest.TestCase):
    def setUp(self):
        support.NativeBatchDialogFixtureTests.setUp(self)
        self.backend.add_folder_button()
        self.backend.labels[31] = 'open'
        self.backend.submit = self.submit
        self.after_cancel = None
        self.cancel_closes = True
        self.submit_ok = True

    def submit(self, hwnd, command):
        self.backend.commands.append((hwnd, command))
        self.assertEqual((hwnd, command), (20, 2))
        if self.after_cancel:
            self.after_cancel()
        if self.submit_ok and self.cancel_closes:
            self.backend.closed = True
        return self.submit_ok

    def request(self, mode='open-multiple', action='cancel'):
        if mode == 'folder':
            self.backend.labels.update({21: 'folder:', 31: 'select folder'})
        target = self.destination if mode == 'folder' else self.inputs
        with patch.object(worker, 'process_created', return_value=123):
            return worker.build_request(self.window, target, self.root, .2,
                                        nonce='a' * 32, mode=mode, action=action)

    def run_cancel(self, request=None, sleep=None):
        request = self.request() if request is None else request
        value = worker.select_fixture(request, self.backend, clock=self.clock,
                                      sleep=sleep or self.clock.sleep)
        checked = worker.validate_result(value, request['nonce'], mode=request['dialogMode'], action='cancel')
        self.assertNotIn(str(self.root), json.dumps(checked))
        self.assertEqual(self.backend.writes + self.backend.invocations + self.backend.pattern_fetches, [])
        return value

    def test_open_cancel_observes_closure_without_typing_or_selecting(self):
        value = self.run_cancel()
        self.assertEqual(self.backend.commands, [(20, 2)])
        self.assertIs(value['selected'], False)
        self.assertIs(value['cancelled'], True)
        self.assertIs(value['filename_field_verified'], False)
        self.assertEqual(value['reason'], 'intentional_cancel')

    def test_folder_cancel_observes_closure_without_typing_or_selecting(self):
        value = self.run_cancel(self.request('folder'))
        self.assertIs(value['cancelled'], True)
        self.assertEqual(value['selection_mode'], 'folder')
        self.assertEqual(self.backend.commands, [(20, 2)])

    def test_schema_action_cannot_be_silently_downgraded_or_injected(self):
        request = self.request()
        self.assertEqual(set(request), worker.CANCEL_FIELDS)
        for change in ({'schemaVersion': 2}, {'schemaVersion': 1}, {'dialogAction': 'select'},
                       {'dialogAction': True}, {'dialogMode': 'file'}, {'extra': 'private'}):
            with self.subTest(change=change), self.assertRaises((ValueError, OSError)):
                worker.validate_request(request | change, expected_parent=os.getpid())
        for action in (True, 'Cancel', None, 'close'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.request(action=action)
        with self.assertRaises(ValueError):
            worker.build_request(self.window, self.inputs[0], self.root, .2,
                                 nonce='a' * 32, mode='file', action='cancel')

    def test_cancel_receipt_cannot_be_selection_or_omitted_evidence(self):
        value = self.run_cancel()
        with self.assertRaises(ValueError):
            worker.validate_result(value, 'a' * 32, mode='open-multiple')
        cases = ({'schemaVersion': 1}, {'selected': True}, {'cancelled': 1}, {'cancel_posted': False},
                 {'dialog_close_confirmed': False}, {'reason': 'worker_timeout'}, {'stage': 'submit'},
                 {'filename_field_verified': True}, {'nonce': 'b' * 32}, {'dialog_action': 'select'},
                 {'selection_count': True}, {'private': 'private'})
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError):
                worker.validate_result(value | change, 'a' * 32, mode='open-multiple', action='cancel')
        for missing in ('cancelled', 'cancel_posted', 'dialog_action', 'filename_field_verified'):
            broken = {k: v for k, v in value.items() if k != missing}
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                worker.validate_result(broken, 'a' * 32, mode='open-multiple', action='cancel')

    def test_selected_receipt_cannot_be_cancel(self):
        request = self.request(action='select')
        # Deliberately author an otherwise valid predecessor receipt.
        value = {'schemaVersion': 1, 'nonce': request['nonce'], 'selected': True,
                 'reason': None, 'filename_field_verified': True, 'scope': worker.SCOPE,
                 'stage': 'confirm-close', 'control_count': 2, 'dialog_count': 1,
                 'selection_mode': 'open-multiple', 'selection_count': 2, 'dialog_close_confirmed': True}
        self.assertTrue(worker.validate_result(value, request['nonce'], mode='open-multiple')['selected'])
        with self.assertRaises(ValueError):
            worker.validate_result(value, request['nonce'], mode='open-multiple', action='cancel')

    def test_wrong_mode_or_save_dialog_is_never_cancelled(self):
        for field, button in (('folder:', 'select folder'), ('file name:', 'save'), ('file name:', 'other')):
            with self.subTest(field=field, button=button):
                self.backend.labels.update({21: field, 31: button})
                self.clock.now = 0
                self.assertFalse(self.run_cancel()['cancelled'])
                self.assertEqual(self.backend.commands, [])

    def test_folder_worker_waits_for_open_to_finish_without_touching_it(self):
        request = self.request('folder')
        self.backend.labels.update({21: 'file name:', 31: 'open'})
        def sleep(seconds):
            self.assertEqual(self.backend.commands, [])
            self.clock.sleep(seconds)
            self.backend.labels.update({21: 'folder:', 31: 'select folder'})
        self.assertTrue(self.run_cancel(request, sleep)['cancelled'])
        self.assertEqual(self.backend.commands, [(20, 2)])

    def test_ambiguous_owned_dialogs_are_untouched(self):
        self.backend.pids[98] = os.getpid()
        self.backend.parents[98] = 10
        self.backend.dialogs.append(98)
        self.assertEqual(self.run_cancel()['reason'], 'ambiguous_owned_dialog')
        self.assertEqual(self.backend.commands, [])

    def test_ambiguous_fields_are_untouched(self):
        self.backend.controls.append(22)
        self.assertEqual(self.run_cancel()['reason'], 'ambiguous_filename_field')
        self.assertEqual(self.backend.commands, [])

    def test_foreign_field_is_untouched(self):
        self.backend.pids[21] += 1
        self.assertFalse(self.run_cancel()['cancelled'])
        self.assertEqual(self.backend.commands, [])

    def test_owner_changed_during_accessibility_lookup_prevents_action(self):
        original = self.backend.label
        def label(hwnd):
            value = original(hwnd)
            self.backend.pids[10] += 1
            return value
        self.backend.label = label
        self.assertFalse(self.run_cancel()['cancelled'])
        self.assertEqual(self.backend.commands, [])

    def test_slow_accessibility_read_cannot_post_after_deadline(self):
        original = self.backend.label
        def label(hwnd):
            self.clock.sleep(1)
            return original(hwnd)
        self.backend.label = label
        self.assertFalse(self.run_cancel()['cancelled'])
        self.assertEqual(self.backend.commands, [])

    def test_progress_write_before_cancel_cannot_bypass_final_guards(self):
        for change in ('deadline', 'owner'):
            with self.subTest(change=change):
                self.clock.now = 0
                self.backend.pids[10] = os.getpid()
                emitter = worker.ProgressEmitter('a' * 32, io.BytesIO())
                def emit(_emitter, event):
                    if event == 'cancel_enter':
                        if change == 'deadline':
                            self.clock.sleep(1)
                        else:
                            self.backend.pids[10] += 1
                with patch.object(worker.ProgressEmitter, 'emit', emit):
                    value = worker.select_fixture(self.request(), self.backend, clock=self.clock,
                                                  sleep=self.clock.sleep, progress=emitter)
                self.assertFalse(value['cancelled'])
                self.assertEqual(self.backend.commands, [])

    def test_identity_change_during_lookup_prevents_cancel(self):
        request = self.request()
        original = self.backend.label
        def label(hwnd):
            request['targetIdentities'][0]['inode'] += 1
            return original(hwnd)
        self.backend.label = label
        self.assertFalse(self.run_cancel(request)['cancelled'])
        self.assertEqual(self.backend.commands, [])

    def test_delayed_closure_requires_observation_and_only_one_cancel(self):
        self.cancel_closes = False
        def sleep(seconds):
            self.assertEqual(self.backend.commands, [(20, 2)])
            self.clock.sleep(seconds)
            self.backend.closed = True
        self.assertTrue(self.run_cancel(sleep=sleep)['cancelled'])
        self.assertGreater(self.clock.now, 0)

    def test_window_disappearance_observed_at_deadline_is_not_success(self):
        def late_window(_hwnd):
            self.clock.now = .2 - .2 * .1
            return False
        self.after_cancel = lambda: setattr(self.backend, 'window', late_window)
        value = self.run_cancel()
        self.assertTrue(value['cancel_posted'])
        self.assertFalse(value['cancelled'])
        self.assertEqual(value['reason'], 'dialog_close_unconfirmed')

    def test_hidden_dialog_observed_after_deadline_is_not_success(self):
        def late_visible(_hwnd):
            self.clock.sleep(1)
            return False
        def change():
            self.backend.window = lambda _: True
            self.backend.visible = late_visible
        self.after_cancel = change
        value = self.run_cancel()
        self.assertTrue(value['cancel_posted'])
        self.assertFalse(value['cancelled'])
        self.assertEqual(value['reason'], 'dialog_close_unconfirmed')

    def test_late_receipt_emission_preserves_on_time_closure(self):
        emitter = worker.ProgressEmitter('a' * 32, io.BytesIO())
        def emit(_emitter, event):
            if event == 'confirm_close_returned':
                self.clock.sleep(1)
        with patch.object(worker.ProgressEmitter, 'emit', emit):
            value = worker.select_fixture(self.request(), self.backend, clock=self.clock,
                                          sleep=self.clock.sleep, progress=emitter)
        self.assertTrue(value['cancelled'])

    def test_post_without_closure_is_not_success_and_is_not_retried(self):
        self.cancel_closes = False
        value = self.run_cancel()
        self.assertTrue(value['cancel_posted'])
        self.assertFalse(value['cancelled'])
        self.assertEqual(value['reason'], 'dialog_close_unconfirmed')
        self.assertEqual(self.backend.commands, [(20, 2)])

    def test_failed_post_is_not_success_and_is_not_retried(self):
        self.submit_ok = False
        value = self.run_cancel()
        self.assertFalse(value['cancel_posted'])
        self.assertFalse(value['cancelled'])
        self.assertEqual(value['reason'], 'cancellation_post_failed')
        self.assertEqual(self.backend.commands, [(20, 2)])

    def test_foreign_replacement_after_cancel_is_not_closure(self):
        self.cancel_closes = False
        self.after_cancel = lambda: self.backend.pids.update({20: os.getpid() + 1})
        value = self.run_cancel()
        self.assertFalse(value['cancelled'])
        self.assertEqual(value['reason'], 'owned_dialog_changed_during_close')

    def test_changed_parent_after_cancel_is_not_closure(self):
        self.after_cancel = lambda: self.backend.pids.update({10: os.getpid() + 1})
        value = self.run_cancel()
        self.assertFalse(value['cancelled'])
        self.assertEqual(value['reason'], 'parent_or_owner_changed')

    def test_changed_fixture_identity_after_cancel_is_not_closure(self):
        request = self.request()
        self.after_cancel = lambda: request['targetIdentities'][0].update(inode=0)
        value = self.run_cancel(request)
        self.assertFalse(value['cancelled'])
        self.assertEqual(value['reason'], 'selection_identity_changed_during_close')

    def test_parent_cleanup_never_guesses_new_cancel_mode(self):
        request = self.request()
        with patch.object(worker, 'WindowsBackend') as backend:
            self.assertFalse(worker.cancel_owned_dialog(request))
        backend.assert_not_called()

    def test_fixture_finish_accepts_only_its_bound_action_and_worker_exit(self):
        request = self.request()
        raw = self.run_cancel(request)
        checked = worker.validate_result(raw, request['nonce'], mode='open-multiple', action='cancel')
        with patch.object(fixture, 'build_request', return_value=request) as build:
            choice = fixture.NativeFileChoice(None, None, None, mode='open-multiple', action='cancel')
        self.addCleanup(choice.close)
        self.assertEqual(build.call_args.kwargs['action'], 'cancel')
        choice.thread = Mock()
        choice.thread.is_alive.return_value = False
        choice._started = True
        choice._deadline = time.monotonic()
        choice.result = checked | {'worker_exit_confirmed': True}
        self.assertTrue(choice.finish()['cancelled'])
        original = copy.deepcopy(choice.result)
        for change in ({'worker_exit_confirmed': False}, {'cancelled': False}, {'cancelled': None}):
            choice.result = original | change
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                choice.finish()
        choice.action = 'select'
        choice.result = original
        with self.assertRaises(RuntimeError):
            choice.finish()


if __name__ == '__main__':
    unittest.main()
