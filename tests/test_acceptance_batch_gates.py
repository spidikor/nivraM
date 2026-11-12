"""Native batch acceptance gates and dispatch; no browser or native UI launch."""
import copy
from contextlib import redirect_stderr
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from nivram import acceptance, acceptance_click


BATCH_CHECKS = frozenset({
    'editor-and-native-bridge-ready',
    'native-batch-profile-and-synthetic-inputs',
    'native-batch-two-file-open-and-folder-selection',
    'native-batch-partial-success-and-frozen-manifest',
    'native-batch-png-and-document-integrity',
    'native-window-loop-returned',
})


def complete_batch_report():
    report = {
        'scenario': 'batch-png', 'status': 'passed', 'errors': [],
        'checks': [{'id': name, 'status': 'passed'} for name in sorted(BATCH_CHECKS)],
        'network_policy': {'status': 'installed', 'installed_before_initial_navigation': True,
                           'failures': 0, 'failure_code': None},
        'cleanup': {'profile_directory_retained': False, 'collection_exit_kind': 'Normal',
                    'managed_session': {'status': 'removed', 'native_profile_matched': True,
                        'collection_exit_observed': True, 'collection_identity_matched': True,
                        'control_disposed': True, 'runtime_cache_removed': True,
                        'browser_exit_kind': 'Normal'}},
        'evaluation_shutdown': {'workerExited': True, 'timedOut': False,
                                'reportingErrorKinds': [], 'outstandingCallMayComplete': False},
        'dialog_fixture_shutdown': [{'shutdownConfirmed': True}, {'shutdownConfirmed': True}],
        'download_shutdown': {'status': 'closed', 'shutdown_confirmed': True,
                              'active': 0, 'completed': 0, 'failures': 0, 'failure_code': None},
        'image_preview_shutdown_confirmed': True,
        'worker_shutdown': {'status': 'confirmed', 'ownedProcessesExited': True},
    }
    report['downloads_final'] = copy.deepcopy(report['download_shutdown'])
    return report


class AcceptanceBatchGateTests(unittest.TestCase):
    def finalize(self, report):
        return acceptance.finalize_acceptance_report(copy.deepcopy(report))

    def test_complete_fixed_six_checks_and_zero_downloads_pass(self):
        report = self.finalize(complete_batch_report())
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['acceptance_failures'], [])
        self.assertEqual(acceptance.SCENARIO_CHECKS['batch-png'], BATCH_CHECKS)

    def test_each_required_check_missing_or_failed_rejects(self):
        for identifier in BATCH_CHECKS:
            for omitted in (True, False):
                with self.subTest(identifier=identifier, omitted=omitted):
                    report = complete_batch_report()
                    if omitted:
                        report['checks'] = [row for row in report['checks'] if row['id'] != identifier]
                    else:
                        next(row for row in report['checks'] if row['id'] == identifier)['status'] = 'observed-unavailable'
                    result = self.finalize(report)
                    self.assertEqual(result['status'], 'failed')
                    self.assertIn('required_functional_checks_missing', result['acceptance_failures'])

    def test_subset_cannot_be_relabelled_as_full_or_drawing(self):
        for scenario in ('full', 'drawing-png'):
            with self.subTest(scenario=scenario):
                report = complete_batch_report()
                report['scenario'] = scenario
                report['download_shutdown']['completed'] = report['downloads_final']['completed'] = 1
                result = self.finalize(report)
                self.assertIn('required_functional_checks_missing', result['acceptance_failures'])

    def test_unknown_scenario_still_has_fixed_failure(self):
        report = complete_batch_report()
        report['scenario'] = 'arbitrary-batch'
        self.assertIn('acceptance_scenario_unrecognized', self.finalize(report)['acceptance_failures'])

    def test_one_download_or_nonzero_active_or_failures_rejects_each_receipt(self):
        for receipt in ('download_shutdown', 'downloads_final'):
            for changes in ({'completed': 1}, {'completed': True}, {'active': 1}, {'failures': 1},
                            {'status': 'open'}, {'shutdown_confirmed': False}, {'failure_code': 'unconfirmed'}):
                with self.subTest(receipt=receipt, changes=changes):
                    report = complete_batch_report()
                    report[receipt].update(changes)
                    self.assertIn('download_shutdown_unconfirmed', self.finalize(report)['acceptance_failures'])

    def test_exactly_two_dialog_shutdown_receipts_are_mandatory(self):
        for rows in ([], [{'shutdownConfirmed': True}], [{'shutdownConfirmed': True}] * 3,
                     [{'shutdownConfirmed': True}, {'shutdownConfirmed': False}],
                     [{'shutdownConfirmed': True}, {}], None):
            with self.subTest(rows=rows):
                report = complete_batch_report()
                report['dialog_fixture_shutdown'] = rows
                self.assertIn('dialog_fixture_shutdown_unconfirmed', self.finalize(report)['acceptance_failures'])

    def test_every_normal_managed_session_cleanup_flag_is_mandatory(self):
        changes = ({'status': 'retained'}, {'browser_exit_kind': 'Failed'},
                   *({key: False} for key in ('native_profile_matched', 'collection_exit_observed',
                       'collection_identity_matched', 'control_disposed', 'runtime_cache_removed')))
        for change in changes:
            with self.subTest(change=change):
                report = complete_batch_report()
                report['cleanup']['managed_session'].update(change)
                self.assertIn('native_session_cleanup_unconfirmed', self.finalize(report)['acceptance_failures'])
        for change in ({'profile_directory_retained': True}, {'collection_exit_kind': 'Failed'}):
            with self.subTest(change=change):
                report = complete_batch_report()
                report['cleanup'].update(change)
                self.assertIn('native_session_cleanup_unconfirmed', self.finalize(report)['acceptance_failures'])

    def test_evaluator_image_scientific_worker_and_process_cleanup_remain_mandatory(self):
        cases = (
            ('evaluation_shutdown', {'workerExited': False}, 'evaluation_shutdown_unconfirmed'),
            ('evaluation_shutdown', {'timedOut': True}, 'evaluation_shutdown_unconfirmed'),
            ('evaluation_shutdown', {'outstandingCallMayComplete': True}, 'evaluation_shutdown_unconfirmed'),
            ('evaluation_shutdown', {'reportingErrorKinds': ['OSError']}, 'evaluation_shutdown_unconfirmed'),
            ('worker_shutdown', {'ownedProcessesExited': False}, 'scientific_worker_shutdown_unconfirmed'),
            ('worker_shutdown', {'status': 'unconfirmed'}, 'scientific_worker_shutdown_unconfirmed'),
            ('process', {'exit_code': 1, 'forced_termination': False}, 'native_child_failed_or_timed_out'),
            ('process', {'exit_code': 0, 'forced_termination': True}, 'native_child_failed_or_timed_out'),
        )
        for key, change, code in cases:
            with self.subTest(key=key, change=change):
                report = complete_batch_report()
                report.setdefault(key, {}).update(change)
                self.assertIn(code, self.finalize(report)['acceptance_failures'])
        report = complete_batch_report()
        report['image_preview_shutdown_confirmed'] = False
        self.assertIn('image_worker_shutdown_unconfirmed', self.finalize(report)['acceptance_failures'])

    def test_network_policy_and_failed_report_cannot_be_bypassed(self):
        report = complete_batch_report()
        report['network_policy']['failures'] = 1
        self.assertIn('network_policy_unconfirmed', self.finalize(report)['acceptance_failures'])
        for change in ({'status': 'failed'}, {'errors': ['operation failed']}, {'report_write_failures': 1}):
            with self.subTest(change=change):
                self.assertIn('native_report_failed', self.finalize(complete_batch_report() | change)['acceptance_failures'])


class AcceptanceBatchDispatchTests(unittest.TestCase):
    def test_source_command_forwards_fixed_batch_scenario(self):
        with patch.object(sys, 'frozen', False, create=True), \
                patch('nivram.native_dialog_fixture._source_interpreter', return_value='C:/pinned/python.exe'), \
                patch('nivram.native_dialog_fixture._source_packages', return_value=Path('C:/pinned/packages')):
            command = acceptance.acceptance_child_command(Path('C:/evidence/report.json'), Path('C:/isolated/profile'), 'batch-png')
        self.assertEqual(command[-2:], ['--scenario', 'batch-png'])
        self.assertEqual(command[0], 'C:/pinned/python.exe')
        self.assertNotIn('--scenario', command[command.index('--report'):command.index('--report') + 5])

    def test_frozen_command_forwards_fixed_batch_scenario(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', 'C:/bundle/nivraM.exe'):
            command = acceptance.acceptance_child_command(Path('C:/evidence/report.json'), Path('C:/isolated/profile'), 'batch-png')
        self.assertEqual(command, ['C:/bundle/nivraM.exe', '--acceptance-report', str(Path('C:/evidence/report.json')),
                                  '--child', '--profile', str(Path('C:/isolated/profile')), '--scenario', 'batch-png'])

    def test_default_full_child_command_remains_without_scenario_argument(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', 'C:/bundle/nivraM.exe'):
            self.assertEqual(acceptance.acceptance_child_command(Path('C:/report.json'), Path('C:/profile')),
                             ['C:/bundle/nivraM.exe', '--acceptance-report', str(Path('C:/report.json')),
                              '--child', '--profile', str(Path('C:/profile'))])

    def test_child_cli_accepts_batch_and_forwards_only_fixed_value(self):
        with tempfile.TemporaryDirectory() as directory:
            report, profile = Path(directory) / 'report.json', Path(directory) / 'profile'
            with patch.object(sys, 'argv', ['acceptance', '--child', '--report', str(report), '--profile', str(profile),
                                          '--scenario', 'batch-png']), patch.object(acceptance, 'run_child', return_value=0) as run:
                self.assertEqual(acceptance.main(), 0)
                run.assert_called_once_with(report.resolve(), profile.resolve(), 'batch-png')
            self.assertFalse(report.exists())
            self.assertFalse(profile.exists())

    def test_unknown_child_cli_scenario_rejects_before_dispatch(self):
        with patch.object(sys, 'argv', ['acceptance', '--child', '--scenario', 'batch-png-arbitrary']), \
                patch.object(acceptance, 'run_child') as run, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                acceptance.main()
            self.assertEqual(caught.exception.code, 2)
            run.assert_not_called()

    def test_batch_click_selector_is_fixed_without_broadening_existing_targets(self):
        self.assertEqual(set(acceptance_click._SELECTORS), {'main', 'preview', 'batch', 'batch-cancel'})
        self.assertEqual(acceptance_click._SELECTORS['batch-cancel'], "document.getElementById('batch-export-cancel')")
        self.assertEqual(acceptance_click._SELECTORS['batch'], "document.getElementById('batch-export-button')")
        self.assertEqual(acceptance_click._SELECTORS['main'], "document.getElementById('export-button')")
        for kind in ('#batch-export-button', 'batch;arbitrary', '', None):
            with self.subTest(kind=kind), patch.object(acceptance_click, '_WindowsBackend') as backend:
                with self.assertRaisesRegex(acceptance_click.NativeClickRejected, 'kind_unrecognized'):
                    acceptance_click.click_png_button(None, None, kind)
                backend.assert_not_called()


if __name__ == '__main__':
    unittest.main()
