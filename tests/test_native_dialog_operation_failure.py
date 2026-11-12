"""Failure location, Win32 last-error semantics, and bounded receipt validation."""
import ctypes
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nivram import native_dialog_worker as worker
from nivram.acceptance_batch_png import dialog_diagnostic
from test_native_batch_dialog_fixture import NativeBatchDialogFixtureTests


class NativeOperationFailureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = NativeBatchDialogFixtureTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.request = self.fixture.request('folder')
        self.fixture.backend.labels[21] = 'folder:'

    def failing(self, method, error):
        with patch.object(self.fixture.backend, method, side_effect=error):
            result = self.fixture.select(self.request)
        self.assertFalse(result['selected'])
        self.assertEqual(result['reason'], 'native_operation_failed')
        self.assertEqual(self.fixture.backend.invocations, [])
        self.assertEqual(self.fixture.backend.commands, [(20, 2)])
        self.assertFalse(worker.validate_result(result, self.request['nonce'], mode='folder')['selected'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        return result

    def test_set_failure_records_native_error_without_exception_text(self):
        result = self.failing('set_text', worker.NativeMessageFailure(1460))
        self.assertEqual(result['operation_failure'], {'operation': 'set-text', 'kind': 'native-message', 'winerror': 1460})
        self.assertEqual(dialog_diagnostic(result)['operation_failure'], result['operation_failure'])

    def test_read_failure_is_distinct_from_text_write(self):
        result = self.failing('read_text', ValueError('PRIVATE path and caption'))
        self.assertEqual(result['operation_failure'], {'operation': 'read-text', 'kind': 'value', 'winerror': None})
        self.assertEqual(len(self.fixture.backend.writes), 1)

    def test_prepare_invoke_failure_does_not_claim_text_failure(self):
        result = self.failing('prepare_invoke', RuntimeError('PRIVATE provider error'))
        self.assertEqual(result['operation_failure'], {'operation': 'prepare-invoke', 'kind': 'other', 'winerror': None})

    def test_success_and_legacy_receipts_need_no_diagnostic(self):
        result = self.fixture.select(self.request)
        self.assertTrue(result['selected'])
        self.assertNotIn('operation_failure', result)
        self.assertTrue(worker.validate_result(result, self.request['nonce'], mode='folder')['selected'])
        self.assertIsNone(dialog_diagnostic(result)['operation_failure'])
        result['operation_failure'] = {'operation': 'set-text', 'kind': 'value', 'winerror': None}
        with self.assertRaises(ValueError):
            worker.validate_result(result, self.request['nonce'], mode='folder')

    def test_malformed_or_unbounded_diagnostics_are_rejected_and_redacted(self):
        result = self.failing('set_text', ValueError('PRIVATE'))
        invalid = [None, {}, {'operation': 'PRIVATE', 'kind': 'value', 'winerror': None},
                   {'operation': 'set-text', 'kind': 'PRIVATE', 'winerror': None},
                   {'operation': 'set-text', 'kind': 'value', 'winerror': 5},
                   {'operation': 'set-text', 'kind': 'native-message', 'winerror': True},
                   {'operation': 'set-text', 'kind': 'native-message', 'winerror': -1},
                   {'operation': 'set-text', 'kind': 'native-message', 'winerror': 2**32},
                   {'operation': 'set-text', 'kind': 'value', 'winerror': None, 'caption': 'PRIVATE'}]
        for diagnostic in invalid:
            with self.subTest(diagnostic=diagnostic):
                changed = result | {'operation_failure': diagnostic}
                with self.assertRaises(ValueError):
                    worker.validate_result(changed, self.request['nonce'], mode='folder')
                self.assertIsNone(dialog_diagnostic(changed)['operation_failure'])

    def test_message_failure_clears_stale_error_and_preserves_zero_as_generic(self):
        backend = object.__new__(worker.WindowsBackend)
        state = {'error': 12345}
        calls = []
        def clear(code):
            state['error'] = code
        def send(*args):
            calls.append((state['error'], args[4], args[5]))
            return 0
        backend.user = SimpleNamespace(SendMessageTimeoutW=send)
        with patch.object(worker.ctypes, 'set_last_error', side_effect=clear), patch.object(worker.ctypes, 'get_last_error', side_effect=lambda: state['error']):
            with self.assertRaises(worker.NativeMessageFailure) as caught:
                backend.set_text(21, 'synthetic', 750)
        self.assertEqual(caught.exception.native_winerror, 0)
        self.assertEqual(calls, [(0, 0x23, 750)])

    def test_message_preserves_reported_win32_failure(self):
        backend = object.__new__(worker.WindowsBackend)
        backend.user = SimpleNamespace(SendMessageTimeoutW=lambda *args: 0)
        with patch.object(worker.ctypes, 'set_last_error'), patch.object(worker.ctypes, 'get_last_error', return_value=5):
            with self.assertRaises(worker.NativeMessageFailure) as caught:
                backend.set_text(21, 'synthetic', 750)
        self.assertEqual(caught.exception.native_winerror, 5)

    def test_completed_message_still_checks_window_result(self):
        backend = object.__new__(worker.WindowsBackend)
        def send(*args):
            ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_size_t)).contents.value = 0
            return 1
        backend.user = SimpleNamespace(SendMessageTimeoutW=send)
        with patch.object(worker.ctypes, 'set_last_error'), patch.object(worker.ctypes, 'get_last_error') as last:
            with self.assertRaises(ValueError):
                backend.set_text(21, 'synthetic', 750)
        last.assert_not_called()


if __name__ == '__main__':
    unittest.main()
