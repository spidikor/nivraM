"""Bound caller waiting in the synthetic native acceptance harness only.

A timed-out WebView call is indeterminate: it may still finish in the native
runtime. We never submit another call after that timeout. The outer process
supervisor remains the final bound if native code blocks Python or UI cleanup.
"""
from __future__ import annotations

import math
import queue
import threading
import time


class EvaluationTimeout(TimeoutError):
    pass


class BoundedEvaluator:
    def __init__(self, evaluate, observe, *, timeout=10):
        self._validate_timeout(timeout)
        self._evaluate = evaluate
        self._observe = observe
        self._timeout = timeout
        self._queue = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._closed = False
        self._poisoned = False
        self._sequence = 0
        self._reporting_errors = []
        self._worker = threading.Thread(target=self._run, daemon=True,
                                        name='acceptance-webview-evaluator')
        self._worker.start()

    @staticmethod
    def _validate_timeout(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError('An evaluation deadline must be finite and positive.')

    def _run(self):
        while True:
            request = self._queue.get()
            if request is None:
                return
            expression, callback, result, completed = request
            try:
                result['value'] = (self._evaluate(expression) if callback is None else
                                   self._evaluate(expression, callback=callback))
            except BaseException as error:
                result['error'] = error
            finally:
                completed.set()
            with self._state_lock:
                if self._closed or self._poisoned:
                    return

    def _terminal_observation(self, observation, original_error=None):
        try:
            self._observe(dict(observation))
        except Exception as error:
            self._reporting_errors.append(type(error).__name__)
            if original_error is None:
                raise

    def __call__(self, expression, callback=None, *, timeout=None, stage='native-ui'):
        timeout = self._timeout if timeout is None else timeout
        self._validate_timeout(timeout)
        if callback is not None and not callable(callback):
            raise TypeError('The native evaluation callback must be callable.')
        # Stage labels are constant harness identifiers, never JS, paths, DOM,
        # molecular text, or values returned from the application.
        if not isinstance(stage, str) or not stage or len(stage) > 80 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in stage):
            raise ValueError('Invalid acceptance stage identifier.')
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('An acceptance evaluation is already outstanding.')
        try:
            started = time.monotonic()
            deadline = started + timeout
            with self._state_lock:
                if self._closed or self._poisoned:
                    raise RuntimeError('The acceptance evaluator is closed or timed out; no further JavaScript was submitted.')
                self._sequence += 1
                sequence = self._sequence
            observation = {'sequence': sequence, 'stage': stage,
                           'status': 'waiting', 'timeoutSeconds': timeout}
            self._observe(dict(observation))
            result, completed = {}, threading.Event()
            if time.monotonic() >= deadline:
                error = EvaluationTimeout('The evaluation deadline expired before JavaScript dispatch.')
                observation.update(status='timed-out', outcome='not-submitted',
                                   elapsedSeconds=round(time.monotonic() - started, 3))
                self._terminal_observation(observation, error)
                raise error
            def guarded_callback(value):
                with self._state_lock:
                    available = not self._closed and not self._poisoned
                if available:
                    callback(value)
            with self._state_lock:
                if self._closed:
                    raise RuntimeError('The acceptance evaluator closed before dispatch; no JavaScript was submitted.')
                self._queue.put_nowait((expression, guarded_callback if callback else None, result, completed))
            if not completed.wait(max(0, deadline - time.monotonic())):
                with self._state_lock:
                    self._poisoned = True
                    self._closed = True
                observation.update(status='timed-out', outcome='indeterminate',
                                   elapsedSeconds=round(time.monotonic() - started, 3))
                error = EvaluationTimeout('Native JavaScript evaluation exceeded its deadline; its outcome is indeterminate.')
                self._terminal_observation(observation, error)
                raise error
            observation['elapsedSeconds'] = round(time.monotonic() - started, 3)
            if 'error' in result:
                observation.update(status='failed', errorKind=type(result['error']).__name__)
                self._terminal_observation(observation, result['error'])
                raise result['error']
            observation['status'] = 'completed'
            self._terminal_observation(observation)
            return result.get('value')
        finally:
            self._lock.release()

    def close(self, timeout=0.2):
        """Never wait indefinitely for a native call that Python cannot cancel."""
        self._validate_timeout(timeout)
        with self._state_lock:
            self._closed = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._worker.join(timeout)
        return {'workerExited': not self._worker.is_alive(),
                'timedOut': self._poisoned,
                'reportingErrorKinds': self._reporting_errors[-8:],
                'outstandingCallMayComplete': self._poisoned and self._worker.is_alive()}
