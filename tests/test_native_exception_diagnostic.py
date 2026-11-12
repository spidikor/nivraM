"""Bounded managed failures and chooser-return evidence cannot promote a failure."""
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nivram import native_exception_diagnostic as diagnostic
from nivram import native_dialog_worker as worker
from nivram.acceptance_batch_png import dialog_diagnostic
import test_native_batch_dialog_fixture as batch_fixtures
import test_acceptance_batch_png as observer_fixtures


class ManagedError(Exception):
    def __init__(self, name, code):
        super().__init__('PRIVATE message / path')
        self.name, self.HResult = name, code
    def GetType(self):
        return SimpleNamespace(FullName=self.name)


class ExceptionDiagnosticTests(unittest.TestCase):
    def test_known_managed_types_preserve_signed_numeric_hresult(self):
        with patch.dict(sys.modules, {'System': SimpleNamespace(Exception=ManagedError)}):
            for name, category in diagnostic.MANAGED_CATEGORIES.items():
                with self.subTest(name=name):
                    result = diagnostic.capture_exception_detail(ManagedError(name, -2146233079))
                    self.assertEqual(result, {'category': category, 'hresult': -2146233079})
                    self.assertEqual(diagnostic.validate_exception_detail(result), result)
                    self.assertNotIn('PRIVATE', json.dumps(result))

    def test_unknown_managed_name_is_not_disclosed(self):
        with patch.dict(sys.modules, {'System': SimpleNamespace(Exception=ManagedError)}):
            value = diagnostic.capture_exception_detail(ManagedError('PRIVATE.Type', -1))
        self.assertEqual(value, {'category': 'managed-other', 'hresult': -1})

    def test_broken_metadata_cannot_replace_original_failure(self):
        class Broken(ManagedError):
            def GetType(self): raise RuntimeError('PRIVATE metadata')
        with patch.dict(sys.modules, {'System': SimpleNamespace(Exception=ManagedError)}):
            value = diagnostic.capture_exception_detail(Broken('unused', 0))
        self.assertEqual(value, {'category': 'metadata-unavailable', 'hresult': None})

    def test_python_categories_and_hresult_bounds(self):
        for kind, category in diagnostic.PYTHON_CATEGORIES.items():
            self.assertEqual(diagnostic.capture_exception_detail(kind('PRIVATE')), {'category': category, 'hresult': None})
        with patch.dict(sys.modules, {'System': SimpleNamespace(Exception=ManagedError)}):
            for code in (True, -2**31-1, 2**31):
                self.assertIsNone(diagnostic.capture_exception_detail(ManagedError('PRIVATE', code))['hresult'])

    def test_receipt_rejects_unknown_keys_categories_and_invalid_numbers(self):
        invalid = [None, {}, {'category': 'PRIVATE', 'hresult': None},
                   {'category': 'python-runtime', 'hresult': 0},
                   {'category': 'managed-com', 'hresult': True},
                   {'category': 'managed-com', 'hresult': 2**31},
                   {'category': 'managed-com', 'hresult': -2**31-1},
                   {'category': 'managed-com', 'hresult': 'PRIVATE'},
                   {'category': 'managed-com', 'hresult': -1, 'message': 'PRIVATE'}]
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(diagnostic.validate_exception_detail(value))
                self.assertIsNone(dialog_diagnostic({'exception_detail': value})['exception_detail'])

    def test_worker_invoke_failure_retains_original_failure_contract(self):
        fixture = batch_fixtures.NativeBatchDialogFixtureTests()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        request = fixture.request('folder'); fixture.backend.labels[21] = 'folder:'
        with patch.dict(sys.modules, {'System': SimpleNamespace(Exception=ManagedError)}), patch.object(
                fixture.backend, 'invoke_prepared', side_effect=ManagedError('System.InvalidOperationException', -2146233079)):
            result = fixture.select(request)
        self.assertFalse(result['selected'])
        self.assertEqual(result['reason'], 'native_operation_failed')
        self.assertEqual(result['operation_failure']['operation'], 'invoke')
        self.assertEqual(result['exception_detail'], {'category': 'managed-invalid-operation', 'hresult': -2146233079})
        self.assertFalse(worker.validate_result(result, request['nonce'], mode='folder')['selected'])
        self.assertEqual(fixture.backend.commands, [(20, 2)])
        self.assertNotIn('PRIVATE', json.dumps(result))
        with self.assertRaises(ValueError):
            worker.validate_result(result | {'exception_detail': {'category': 'PRIVATE', 'hresult': None}}, request['nonce'], mode='folder')


class ChooserFailureObservationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = observer_fixtures.NativeDialogObserverTests()
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)

    def run_failure(self, returned):
        fixture = self.fixture
        fixture.results = (returned,)
        error = RuntimeError('PRIVATE helper failure')
        observer = fixture.observer(); observer.choose = fixture.failed_choice(error)
        original = fixture.window.create_file_dialog
        observer.attach()
        try:
            with self.assertRaises(RuntimeError) as caught:
                fixture.window.create_file_dialog(1, allow_multiple=True, file_types=('Molecular projects and 2D MOL (*.nivram;*.mol)',))
            self.assertIs(caught.exception, error)
            with self.assertRaises(AssertionError): observer.assert_complete()
        finally:
            observer.close()
        self.assertEqual(fixture.window.create_file_dialog, original)
        self.assertFalse(observer.records[0].get('actualReturnVerified', False))
        self.assertNotIn('PRIVATE', json.dumps(observer.records[0]))
        return observer.records[0]['originalReturnAfterFixtureFailure']

    def test_expected_native_return_is_observed_but_helper_failure_stays_failed(self):
        self.assertEqual(self.run_failure(tuple(str(p) for p in self.fixture.inputs)),
                         {'matchesExpectedSelection': True, 'emptySelection': False})

    def test_cancelled_return_is_distinguished_from_matching_selection(self):
        self.assertEqual(self.run_failure(None), {'matchesExpectedSelection': False, 'emptySelection': True})

    def test_foreign_return_is_not_disclosed_or_accepted(self):
        self.assertEqual(self.run_failure(('C:\\PRIVATE\\unrelated.nivram',)),
                         {'matchesExpectedSelection': False, 'emptySelection': False})


if __name__ == '__main__':
    unittest.main()
