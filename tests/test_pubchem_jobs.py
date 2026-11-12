"""Consent, ownership and lifecycle tests; no external requests."""
import copy
import threading
import unittest
from unittest.mock import Mock

from nivram.pubchem_jobs import PubChemJobs, PubChemJobError
from nivram.worker_processes import WorkerSupervisor, WorkerCancelled


def prepared(graph, revision):
    return {'graph': copy.deepcopy(graph), 'inputIdentity': {'documentRevision': revision,
        'smiles': 'CCO', 'graphHash': 'a' * 64, 'structureHash': 'b' * 64},
        'disclosure': {'version': 1, 'provider': 'PubChem (NCBI/NLM)',
                       'submittedSmiles': 'CCO'}}


class PubChemJobTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.supervisor = WorkerSupervisor(max_workers=2)
        self.lookup = Mock(return_value={'status': 'matched', 'name': 'ethanol'})
        self.jobs = PubChemJobs(self.supervisor, prepare=prepared, lookup=self.lookup,
                               clock=lambda: self.now)
        self.addCleanup(self.supervisor.shutdown)
        self.addCleanup(self.jobs.close)

    def proposal(self, revision=3):
        return self.jobs.prepare({'graph': {'atoms': [], 'bonds': []}, 'documentRevision': revision})

    def request(self, proposal):
        return {'proposalId': proposal['proposalId'], 'consent': True, 'disclosureVersion': 1}

    def test_prepare_is_local_detached_and_contains_exact_review(self):
        source = {'graph': {'atoms': [{'id': 1}], 'bonds': []}, 'documentRevision': 3}
        proposal = self.jobs.prepare(source)
        self.assertRegex(proposal['proposalId'], r'^[0-9a-f]{64}$')
        self.assertEqual(proposal['disclosure']['submittedSmiles'], 'CCO')
        self.assertEqual(proposal['inputIdentity']['documentRevision'], 3)
        self.lookup.assert_not_called()
        source['graph']['atoms'].clear()
        proposal['inputIdentity']['smiles'] = 'OTHER'
        proposal['disclosure']['submittedSmiles'] = 'OTHER'
        self.jobs.lookup(self.request(proposal))
        sent = self.lookup.call_args.args[0]
        self.assertEqual(sent['inputIdentity']['smiles'], 'CCO')
        self.assertEqual(sent['graph']['atoms'], [{'id': 1}])

    def test_explicit_consent_and_current_disclosure_are_required_before_network(self):
        proposal = self.proposal()
        valid = self.request(proposal)
        invalid = [{}, {'proposalId': proposal['proposalId']},
                   dict(valid, consent=False), dict(valid, consent=1),
                   dict(valid, disclosureVersion=True), dict(valid, disclosureVersion=2),
                   dict(valid, endpoint='https://example.invalid'),
                   dict(valid, proposalId='unknown')]
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(PubChemJobError):
                self.jobs.lookup(request)
        self.lookup.assert_not_called()
        self.jobs.lookup(valid)
        self.lookup.assert_called_once()

    def test_proposal_is_one_use_even_when_provider_fails(self):
        self.lookup.side_effect = RuntimeError('private transport details')
        proposal = self.proposal()
        with self.assertRaises(RuntimeError):
            self.jobs.lookup(self.request(proposal))
        self.now += 2
        with self.assertRaises(PubChemJobError):
            self.jobs.lookup(self.request(proposal))
        self.assertEqual(self.lookup.call_count, 1)
        self.assertEqual(self.jobs.snapshot()['activeJobs'], 0)

    def test_expired_proposals_and_cancelled_proposals_never_dispatch(self):
        proposal = self.proposal()
        self.now += 121
        with self.assertRaises(PubChemJobError):
            self.jobs.lookup(self.request(proposal))
        proposal = self.proposal()
        self.assertEqual(self.jobs.cancel({'proposalId': proposal['proposalId']})['status'], 'cancelled')
        with self.assertRaises(PubChemJobError):
            self.jobs.lookup(self.request(proposal))
        self.lookup.assert_not_called()

    def test_queue_is_bounded_and_minimum_interval_prevents_bursts(self):
        proposals = [self.proposal(i) for i in range(8)]
        with self.assertRaises(PubChemJobError):
            self.proposal(9)
        self.jobs.lookup(self.request(proposals[0]))
        with self.assertRaises(PubChemJobError) as error:
            self.jobs.lookup(self.request(proposals[1]))
        self.assertEqual(error.exception.http_status, 429)
        self.now += 1
        self.jobs.lookup(self.request(proposals[1]))
        self.assertEqual(self.lookup.call_count, 2)

    def test_active_cancel_blocks_late_result_without_holding_registry_lock(self):
        entered, release = threading.Event(), threading.Event()
        outcomes = []
        def pending(submission, *, supervisor, cancellation_token):
            entered.set()
            self.assertTrue(release.wait(3))
            return {'status': 'matched', 'name': 'late'}
        self.lookup.side_effect = pending
        proposal = self.proposal()
        def run():
            try: outcomes.append(self.jobs.lookup(self.request(proposal)))
            except WorkerCancelled: outcomes.append('cancelled')
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.jobs.snapshot()['activeJobs'], 1)
            other = self.proposal(4)
            with self.assertRaises(PubChemJobError):
                self.jobs.lookup(self.request(other))
            result = self.jobs.cancel({'proposalId': proposal['proposalId']})
            self.assertEqual(result['status'], 'cancellation_requested')
        finally:
            release.set(); thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes, ['cancelled'])
        self.assertEqual(self.jobs.snapshot()['activeJobs'], 0)

    def test_close_revokes_proposals_and_rejects_new_work(self):
        proposal = self.proposal()
        self.jobs.close()
        for action in (lambda: self.proposal(), lambda: self.jobs.lookup(self.request(proposal))):
            with self.assertRaises(PubChemJobError): action()
        self.lookup.assert_not_called()
        snapshot = self.jobs.snapshot()
        self.assertFalse(snapshot['acceptingRequests'])
        self.assertEqual(snapshot['pendingReviews'], 0)
        self.assertNotIn('CCO', str(snapshot))

    def test_cancel_or_close_winning_completion_lock_cannot_return_success(self):
        for action in ('cancel', 'close'):
            with self.subTest(action=action):
                jobs = PubChemJobs(self.supervisor, prepare=prepared, lookup=self.lookup,
                                   clock=lambda: self.now)
                proposal = jobs.prepare({'graph': {}, 'documentRevision': 3})
                reached, release = threading.Event(), threading.Event()
                original_lock = jobs._lock
                class CompletionGate:
                    def __init__(self): self.worker_entries = 0
                    def __enter__(self):
                        if threading.current_thread().name == 'completion-race':
                            self.worker_entries += 1
                            if self.worker_entries == 2:
                                reached.set()
                                if not release.wait(3): raise RuntimeError('completion gate timed out')
                        original_lock.acquire()
                    def __exit__(self, *args): original_lock.release()
                jobs._lock = CompletionGate()
                outcomes = []
                def run():
                    try: outcomes.append(jobs.lookup(self.request(proposal)))
                    except WorkerCancelled: outcomes.append('cancelled')
                thread = threading.Thread(target=run, name='completion-race')
                thread.start()
                try:
                    self.assertTrue(reached.wait(3))
                    if action == 'cancel':
                        self.assertEqual(jobs.cancel({'proposalId': proposal['proposalId']})['status'], 'cancellation_requested')
                    else: jobs.close()
                finally:
                    release.set(); thread.join(3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(outcomes, ['cancelled'])

    def test_prepare_and_cancel_reject_unexpected_payloads(self):
        for request in ({}, {'graph': {}, 'documentRevision': 1, 'consent': True},
                        {'graph': {}, 'documentRevision': True},
                        {'graph': {}, 'documentRevision': -1}):
            with self.assertRaises(PubChemJobError): self.jobs.prepare(request)
        for request in ({}, {'proposalId': 'a'*64, 'graph': {}}, {'proposalId': 1}):
            with self.assertRaises(PubChemJobError): self.jobs.cancel(request)


if __name__ == '__main__': unittest.main()
