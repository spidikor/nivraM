from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from nivram import acceptance
from nivram.acceptance import acceptance_child_command, acceptance_child_environment, close_dialog_fixtures, persist_cleanup_checkpoint, record_failure, write_report


class AcceptanceReportingTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows sharing semantics')
    def test_temporary_reader_sharing_lock_does_not_lose_atomic_checkpoint(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'acceptance.json'
            write_report(target, {'stage': 'before'})
            # Read/write sharing permitted; deletion/atomic replacement denied.
            handle = kernel.CreateFileW(str(target), 0x80000000, 3, None, 3, 0x80, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
            held = True
            attempts = []
            replace = os.replace
            def competing_reader(source, destination):
                nonlocal held
                attempts.append(1)
                if len(attempts) == 2:
                    self.assertEqual(json.loads(target.read_text()), {'stage': 'before'})
                    self.assertTrue(kernel.CloseHandle(handle))
                    held = False
                return replace(source, destination)
            try:
                with patch('nivram.acceptance.os.replace', side_effect=competing_reader):
                    write_report(target, {'stage': 'after'})
                self.assertEqual(len(attempts), 2)
                self.assertEqual(json.loads(target.read_text()), {'stage': 'after'})
                self.assertEqual(list(Path(directory).iterdir()), [target])
            finally:
                if held:
                    kernel.CloseHandle(handle)

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing retry')
    def test_persistent_sharing_failure_is_bounded_and_preserves_old_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'acceptance.json'
            write_report(target, {'stage': 'before'})
            error = PermissionError('synthetic sharing failure')
            error.winerror = 32
            with patch('nivram.acceptance.os.replace', side_effect=error) as replace, patch('nivram.acceptance.time.sleep') as sleep:
                with self.assertRaises(PermissionError):
                    write_report(target, {'stage': 'after'})
            self.assertEqual(replace.call_count, 6)
            self.assertAlmostEqual(sum(call.args[0] for call in sleep.call_args_list), .31)
            self.assertEqual(json.loads(target.read_text()), {'stage': 'before'})
            self.assertEqual(list(Path(directory).iterdir()), [target])
            report = {'errors': []}
            record_failure(report, error)
            self.assertEqual(report['failures'][0]['winerror'], 32)
            self.assertNotIn('synthetic', json.dumps(report))

    def test_frozen_relative_filename_and_failure_context_survive_cleanup_updates(self):
        report = {'errors': [], 'stage': 'exact-profile-png-preview-validating',
                  'last_completed_check': 'native-profile-save-reopen-apply-preserves-graph',
                  'evaluation': {'sequence': 74, 'stage': 'poll-native-ui', 'status': 'completed'}}
        try:
            exec(compile('raise TypeError("private returned value")', 'nivram/acceptance.py', 'exec'))
        except TypeError as error:
            record_failure(report, error)
        failure = report['failures'][0]
        self.assertEqual(failure['harnessLine'], 1)
        self.assertEqual(failure['stage'], 'exact-profile-png-preview-validating')
        report['stage'] = 'cleanup'
        report['evaluation']['sequence'] = 75
        report['last_completed_check'] = 'native-window-loop-returned'
        self.assertEqual(failure['evaluation']['sequence'], 74)
        self.assertEqual(failure['last_completed_check'], 'native-profile-save-reopen-apply-preserves-graph')
        self.assertNotIn('private', json.dumps(report))

    def test_failed_atomic_replace_preserves_previous_valid_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'acceptance.json'
            write_report(target, {'status': 'failed', 'stage': 'before-save'})
            with patch('nivram.acceptance.os.replace', side_effect=OSError('synthetic write failure')):
                with self.assertRaises(OSError):
                    write_report(target, {'status': 'failed', 'stage': 'after-save'})
            self.assertEqual(json.loads(target.read_text()), {'status': 'failed', 'stage': 'before-save'})
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_bad_serialization_never_truncates_prior_report(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'acceptance.json'
            write_report(target, {'status': 'failed', 'errors': ['original']})
            with self.assertRaises(ValueError):
                write_report(target, {'bad': float('nan')})
            self.assertEqual(json.loads(target.read_text())['errors'], ['original'])
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_fixture_cleanup_failure_does_not_skip_other_owned_children(self):
        called = []
        class Broken:
            def close(self):
                called.append('broken')
                raise OSError('private value must not appear')
        class Healthy:
            def close(self):
                called.append('healthy')
                return {'shutdownConfirmed': True}
        outcomes = close_dialog_fixtures([Broken(), Healthy()])
        self.assertEqual(called, ['broken', 'healthy'])
        self.assertEqual(outcomes, [{'shutdownConfirmed': False, 'errorKind': 'OSError'}, {'shutdownConfirmed': True}])

    def test_cleanup_checkpoint_failure_returns_and_preserves_original_error(self):
        report = {'status': 'failed', 'errors': []}
        record_failure(report, ValueError('private script or path'))
        with patch('nivram.acceptance.write_report', side_effect=OSError('private path')):
            self.assertFalse(persist_cleanup_checkpoint(Path('unused'), report))
        self.assertEqual(report['failures'], [{'kind': 'ValueError'}])
        self.assertEqual(report['last_report_write_error_kind'], 'OSError')
        self.assertEqual(report['report_write_failures'], 1)
        self.assertNotIn('private', json.dumps(report))

    def test_failure_metadata_is_saved_before_failing_cleanup_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'acceptance.json'
            report = {'status': 'passed', 'errors': []}
            record_failure(report, ValueError('arbitrary JS content'))
            self.assertTrue(persist_cleanup_checkpoint(target, report))
            with patch('nivram.acceptance.write_report', side_effect=OSError('synthetic')):
                self.assertFalse(persist_cleanup_checkpoint(target, report))
            saved = json.loads(target.read_text())
            self.assertEqual(saved['status'], 'failed')
            self.assertEqual(saved['failures'], [{'kind': 'ValueError'}])
            self.assertNotIn('arbitrary', json.dumps(saved))

    @unittest.skipUnless(os.name == 'nt', 'Windows direct child process identity')
    def test_source_supervisor_launches_actual_child_with_pinned_packages(self):
        from nivram.native_dialog_fixture import _source_packages
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'nivram'
            package.mkdir()
            (package / '__init__.py').write_text('', encoding='utf-8')
            (package / 'acceptance.py').write_text(
                'import os,sys,json,webview,subprocess\n'
                'worker=subprocess.check_output([sys.executable,"-c","import webview;print(webview.__file__)"],timeout=5,text=True).strip()\n'
                'print(json.dumps({"pid":os.getpid(),"ppid":os.getppid(),"argv":sys.argv[1:],"webview":webview.__file__,"worker_webview":worker}))\n',
                encoding='utf-8')
            report, profile = root / 'report with spaces.json', root / 'profile with spaces'
            with patch('nivram.acceptance.ROOT', root):
                command = acceptance_child_command(report, profile)
                environment = acceptance_child_environment()
            self.assertEqual(command[1:3], ['-I', '-S'])
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       creationflags=subprocess.CREATE_NO_WINDOW, env=environment)
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=3)
                raise
            self.assertEqual(process.returncode, 0, stderr.decode(errors='replace'))
            result = json.loads(stdout)
            self.assertEqual(result['pid'], process.pid)
            self.assertEqual(result['ppid'], os.getpid())
            self.assertEqual(result['argv'], ['--report', str(report), '--child', '--profile', str(profile)])
            self.assertTrue(Path(result['webview']).is_relative_to(_source_packages()))
            self.assertEqual(result['webview'], result['worker_webview'])

    def test_source_child_environment_replaces_user_python_search_paths(self):
        with patch.dict(os.environ, {'PYTHONPATH': 'untrusted-path', 'PYTHONHOME': 'untrusted-home'}):
            environment = acceptance_child_environment()
        self.assertNotIn('PYTHONHOME', environment)
        self.assertNotIn('untrusted', environment['PYTHONPATH'])
        self.assertEqual(environment['PYTHONNOUSERSITE'], '1')

    def test_frozen_supervisor_dispatches_same_executable(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', 'C:/bundle/nivraM.exe'):
            self.assertEqual(acceptance_child_command(Path('report.json'), Path('profile')),
                             ['C:/bundle/nivraM.exe', '--acceptance-report', 'report.json', '--child', '--profile', 'profile'])


class AcceptanceFinalResultTests(unittest.TestCase):
    def completed_report(self):
        completed = [
            'analysis-does-not-mutate-document', 'editor-and-native-bridge-ready',
            'exact-profile-png-preview-and-pixel-inspection', 'export-does-not-mutate-document',
            'native-9999-png-save-decoded-margins-opacity-and-nonmutation',
            'native-dark-mode-preserves-png-and-document', 'native-inspect-and-save-diagnostics',
            'native-large-png-detail-accept-preserve-and-one-undo', 'native-large-png-overview-navigation-only',
            'native-open-and-editor-readback', 'native-png-image-file-selected',
            'native-png-metadata-review-accept-preserve-and-one-undo',
            'native-profile-save-reopen-apply-preserves-graph', 'native-save-as-and-disk-readback',
            'native-save-existing-preserves-graph', 'native-window-loop-returned',
            'properties-panel', 'svg-image-blob-to-png', 'synthetic-import-through-real-ui-and-loopback',
        ]
        report = {
            'status': 'passed', 'errors': [],
            'checks': [{'id': name, 'status': 'passed'} for name in completed]
                      + [{'id': 'naming-panel', 'status': 'observed-unavailable'}],
            'naming_ui_verified': False, 'naming_roundtrip_verified': False,
            'network_policy': {'status': 'installed', 'installed_before_initial_navigation': True,
                               'failures': 0, 'failure_code': None},
            'cleanup': {'profile_directory_retained': False, 'collection_exit_kind': 'Normal',
                        'managed_session': {'status': 'removed', 'native_profile_matched': True,
                            'collection_exit_observed': True, 'collection_identity_matched': True,
                            'control_disposed': True, 'runtime_cache_removed': True,
                            'browser_exit_kind': 'Normal'}},
            'evaluation_shutdown': {'workerExited': True, 'timedOut': False,
                                    'reportingErrorKinds': [], 'outstandingCallMayComplete': False},
            'dialog_fixture_shutdown': [{'shutdownConfirmed': True}],
            'download_shutdown': {'status': 'closed', 'shutdown_confirmed': True,
                                  'active': 0, 'completed': 1, 'failures': 0, 'failure_code': None},
            'image_preview_shutdown_confirmed': True,
            'worker_shutdown': {'status': 'confirmed', 'ownedProcessesExited': True},
        }
        report['downloads_final'] = copy.deepcopy(report['download_shutdown'])
        return report

    def run_supervisor(self, report, *, raw=None, missing=False, exit_code=0, timed_out=False):
        # Exercise actual main receipt loading, aggregation, writing and return.
        # Only the child-process boundary is fake; no native UI is launched.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination, profile = root / 'report.json', root / 'profile'
            child = Mock()
            child.wait.side_effect = ([subprocess.TimeoutExpired('synthetic', 180), exit_code]
                                      if timed_out else [exit_code])
            def launch(*args, **kwargs):
                if not missing:
                    destination.write_text(raw if raw is not None else json.dumps(report), encoding='utf-8')
                return child
            with patch.object(sys, 'argv', ['acceptance', '--report', str(destination), '--profile', str(profile)]), \
                    patch.object(acceptance, 'acceptance_child_command', return_value=['synthetic-child']), \
                    patch.object(acceptance, 'acceptance_child_environment', return_value={}), \
                    patch.object(acceptance.subprocess, 'Popen', side_effect=launch) as create, \
                    redirect_stdout(io.StringIO()):
                result = acceptance.main()
            create.assert_called_once()
            self.assertEqual(child.kill.call_count, int(timed_out))
            return result, json.loads(destination.read_text(encoding='utf-8'))

    def test_retained_profile_cannot_pass_after_child_exit_zero(self):
        report = self.completed_report()
        report['cleanup']['profile_directory_retained'] = True
        report['cleanup']['managed_session'].update(status='retained', runtime_cache_removed=False,
                                                    reason='runtime_collection_exit_unconfirmed')
        result, saved = self.run_supervisor(report)
        self.assertEqual(result, 1)
        self.assertEqual(saved['status'], 'failed')
        self.assertIn('native_session_cleanup_unconfirmed', saved['acceptance_failures'])
        self.assertEqual(saved['cleanup'], report['cleanup'])

    def test_both_browser_exit_receipts_must_explicitly_report_normal(self):
        cases = [('Failed', 'Normal'), ('Failed', 'missing'), ('missing', 'Normal'),
                 (None, 'Normal'), ({}, 'Normal'), ('Normal', 'missing'),
                 ('Normal', 'Failed'), ('Normal', None), ('Normal', True),
                 ('Normal', {}), ('Normal', []), ('Normal', 'Unexpected')]
        for authoritative, observer in cases:
            with self.subTest(authoritative=authoritative, observer=observer):
                report = self.completed_report()
                cleanup = report['cleanup']
                session = cleanup['managed_session']
                if authoritative == 'missing':
                    del session['browser_exit_kind']
                else:
                    session['browser_exit_kind'] = authoritative
                if observer == 'missing':
                    del cleanup['collection_exit_kind']
                else:
                    cleanup['collection_exit_kind'] = observer
                result, saved = self.run_supervisor(report)
                self.assertEqual(result, 1)
                self.assertEqual(saved['status'], 'failed')
                self.assertIn('native_session_cleanup_unconfirmed', saved['acceptance_failures'])
                self.assertEqual(saved['cleanup'], cleanup)

    def test_both_download_receipts_must_confirm_completion_independently(self):
        changes = ({'status': 'failed'}, {'status': 'unknown'}, {'shutdown_confirmed': False},
                   {'active': 1}, {'active': None}, {'active': False}, {'failures': 1},
                   {'failures': False}, {'failure_code': 'late_failure'},
                   {'completed': 0}, {'completed': None}, {'completed': True})
        for receipt in ('download_shutdown', 'downloads_final'):
            for changed in changes:
                with self.subTest(receipt=receipt, changed=changed):
                    report = self.completed_report()
                    report[receipt].update(changed)
                    result, saved = self.run_supervisor(report)
                    self.assertEqual(result, 1)
                    self.assertEqual(saved['status'], 'failed')
                    self.assertIn('download_shutdown_unconfirmed', saved['acceptance_failures'])
                    self.assertEqual(saved[receipt], report[receipt])
            for missing in (True, False):
                with self.subTest(receipt=receipt, missing=missing):
                    report = self.completed_report()
                    if missing:
                        del report[receipt]
                    else:
                        report[receipt] = 'unknown'
                    self.assertEqual(self.run_supervisor(report)[0], 1)

    def test_final_policy_refresh_rejects_a_failure_after_earlier_snapshot(self):
        from nivram.webview_policy import PolicyReceipt
        policy = PolicyReceipt('http://127.0.0.1:12345')
        policy._update(status='installed', installed_before_initial_navigation=True)
        report = self.completed_report()
        report['network_policy'] = policy.snapshot()
        policy._increment('failures')
        policy._update(status='failed', failure_code='late_synthetic_failure')
        self.assertEqual(report['network_policy']['status'], 'installed')
        acceptance.finalize_acceptance_report(report, policy=policy)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('network_policy_unconfirmed', report['acceptance_failures'])
        self.assertEqual(report['network_policy'], policy.snapshot())

    def test_failed_final_policy_snapshot_preserves_original_error_without_private_text(self):
        report = self.completed_report()
        report.update(status='failed', errors=['Original synthetic failure.'])
        policy = Mock()
        policy.snapshot.side_effect = OSError('private path or native exception detail')
        acceptance.finalize_acceptance_report(report, policy=policy)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['errors'], ['Original synthetic failure.'])
        self.assertIn('network_policy_unconfirmed', report['acceptance_failures'])
        self.assertEqual(report['network_policy_snapshot_error_kind'], 'OSError')
        self.assertNotIn('private', json.dumps(report))

    def test_failed_or_missing_critical_receipt_cannot_pass(self):
        cases = [
            ('network_policy', {'status': 'failed', 'failures': 1}),
            ('network_policy', {'installed_before_initial_navigation': False}),
            ('cleanup', {'managed_session': {'status': 'removed'}}),
            ('cleanup', {'collection_exit_kind': 'Failed'}),
            ('evaluation_shutdown', {'workerExited': False}),
            ('evaluation_shutdown', {'timedOut': True}),
            ('evaluation_shutdown', {'reportingErrorKinds': ['OSError']}),
            ('dialog_fixture_shutdown', [{'shutdownConfirmed': False}]),
            ('download_shutdown', {'shutdown_confirmed': False}),
            ('download_shutdown', {'active': 1}),
            ('download_shutdown', {'completed': 0}),
            ('download_shutdown', {'failures': 1}),
            ('image_preview_shutdown_confirmed', False),
            ('worker_shutdown', {'status': 'unconfirmed', 'ownedProcessesExited': False}),
            ('checks', []),
        ]
        for key, changed in cases:
            with self.subTest(key=key, changed=changed):
                report = self.completed_report()
                if isinstance(changed, dict):
                    report[key].update(changed)
                else:
                    report[key] = changed
                result, saved = self.run_supervisor(report)
                self.assertEqual(result, 1)
                self.assertEqual(saved['status'], 'failed')
        for key in ('network_policy', 'cleanup', 'download_shutdown', 'evaluation_shutdown',
                    'dialog_fixture_shutdown', 'worker_shutdown', 'image_preview_shutdown_confirmed'):
            with self.subTest(missing=key):
                report = self.completed_report()
                del report[key]
                self.assertEqual(self.run_supervisor(report)[0], 1)

    def test_malformed_nested_receipts_fail_without_crashing(self):
        for key in ('network_policy', 'cleanup', 'download_shutdown', 'evaluation_shutdown',
                    'dialog_fixture_shutdown', 'worker_shutdown', 'checks'):
            with self.subTest(key=key):
                report = self.completed_report()
                report[key] = 'private untrusted diagnostic'
                result, saved = self.run_supervisor(report)
                self.assertEqual(result, 1)
                self.assertEqual(saved['status'], 'failed')

    def test_missing_or_invalid_report_saves_bounded_failure(self):
        for options in ({'missing': True}, {'raw': '{private broken JSON'},
                        {'raw': '[]'}, {'raw': 'null'}, {'raw': '{"private": NaN}'}):
            with self.subTest(options=options):
                result, saved = self.run_supervisor({}, **options)
                self.assertEqual(result, 1)
                self.assertEqual(saved['status'], 'failed')
                self.assertEqual(saved['process']['exit_code'], 0)
                self.assertNotIn('private', json.dumps(saved))

    def test_complete_optional_naming_unavailable_run_still_passes(self):
        result, saved = self.run_supervisor(self.completed_report())
        self.assertEqual(result, 0)
        self.assertEqual(saved['status'], 'passed')
        self.assertFalse(saved['naming_ui_verified'])
        self.assertFalse(saved['naming_roundtrip_verified'])

    def test_missing_export_check_fails_even_after_native_loop_and_cleanup(self):
        report = self.completed_report()
        report['checks'] = [row for row in report['checks'] if row['id'] != 'svg-image-blob-to-png']
        result, saved = self.run_supervisor(report)
        self.assertEqual(result, 1)
        self.assertIn('required_functional_checks_missing', saved['acceptance_failures'])

    def test_final_result_is_idempotent_and_keeps_original_observations(self):
        report = self.completed_report()
        report['cleanup']['profile_directory_retained'] = True
        before = copy.deepcopy(report['cleanup'])
        acceptance.finalize_acceptance_report(report)
        first = copy.deepcopy(report)
        acceptance.finalize_acceptance_report(report)
        self.assertEqual(report, first)
        self.assertEqual(report['cleanup'], before)

    def test_supervisor_failure_never_promotes_complete_child_report(self):
        for options in ({'exit_code': 3}, {'timed_out': True}):
            with self.subTest(options=options):
                result, saved = self.run_supervisor(self.completed_report(), **options)
                self.assertEqual(result, 1)
                self.assertEqual(saved['status'], 'failed')

    def test_original_failure_evidence_survives_final_aggregation(self):
        report = self.completed_report()
        report.update(status='failed', errors=['Synthetic original failure.'],
                      failures=[{'kind': 'TypeError', 'stage': 'initial-export'}])
        result, saved = self.run_supervisor(report)
        self.assertEqual(result, 1)
        self.assertEqual(saved['errors'], report['errors'])
        self.assertEqual(saved['failures'], report['failures'])


class PngExportObservationTests(unittest.TestCase):
    def test_failure_diagnostic_keeps_only_bounded_stage_and_type_evidence(self):
        returned = {'ok': False, 'width': 640, 'height': 480, 'pngSignature': True,
                    'stage': 'dark-export', 'errorKind': 'SecurityError',
                    'error': 'private path or exception message', 'pngBase64': 'private PNG bytes',
                    'svg': 'private SVG content'}
        self.assertEqual(acceptance.png_export_observation(returned), {
            'resultType': 'dict', 'ok': False, 'width': 640, 'height': 480,
            'pngSignature': True, 'stage': 'dark-export', 'errorKind': 'SecurityError'})
        self.assertIn('private', returned['error'])

    def test_untrusted_error_names_stages_and_invalid_numeric_types_are_not_copied(self):
        returned = {'ok': 'private', 'width': True, 'height': float('inf'),
                    'pngSignature': 1, 'stage': 'private-content', 'errorKind': 'private-content'}
        observation = acceptance.png_export_observation(returned)
        self.assertEqual(observation, {'resultType': 'dict', 'ok': None, 'width': None, 'height': None,
                                      'pngSignature': None, 'stage': 'unknown', 'errorKind': 'OtherError'})
        self.assertNotIn('private', json.dumps(observation))
        self.assertEqual(acceptance.png_export_observation(None), {'resultType': 'NoneType'})


if __name__ == '__main__':
    unittest.main()
