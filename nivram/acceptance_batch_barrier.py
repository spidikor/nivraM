"""Acceptance-only delay of one real preparation response, never its contents.

The second service call completes normally before it is held. Cancellation is
performed through the real native UI; the original batch manager then receives
the unchanged late response and decides whether it can be adopted. This does
not claim cancellation of an in-flight scientific worker.
"""
from __future__ import annotations

import math
import threading
import time


class SecondPreparationBarrier:
    def __init__(self, manager, *, hold_seconds=30, clock=time.monotonic):
        if type(hold_seconds) not in (int, float) or not math.isfinite(hold_seconds) or not 0 < hold_seconds <= 30:
            raise ValueError('A finite preparation observation bound of at most 30 seconds is required.')
        self.manager = manager
        self.service = manager._preparation_service
        self.original_service = self.service.prepare_batch
        self.original_prepare = manager.prepare
        self._service_attribute = self.service.__dict__.get('prepare_batch')
        self._prepare_attribute = manager.__dict__.get('prepare')
        self.hold_seconds, self.clock = hold_seconds, clock
        self.held, self.release, self.finished = threading.Event(), threading.Event(), threading.Event()
        self._lock = threading.RLock()
        self._idle = threading.Event()
        self._idle.set()
        self._active_calls = 0
        self._closing = False
        self._token = None
        self.attached = False
        self.values = {'serviceCalls': 0, 'batchCalls': 0, 'secondOriginalReturned': False,
            'cancelObservedBeforeRelease': False, 'lateResultReturned': False,
            'lateResultStatus': None, 'lateResultHadScene': None, 'holdTimedOut': False,
            'methodsRestored': False}

        def enter(kind):
            with self._lock:
                if self._closing:
                    raise RuntimeError('The preparation observer is closing; late callback entry is rejected.')
                self.values[kind] += 1
                index = self.values[kind]
                if index > 2:
                    raise AssertionError('Unexpected extra observed preparation call.')
                self._active_calls += 1
                self._idle.clear()
                return index

        def leave(*, second_batch=False):
            with self._lock:
                if second_batch:
                    self.finished.set()
                self._active_calls -= 1
                if not self._active_calls:
                    self._idle.set()

        def observed_service(*args, **kwargs):
            index = enter('serviceCalls')
            try:
                result = self.original_service(*args, **kwargs)
                if index == 2:
                    token = kwargs.get('cancellation_token')
                    if token is None or token.cancelled is not False:
                        raise AssertionError('Second preparation must complete before cancellation.')
                    with self._lock:
                        self._token = token
                        self.values['secondOriginalReturned'] = True
                    deadline = self.clock() + self.hold_seconds
                    self.held.set()
                    if not self.release.wait(max(0, deadline - self.clock())) or self.clock() >= deadline:
                        with self._lock:
                            self.values['holdTimedOut'] = True
                        raise TimeoutError('The observed preparation response was not released within its bound.')
                return result
            finally:
                leave()

        def observed_prepare(*args, **kwargs):
            index = enter('batchCalls')
            try:
                result = self.original_prepare(*args, **kwargs)
                if index == 2:
                    with self._lock:
                        self.values.update(lateResultReturned=True,
                            lateResultStatus=result.get('status') if type(result) is dict and result.get('status') in
                                ('cancelled', 'ready', 'failed') else 'unrecognized',
                            lateResultHadScene=type(result) is dict and 'scene' in result)
                return result
            finally:
                leave(second_batch=index == 2)

        self.observed_service, self.observed_prepare = observed_service, observed_prepare

    def attach(self):
        with self._lock:
            if self.attached or self._closing:
                raise RuntimeError('The preparation observer is already attached or closing.')
            self.service.prepare_batch = self.observed_service
            self.manager.prepare = self.observed_prepare
            self.attached = True

    def release_after_cancel(self):
        with self._lock:
            if not self.held.is_set() or self._token is None or self._token.cancelled is not True:
                raise AssertionError('The real batch cancellation signal must precede release.')
            self.values['cancelObservedBeforeRelease'] = True
        self.release.set()

    def snapshot(self):
        with self._lock:
            return {**self.values, 'held': self.held.is_set(), 'released': self.release.is_set(),
                    'secondCallFinished': self.finished.is_set()}

    def close(self, timeout=2):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 2:
            raise ValueError('Invalid preparation observer cleanup bound.')
        with self._lock:
            self._closing = True
            self.release.set()
        stopped = self._idle.wait(timeout)
        with self._lock:
            stopped = stopped and self._active_calls == 0
            if self.attached and stopped:
                if self.service.prepare_batch is not self.observed_service or self.manager.prepare is not self.observed_prepare:
                    raise RuntimeError('The preparation observer was replaced during acceptance.')
                for owner, name, previous in ((self.service, 'prepare_batch', self._service_attribute),
                                               (self.manager, 'prepare', self._prepare_attribute)):
                    if previous is None:
                        delattr(owner, name)
                    else:
                        setattr(owner, name, previous)
                self.attached = False
                self.values['methodsRestored'] = True
            return {'shutdownConfirmed': stopped and not self.attached, **self.snapshot()}

    def assert_complete(self):
        value = self.snapshot()
        if not (value['serviceCalls'] == value['batchCalls'] == 2
                and all(value[key] is True for key in ('held', 'released', 'secondCallFinished',
                    'secondOriginalReturned', 'cancelObservedBeforeRelease', 'lateResultReturned'))
                and value['lateResultStatus'] == 'cancelled' and value['lateResultHadScene'] is False
                and value['holdTimedOut'] is False):
            raise AssertionError('The controlled late preparation did not demonstrate cancelled adoption.')
