"""Exercise desktop.main result/report ordering with fake native boundaries."""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from nivram import desktop


class Event:
    def __iadd__(self, callback):
        return self


class DesktopSmokeResultTests(unittest.TestCase):
    def run_main(self, *, cleanup=None, policy=None, lifetime=None, finalization=None,
                 image_error=False, image_result=True, setup_result=True, reference_result=None, population_result=None, server_shutdown=None,
                 startup_error=False, loop_error=False, smoke=True,
                 job_receipt=None, job_snapshot_error=False):
        """Run real orchestration/finalization; replace only OS/UI/service edges."""
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            report_path = root / 'smoke.json'
            stopped = threading.Event()
            calls = []
            policy_state = {'status': 'installed', 'installed_before_initial_navigation': True,
                            'failures': 0, 'failure_code': None}
            cleanup_state = {'status': 'active', 'native_profile_matched': True,
                             'collection_exit_observed': False, 'collection_identity_matched': False,
                             'control_disposed': False, 'runtime_cache_removed': False, 'reason': None}
            download_state = {'status': 'installed', 'shutdown_confirmed': False, 'active': 0,
                              'failures': 0, 'failure_code': None}
            lifetime_state = {'status': 'finalizing', 'closed_observed': True, 'guard_released': True,
                              'watchdog_release': False, 'finalizer_finished': True,
                              'finalizer_alive': False, 'errors': []}
            lifetime_state.update(lifetime or {})
            server = SimpleNamespace(server_port=12345, import_preparation=object(),
                                     serve_forever=lambda: stopped.wait(2),
                                     shutdown=stopped.set, server_close=Mock(),
                                     scientific_jobs_status=lambda: {'activeJobs': 0, 'acceptingJobs': False,
                                         'shutdown': {'status': 'confirmed', 'ownedProcessesExited': True,
                                                      **(server_shutdown or {})}})
            images = Mock()
            images.close.return_value = image_result
            if image_error:
                images.close.side_effect = OSError('synthetic close failure')
            api = SimpleNamespace(_images=images, _optical_setup=Mock(), _batch=Mock(),
                                  _reference_exports=Mock(), _population_exports=Mock(), _set_window=Mock(),
                                  _store=Mock(), _appearance=Mock())
            api._batch.close.return_value = None
            api._optical_setup.close.return_value = setup_result
            api._reference_exports.close.return_value = reference_result or {'closed': True, 'active': 0}
            api._population_exports.close.return_value = population_result or {'closed': True, 'active': 0}

            def finish_session(**kwargs):
                calls.append('session.finish')
                cleanup_state.update(status='removed', collection_exit_observed=True,
                                     collection_identity_matched=True, control_disposed=True,
                                     runtime_cache_removed=True)
                cleanup_state.update(cleanup or {})
                return dict(cleanup_state)

            session = SimpleNamespace(storage_path=root / 'owned-webview', attach=Mock(),
                                      dispose_on_ui=Mock(), finish_shutdown=finish_session,
                                      snapshot=lambda: dict(cleanup_state))

            def finish_downloads(**kwargs):
                download_state.update(status='closed', shutdown_confirmed=True)
                download_state.update(self.download_override)
                return dict(download_state)

            downloads = SimpleNamespace(begin_shutdown_on_ui=Mock(), finish_shutdown=finish_downloads,
                                        snapshot=lambda: dict(download_state))
            ready = threading.Event()
            ready.set()
            window = SimpleNamespace(events=SimpleNamespace(_pywebviewready=ready, loaded=ready,
                                                             closing=Event()), destroy=Mock())
            window.evaluate_js = Mock(return_value={'ready': True, 'bridge': True, 'status': 'Ready'})
            if startup_error:
                window.evaluate_js.side_effect = RuntimeError('synthetic startup failure')

            class Lifetime:
                def __init__(self, _window, **callbacks):
                    self.callbacks = callbacks
                def finish(self, **kwargs):
                    calls.append('lifetime.finish')
                    self.callbacks['cancel_on_ui']()
                    self.callbacks['dispose_on_ui']()
                    self.callbacks['finalize']()
                    policy_state.update(policy or {})  # A failure after startup's snapshot.
                    return deepcopy(lifetime_state)

            def start(callback, **kwargs):
                calls.append('webview.start')
                if callback is not None:
                    callback()
                self.assertFalse(report_path.exists(), 'No final report before owned cleanup.')
                if loop_error:
                    raise RuntimeError('synthetic native loop failure after readiness')

            fake_webview = SimpleNamespace(settings={}, create_window=Mock(return_value=window), start=start)
            original_group = desktop._finalization_group

            def group_factory(*args):
                group = original_group(*args)
                if finalization:
                    snapshot = group.snapshot
                    def altered_snapshot():
                        value = snapshot()
                        value.update(deepcopy(finalization))
                        return value
                    group.snapshot = altered_snapshot
                return group

            def create_server():
                calls.append('server.create')
                return server

            # Load/patch actual modules before replacing the webview import seam.
            stack.enter_context(patch('nivram.server.make_server', side_effect=create_server))
            stack.enter_context(patch('nivram.native_bridge.expose_desktop_api'))
            stack.enter_context(patch('nivram.webview_policy.attach_policy',
                                      return_value=SimpleNamespace(snapshot=lambda: dict(policy_state))))
            stack.enter_context(patch('nivram.webview_downloads.attach_downloads', return_value=downloads))
            stack.enter_context(patch('nivram.webview_cleanup.ManagedWebViewSession', return_value=session))
            stack.enter_context(patch('nivram.native_lifetime.NativeLifetime', Lifetime))
            stack.enter_context(patch('nivram.lifecycle.CloseCoordinator'))
            stack.enter_context(patch.object(desktop, '_finalization_group', side_effect=group_factory))
            api_factory = stack.enter_context(patch.object(desktop, 'DesktopApi', return_value=api))
            lock = stack.enter_context(patch.object(desktop, 'InstanceLock')).return_value
            lock.acquire.return_value = True
            response = stack.enter_context(patch.object(desktop, 'urlopen')).return_value
            response.__enter__.return_value.status = 200
            # main may establish process ownership before native startup. Never
            # assign the test runner itself to a real Windows job.
            job_state = {'status': 'confirmed', 'confirmed': True,
                         'forced_count': 0, 'remaining_children': 0}
            job_state.update(job_receipt or {})

            def snapshot_job():
                calls.append('job.snapshot')
                if job_snapshot_error:
                    raise OSError('synthetic ownership receipt failure')
                return dict(job_state)

            fake_job = SimpleNamespace(snapshot=snapshot_job,
                                       terminate_children=lambda **kwargs: dict(job_state))

            def acquire_job():
                calls.append('job.acquire')
                return fake_job

            fake_job_module = SimpleNamespace(OwnedProcessJob=SimpleNamespace(
                for_current_process=Mock(side_effect=acquire_job)))
            stack.enter_context(patch.dict(sys.modules, {'webview': fake_webview,
                                                        'nivram.owned_process_job': fake_job_module}))
            args = ['--data-dir', str(root / 'profile')]
            if smoke:
                args += ['--smoke-report', str(report_path)]
            try:
                result = desktop.main(args)
            finally:
                stopped.set()
            lock.close.assert_called_once_with()
            api_factory.assert_called_once_with(root / 'profile', preparation_service=server.import_preparation)
            server.server_close.assert_called_once_with()
            images.close.assert_called_once_with()
            expected_calls = ['job.acquire', 'server.create', 'webview.start',
                              'lifetime.finish', 'session.finish']
            if smoke:
                expected_calls.append('job.snapshot')
            self.assertEqual(calls, expected_calls)
            report = json.loads(report_path.read_text(encoding='utf-8')) if smoke else None
            self.assertEqual(report_path.exists(), smoke)
            return result, report

    def setUp(self):
        self.download_override = {}

    def assert_failed(self, **scenario):
        result, report = self.run_main(**scenario)
        self.assertEqual(result, 1)
        self.assertEqual(report['status'], 'failed')
        return report

    def test_retained_runtime_receipt_cannot_be_reported_as_passed_startup(self):
        report = self.assert_failed(cleanup={'status': 'retained', 'runtime_cache_removed': False,
            'collection_exit_observed': False, 'collection_identity_matched': False,
            'reason': 'runtime_collection_exit_unconfirmed'})
        self.assertTrue(report['observations']['ready'])
        self.assertEqual(report['webview_cleanup']['reason'], 'runtime_collection_exit_unconfirmed')

    def test_exact_completed_cleanup_returns_zero_and_saved_pass(self):
        result, report = self.run_main()
        self.assertEqual(result, 0)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['webview_cleanup']['status'], 'removed')
        self.assertTrue(report['webview_cleanup']['runtime_cache_removed'])
        self.assertTrue(report['finalization']['completed'])
        self.assertEqual(report['finalization']['workers_alive'], 0)

    def test_job_is_acquired_before_server_creation_and_native_startup(self):
        # run_main checks the actual main orchestration trace, including that
        # the final ownership snapshot is collected only after session cleanup.
        result, report = self.run_main()
        self.assertEqual(result, 0)
        self.assertEqual(report['status'], 'passed')
        self.assertTrue(report['owned_processes']['confirmed'])
        self.assertEqual(report['owned_processes']['remaining_children'], 0)

    def test_unconfirmed_owned_processes_fail_after_successful_native_cleanup(self):
        for values in ({'status': 'unconfirmed', 'confirmed': False, 'remaining_children': 2},
                       {'status': 'unconfirmed', 'confirmed': False, 'remaining_children': None},
                       {'status': 'unconfirmed', 'confirmed': False, 'remaining_children': 0},
                       {'status': 'confirmed', 'confirmed': True, 'remaining_children': 2}):
            with self.subTest(values=values):
                report = self.assert_failed(job_receipt=values)
                self.assertEqual(report['startup_status'], 'passed')
                self.assertTrue(report['webview_cleanup']['runtime_cache_removed'])
                self.assertEqual(report['smoke_failures'], ['owned_process_exit_unconfirmed'])
                for key, value in values.items():
                    self.assertEqual(report['owned_processes'][key], value)

    def test_raising_ownership_snapshot_saves_failed_unavailable_receipt(self):
        report = self.assert_failed(job_snapshot_error=True)
        self.assertEqual(report['startup_status'], 'passed')
        self.assertEqual(report['smoke_failures'], ['owned_process_exit_unconfirmed'])
        self.assertEqual(report['owned_processes'], {
            'status': 'unconfirmed', 'confirmed': False, 'remaining_children': None,
            'reason': 'ownership_receipt_unavailable'})

    def test_removed_label_requires_verified_cleanup_fields(self):
        for field in ('runtime_cache_removed', 'collection_exit_observed',
                      'collection_identity_matched', 'native_profile_matched', 'control_disposed'):
            with self.subTest(field=field):
                self.assert_failed(cleanup={field: False})

    def test_late_policy_failure_changes_final_status(self):
        for values in ({'status': 'failed'}, {'failures': 1},
                       {'installed_before_initial_navigation': False}):
            with self.subTest(values=values):
                self.assert_failed(policy=values)

    def test_finalization_failed_stage_is_not_success_even_when_all_stages_settle(self):
        report = self.assert_failed(image_error=True)
        self.assertTrue(report['finalization']['completed'])
        self.assertEqual(report['finalization']['stages']['images']['status'], 'failed')

    def test_pending_or_live_finalization_is_not_success(self):
        for values in ({'completed': False}, {'workers_alive': 1},
                       {'stages': {'webview': {'status': 'pending'}}}):
            with self.subTest(values=values):
                self.assert_failed(finalization=values)

    def test_returned_resource_shutdown_failure_is_not_completion(self):
        for scenario in ({'image_result': False},
                         {'setup_result': False},
                         {'reference_result': {'closed': False, 'active': 1}},
                         {'population_result': {'closed': False, 'active': 1}},
                         {'server_shutdown': {'status': 'unconfirmed', 'ownedProcessesExited': False}}):
            with self.subTest(scenario=scenario):
                report = self.assert_failed(**scenario)
                self.assertTrue(report['finalization']['completed'])
                self.assertTrue(all(value['status'] == 'returned'
                                    for value in report['finalization']['stages'].values()))

    def test_unconfirmed_download_shutdown_is_not_success(self):
        for values in ({'status': 'shutdown_unconfirmed'}, {'shutdown_confirmed': False}, {'active': 1}):
            with self.subTest(values=values):
                self.download_override = values
                self.assert_failed()

    def test_native_lifetime_error_or_unfinished_owner_is_not_success(self):
        for values in ({'errors': [{'stage': 'native_dispose', 'error': 'OSError'}]},
                       {'finalizer_alive': True}, {'finalizer_finished': False},
                       {'closed_observed': False}, {'guard_released': False}, {'watchdog_release': True}):
            with self.subTest(values=values):
                self.assert_failed(lifetime=values)

    def test_startup_failure_stays_failed_after_successful_cleanup(self):
        self.assert_failed(startup_error=True)

    def test_native_loop_exception_after_readiness_is_not_saved_as_passed(self):
        self.assert_failed(loop_error=True)

    def test_ordinary_non_smoke_return_is_unchanged_by_retained_cleanup(self):
        result, report = self.run_main(smoke=False, cleanup={'status': 'retained',
            'runtime_cache_removed': False, 'reason': 'runtime_collection_exit_unconfirmed'})
        self.assertEqual(result, 0)
        self.assertIsNone(report)


if __name__ == '__main__':
    unittest.main()
