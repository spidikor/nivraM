from pathlib import Path
import tempfile
import unittest
from nivram.profiles import ProfileStore

EXPORT = {'width': 2000, 'marginMode': 'percent', 'margin': 2}


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ProfileStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_revisions_reopen_without_overwriting_prior_settings(self):
        one = self.store.save('Paper figure', {}, EXPORT)
        original = next(self.root.glob('*.json')).read_bytes()
        two = self.store.save('Paper figure', {'bondWidth': 1.5}, EXPORT, one['id'], 1)
        self.assertEqual(two['revision'], 2)
        self.assertEqual(self.store.list()['profiles'][0], two)
        self.assertEqual((self.root / f"{one['id']}-000001.json").read_bytes(), original)
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.store.save('Stale', {}, EXPORT, one['id'], 1)

    def test_invalid_export_does_not_write_a_revision(self):
        self.store.save('Valid', {}, EXPORT)
        before = sorted(p.name for p in self.root.iterdir())
        with self.assertRaises(ValueError):
            self.store.save('Invalid', {}, {**EXPORT, 'width': 10000})
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)

    def test_damaged_record_is_reported_and_never_overwritten(self):
        one = self.store.save('Valid', {}, EXPORT)
        target = self.root / f"{one['id']}-000001.json"
        target.write_text('[]', encoding='utf-8')
        self.assertEqual(self.store.list()['invalidRecords'], 1)
        with self.assertRaisesRegex(ValueError, 'damaged'):
            self.store.save('Replacement', {}, EXPORT)
        self.assertEqual(target.read_text(), '[]')


if __name__ == '__main__':
    unittest.main()
