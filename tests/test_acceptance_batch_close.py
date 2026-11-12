"""Production-close qualification must retain native ownership and failure gates."""
import copy
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from nivram import acceptance, acceptance_batch_close as close, desktop
from test_acceptance_batch_gates import complete_batch_report


def completed():
    report = complete_batch_report()
    report['scenario'] = 'batch-close'
    report['checks'] = [{'id': k, 'status': 'passed'} for k in close.REQUIRED_CHECKS |
        {'editor-and-native-bridge-ready', 'native-window-loop-returned'}]
    report['dialog_fixture_shutdown'] *= 2
    report['production_close'] = {
        'startup_status': 'passed', 'network_policy': copy.deepcopy(report['network_policy']),
        'webview_cleanup': copy.deepcopy(report['cleanup']['managed_session']),
        'download_shutdown': copy.deepcopy(report['download_shutdown']),
        'owned_processes': {'confirmed': True, 'remaining_children': 0},
        'native_lifetime': {'errors': [], 'finalizer_alive': False, 'finalizer_finished': True,
            'closed_observed': True, 'guard_released': True, 'watchdog_release': False},
        'finalization': {'started': True, 'completed': True, 'workers_alive': 0, 'stages': {
            'optical_setup': {'status': 'returned', 'result': True},
            'images': {'status': 'returned', 'result': True}, 'batch': {'status': 'returned', 'result': None},
            'reference_exports': {'status': 'returned', 'result': {'closed': True, 'active': 0}},
            'population_exports': {'status': 'returned', 'result': {'closed': True, 'active': 0}},
            'downloads': {'status': 'returned'}, 'webview': {'status': 'returned'},
            'server': {'status': 'returned', 'result': {'shutdown': {'status': 'confirmed', 'ownedProcessesExited': True}}}}}}
    report['batch_close'] = {'declinedCloseVerified': True, 'approvedCloseVerified': True,
        'fallbackDestroy': False, 'verifyThreadFinished': True, 'scientificWorkerInterruptionVerified': False,
        'batchManagerClosed': True, 'confirmationShutdown': {'shutdownConfirmed': True, 'methodsRestored': True},
        'confirmations': [{'decision': decision, 'originalCalled': True, 'originalReturned': True,
            'actualReturnVerified': True, 'buttonPosted': True, 'workerExited': True} for decision in (False, True)],
        'journeys': [{'decision': decision, 'retained': True, 'lateWriteRejected': True, 'exactRecoveryVerified': True,
            'editorVerifiedBeforeClose': True, 'editorVerifiedAfterDecline': not decision,
            'lateResponseRejectedAfterActualClose': decision, 'barrierShutdown': {'shutdownConfirmed': True,
                'methodsRestored': True, 'lateResultStatus': 'cancelled', 'lateResultHadScene': False,
                'holdTimedOut': False}} for decision in (False, True)]}
    return report


class CloseGateTests(unittest.TestCase):
    def test_complete_six_checks_and_all_real_lifecycle_gates_pass(self):
        self.assertEqual(close.production_failures(completed()), [])
        self.assertEqual(acceptance.finalize_acceptance_report(completed())['status'], 'passed')

    def test_missing_functional_check_or_any_resource_stage_fails(self):
        for name in acceptance.BATCH_CLOSE_REQUIRED_CHECKS:
            report = completed()
            report['checks'] = [r for r in report['checks'] if r['id'] != name]
            self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')
        for name in completed()['production_close']['finalization']['stages']:
            report = completed()
            del report['production_close']['finalization']['stages'][name]
            self.assertTrue(close.production_failures(report))

    def test_forced_lifetime_or_late_callbacks_and_incomplete_cleanup_fail(self):
        for key, value in [('closed_observed', False), ('watchdog_release', True), ('guard_released', False),
                           ('finalizer_alive', True), ('finalizer_finished', False), ('errors', [{'stage':'synthetic'}])]:
            report = completed(); report['production_close']['native_lifetime'][key] = value
            self.assertTrue(close.production_failures(report), key)
        for field, key, value in [('owned_processes', 'remaining_children', 1),
                ('webview_cleanup', 'runtime_cache_removed', False), ('finalization', 'workers_alive', 1),
                ('download_shutdown', 'active', 1), ('network_policy', 'failures', 1)]:
            report = completed(); report['production_close'][field][key] = value
            self.assertTrue(close.production_failures(report), key)

    def test_fallback_or_unfinished_review_and_wrong_original_return_fail(self):
        for key, value in [('fallbackDestroy', True), ('verifyThreadFinished', False), ('batchManagerClosed', False),
                           ('declinedCloseVerified', False), ('approvedCloseVerified', False)]:
            report = completed(); report['batch_close'][key] = value
            self.assertTrue(close.production_failures(report), key)
        for key, value in [('actualReturnVerified', False), ('decision', 0), ('buttonPosted', False), ('workerExited', False)]:
            report = completed(); report['batch_close']['confirmations'][0][key] = value
            self.assertTrue(close.production_failures(report), key)

    def test_either_batch_missing_or_late_scene_accepted_cannot_pass(self):
        for value in (None, [], [{}], 'bad'):
            report = completed(); report['batch_close']['journeys'] = value
            self.assertTrue(close.production_failures(report))
        for index in (0, 1):
            for key in ('retained', 'lateWriteRejected', 'exactRecoveryVerified'):
                report = completed(); report['batch_close']['journeys'][index][key] = False
                self.assertTrue(close.production_failures(report))
            report = completed(); report['batch_close']['journeys'][index]['barrierShutdown']['lateResultHadScene'] = True
            self.assertTrue(close.production_failures(report))

    def test_exact_four_choosers_zero_downloads_and_existing_process_gates(self):
        for count in (0, 1, 2, 3, 5):
            report = completed(); report['dialog_fixture_shutdown'] = [{'shutdownConfirmed': True}] * count
            self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')
        report = completed(); report['download_shutdown']['completed'] = 1
        self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')
        report = completed(); report['process'] = {'exit_code': 0, 'forced_termination': True}
        self.assertEqual(acceptance.finalize_acceptance_report(report)['status'], 'failed')

    def test_malformed_production_report_fails_without_crashing(self):
        for value in (None, [], {}, {'native_lifetime': None}, {'startup_status': 'passed', 'finalization': []}):
            report = completed(); report['production_close'] = value
            self.assertTrue(close.production_failures(report))


class WiringTests(unittest.TestCase):
    def test_same_production_factory_owns_review_disposal_and_finalization(self):
        class Event:
            def __init__(self): self.handlers = []
            def __iadd__(self, fn): self.handlers.append(fn); return self
        for review in (False, True):
            window = NS(events=NS(closing=Event()), evaluate_js=Mock())
            api, downloads, session = Mock(), Mock(), Mock()
            group = Mock()
            with patch('nivram.desktop._finalization_group', return_value=group), \
                    patch('nivram.native_lifetime.NativeLifetime') as lifetime:
                coordinator, actual, native = desktop._attach_lifecycle(window, api, None, None, downloads, session, review=review)
            self.assertIs(actual, group)
            self.assertIs(native, lifetime.return_value)
            callbacks = lifetime.call_args.kwargs
            self.assertIs(callbacks['cancel_on_ui'], downloads.begin_shutdown_on_ui)
            self.assertIs(callbacks['dispose_on_ui'], session.dispose_on_ui)
            callbacks['finalize'](); group.run.assert_called_once_with(timeout_seconds=30)
            if review:
                self.assertEqual(window.events.closing.handlers, [coordinator.request_close])
                self.assertEqual(callbacks['on_closed'], coordinator.observe_closed)
            else:
                self.assertIsNone(coordinator); self.assertEqual(window.events.closing.handlers, [])


if __name__ == '__main__': unittest.main()
