"""Exercise real authenticated loopback routes; provider is a synthetic fixture."""
import copy
import http.client
import json
import sys
import threading
import time
import unittest
from unittest.mock import Mock

from nivram.chemistry import import_structure
from nivram.server import make_server


class PubChemServerTests(unittest.TestCase):
    def setUp(self):
        self.server = make_server(0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.provider = Mock(side_effect=lambda submission, **kwargs: {
            'status': 'matched', 'name': 'ethanol', 'inputIdentity': submission['inputIdentity']})
        self.server.pubchem_jobs._lookup = self.provider
        self.graph = import_structure('CCO')

    def close_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)

    def request(self, path, data=None, extra=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'Content-Type': 'application/json', 'X-Nivram-Token': self.server.api_token,
                   'Origin': f'http://127.0.0.1:{self.server.server_port}'}
        headers.update(extra or {})
        try:
            connection.request('POST' if data is not None else 'GET', path,
                               body=json.dumps(data) if data is not None else None, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally: connection.close()

    def prepare(self):
        status, proposal = self.request('/api/pubchem/prepare', {'graph': self.graph, 'documentRevision': 7})
        self.assertEqual(status, 200, proposal)
        return proposal

    def test_offline_start_and_prepare_never_contact_provider(self):
        before = copy.deepcopy(self.graph)
        status, data = self.request('/api/status')
        self.assertEqual(status, 200)
        self.assertEqual(data['externalProviders'], 'per-request consent')
        proposal = self.prepare()
        self.assertEqual(proposal['disclosure']['submittedSmiles'], 'CCO')
        self.assertEqual(proposal['inputIdentity']['documentRevision'], 7)
        self.provider.assert_not_called()
        self.assertEqual(self.graph, before)

    def test_lookup_requires_exact_one_use_consent_and_returns_bound_identity(self):
        proposal = self.prepare()
        base = {'proposalId': proposal['proposalId'], 'disclosureVersion': 1}
        self.assertEqual(self.request('/api/pubchem/lookup', dict(base, consent=False))[0], 422)
        self.provider.assert_not_called()
        status, result = self.request('/api/pubchem/lookup', dict(base, consent=True))
        self.assertEqual(status, 200)
        self.assertEqual(result['inputIdentity'], proposal['inputIdentity'])
        self.assertEqual(result['name'], 'ethanol')
        self.assertEqual(self.request('/api/pubchem/lookup', dict(base, consent=True))[0], 409)
        self.provider.assert_called_once()

    def test_existing_authentication_guards_apply_before_preparation(self):
        for headers in ({'X-Nivram-Token': ''}, {'Origin': 'https://example.invalid'},
                        {'Host': 'example.invalid'}):
            with self.subTest(headers=headers):
                status, _ = self.request('/api/pubchem/prepare', {'graph': self.graph, 'documentRevision': 7}, headers)
                self.assertEqual(status, 403)
        self.assertEqual(self.server.pubchem_jobs.snapshot()['pendingReviews'], 0)
        self.provider.assert_not_called()

    def test_declining_a_review_prevents_later_upload(self):
        proposal = self.prepare()
        self.assertEqual(self.request('/api/pubchem/cancel', {'proposalId': proposal['proposalId']})[0], 200)
        status, _ = self.request('/api/pubchem/lookup', {'proposalId': proposal['proposalId'],
                                                       'consent': True, 'disclosureVersion': 1})
        self.assertEqual(status, 409)
        self.provider.assert_not_called()

    def test_lookup_does_not_hold_chemistry_lock_and_cancel_rejects_late_result(self):
        entered, release = threading.Event(), threading.Event()
        def pending(submission, **kwargs):
            entered.set()
            if not release.wait(3): raise RuntimeError('test barrier timed out')
            return {'status': 'matched', 'name': 'late'}
        self.provider.side_effect = pending
        proposal = self.prepare()
        outcome = []
        thread = threading.Thread(target=lambda: outcome.append(self.request('/api/pubchem/lookup',
            {'proposalId': proposal['proposalId'], 'consent': True, 'disclosureVersion': 1})))
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            status, data = self.request('/api/properties', {'graph': self.graph, 'documentRevision': 7})
            self.assertEqual(status, 200)
            self.assertEqual(data['formula'], 'C2H6O')
            self.assertEqual(self.request('/api/pubchem/cancel', {'proposalId': proposal['proposalId']})[0], 200)
        finally:
            release.set(); thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome[0][0], 409)
        self.assertEqual(outcome[0][1]['status'], 'cancelled')

    def test_server_shutdown_owns_and_reaps_lookup_worker(self):
        entered = threading.Event()
        def held(submission, *, supervisor, cancellation_token):
            entered.set()
            return supervisor.run([sys.executable, '-I', '-c', 'import time;time.sleep(20)'],
                timeout_seconds=25, cancellation_token=cancellation_token)
        self.provider.side_effect = held
        proposal = self.prepare()
        outcome = []
        thread = threading.Thread(target=lambda: outcome.append(self.request('/api/pubchem/lookup',
            {'proposalId': proposal['proposalId'], 'consent': True, 'disclosureVersion': 1})))
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            deadline = time.monotonic() + 3
            while not self.server.worker_supervisor.active_count and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(self.server.worker_supervisor.active_count, 1)
            receipt = self.server.shutdown_scientific_jobs()
            thread.join(4)
            self.assertTrue(receipt['ownedProcessesExited'])
            self.assertFalse(thread.is_alive())
            self.assertEqual(self.server.worker_supervisor.active_count, 0)
            self.assertEqual(outcome[0][1]['status'], 'cancelled')
            self.assertFalse(self.server.pubchem_jobs.snapshot()['acceptingRequests'])
        finally:
            self.server.shutdown_scientific_jobs(); thread.join(4)


if __name__ == '__main__': unittest.main()
