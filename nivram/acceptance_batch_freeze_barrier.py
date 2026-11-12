"""Hold the second real, adopted preparation before its native bridge return.

Only the isolated acceptance scenario installs this observer. It neither changes
the preparation result nor bypasses cancellation, and every wait is bounded.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


class PreparedResponseBarrier:
    def __init__(self, manager, *, hold_seconds=30, clock=time.monotonic):
        if type(hold_seconds) not in (int, float) or not math.isfinite(hold_seconds) or not 0 < hold_seconds <= 30:
            raise ValueError('A finite response hold of at most 30 seconds is required.')
        self.manager, self.hold_seconds, self.clock = manager, hold_seconds, clock
        self.original = manager.prepare
        self.previous = manager.__dict__.get('prepare')
        self.held, self.release, self.finished = threading.Event(), threading.Event(), threading.Event()
        self._idle, self._lock = threading.Event(), threading.RLock()
        self._idle.set()
        self._active = 0
        self._closing = self.attached = False
        self.values = {'calls': 0, 'secondReady': False, 'resultUnchanged': False,
                       'holdTimedOut': False, 'releasedWhileRunning': False, 'methodsRestored': False}

        def observed(*args, **kwargs):
            with self._lock:
                if self._closing or self._active or self.values['calls'] >= 2:
                    raise RuntimeError('Unexpected preparation callback entry.')
                self.values['calls'] += 1
                second = self.values['calls'] == 2
                self._active += 1
                self._idle.clear()
            try:
                result = self.original(*args, **kwargs)
                if second:
                    if type(result) is not dict or result.get('status') != 'ready' or not all(
                            name in result for name in ('item', 'project', 'graph', 'scene')):
                        raise AssertionError('The second real preparation did not become ready.')
                    digest = identity(result)
                    with self._lock:
                        self.values.update(secondReady=True, preparedResultSha256=digest)
                    deadline = self.clock() + self.hold_seconds
                    self.held.set()
                    if not self.release.wait(max(0, deadline - self.clock())) or self.clock() >= deadline:
                        with self._lock:
                            self.values['holdTimedOut'] = True
                        raise TimeoutError('The observed preparation response exceeded its hold bound.')
                    if identity(result) != digest:
                        raise AssertionError('The delayed preparation response changed.')
                    with self._lock:
                        self.values['resultUnchanged'] = True
                return result
            finally:
                with self._lock:
                    if second:
                        self.finished.set()
                    self._active -= 1
                    if not self._active:
                        self._idle.set()

        self.observed = observed

    def attach(self):
        with self._lock:
            if self.attached or self._closing:
                raise RuntimeError('The response observer is already attached or closing.')
            self.manager.prepare = self.observed
            self.attached = True

    def release_after_edits(self):
        with self.manager._lock:
            session = self.manager._session
            if not self.held.is_set() or not session or session['status'] != 'running' or session['cancelled'].is_set():
                raise AssertionError('The original batch must remain running before release.')
            with self._lock:
                if self._closing or self.release.is_set():
                    raise AssertionError('The response cannot be released twice or during cleanup.')
                self.values['releasedWhileRunning'] = True
                self.release.set()

    def snapshot(self):
        with self._lock:
            return {**self.values, 'held': self.held.is_set(), 'released': self.release.is_set(),
                    'finished': self.finished.is_set()}

    def assert_complete(self):
        value = self.snapshot()
        if not (value['calls'] == 2 and value['holdTimedOut'] is False and all(value[key] is True
                for key in ('secondReady', 'resultUnchanged', 'releasedWhileRunning', 'held', 'released', 'finished'))):
            raise AssertionError('The delayed real preparation was not returned unchanged after the edits.')

    def close(self, timeout=2):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 2:
            raise ValueError('Invalid observer cleanup bound.')
        with self._lock:
            self._closing = True
            self.release.set()
        stopped = self._idle.wait(timeout)
        with self._lock:
            stopped = stopped and self._active == 0
            if self.attached and stopped:
                if self.manager.prepare is not self.observed:
                    raise RuntimeError('The observed method was replaced during acceptance.')
                if self.previous is None:
                    delattr(self.manager, 'prepare')
                else:
                    self.manager.prepare = self.previous
                self.attached = False
                self.values['methodsRestored'] = True
            return {'shutdownConfirmed': stopped and not self.attached, **self.snapshot()}
