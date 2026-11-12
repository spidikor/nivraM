"""Path-length and owned-target preflight; no compiler, installer or registry writes."""
import importlib.util
from pathlib import Path, PureWindowsPath
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
with patch.object(sys, 'path', [str(SCRIPTS), *sys.path]):
    import build_installer as builder
    if sys.platform == 'win32':
        import check_installer as checker


@unittest.skipUnless(sys.platform == 'win32', 'The installer helper targets native Windows.')
class InstallerPathTests(unittest.TestCase):
    def test_longest_bundle_path_counts_utf16_units_not_unicode_codepoints(self):
        files = [{'path': 'abcd'}, {'path': 'ab\U0001f9ea\U0001f9ea'}]
        self.assertEqual(builder.max_bundle_relative_path_units(files), 6)
        self.assertEqual(builder.utf16_units('x\U0001f9ea'), 3)

    def test_empty_bundle_cannot_supply_a_path_limit(self):
        with self.assertRaises(RuntimeError):
            builder.max_bundle_relative_path_units([])

    def test_exact_259_is_allowed_and_260_is_rejected(self):
        target, subdir = PureWindowsPath('C:/owned'), 'versions/test'
        prefix = str(target / subdir) + '\\'
        room = 259 - len(prefix)
        self.assertEqual(checker.validate_install_path(target, subdir, [{'path': 'x' * room}]), 259)
        with self.assertRaisesRegex(RuntimeError, 'shorter'):
            checker.validate_install_path(target, subdir, [{'path': 'x' * (room + 1)}])

    def test_non_bmp_destination_uses_two_units_per_surrogate_pair(self):
        target, subdir = PureWindowsPath('C:/\U0001f9ea'), 'versions/test'
        prefix = str(target / subdir) + '\\'
        files = [{'path': 'x' * (259 - len(prefix))}]
        with self.assertRaisesRegex(RuntimeError, 'shorter'):
            checker.validate_install_path(target, subdir, files)

    def test_relative_and_extended_namespace_destinations_are_rejected(self):
        for target in ('relative/path', 'C:drive-relative', '\\\\?\\C:\\owned', '\\\\.\\C:\\owned'):
            with self.subTest(target=target), self.assertRaises(RuntimeError):
                checker.validate_install_path(target, 'versions/test', [{'path': 'file.dll'}])

    def test_isolated_destination_is_short_owned_sibling_with_exact_token(self):
        target = checker.isolated_target(checker.ROOT, '0123456789ab')
        self.assertEqual(target, checker.ROOT.parent / 'installer-tests' / '0123456789ab')
        self.assertNotIn('verification', target.parts)
        self.assertEqual(checker.verify_isolated_target(target, checker.ROOT, '0123456789ab'), target)
        for token in ('../outside', '0123456789', 'ABCDEF012345', '0123456789ab-extra'):
            with self.subTest(token=token), self.assertRaises(RuntimeError):
                checker.isolated_target(checker.ROOT, token)

    def test_uninstall_guard_rejects_another_target_or_redirected_directory(self):
        token = '0123456789ab'
        target = checker.isolated_target(checker.ROOT, token)
        with self.assertRaises(RuntimeError):
            checker.verify_isolated_target(target.parent / 'fedcba987654', checker.ROOT, token)
        original = Path.resolve
        def redirect(path, *args, **kwargs):
            if path == target:
                return target.parent / 'outside'
            return original(path, *args, **kwargs)
        with patch.object(Path, 'resolve', redirect), self.assertRaises(RuntimeError):
            checker.verify_isolated_target(target, checker.ROOT, token)

    def test_probe_is_fresh_contained_and_exactly_overlong(self):
        target = checker.isolated_target(checker.ROOT, '0123456789ab')
        files = [{'path': '_internal/library/file.dll'}]
        probe = checker.long_path_probe_target(target, 'versions/test', files)
        self.assertEqual(probe.parent, target.parent)
        self.assertNotEqual(probe, target)
        full = str(PureWindowsPath(probe) / 'versions/test' / files[0]['path'])
        self.assertEqual(builder.utf16_units(full), 260)
        with self.assertRaises(RuntimeError):
            checker.validate_install_path(probe, 'versions/test', files)


if __name__ == '__main__':
    unittest.main()
