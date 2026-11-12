"""Diagnostic close must add evidence without weakening the uncertainty workflow."""
import copy
import os
from pathlib import Path
import tempfile
import unittest

from nivram import acceptance, acceptance_uncertainty_close as close
from test_acceptance_batch_close import completed as completed_close
from test_acceptance_batch_uncertain import complete as completed_uncertainty


def files():
    result = {}
    for names in close.NAMES.values():
        folder = names[0][0]
        for name in names:
            result[name] = b'synthetic input'
        for name in (Path(names[0]).stem + '.png', Path(names[0]).stem + ' (2).png', close.MANIFEST_NAME):
            result[folder + '/' + name] = b'synthetic retained output'
    return result


def fixture(root):
    directory = root / 'synthetic fixtures'
    directory.mkdir()
    for name, data in files().items():
        path = directory / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)


def completed():
    report = completed_uncertainty()
    report['scenario'] = 'batch-uncertain-close'
    report['checks'].append({'id': 'native-production-close-finalization', 'status': 'passed'})
    report['production_close'] = completed_close()['production_close']
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); fixture(root)
        rows = close.fixture_inventory(root)
    report['uncertainty_close'] = dict(workflowVerifiedBeforeClose=True, approvedCloseVerified=True,
        actualClosedObserved=True, exactRecoveryVerified=True, retainedFilesVerifiedAfterClose=True,
        verifyThreadFinished=True, batchManagerClosed=True, fallbackDestroy=False, completionSeconds=3,
        fixturesBefore=rows, fixturesAfter=copy.deepcopy(rows),
        closeRequest={'message': 'WM_CLOSE', 'ownedWindowVerified': True, 'posted': True},
        confirmationShutdown={'shutdownConfirmed': True, 'methodsRestored': True},
        confirmations=[dict(decision=True, originalCalled=True, originalReturned=True,
            actualReturnVerified=True, buttonPosted=True, workerExited=True)])
    return report


class DiagnosticCloseTests(unittest.TestCase):
    def test_complete_diagnostic_has_all_original_checks_plus_finalization(self):
        self.assertEqual(acceptance.SCENARIO_CHECKS['batch-uncertain-close'],
            acceptance.BATCH_UNCERTAIN_REQUIRED_CHECKS | {'native-production-close-finalization'})
        report = completed()
        self.assertEqual(close.production_failures(report), [])
        self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'passed')

    def test_legacy_uncertainty_keeps_its_separate_teardown_contract(self):
        self.assertNotIn('batch-uncertain', acceptance.PRODUCTION_CLOSE_SCENARIOS)
        self.assertEqual(acceptance.finalize_acceptance_report(completed_uncertainty())['status'], 'passed')

    def test_missing_any_workflow_or_finalization_check_is_rejected(self):
        for check in acceptance.SCENARIO_CHECKS['batch-uncertain-close']:
            report = completed()
            report['checks'] = [row for row in report['checks'] if row['id'] != check]
            self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed', check)

    def test_missing_workflow_observation_cannot_be_promoted_by_clean_close(self):
        report = completed(); report['batch_uncertain']['uncertaintyVerified'] = False
        self.assertIn('uncertainty_observations_missing', close.production_failures(report))

    def test_every_close_fact_and_real_confirmation_is_required(self):
        for key in ('workflowVerifiedBeforeClose', 'approvedCloseVerified', 'actualClosedObserved',
                    'exactRecoveryVerified', 'retainedFilesVerifiedAfterClose', 'verifyThreadFinished', 'batchManagerClosed'):
            report = completed(); report['uncertainty_close'][key] = False
            self.assertTrue(close.production_failures(report), key)
        for key in ('decision', 'originalCalled', 'originalReturned', 'actualReturnVerified', 'buttonPosted', 'workerExited'):
            report = completed(); report['uncertainty_close']['confirmations'][0][key] = False
            self.assertTrue(close.production_failures(report), key)

    def test_fallback_destroy_and_wrong_confirmation_count_are_rejected(self):
        report = completed(); report['uncertainty_close']['fallbackDestroy'] = True
        self.assertTrue(close.production_failures(report))
        for rows in ([], [{}, {}], None):
            report = completed(); report['uncertainty_close']['confirmations'] = rows
            self.assertTrue(close.production_failures(report))

    def test_unchanged_completion_bound_rejects_late_nonfinite_and_boolean_times(self):
        for value in (40, 41, -1, float('inf'), float('nan'), True, '3', None):
            report = completed(); report['uncertainty_close']['completionSeconds'] = value
            self.assertIn('uncertainty_close_completion_bound', close.production_failures(report))

    def test_changed_retained_file_or_duplicate_identity_is_rejected(self):
        report = completed(); facts = report['uncertainty_close']
        facts['fixturesAfter'][-1]['sha256'] = '0' * 64
        self.assertTrue(close.production_failures(report))
        for bad in (None, [], [{}] * 12):
            report = completed(); report['uncertainty_close'].update(fixturesBefore=bad, fixturesAfter=bad)
            self.assertTrue(close.production_failures(report))

    def test_all_production_resource_stages_remain_required(self):
        for name in completed()['production_close']['finalization']['stages']:
            report = completed(); del report['production_close']['finalization']['stages'][name]
            self.assertTrue(close.production_failures(report), name)

    def test_four_choosers_and_zero_downloads_remain_required(self):
        for count in (0, 1, 2, 3, 5):
            report = completed(); report['dialog_fixture_shutdown'] = [{'shutdownConfirmed': True}] * count
            self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')
        report = completed(); report['download_shutdown']['completed'] = 1
        self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')

    def test_bounded_fixture_snapshot_detects_content_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture(root)
            before = close.fixture_inventory(root)
            self.assertTrue(close.valid_inventory(before))
            (root / 'synthetic fixtures' / 'a1.nivram').write_bytes(b'changed')
            self.assertNotEqual(before, close.fixture_inventory(root))

    def test_extra_file_and_hardlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture(root)
            extra = root / 'synthetic fixtures' / 'extra.txt'
            extra.write_bytes(b'unexpected')
            with self.assertRaises(AssertionError): close.fixture_inventory(root)
            extra.unlink()
            source = root / 'synthetic fixtures' / 'a1.nivram'
            os.link(source, root / 'outside-link')
            with self.assertRaises((AssertionError, ValueError, OSError)): close.fixture_inventory(root)


if __name__ == '__main__':
    unittest.main()
