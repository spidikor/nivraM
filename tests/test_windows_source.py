"""Offline control-flow checks only: no installs, downloads or native launches."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_environment as gate
import windows_source as setup


class InterpreterTests(unittest.TestCase):
    def supported(self):
        stack = ExitStack()
        for target, name, value in [
            (gate.sys, 'platform', 'win32'), (gate.sys, 'implementation', SimpleNamespace(name='cpython')),
            (gate.sys, 'version_info', (3, 12, 14)),
        ]:
            stack.enter_context(patch.object(target, name, value))
        stack.enter_context(patch.object(gate.sysconfig, 'get_platform', return_value='win-amd64'))
        stack.enter_context(patch.object(gate.sysconfig, 'get_config_var', return_value=0))
        stack.enter_context(patch.object(gate.platform, 'machine', return_value='AMD64'))
        stack.enter_context(patch.object(gate.struct, 'calcsize', return_value=8))
        stack.enter_context(patch.object(gate.sys, 'getwindowsversion', create=True, return_value=SimpleNamespace(build=22000)))
        return stack

    def test_accepts_exact_supported_runtime(self):
        with self.supported():
            gate.require_environment()

    def test_rejects_all_wrong_runtime_dimensions(self):
        cases = [(gate.sys, 'platform', 'linux'), (gate.sys, 'implementation', SimpleNamespace(name='pypy')),
                 (gate.sys, 'version_info', (3,12,13)), (gate.sys, 'version_info', (3,12,15)),
                 (gate.sys, 'version_info', (3,13,0)), (gate.sys, 'version_info', (3,11,9)),
                 (gate.sys, 'version_info', (3,14,0))]
        for obj, name, value in cases:
            with self.subTest(name=name, value=value), self.supported(), patch.object(obj, name, value):
                with self.assertRaises(RuntimeError): gate.require_environment()
        for obj, name, value in [(gate.sysconfig,'get_platform','win-arm64'),
                                 (gate.sysconfig,'get_config_var',1), (gate.platform,'machine','ARM64'),
                                 (gate.struct,'calcsize',4), (gate.sys,'getwindowsversion',SimpleNamespace(build=21999))]:
            with self.subTest(name=name), self.supported(), patch.object(obj,name,create=True,return_value=value):
                with self.assertRaises(RuntimeError): gate.require_environment()


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'source'
        (self.root / 'scripts').mkdir(parents=True)
        for name in ('signed-rdkit-wheel-pin.json','signed-rdkit-lock.json','signed_rdkit_wheel.py'):
            shutil.copyfile(ROOT / 'scripts' / name, self.root / 'scripts' / name)
        shutil.copyfile(ROOT / 'requirements-windows-lock.txt', self.root / 'requirements-windows-lock.txt')
        self.env = Path(self.tmp.name) / 'fresh env'
        self.pin = json.loads((self.root / 'scripts/signed-rdkit-wheel-pin.json').read_text())
        self.wheel = Path(self.tmp.name) / self.pin['wheelFilename']
        self.wheel.write_bytes(b'Synthetic inert wheel fixture, never installed')
        # Synthetic fixture pin only; production pins are never edited.
        self.pin['wheelSha256'] = hashlib.sha256(self.wheel.read_bytes()).hexdigest()
        (self.root / 'scripts/signed-rdkit-wheel-pin.json').write_text(json.dumps(self.pin))
        self.calls = self.enterContext(patch.object(setup.subprocess, 'run'))
        self.enterContext(patch.object(setup, 'require_environment'))

    def test_missing_and_altered_wheel_rejected_before_effects(self):
        for wheel in [self.wheel.with_name('missing.whl'), self.wheel]:
            if wheel == self.wheel: wheel.write_bytes(b'altered')
            with self.assertRaises(RuntimeError): setup.setup(self.root,self.env,wheel)
            self.assertFalse(self.env.exists()); self.calls.assert_not_called()

    def test_rejects_recipe_lock_and_dependency_drift_before_effects(self):
        for name in ['scripts/signed_rdkit_wheel.py','scripts/signed-rdkit-lock.json','requirements-windows-lock.txt']:
            path = self.root / name; old = path.read_bytes()
            path.write_bytes(b'{}' if name.endswith('.json') else b'drift')
            with self.assertRaises(RuntimeError): setup.setup(self.root,self.env,self.wheel)
            path.write_bytes(old)
            self.assertFalse(self.env.exists()); self.calls.assert_not_called()

    def test_existing_environment_and_data_never_overwritten(self):
        self.env.mkdir(); sentinel = self.env / 'project.nivram'; sentinel.write_bytes(b'user data')
        with self.assertRaises(FileExistsError): setup.setup(self.root,self.env,self.wheel)
        self.assertEqual(sentinel.read_bytes(),b'user data'); self.calls.assert_not_called()

    def test_source_descendant_and_ancestor_rejected(self):
        for target in [self.root, self.root / '.venv', self.root.parent]:
            with self.assertRaises(RuntimeError): setup.setup(self.root,target,self.wheel)
        self.calls.assert_not_called()

    def test_redirected_environment_rejected(self):
        destination = Path(self.tmp.name) / 'other'; destination.mkdir()
        self.env.symlink_to(destination, target_is_directory=True)
        with self.assertRaises(RuntimeError): setup.setup(self.root,self.env,self.wheel)
        self.assertEqual(list(destination.iterdir()), []); self.calls.assert_not_called()

    def test_pinned_install_order_and_receipt(self):
        with patch.dict(setup.os.environ, {'PIP_INDEX_URL':'https://invalid.example', 'PYTHONPATH':'untrusted'}):
            setup.setup(self.root,self.env,self.wheel)
        commands = [call.args[0] for call in self.calls.call_args_list]
        self.assertEqual(len(commands),4)
        self.assertEqual(commands[0],[sys.executable,'-I','-B','-m','venv',str(self.env)])
        self.assertEqual(commands[1],[str(self.env/'Scripts/python.exe'),'-I','-B','-m','pip','--isolated',
            '--disable-pip-version-check','install','--require-virtualenv','--only-binary=:all:','--index-url','https://pypi.org/simple',
            str(self.env/'nivram-setup-input'/self.wheel.name),'-r',str(self.env/'nivram-setup-input/requirements-windows-lock.txt')])
        for call in self.calls.call_args_list:
            self.assertTrue(call.kwargs['check'])
            self.assertNotIn('PIP_INDEX_URL',call.kwargs['env']); self.assertNotIn('PYTHONPATH',call.kwargs['env'])
            self.assertEqual(call.kwargs['env']['PIP_CONFIG_FILE'],setup.os.devnull)
        self.assertTrue((self.env/setup.READY).is_file())

    def test_failures_stop_without_success_marker_or_cleanup(self):
        for failure_at in range(4):
            env = self.env.with_name('fail-'+str(failure_at))
            self.calls.reset_mock()
            self.calls.side_effect = [None]*failure_at + [subprocess.CalledProcessError(17,['synthetic'])]
            with self.assertRaises(subprocess.CalledProcessError): setup.setup(self.root,env,self.wheel)
            self.assertEqual(self.calls.call_count,failure_at+1)
            self.assertTrue(env.is_dir()); self.assertFalse((env/setup.READY).exists())

    def test_launch_refuses_missing_or_wrong_receipt(self):
        self.env.mkdir()
        with self.assertRaises(FileNotFoundError): setup.launch(self.root,self.env)
        (self.env/setup.READY).write_text('{}')
        with self.assertRaises(RuntimeError): setup.launch(self.root,self.env)
        self.calls.assert_not_called()

    def test_launch_checks_before_desktop_and_propagates_failure(self):
        setup.setup(self.root,self.env,self.wheel); self.calls.reset_mock()
        setup.launch(self.root,self.env)
        self.assertIn('check', self.calls.call_args_list[0].args[0])
        self.assertEqual(self.calls.call_args_list[1].args[0][-1],str(self.root/'run_desktop.py'))
        self.calls.reset_mock(); self.calls.side_effect = subprocess.CalledProcessError(23,['synthetic'])
        with patch.object(setup,'ROOT',self.root):
            self.assertEqual(setup.main(['launch','--environment',str(self.env)]),23)
        self.assertEqual(self.calls.call_count,1)

    def test_no_global_runtime_validation(self):
        with patch.object(setup.sys,'prefix',str(self.env)), patch.object(setup.sys,'base_prefix',str(self.env)):
            with self.assertRaises(RuntimeError): setup.check_runtime(self.root,self.env)

    def test_invalid_platform_rejected_before_any_effect(self):
        with patch.object(setup, 'require_environment', side_effect=RuntimeError('wrong platform')):
            with self.assertRaises(RuntimeError): setup.setup(self.root,self.env,self.wheel)
        self.assertFalse(self.env.exists()); self.calls.assert_not_called()

    def test_staged_wheel_tamper_prevents_pip(self):
        original = setup.shutil.copyfileobj
        def corrupt(source, target):
            original(source, target); target.write(b'changed during staging')
        with patch.object(setup.shutil, 'copyfileobj', side_effect=corrupt):
            with self.assertRaises(RuntimeError): setup.setup(self.root,self.env,self.wheel)
        self.assertEqual(self.calls.call_count,1)
        self.assertFalse((self.env/setup.READY).exists())

    def test_runtime_reuses_existing_lock_and_native_evidence_gates(self):
        import build_evidence
        import signed_rdkit_evidence
        with patch.object(setup.sys,'prefix',str(self.env)), patch.object(setup.sys,'base_prefix','different'), \
                patch.object(build_evidence,'verify_lock') as lock, \
                patch.object(signed_rdkit_evidence,'verify_installed') as signed:
            setup.check_runtime(self.root,self.env)
            lock.assert_called_once_with(self.root); signed.assert_called_once_with(self.root)
            lock.side_effect = RuntimeError('lock differs'); signed.reset_mock()
            with self.assertRaises(RuntimeError): setup.check_runtime(self.root,self.env)
            signed.assert_not_called()
            lock.side_effect = None; signed.side_effect = RuntimeError('native differs')
            with self.assertRaises(RuntimeError): setup.check_runtime(self.root,self.env)

    def test_complete_lock_drift_rejected_before_any_effect(self):
        path = self.root / 'requirements-windows-lock.txt'; original = path.read_bytes()
        for altered in [original+b'\nextra-package==1.0\n',
                        original+b'\n--extra-index-url https://invalid.example/simple\n',
                        original.replace(b'bottle==0.13.4',b'bottle==0.13.5'),
                        original.replace(b'\r\n',b'\n')]:
            with self.subTest(altered=altered[-70:]):
                path.write_bytes(altered)
                with self.assertRaisesRegex(RuntimeError,'Complete Windows dependency lock'):
                    setup.setup(self.root,self.env,self.wheel)
                self.assertFalse(self.env.exists()); self.calls.assert_not_called()
        path.write_bytes(original)

    def test_source_lock_swap_after_admission_never_reaches_pip(self):
        def change_after_venv(*args, **kwargs):
            with (self.root/'requirements-windows-lock.txt').open('ab') as handle:
                handle.write(b'\n--extra-index-url https://invalid.example/simple\n')
        self.calls.side_effect = change_after_venv
        with self.assertRaisesRegex(RuntimeError,'Complete Windows dependency lock'):
            setup.setup(self.root,self.env,self.wheel)
        self.assertEqual(self.calls.call_count,1)
        self.assertTrue(self.env.exists()); self.assertFalse((self.env/setup.READY).exists())

    def test_staged_lock_tamper_prevents_pip(self):
        original = setup.shutil.copyfileobj
        def corrupt_lock(source,target):
            original(source,target)
            if Path(target.name).name == 'requirements-windows-lock.txt': target.write(b'\nextra-package==1.0')
        with patch.object(setup.shutil,'copyfileobj',side_effect=corrupt_lock):
            with self.assertRaisesRegex(RuntimeError,'Complete Windows dependency lock'):
                setup.setup(self.root,self.env,self.wheel)
        self.assertEqual(self.calls.call_count,1); self.assertFalse((self.env/setup.READY).exists())

    def test_hashing_never_uses_read_bytes_for_caller_wheel(self):
        original = Path.read_bytes
        def bounded(path):
            if path.suffix == '.whl': raise AssertionError('unbounded wheel read')
            return original(path)
        with patch.object(Path,'read_bytes',bounded):
            setup.setup(self.root,self.env,self.wheel)
        self.assertTrue((self.env/setup.READY).exists())
        self.assertEqual((self.env/'nivram-setup-input/requirements-windows-lock.txt').read_bytes(),
                         (self.root/'requirements-windows-lock.txt').read_bytes())

    def test_wrappers_select_desktop_helper_and_no_legacy_install(self):
        for name, action in [('SETUP-WINDOWS.cmd','setup'),('START-NIVRAM.cmd','launch')]:
            text = (ROOT / name).read_text()
            self.assertIn('windows_source.py '+action+' %*',text)
            self.assertIn('exit /b %errorlevel%',text)
            self.assertNotIn('requirements.txt',text); self.assertNotIn('run_nivram.py',text)


if __name__ == '__main__': unittest.main()
