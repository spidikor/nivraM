"""Storage integration with real synthetic files and a modeled native boundary.

No DLL, process or socket calls. This checks routing, backup/readback and failure
mapping; the model's writer exclusion is not evidence of Windows exclusion.
"""
import ctypes
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nivram import conditional_save as adapter
from nivram import persistence


def project(width=2000):
    return {'schema': 'nivram/1', 'graph': {'atoms': [], 'bonds': []}, 'style': {},
            'export': {'width': width, 'marginMode': 'percent', 'margin': 2}}


class FileTransactionModel:
    """Actual file bytes, staged in memory; Windows locking explicitly modeled."""
    def __init__(self, path, before_open=None):
        self.path, self.before_open = Path(path), before_open
        self.events, self.staged, self.protected = [], None, False

    def check_drive(self, root): pass
    def open_directory(self, path): return path
    def check_directory(self, handle, path): pass
    def check_volume(self, handle): pass
    def begin(self): self.events.append('begin'); return 'transaction'
    def open_existing(self, path, transaction):
        self.events.append('writer_open')
        if self.before_open: self.before_open()
        try: self.path.stat()
        except FileNotFoundError as exc: raise adapter.SaveConflict('missing') from exc
        self.protected = True
        return 'target'
    def check_target(self, handle, path): pass
    def read(self, handle, limit):
        self.events.append('read')
        return self.path.read_bytes() if self.staged is None else self.staged
    def write(self, handle, data): self.events.append('write'); self.staged = data
    def commit(self, transaction):
        self.events.append('commit')
        self.path.write_bytes(self.staged)
        self.protected = False
    def rollback(self, transaction): self.events.append('rollback'); self.protected = False
    def close(self, handle): self.events.append('close:' + handle)
    def foreign_publish(self, data):
        self.events.append('foreign_attempt')
        if self.protected: raise PermissionError('modeled writer exclusion')
        staged = self.path.with_suffix('.foreign')
        staged.write_bytes(data)
        os.replace(staged, self.path)


class SaveIntegrationTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / '.nv'
        root.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='si-', dir=root)
        self.addCleanup(temporary.cleanup)
        self.root, self.path = Path(temporary.name), Path(temporary.name) / 'molecule.nivram'
        self.store = persistence.ProjectStore(self.root / 'recovery')
        self.model = FileTransactionModel(self.path)
        for owner, name in ((ctypes, 'WinDLL'), (subprocess, 'Popen'), (socket, 'socket')):
            self.enterContext(patch.object(owner, name, side_effect=AssertionError('Native execution forbidden'), create=True))
        self.enterContext(patch.object(adapter, '_Win32', return_value=self.model))

    def baseline(self):
        result = self.store.save(self.path, project())
        return result['disk_hash'], self.path.read_bytes()

    def test_public_failure_categories_cannot_contradict_commit_outcome(self):
        for status in ('failed', 'unsupported', 'unconfirmed'):
            for outcome in ('not_committed', 'unknown', 'committed'):
                with self.subTest(status=status, outcome=outcome):
                    if status != 'unconfirmed' and outcome != 'not_committed':
                        with self.assertRaises(ValueError): persistence.SaveFailure(status, outcome)
                    else:
                        error = persistence.SaveFailure(status, outcome)
                        self.assertEqual((error.status, error.outcome), (status, outcome))
                        self.assertNotIn('recovery copies are retained', str(error))

    def test_existing_success_verifies_exact_backup_before_staging(self):
        expected, old = self.baseline()
        real_write = self.model.write
        def write(handle, data):
            self.assertTrue(self.model.protected)
            self.assertEqual(self.path.with_suffix('.nivram.bak').read_bytes(), old)
            return real_write(handle, data)
        self.model.write = write
        with patch.object(Path, 'exists', side_effect=AssertionError('exists must not select save mode')):
            result = self.store.save(self.path, project(1500), expected)
        self.assertEqual(result['status'], 'saved')
        self.assertEqual(result['disk_hash'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(self.model.events.count('writer_open'), 1)
        self.assertEqual(self.model.events.count('commit'), 1)

    def test_vanished_expected_destination_is_never_created(self):
        expected, _ = self.baseline(); self.path.unlink()
        with patch.object(persistence, 'atomic_write', side_effect=AssertionError('implicit create')):
            with self.assertRaises(persistence.SaveConflict): self.store.save(self.path, project(1500), expected)
        self.assertIn('writer_open', self.model.events)
        self.assertFalse(self.path.exists())

    def test_existing_without_expected_hash_stays_exclusive(self):
        _, old = self.baseline()
        with patch.object(adapter, 'conditional_write_existing', side_effect=AssertionError('existing mode without hash')):
            with self.assertRaises(persistence.SaveConflict): self.store.save(self.path, project(1500))
        self.assertEqual(self.path.read_bytes(), old)

    def test_backup_failure_or_corruption_prevents_target_commit(self):
        expected, old = self.baseline()
        real_atomic = persistence.atomic_write
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                self.model.events.clear()
                def backup(path, data, **kwargs):
                    if not corrupt: raise OSError('private backup path')
                    real_atomic(path, data, **kwargs)
                    Path(path).write_bytes(b'corrupt')
                with patch.object(persistence, 'atomic_write', side_effect=backup):
                    with self.assertRaises(persistence.SaveFailure) as caught:
                        self.store.save(self.path, project(1500), expected)
                self.assertEqual((caught.exception.status, caught.exception.outcome), ('failed', 'not_committed'))
                self.assertNotIn('private', str(caught.exception))
                self.assertNotIn('write', self.model.events); self.assertNotIn('commit', self.model.events)
                self.assertEqual(self.path.read_bytes(), old)

    def test_adapter_categories_preserve_uncertainty_without_success(self):
        expected, old = self.baseline()
        cases = [
            (adapter.SaveConflict('private conflict'), persistence.SaveConflict, None),
            (adapter.SaveUnsupported('private path'), persistence.SaveFailure, ('unsupported', 'not_committed')),
            (adapter.SaveError('private failure'), persistence.SaveFailure, ('failed', 'not_committed')),
            (adapter.SaveOutcomeUnknown('private commit', outcome='unknown'), persistence.SaveFailure, ('unconfirmed', 'unknown')),
            (adapter.SaveError('private cleanup', outcome='committed', cleanup_errors=(OSError('private'),)), persistence.SaveFailure, ('unconfirmed', 'committed')),
            (adapter.SaveError('private close', operation='CloseHandle'), persistence.SaveFailure, ('unconfirmed', 'not_committed')),
            (adapter.SaveError('private rollback', cleanup_errors=(OSError('private'),)), persistence.SaveFailure, ('unconfirmed', 'not_committed')),
            (adapter.SaveConflict('private masked', cleanup_errors=(OSError('private'),)), persistence.SaveFailure, ('unconfirmed', 'not_committed')),
        ]
        for error, exception_type, outcome in cases:
            with self.subTest(error=type(error).__name__, outcome=outcome):
                with patch.object(adapter, 'conditional_write_existing', side_effect=error) as writer:
                    with self.assertRaises(exception_type) as caught:
                        self.store.save(self.path, project(1500), expected)
                writer.assert_called_once()
                if outcome: self.assertEqual((caught.exception.status, caught.exception.outcome), outcome)
                self.assertNotIn('private', str(caught.exception))
                self.assertEqual(self.path.read_bytes(), old)

    def test_actual_adapter_target_close_failure_is_unconfirmed_without_cleanup_entries(self):
        expected, old = self.baseline()
        real_close = self.model.close
        def close(handle):
            real_close(handle)
            if handle == 'target': raise adapter.SaveError('private close', operation='CloseHandle', win32=6)
        self.model.close = close
        with self.assertRaises(persistence.SaveFailure) as caught:
            self.store.save(self.path, project(1500), expected)
        self.assertEqual((caught.exception.status, caught.exception.outcome), ('unconfirmed', 'not_committed'))
        self.assertEqual(caught.exception.__cause__.cleanup_errors, ())
        self.assertEqual(self.model.events.count('close:target'), 1)
        self.assertIn('rollback', self.model.events); self.assertNotIn('commit', self.model.events)
        self.assertEqual(self.path.read_bytes(), old)

    def test_unacknowledged_commit_can_leave_new_bytes_without_success_or_retry(self):
        expected, old = self.baseline()
        real_commit = self.model.commit
        def commit(handle):
            real_commit(handle)
            raise adapter.SaveError('private acknowledgement', operation='CommitTransaction')
        self.model.commit = commit
        with self.assertRaises(persistence.SaveFailure) as caught:
            self.store.save(self.path, project(1500), expected)
        self.assertEqual((caught.exception.status, caught.exception.outcome), ('unconfirmed', 'unknown'))
        self.assertEqual(self.model.events.count('commit'), 1)
        self.assertNotEqual(self.path.read_bytes(), old)
        self.assertEqual(self.store.open(self.path)['document'], project(1500))

    def test_vanished_postcommit_path_is_unconfirmed_without_recreation(self):
        expected, _ = self.baseline()
        real_conditional = adapter.conditional_write_existing
        def write(*args, **kwargs):
            result = real_conditional(*args, **kwargs)
            self.path.unlink()
            return result
        with patch.object(adapter, 'conditional_write_existing', side_effect=write) as writer:
            with self.assertRaises(persistence.SaveFailure) as caught:
                self.store.save(self.path, project(1500), expected)
        self.assertEqual((caught.exception.status, caught.exception.outcome), ('unconfirmed', 'committed'))
        writer.assert_called_once(); self.assertFalse(self.path.exists())

    def test_protected_counterwriter_is_attempted_and_rejected_by_model(self):
        expected, old = self.baseline()
        real_atomic = persistence.atomic_write
        def backup(path, data, **kwargs):
            with self.assertRaises(PermissionError): self.model.foreign_publish(b'FOREIGN')
            self.assertEqual(self.path.read_bytes(), old)
            return real_atomic(path, data, **kwargs)
        with patch.object(persistence, 'atomic_write', side_effect=backup):
            self.assertEqual(self.store.save(self.path, project(1500), expected)['status'], 'saved')
        self.assertEqual(self.model.events.count('foreign_attempt'), 1)
        self.assertEqual(self.model.events.count('commit'), 1)

    def test_foreign_publication_after_adapter_return_survives_failed_readback(self):
        expected, _ = self.baseline()
        real_conditional = adapter.conditional_write_existing
        foreign = b'FOREIGN after acknowledged commit'
        def write(*args, **kwargs):
            result = real_conditional(*args, **kwargs)
            self.model.foreign_publish(foreign)
            return result
        with patch.object(adapter, 'conditional_write_existing', side_effect=write) as writer:
            with self.assertRaises(persistence.SaveFailure) as caught:
                self.store.save(self.path, project(1500), expected)
        self.assertEqual((caught.exception.status, caught.exception.outcome), ('unconfirmed', 'committed'))
        writer.assert_called_once(); self.assertEqual(self.model.events.count('foreign_attempt'), 1)
        self.assertEqual(self.path.read_bytes(), foreign)

    def test_new_publication_error_is_uncertain_even_if_bytes_exist(self):
        real_atomic = persistence.atomic_write
        def publish_then_error(*args, **kwargs):
            self.assertTrue(kwargs['exclusive'])
            real_atomic(*args, **kwargs)
            raise OSError('private cleanup after publication')
        with patch.object(persistence, 'atomic_write', side_effect=publish_then_error) as writer:
            with self.assertRaises(persistence.SaveFailure) as caught: self.store.save(self.path, project())
        self.assertEqual((caught.exception.status, caught.exception.outcome), ('unconfirmed', 'unknown'))
        self.assertEqual(self.store.open(self.path)['document'], project())
        writer.assert_called_once(); self.assertEqual(self.model.events, [])


if __name__ == '__main__':
    unittest.main()
