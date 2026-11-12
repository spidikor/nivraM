"""Fixed mid-job cancellation gate/manifest regressions; no native launch."""
import copy
import hashlib
from pathlib import Path
import unittest

from nivram import acceptance, acceptance_batch_midjob as midjob, acceptance_click
import test_acceptance_batch_gates as base

CHECKS = frozenset({'editor-and-native-bridge-ready', 'native-batch-midjob-synthetic-inputs',
    'native-batch-first-png-before-cancel', 'native-batch-midjob-cancel-and-late-return',
    'native-batch-cancelled-manifest-and-integrity', 'native-window-loop-returned'})


def completed():
    report = base.complete_batch_report()
    report['scenario'] = 'batch-midjob-cancel'
    report['checks'] = [{'id': name, 'status': 'passed'} for name in CHECKS]
    return report


class MidjobGateTests(unittest.TestCase):
    def test_six_checks_and_original_lifecycle_policy_gates_pass(self):
        self.assertEqual(acceptance.SCENARIO_CHECKS['batch-midjob-cancel'], CHECKS)
        self.assertEqual(midjob.REQUIRED_CHECKS | {'editor-and-native-bridge-ready', 'native-window-loop-returned'}, CHECKS)
        self.assertEqual(acceptance.finalize_acceptance_report(completed())['status'], 'passed')

    def test_each_missing_check_is_required(self):
        for name in CHECKS:
            with self.subTest(name=name):
                report = completed()
                report['checks'] = [row for row in report['checks'] if row['id'] != name]
                self.assertIn('required_functional_checks_missing', acceptance.finalize_acceptance_report(report)['acceptance_failures'])

    def test_no_other_scenario_can_be_qualified_by_this_subset(self):
        for scenario in ('full', 'drawing-png', 'batch-png', 'batch-selection-cancel'):
            with self.subTest(scenario=scenario):
                report = completed()
                report['scenario'] = scenario
                self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')

    def test_exact_two_dialogs_and_zero_downloads_are_required(self):
        for count in (0, 1, 3):
            report = completed()
            report['dialog_fixture_shutdown'] = [{'shutdownConfirmed': True}] * count
            self.assertIn('dialog_fixture_shutdown_unconfirmed', acceptance.finalize_acceptance_report(report)['acceptance_failures'])
        for name in ('download_shutdown', 'downloads_final'):
            report = completed()
            report[name]['completed'] = 1
            self.assertIn('download_shutdown_unconfirmed', acceptance.finalize_acceptance_report(report)['acceptance_failures'])

    def test_cleanup_policy_process_and_evaluation_failures_remain_failures(self):
        for field, changes in (('network_policy', {'failures': 1}), ('cleanup', {'profile_directory_retained': True}),
                ('worker_shutdown', {'ownedProcessesExited': False}), ('evaluation_shutdown', {'timedOut': True}),
                ('process', {'exit_code': 1, 'forced_termination': False}), ('process', {'exit_code': 0, 'forced_termination': True})):
            with self.subTest(field=field):
                report = completed()
                report.setdefault(field, {}).update(changes)
                self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')

    def test_native_click_has_only_fixed_cancel_button_target(self):
        self.assertEqual(acceptance_click._SELECTORS['batch-cancel'], "document.getElementById('batch-export-cancel')")
        self.assertEqual(acceptance.acceptance_child_command(Path('report.json'), Path('profile'), 'batch-midjob-cancel')[-2:],
                         ['--scenario', 'batch-midjob-cancel'])


class MidjobManifestTests(unittest.TestCase):
    def setUp(self):
        self.sources = dict(zip(midjob.INPUT_NAMES, (b'synthetic first', b'synthetic second')))
        self.settings = {'style': {}, 'export': dict(midjob.OPTIONS), 'profile': None}
        first, second = midjob.INPUT_NAMES
        self.receipt = {'sessionId': 'a'*32, 'status': 'cancelled', 'settings': copy.deepcopy(self.settings),
            'manifestName': midjob.MANIFEST_NAME, 'manifestError': None, 'items': [
                {'id': 'b'*32, 'name': first, 'extension': '.nivram', 'status': 'exported',
                 'sourceSha256': hashlib.sha256(self.sources[first]).hexdigest(), 'outputName': 'midjob first.png',
                 'outputSha256': 'd'*64, 'dimensions': {'width': 640, 'height': 240, 'padding': 12}, 'error': None},
                {'id': 'c'*32, 'name': second, 'extension': '.nivram', 'status': 'cancelled',
                 'sourceSha256': hashlib.sha256(self.sources[second]).hexdigest(), 'outputName': None,
                 'outputSha256': None, 'dimensions': None, 'error': dict(midjob.CANCEL_ERROR)}]}

    def validate(self, receipt=None, manifest=None):
        value = receipt or self.receipt
        return midjob.validate_cancelled(value, manifest or {'schema': 'nivram-batch-export/1', **copy.deepcopy(value)},
                                         self.settings, self.sources, Path('synthetic-root').resolve())

    def test_completed_and_cancelled_items_have_exact_independent_provenance(self):
        good, cancelled = self.validate()
        self.assertEqual((good['status'], cancelled['status']), ('exported', 'cancelled'))

    def original_observation(self):
        before = copy.deepcopy(self.receipt)
        before['status'] = 'running'
        before['items'][1].update(status='pending', error=None)
        return before, {'sha256': 'd'*64, 'width': 640, 'height': 240, 'padding': 12}

    def test_final_session_and_pending_item_must_match_original_request(self):
        before, metadata = self.original_observation()
        for change in ('session', 'pending-id', 'completed-id'):
            value = copy.deepcopy(self.receipt)
            if change == 'session':
                value['sessionId'] = 'e'*32
            else:
                value['items'][1 if change == 'pending-id' else 0]['id'] = 'e'*32
            with self.subTest(change=change), self.assertRaises(AssertionError):
                midjob.validate_retained(before, value, {'schema': 'nivram-batch-export/1', **value},
                    self.settings, self.sources, Path('synthetic-root').resolve(), metadata)

    def test_final_completed_hash_and_dimensions_must_match_observed_png(self):
        before, metadata = self.original_observation()
        for change in ('hash', 'dimensions'):
            value = copy.deepcopy(self.receipt)
            if change == 'hash':
                value['items'][0]['outputSha256'] = 'e'*64
            else:
                value['items'][0]['dimensions']['height'] = 241
            with self.subTest(change=change), self.assertRaises(AssertionError):
                midjob.validate_retained(before, value, {'schema': 'nivram-batch-export/1', **value},
                    self.settings, self.sources, Path('synthetic-root').resolve(), metadata)

    def test_unchanged_wrong_receipt_hash_cannot_override_actual_png(self):
        before, metadata = self.original_observation()
        metadata['sha256'] = 'f'*64
        with self.assertRaises(AssertionError):
            midjob.validate_retained(before, self.receipt, {'schema': 'nivram-batch-export/1', **self.receipt},
                self.settings, self.sources, Path('synthetic-root').resolve(), metadata)

    def test_late_publication_or_false_terminal_status_is_rejected(self):
        for change in ({'status': 'exported'}, {'outputName': 'second.png'}, {'outputSha256': 'e'*64},
                       {'dimensions': {'width': 640}}, {'error': None}, {'error': {'code': 'render_failed'}}):
            value = copy.deepcopy(self.receipt)
            value['items'][1].update(change)
            with self.subTest(change=change), self.assertRaises(AssertionError):
                self.validate(value)
        for change in ({'status': 'completed'}, {'manifestError': 'failed'}, {'sessionId': 'invalid'}):
            with self.subTest(change=change), self.assertRaises(AssertionError):
                self.validate(self.receipt | change)

    def test_manifest_settings_source_and_completed_png_mismatches_are_rejected(self):
        value = copy.deepcopy(self.receipt)
        value['settings']['export']['width'] = 320
        with self.assertRaises(AssertionError): self.validate(value)
        value = copy.deepcopy(self.receipt)
        value['items'][0]['sourceSha256'] = 'f'*64
        with self.assertRaises(AssertionError): self.validate(value)
        value = copy.deepcopy(self.receipt)
        value['items'][0]['dimensions']['width'] = True
        with self.assertRaises(AssertionError): self.validate(value)
        manifest = {'schema': 'nivram-batch-export/1', **copy.deepcopy(self.receipt)}
        manifest['items'][1]['status'] = 'exported'
        with self.assertRaises(AssertionError): self.validate(manifest=manifest)

    def test_unsafe_names_extra_items_and_duplicate_ids_are_rejected(self):
        for field, value in (('name', '../private.nivram'), ('id', 'b'*32), ('name', midjob.INPUT_NAMES[0])):
            changed = copy.deepcopy(self.receipt)
            changed['items'][1][field] = value
            with self.subTest(field=field), self.assertRaises(AssertionError): self.validate(changed)
        changed = copy.deepcopy(self.receipt)
        changed['items'].append(copy.deepcopy(changed['items'][0]))
        with self.assertRaises(AssertionError): self.validate(changed)

    def test_terminal_ui_requires_cancel_message_and_no_temporary_stage(self):
        good, cancelled = self.validate()
        value = {'status': '1 of 2 PNG files saved. Batch cancelled. Manifest: nivram-batch-manifest.json. Completed files were retained.',
                 'startDisabled': False, 'cancelDisabled': True, 'profileDisabled': False, 'refreshDisabled': False,
                 'meterValue': 2, 'meterMax': 2, 'stageCount': 0,
                 'rows': [f"{good['name']}: exported → {good['outputName']}",
                          f"{cancelled['name']}: cancelled — {midjob.CANCEL_ERROR['message']}"]}
        midjob.validate_ui(value, good, cancelled)
        for changes in ({'stageCount': 1}, {'cancelDisabled': False}, {'status': 'complete'}, {'meterValue': 1}, {'rows': []}):
            with self.subTest(changes=changes), self.assertRaises(AssertionError):
                midjob.validate_ui(value | changes, good, cancelled)


if __name__ == '__main__':
    unittest.main()
