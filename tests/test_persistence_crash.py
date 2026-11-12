"""Storage crash boundaries in real owned processes, using synthetic empty projects.

These checks establish storage behavior, not a native editor autosave-timing claim.
"""
import errno
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from nivram.persistence import ProjectStore


def project(width):
    return {'schema': 'nivram/1', 'version': '0.2.0-dev.1',
            'graph': {'atoms': [], 'bonds': []}, 'style': {},
            'export': {'width': width, 'marginMode': 'percent', 'margin': 2}}


CHILD = r'''
import json, sys, time
from pathlib import Path
from nivram.persistence import ProjectStore
import nivram.persistence as storage
root=Path(sys.argv[1]);phase=sys.argv[2]
store=ProjectStore(root/'recovery')
def doc(width):
    return {'schema':'nivram/1','version':'0.2.0-dev.1','graph':{'atoms':[],'bonds':[]},
            'style':{},'export':{'width':width,'marginMode':'percent','margin':2}}
if phase=='flushed-before-publish':
    def stop_before_publish(source,destination):
        print(json.dumps({'boundary':phase}),flush=True)
        time.sleep(60)
        raise RuntimeError('The supervising test did not terminate this process.')
    storage.os.replace=stop_before_publish
    storage.os.rename=stop_before_publish
    storage.os.link=stop_before_publish
store.autosave(doc(1200), store.recovery_context()['generation'], 1)
print(json.dumps({'boundary':'durable-recovery'}),flush=True)
time.sleep(60)
'''


class PersistenceCrashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nivram-storage-crash-')
        self.root = Path(self.temp.name)
        self.store = ProjectStore(self.root / 'recovery')
        self.generation = self.store.recovery_context()['generation']
        self.path = self.root / 'saved ü.nivram'
        self.receipt = self.store.save(self.path, project(2000))
        self.original = self.path.read_bytes()
        self.previous = self.store.autosave(project(1000), self.generation, 0)

    def tearDown(self):
        self.temp.cleanup()

    def terminate_at(self, phase):
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        process = subprocess.Popen([sys.executable, '-c', CHILD, str(self.root), phase],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **options)
        observed = queue.Queue()
        threading.Thread(target=lambda: observed.put(process.stdout.readline()), daemon=True).start()
        try:
            boundary = json.loads(observed.get(timeout=15))
            self.assertEqual(boundary['boundary'], phase)
            process.kill()  # The exact child handle created by this test only.
            process.wait(timeout=5)
            self.assertNotEqual(process.returncode, 0)
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=5)
            process.stdout.close(); process.stderr.close()

    def test_durable_recovery_survives_forced_process_termination(self):
        self.terminate_at('durable-recovery')
        reopened = ProjectStore(self.root / 'recovery')
        records = reopened.list_recovery()
        self.assertEqual(len(records), 2)
        self.assertEqual(reopened.recover(records[0]['id'])['document'], project(1200))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(reopened.open(self.path)['document'], project(2000))

    def test_termination_after_flush_before_publication_retains_previous_recovery(self):
        self.terminate_at('flushed-before-publish')
        reopened = ProjectStore(self.root / 'recovery')
        self.assertEqual([row['id'] for row in reopened.list_recovery()], [self.previous['id']])
        self.assertEqual(reopened.recover(self.previous['id'])['document'], project(1000))
        fragments = list((self.root / 'recovery').glob('.nv-*.tmp'))
        self.assertEqual(len(fragments), 1)
        before = fragments[0].read_bytes()
        self.assertEqual(json.loads(before)['document'], project(1200))
        inventory = reopened.recovery_inventory()
        self.assertEqual(inventory['status'], 'ready')
        self.assertEqual(inventory['issues'], dict(invalid=0, unsupported=0, unreadable=0, interrupted=1))
        self.assertEqual(fragments[0].read_bytes(), before)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_disk_full_and_permission_failures_never_report_success_or_replace_original(self):
        for failure in (OSError(errno.ENOSPC, 'injected disk full'), PermissionError(errno.EACCES, 'injected permission failure')):
            with self.subTest(error=failure.errno):
                with patch('nivram.persistence.os.fsync', side_effect=failure):
                    with self.assertRaises(OSError):
                        self.store.save(self.path, project(1200), self.receipt['disk_hash'])
                    with self.assertRaises(OSError):
                        self.store.autosave(project(1200), self.generation, 1)
                self.assertEqual(self.path.read_bytes(), self.original)
                self.assertEqual([row['id'] for row in self.store.list_recovery()], [self.previous['id']])
                self.assertEqual(list(self.root.rglob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
