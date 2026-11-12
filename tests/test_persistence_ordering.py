"""Actual storage ordering with synthetic files and bounded scheduling barriers."""
from contextlib import ExitStack
import copy
import ctypes
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from nivram import persistence


RETAINED = []


def project(width):
    return {'schema': 'nivram/1', 'graph': {'atoms': [], 'bonds': []}, 'style': {},
            'export': {'width': width, 'marginMode': 'percent', 'margin': 2}}


class PersistenceOrderingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='nivram-recovery-order-')
        self.root = Path(self.temporary.name)
        self.effects, self.workers, self.releases = ExitStack(), [], []
        self.addCleanup(self.stop_workers)
        for owner, name in ((ctypes, 'WinDLL'), (subprocess, 'Popen'), (socket, 'socket')):
            self.effects.enter_context(patch.object(owner, name,
                side_effect=AssertionError('Native/process/network execution forbidden'), create=True))
        self.store = persistence.ProjectStore(self.root / 'recovery')
        self.generation = self.store.recovery_context()['generation']
        persistence.validate_project(project(1000))

    def stop_workers(self):
        for release in self.releases:
            release.set()
        for row in self.workers:
            row['thread'].join(2)
        if any(row['thread'].is_alive() for row in self.workers):
            self.temporary._finalizer.detach()
            RETAINED.append((self.temporary, self.effects, self.workers))
            self.fail('Owned worker did not terminate; guards and synthetic fixture retained')
        self.effects.close()
        self.temporary.cleanup()

    def save(self, width, revision, generation=None):
        return self.store.autosave(project(width), self.generation if generation is None else generation, revision)

    def receipt(self, value, revision, generation=None):
        self.assertEqual(set(value), {'status', 'id', 'generation', 'revision'})
        self.assertEqual(value['status'], 'recovered-copy-saved')
        self.assertEqual(value['revision'], revision)
        self.assertEqual(value['generation'], generation or self.generation)
        self.assertRegex(value['id'], r'^[1-9][0-9]*-[0-9a-f]{32}$')

    def hold_validation(self, revision=1):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        real_validate = persistence.validate_project
        row = {}
        def validate(document):
            result = real_validate(document)
            if threading.current_thread() is row['thread'] and not entered.is_set():
                entered.set()
                if not release.wait(2):
                    raise TimeoutError('Synthetic scheduling barrier was not released')
            return result
        self.effects.enter_context(patch.object(persistence, 'validate_project', side_effect=validate))
        def submit():
            try:
                row['result'] = self.save(1000, revision)
            except BaseException as error:
                row['error'] = error
        row['thread'] = threading.Thread(target=submit, daemon=True)
        self.workers.append(row)
        row['thread'].start()
        self.assertTrue(entered.wait(1))
        return row, release

    def finish(self, row, release):
        release.set()
        row['thread'].join(2)
        self.assertFalse(row['thread'].is_alive())
        if 'error' in row:
            raise row['error']
        return row['result']

    def test_context_is_readonly_host_generation_without_filesystem_creation(self):
        self.assertRegex(self.generation, r'^[0-9a-f]{32}$')
        context = self.store.recovery_context()
        context['generation'] = '0' * 32
        self.assertEqual(self.store.recovery_context(), {'generation': self.generation})
        self.assertFalse(self.store.recovery_dir.exists())
        self.assertNotEqual(persistence.ProjectStore(self.root / 'other').recovery_context(),
                            self.store.recovery_context())

    def test_metadata_is_strict_and_no_implicit_generation_or_revision_is_accepted(self):
        with self.assertRaises(TypeError):
            self.store.autosave(project(1000))
        for value in (None, True, -1, 1.5, '1', 2**53):
            with self.subTest(revision=value), self.assertRaises(ValueError):
                self.save(1000, value)
        for value in (None, True, '', 'x' * 32, 'A' * 32, 'a' * 31, 'a' * 33):
            with self.subTest(generation=value), self.assertRaises(ValueError):
                self.store.autosave(project(1000), value, 0)
        self.assertFalse(self.store.recovery_dir.exists())
        self.receipt(self.save(1000, 2**53 - 1), 2**53 - 1)

    def test_mismatched_generation_returns_submitted_tuple_without_validation_or_filesystem(self):
        other = ('1' if self.generation[0] != '1' else '2') + self.generation[1:]
        with patch.object(persistence, 'validate_project', side_effect=AssertionError('Must not validate stale generation')), \
                patch.object(persistence, 'atomic_write', side_effect=AssertionError('Must not publish stale generation')):
            self.assertEqual(self.store.autosave({}, other, 7),
                {'status': 'superseded', 'generation': other, 'revision': 7})
        self.assertFalse(self.store.recovery_dir.exists())

    def test_older_validated_request_cannot_become_latest_after_newer_real_publication(self):
        row, release = self.hold_validation()
        newer = self.save(2000, 2)
        self.receipt(newer, 2)
        self.assertEqual(self.store.recover(newer['id'])['document'], project(2000))
        older = self.finish(row, release)
        self.assertEqual(older, {'status': 'superseded', 'generation': self.generation, 'revision': 1})
        self.assertEqual([item['id'] for item in self.store.list_recovery()], [newer['id']])

    def test_host_transition_blocks_old_request_already_past_validation(self):
        row, release = self.hold_validation()
        current = self.store.begin_recovery_generation()['generation']
        self.assertNotEqual(current, self.generation)
        newer = self.save(2000, 0, current)
        self.receipt(newer, 0, current)
        self.assertEqual(self.finish(row, release),
                         {'status': 'superseded', 'generation': self.generation, 'revision': 1})
        self.assertEqual(self.store.recovery_context(), {'generation': current})
        self.assertEqual([item['id'] for item in self.store.list_recovery()], [newer['id']])

    def test_equal_revision_and_document_is_verified_idempotent_even_with_reordered_keys(self):
        original = project(1000)
        unchanged = copy.deepcopy(original)
        with patch.object(persistence, 'atomic_write', wraps=persistence.atomic_write) as write:
            first = self.store.autosave(original, self.generation, 0)
            reordered = dict(reversed(list(original.items())))
            second = self.store.autosave(reordered, self.generation, 0)
        self.assertEqual(first, second)
        self.assertEqual(write.call_count, 1)
        self.assertEqual(original, unchanged)
        self.assertEqual(len(self.store.list_recovery()), 1)

    def test_equal_revision_with_conflicting_document_is_rejected_without_history_change(self):
        first = self.save(1000, 1)
        before = {path.name: path.read_bytes() for path in self.store.recovery_dir.iterdir()}
        with self.assertRaises(ValueError):
            self.save(2000, 1)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.store.recovery_dir.iterdir()})
        self.assertEqual(self.save(1000, 1), first)

    def test_error_after_real_publication_keeps_fence_and_retry_reconciles_same_candidate(self):
        real_write = persistence.atomic_write
        written = []
        def publish_then_fail(path, data, **kwargs):
            real_write(path, data, **kwargs)
            written.append(Path(path))
            raise OSError('Synthetic failure after atomic publication')
        with patch.object(persistence, 'atomic_write', side_effect=publish_then_fail):
            with self.assertRaises(OSError):
                self.save(2000, 2)
        self.assertEqual(self.save(1000, 1)['status'], 'superseded')
        with patch.object(persistence, 'atomic_write', side_effect=AssertionError('Uncertain publication must not replay')):
            recovered = self.save(2000, 2)
        self.receipt(recovered, 2)
        self.assertEqual(recovered['id'], written[0].stem)
        self.assertEqual(len(self.store.list_recovery()), 1)

    def test_readback_failure_does_not_acknowledge_and_retry_verifies_exact_pending_bytes(self):
        real_read = persistence._read_bounded
        failed = []
        def fail_once(path, limit):
            if not failed:
                failed.append(Path(path))
                raise PermissionError('Synthetic readback unavailable')
            return real_read(path, limit)
        with patch.object(persistence, '_read_bounded', side_effect=fail_once):
            with self.assertRaises(PermissionError):
                self.save(2000, 2)
        self.assertEqual(self.save(1000, 1)['status'], 'superseded')
        with patch.object(persistence, 'atomic_write', side_effect=AssertionError('Must reconcile exact pending file')):
            result = self.save(2000, 2)
        self.receipt(result, 2)
        self.assertEqual(result['id'], failed[0].stem)

    def test_foreign_pending_candidate_is_never_overwritten_or_acknowledged(self):
        real_write = persistence.atomic_write
        paths = []
        def publish_then_fail(path, data, **kwargs):
            real_write(path, data, **kwargs)
            paths.append(Path(path))
            raise OSError('Synthetic postpublication failure')
        with patch.object(persistence, 'atomic_write', side_effect=publish_then_fail):
            with self.assertRaises(OSError):
                self.save(2000, 2)
        paths[0].write_bytes(b'foreign synthetic replacement')
        with patch.object(persistence, 'atomic_write', side_effect=AssertionError('Must not replace foreign candidate')):
            with self.assertRaises(OSError):
                self.save(2000, 2)
        self.assertEqual(paths[0].read_bytes(), b'foreign synthetic replacement')
        self.assertEqual(self.save(1000, 1)['status'], 'superseded')

    def test_prepublication_failure_keeps_fence_but_same_tuple_can_publish_missing_candidate(self):
        with patch.object(persistence, 'atomic_write', side_effect=PermissionError('Synthetic denied write')):
            with self.assertRaises(PermissionError):
                self.save(2000, 2)
        self.assertEqual(self.save(1000, 1)['status'], 'superseded')
        self.receipt(self.save(2000, 2), 2)
        self.assertEqual(len(self.store.list_recovery()), 1)

    def test_reserved_timestamp_survives_uncertain_publication_clock_rollback_and_generation_transition(self):
        real_write = persistence.atomic_write
        def publish_then_fail(path, data, **kwargs):
            real_write(path, data, **kwargs)
            raise OSError('Synthetic postpublication failure')
        with patch.object(persistence.time, 'time_ns', return_value=999999999999999999), \
                patch.object(persistence, 'atomic_write', side_effect=publish_then_fail):
            with self.assertRaises(OSError):
                self.save(1000, 10)
        original = self.store.list_recovery()[0]
        current = self.store.begin_recovery_generation()['generation']
        # Model the uncertain record temporarily unavailable to inventory.
        with patch.object(persistence.time, 'time_ns', return_value=1), \
                patch.object(self.store, '_valid_recovery_records', return_value=[]):
            result = self.save(2000, 0, current)
        self.assertGreater(int(result['id'].split('-')[0]), original['created_ns'])
        self.assertEqual(self.store.list_recovery()[0]['id'], result['id'])

    def test_legacy_schema_one_records_still_recover_and_participate_in_bounded_history(self):
        self.store.recovery_dir.mkdir()
        stamp = 999999999999999999
        legacy_id = str(stamp) + '-' + 'a' * 32
        record = {'schema': 'nivram-recovery/1', 'created_ns': stamp, 'document': project(1000)}
        (self.store.recovery_dir / (legacy_id + '.json')).write_text(json.dumps(record), encoding='utf-8')
        self.assertEqual(self.store.recover(legacy_id), record)
        self.store.history_limit = 2
        with patch.object(persistence.time, 'time_ns', return_value=1):
            first = self.save(2000, 1)
            second = self.save(3000, 2)
        self.assertEqual([item['id'] for item in self.store.list_recovery()], [second['id'], first['id']])
        self.assertEqual(set(self.store.recover(second['id'])), {'schema', 'created_ns', 'document'})

    def test_verified_duplicate_requires_fresh_readback_and_cannot_hide_later_change(self):
        receipt = self.save(2000, 2)
        path = self.store.recovery_dir / (receipt['id'] + '.json')
        path.write_bytes(b'changed after verified acknowledgment')
        with self.assertRaises(OSError):
            self.save(2000, 2)
        self.assertEqual(self.save(1000, 1)['status'], 'superseded')


if __name__ == '__main__':
    unittest.main()
