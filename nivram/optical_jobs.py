"""Cancellable local optical recognition using a separately provisioned runtime.

The caller owns the interpreter and model paths; neither is read from image
content or accepted as a web request parameter. Recognition never provisions
dependencies or downloads a model. Input is a bounded review crop produced by
the PNG preview pipeline, with at most a validated sRGB declaration.
"""
from __future__ import annotations

import base64
from collections import deque
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
import time

from .optical_processes import OpticalSupervisor, WALL_SECONDS
from .optical_validation import validate_optical_result
from .png_preview_jobs import _dimensions, _validate_derivative_png
from .worker_processes import CancellationToken, WorkerCancelled, WorkerProcessError, _positive_seconds

MAX_PNG_BYTES = 4 * 1024 * 1024
MAX_PROTOCOL_BYTES = 6 * 1024 * 1024
MAX_RESULT_BYTES = 1024 * 1024
MODEL_BYTES = 1_134_173_494
MODEL_SHA256 = '32821fa4ba8d17f9a92e374601438c5e757947dbf1141e9ad0ef51cccfa1c501'
SAFE_FAILURE = 'Local structure recognition could not complete. The drawing is unchanged.'
WORKER_FAILURES = {
    'model_unavailable': 'The local recognition model could not be read. The drawing is unchanged.',
    'model_mismatch': 'The local recognition model failed its integrity check. Download the pinned model again.',
    'model_incompatible': 'The recognition model and local runtime are incompatible. The drawing is unchanged.',
    'network_disabled': 'The local recognition runtime attempted network access and was stopped.',
    'invalid_prediction': 'No supported structure could be recognized in this crop. Try a clearer or tighter crop.',
    'inference_failed': 'No structure could be recognized in this crop. Try a clearer or tighter crop.',
}


def configured_optical_jobs(directory):
    """Read a local application-profile setting, never an image-supplied path."""
    path = Path(directory) / 'runtime.json'
    try:
        with path.open('rb') as handle:
            encoded = handle.read(16_385)
    except FileNotFoundError:
        return None, {'status': 'not-configured', 'message': 'Local structure recognition has not been set up.'}
    except OSError:
        return None, {'status': 'unavailable', 'message': 'The local recognition settings could not be read.'}
    try:
        if len(encoded) > 16_384:
            raise ValueError
        value = json.loads(encoded)
        if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'method', 'modelSha256', 'python', 'model'}
                or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
                or value['method'] != 'MolScribe' or value['modelSha256'] != MODEL_SHA256
                or any(not isinstance(value[key], str) or not value[key] or '\x00' in value[key]
                       for key in ('python', 'model'))):
            raise ValueError
        jobs = OpticalJobs(value['python'], value['model'])
        return jobs, jobs.availability()
    except (ValueError, TypeError, OSError, RecursionError):
        return None, {'status': 'invalid-configuration', 'message': 'The local recognition settings are invalid.'}


class OpticalJobs:
    def __init__(self, python_executable, model_path):
        self._python = Path(python_executable)
        self._model = Path(model_path)
        if not self._python.is_absolute() or not self._model.is_absolute():
            raise ValueError('The optical runtime requires native-owned absolute paths.')
        self._supervisor = OpticalSupervisor()
        self._lock = threading.RLock()
        self._active = None
        self._cancelled = deque(maxlen=64)
        self._closed = False

    def availability(self):
        if self._closed:
            return {'status': 'closed', 'message': 'Local recognition has closed.'}
        try:
            if not self._python.is_file():
                return {'status': 'missing-runtime', 'message': 'The local recognition runtime is not installed.'}
            if not self._model.is_file():
                return {'status': 'missing-model', 'message': 'The local recognition model has not been downloaded.'}
            if self._model.stat().st_size != MODEL_BYTES:
                return {'status': 'invalid-model', 'message': 'The local recognition model is incomplete.'}
        except OSError:
            return {'status': 'unavailable', 'message': 'The local recognition files could not be read.'}
        return {'status': 'configured', 'modelSha256': MODEL_SHA256,
                'message': 'Model integrity and runtime compatibility are checked when recognition starts.'}

    def _command(self):
        worker = Path(__file__).with_name('optical_worker.py')
        return [str(self._python), '-I', '-B', '-u', str(worker), '--model', str(self._model)]

    def reconfigure(self, python_executable, model_path, commit):
        """Commit native-owned settings before changing a healthy idle owner."""
        python_path, model = Path(python_executable), Path(model_path)
        if not python_path.is_absolute() or not model.is_absolute():
            raise ValueError('The optical runtime requires native-owned absolute paths.')
        with self._lock:
            if self._closed or self._supervisor.closed:
                raise ValueError('Local recognition has closed. Restart nivraM before changing its setup.')
            if self._active is not None:
                raise ValueError('Wait for local structure recognition to finish before changing its setup.')
            commit()
            self._python, self._model = python_path, model

    def recognize(self, data, job_id, *, timeout=WALL_SECONDS, _cancellation_token=None):
        if not _positive_seconds(timeout, WALL_SECONDS):
            raise ValueError('Local recognition must use a time limit of at most 90 seconds.')
        deadline = time.monotonic() + timeout
        if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', job_id):
            raise ValueError('Invalid optical recognition job identifier.')
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_PNG_BYTES:
            raise ValueError('Use a bounded PNG detail crop for structure recognition.')
        width, height = _dimensions(data)
        if width > 2048 or height > 2048 or width * height > 1_000_000:
            raise ValueError('Choose a detail crop of at most one million pixels for recognition.')
        _validate_derivative_png(data, width, height)
        with self._lock:
            if self._closed or job_id in self._cancelled:
                raise WorkerCancelled('Local structure recognition was cancelled.')
            if self._active is not None:
                raise ValueError('Another local structure recognition is already running.')
            token = _cancellation_token if _cancellation_token is not None else CancellationToken()
            state = {'id': job_id, 'token': token}
            self._active = state
        try:
            def remaining_time():
                self._supervisor.check_cancelled(token)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(['local-structure-recognition'], timeout)
                return remaining

            self._supervisor.check_cancelled(token)
            availability = self.availability()
            if availability['status'] != 'configured':
                raise ValueError(availability['message'])
            digest = hashlib.sha256(data).hexdigest()
            payload = json.dumps({'schemaVersion': 1, 'inputSha256': digest,
                                  'pngBase64': base64.b64encode(data).decode('ascii')})
            try:
                result = self._supervisor.run(self._command(), input_text=payload,
                    timeout_seconds=remaining_time(), cancellation_token=token,
                    max_input_bytes=MAX_PROTOCOL_BYTES, max_stdout_bytes=MAX_RESULT_BYTES,
                    max_stderr_bytes=64 * 1024)
            except subprocess.TimeoutExpired as exc:
                raise ValueError('Local structure recognition reached its time limit. The drawing is unchanged.') from exc
            self._supervisor.check_cancelled(token)
            try:
                raw = json.loads(result.stdout)
            except (ValueError, TypeError, RecursionError) as exc:
                raise ValueError(SAFE_FAILURE) from exc
            if result.returncode != 0:
                error = raw.get('error') if isinstance(raw, dict) else None
                code = error.get('code') if isinstance(error, dict) else None
                raise ValueError(WORKER_FAILURES.get(code, SAFE_FAILURE) if isinstance(code, str) else SAFE_FAILURE)
            if not isinstance(raw, dict) or raw.get('inputSha256') != digest:
                raise ValueError('The recognition result does not match the reviewed PNG crop.')
            # Native chemistry parsing/layout runs under the same remaining
            # deadline in the trusted core runtime, never in the desktop host.
            candidate = validate_optical_result(result.stdout, digest, self._supervisor,
                                                remaining_time, token)
            with self._lock:
                remaining_time()
                if self._closed or self._active is not state:
                    raise WorkerCancelled('Local structure recognition was cancelled.')
            checked = {'schemaVersion': 1, 'inputSha256': digest, 'candidate': candidate}
        except subprocess.TimeoutExpired as exc:
            raise ValueError('Local structure recognition reached its time limit. The drawing is unchanged.') from exc
        except WorkerProcessError:
            # An uncertain native cleanup cannot authorize reuse of this owner.
            self.close()
            raise
        finally:
            with self._lock:
                if self._active is state:
                    self._active = None

        # Retirement can wait behind cancel/close or exhaust the total deadline.
        # Recheck only successful work, using this attempt's token and deadline.
        if self._closed:
            raise WorkerCancelled('Local structure recognition was cancelled.')
        try:
            remaining_time()
        except subprocess.TimeoutExpired as exc:
            raise ValueError('Local structure recognition reached its time limit. The drawing is unchanged.') from exc
        return checked

    def cancel(self, job_id):
        with self._lock:
            self._cancelled.append(job_id)
            if self._active is None or self._active['id'] != job_id:
                return {'status': 'not_running'}
            self._active['token'].cancel()
            return {'status': 'cancelled'}

    def close(self):
        with self._lock:
            self._closed = True
            if self._active is not None:
                self._active['token'].cancel()
        return self._supervisor.shutdown()
