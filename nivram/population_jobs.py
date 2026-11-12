"""Bounded local evaluation of explicitly supplied population models.

Drawing revision/hash are client snapshot context, not proof that the supplied
thermodynamics describe the drawing. This stdlib-only worker neither predicts
states/energies nor changes caller-provided provenance or the active document.
"""
from __future__ import annotations

from dataclasses import fields
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from .population_verification import verify_population_response
from .worker_processes import (CancellationToken, WorkerCancelled, WorkerOutputLimit,
                               WorkerProcessError, WorkerSupervisor)


MAX_MODEL_BYTES = 2 * 1024 * 1024
MAX_TRANSPORT_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_HISTORY = 128
HISTORY_SECONDS = 120.0
DEFAULT_TIMEOUT = 60.0
_ERRORS = {
    'invalid_input': (422, 'Supply a valid bounded model request with its exact text digest and drawing snapshot context.'),
    'invalid_model': (422, 'Supply a complete PopulationModel JSON object with all state energies, conditions and provenance. No missing scientific values are inferred.'),
    'output_too_large': (422, 'The complete 141-sample result exceeds the 16 MiB response limit. No states or metadata were truncated.'),
    'busy': (429, 'Another local population calculation is running. Cancel it or wait for completion.'),
    'reused_request': (409, 'This population request identifier was already used. Start a new request.'),
    'history_full': (429, 'The bounded recent population request registry is full. Try again shortly.'),
    'unavailable': (503, 'The local population evaluator is unavailable.'),
    'timed_out': (504, 'The supplied-model calculation exceeded its time limit. No result was adopted.'),
    'worker_failed': (502, 'The local population worker could not complete this request. No result was adopted.'),
}


class PopulationJobError(ValueError):
    def __init__(self, code):
        if code not in _ERRORS:
            raise ValueError('Unknown population job error.')
        self.code = code
        self.http_status, message = _ERRORS[code]
        super().__init__(message)


def _json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON field.')
            result[key] = value
        return result
    def constant(_value):
        raise ValueError('Nonfinite JSON number.')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def _request_id(data, keys):
    if (not isinstance(data, dict) or set(data) != keys or
            type(data.get('schemaVersion')) is not int or data['schemaVersion'] != 1 or
            not isinstance(data.get('requestId'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', data['requestId'])):
        raise PopulationJobError('invalid_input')
    return data['requestId']


def validate_request(data):
    _request_id(data, {'schemaVersion', 'requestId', 'documentRevision', 'graphHash', 'modelText', 'modelInputHash'})
    if (type(data['documentRevision']) is not int or not 0 <= data['documentRevision'] <= 2**53 - 1 or
            any(not isinstance(data[key], str) or not re.fullmatch('[a-f0-9]{64}', data[key])
                for key in ('graphHash', 'modelInputHash'))):
        raise PopulationJobError('invalid_input')
    text = data['modelText']
    if not isinstance(text, str) or not text or len(text) > MAX_MODEL_BYTES:
        raise PopulationJobError('invalid_input')
    try:
        encoded = text.encode('utf-8')
    except UnicodeError:
        raise PopulationJobError('invalid_input') from None
    if len(encoded) > MAX_MODEL_BYTES or hashlib.sha256(encoded).hexdigest() != data['modelInputHash']:
        raise PopulationJobError('invalid_input')
    return dict(data)


def _complete_model(text):
    from .populations import JointState, ModelConditions, ModelProvenance, PopulationModel, model_from_dict
    try:
        value = _json(text)
        def exact(record, record_type):
            if not isinstance(record, dict) or set(record) != {field.name for field in fields(record_type) if field.init}:
                raise ValueError('A complete record is required.')
        exact(value, PopulationModel)
        if not isinstance(value['states'], list) or not 1 <= len(value['states']) <= 4096:
            raise ValueError('A bounded state array is required.')
        for state in value['states']:
            exact(state, JointState)
        exact(value['conditions'], ModelConditions)
        exact(value['provenance'], ModelProvenance)
        return model_from_dict(value)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise PopulationJobError('invalid_model') from None


def evaluate_request(data):
    """Return bounded UTF-8 JSON; materialize only one grid row at a time."""
    request = validate_request(data)
    model = _complete_model(request['modelText'])
    from .populations import MAX_GRID_STATE_EVALUATIONS, sample_grid
    if 141 * len(model.states) > MAX_GRID_STATE_EVALUATIONS:
        raise PopulationJobError('invalid_model')
    buffer = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    def append(text):
        remaining = MAX_OUTPUT_BYTES - len(buffer)
        if len(text) > remaining:
            raise PopulationJobError('output_too_large')
        encoded = text.encode('utf-8')
        if len(encoded) > remaining:
            raise PopulationJobError('output_too_large')
        buffer.extend(encoded)
    def value(item):
        for fragment in encoder.iterencode(item):
            append(fragment)
    append('{"schemaVersion":1,"requestId":'); value(request['requestId'])
    append(',"modelInputHash":'); value(request['modelInputHash'])
    append(',"envelope":{"schemaVersion":1,"documentRevision":'); value(request['documentRevision'])
    append(',"graphHash":'); value(request['graphHash'])
    append(',"modelSignature":'); value(model.signature)
    append(',"model":'); value(model.to_dict())
    append(',"samples":[')
    try:
        for index in range(141):
            if index:
                append(',')
            value(sample_grid(model, index, index)[0].to_dict())
    except PopulationJobError:
        raise
    except (ValueError, TypeError, OverflowError):
        raise PopulationJobError('invalid_model') from None
    append(']}}')
    return bytes(buffer)


def worker_command():
    if getattr(sys, 'frozen', False):
        return [str(Path(sys.executable).with_name('nivram-worker.exe')), '--populations']
    return [sys.executable, '-B', '-s', '-m', 'nivram.population_jobs', '--worker']


class PopulationJobs:
    def __init__(self, *, supervisor=None, command_factory=None):
        self._supervisor = supervisor if supervisor is not None else WorkerSupervisor(max_workers=1)
        self._command_factory = command_factory or worker_command
        self._lock = threading.Lock()
        self._active = None
        self._history = {}
        self._closed = False

    def _purge(self):
        now = time.monotonic()
        self._history = {key: row for key, row in self._history.items() if row[1] > now}

    def snapshot(self):
        with self._lock:
            self._purge()
            return {'acceptingJobs': not self._closed, 'activeJobs': int(self._active is not None),
                    'maximumActiveJobs': 1, 'recentRequestIds': len(self._history)}

    def cancel(self, data):
        request_id = _request_id(data, {'schemaVersion', 'requestId'})
        with self._lock:
            self._purge()
            if self._active and self._active[0] == request_id:
                self._active[1].cancel()
                return {'status': 'cancellation_requested'}
            if request_id in self._history:
                return {'status': self._history[request_id][0]}
            if self._closed:
                return {'status': 'closed'}
            if len(self._history) + int(self._active is not None) >= MAX_HISTORY:
                raise PopulationJobError('history_full')
            self._history[request_id] = ('cancelled', time.monotonic() + HISTORY_SECONDS)
            return {'status': 'cancelled_before_registration'}

    def evaluate(self, data, *, cancellation_token=None, timeout=DEFAULT_TIMEOUT):
        request = validate_request(data)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= DEFAULT_TIMEOUT:
            raise PopulationJobError('invalid_input')
        request_id = request['requestId']
        token = cancellation_token if cancellation_token is not None else CancellationToken()
        with self._lock:
            self._purge()
            if self._closed or token.cancelled:
                raise WorkerCancelled('Population calculation was cancelled.')
            if request_id in self._history:
                if self._history[request_id][0] == 'cancelled':
                    raise WorkerCancelled('Population calculation was cancelled before registration.')
                raise PopulationJobError('reused_request')
            if self._active is not None:
                raise PopulationJobError('busy')
            if len(self._history) >= MAX_HISTORY:
                raise PopulationJobError('history_full')
            self._active = (request_id, token)
        try:
            payload = json.dumps(request, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            completed = self._supervisor.run(self._command_factory(), input_text=payload,
                timeout_seconds=timeout, cancellation_token=token, max_input_bytes=MAX_TRANSPORT_BYTES,
                max_stdout_bytes=MAX_OUTPUT_BYTES, max_stderr_bytes=8192)
            self._supervisor.check_cancelled(token)
            result = _json(completed.stdout)
            if completed.returncode != 0:
                if (completed.returncode == 2 and isinstance(result, dict) and set(result) == {'schemaVersion', 'error'} and
                        result['schemaVersion'] == 1 and result['error'] in ('invalid_input', 'invalid_model', 'output_too_large')):
                    raise PopulationJobError(result['error'])
                raise PopulationJobError('worker_failed')
            envelope = result.get('envelope') if isinstance(result, dict) else None
            if (not isinstance(result, dict) or set(result) != {'schemaVersion', 'requestId', 'modelInputHash', 'envelope'} or
                    type(result['schemaVersion']) is not int or result['schemaVersion'] != 1 or
                    result['requestId'] != request_id or result['modelInputHash'] != request['modelInputHash'] or
                    not isinstance(envelope, dict) or set(envelope) != {'schemaVersion', 'documentRevision', 'graphHash', 'modelSignature', 'model', 'samples'} or
                    type(envelope['schemaVersion']) is not int or envelope['schemaVersion'] != 1 or
                    type(envelope['documentRevision']) is not int or envelope['documentRevision'] != request['documentRevision'] or
                    envelope['graphHash'] != request['graphHash'] or not isinstance(envelope['modelSignature'], str) or
                    not re.fullmatch('[a-f0-9]{64}', envelope['modelSignature']) or not isinstance(envelope['model'], dict) or
                    not isinstance(envelope['samples'], list) or len(envelope['samples']) != 141):
                raise PopulationJobError('worker_failed')
            self._supervisor.check_cancelled(token)
            # The complete-model route preserves all JSON values (only tuple/list
            # containers change). Match PopulationModel's identifier from the
            # frozen original input, never from worker-returned model data.
            # The route verifier below independently checks returned arithmetic.
            model_value = _json(request['modelText'])
            self._supervisor.check_cancelled(token)
            model_payload = json.dumps(model_value, sort_keys=True,
                                       separators=(',', ':'), allow_nan=False)
            self._supervisor.check_cancelled(token)
            expected_signature = hashlib.sha256(model_payload.encode('utf-8')).hexdigest()
            self._supervisor.check_cancelled(token)
            if envelope['modelSignature'] != expected_signature:
                raise PopulationJobError('worker_failed')
            verify_population_response(model_value, envelope, expected_signature,
                lambda: self._supervisor.check_cancelled(token))
            # Preserve the bounded wire representation instead of reformatting
            # and expanding every repeated provenance record in Handler._send.
            wire = completed.stdout.encode('utf-8')
        except PopulationJobError:
            raise
        except WorkerCancelled:
            raise
        except subprocess.TimeoutExpired:
            raise PopulationJobError('timed_out') from None
        except WorkerOutputLimit:
            raise PopulationJobError('output_too_large') from None
        except (WorkerProcessError, OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError, ZeroDivisionError):
            raise PopulationJobError('worker_failed') from None
        finally:
            with self._lock:
                if self._active == (request_id, token):
                    self._active = None
                    self._history[request_id] = ('cancelled' if token.cancelled else 'finished', time.monotonic() + HISTORY_SECONDS)

        # Registry finalization can wait while cancellation or shutdown finishes.
        # Recheck this attempt only after cleanup; never replace a primary error.
        if self._closed or token.cancelled:
            raise WorkerCancelled('Population calculation was cancelled.')
        self._supervisor.check_cancelled(token)
        return wire

    def shutdown(self, *, timeout_seconds=2.0):
        with self._lock:
            self._closed = True
            if self._active:
                self._active[1].cancel()
        return self._supervisor.shutdown(timeout_seconds=timeout_seconds)


def worker_main():
    try:
        raw = sys.stdin.buffer.read(MAX_TRANSPORT_BYTES + 1)
        if not raw or len(raw) > MAX_TRANSPORT_BYTES:
            raise PopulationJobError('invalid_input')
        try:
            data = _json(raw.decode('utf-8'))
        except (ValueError, UnicodeError, RecursionError):
            raise PopulationJobError('invalid_input') from None
        output = evaluate_request(data)
        code = 0
    except PopulationJobError as error:
        output = json.dumps({'schemaVersion': 1, 'error': error.code}, separators=(',', ':')).encode('ascii')
        code = 2
    except Exception:
        output = b'{"schemaVersion":1,"error":"worker_failed"}'
        code = 2
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    return code


if __name__ == '__main__':
    raise SystemExit(worker_main() if sys.argv[1:] == ['--worker'] else 2)
