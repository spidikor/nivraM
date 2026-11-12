"""Verify the pinned model bytes in a bounded trusted-core child, without ML.

The setup owner supplies an OpticalSupervisor (at most 4 GiB) and cancellation
token. The selected ML interpreter is never used for this integrity check.
Successful setup verification does not replace recognition's same-handle check.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from . import optical_worker
from .import_protocol import strict_json_loads
from .optical_processes import OpticalSupervisor, WALL_SECONDS
from .worker_processes import CancellationToken

MAX_INPUT_BYTES = 16 * 1024
MAX_OUTPUT_BYTES = 4096
MAX_STDERR_BYTES = 4096
SAFE_FAILURE = 'The local recognition model could not be verified.'
ERROR_MESSAGES = {
    'invalid_request': SAFE_FAILURE,
    'verification_failed': SAFE_FAILURE,
    'model_unavailable': 'The selected recognition model could not be read.',
    'model_mismatch': 'The selected file does not match the required recognition model.',
}
_SOURCE_WORKER_BOOTSTRAP = (
    'import sys; sys.path.insert(0, sys.argv.pop(1)); '
    'from nivram.optical_setup_worker import worker_main; '
    'raise SystemExit(worker_main())')


def _command():
    if getattr(sys, 'frozen', False):
        return [str(Path(sys.executable).with_name('nivram-worker.exe')), '--verify-optical-model']
    return [sys.executable, '-I', '-B', '-c', _SOURCE_WORKER_BOOTSTRAP,
            str(Path(__file__).resolve().parent.parent)]


def _require(condition):
    if not condition:
        raise ValueError(SAFE_FAILURE)


def _model_path(value):
    _require(isinstance(value, str) and 0 < len(value) <= MAX_INPUT_BYTES and '\x00' not in value)
    try:
        _require(len(value.encode('utf-8')) <= MAX_INPUT_BYTES and Path(value).is_absolute())
    except (UnicodeError, OSError):
        raise ValueError(SAFE_FAILURE) from None
    return value


def verify_optical_model(model, *, token: CancellationToken, supervisor: OpticalSupervisor) -> None:
    """Verify through the caller's process owner; never read weights in parent.

Cancellation, timeout and process-ownership failures propagate to that owner.
The protocol contains no executable or user-selected interpreter arguments.
"""
    supervisor.check_cancelled(token)
    _require(isinstance(model, (str, Path)))
    payload = json.dumps({'schemaVersion': 1, 'model': _model_path(str(model))},
                         ensure_ascii=True, separators=(',', ':'))
    encoded = payload.encode('utf-8')
    _require(len(encoded) <= MAX_INPUT_BYTES)
    request_digest = hashlib.sha256(encoded).hexdigest()
    completed = supervisor.run(_command(), input_text=payload, timeout_seconds=WALL_SECONDS,
        cancellation_token=token, max_input_bytes=MAX_INPUT_BYTES,
        max_stdout_bytes=MAX_OUTPUT_BYTES, max_stderr_bytes=MAX_STDERR_BYTES)
    supervisor.check_cancelled(token)
    rejection = None
    try:
        _require(type(completed.returncode) is int and completed.returncode in (0, 2))
        _require(isinstance(completed.stdout, str) and 0 < len(completed.stdout) <= MAX_OUTPUT_BYTES
                 and len(completed.stdout.encode('utf-8')) <= MAX_OUTPUT_BYTES)
        result = strict_json_loads(completed.stdout)
        _require(isinstance(result, dict) and type(result.get('schemaVersion')) is int
                 and result['schemaVersion'] == 1 and result.get('requestSha256') == request_digest)
        if completed.returncode == 0:
            _require(set(result) == {'schemaVersion', 'requestSha256', 'status', 'modelSha256', 'modelBytes'}
                     and result['status'] == 'verified' and result['modelSha256'] == optical_worker.MODEL_SHA256
                     and type(result['modelBytes']) is int and result['modelBytes'] == optical_worker.MODEL_BYTES)
        else:
            _require(set(result) == {'schemaVersion', 'requestSha256', 'error'})
            error = result['error']
            _require(isinstance(error, dict) and set(error) == {'code', 'message'}
                     and isinstance(error['code'], str) and error['code'] in ERROR_MESSAGES
                     and error['message'] == ERROR_MESSAGES[error['code']])
            rejection = ERROR_MESSAGES[error['code']]
    except (ValueError, UnicodeError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        raise ValueError(SAFE_FAILURE) from None
    supervisor.check_cancelled(token)
    if rejection is not None:
        raise ValueError(rejection)


def worker_main(*, stdin=None, stdout=None):
    """Read one bounded request, hash through verified_model, emit one envelope."""
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout.buffer if stdout is None else stdout
    request_digest = None
    error_code = 'invalid_request'
    try:
        raw = stdin.read(MAX_INPUT_BYTES + 1)
        _require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_INPUT_BYTES)
        request_digest = hashlib.sha256(raw).hexdigest()
        request = strict_json_loads(raw.decode('utf-8'))
        _require(isinstance(request, dict) and set(request) == {'schemaVersion', 'model'}
                 and type(request['schemaVersion']) is int and request['schemaVersion'] == 1)
        model = _model_path(request['model'])
        error_code = 'verification_failed'
        with optical_worker.verified_model(model):
            pass
        result = {'schemaVersion': 1, 'requestSha256': request_digest, 'status': 'verified',
                  'modelSha256': optical_worker.MODEL_SHA256, 'modelBytes': optical_worker.MODEL_BYTES}
        exit_code = 0
    except Exception as error:
        if isinstance(error, optical_worker.OpticalWorkerError) and error.code in ('model_unavailable', 'model_mismatch'):
            error_code = error.code
        result = {'schemaVersion': 1, 'requestSha256': request_digest,
                  'error': {'code': error_code, 'message': ERROR_MESSAGES[error_code]}}
        exit_code = 2
    encoded = json.dumps(result, ensure_ascii=True, separators=(',', ':')).encode('ascii') + b'\n'
    _require(len(encoded) <= MAX_OUTPUT_BYTES)
    stdout.write(encoded)
    stdout.flush()
    return exit_code


if __name__ == '__main__':
    raise SystemExit(worker_main() if sys.argv[1:] == ['--worker'] else 2)
