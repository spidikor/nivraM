"""Independent recovery serialization during a controlled manual save.

Actual ProjectStore and local files; conditional publication is simulated.
No Windows transaction, native adapter, application, or child process runs.
"""
from contextlib import ExitStack
import ctypes
import hashlib
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from nivram import persistence

_RETAINED_FAILED_CLEANUPS = []


def project(width):
    return {'schema': 'nivram/1', 'graph': {'atoms': [], 'bonds': []}, 'style': {},
            'export': {'width': width, 'marginMode': 'percent', 'margin': 2}}


class AutosaveContentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='nivram-autosave-contention-')
        self.workers, self.barriers = [], []
        self.patches = ExitStack()
        # One owner gates both restoration and directory deletion on terminal
        # workers; unittest must not run either after a failed join assertion.
        self.addCleanup(self.stop_workers)
        self.root = Path(self.temporary.name)
        self.store = persistence.ProjectStore(self.root / 'recovery', history_limit=2)
        self.generation = self.store.recovery_context()['generation']
        self.path = self.root / 'project.nivram'
        self.baseline = self.store.save(self.path, project(1000))
        for owner, name in ((ctypes, 'WinDLL'), (subprocess, 'Popen'), (socket, 'socket')):
            self.patches.enter_context(patch.object(owner, name, side_effect=AssertionError('Native/process/network execution forbidden'), create=True))

    def stop_workers(self):
        for barrier in self.barriers: barrier.set()
        for worker in self.workers:
            worker['thread'].join(2)
        if any(worker['thread'].is_alive() for worker in self.workers):
            # Retain the TemporaryDirectory object and active patches for the
            # remainder of this test process; never expose a live worker to
            # restored native calls or automatic directory finalization.
            _RETAINED_FAILED_CLEANUPS.append((self.temporary, self.patches, self.workers))
            self.fail('Owned worker did not terminate; guards and fixture retained')
        self.patches.close()
        self.temporary.cleanup()

    def worker(self, function):
        row = {'started': threading.Event(), 'done': threading.Event()}
        def invoke():
            row['started'].set()
            try: row['value'] = function()
            except BaseException as error: row['error'] = error
            finally: row['done'].set()
        row['thread'] = threading.Thread(target=invoke, daemon=True)
        self.workers.append(row); row['thread'].start()
        self.assertTrue(row['started'].wait(1))
        return row

    def result(self, worker):
        self.assertTrue(worker['done'].wait(2), 'Worker did not finish after barrier release')
        if 'error' in worker: raise worker['error']
        return worker['value']

    def controlled_save(self):
        entered, release = threading.Event(), threading.Event()
        self.barriers.append(release)
        calls = []
        def publish(path, data, expected_hash, backup):
            calls.append(expected_hash)
            old = Path(path).read_bytes()
            if hashlib.sha256(old).hexdigest() != expected_hash:
                raise persistence._conditional.SaveConflict('Synthetic destination changed')
            backup(old)
            entered.set()
            if not release.wait(7): raise TimeoutError('Controlled manual save was not released')
            persistence.atomic_write(path, data)
            return hashlib.sha256(data).hexdigest()
        self.patches.enter_context(patch.object(persistence._conditional, 'conditional_write_existing', side_effect=publish))
        first = self.worker(lambda: self.store.save(self.path, project(1500), self.baseline['disk_hash']))
        self.assertTrue(entered.wait(1)); self.assertFalse(first['done'].is_set())
        return first, release, calls

    def test_dirty_recovery_completes_within_five_seconds_while_manual_save_remains_pending(self):
        manual, release, _ = self.controlled_save()
        auto = self.worker(lambda: self.store.autosave(project(1900), self.generation, 1))
        self.assertTrue(auto['done'].wait(5), 'Dirty autosave starved behind the pending manual save')
        receipt = self.result(auto)
        self.assertFalse(manual['done'].is_set(), 'Manual save must still be pending for this assertion')
        self.assertEqual(self.store.recover(receipt['id'])['document'], project(1900))
        self.assertEqual([x['id'] for x in self.store.list_recovery()], [receipt['id']])
        self.assertEqual(self.store.open(self.path)['document'], project(1000))
        release.set()
        self.assertEqual(self.result(manual)['status'], 'saved')
        self.assertEqual(self.store.open(self.path)['document'], project(1500))

    def test_manual_saves_still_serialize_and_recheck_the_expected_file(self):
        first, release, calls = self.controlled_save()
        second = self.worker(lambda: self.store.save(self.path, project(1900), self.baseline['disk_hash']))
        self.assertFalse(second['done'].wait(.1)); self.assertEqual(len(calls), 1)
        release.set()
        self.assertEqual(self.result(first)['status'], 'saved')
        self.assertTrue(second['done'].wait(2))
        self.assertIsInstance(second.get('error'), persistence.SaveConflict)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.store.open(self.path)['document'], project(1500))

    def test_recovery_publication_rotation_and_listing_remain_one_serialized_domain(self):
        self.store.autosave(project(1000), self.generation, 1)
        self.store.autosave(project(1100), self.generation, 2)
        published, release = threading.Event(), threading.Event()
        self.barriers.append(release)
        real_write, recovery_writes = persistence.atomic_write, []
        def pause_first(path, data, **kwargs):
            real_write(path, data, **kwargs)
            if Path(path).parent == self.store.recovery_dir:
                recovery_writes.append(Path(path).stem)
                if len(recovery_writes) == 1:
                    published.set()
                    if not release.wait(2): raise TimeoutError('Recovery rotation barrier not released')
        self.patches.enter_context(patch.object(persistence, 'atomic_write', side_effect=pause_first))
        first = self.worker(lambda: self.store.autosave(project(1500), self.generation, 3))
        self.assertTrue(published.wait(1))
        second = self.worker(lambda: self.store.autosave(project(1900), self.generation, 4))
        listing = self.worker(self.store.list_recovery)
        self.assertFalse(second['done'].wait(.1)); self.assertFalse(listing['done'].is_set())
        self.assertEqual(len(recovery_writes), 1)
        # A direct immutable-record read may proceed; it returns a whole record.
        self.assertEqual(self.store.recover(recovery_writes[0])['document'], project(1500))
        release.set()
        receipts = [self.result(first), self.result(second)]
        self.assertEqual(len(self.result(listing)), 2)
        records = self.store.list_recovery()
        self.assertEqual([x['id'] for x in records], [receipts[1]['id'], receipts[0]['id']])
        self.assertEqual([self.store.recover(x['id'])['document'] for x in records], [project(1900), project(1500)])


if __name__ == '__main__':
    unittest.main()
