"""Project save receipts through the desktop API, without launching a native UI."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from nivram.desktop import DesktopApi
from nivram.persistence import SaveConflict, SaveFailure


class DesktopSaveOutcomeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.api = DesktopApi(self.root / 'profile')
        self.addCleanup(self.api._images.close)
        self.addCleanup(self.api._batch.close)
        self.addCleanup(self.api._reference_exports.close)
        self.old_path = self.root / 'opened.nivram'
        self.new_path = self.root / 'separate.nivram'
        self.api._path = self.old_path
        self.api._window = SimpleNamespace(create_file_dialog=Mock(return_value=str(self.new_path)))
        self.store = SimpleNamespace(save=Mock())
        self.api._store = self.store

    def test_all_failure_outcomes_remain_explicit_without_a_destination_change(self):
        for status, outcome in [('unsupported', 'not_committed'), ('failed', 'not_committed'),
                                ('unconfirmed', 'not_committed'), ('unconfirmed', 'unknown'),
                                ('unconfirmed', 'committed')]:
            error = SaveFailure(status=status, outcome=outcome)
            self.store.save.side_effect = error
            with self.subTest(status=status, outcome=outcome):
                result = self.api.save_project({'synthetic': True}, 'a' * 64, True)
                self.assertEqual(result, {'status': status, 'outcome': outcome, 'message': str(error)})
                self.assertEqual(self.api._path, self.old_path)
                self.assertNotIn('disk_hash', result)
                self.assertNotIn(str(self.root), result['message'])

    def test_conflict_is_distinct_and_does_not_adopt_save_as_destination(self):
        self.store.save.side_effect = SaveConflict('The destination changed.')
        result = self.api.save_project({'synthetic': True}, 'a' * 64, True)
        self.assertEqual(result['status'], 'conflict')
        self.assertNotIn('disk_hash', result)
        self.assertEqual(self.api._path, self.old_path)

    def test_only_a_saved_receipt_adopts_the_selected_destination(self):
        self.store.save.return_value = {'status': 'unconfirmed', 'outcome': 'unknown'}
        result = self.api.save_project({'synthetic': True}, None, True)
        self.assertEqual(result['status'], 'unconfirmed')
        self.assertEqual(self.api._path, self.old_path)
        saved = {'status': 'saved', 'disk_hash': 'b' * 64, 'name': self.new_path.name}
        self.store.save.return_value = saved
        self.assertEqual(self.api.save_project({'synthetic': True}, None, True), saved)
        self.assertEqual(self.api._path, self.new_path)

    def test_cancelled_dialog_never_dispatches_a_save_or_changes_destination(self):
        self.api._window.create_file_dialog.return_value = None
        self.assertEqual(self.api.save_project({'synthetic': True}, None, True), {'status': 'cancelled'})
        self.store.save.assert_not_called()
        self.assertEqual(self.api._path, self.old_path)

    def test_save_as_hash_precedes_confirmation_so_counterwriter_remains_a_conflict(self):
        self.new_path.write_bytes(b'OLD reviewed destination')
        original_hash = hashlib.sha256(self.new_path.read_bytes()).hexdigest()
        foreign = b'FOREIGN replacement while confirmation is pending'
        events = []
        # The dialog-selected name needs its final extension before any read.
        self.api._window.create_file_dialog.return_value = str(self.new_path.with_suffix(''))
        def read(path):
            self.assertEqual(path, self.new_path)
            events.append('read')
            return {'disk_hash': hashlib.sha256(path.read_bytes()).hexdigest()}
        self.store.open = Mock(side_effect=read)
        def confirm(title, question):
            self.assertIn(self.new_path.name, question)
            events.append('counterwriter')
            self.new_path.write_bytes(foreign)
            return True
        self.api._window.create_confirmation_dialog = Mock(side_effect=confirm)
        def save(path, document, expected):
            events.append('save')
            # Model only the existing conditional store contract. The external
            # replacement above is real filesystem I/O at the dialog boundary.
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise SaveConflict('The destination changed.')
            path.write_bytes(b'UNWANTED overwrite of newer file')
            return {'status': 'saved', 'disk_hash': 'a' * 64, 'name': path.name}
        self.store.save.side_effect = save
        result = self.api.save_project({'synthetic': True}, None, True)
        self.assertIn('counterwriter', events, 'The actual external write must execute')
        self.assertEqual(result['status'], 'conflict')
        self.assertEqual(events, ['read', 'counterwriter', 'save'])
        self.assertEqual(self.store.save.call_args.args[2], original_hash)
        self.assertEqual(self.new_path.read_bytes(), foreign)
        self.assertEqual(self.api._path, self.old_path)
        self.store.open.assert_called_once_with(self.new_path)

    def test_declining_save_as_confirmation_does_not_dispatch_or_change_destination(self):
        self.new_path.write_bytes(b'OLD')
        self.store.open = Mock(return_value={'disk_hash': hashlib.sha256(b'OLD').hexdigest()})
        self.api._window.create_confirmation_dialog = Mock(return_value=False)
        self.assertEqual(self.api.save_project({'synthetic': True}, None, True), {'status': 'cancelled'})
        self.store.save.assert_not_called()
        self.assertEqual(self.new_path.read_bytes(), b'OLD')
        self.assertEqual(self.api._path, self.old_path)

    def test_save_as_read_failure_is_safe_definite_precommit_failure_before_confirmation(self):
        self.new_path.write_bytes(b'OLD')
        self.api._window.create_confirmation_dialog = Mock(return_value=True)
        for error in (PermissionError('SECRET path'), ValueError('SECRET invalid project')):
            self.store.open = Mock(side_effect=error)
            with self.subTest(error=type(error).__name__):
                result = self.api.save_project({'synthetic': True}, None, True)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['outcome'], 'not_committed')
                self.assertNotIn('SECRET', result['message'])
                self.assertNotIn('disk_hash', result)
                self.api._window.create_confirmation_dialog.assert_not_called()
                self.store.save.assert_not_called()
                self.assertEqual(self.new_path.read_bytes(), b'OLD')
                self.assertEqual(self.api._path, self.old_path)

    def test_save_as_vanished_existing_file_is_conflict_and_is_never_recreated(self):
        self.new_path.write_bytes(b'OLD')
        def read(path):
            path.unlink()
            raise FileNotFoundError('SECRET removed destination')
        self.store.open = Mock(side_effect=read)
        self.api._window.create_confirmation_dialog = Mock(return_value=True)
        result = self.api.save_project({'synthetic': True}, None, True)
        self.assertEqual(result['status'], 'conflict')
        self.assertNotIn('SECRET', result['message'])
        self.api._window.create_confirmation_dialog.assert_not_called()
        self.store.save.assert_not_called()
        self.assertFalse(self.new_path.exists())
        self.assertEqual(self.api._path, self.old_path)


if __name__ == '__main__':
    unittest.main()
