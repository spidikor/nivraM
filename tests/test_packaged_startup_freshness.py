"""Packaged-startup evidence freshness with synthetic files and no app launch."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'scripts' / 'check_packaged_startup.py'


class PackagedStartupFreshnessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='nivram-startup-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'application'
        self.script = self.root / 'scripts' / SOURCE.name
        self.script.parent.mkdir(parents=True)
        self.script.write_bytes(SOURCE.read_bytes())
        self.executable = self.root / 'dist' / 'nivraM' / 'nivraM.exe'
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b'synthetic non-executable fixture')
        self.old_report = self.root / 'verification' / 'desktop-packaged-smoke.json'
        self.old_report.parent.mkdir()
        self.old_report.write_text('{"status":"passed","marker":"OLD"}', encoding='utf-8')
        self.old_profile = self.root.parent / 'packaged-smoke-profile'
        self.old_profile.mkdir()
        self.sentinel = self.old_profile / 'keep.txt'
        self.sentinel.write_bytes(b'preserve previous profile')
        self.calls = []

    def runner(self, *, report='{"status":"passed","marker":"CURRENT"}', code=0):
        def run(command, **kwargs):
            self.assertEqual(Path(command[0]), self.executable)
            requested_report = Path(command[command.index('--smoke-report') + 1])
            profile = Path(command[command.index('--data-dir') + 1])
            self.calls.append({'report': requested_report, 'profile': profile,
                               'report_existed': requested_report.exists(),
                               'profile_existed': profile.exists(), 'options': kwargs})
            if report is not None:
                requested_report.parent.mkdir(parents=True, exist_ok=True)
                requested_report.write_text(report, encoding='utf-8')
            return subprocess.CompletedProcess(command, code)
        return run

    def invoke(self, runner):
        # The only process boundary is patched. No executable or native UI runs.
        with patch.object(subprocess, 'run', side_effect=runner), \
                patch.object(subprocess, 'CREATE_NO_WINDOW', 0, create=True), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                runpy.run_path(str(self.script), run_name='__main__')
            except SystemExit as error:
                return error.code
            except (RuntimeError, ValueError, TypeError, KeyError):
                return 1
        return 0

    def test_old_passed_report_cannot_pass_a_current_zero_exit_without_report(self):
        original = self.old_report.read_bytes()
        self.assertNotEqual(self.invoke(self.runner(report=None)), 0)
        self.assertEqual(self.old_report.read_bytes(), original)
        self.assertEqual(self.sentinel.read_bytes(), b'preserve previous profile')

    def test_each_run_uses_fresh_unique_report_and_profile_paths(self):
        original = self.old_report.read_bytes()
        for _ in range(2):
            self.assertEqual(self.invoke(self.runner()), 0)
        self.assertEqual(len(self.calls), 2)
        for row in self.calls:
            self.assertFalse(row['report_existed'])
            self.assertFalse(row['profile_existed'])
            self.assertEqual(row['report'].parent, row['profile'].parent)
            self.assertNotEqual(row['report'], self.old_report)
            self.assertNotEqual(row['profile'], self.old_profile)
            self.assertEqual(row['options']['timeout'], 90)
        self.assertNotEqual(self.calls[0]['report'].parent, self.calls[1]['report'].parent)
        self.assertEqual(self.old_report.read_bytes(), original)
        self.assertEqual(self.sentinel.read_bytes(), b'preserve previous profile')

    def test_nonzero_exit_cannot_pass_even_with_a_current_passed_report(self):
        self.assertNotEqual(self.invoke(self.runner(code=7)), 0)

    def test_missing_invalid_or_nonpassed_report_fails(self):
        for report in (None, '', '{broken', 'null', '[]', '{}',
                       '{"status":"failed"}', '{"status":true}'):
            with self.subTest(report=report):
                self.assertNotEqual(self.invoke(self.runner(report=report)), 0)

    def test_import_is_safe_and_does_not_launch_or_allocate_a_run(self):
        before = set(self.root.parent.rglob('*'))
        with patch.object(subprocess, 'run', side_effect=AssertionError('Import must not launch')):
            runpy.run_path(str(self.script), run_name='startup_import_test')
        self.assertEqual(set(self.root.parent.rglob('*')), before)

    def test_current_report_retains_executable_identity_and_reduced_environment(self):
        with patch.dict('os.environ', {'PYTHONPATH': 'UNTRUSTED', 'PYTHONHOME': 'UNTRUSTED',
                                      'VIRTUAL_ENV': 'UNTRUSTED'}):
            self.assertEqual(self.invoke(self.runner()), 0)
        row = self.calls[0]
        receipt = json.loads((row['report'].parent / 'run.json').read_text(encoding='utf-8'))
        expected = hashlib.sha256(self.executable.read_bytes()).hexdigest()
        self.assertEqual(receipt['executableSha256'], expected)
        self.assertEqual(receipt['executableSha256After'], expected)
        self.assertEqual(receipt['exitCode'], 0)
        self.assertEqual(receipt['status'], 'passed')
        self.assertEqual(receipt['report'], row['report'].name)
        for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'):
            self.assertNotIn(key, row['options']['env'])

    def test_executable_change_during_run_invalidates_a_passed_report(self):
        write_report = self.runner()
        def changed(command, **kwargs):
            result = write_report(command, **kwargs)
            self.executable.write_bytes(b'changed synthetic executable')
            return result
        self.assertNotEqual(self.invoke(changed), 0)


if __name__ == '__main__':
    unittest.main()
