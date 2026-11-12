"""Fixed acceptance-scenario evidence and dispatch, without launching native UI."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from nivram import acceptance
import test_acceptance_reporting as reporting


DRAWING_PNG_CHECKS = frozenset({
    'editor-and-native-bridge-ready',
    'synthetic-hash-import-and-style',
    'native-spaced-profile-disk-readback-and-apply',
    'native-main-and-preview-png-rgba-equality',
    'native-theme-png-rgba-equality-and-document-nonmutation',
    'native-window-loop-returned',
})


def completed_report(scenario='drawing-png'):
    # Reuse the existing successful lifecycle receipt, without importing its
    # TestCase into this module's discovery namespace or running any of its tests.
    report = reporting.AcceptanceFinalResultTests().completed_report()
    report['scenario'] = scenario
    report['checks'] = [{'id': name, 'status': 'passed'} for name in sorted(DRAWING_PNG_CHECKS)]
    return report


class AcceptanceScenarioEvidenceTests(unittest.TestCase):
    def test_complete_drawing_png_fixed_checks_pass(self):
        report = completed_report()
        acceptance.finalize_acceptance_report(report)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['acceptance_failures'], [])
        self.assertEqual(report['scenario'], 'drawing-png')
        self.assertFalse(report['naming_ui_verified'])

    def test_complete_legacy_report_defaults_to_full_without_relabeling(self):
        report = reporting.AcceptanceFinalResultTests().completed_report()
        acceptance.finalize_acceptance_report(report)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['acceptance_failures'], [])
        self.assertNotIn('scenario', report)

    def test_drawing_subset_cannot_claim_full_or_unmarked_legacy_acceptance(self):
        for scenario in ('full', None):
            with self.subTest(scenario=scenario):
                report = completed_report()
                if scenario is None:
                    del report['scenario']
                else:
                    report['scenario'] = scenario
                acceptance.finalize_acceptance_report(report)
                self.assertEqual(report['status'], 'failed')
                self.assertIn('required_functional_checks_missing', report['acceptance_failures'])

    def test_each_missing_fixed_drawing_check_fails(self):
        for missing in sorted(DRAWING_PNG_CHECKS):
            with self.subTest(missing=missing):
                report = completed_report()
                report['checks'] = [row for row in report['checks'] if row['id'] != missing]
                acceptance.finalize_acceptance_report(report)
                self.assertEqual(report['status'], 'failed')
                self.assertIn('required_functional_checks_missing', report['acceptance_failures'])

    def test_observed_or_duplicate_other_check_cannot_replace_passed_check(self):
        report = completed_report()
        required = 'native-spaced-profile-disk-readback-and-apply'
        for row in report['checks']:
            if row['id'] == required:
                row['status'] = 'observed-unavailable'
        report['checks'].append({'id': 'editor-and-native-bridge-ready', 'status': 'passed'})
        acceptance.finalize_acceptance_report(report)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('required_functional_checks_missing', report['acceptance_failures'])

    def test_unknown_or_malformed_scenario_fails_with_stable_code(self):
        for scenario in ('unknown', 'DRAWING-PNG', '../full', '', None, True, [], {}):
            with self.subTest(scenario=scenario):
                report = completed_report(scenario)
                # Even every recognized functional check cannot authorize an
                # unrecognized scenario or cause an unhashable-key exception.
                report['checks'] += reporting.AcceptanceFinalResultTests().completed_report()['checks']
                acceptance.finalize_acceptance_report(report)
                self.assertEqual(report['status'], 'failed')
                self.assertIn('acceptance_scenario_unrecognized', report['acceptance_failures'])
                self.assertEqual(report['scenario'], scenario)

    def test_drawing_scenario_retains_every_lifecycle_and_policy_gate(self):
        cases = (
            ('network_policy', 'failures', 1),
            ('cleanup', 'profile_directory_retained', True),
            ('evaluation_shutdown', 'workerExited', False),
            ('dialog_fixture_shutdown', None, [{'shutdownConfirmed': False}]),
            ('download_shutdown', 'shutdown_confirmed', False),
            ('downloads_final', 'completed', 0),
            ('image_preview_shutdown_confirmed', None, False),
            ('worker_shutdown', 'ownedProcessesExited', False),
        )
        for key, field, value in cases:
            with self.subTest(key=key, field=field):
                report = completed_report()
                if field is None:
                    report[key] = value
                else:
                    report[key][field] = value
                original = copy.deepcopy(report[key])
                acceptance.finalize_acceptance_report(report)
                self.assertEqual(report['status'], 'failed')
                self.assertTrue(report['acceptance_failures'])
                self.assertNotIn('required_functional_checks_missing', report['acceptance_failures'])
                self.assertEqual(report[key], original)


class AcceptanceScenarioDispatchTests(unittest.TestCase):
    def setUp(self):
        self.report = Path('synthetic report with spaces ü.json')
        self.profile = Path('synthetic profile with spaces ü')

    def test_frozen_default_and_explicit_full_keep_legacy_command(self):
        with patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, 'executable', 'C:/synthetic bundle/nivraM.exe'):
            expected = ['C:/synthetic bundle/nivraM.exe', '--acceptance-report', str(self.report),
                        '--child', '--profile', str(self.profile)]
            self.assertEqual(acceptance.acceptance_child_command(self.report, self.profile), expected)
            self.assertEqual(acceptance.acceptance_child_command(self.report, self.profile, 'full'), expected)

    def test_frozen_child_forwards_exact_drawing_scenario(self):
        with patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, 'executable', 'C:/synthetic bundle/nivraM.exe'):
            self.assertEqual(acceptance.acceptance_child_command(self.report, self.profile, 'drawing-png'),
                             ['C:/synthetic bundle/nivraM.exe', '--acceptance-report', str(self.report),
                              '--child', '--profile', str(self.profile), '--scenario', 'drawing-png'])

    def source_commands(self):
        with patch.object(sys, 'frozen', False, create=True), \
                patch('nivram.native_dialog_fixture._source_interpreter', return_value='C:/synthetic Python/python.exe'), \
                patch('nivram.native_dialog_fixture._source_packages', return_value=Path('synthetic packages with spaces')):
            return (
                acceptance.acceptance_child_command(self.report, self.profile),
                acceptance.acceptance_child_command(self.report, self.profile, 'full'),
                acceptance.acceptance_child_command(self.report, self.profile, 'drawing-png'),
            )

    def test_source_default_and_explicit_full_keep_legacy_command(self):
        default, full, _ = self.source_commands()
        bootstrap = ("import runpy,sys;sys.path[:0]=sys.argv[1:3];"
                     "sys.argv=['nivram.acceptance',*sys.argv[3:]];"
                     "runpy.run_module('nivram.acceptance',run_name='__main__')")
        self.assertEqual(default,
                         ['C:/synthetic Python/python.exe', '-I', '-S', '-c', bootstrap,
                          str(acceptance.ROOT), str(Path('synthetic packages with spaces')),
                          '--report', str(self.report), '--child', '--profile', str(self.profile)])
        self.assertEqual(full, default)
        self.assertNotIn('--scenario', default)

    def test_source_child_only_appends_exact_drawing_scenario(self):
        default, _, drawing = self.source_commands()
        self.assertEqual(drawing, [*default, '--scenario', 'drawing-png'])

    def test_main_child_forwards_default_full_and_drawing_without_launch(self):
        for selected in (None, 'full', 'drawing-png'):
            with self.subTest(selected=selected):
                argv = ['acceptance', '--report', str(self.report), '--profile', str(self.profile), '--child']
                if selected is not None:
                    argv += ['--scenario', selected]
                with patch.object(sys, 'argv', argv), \
                        patch.object(acceptance, 'run_child', return_value=17) as child:
                    self.assertEqual(acceptance.main(), 17)
                child.assert_called_once()
                arguments = child.call_args
                self.assertEqual(arguments.args[:2], (self.report.resolve(), self.profile.resolve()))
                actual = arguments.args[2] if len(arguments.args) == 3 else arguments.kwargs.get('scenario', 'full')
                self.assertEqual(actual, selected or 'full')

    def test_cli_unknown_scenario_rejected_before_child_dispatch(self):
        with patch.object(sys, 'argv', ['acceptance', '--child', '--scenario', 'unrecognized']), \
                patch.object(acceptance, 'run_child') as child, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                acceptance.main()
        self.assertEqual(caught.exception.code, 2)
        child.assert_not_called()

    def test_supervisor_does_not_promote_unmarked_subset_to_selected_scenario(self):
        report = completed_report()
        del report['scenario']
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'report.json'
            profile = Path(directory) / 'profile'
            process = Mock()
            process.wait.return_value = 0

            def launch(*args, **kwargs):
                destination.write_text(json.dumps(report), encoding='utf-8')
                return process

            argv = ['acceptance', '--report', str(destination), '--profile', str(profile),
                    '--scenario', 'drawing-png']
            with patch.object(sys, 'argv', argv), \
                    patch.object(acceptance, 'acceptance_child_command', return_value=['synthetic-child']) as command, \
                    patch.object(acceptance, 'acceptance_child_environment', return_value={}), \
                    patch.object(acceptance.subprocess, 'Popen', side_effect=launch) as create, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(acceptance.main(), 1)
            command.assert_called_once()
            forwarded = command.call_args
            actual = forwarded.args[2] if len(forwarded.args) == 3 else forwarded.kwargs.get('scenario')
            self.assertEqual(actual, 'drawing-png')
            create.assert_called_once()
            process.kill.assert_not_called()
            saved = json.loads(destination.read_text(encoding='utf-8'))
            self.assertNotIn('scenario', saved)
            self.assertEqual(saved['status'], 'failed')
            self.assertIn('required_functional_checks_missing', saved['acceptance_failures'])


if __name__ == '__main__':
    unittest.main()
