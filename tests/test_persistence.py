import errno
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nivram import persistence
from nivram.persistence import ProjectStore, SaveConflict, validate_project


def project(width=2000):
    return {'schema': 'nivram/1', 'version': '0.1.0-alpha.1',
            'graph': {'atoms': [], 'bonds': []}, 'style': {},
            'export': {'width': width, 'marginMode': 'percent', 'margin': 2}}


class ProjectEncodingBoundTests(unittest.TestCase):
    LIMIT = 1024

    @staticmethod
    def expanded_project():
        document = project()
        metadata = [0] * 160
        for _ in range(20):
            metadata = [metadata]
        document['analysis'] = metadata
        return document

    def test_oversized_pretty_encoding_stops_before_consuming_iterator_tail(self):
        document = self.expanded_project()
        compact = json.dumps(document, separators=(',', ':')).encode('utf-8')
        self.assertLess(len(compact), self.LIMIT)
        original = json.JSONEncoder.iterencode
        observed = {'bytes': 0, 'crossed': False, 'tail_consumed': False}

        def watched(encoder, value, _one_shot=False):
            for chunk in original(encoder, value, _one_shot=_one_shot):
                if observed['crossed']:
                    observed['tail_consumed'] = True
                    raise AssertionError('Encoding continued after crossing the byte budget.')
                observed['bytes'] += len(chunk.encode('utf-8'))
                observed['crossed'] = observed['bytes'] > self.LIMIT
                yield chunk

        with (patch.object(persistence, 'MAX_PROJECT_BYTES', self.LIMIT),
              patch.object(json.JSONEncoder, 'iterencode', watched),
              patch('nivram.chemistry.graph_to_mol') as native):
            with self.assertRaisesRegex(ValueError, 'Project exceeds the 4 MB local document limit.'):
                validate_project(document)
        self.assertTrue(observed['crossed'])
        self.assertFalse(observed['tail_consumed'])
        native.assert_not_called()

    def test_valid_unicode_formatting_is_identical_at_inclusive_byte_limit(self):
        document = project()
        document['analysis'] = {'label': 'caf\u00e9 \u03b2 \U0001f9ea',
                                'escaped': '\t\n\\"', 'nested': [True, None, 1.25]}
        expected = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8')
        with patch.object(persistence, 'MAX_PROJECT_BYTES', len(expected)):
            self.assertEqual(persistence._encode(document), expected)
            self.assertEqual(validate_project(document), document)
        with patch.object(persistence, 'MAX_PROJECT_BYTES', len(expected) - 1):
            with self.assertRaisesRegex(ValueError, 'Project exceeds the 4 MB local document limit.'):
                persistence._encode(document)

    def test_multibyte_chunk_must_fit_remaining_utf8_bytes(self):
        self.assertEqual(len(json.dumps('\u00e9', ensure_ascii=False)), 3)
        with patch.object(persistence, 'MAX_PROJECT_BYTES', 3):
            with self.assertRaisesRegex(ValueError, 'Project exceeds the 4 MB local document limit.'):
                persistence._encode('\u00e9')
        with patch.object(persistence, 'MAX_PROJECT_BYTES', 4):
            self.assertEqual(persistence._encode('\u00e9'), '"\u00e9"'.encode('utf-8'))

    def test_rejected_open_save_and_autosave_leave_original_files_unchanged(self):
        document = self.expanded_project()
        compact = json.dumps(document, separators=(',', ':')).encode('utf-8')
        self.assertLess(len(compact), self.LIMIT)
        with tempfile.TemporaryDirectory(prefix='project-encoding-') as directory:
            root = Path(directory)
            store = ProjectStore(root / 'recovery')
            source = root / 'untrusted.nivram'
            source.write_bytes(compact)
            saved = root / 'existing.nivram'
            receipt = store.save(saved, project())
            before = {path.name: path.read_bytes() for path in root.iterdir()}
            with patch.object(persistence, 'MAX_PROJECT_BYTES', self.LIMIT):
                operations = (
                    lambda: store.open(source),
                    lambda: store.save(root / 'new.nivram', document),
                    lambda: store.save(saved, document, receipt['disk_hash']),
                    lambda: store.autosave(document, store.recovery_context()['generation'], 0),
                )
                for operation in operations:
                    with self.assertRaisesRegex(ValueError, 'Project exceeds the 4 MB local document limit.'):
                        operation()
                    self.assertEqual({path.name: path.read_bytes() for path in root.iterdir()}, before)
            self.assertFalse((root / 'recovery').exists())


class PersistenceTests(unittest.TestCase):
    def test_near_limit_project_autosave_can_be_recovered(self):
        doc = project()
        doc['analysis'] = ['x' * 90] * 40100
        validate_project(doc)
        receipt = self.store.autosave(doc, self.generation, 0)
        self.assertEqual(self.store.recover(receipt['id'])['document'], doc)

    def test_malformed_recovery_record_does_not_hide_valid_copies(self):
        receipt = self.store.autosave(project(), self.generation, 0)
        for index, content in enumerate(('[]', 'null', '{"schema":"nivram-recovery/1"}')):
            path = self.root / 'recovery' / (f'999999999999999999{index}-' + 'a' * 32 + '.json')
            path.write_text(content, encoding='utf-8')
        self.assertEqual([entry['id'] for entry in self.store.list_recovery()], [receipt['id']])

    def setUp(self):
        temporary_root = Path(__file__).resolve().parents[2] / '.nv'
        temporary_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='ps-', dir=temporary_root)
        self.root = Path(self.temp.name)
        self.store = ProjectStore(self.root / 'recovery')
        self.generation = self.store.recovery_context()['generation']
        self.path = self.root / 'molecule ü.nivram'

    def tearDown(self):
        self.temp.cleanup()

    def test_roundtrip_and_conflict_preserves_newer_file(self):
        receipt = self.store.save(self.path, project())
        self.assertEqual(self.store.open(self.path)['document'], project())
        self.path.write_text(json.dumps(project(1200)), encoding='utf-8')
        with self.assertRaises(SaveConflict):
            self.store.save(self.path, project(1600), receipt['disk_hash'])
        self.assertEqual(json.loads(self.path.read_text())['export']['width'], 1200)

    def test_external_replacement_before_writer_acquisition_is_retained(self):
        import ctypes
        from nivram import conditional_save as adapter
        from test_save_integration import FileTransactionModel
        receipt = self.store.save(self.path, project())
        competing = json.dumps(project(1200)).encode('utf-8')
        attempts = []
        backup = self.path.with_suffix('.nivram.bak'); backup.write_bytes(b'previous backup')
        def external_writer_before_acquisition():
            competitor = self.root / 'external-newer.nivram'
            competitor.write_bytes(competing)
            os.replace(competitor, self.path)
            attempts.append('foreign publication acknowledged')
        model = FileTransactionModel(self.path, before_open=external_writer_before_acquisition)
        # The prior failing os.replace interleaving is preserved in storage-before.
        # The new protocol acquires its writer before its authoritative read.
        # Exercise that boundary and assert the real competing replacement ran.
        # Native exclusion is modeled here; it requires separate native acceptance.
        with (patch.object(adapter, '_Win32', return_value=model),
              patch.object(ctypes, 'WinDLL', side_effect=AssertionError('Native execution forbidden'), create=True)):
            with self.assertRaises(SaveConflict):
                self.store.save(self.path, project(1600), receipt['disk_hash'])
        self.assertEqual(attempts, ['foreign publication acknowledged'])
        self.assertIn('writer_open', model.events)
        self.assertNotIn('write', model.events); self.assertNotIn('commit', model.events)
        self.assertEqual(self.path.read_bytes(), competing)
        self.assertEqual(backup.read_bytes(), b'previous backup')

    def test_external_replacement_after_final_check_is_not_overwritten(self):
        """Native Windows regression: actual protected readback and replacement.

        This is intentionally distinct from the modeled pre-acquisition test.
        Every adapter operation delegates to the real reviewed Windows API.
        The target handle remains open; a separate native helper must cover
        the closed-target/precommit boundary with another process.
        """
        from nivram import conditional_save as adapter
        receipt = self.store.save(self.path, project())
        old = self.path.read_bytes()
        competing = json.dumps(project(1200)).encode('utf-8')
        competitor = self.root / 'external-newer.nivram'
        competitor.write_bytes(competing)
        native, attempts = adapter._Win32(), []

        class ReadbackBoundary:
            def __init__(self): self.reads = 0
            def __getattr__(self, name): return getattr(native, name)
            def read(self, handle, limit):
                data = native.read(handle, limit)
                self.reads += 1
                if self.reads == 2:
                    # The complete staged readback finished; commit has not run.
                    try:
                        os.replace(competitor, self_path)
                    except OSError as error:
                        attempts.append(error.winerror)
                    else:
                        attempts.append(None)
                        raise AssertionError('A foreign replacement bypassed the protected writer.')
                return data

        self_path = self.path
        boundary = ReadbackBoundary()
        with patch.object(adapter, '_Win32', return_value=boundary):
            result = self.store.save(self.path, project(1600), receipt['disk_hash'])
        self.assertEqual(boundary.reads, 2, 'The real protected readback boundary was not reached.')
        self.assertEqual(len(attempts), 1, 'The real competing replacement was not attempted exactly once.')
        self.assertIn(attempts[0], (32, 6800), 'Expected sharing violation or transactional conflict.')
        self.assertEqual(result['status'], 'saved')
        self.assertEqual(self.store.open(self.path)['document'], project(1600))
        self.assertEqual(competitor.read_bytes(), competing)
        self.assertEqual(self.path.with_suffix('.nivram.bak').read_bytes(), old)

    def test_replace_failure_retains_original_and_cleans_temporary(self):
        receipt = self.store.save(self.path, project())
        before = self.path.read_bytes()
        with patch('nivram.persistence.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.store.save(self.path, project(3000), receipt['disk_hash'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_atomic_replace_valid_long_filename_without_expanding_component(self):
        from nivram.persistence import atomic_write
        # A legal destination component must not become an illegal temporary
        # component merely because the writer duplicates its complete name.
        directory = self.root
        if os.name == 'nt':
            # Isolate the component limit from the separate full-path limit.
            absolute = str(directory.resolve())
            directory = Path('\\\\?\\UNC\\' + absolute[2:] if absolute.startswith('\\\\') else '\\\\?\\' + absolute)
        destination = directory / ('a' * 248 + '.png')
        try:
            destination.write_bytes(b'original')
            self.assertEqual(len(destination.name), 252)
            atomic_write(destination, b'replacement')
            self.assertEqual(destination.read_bytes(), b'replacement')
            self.assertEqual(set(directory.iterdir()), {destination})
        finally:
            destination.unlink(missing_ok=True)

    def test_atomic_write_valid_249_unit_destination_under_legacy_windows_limit(self):
        from nivram.persistence import atomic_write
        units = lambda value: len(str(value).encode('utf-16-le')) // 2
        # Reproduce the installed PNG source-store geometry without requiring
        # this test interpreter to disable its own long-path support.
        directory = self.root
        while units(directory) < 180:
            remaining = 180 - units(directory) - 1
            self.assertGreater(remaining, 0, 'The temporary root cannot represent this path fixture.')
            length = min(40, remaining)
            if remaining - length == 1:
                length -= 1
            directory /= 'd' * length
        self.assertEqual(units(directory), 180)
        directory.mkdir(parents=True)
        destination = directory / ('a' * 64 + '.png')
        self.assertEqual(units(destination), 249)
        real_open = os.open
        opened_lengths = []

        def legacy_open(path, flags, mode=0o777, *, dir_fd=None):
            length = units(path)
            opened_lengths.append(length)
            if length >= 260:
                raise FileNotFoundError(errno.ENOENT, 'Legacy Windows path limit', str(path))
            if dir_fd is None:
                return real_open(path, flags, mode)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        # Guard mkstemp's actual generated filename at its os.open boundary,
        # rather than assuming a particular random suffix or writer prefix.
        with patch('tempfile._os.open', side_effect=legacy_open):
            atomic_write(destination, b'preserved PNG bytes', exclusive=True)
        self.assertEqual(destination.read_bytes(), b'preserved PNG bytes')
        self.assertTrue(opened_lengths)
        self.assertTrue(all(length < 260 for length in opened_lengths))
        self.assertEqual(set(directory.iterdir()), {destination})

    def test_save_refuses_unacknowledged_existing_destination(self):
        self.store.save(self.path, project())
        with self.assertRaises(SaveConflict):
            self.store.save(self.path, project(1000))

    def test_new_destination_collision_is_not_overwritten(self):
        import nivram.persistence as persistence
        original = persistence.atomic_write
        competing = json.dumps(project(1300)).encode('utf-8')
        def race(path, data, **kwargs):
            if Path(path) == self.path:
                self.path.write_bytes(competing)
            return original(path, data, **kwargs)
        with patch('nivram.persistence.atomic_write', side_effect=race):
            with self.assertRaises(SaveConflict):
                self.store.save(self.path, project(2000))
        self.assertEqual(self.path.read_bytes(), competing)

    def test_clock_rollback_and_unrelated_files_cannot_evict_new_recovery(self):
        with patch('nivram.persistence.time.time_ns', return_value=9999999999999999999):
            for width in range(1000, 1010):
                self.store.autosave(project(width), self.generation, width)
        unrelated = []
        for index in range(10):
            path = self.root / 'recovery' / f'z-unrelated-{index}.json'
            path.write_text('{"unrelated":true}', encoding='utf-8'); unrelated.append(path)
        with patch('nivram.persistence.time.time_ns', return_value=1):
            receipt = self.store.autosave(project(1500), self.generation, 1500)
        records = self.store.list_recovery()
        self.assertEqual(len(records), 10)
        self.assertEqual(records[0]['id'], receipt['id'])
        self.assertEqual(self.store.recover(receipt['id'])['document'], project(1500))
        self.assertTrue(all(path.read_text(encoding='utf-8') == '{"unrelated":true}' for path in unrelated))

    def test_recovery_is_bounded_and_never_overwrites_saved_project(self):
        self.store.save(self.path, project())
        before = self.path.read_bytes()
        for width in range(1000, 1015):
            self.store.autosave(project(width), self.generation, width)
        records = self.store.list_recovery()
        self.assertEqual(len(records), 10)
        recovered = self.store.recover(records[0]['id'])
        self.assertEqual(recovered['document']['export']['width'], 1014)
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.store.recover('../molecule')

    def test_invalid_project_never_replaces_good_file(self):
        receipt = self.store.save(self.path, project())
        before = self.path.read_bytes()
        broken = project(); broken['graph'] = {'atoms': [{'id': 'a', 'element': 'madeup'}], 'bonds': []}
        with self.assertRaises(ValueError):
            self.store.save(self.path, broken, receipt['disk_hash'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_reject_nonfinite_unknown_schema_and_unusable_export(self):
        for field, value in [('schema', 'nivram/999'), ('style', {'bondWidth': float('nan')}),
                             ('export', {'width': 1, 'marginMode': 'percent', 'margin': 2})]:
            doc = project(); doc[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_project(doc)


if __name__ == '__main__':
    unittest.main()
