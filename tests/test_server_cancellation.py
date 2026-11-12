import http.client
import json
import secrets
import sys
import threading
import time
import unittest
from unittest.mock import patch

from nivram.naming_runtime import NamingRuntime
from nivram.server import NamingJobError, NamingJobs, make_server
from nivram.worker_processes import CancellationToken


def identity(**changes):
    return {'clientId': secrets.token_hex(16), 'jobId': secrets.token_hex(16),
            'expiresAt': int(time.time()*1000)+30000, **changes}


def held_worker(graph, revision, *, supervisor, cancellation_token, **options):
    # Real owned child process with synthetic work, not a substitute chemistry result.
    supervisor.run([sys.executable, '-c', 'import time; time.sleep(30)'],
                   timeout_seconds=20, cancellation_token=cancellation_token)
    raise AssertionError('The synthetic long worker should be cancelled.')


def fast_result(graph, revision, **options):
    return {'status': 'generated_unverified', 'name': 'synthetic-test-result',
            'inputIdentity': {'documentRevision': revision}, 'warnings': [],
            'engine': {'name': 'test-boundary'}, 'roundTrip': {'status': 'unavailable'}}


class ServerCancellationTests(unittest.TestCase):
    def setUp(self):
        self.server = self.make_server()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.requests = []

    def make_server(self):
        with patch('nivram.naming_runtime.discover_naming_runtime', return_value=NamingRuntime('unavailable', None, 'Synthetic test runtime boundary.')):
            server = make_server(0)
            self.assertTrue(server.naming_runtime_ready.wait(2))
        return server

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        for thread in self.requests:
            thread.join(3)
            self.assertFalse(thread.is_alive())

    def request(self, route, body, *, token=None, origin=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=8)
        headers = {'Content-Type': 'application/json',
                   'X-Nivram-Token': self.server.api_token if token is None else token,
                   'Origin': origin or f'http://127.0.0.1:{self.server.server_port}'}
        try:
            connection.request('POST', route, body=json.dumps(body), headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def start_name(self, job):
        outcome = {}
        def request():
            try:
                outcome['response'] = self.request('/api/name', {**job, 'graph': {}, 'documentRevision': 7})
            except Exception as error:
                outcome['error'] = error
        thread = threading.Thread(target=request, daemon=True)
        self.requests.append(thread)
        thread.start()
        return thread, outcome

    def wait_worker(self):
        deadline = time.monotonic()+5
        while self.server.worker_supervisor.active_count == 0 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.server.worker_supervisor.active_count, 1)

    def test_cancel_endpoint_requires_session_token_and_same_origin(self):
        for changes in ({'token': ''}, {'origin': 'https://example.invalid'}):
            self.assertEqual(self.request('/api/cancel-job', identity(), **changes)[0], 403)
        self.assertEqual(self.server.naming_jobs.snapshot()['recentJobIds'], 0)

    def test_cancel_before_registration_never_starts_naming(self):
        job = identity()
        status, receipt = self.request('/api/cancel-job', job)
        self.assertEqual((status, receipt['status']), (200, 'cancelled_before_registration'))
        with patch('nivram.naming.name_structure') as name:
            status, receipt = self.request('/api/name', {**job, 'graph': {}})
        self.assertEqual((status, receipt['status']), (409, 'cancelled'))
        name.assert_not_called()

    def test_http_cancellation_stops_an_owned_child_and_keeps_properties_responsive(self):
        job = identity()
        with patch('nivram.naming.name_structure', side_effect=held_worker):
            thread, outcome = self.start_name(job)
            self.wait_worker()
            # This would block if naming held the shared chemistry lock.
            status, properties = self.request('/api/properties', {'graph': {'atoms': [], 'bonds': []}, 'documentRevision': 8})
            self.assertEqual(status, 200)
            self.assertEqual(properties['inputIdentity']['documentRevision'], 8)
            status, cancelled = self.request('/api/cancel-job', job)
            self.assertEqual((status, cancelled['status']), (200, 'cancellation_requested'))
            thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome['response'][0], 409)
        self.assertEqual(outcome['response'][1]['status'], 'cancelled')
        self.assertEqual(self.server.worker_supervisor.active_count, 0)
        self.assertEqual(self.server.naming_jobs.snapshot()['activeJobs'], 0)

    def test_duplicate_active_id_rejects_and_late_cancel_is_explicit(self):
        job = identity()
        with patch('nivram.naming.name_structure', side_effect=held_worker):
            thread, _outcome = self.start_name(job)
            self.wait_worker()
            duplicate = self.request('/api/name', {**job, 'graph': {}})
            self.assertEqual((duplicate[0], duplicate[1]['status']), (409, 'active'))
            self.request('/api/cancel-job', job)
            thread.join(4)
        status, receipt = self.request('/api/cancel-job', job)
        self.assertEqual((status, receipt['status']), (200, 'cancelled'))

    def test_completed_ids_are_not_reused_and_response_echoes_job_identity(self):
        job = identity()
        with patch('nivram.naming.name_structure', side_effect=fast_result) as name:
            status, result = self.request('/api/name', {**job, 'graph': {}, 'documentRevision': 9})
        self.assertEqual(status, 200)
        self.assertEqual((result['clientId'], result['jobId']), (job['clientId'], job['jobId']))
        self.assertIs(name.call_args.kwargs['supervisor'], self.server.worker_supervisor)
        self.assertIsInstance(name.call_args.kwargs['cancellation_token'], CancellationToken)
        self.assertEqual(self.request('/api/cancel-job', job)[1]['status'], 'finished')
        self.assertEqual(self.request('/api/name', {**job, 'graph': {}})[0], 409)

    def test_legacy_without_ids_is_still_owned_and_explicit_bad_ids_do_not_bypass_validation(self):
        with patch('nivram.naming.name_structure', side_effect=fast_result) as name:
            self.assertEqual(self.request('/api/name', {'graph': {}})[0], 200)
        self.assertIs(name.call_args.kwargs['supervisor'], self.server.worker_supervisor)
        for changes in ({'clientId': None, 'jobId': None}, {'clientId': 'a'}, identity(jobId='../path'),
                        identity(expiresAt=True), identity(expiresAt=int(time.time()*1000)+120000)):
            with self.subTest(changes=changes):
                self.assertEqual(self.request('/api/name', {**changes, 'graph': {}})[0], 422)

    def test_server_close_cancels_owned_workers_and_reports_the_observed_exit(self):
        job = identity()
        with patch('nivram.naming.name_structure', side_effect=held_worker):
            thread, outcome = self.start_name(job)
            self.wait_worker()
            # Match the production socket lifecycle: stop accepting requests
            # before closing the listening socket and owned workers.
            self.server.shutdown()
            self.thread.join(2)
            self.server.server_close()
            thread.join(4)
        self.assertEqual(outcome['response'][1]['status'], 'cancelled')
        self.assertTrue(self.server.worker_shutdown_receipt['ownedProcessesExited'])
        self.assertTrue(self.server.worker_supervisor.closed)
        self.assertEqual(self.server.worker_supervisor.active_count, 0)

    def test_unconfirmed_shutdown_is_not_reported_as_success(self):
        with patch.object(self.server.worker_supervisor, 'shutdown', return_value=False):
            receipt = self.server.shutdown_scientific_jobs()
        self.assertEqual(receipt['status'], 'unconfirmed')
        self.assertFalse(receipt['ownedProcessesExited'])
        self.assertEqual(self.server.scientific_jobs_status()['shutdown'], receipt)

    def test_servers_have_independent_capacity_and_lifecycle(self):
        other = self.make_server()
        try:
            keys = [self.server.naming_jobs.register(identity()) for _ in range(2)]
            with self.assertRaises(NamingJobError):
                self.server.naming_jobs.register(identity())
            key, token = other.naming_jobs.register(identity())
            self.server.shutdown_scientific_jobs()
            self.assertFalse(token.cancelled)
            self.assertFalse(other.worker_supervisor.closed)
            self.assertIsNot(other.worker_supervisor, self.server.worker_supervisor)
            other.naming_jobs.finish(key, token)
            for key, token in keys:
                self.server.naming_jobs.finish(key, token)
        finally:
            other.server_close()


class NamingRegistryBoundsTests(unittest.TestCase):
    def test_unexpired_cancellation_records_are_not_evicted_under_pressure(self):
        registry, first, second = NamingJobs(), identity(), identity()
        with patch('nivram.server.MAX_JOB_RECORDS', 2):
            registry.cancel(first)
            registry.cancel(second)
            with self.assertRaises(NamingJobError) as exhausted:
                registry.cancel(identity())
            self.assertEqual(exhausted.exception.http_status, 429)
            with self.assertRaises(NamingJobError) as old:
                registry.register(first)
            self.assertEqual(old.exception.state, 'cancelled')
            self.assertEqual(registry.snapshot()['recentJobIds'], 2)

    def test_expired_queued_request_cannot_start_after_its_tombstone_was_purged(self):
        registry = NamingJobs()
        job = identity()
        registry.cancel(job)
        key = (job['clientId'], job['jobId'])
        registry._history[key] = ('cancelled', 0)
        job['expiresAt'] = int(time.time()*1000)-1
        with self.assertRaises(NamingJobError) as error:
            registry.register(job)
        self.assertEqual(error.exception.state, 'expired')
        self.assertEqual(registry.snapshot()['activeJobs'], 0)


if __name__ == '__main__':
    unittest.main()
