"""Synthetic ownership tests and real disposable children; no native GUI launch."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from nivram import native_dialog_fixture as fixture
from nivram import native_dialog_worker as worker


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    def sleep(self, value): self.now += value


class FakeWindows:
    def __init__(self, pid):
        self.owner_pid = pid
        self.pids = {10: pid, 20: pid, 21: pid, 22: pid, 99: pid+1}
        self.dialogs = [20, 99]
        self.controls = [21]
        self.parents = {20: 10, 21: 20, 22: 20}
        self.labels = {21: 'file name:', 22: 'file name:'}
        self.writes = []; self.commands = []; self.label_calls = []
        self.text = ''; self.living = True
    def alive(self): return self.living
    def pid(self, hwnd): return self.pids.get(hwnd, 0)
    def visible(self, hwnd): return True
    def enabled(self, hwnd): return True
    def class_name(self, hwnd): return 'Edit' if hwnd in (21, 22) else '#32770'
    def owner(self, hwnd): return self.parents.get(hwnd)
    def parent(self, hwnd): return self.parents.get(hwnd)
    def control_id(self, hwnd): return 0
    def windows(self, parent=None): return list(self.dialogs if parent is None else self.controls)
    def label(self, hwnd):
        self.label_calls.append(hwnd)
        return self.labels[hwnd]
    def set_text(self, hwnd, value, timeout_ms):
        self.writes.append((hwnd, value, timeout_ms)); self.text = value
    def read_text(self, hwnd, timeout_ms): return self.text
    def submit(self, hwnd, command):
        self.commands.append((hwnd, command)); return True


class DialogFixtureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='nv-dialog-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        (self.root / 'synthetic fixtures').mkdir()
        (self.root / 'instance.lock').write_bytes(b'0')
        info = self.root.stat()
        self.request = {'schemaVersion': 1, 'parentPid': os.getpid(), 'parentCreated': 123,
                        'ownerHwnd': 10, 'root': str(self.root), 'rootDevice': info.st_dev,
                        'rootInode': info.st_ino,
                        'target': str(self.root / 'synthetic fixtures' / 'ethanol ü.nivram'),
                        'timeout': .2, 'nonce': 'a' * 32}
        self.clock = Clock()
        self.backend = FakeWindows(os.getpid())

    def select(self):
        return worker.select_fixture(self.request, self.backend, clock=self.clock, sleep=self.clock.sleep)

    def test_success_uses_exact_target_then_readback_and_submit(self):
        result = self.select()
        self.assertTrue(result['selected'])
        self.assertEqual(self.backend.writes[0][:2], (21, self.request['target']))
        self.assertLessEqual(self.backend.writes[0][2], 750)
        self.assertEqual(self.backend.commands, [(20, 1)])
        self.assertEqual(self.backend.label_calls, [21])
        self.assertTrue(worker.validate_result(result, self.request['nonce'])['selected'])

    def test_csv_reference_export_uses_the_same_owned_dialog_and_exact_path(self):
        self.request['target'] = str(self.root / 'synthetic fixtures' / 'reference.csv')
        worker.validate_request(self.request, expected_parent=os.getpid())
        result = self.select()
        self.assertTrue(result['selected'])
        self.assertEqual(self.backend.writes[0][:2], (21, self.request['target']))
        self.assertEqual(self.backend.commands, [(20, 1)])
        self.assertTrue(worker.validate_result(result, self.request['nonce'])['selected'])

    def test_request_rejects_parent_mismatch_and_profile_identity_change(self):
        for changes in ({'parentPid': os.getpid()+1}, {'rootInode': self.request['rootInode']+1},
                        {'ownerHwnd': True}, {'timeout': float('nan')}, {'timeout': 36},
                        {'schemaVersion': True}, {'nonce': 'no'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                worker.validate_request(self.request | changes, expected_parent=os.getpid())

    def test_escape_nested_target_extension_and_directory_are_rejected(self):
        directory = self.root / 'synthetic fixtures' / 'directory.png'
        directory.mkdir()
        for path in (self.root / 'outside.png', self.root.parent / 'escape.png', directory,
                     self.root / 'outside.csv', self.root.parent / 'escape.csv',
                     self.root / 'synthetic fixtures' / 'nested' / 'other.csv',
                     self.root / 'synthetic fixtures' / 'payload.csv.exe',
                     self.root / 'synthetic fixtures' / 'nested' / 'other.png',
                     self.root / 'synthetic fixtures' / 'payload.exe',
                     self.root / 'synthetic fixtures' / '..' / 'escaped.png'):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                worker.validate_request(self.request | {'target': str(path)}, expected_parent=os.getpid())

    def test_reparse_directory_is_rejected_before_ui(self):
        real_stat = Path.stat
        def redirected(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            if path == self.root / 'synthetic fixtures' and kwargs.get('follow_symlinks') is False:
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info
        with patch.object(Path, 'stat', redirected), self.assertRaisesRegex(ValueError, 'redirected'):
            worker.validate_request(self.request, expected_parent=os.getpid())

    def test_missing_acceptance_profile_marker_is_rejected(self):
        (self.root / 'instance.lock').unlink()
        with self.assertRaises(OSError):
            worker.validate_request(self.request, expected_parent=os.getpid())

    def test_wrong_owner_pid_never_reads_or_mutates_any_control(self):
        self.backend.pids[10] += 1
        result = self.select()
        self.assertEqual(result['reason'], 'parent_or_owner_changed')
        self.assertEqual(self.backend.label_calls + self.backend.writes + self.backend.commands, [])

    def test_other_process_dialog_and_wrong_owner_chain_are_ignored(self):
        self.backend.parents[20] = 99
        result = self.select()
        self.assertFalse(result['selected'])
        self.assertEqual(self.backend.label_calls + self.backend.writes + self.backend.commands, [])

    def test_wrong_process_edit_never_receives_accessibility_query(self):
        self.backend.pids[21] += 1
        result = self.select()
        self.assertFalse(result['selected'])
        self.assertEqual(self.backend.label_calls + self.backend.writes, [])
        self.assertEqual(self.backend.commands, [(20, 2)])

    def test_ambiguous_dialog_or_field_fails_without_filename_write(self):
        self.backend.pids[30] = os.getpid(); self.backend.parents[30] = 10
        self.backend.dialogs.append(30)
        self.assertEqual(self.select()['reason'], 'ambiguous_owned_dialog')
        self.assertEqual(self.backend.commands, [])
        self.backend.dialogs.remove(30); self.backend.controls.append(22)
        self.assertEqual(self.select()['reason'], 'ambiguous_filename_field')
        self.assertEqual(self.backend.writes, [])

    def test_owner_changed_during_accessibility_query_prevents_write_and_cancel(self):
        def label(hwnd):
            self.backend.pids[10] += 1
            return 'file name:'
        self.backend.label = label
        self.assertFalse(self.select()['selected'])
        self.assertEqual(self.backend.writes + self.backend.commands, [])

    def test_late_accessibility_query_cannot_submit_after_deadline(self):
        def label(hwnd):
            self.clock.sleep(1)
            return 'file name:'
        self.backend.label = label
        self.assertFalse(self.select()['selected'])
        self.assertEqual(self.backend.writes, [])
        self.assertNotIn((20, 1), self.backend.commands)

    def test_filename_readback_mismatch_never_submits(self):
        self.backend.read_text = lambda *_: 'incorrect path'
        self.assertEqual(self.select()['reason'], 'filename_readback_mismatch')
        self.assertEqual(self.backend.commands, [(20, 2)])

    def test_result_rejects_extra_fields_nonce_type_and_unverified_success(self):
        good = self.select()
        for changes in ({'nonce': 'b'*32}, {'selected': 1}, {'filename_field_verified': False},
                        {'private': 'unrequested'}, {'control_count': 257}, {'stage': 'identify-field'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                worker.validate_result(good | changes, self.request['nonce'])

    def diagnostic_receipt(self, *, completed=None, error=None, shutdown=True,
                           shutdown_error=None, command_error=None, request=None):
        with patch.object(fixture, 'build_request', return_value=request or self.request):
            choice = fixture.NativeFileChoice(None, None, None, timeout=3)
        choice._supervisor = SimpleNamespace(
            run=Mock(return_value=completed, side_effect=error),
            shutdown=Mock(return_value=shutdown, side_effect=shutdown_error))
        with patch.object(fixture, 'worker_command', return_value=['PRIVATE_COMMAND'],
                          side_effect=command_error), patch.object(fixture, 'cancel_owned_dialog', return_value=True):
            choice.start()
            with self.assertRaises(RuntimeError) as caught:
                choice.finish()
        self.assertFalse(choice.thread.is_alive())
        receipt = caught.exception.dialog_result
        self.assertFalse(receipt['selected'])
        self.assertNotIn('PRIVATE', json.dumps(receipt))
        return receipt

    def test_diagnostic_nonzero_exit_rejects_success_payload_and_keeps_code(self):
        for code in (1, 23, 0xC0000005):
            with self.subTest(code=code):
                completed = subprocess.CompletedProcess(['PRIVATE_COMMAND'], code,
                    json.dumps(self.select()), 'PRIVATE_STDERR')
                receipt = self.diagnostic_receipt(completed=completed)
                self.assertEqual(receipt['reason'], 'worker_failure')
                self.assertEqual(receipt['parent_phase'], 'worker_exit')
                self.assertEqual(receipt['error_kind'], 'nonzero_exit')
                self.assertEqual(receipt['worker_returncode'], code)
                self.assertTrue(receipt['worker_exit_confirmed'])

    def test_diagnostic_json_decode_failure_is_distinct_from_worker_exit(self):
        completed = subprocess.CompletedProcess(['PRIVATE_COMMAND'], 0,
                                               'PRIVATE_MOLECULE not json', 'PRIVATE_STDERR')
        receipt = self.diagnostic_receipt(completed=completed)
        self.assertEqual(receipt['reason'], 'worker_failure')
        self.assertEqual(receipt['parent_phase'], 'result_decode')
        self.assertEqual(receipt['error_kind'], 'json_decode_error')
        self.assertEqual(receipt['worker_returncode'], 0)

    def test_diagnostic_result_validation_rejects_nonce_and_unverified_success(self):
        for changes in ({'nonce': 'b' * 32}, {'filename_field_verified': False},
                        {'private': 'PRIVATE_MOLECULE'}):
            with self.subTest(changes=changes):
                completed = subprocess.CompletedProcess(['PRIVATE_COMMAND'], 0,
                    json.dumps(self.select() | changes), 'PRIVATE_STDERR')
                receipt = self.diagnostic_receipt(completed=completed)
                self.assertEqual(receipt['reason'], 'worker_failure')
                self.assertEqual(receipt['parent_phase'], 'result_validate')
                self.assertEqual(receipt['error_kind'], 'value_error')
                self.assertEqual(receipt['worker_returncode'], 0)

    def test_diagnostic_supervisor_errors_use_only_fixed_kinds_and_known_facts(self):
        from nivram.worker_processes import WorkerProcessError
        private_type = type('PRIVATE_EXCEPTION_CLASS', (Exception,), {})
        cases = (
            (subprocess.TimeoutExpired(['PRIVATE_COMMAND'], 3), 'worker_timeout', 'timeout'),
            (fixture.WorkerCancelled('PRIVATE_TEXT'), 'worker_cancelled', 'cancelled'),
            (fixture.WorkerOutputLimit('PRIVATE_TEXT'), 'worker_output_limit', 'output_limit'),
            (WorkerProcessError('PRIVATE_MASKED_CAUSE'), 'worker_failure', 'worker_process_error'),
            (UnicodeDecodeError('utf-8', b'PRIVATE\xff', 7, 8, 'PRIVATE_TEXT'), 'worker_failure', 'unicode_decode_error'),
            (OSError('PRIVATE_PATH'), 'worker_failure', 'os_error'),
            (TypeError('PRIVATE_TEXT'), 'worker_failure', 'type_error'),
            (private_type('PRIVATE_TEXT'), 'worker_failure', 'other_error'),
        )
        for error, reason, kind in cases:
            with self.subTest(kind=kind):
                receipt = self.diagnostic_receipt(error=error)
                self.assertEqual(receipt['reason'], reason)
                self.assertEqual(receipt['parent_phase'], 'supervise')
                self.assertEqual(receipt['error_kind'], kind)
                self.assertIsNone(receipt['worker_returncode'])

    def test_diagnostic_prelaunch_failures_have_no_worker_exit(self):
        receipt = self.diagnostic_receipt(command_error=OSError('PRIVATE_PATH'))
        self.assertEqual(receipt['parent_phase'], 'command')
        self.assertEqual(receipt['error_kind'], 'os_error')
        self.assertIsNone(receipt['worker_returncode'])
        receipt = self.diagnostic_receipt(request=self.request | {'timeout': float('nan')})
        self.assertEqual(receipt['parent_phase'], 'request_encode')
        self.assertEqual(receipt['error_kind'], 'value_error')
        self.assertIsNone(receipt['worker_returncode'])

    def test_diagnostic_cleanup_failure_retains_available_primary_metadata(self):
        completed = subprocess.CompletedProcess(['PRIVATE_COMMAND'], 0, 'PRIVATE not json', '')
        receipt = self.diagnostic_receipt(completed=completed, shutdown=False)
        self.assertEqual(receipt['reason'], 'worker_cleanup_unconfirmed')
        self.assertEqual(receipt['parent_phase'], 'result_decode')
        self.assertEqual(receipt['error_kind'], 'json_decode_error')
        self.assertEqual(receipt['worker_returncode'], 0)
        self.assertFalse(receipt['worker_exit_confirmed'])
        self.assertFalse(receipt['cleanup']['shutdownConfirmed'])

    def test_diagnostic_cleanup_exception_is_bounded_and_never_passes(self):
        completed = subprocess.CompletedProcess(['PRIVATE_COMMAND'], 0, json.dumps(self.select()), '')
        receipt = self.diagnostic_receipt(completed=completed, shutdown_error=OSError('PRIVATE_PATH'))
        self.assertEqual(receipt['reason'], 'worker_cleanup_unconfirmed')
        self.assertEqual(receipt['parent_phase'], 'result_validate')
        self.assertIsNone(receipt['error_kind'])
        self.assertEqual(receipt['worker_returncode'], 0)
        self.assertEqual(receipt['cleanup_error_kind'], 'os_error')
        self.assertFalse(receipt['worker_exit_confirmed'])
        self.assertFalse(receipt['cleanup']['shutdownConfirmed'])
        self.assertEqual(receipt['cleanup']['cleanup_error_kind'], 'os_error')

    def test_diagnostic_valid_worker_failure_preserves_worker_stage(self):
        result = self.select() | {'selected': False, 'reason': 'native_operation_failed',
                                  'stage': 'identify-field'}
        completed = subprocess.CompletedProcess(['PRIVATE_COMMAND'], 0, json.dumps(result), '')
        receipt = self.diagnostic_receipt(completed=completed)
        self.assertEqual(receipt['reason'], 'native_operation_failed')
        self.assertEqual(receipt['stage'], 'identify-field')
        self.assertEqual(receipt['parent_phase'], 'result_validate')
        self.assertIsNone(receipt['error_kind'])
        self.assertEqual(receipt['worker_returncode'], 0)

    def make_choice(self, script, timeout=.2):
        request = self.request | {'timeout': timeout}
        with patch.object(fixture, 'build_request', return_value=request):
            choice = fixture.NativeFileChoice(None, None, None, timeout=timeout)
        commands = patch.object(fixture, 'worker_command', return_value=[fixture._source_interpreter(), '-u', '-c', script])
        cancel = patch.object(fixture, 'cancel_owned_dialog', return_value=True)
        commands.start(); cancel.start()
        self.addCleanup(commands.stop); self.addCleanup(cancel.stop)
        self.addCleanup(choice.close)
        return choice

    def test_real_hung_child_is_stopped_and_reaped_with_bounded_receipt(self):
        choice = self.make_choice('import time; time.sleep(60)')
        started = time.monotonic()
        choice.start()
        with self.assertRaises(RuntimeError) as caught:
            choice.finish()
        self.assertLess(time.monotonic()-started, 3)
        self.assertEqual(caught.exception.dialog_result['reason'], 'worker_timeout')
        self.assertTrue(caught.exception.dialog_result['cleanup']['shutdownConfirmed'])
        self.assertEqual(choice._supervisor.active_count, 0)

    def test_selected_stdout_does_not_pass_before_child_exit(self):
        good = self.select()
        script = 'import sys,time; sys.stdout.write(' + repr(json.dumps(good)) + '); sys.stdout.flush(); time.sleep(60)'
        choice = self.make_choice(script).start()
        with self.assertRaises(RuntimeError) as caught:
            choice.finish()
        self.assertFalse(caught.exception.dialog_result['selected'])
        self.assertEqual(caught.exception.dialog_result['reason'], 'worker_timeout')

    def test_start_then_caller_failure_close_reaps_without_finish(self):
        choice = self.make_choice('import time; time.sleep(60)', timeout=10).start()
        deadline = time.monotonic()+3
        while not choice._supervisor.active_count and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(choice._supervisor.active_count, 1)
        self.assertTrue(choice.close()['shutdownConfirmed'])
        self.assertTrue(choice.close()['shutdownConfirmed'])
        self.assertEqual(choice._supervisor.active_count, 0)

    def test_valid_child_success_exits_and_later_close_never_cancels_dialog(self):
        script = 'import sys; sys.stdout.write(' + repr(json.dumps(self.select())) + ')'
        choice = self.make_choice(script, timeout=3).start()
        self.assertTrue(choice.finish()['worker_exit_confirmed'])
        with patch.object(fixture, 'cancel_owned_dialog') as cancel:
            self.assertTrue(choice.close()['shutdownConfirmed'])
            cancel.assert_not_called()

    def test_malformed_and_oversized_worker_outputs_fail_and_exit(self):
        for script in ('print("not-json")', 'import sys; sys.stdout.write("x"*20000)'):
            with self.subTest(script=script):
                choice = self.make_choice(script, timeout=3).start()
                with self.assertRaises(RuntimeError) as caught:
                    choice.finish()
                self.assertFalse(caught.exception.dialog_result['selected'])
                self.assertTrue(choice.close()['shutdownConfirmed'])

    def test_close_before_start_keeps_supervisor_closed_and_starts_no_child(self):
        choice = self.make_choice('raise AssertionError("must not start")')
        self.assertTrue(choice.close()['shutdownConfirmed'])
        with patch('nivram.worker_processes.subprocess.Popen') as spawn:
            choice.start()
            with self.assertRaises(RuntimeError): choice.finish()
            spawn.assert_not_called()

    def test_close_during_process_registration_remains_unconfirmed_until_exit(self):
        choice = self.make_choice('import time; time.sleep(60)', timeout=10)
        entered, release = threading.Event(), threading.Event()
        real_spawn = subprocess.Popen
        def delayed_spawn(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Synthetic spawn gate timed out.')
            return real_spawn(*args, **kwargs)
        with patch('nivram.worker_processes.subprocess.Popen', delayed_spawn):
            choice.start()
            self.assertTrue(entered.wait(2))
            try:
                self.assertFalse(choice.close(timeout=.05)['shutdownConfirmed'])
                self.assertFalse(choice.snapshot()['shutdownConfirmed'])
            finally:
                release.set()
            choice.thread.join(3)
            self.assertFalse(choice.thread.is_alive())
            self.assertTrue(choice.close()['shutdownConfirmed'])
            self.assertEqual(choice._supervisor.active_count, 0)

    @unittest.skipUnless(os.name == 'nt', 'Windows parent process identity')
    def test_real_worker_dispatch_refuses_invalid_owner_without_native_ui(self):
        request = self.request | {'parentCreated': worker.process_created(os.getpid()),
                                  'ownerHwnd': 0x7FFFFFFF, 'timeout': 2}
        supervisor = fixture.WorkerSupervisor(max_workers=1)
        try:
            result = supervisor.run(fixture.worker_command(), input_text=json.dumps(request),
                                    timeout_seconds=5, max_stdout_bytes=8192, max_stderr_bytes=4096)
            self.assertEqual(result.returncode, 0)
            outcome = worker.validate_result(json.loads(result.stdout), request['nonce'])
            self.assertFalse(outcome['selected'])
            self.assertEqual(outcome['reason'], 'parent_or_owner_changed')
            self.assertEqual(outcome['control_count'], 0)
        finally:
            self.assertTrue(supervisor.shutdown())

    @unittest.skipUnless(os.name == 'nt', 'Windows direct-interpreter nesting')
    def test_nested_isolated_base_launcher_preserves_pinned_packages(self):
        script = '''import json,os,sys
from pathlib import Path
sys.path[:0]=sys.argv[1:3]
from nivram.native_dialog_fixture import _source_packages,worker_command,WorkerSupervisor
from nivram.native_dialog_worker import process_created
request=json.loads(sys.argv[3]);request.update(parentPid=os.getpid(),parentCreated=process_created(os.getpid()),ownerHwnd=0x7fffffff)
supervisor=WorkerSupervisor(max_workers=1)
try:
 result=supervisor.run(worker_command(),input_text=json.dumps(request),timeout_seconds=5,max_stdout_bytes=8192,max_stderr_bytes=4096)
 print(json.dumps({'isolated':sys.flags.isolated,'noSite':sys.flags.no_site,'basePrefix':str(Path(sys.prefix)/'Lib'/'site-packages')!=sys.argv[2],'packagesMatch':str(_source_packages())==sys.argv[2],'workerExit':result.returncode,'workerResult':json.loads(result.stdout)}))
finally:
 if not supervisor.shutdown():raise RuntimeError('Nested worker cleanup unconfirmed')
'''
        supervisor = fixture.WorkerSupervisor(max_workers=1)
        try:
            result = supervisor.run([fixture._source_interpreter(), '-I', '-S', '-c', script,
                                     str(Path(fixture.__file__).resolve().parents[1]),
                                     str(fixture._source_packages()), json.dumps(self.request)],
                                    timeout_seconds=8, max_stdout_bytes=8192, max_stderr_bytes=4096)
            self.assertEqual(result.returncode, 0, result.stderr)
            outcome = json.loads(result.stdout)
            self.assertEqual((outcome['isolated'], outcome['noSite']), (1, 1))
            self.assertTrue(outcome['basePrefix'])
            self.assertTrue(outcome['packagesMatch'])
            self.assertEqual(outcome['workerExit'], 0)
            self.assertEqual(outcome['workerResult']['reason'], 'parent_or_owner_changed')
        finally:
            self.assertTrue(supervisor.shutdown())

    def test_frozen_dispatch_uses_console_sibling_worker(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', 'C:/bundle/nivraM.exe'):
            self.assertEqual(Path(fixture.worker_command()[0]), Path('C:/bundle/nivram-worker.exe'))
            self.assertEqual(fixture.worker_command()[1:], ['--native-dialog-fixture'])


if __name__ == '__main__':
    unittest.main()
