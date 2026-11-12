"""Short-lived, one-use consent for the fixed optional PubChem adapter.

Preparation is local. Only lookup with an existing reviewed proposal and explicit
consent may invoke the provider. This registry is memory-only and stores no
preferences or reusable grants. The server's existing supervisor owns workers.
"""
from __future__ import annotations

import copy
import re
import secrets
import threading
import time

from .worker_processes import CancellationToken

PROPOSAL_SECONDS = 120
MAX_PROPOSALS = 8
MIN_REQUEST_INTERVAL = 1.0
PROPOSAL_ID = re.compile(r'[0-9a-f]{64}')


class PubChemJobError(ValueError):
    def __init__(self, http_status, message, state='rejected'):
        super().__init__(message)
        self.http_status, self.state = http_status, state


class PubChemJobs:
    def __init__(self, supervisor, *, prepare=None, lookup=None, clock=time.monotonic):
        self._supervisor = supervisor
        self._prepare = prepare
        self._lookup = lookup
        self._clock = clock
        self._lock = threading.Lock()
        self._pending = {}
        self._active = {}
        self._closed = False
        self._last_start = None

    def _purge(self):
        now = self._clock()
        self._pending = {key: value for key, value in self._pending.items()
                         if value['deadline'] > now}

    def _require_open(self):
        if self._closed:
            raise PubChemJobError(503, 'Online lookup is closing. No new request was sent.', 'closed')

    @staticmethod
    def _proposal_id(data, keys):
        if (not isinstance(data, dict) or set(data) != keys
                or not isinstance(data.get('proposalId'), str)
                or not PROPOSAL_ID.fullmatch(data['proposalId'])):
            raise PubChemJobError(422, 'Provide the identifier of the reviewed PubChem request.')
        return data['proposalId']

    def prepare(self, data):
        if (not isinstance(data, dict) or set(data) != {'graph', 'documentRevision'}
                or not isinstance(data['graph'], dict)
                or type(data['documentRevision']) is not int
                or not 0 <= data['documentRevision'] <= 2**53 - 1):
            raise PubChemJobError(422, 'Review a valid current structure before an online lookup.')
        with self._lock:
            self._require_open()
            self._purge()
            if len(self._pending) >= MAX_PROPOSALS:
                raise PubChemJobError(429, 'Too many pending lookup reviews. Cancel one or let it expire.', 'busy')
        prepare = self._prepare
        if prepare is None:
            from .pubchem import prepare_submission
            prepare = prepare_submission
        # Caller serializes this local chemistry step; no provider I/O occurs.
        submission = prepare(copy.deepcopy(data['graph']), data['documentRevision'])
        with self._lock:
            self._require_open()
            self._purge()
            if len(self._pending) >= MAX_PROPOSALS:
                raise PubChemJobError(429, 'Too many pending lookup reviews. Cancel one or let it expire.', 'busy')
            identifier = secrets.token_hex(32)
            self._pending[identifier] = {'submission': copy.deepcopy(submission),
                                         'deadline': self._clock() + PROPOSAL_SECONDS}
        return {'proposalId': identifier, 'expiresAt': int((time.time() + PROPOSAL_SECONDS) * 1000),
                'inputIdentity': copy.deepcopy(submission['inputIdentity']),
                'disclosure': copy.deepcopy(submission['disclosure'])}

    def lookup(self, data):
        identifier = self._proposal_id(data, {'proposalId', 'consent', 'disclosureVersion'})
        if data['consent'] is not True or type(data['disclosureVersion']) is not int or data['disclosureVersion'] != 1:
            raise PubChemJobError(422, 'Explicit consent to the current PubChem disclosure is required.')
        with self._lock:
            self._require_open()
            self._purge()
            proposal = self._pending.get(identifier)
            if proposal is None:
                raise PubChemJobError(409, 'This lookup review expired, was cancelled or was already used. Review it again.', 'expired')
            if self._active or (self._last_start is not None
                                and self._clock() - self._last_start < MIN_REQUEST_INTERVAL):
                raise PubChemJobError(429, 'Wait for the current lookup to finish before sending another.', 'busy')
            del self._pending[identifier]
            token = CancellationToken()
            self._active[identifier] = token
            self._last_start = self._clock()
        try:
            self._supervisor.check_cancelled(token)
            lookup = self._lookup
            if lookup is None:
                from .pubchem import lookup_submission
                lookup = lookup_submission
            result = lookup(proposal['submission'], supervisor=self._supervisor, cancellation_token=token)
            # Completion and cancel/close compete for the same boundary. A
            # cancellation already acknowledged here must prevent publication.
            with self._lock:
                self._supervisor.check_cancelled(token)
                self._active.pop(identifier, None)
            return result
        finally:
            with self._lock:
                self._active.pop(identifier, None)

    def cancel(self, data):
        identifier = self._proposal_id(data, {'proposalId'})
        with self._lock:
            self._purge()
            token = self._active.get(identifier)
            if token is not None:
                token.cancel()
                return {'status': 'cancellation_requested', 'alreadySentMayNotBeRecalled': True}
            pending = self._pending.pop(identifier, None)
            return {'status': 'cancelled' if pending is not None else 'not_active',
                    'alreadySentMayNotBeRecalled': pending is None}

    def close(self):
        with self._lock:
            self._closed = True
            self._pending.clear()
            for token in self._active.values():
                token.cancel()

    def snapshot(self):
        with self._lock:
            self._purge()
            return {'provider': 'PubChem', 'consent': 'required_per_request',
                    'acceptingRequests': not self._closed,
                    'pendingReviews': len(self._pending), 'activeJobs': len(self._active),
                    'maximumActiveJobs': 1, 'maximumPendingReviews': MAX_PROPOSALS}
