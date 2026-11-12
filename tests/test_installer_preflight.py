"""Synthetic installer preflight tests: no installer, registry writes, or UI."""
from contextlib import ExitStack
import copy
import hashlib
import importlib.util
import json
from pathlib import Path, PureWindowsPath
import sys
import unittest
from unittest.mock import MagicMock, patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
if sys.platform == 'win32':
    spec = importlib.util.spec_from_file_location('nivram_installer_preflight_tests', SCRIPTS / 'check_installer.py')
    installer = importlib.util.module_from_spec(spec)
    with patch.object(sys, 'path', [str(SCRIPTS), *sys.path]):
        spec.loader.exec_module(installer)
else:
    installer = None


def rebind_inventory(package, manifest):
    value = hashlib.sha256(json.dumps(manifest['files'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    package['bundleInventorySha256'] = value
    package['installSubdirectory'] = f"versions/{package['version']}-{value[:16]}"
    package['maxBundleRelativePathUtf16Units'] = max(len(row['path'].encode('utf-16-le')) // 2 for row in manifest['files'])


def receipts():
    manifest = {'source': {'sha256': 'a' * 64}, 'files': [
        {'path': 'nivraM.exe', 'bytes': 12, 'sha256': 'b' * 64},
        {'path': 'nivram-worker.exe', 'bytes': 13, 'sha256': 'c' * 64},
        {'path': '_internal/example library/data.json', 'bytes': 0, 'sha256': 'd' * 64},
    ]}
    package = {'schemaVersion': 1, 'version': '0.2.0-dev.1', 'sourceSha256': 'a' * 64,
               'installerSha256': 'e' * 64, 'bundleReceiptSha256': 'f' * 64}
    rebind_inventory(package, manifest)
    return package, manifest


@unittest.skipUnless(sys.platform == 'win32', 'The installer helper targets native Windows.')
class InstallerPreflightTests(unittest.TestCase):
    def test_valid_receipt_derives_only_exact_contained_version_directory(self):
        package, manifest = receipts()
        before = copy.deepcopy((package, manifest))
        subdir = installer.validate_receipt(package, manifest)
        self.assertEqual(subdir, package['installSubdirectory'])
        target = PureWindowsPath('C:/synthetic-owned-install')
        self.assertTrue((target / subdir).is_relative_to(target))
        self.assertEqual((package, manifest), before)

    def test_unsafe_versions_are_rejected(self):
        for value in (None, 1, True, '', '../other', 'C:/other', '0.2.0/../other',
                      '0.2.0\\other', '0.2.0\x00', '0.2.0-dev/1'):
            package, manifest = receipts()
            package['version'] = value
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                installer.validate_receipt(package, manifest)

    def test_absolute_traversing_or_wrong_bundle_targets_are_rejected(self):
        package, manifest = receipts()
        for path in ('C:/other-build', '//server/share/build', '/other-build',
                     '../other-build', 'versions/../../other-build', '.',
                     package['installSubdirectory'] + '-different',
                     package['installSubdirectory'].replace('/', '\\')):
            changed = dict(package, installSubdirectory=path)
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                installer.validate_receipt(changed, manifest)

    def test_mismatched_source_inventory_and_invalid_identity_are_rejected(self):
        for field in ('sourceSha256', 'bundleInventorySha256', 'installerSha256', 'bundleReceiptSha256'):
            package, manifest = receipts()
            package[field] = ('0' * 64) if field in ('sourceSha256', 'bundleInventorySha256') else 'invalid'
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                installer.validate_receipt(package, manifest)

    def test_path_length_receipt_cannot_understate_the_bundle(self):
        package, manifest = receipts()
        actual = package['maxBundleRelativePathUtf16Units']
        for value in (None, True, 0, actual - 1, actual + 1, float(actual)):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'path-length'):
                installer.validate_receipt(dict(package, maxBundleRelativePathUtf16Units=value), manifest)

    def test_unsafe_inventory_paths_reject_even_self_consistent_hashes(self):
        for value in ('../outside', '/absolute', 'C:/absolute', '//server/share',
                      'C:relative', 'folder\\file', 'folder/./file', 'folder/../file',
                      'folder//file', 'folder/name.', 'folder/name ', 'folder/\x00name', ''):
            package, manifest = receipts()
            manifest['files'][2]['path'] = value
            rebind_inventory(package, manifest)
            with self.subTest(path=value), self.assertRaises(RuntimeError):
                installer.validate_receipt(package, manifest)

    def test_case_insensitive_duplicate_inventory_paths_rejected(self):
        package, manifest = receipts()
        manifest['files'].append(dict(manifest['files'][0], path='NIVRAM.EXE'))
        rebind_inventory(package, manifest)
        with self.assertRaises(RuntimeError):
            installer.validate_receipt(package, manifest)

    def test_bad_shapes_sizes_and_missing_runtime_rejected(self):
        for change in ('package-list', 'manifest-list', 'missing-source', 'empty-files',
                       'invalid-record', 'boolean-size', 'negative-size', 'float-size',
                       'bad-file-hash', 'missing-worker'):
            package, manifest = receipts()
            if change == 'package-list': package = []
            elif change == 'manifest-list': manifest = []
            elif change == 'missing-source': del manifest['source']
            elif change == 'empty-files': manifest['files'] = []
            elif change == 'invalid-record': manifest['files'][0] = []
            elif change == 'boolean-size': manifest['files'][0]['bytes'] = True
            elif change == 'negative-size': manifest['files'][0]['bytes'] = -1
            elif change == 'float-size': manifest['files'][0]['bytes'] = 1.5
            elif change == 'bad-file-hash': manifest['files'][0]['sha256'] = 'wrong'
            elif change == 'missing-worker': manifest['files'].pop(1)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                installer.validate_receipt(package, manifest)

    def test_registry_key_absence_is_distinct_from_value_absence(self):
        with patch.object(installer.winreg, 'OpenKey', side_effect=FileNotFoundError), \
                patch.object(installer.winreg, 'QueryValueEx') as query:
            self.assertIsNone(installer.registration())
            query.assert_not_called()
        for missing in ('DisplayVersion', 'InstallLocation', 'UninstallString'):
            key = MagicMock()
            def query_value(_key, name):
                if name == missing:
                    raise FileNotFoundError('Synthetic missing value.')
                return ('synthetic existing value', 1)
            with self.subTest(missing=missing), patch.object(installer.winreg, 'OpenKey', return_value=key), \
                    patch.object(installer.winreg, 'QueryValueEx', side_effect=query_value), \
                    self.assertRaisesRegex(RuntimeError, 'present but incomplete'):
                installer.registration()

    def test_registry_access_failure_is_not_misreported_as_absence(self):
        with patch.object(installer.winreg, 'OpenKey', side_effect=PermissionError), \
                self.assertRaises(PermissionError):
            installer.registration()

    def test_complete_current_user_registration_is_returned(self):
        expected = {'DisplayVersion': 'existing-release', 'InstallLocation': 'C:/preserved',
                    'UninstallString': '"C:/preserved/unins000.exe"'}
        key = MagicMock()
        with patch.object(installer.winreg, 'OpenKey', return_value=key) as opening, \
                patch.object(installer.winreg, 'QueryValueEx', side_effect=lambda _key, name: (expected[name], 1)):
            self.assertEqual(installer.registration(), expected)
        opening.assert_called_once_with(installer.winreg.HKEY_CURRENT_USER, installer.UNINSTALL_KEY)

    def guarded_main(self, package, manifest, *, registration=None, hashes=None):
        """Exercise actual main preflight; prohibit all native/process mutations."""
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(sys, 'argv', ['check_installer.py', '--phase', 'isolated']))
        stack.enter_context(patch.object(Path, 'read_text', side_effect=[json.dumps(package), json.dumps(manifest)]))
        stack.enter_context(patch.object(Path, 'mkdir', side_effect=AssertionError('No directory creation allowed.')))
        stack.enter_context(patch.object(installer, 'sha256', side_effect=hashes or ['e' * 64, 'f' * 64]))
        registry = stack.enter_context(patch.object(installer, 'registration', side_effect=registration))
        shortcut = stack.enter_context(patch.object(installer, 'user_shortcut', side_effect=AssertionError('No user shortcut access allowed.')))
        launch = stack.enter_context(patch.object(installer, 'execute', side_effect=AssertionError('No process launch allowed.')))
        raw_launch = stack.enter_context(patch.object(installer.subprocess, 'run', side_effect=AssertionError('No subprocess allowed.')))
        return registry, shortcut, launch, raw_launch

    def test_main_rejects_malformed_receipt_before_registry_or_process_use(self):
        for change in ('version', 'path', 'source', 'inventory', 'inventory-traversal'):
            package, manifest = receipts()
            if change == 'version': package['version'] = '../escape'
            elif change == 'path': package['installSubdirectory'] = 'C:/other-build'
            elif change == 'source': package['sourceSha256'] = '0' * 64
            elif change == 'inventory': package['bundleInventorySha256'] = '0' * 64
            else:
                manifest['files'][2]['path'] = '../outside'
                rebind_inventory(package, manifest)
            with self.subTest(change=change):
                guarded = self.guarded_main(package, manifest)
                with self.assertRaises(RuntimeError):
                    installer.main()
                for call in guarded:
                    call.assert_not_called()
                self.doCleanups()

    def test_main_rejects_byte_hash_mismatch_before_registry_or_process_use(self):
        for hashes in (['0' * 64], ['e' * 64, '0' * 64]):
            with self.subTest(hashes=hashes):
                guarded = self.guarded_main(*receipts(), hashes=hashes)
                with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                    installer.main()
                for call in guarded:
                    call.assert_not_called()
                self.doCleanups()

    def test_main_never_launches_with_incomplete_or_existing_registration(self):
        for state in (RuntimeError('A present but incomplete registration.'), {'InstallLocation': 'C:/preserved'}):
            def registration():
                if isinstance(state, Exception):
                    raise state
                return state
            with self.subTest(state=str(state)):
                registry, shortcut, launch, raw_launch = self.guarded_main(*receipts(), registration=registration)
                with self.assertRaises(RuntimeError):
                    installer.main()
                registry.assert_called_once_with()
                shortcut.assert_not_called()
                launch.assert_not_called()
                raw_launch.assert_not_called()
                self.doCleanups()

    def test_main_rejects_overlong_default_before_directory_or_native_mutations(self):
        registry, shortcut, launch, raw_launch = self.guarded_main(*receipts(), registration=lambda: None)
        with patch.object(sys, 'argv', ['check_installer.py', '--phase', 'user-install']), \
                patch.dict(installer.os.environ, {'LOCALAPPDATA': 'C:/' + 'x' * 220}), \
                self.assertRaisesRegex(RuntimeError, 'shorter'):
            installer.main()
        registry.assert_called_once_with()
        shortcut.assert_not_called()
        launch.assert_not_called()
        raw_launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
