"""Appearance is durable application data, independent of disposable web profiles."""
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from nivram.preferences import AppearanceStore


class AppearanceTests(unittest.TestCase):
    def test_independent_python_processes_share_application_preference_without_web_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            script = ('import sys; from nivram.preferences import AppearanceStore; '
                      's=AppearanceStore(sys.argv[1]); print(s.read()["theme"]); '
                      's.save(sys.argv[2], s.generation, 1)')
            modes = [('dark', 'light'), ('light', 'dark'), ('light', 'light')]
            for desired, expected in modes:
                result = subprocess.run([sys.executable, '-B', '-c', script, directory, desired],
                                        capture_output=True, text=True, timeout=10, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_light_default_then_dark_and_light_survive_new_store_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            first = AppearanceStore(directory)
            self.assertEqual(first.read()['theme'], 'light')
            self.assertFalse((Path(directory) / 'appearance.json').exists())
            self.assertEqual(first.save('dark', first.generation, 1)['status'], 'saved')
            second = AppearanceStore(directory)
            self.assertEqual(second.read()['theme'], 'dark')
            second.save('light', second.generation, 1)
            self.assertEqual(AppearanceStore(directory).read()['theme'], 'light')

    def test_old_page_and_out_of_order_write_cannot_replace_latest_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppearanceStore(directory)
            old = store.generation
            store.save('dark', old, 1)
            store.save('light', old, 3)
            self.assertEqual(store.save('dark', old, 2)['status'], 'superseded')
            store.begin_page()
            self.assertEqual(store.save('dark', old, 4)['status'], 'superseded')
            self.assertEqual(store.read()['theme'], 'light')

    def test_invalid_enum_and_sequence_never_write(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppearanceStore(directory)
            for theme in ('auto', '', None, {'theme': 'dark'}):
                with self.assertRaises(ValueError):
                    store.save(theme, store.generation, 1)
            for revision in (True, -1, 1.5, 9007199254740992):
                with self.assertRaises(ValueError):
                    store.save('dark', store.generation, revision)
            self.assertFalse((Path(directory) / 'appearance.json').exists())

    def test_corrupt_or_oversized_preferences_fall_back_without_overwrite(self):
        for data in (b'{', b'x' * 4097, b'{"schema":"other","theme":"dark"}'):
            with self.subTest(data_length=len(data)), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'appearance.json'
                path.write_bytes(data)
                result = AppearanceStore(directory).read()
                self.assertEqual((result['theme'], result['status']), ('light', 'unavailable'))
                self.assertEqual(path.read_bytes(), data)

    def test_failed_write_is_reported_and_does_not_replace_last_saved_preference(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppearanceStore(directory)
            store.save('dark', store.generation, 1)
            with patch('nivram.preferences.atomic_write', side_effect=OSError('private path')):
                with self.assertRaisesRegex(OSError, '^Appearance preference could not be saved.$'):
                    store.save('light', store.generation, 2)
            self.assertEqual(AppearanceStore(directory).read()['theme'], 'dark')
            self.assertEqual(store.save('dark', store.generation, 1)['status'], 'superseded')
            self.assertEqual(store.save('light', store.generation, 2)['status'], 'saved')

    def test_close_commit_fences_a_pending_older_bridge_write(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppearanceStore(directory)
            store.save_for_close({'theme': 'light', 'revision': 2, 'generation': store.generation})
            self.assertEqual(store.save('dark', store.generation, 1)['status'], 'superseded')
            self.assertEqual(AppearanceStore(directory).read()['theme'], 'light')

    def test_no_user_choice_during_startup_does_not_overwrite_saved_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppearanceStore(directory)
            store.save('dark', store.generation, 1)
            fresh = AppearanceStore(directory)
            fresh.save_for_close({'theme': 'light', 'revision': 0, 'generation': None})
            self.assertEqual(fresh.read()['theme'], 'dark')


if __name__ == '__main__':
    unittest.main()
