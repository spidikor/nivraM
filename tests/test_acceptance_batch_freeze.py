"""Adversarial freeze evidence checks; these tests do not launch native UI."""
import copy
import hashlib
from pathlib import Path
import unittest

from nivram import acceptance, acceptance_batch_freeze as freeze
import test_acceptance_batch_gates as gates

CHECKS = freeze.REQUIRED_CHECKS | {'editor-and-native-bridge-ready', 'native-window-loop-returned'}


def complete():
    value = gates.complete_batch_report()
    value.update(scenario='batch-freeze', checks=[{'id': name, 'status': 'passed'} for name in CHECKS],
                 process={'exit_code': 0, 'forced_termination': False})
    value['batch_freeze'] = {
        'freezeVerified': True, 'uncertainPublicationVerified': False,
        'delayLocation': 'after_real_preparation_adoption_before_bridge_return',
        'barrierShutdown': {'shutdownConfirmed': True, 'methodsRestored': True, 'calls': 2, 'holdTimedOut': False,
            **dict.fromkeys(('held', 'released', 'finished', 'secondReady', 'releasedWhileRunning', 'resultUnchanged'), True)},
        'dialogs': [{'mode': mode, 'actualReturnVerified': True, 'originalReturned': True} for mode in ('open-multiple', 'folder')],
        'originalSourceSha256': 'a'*64, 'editedSourceSha256': 'b'*64, 'originalProfileSha256': 'c'*64,
        'editedProfileSha256': 'd'*64, 'newRevisionSha256': 'e'*64, 'manifestSha256': 'f'*64}
    return value


class FreezeGateTests(unittest.TestCase):
    def test_fixed_six_checks_and_all_original_gates_pass(self):
        self.assertEqual(acceptance.SCENARIO_CHECKS['batch-freeze'], CHECKS)
        result = acceptance.finalize_acceptance_report(complete())
        self.assertEqual((result['status'], result['acceptance_failures']), ('passed', []))
        self.assertEqual(acceptance.acceptance_child_command(Path('report'), Path('profile'), 'batch-freeze')[-2:],
                         ['--scenario', 'batch-freeze'])

    def test_each_missing_check_and_cross_scenario_claim_fail(self):
        for name in CHECKS:
            report = complete()
            report['checks'] = [row for row in report['checks'] if row['id'] != name]
            self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')
        for scenario in acceptance.SCENARIO_CHECKS.keys() - {'batch-freeze'}:
            report = complete()
            report['scenario'] = scenario
            self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')

    def test_changed_or_unobserved_edits_and_unsettled_barrier_fail(self):
        for key in ('freezeVerified', 'uncertainPublicationVerified'):
            report = complete()
            report['batch_freeze'][key] = not report['batch_freeze'][key]
            self.assertTrue(freeze.observation_failures(report))
        for key in complete()['batch_freeze']['barrierShutdown']:
            report = complete()
            report['batch_freeze']['barrierShutdown'][key] = None
            self.assertTrue(freeze.observation_failures(report), key)
        for prefix in ('Source', 'Profile'):
            report = complete()
            report['batch_freeze']['edited' + prefix + 'Sha256'] = report['batch_freeze']['original' + prefix + 'Sha256']
            self.assertTrue(freeze.observation_failures(report))
        for bad in (None, [], {}, {'barrierShutdown': {}, 'dialogs': [None]}):
            report = complete()
            report['batch_freeze'] = bad
            self.assertTrue(freeze.observation_failures(report))

    def test_native_original_returns_exact_dialog_count_and_zero_downloads_required(self):
        for key in ('actualReturnVerified', 'originalReturned', 'fixtureFailed'):
            report = complete()
            report['batch_freeze']['dialogs'][0][key] = key == 'fixtureFailed'
            self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')
        for count in (0, 1, 3):
            report = complete()
            report['dialog_fixture_shutdown'] = [{'shutdownConfirmed': True}] * count
            self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')
        for key in ('download_shutdown', 'downloads_final'):
            report = complete()
            report[key]['completed'] = 1
            self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')

    def test_policy_cleanup_process_and_worker_failures_still_fail(self):
        for field, changes in (('network_policy', {'failures': 1}), ('cleanup', {'profile_directory_retained': True}),
            ('worker_shutdown', {'ownedProcessesExited': False}), ('evaluation_shutdown', {'timedOut': True}),
            ('process', {'exit_code': 1, 'forced_termination': False}), ('process', {'exit_code': 0, 'forced_termination': True})):
            report = complete()
            report[field].update(changes)
            self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')


class FreezeManifestTests(unittest.TestCase):
    def setUp(self):
        self.sources = dict(zip(freeze.INPUT_NAMES, (b'original first', b'original second')))
        self.settings = {'style': {}, 'export': dict(freeze.OPTIONS),
            'profile': {'id': 'd'*32, 'revision': 1, 'name': freeze.PROFILE_NAME}}
        self.metadata = [{'width': 640, 'height': 216, 'padding': 12, 'sha256': 'c'*64} for _ in range(2)]
        self.value = {'sessionId': 'a'*32, 'status': 'completed', 'settings': copy.deepcopy(self.settings),
            'manifestName': freeze.MANIFEST_NAME, 'manifestError': None, 'items': [
                {'id': char*32, 'name': name, 'extension': '.nivram', 'status': 'exported',
                 'sourceSha256': hashlib.sha256(self.sources[name]).hexdigest(), 'outputName': output,
                 'outputSha256': 'c'*64, 'dimensions': {'width': 640, 'height': 216, 'padding': 12}, 'error': None}
                for char, name, output in zip(('b', 'c'), freeze.INPUT_NAMES, freeze.OUTPUT_NAMES)]}
        self.before = copy.deepcopy(self.value)
        self.before['status'] = 'running'
        self.before['items'][1].update(status='rendering', outputName=None, outputSha256=None, dimensions=None)

    def validate(self, value=None, manifest=None):
        value = self.value if value is None else value
        return freeze.validate_completed(value, manifest or {'schema': 'nivram-batch-export/1', **value},
            self.settings, self.sources, self.before, self.metadata, Path('isolated-profile').resolve())

    def test_original_settings_and_both_actual_png_identities_pass(self):
        self.assertEqual(self.validate(), self.value['items'])

    def test_edited_source_profile_revision_and_export_settings_cannot_replace_frozen_provenance(self):
        for change in ('source', 'profile-revision', 'profile-width', 'session'):
            value = copy.deepcopy(self.value)
            if change == 'source': value['items'][1]['sourceSha256'] = hashlib.sha256(freeze.EDITED_SOURCE).hexdigest()
            elif change == 'profile-revision': value['settings']['profile']['revision'] = 2
            elif change == 'profile-width': value['settings']['export']['width'] = 960
            else: value['sessionId'] = 'f'*32
            with self.subTest(change=change), self.assertRaises(AssertionError): self.validate(value)

    def test_changed_boundary_existing_output_or_false_actual_png_identity_fail(self):
        for index in (0, 1):
            for key, changed in (('id', 'f'*32), ('outputName', '../other.png'), ('outputSha256', 'f'*64),
                    ('dimensions', {'width': 640, 'height': 217, 'padding': 12}), ('status', 'failed'), ('error', {})):
                value = copy.deepcopy(self.value)
                value['items'][index][key] = changed
                with self.subTest(index=index, key=key), self.assertRaises(AssertionError): self.validate(value)
        self.metadata[1]['sha256'] = 'f'*64
        with self.assertRaises(AssertionError): self.validate()

    def test_uncertain_manifest_duplicate_ids_and_extra_fields_fail(self):
        for changes in ({'manifestError': 'uncertain'}, {'status': 'stopped'}, {'extra': True}):
            with self.subTest(changes=changes), self.assertRaises(AssertionError): self.validate(self.value | changes)
        value = copy.deepcopy(self.value)
        value['items'][1]['id'] = value['items'][0]['id']
        with self.assertRaises(AssertionError): self.validate(value)
        manifest = {'schema': 'nivram-batch-export/1', **copy.deepcopy(self.value)}
        manifest['settings']['export']['width'] = 320
        with self.assertRaises(AssertionError): self.validate(manifest=manifest)

    def test_terminal_ui_cannot_hide_partial_batch_or_retained_renderer(self):
        value = {'status': '2 of 2 PNG files saved. Manifest: nivram-batch-manifest.json. Completed files were retained.',
            'startDisabled': False, 'profileDisabled': False, 'refreshDisabled': False, 'cancelDisabled': True,
            'meterValue': 2, 'meterMax': 2, 'stageCount': 0,
            'rows': [f"{i['name']}: exported → {i['outputName']}" for i in self.value['items']]}
        freeze.validate_ui(value, self.value['items'])
        for changes in ({'status': 'complete'}, {'meterValue': 1}, {'rows': []}, {'stageCount': 1}, {'profileDisabled': True}):
            with self.subTest(changes=changes), self.assertRaises(AssertionError):
                freeze.validate_ui(value | changes, self.value['items'])


if __name__ == '__main__':
    unittest.main()
