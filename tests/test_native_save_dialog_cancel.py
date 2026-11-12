"""Save-specific native Cancel boundaries; fake UI does not qualify a native run."""
import copy
import os
import unittest
from unittest.mock import patch

from nivram import native_dialog_worker as worker
import test_native_dialog_cancel as support


class SaveCancelTests(unittest.TestCase):
    setUp = support.IntentionalCancelTests.setUp
    submit = support.IntentionalCancelTests.submit
    run_cancel = support.IntentionalCancelTests.run_cancel

    def request(self, action='cancel'):
        self.backend.labels.update({21:'file name:',31:'save'})
        self.target = self.root/'synthetic fixtures'/'cancel.nivram'
        with patch.object(worker,'process_created',return_value=123):
            return worker.build_request(self.window,self.target,self.root,.2,
                                        nonce='a'*32,mode='save',action=action)

    def test_explicit_save_cancel_posts_once_without_creating_or_typing(self):
        request = self.request()
        value = self.run_cancel(request)
        self.assertTrue(value['cancelled'])
        self.assertEqual(value['selection_mode'],'save')
        self.assertEqual(self.backend.commands,[(20,2)])
        self.assertFalse(self.target.exists())

    def test_save_cancel_cannot_be_downgraded_to_selection(self):
        with self.assertRaises(ValueError): self.request(action='select')
        request = self.request()
        downgraded = {k:v for k,v in request.items() if k != 'dialogAction'}
        downgraded['schemaVersion'] = 2
        with self.assertRaises(ValueError):
            worker.validate_request(downgraded,expected_parent=os.getpid())
        value = self.run_cancel(request)
        with self.assertRaises(ValueError):
            worker.validate_result(value,request['nonce'],mode='save',action='select')

    def test_open_folder_and_ambiguous_dialogs_are_not_touched(self):
        for label in ('open','select folder','unknown'):
            self.clock.now = 0
            request = self.request(); self.backend.labels[31] = label
            with self.subTest(label=label):
                self.assertFalse(self.run_cancel(request)['cancelled'])
                self.assertEqual(self.backend.commands,[])

    def test_newly_created_destination_invalidates_cancel_identity(self):
        request = self.request()
        self.target.write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            worker.validate_request(request,expected_parent=os.getpid())
        self.assertEqual(self.backend.commands,[])
        self.assertEqual(self.target.read_bytes(),b'foreign')

    def test_wrong_parent_and_foreign_controls_are_rejected(self):
        request = self.request()
        self.backend.pids[21] += 1
        self.assertFalse(self.run_cancel(request)['cancelled'])
        self.assertEqual(self.backend.commands,[])
        changed = copy.deepcopy(request); changed['targetIdentities'][0]['inode'] += 1
        with self.assertRaises(ValueError):
            worker.validate_request(changed,expected_parent=os.getpid())

    def test_post_without_observed_closure_is_not_cancellation(self):
        self.cancel_closes = False
        value = self.run_cancel(self.request())
        self.assertFalse(value['cancelled'])
        self.assertEqual(self.backend.commands,[(20,2)])


if __name__ == '__main__': unittest.main()

