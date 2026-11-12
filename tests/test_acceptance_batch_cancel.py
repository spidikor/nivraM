"""Native cancellation evidence gates/observer; fake chooser calls only."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from nivram import acceptance
from nivram import acceptance_batch_cancel as cancel
from nivram.acceptance_batch_png import INPUT_FILTER
import test_acceptance_batch_gates as prior


CHECKS = frozenset({'editor-and-native-bridge-ready', 'native-batch-cancel-synthetic-inputs',
    'native-batch-open-cancelled', 'native-batch-folder-cancelled',
    'native-batch-selection-cancel-integrity', 'native-window-loop-returned'})


def report():
    value = prior.complete_batch_report()
    value['scenario'] = 'batch-selection-cancel'
    value['checks'] = [{'id': name, 'status': 'passed'} for name in CHECKS]
    value['dialog_fixture_shutdown'] = [{'shutdownConfirmed': True} for _ in range(3)]
    return value


class CancelGateTests(unittest.TestCase):
    def test_exact_six_checks_and_three_dialogs_and_zero_downloads(self):
        self.assertEqual(acceptance.SCENARIO_CHECKS['batch-selection-cancel'], CHECKS)
        self.assertEqual(cancel.REQUIRED_CHECKS | {'editor-and-native-bridge-ready', 'native-window-loop-returned'}, CHECKS)
        self.assertEqual(acceptance.finalize_acceptance_report(report())['status'], 'passed')

    def test_each_missing_or_unconfirmed_check_fails(self):
        for name in CHECKS:
            for omitted in (True, False):
                with self.subTest(name=name, omitted=omitted):
                    value = report()
                    if omitted:
                        value['checks'] = [row for row in value['checks'] if row['id'] != name]
                    else:
                        next(row for row in value['checks'] if row['id'] == name)['status'] = 'observed'
                    self.assertIn('required_functional_checks_missing', acceptance.finalize_acceptance_report(value)['acceptance_failures'])

    def test_selection_cancel_cannot_qualify_existing_scenarios_or_reverse(self):
        for scenario in ('full', 'drawing-png', 'batch-png'):
            with self.subTest(scenario=scenario):
                value = report()
                value['scenario'] = scenario
                self.assertEqual(acceptance.finalize_acceptance_report(value)['status'], 'failed')
        value = prior.complete_batch_report()
        value['scenario'] = 'batch-selection-cancel'
        self.assertEqual(acceptance.finalize_acceptance_report(value)['status'], 'failed')

    def test_exact_three_confirmed_shutdowns_required(self):
        for rows in (None, [], [{'shutdownConfirmed': True}] * 2, [{'shutdownConfirmed': True}] * 4,
                     [{'shutdownConfirmed': True}, {'shutdownConfirmed': True}, {'shutdownConfirmed': False}]):
            with self.subTest(rows=rows):
                value = report()
                value['dialog_fixture_shutdown'] = rows
                self.assertIn('dialog_fixture_shutdown_unconfirmed', acceptance.finalize_acceptance_report(value)['acceptance_failures'])

    def test_all_privacy_process_worker_and_download_gates_retained(self):
        cases = (('network_policy', {'failures': 1}), ('cleanup', {'profile_directory_retained': True}),
                 ('evaluation_shutdown', {'outstandingCallMayComplete': True}),
                 ('worker_shutdown', {'ownedProcessesExited': False}),
                 ('process', {'exit_code': 0, 'forced_termination': True}),
                 ('process', {'exit_code': 1, 'forced_termination': False}),
                 ('download_shutdown', {'completed': 1}), ('downloads_final', {'completed': 1}),
                 ('download_shutdown', {'shutdown_confirmed': False}), ('downloads_final', {'active': 1}))
        for key, change in cases:
            with self.subTest(key=key, change=change):
                value = report()
                value.setdefault(key, {}).update(change)
                self.assertEqual(acceptance.finalize_acceptance_report(value)['status'], 'failed')
        value = report()
        value['image_preview_shutdown_confirmed'] = False
        self.assertEqual(acceptance.finalize_acceptance_report(value)['status'], 'failed')

    def test_child_command_binds_explicit_scenario(self):
        command = acceptance.acceptance_child_command(Path('native.json'), Path('profile'), 'batch-selection-cancel')
        self.assertEqual(command[-2:], ['--scenario', 'batch-selection-cancel'])


class CancelObserverTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='nv-cancel-observer-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.inputs = (self.root / 'one.nivram', self.root / 'two.mol')
        for target in self.inputs:
            target.write_bytes(b'synthetic')
        self.folder = self.root / 'output'
        self.folder.mkdir()
        self.actual = None
        self.calls, self.choices = [], []
        self.receipt_changes = {}
        self.original_error = None
        self.fixture_error = None
        def original(*args, **kwargs):
            self.calls.append((args, kwargs))
            if self.original_error:
                raise self.original_error
            return self.actual
        self.window = SimpleNamespace(create_file_dialog=original)
        self.original = original
        def choose(target, **kwargs):
            self.choices.append((target, kwargs))
            cancelled = kwargs['action'] == 'cancel'
            result = {'selected': not cancelled, 'cancelled': cancelled, 'cancel_posted': cancelled,
                      'worker_exit_confirmed': True, 'reason': 'intentional_cancel' if cancelled else None,
                      'dialog_close_confirmed': True, **self.receipt_changes}
            def finish():
                if self.fixture_error:
                    raise self.fixture_error
                return result
            return SimpleNamespace(finish=finish)
        self.choose = choose

    def observer(self, journey='open-cancel'):
        value = cancel.CancellationDialogObserver(self.window, self.choose, root=self.root,
            inputs=self.inputs, folder=self.folder, open_kind=10, folder_kind=20, journey=journey, timeout=lambda: 1)
        value.attach()
        self.addCleanup(value.close)
        return value

    def invoke_open(self):
        return self.window.create_file_dialog(10, allow_multiple=True, file_types=INPUT_FILTER)

    def test_open_cancel_returns_original_none_and_restores_exact_method(self):
        observer = self.observer()
        self.assertIsNone(self.invoke_open())
        observer.assert_complete()
        self.assertEqual(self.choices[0][1]['action'], 'cancel')
        self.assertEqual(observer.snapshot()[0]['returnedKind'], 'none')
        observer.close()
        self.assertIs(self.window.create_file_dialog, self.original)

    def test_folder_cancel_returns_original_selection_then_original_empty_tuple(self):
        observer = self.observer('folder-cancel')
        self.actual = [str(path) for path in self.inputs]
        self.assertIs(self.invoke_open(), self.actual)
        self.actual = ()
        self.assertIs(self.window.create_file_dialog(20), self.actual)
        observer.assert_complete()
        self.assertEqual([row[1]['action'] for row in self.choices], ['select', 'cancel'])
        self.assertEqual(observer.snapshot()[1]['returnedKind'], 'tuple')

    def test_falsey_malformed_native_returns_are_rejected(self):
        for value in (False, 0, '', {}, {'cancelled': True}, ['private'], ('private',)):
            with self.subTest(value=value), self.assertRaises(AssertionError):
                cancel.assert_cancelled_return(value)
        for value in (None, [], ()):
            cancel.assert_cancelled_return(value)

    def test_cancel_return_with_selection_cannot_be_hidden_by_worker_receipt(self):
        observer = self.observer()
        self.actual = [str(self.inputs[0])]
        with self.assertRaises(AssertionError):
            self.invoke_open()
        with self.assertRaises(AssertionError):
            observer.assert_complete()
        self.assertFalse(observer.snapshot()[0]['actualReturnVerified'])

    def test_incomplete_worker_receipt_cannot_be_hidden_by_empty_native_return(self):
        for changes in ({'cancelled': False}, {'selected': True}, {'worker_exit_confirmed': False}):
            with self.subTest(changes=changes):
                self.receipt_changes = changes
                observer = self.observer()
                with self.assertRaises(AssertionError):
                    self.invoke_open()
                self.assertTrue(observer.snapshot()[0]['fixtureFailed'])
                observer.close()

    def test_wrong_native_call_and_extra_call_never_reach_original(self):
        observer = self.observer()
        with self.assertRaises(AssertionError):
            self.window.create_file_dialog(20)
        self.assertEqual(self.calls + self.choices, [])
        observer.close()
        observer = self.observer()
        self.invoke_open()
        with self.assertRaises(AssertionError):
            self.invoke_open()
        self.assertEqual(len(self.calls), 1)

    def test_original_exception_survives_fixture_failure(self):
        observer = self.observer()
        self.original_error = OSError('original synthetic failure')
        self.fixture_error = RuntimeError('fixture synthetic failure')
        with self.assertRaises(OSError) as caught:
            self.invoke_open()
        self.assertIs(caught.exception, self.original_error)
        self.assertTrue(observer.snapshot()[0]['originalCallFailed'])
        self.assertTrue(observer.snapshot()[0]['fixtureFailed'])

    def test_snapshot_cannot_modify_observer_records(self):
        observer = self.observer()
        self.invoke_open()
        external = observer.snapshot()
        external[0]['actualReturnVerified'] = False
        observer.assert_complete()

    def test_sanitized_cancel_diagnostics_do_not_include_paths_or_arbitrary_text(self):
        value = cancel.fixture_diagnostic({'selected': False, 'cancelled': True, 'cancel_posted': True,
            'reason': 'intentional_cancel', 'private': str(self.root), 'worker_exit_confirmed': True})
        self.assertNotIn('private', value)
        self.assertTrue(value['intentionalCancel'])
        self.assertEqual(cancel.fixture_diagnostic(None)['cancelled'], None)


if __name__ == '__main__':
    unittest.main()
