"""Pure activation metadata and exact reviewed-report persistence; no native UI."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from nivram import diagnostics
from nivram.desktop import DesktopApi


def observation():
    return {'schema': 1, 'seen': 1, 'dropped': 0, 'events': [{
        'sequence': 1, 'format': 'csv', 'trusted': True,
        'handler_active': False, 'dispatch_active': False, 'dispatch_observed': True}]}


class ActivationDiagnosticTests(unittest.TestCase):
    def test_exact_allowlist_retains_false_and_unavailable_without_private_data(self):
        value = observation()
        value.update(filename='PRIVATE', content='PRIVATE', url='PRIVATE')
        value['events'][0].update(filename='PRIVATE', exception='PRIVATE')
        expected = {'status': 'observing', **observation()}
        safe = diagnostics.sanitize_reference_exports(value)
        self.assertEqual(safe, expected)
        self.assertNotIn('PRIVATE', json.dumps(safe))
        for key in ('trusted', 'handler_active', 'dispatch_active'):
            variant = observation(); variant['events'][0][key] = None
            result = diagnostics.sanitize_reference_exports(variant)
            self.assertIsNone(result['events'][0][key])

    def test_missing_or_malformed_envelope_is_unavailable_without_coercion(self):
        invalid = [None, [], 'PRIVATE', {}, True]
        for key in ('schema', 'seen', 'dropped', 'events'):
            value = observation(); value.pop(key); invalid.append(value)
        for key in ('schema', 'seen', 'dropped'):
            for bad in (True, '1', 1.0, -1, 10**9 + 1, None):
                invalid.append({**observation(), key: bad})
        invalid += [{**observation(), 'schema': 0}, {**observation(), 'schema': 2},
                    {**observation(), 'events': {}}, {**observation(), 'events': 'PRIVATE'}]
        for value in invalid:
            with self.subTest(value=value):
                self.assertEqual(diagnostics.sanitize_reference_exports(value),
                                 {'status': 'unavailable', 'events': []})

    def test_malformed_rows_are_skipped_and_required_nullable_fields_are_not_invented(self):
        good = observation()['events'][0]
        invalid = [None, 'PRIVATE', True, []]
        invalid += [{k: v for k, v in good.items() if k != missing} for missing in good]
        for key in ('trusted', 'handler_active', 'dispatch_active'):
            invalid += [{**good, key: bad} for bad in (0, 1, 'false', [], {})]
        invalid += [{**good, 'dispatch_observed': bad} for bad in (None, 0, 1, 'false', [], {})]
        invalid += [{**good, 'dispatch_observed': False, 'dispatch_active': active} for active in (False, True)]
        invalid += [{**good, 'sequence': bad} for bad in (True, 0, -1, 10**9 + 1, 1.0, '1')]
        invalid += [{**good, 'format': bad} for bad in (None, [], {}, 'PRIVATE', 'CSV')]
        for row in invalid:
            value = {**observation(), 'events': [row, good]}
            with self.subTest(row=row):
                self.assertEqual(diagnostics.sanitize_reference_exports(value)['events'], [good])
        pending = {**good, 'dispatch_observed': False, 'dispatch_active': None}
        self.assertEqual(diagnostics.sanitize_reference_exports({**observation(), 'events': [pending]})['events'], [pending])

    def test_four_most_recent_operations_are_copied_with_bounded_counters(self):
        value = observation(); value.update(seen=10**9, dropped=10**9)
        value['events'] = [{**value['events'][0], 'sequence': n} for n in range(1, 8)]
        safe = diagnostics.sanitize_reference_exports(value)
        self.assertEqual([row['sequence'] for row in safe['events']], [4, 5, 6, 7])
        self.assertEqual((safe['seen'], safe['dropped']), (10**9, 10**9))
        expected = copy.deepcopy(safe)
        value['events'][-1]['format'] = 'PRIVATE'; value['events'].clear(); value['seen'] = 0
        self.assertEqual(safe, expected)
        safe['events'][0]['format'] = 'json'
        self.assertEqual(value['events'], [])

    def test_report_includes_sanitized_snapshot_and_no_argument_remains_supported(self):
        value = observation()
        report = diagnostics.diagnostics_report(reference_exports=value)
        self.assertEqual(report['referenceExports'], {'status': 'observing', **value})
        value['events'][0]['trusted'] = False
        self.assertTrue(report['referenceExports']['events'][0]['trusted'])
        self.assertEqual(diagnostics.diagnostics_report()['referenceExports'],
                         {'status': 'unavailable', 'events': []})


class ReviewedActivationSaveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='activation-report-', dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.api = DesktopApi(self.root / 'profile')
        self.addCleanup(self.api._images.close)
        self.addCleanup(self.api._batch.close)
        self.destination = self.root / 'reviewed.json'
        self.api._window = SimpleNamespace(create_file_dialog=Mock(return_value=str(self.destination)),
            create_confirmation_dialog=Mock(return_value=True))

    def test_saved_bytes_are_exact_reviewed_snapshot_after_caller_and_returned_data_mutation(self):
        value = observation(); value['private'] = 'PRIVATE'
        result = self.api.diagnostics(value)
        reviewed = copy.deepcopy(result['data'])
        cached = self.api._diagnostic_pending[1]
        self.assertEqual(json.loads(cached), reviewed)
        self.assertEqual(reviewed['referenceExports'], {'status': 'observing', **observation()})
        value['events'][0]['trusted'] = False
        result['data']['referenceExports']['events'][0]['format'] = 'PRIVATE'
        with patch.dict(sys.modules, {'webview': SimpleNamespace(FileDialog=SimpleNamespace(SAVE='save'))}):
            self.assertEqual(self.api.save_diagnostics(result['reportId']), {'status': 'saved'})
        self.assertEqual(self.destination.read_bytes(), cached)
        self.assertEqual(json.loads(self.destination.read_bytes()), reviewed)
        self.assertNotIn(b'PRIVATE', cached)
        self.api._window.create_file_dialog.assert_called_once()

    def test_no_argument_and_superseded_report_keep_existing_review_gate(self):
        old = self.api.diagnostics()
        self.assertEqual(old['data']['referenceExports'], {'status': 'unavailable', 'events': []})
        latest = self.api.diagnostics(observation())
        self.assertNotEqual(old['reportId'], latest['reportId'])
        with patch.dict(sys.modules, {'webview': SimpleNamespace(FileDialog=SimpleNamespace(SAVE='save'))}):
            with self.assertRaisesRegex(ValueError, 'Reopen diagnostics'):
                self.api.save_diagnostics(old['reportId'])
        self.api._window.create_file_dialog.assert_not_called()
        self.assertFalse(self.destination.exists())


if __name__ == '__main__':
    unittest.main()
