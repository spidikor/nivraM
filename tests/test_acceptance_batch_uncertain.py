"""Native uncertainty gates and exact disk/memory provenance rejection tests."""
import copy
import hashlib
from pathlib import Path
import unittest

from nivram import acceptance, acceptance_batch_uncertain as uncertain
import test_acceptance_batch_gates as gates

CHECKS = uncertain.REQUIRED_CHECKS | {'editor-and-native-bridge-ready', 'native-window-loop-returned'}


def complete():
    report = gates.complete_batch_report()
    report.update(scenario='batch-uncertain', checks=[{'id': name, 'status': 'passed'} for name in CHECKS],
        process={'exit_code': 0, 'forced_termination': False},
        dialog_fixture_shutdown=[{'shutdownConfirmed': True}] * 4)
    cases = {}
    for mode in uncertain.MODES:
        cases[mode] = {'verified': True, 'latePrepareRejected': True, 'lateWriteRejected': True,
            'completeEditorStateUnchanged': True, 'retainedFilesUnchanged': True,
            'terminalStatus': 'cancelled' if mode == 'lost-ack' else 'stopped',
            'diskManifestStatus': 'cancelled' if mode == 'lost-ack' else 'running',
            'diskMatchesTerminal': mode == 'lost-ack', 'pngSha256': 'a'*64, 'manifestSha256': 'b'*64,
            'dialogs': [{'mode': kind, 'actualReturnVerified': True, 'originalReturned': True} for kind in ('open-multiple', 'folder')],
            'faultObserver': {'mode': mode, 'prepareCalls': 1, 'writeCalls': 1, 'cancelCalls': 1,
                'failCalls': 0, 'finishCalls': 0, 'faultCount': 1, 'manifestCalls': 4 if mode == 'lost-ack' else 3,
                'atomicCommits': 0 if mode == 'lost-ack' else 1, 'preparationReady': True,
                'writeResultStatus': 'exported', 'writeManifestError': None if mode == 'lost-ack' else uncertain.MANIFEST_ERROR,
                'shutdownConfirmed': True, 'methodsRestored': True, 'atomicWriterRestored': True}}
    report['batch_uncertain'] = {'uncertaintyVerified': True, 'cases': cases}
    return report


class UncertaintyGateTests(unittest.TestCase):
    def test_exact_six_checks_and_fixed_child_dispatch_pass(self):
        self.assertEqual(acceptance.SCENARIO_CHECKS['batch-uncertain'], CHECKS)
        final = acceptance.finalize_acceptance_report(complete())
        self.assertEqual((final['status'], final['acceptance_failures']), ('passed', []))
        self.assertEqual(acceptance.acceptance_child_command(Path('report'), Path('profile'), 'batch-uncertain')[-2:],
                         ['--scenario', 'batch-uncertain'])

    def test_missing_check_or_other_scenario_cannot_qualify(self):
        for name in CHECKS:
            report = complete()
            report['checks'] = [row for row in report['checks'] if row['id'] != name]
            self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')
        for scenario in acceptance.SCENARIO_CHECKS.keys() - {'batch-uncertain'}:
            report = complete(); report['scenario'] = scenario
            self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')

    def test_unobserved_fault_extra_work_or_unrestored_methods_reject(self):
        for mode in uncertain.MODES:
            for key in complete()['batch_uncertain']['cases'][mode]['faultObserver']:
                report = complete()
                fault = report['batch_uncertain']['cases'][mode]['faultObserver']
                fault[key] = 'invalid' if fault[key] is None else None
                with self.subTest(mode=mode, key=key): self.assertTrue(uncertain.observation_failures(report))
                report = complete()
                del report['batch_uncertain']['cases'][mode]['faultObserver'][key]
                with self.subTest(mode=mode, missing=key): self.assertTrue(uncertain.observation_failures(report))
            for key in ('prepareCalls', 'writeCalls', 'cancelCalls', 'failCalls', 'finishCalls'):
                report = complete()
                report['batch_uncertain']['cases'][mode]['faultObserver'][key] += 1
                self.assertTrue(uncertain.observation_failures(report))
            report = complete()
            report['batch_uncertain']['cases'][mode]['faultObserver']['writeCalls'] = True
            self.assertTrue(uncertain.observation_failures(report))

    def test_disk_memory_states_cannot_be_conflated_and_both_cases_required(self):
        for mode in uncertain.MODES:
            report = complete()
            report['batch_uncertain']['cases'][mode]['diskMatchesTerminal'] = mode != 'lost-ack'
            self.assertTrue(uncertain.observation_failures(report))
            report = complete()
            report['batch_uncertain']['cases'][mode]['diskManifestStatus'] = 'stopped'
            self.assertTrue(uncertain.observation_failures(report))
            report = complete(); del report['batch_uncertain']['cases'][mode]
            self.assertTrue(uncertain.observation_failures(report))
        for bad in (None, [], {}, {'uncertaintyVerified': True, 'cases': {'lost-ack': None, 'manifest-uncertain': None}}):
            report = complete(); report['batch_uncertain'] = bad
            self.assertTrue(uncertain.observation_failures(report))

    def test_original_dialog_returns_four_shutdowns_and_zero_downloads_are_required(self):
        for count in (0, 2, 3, 5):
            report = complete(); report['dialog_fixture_shutdown'] = [{'shutdownConfirmed': True}] * count
            self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')
        report = complete()
        report['batch_uncertain']['cases']['lost-ack']['dialogs'][0]['actualReturnVerified'] = False
        self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')
        for name in ('download_shutdown', 'downloads_final'):
            report = complete(); report[name]['completed'] = 1
            self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')

    def test_policy_cleanup_process_and_worker_failures_remain_failures(self):
        for field, changes in (('network_policy', {'failures': 1}), ('cleanup', {'profile_directory_retained': True}),
            ('worker_shutdown', {'ownedProcessesExited': False}), ('evaluation_shutdown', {'timedOut': True}),
            ('process', {'exit_code': 1, 'forced_termination': False}), ('process', {'exit_code': 0, 'forced_termination': True})):
            report = complete(); report[field].update(changes)
            self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')


class UncertaintyManifestTests(unittest.TestCase):
    def fixture(self, mode):
        names = uncertain.NAMES[mode]
        sources = dict(zip(names, (b'original one', b'original two')))
        settings = {'style': {}, 'export': dict(uncertain.OPTIONS), 'profile': None}
        metadata = {'sha256': 'e'*64, 'width': 640, 'height': 216, 'padding': 12}
        chosen = {'sessionId': 'a'*32, 'status': 'running', 'manifestName': uncertain.MANIFEST_NAME,
            'manifestError': None, 'settings': copy.deepcopy(settings), 'items': [
                {'id': code*32, 'name': name, 'extension': '.nivram', 'status': 'pending',
                 'sourceSha256': hashlib.sha256(sources[name]).hexdigest(), 'outputName': None,
                 'outputSha256': None, 'dimensions': None, 'error': None} for code, name in zip(('b', 'c'), names)]}
        receipt = copy.deepcopy(chosen)
        receipt['status'] = 'cancelled' if mode == 'lost-ack' else 'stopped'
        receipt['manifestError'] = None if mode == 'lost-ack' else uncertain.MANIFEST_ERROR
        receipt['items'][0].update(status='exported', outputName=Path(names[0]).stem + ' (2).png',
            outputSha256=metadata['sha256'], dimensions={k: metadata[k] for k in ('width', 'height', 'padding')})
        receipt['items'][1].update(status='cancelled', error=dict(uncertain.CANCEL_ERROR))
        written = {'status': 'exported', 'item': copy.deepcopy(receipt['items'][0]), 'manifestError': receipt['manifestError']}
        disk = {'schema': 'nivram-batch-export/1', **copy.deepcopy(receipt)}
        if mode == 'manifest-uncertain':
            disk.update(status='running', manifestError=None)
            disk['items'][1] = copy.deepcopy(chosen['items'][1])
        return [mode, receipt, disk, chosen, written, settings, sources, metadata, Path('synthetic-profile').resolve()]

    def test_two_distinct_disk_memory_contracts_pass(self):
        for mode in uncertain.MODES:
            good, cancelled = uncertain.validate_terminal(*self.fixture(mode))
            self.assertEqual((good['status'], cancelled['status']), ('exported', 'cancelled'))

    def test_fabricated_terminal_disk_manifest_and_stale_ack_manifest_fail(self):
        args = self.fixture('manifest-uncertain')
        args[2] = {'schema': 'nivram-batch-export/1', **copy.deepcopy(args[1])}
        with self.assertRaises(AssertionError): uncertain.validate_terminal(*args)
        args = self.fixture('lost-ack'); args[2]['status'] = 'running'
        with self.assertRaises(AssertionError): uncertain.validate_terminal(*args)

    def test_changed_original_request_source_settings_and_profile_fail(self):
        for mode in uncertain.MODES:
            for change in ('session', 'source', 'settings', 'profile'):
                args = self.fixture(mode)
                if change == 'session': args[1]['sessionId'] = 'f'*32
                elif change == 'source': args[1]['items'][1]['sourceSha256'] = 'f'*64
                elif change == 'settings': args[1]['settings']['export']['width'] = 960
                else: args[1]['settings']['profile'] = {'id': 'f'*32, 'revision': 2, 'name': 'different'}
                with self.subTest(mode=mode, change=change), self.assertRaises(AssertionError): uncertain.validate_terminal(*args)

    def test_changed_png_or_cancelled_item_and_boolean_dimensions_fail(self):
        for mode in uncertain.MODES:
            for field, value in (('outputSha256', 'f'*64), ('outputName', '../other.png'), ('status', 'failed'),
                    ('dimensions', {'width': True, 'height': 216, 'padding': 12})):
                args = self.fixture(mode); args[1]['items'][0][field] = value
                with self.subTest(mode=mode, field=field), self.assertRaises(AssertionError): uncertain.validate_terminal(*args)
            args = self.fixture(mode); args[1]['items'][1]['outputName'] = 'unexpected.png'
            with self.assertRaises(AssertionError): uncertain.validate_terminal(*args)
            args = self.fixture(mode); args[7]['sha256'] = 'f'*64
            with self.assertRaises(AssertionError): uncertain.validate_terminal(*args)

    def test_lost_manifest_error_false_write_status_and_duplicate_ids_fail(self):
        for mode in uncertain.MODES:
            args = self.fixture(mode); args[4]['status'] = 'failed'
            with self.assertRaises(AssertionError): uncertain.validate_terminal(*args)
            args = self.fixture(mode); args[1]['items'][1]['id'] = args[1]['items'][0]['id']
            with self.assertRaises(AssertionError): uncertain.validate_terminal(*args)
        args = self.fixture('manifest-uncertain'); args[1]['manifestError'] = None
        with self.assertRaises(AssertionError): uncertain.validate_terminal(*args)

    def test_ui_must_display_terminal_rows_and_restore_controls(self):
        args = self.fixture('lost-ack'); good, cancelled = uncertain.validate_terminal(*args)
        value = {'status': uncertain.STOPPED_MESSAGE, 'startDisabled': False, 'profileDisabled': False,
            'refreshDisabled': False, 'cancelDisabled': True, 'meterValue': 2, 'meterMax': 2, 'stageCount': 0,
            'rows': [f"{good['name']}: exported → {good['outputName']}",
                     f"{cancelled['name']}: cancelled — {uncertain.CANCEL_ERROR['message']}"]}
        uncertain.validate_ui(value, good, cancelled)
        for changes in ({'status': 'complete'}, {'rows': []}, {'meterValue': 1}, {'stageCount': 1}, {'cancelDisabled': False}):
            with self.subTest(changes=changes), self.assertRaises(AssertionError):
                uncertain.validate_ui(value | changes, good, cancelled)


if __name__ == '__main__':
    unittest.main()
