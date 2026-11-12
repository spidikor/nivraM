import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nivram import naming_runtime as runtime


class NamingRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(__file__).resolve().parents[1]
        self.scratch = self.workspace / '.runtime-test-work'
        self.scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=self.scratch)
        self.root = Path(self.temporary.name).resolve()
        self.assertTrue(self.root.is_relative_to(self.scratch.resolve()))
        self.files = {
            'jre/bin/java.exe': b'test-java',
            'jre/bin/server/jvm.dll': b'test-jvm',
            'jre/lib/modules': b'test-modules',
            'jre/release': b'test-release',
            'jre/NOTICE': b'test-notice',
            'jre/legal/java.base/LICENSE': b'test-license',
            'jre/legal/java.base/ASSEMBLY_EXCEPTION': b'test-exception',
            'opsin.jar': b'test-opsin',
            'legal/OPSIN-LICENSE.txt': b'test-opsin-license',
        }
        for name, content in self.files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.manifest = {
            'schemaVersion': 1,
            'javaVersion': runtime.JAVA_VERSION,
            'javaVendor': 'Eclipse Adoptium',
            'opsinVersion': runtime.OPSIN_VERSION,
            'files': {name: {'sha256': hashlib.sha256(content).hexdigest(),
                             'size': len(content)}
                      for name, content in self.files.items()},
        }
        self.platform = patch.object(runtime.sys, 'platform', 'win32')
        self.architecture = patch.object(runtime.sysconfig, 'get_platform', return_value='win-amd64')
        self.pointer_width = patch.object(runtime.struct, 'calcsize', return_value=8)
        self.jar_pin = patch.object(runtime, 'OPSIN_SHA256', hashlib.sha256(self.files['opsin.jar']).hexdigest())
        self.platform.start()
        self.architecture.start()
        self.pointer_width.start()
        self.jar_pin.start()
        self._write_manifest()

    def _write_manifest(self):
        raw = json.dumps(self.manifest, sort_keys=True).encode('utf-8')
        (self.root / 'manifest.json').write_bytes(raw)
        if hasattr(self, 'manifest_pin'):
            self.manifest_pin.stop()
        self.manifest_pin = patch.object(runtime, 'MANIFEST_SHA256', hashlib.sha256(raw).hexdigest())
        self.manifest_pin.start()

    def tearDown(self):
        self.manifest_pin.stop()
        self.jar_pin.stop()
        self.architecture.stop()
        self.pointer_width.stop()
        self.platform.stop()
        self.assertTrue(self.root.resolve().is_relative_to(self.scratch.resolve()))
        self.temporary.cleanup()

    def test_intact_bundle_returns_only_explicit_verified_paths(self):
        result = runtime.discover_naming_runtime(self.root)
        self.assertEqual(result.status, 'available')
        self.assertEqual(Path(result.config.java_path), self.root / 'jre/bin/java.exe')
        self.assertEqual(Path(result.config.jar_path), self.root / 'opsin.jar')
        self.assertEqual(result.config.expected_sha256, runtime.OPSIN_SHA256)

    def test_missing_bundle_does_not_search_system_java(self):
        with patch.dict('os.environ', {'JAVA_HOME': str(self.root / 'untrusted')}):
            result = runtime.discover_naming_runtime(self.root / 'missing')
        self.assertEqual(result.status, 'unavailable')
        self.assertIsNone(result.config)

    def test_missing_manifest_is_unavailable(self):
        (self.root / 'manifest.json').unlink()
        result = runtime.discover_naming_runtime(self.root)
        self.assertEqual(result.status, 'unavailable')
        self.assertIsNone(result.config)

    def test_changed_manifest_is_rejected(self):
        with (self.root / 'manifest.json').open('ab') as f:
            f.write(b' ')
        self.assertEqual(runtime.discover_naming_runtime(self.root).status, 'invalid')

    def test_tampered_java_modules_and_jar_are_rejected(self):
        for name in ('jre/bin/java.exe', 'jre/bin/server/jvm.dll', 'jre/lib/modules', 'opsin.jar'):
            with self.subTest(file=name):
                original = (self.root / name).read_bytes()
                (self.root / name).write_bytes(b'X' * len(original))
                result = runtime.discover_naming_runtime(self.root)
                self.assertEqual(result.status, 'invalid')
                self.assertIsNone(result.config)
                (self.root / name).write_bytes(original)

    def test_missing_runtime_or_license_file_is_rejected(self):
        for name in ('jre/bin/server/jvm.dll', 'jre/legal/java.base/LICENSE'):
            original = (self.root / name).read_bytes()
            (self.root / name).unlink()
            self.assertEqual(runtime.discover_naming_runtime(self.root).status, 'invalid')
            (self.root / name).write_bytes(original)

    def test_unexpected_dll_is_rejected(self):
        (self.root / 'jre/bin/unexpected.dll').write_bytes(b'untrusted')
        result = runtime.discover_naming_runtime(self.root)
        self.assertEqual(result.status, 'invalid')
        self.assertIsNone(result.config)

    def test_os_errors_do_not_disclose_personal_paths(self):
        private_path = str(self.root / 'private-user-directory' / 'java.exe')
        with patch.object(runtime.os, 'scandir',
                          side_effect=PermissionError(13, 'private diagnostic', private_path)):
            result = runtime.discover_naming_runtime(self.root)
        self.assertEqual(result.status, 'invalid')
        self.assertIsNone(result.config)
        self.assertIn('PermissionError', result.message)
        self.assertNotIn(str(self.root), result.message)
        self.assertNotIn('private diagnostic', result.message)
        self.assertNotIn('private-user-directory', result.message)

    def test_checksum_failure_names_only_safe_relative_file(self):
        name = 'jre/bin/java.exe'
        (self.root / name).write_bytes(b'X' * len(self.files[name]))
        result = runtime.discover_naming_runtime(self.root)
        self.assertEqual(result.status, 'invalid')
        self.assertIn(name, result.message)
        self.assertNotIn(str(self.root), result.message)

    def test_traversal_in_manifest_is_rejected_before_file_access(self):
        self.manifest['files']['../escape.exe'] = {'sha256': '0' * 64, 'size': 0}
        self._write_manifest()
        self.assertEqual(runtime.discover_naming_runtime(self.root).status, 'invalid')

    def test_reparse_file_is_rejected(self):
        original = runtime._is_reparse
        with patch.object(runtime, '_is_reparse',
                          side_effect=lambda p: p.name == 'java.exe' or original(p)):
            self.assertEqual(runtime.discover_naming_runtime(self.root).status, 'invalid')

    def test_non_windows_host_is_unavailable(self):
        with patch.object(runtime.sys, 'platform', 'linux'):
            self.assertEqual(runtime.discover_naming_runtime(self.root).status, 'unavailable')

    def test_missing_architecture_environment_still_uses_python_target(self):
        with patch.dict('os.environ', {}, clear=True), \
                patch('platform.machine', return_value=''):
            self.assertEqual(runtime.discover_naming_runtime(self.root).status, 'available')

    def test_non_x64_python_target_is_unavailable(self):
        for target, width in (('win32', 4), ('win-arm64', 8), ('win-amd64', 4)):
            with self.subTest(target=target, pointer_bytes=width), \
                    patch.object(runtime.sysconfig, 'get_platform', return_value=target), \
                    patch.object(runtime.struct, 'calcsize', return_value=width):
                self.assertEqual(runtime.discover_naming_runtime(self.root).status, 'unavailable')

    def test_frozen_default_uses_the_resource_directory(self):
        with patch.object(runtime.sys, 'frozen', True, create=True), \
                patch.object(runtime.sys, '_MEIPASS', str(self.root), create=True):
            self.assertEqual(runtime.default_bundle_root(), self.root / 'naming-runtime')


if __name__ == '__main__':
    unittest.main()
