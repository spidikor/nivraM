"""Worker observations must survive an original chooser call that has not returned.

These tests use the real NativeDialogObserver with finite owned test threads;
they launch neither a browser nor a native chooser or child process.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading
import unittest

from nivram.acceptance_batch_png import NativeDialogObserver, retain_dialog_records


PRIVATE_SENTINEL = r'C:\private-synthetic-only\unreviewed-caption-and-nonce'
PRIVATE_MARKER = 'unreviewed-caption-and-nonce'


def worker_result():
    return {
        'selected': False,
        'reason': 'supported_folder_control_not_found',
        'stage': 'identify-field',
        'selection_mode': 'folder',
        'selection_count': 1,
        'control_count': 44,
        'dialog_count': 1,
        'label_counts': {'filename': 0, 'folder': 1, 'other': 0},
        'idok_label_kind': 'folder',
        'filename_field_verified': False,
        'dialog_close_confirmed': False,
        'worker_exit_confirmed': True,
        'parent_phase': 'result_validate',
        'error_kind': None,
        'cleanup_error_kind': None,
        'worker_returncode': 0,
        'elapsed_seconds': 1.25,
        'cleanup': {
            'shutdownConfirmed': True,
            'worker_exit_confirmed': True,
            'supervisor_thread_exited': True,
            'cancel_requested': False,
            'owned_dialog_cancel_posted': False,
            'private_caption': PRIVATE_SENTINEL,
        },
        'nonce': PRIVATE_SENTINEL,
        'path': PRIVATE_SENTINEL,
        'control_caption': PRIVATE_SENTINEL,
        'unknown': {'private': PRIVATE_SENTINEL},
    }


class PendingOriginalDialogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inputs = (self.root / 'first \u00fc.nivram', self.root / 'second \u00fc.mol')
        for path in self.inputs:
            path.write_bytes(b'Owned synthetic input; preserve these bytes.')
        self.folder = self.root / 'outputs \u00fc'
        self.folder.mkdir()

    @contextmanager
    def blocked_original(self, result, *, property_error=False):
        entered, release = threading.Event(), threading.Event()
        original_error = RuntimeError('The original synthetic folder call failed.')
        finish_error = RuntimeError('Synthetic fixture finish also failed.')
        errors = []
        folder_calls = {'finish': 0, 'cancel': 0, 'close': 0}

        class OpenChoice:
            result = {'selected': True, 'worker_exit_confirmed': True}

            def finish(self):
                return dict(self.result)

        class FolderChoice:
            def __init__(self):
                self.result_reads = 0

            @property
            def result(self):
                self.result_reads += 1
                if property_error:
                    raise ValueError(PRIVATE_SENTINEL)
                return result

            def finish(self):
                folder_calls['finish'] += 1
                raise finish_error

            def cancel(self):
                folder_calls['cancel'] += 1

            def close(self):
                folder_calls['close'] += 1

        folder_choice = FolderChoice()
        input_result = tuple(str(path) for path in reversed(self.inputs))

        class Window:
            def create_file_dialog(self, kind, **kwargs):
                if kind == 1:
                    return input_result
                entered.set()
                if not release.wait(10):
                    raise AssertionError('Owned test dialog was not released within its finite deadline.')
                raise original_error

        window = Window()
        original = window.create_file_dialog

        def choose(target, *, mode, timeout, parent_io):
            self.assertEqual(timeout, 15)
            self.assertIs(parent_io, True)
            if mode == 'open-multiple':
                self.assertEqual(tuple(target), self.inputs)
                return OpenChoice()
            self.assertEqual(mode, 'folder')
            self.assertEqual(target, self.folder)
            return folder_choice

        observer = NativeDialogObserver(window, choose, root=self.root, inputs=self.inputs,
            folder=self.folder, open_kind=1, folder_kind=3, timeout=lambda: 15)
        observer.attach()
        thread = None
        try:
            returned = window.create_file_dialog(1, allow_multiple=True,
                file_types=('Molecular projects and 2D MOL (*.nivram;*.mol)',))
            self.assertIs(returned, input_result)

            def call_original():
                try:
                    window.create_file_dialog(3)
                except Exception as error:
                    errors.append(error)

            thread = threading.Thread(target=call_original, name='owned-pending-dialog-test')
            thread.start()
            self.assertTrue(entered.wait(2), 'The original folder call did not enter its bounded test wait.')
            self.assertTrue(thread.is_alive())
            yield observer, folder_choice, folder_calls, window, original
        finally:
            release.set()
            if thread is not None:
                thread.join(2)
                self.assertFalse(thread.is_alive(), 'Owned test dialog thread leaked after release.')
                self.assertEqual(len(errors), 1)
                self.assertIs(errors[0], original_error,
                              'Observation or fixture completion replaced the original failure.')
            observer.close()

    def retain_pending(self, observer, folder_calls, window, original):
        before = {path: path.read_bytes() for path in self.inputs}
        report = {}
        retain_dialog_records(report, observer, failure_active=True)
        self.assertEqual(len(report['batch_png_dialogs']), 2)
        record = report['batch_png_dialogs'][1]
        self.assertEqual(record['mode'], 'folder')
        self.assertIs(record['originalCalled'], True)
        self.assertIs(record['originalReturned'], False)
        self.assertNotIn('actualReturnVerified', record)
        self.assertNotIn('returnedNames', record)
        self.assertEqual(folder_calls, {'finish': 0, 'cancel': 0, 'close': 0},
                         'Snapshot capture must not invoke fixture completion or cancellation.')
        with self.assertRaisesRegex(AssertionError, 'batch_native_dialog_returns_incomplete'):
            observer.assert_complete()
        self.assertEqual(window.create_file_dialog, original)
        self.assertEqual(report['batch_png_dialog_observer_cleanup'], {'restored': True})
        self.assertEqual(before, {path: path.read_bytes() for path in self.inputs})
        self.assertIn('resultAtSnapshot', record,
                      'A pending original call must retain the sanitized available worker observation.')
        return report, record, record['resultAtSnapshot']

    def test_stalled_original_retains_bounded_private_worker_observation_and_independent_copy(self):
        result = worker_result()
        with self.blocked_original(result) as (observer, choice, calls, window, original):
            report, record, observed = self.retain_pending(observer, calls, window, original)
            self.assertGreaterEqual(choice.result_reads, 1)
            self.assertIs(observed['available'], True)
            self.assertIs(observed['selected'], False)
            self.assertEqual(observed['reason'], 'supported_folder_control_not_found')
            self.assertEqual(observed['stage'], 'identify-field')
            self.assertEqual(observed['parent_phase'], 'result_validate')
            self.assertEqual(observed['selection_count'], 1)
            self.assertEqual(observed['control_count'], 44)
            self.assertEqual(observed['label_counts'], {'filename': 0, 'folder': 1, 'other': 0})
            self.assertEqual(observed['worker_returncode'], 0)
            self.assertEqual(observed['elapsed_seconds'], 1.25)
            self.assertIs(observed['worker_exit_confirmed'], True)
            self.assertNotIn('fixture', record)
            self.assertNotIn('fixtureFailure', record)
            serialized = json.dumps(report, allow_nan=False, sort_keys=True)
            self.assertNotIn(PRIVATE_MARKER, serialized)
            self.assertTrue({'nonce', 'path', 'control_caption', 'unknown'}.isdisjoint(observed))
            self.assertNotIn('private_caption', observed['cleanup'])
            result['reason'] = 'worker_failure'
            result['label_counts']['folder'] = 0
            result['cleanup']['shutdownConfirmed'] = False
            observer.records[1]['originalReturned'] = True
            self.assertEqual(json.dumps(report, allow_nan=False, sort_keys=True), serialized)

    def test_absent_worker_result_is_unavailable_without_promoting_native_return(self):
        with self.blocked_original(None) as (observer, choice, calls, window, original):
            _, _, observed = self.retain_pending(observer, calls, window, original)
            self.assertIs(observed['available'], False)
            self.assertIsNone(observed['selected'])
            self.assertIsNone(observed['reason'])
            self.assertIsNone(observed['worker_returncode'])

    def test_dict_subclass_result_is_unavailable_without_executing_its_get(self):
        class HostileResult(dict):
            def get(self, *args, **kwargs):
                raise AssertionError('Do not execute custom mapping methods while recording diagnostics.')

        with self.blocked_original(HostileResult(worker_result())) as (observer, choice, calls, window, original):
            _, _, observed = self.retain_pending(observer, calls, window, original)
            self.assertIs(observed['available'], False)
            self.assertIsNone(observed['selected'])
            self.assertIsNone(observed['reason'])

    def test_failing_result_property_is_unavailable_and_cannot_replace_original_failure(self):
        with self.blocked_original(worker_result(), property_error=True) as (observer, choice, calls, window, original):
            report, _, observed = self.retain_pending(observer, calls, window, original)
            self.assertGreaterEqual(choice.result_reads, 1)
            self.assertIs(observed['available'], False)
            self.assertIsNone(observed['selected'])
            self.assertNotIn(PRIVATE_MARKER, json.dumps(report, allow_nan=False))

    def test_selected_worker_result_cannot_authorize_an_original_call_that_has_not_returned(self):
        result = worker_result()
        result.update(selected=True, reason=None, stage='confirm-close',
                      filename_field_verified=True, dialog_close_confirmed=True)
        with self.blocked_original(result) as (observer, choice, calls, window, original):
            _, record, observed = self.retain_pending(observer, calls, window, original)
            self.assertIs(observed['available'], True)
            self.assertIs(observed['selected'], True)
            self.assertIs(record['originalReturned'], False)
            self.assertNotIn('actualReturnVerified', record)

    def test_pending_observation_redacts_nonfinite_out_of_bounds_and_unknown_categories(self):
        result = worker_result()
        result.update(reason=PRIVATE_SENTINEL, stage=PRIVATE_SENTINEL,
                      parent_phase=PRIVATE_SENTINEL, error_kind=PRIVATE_SENTINEL,
                      control_count=257, dialog_count=257, selection_count=3,
                      worker_returncode=2**32, elapsed_seconds=float('nan'))
        with self.blocked_original(result) as (observer, choice, calls, window, original):
            report, _, observed = self.retain_pending(observer, calls, window, original)
            self.assertIs(observed['available'], True)
            for key in ('reason', 'stage', 'parent_phase', 'error_kind'):
                self.assertEqual(observed[key], 'unrecognized')
            for key in ('control_count', 'dialog_count', 'selection_count',
                        'worker_returncode', 'elapsed_seconds'):
                self.assertIsNone(observed[key])
            self.assertEqual(observed['label_counts'], {'filename': None, 'folder': None, 'other': None})
            self.assertNotIn(PRIVATE_MARKER, json.dumps(report, allow_nan=False))


if __name__ == '__main__':
    unittest.main()
