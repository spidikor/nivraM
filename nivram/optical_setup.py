"""Native-dialog-owned optical setup, separate from image and HTTP inputs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import threading
import uuid

from .optical_jobs import MODEL_SHA256
from .optical_processes import OpticalSupervisor
from .optical_setup_worker import verify_optical_model
from .persistence import _read_bounded, atomic_write
from . import conditional_save as _conditional
from .worker_processes import CancellationToken, WorkerCancelled, WorkerProcessError

MAX_SETTINGS_BYTES = 16_384
FIELDS = {'schemaVersion', 'method', 'modelSha256', 'python', 'model'}


def _selected_path(value, kind):
    """Only a local regular file selected by the host may reach this helper."""
    if not isinstance(value, (str, Path)):
        raise ValueError('Select a local file with the native file chooser.')
    raw = str(value)
    if (not raw or any(ord(char) < 32 for char in raw) or raw.startswith(('\\\\', '//'))):
        raise ValueError('Select a regular file on a local drive.')
    path = Path(raw)
    if not path.is_absolute() or (os.name == 'nt' and ':' in raw[2:]):
        raise ValueError('Select a regular file on a local drive.')
    try:
        resolved = path.resolve(strict=True)
        if str(resolved).startswith(('\\\\', '//')) or not stat.S_ISREG(path.stat().st_mode):
            raise ValueError('Select a regular file on a local drive.')
    except (OSError, RuntimeError):
        raise ValueError('The selected local file could not be read.') from None
    if kind == 'python' and path.name.casefold() != 'python.exe':
        raise ValueError('Select the local recognition runtime’s python.exe file.')
    return path


class OpticalSetupStore:
    def __init__(self, directory, images):
        self._path = Path(directory) / 'optical-runtime' / 'runtime.json'
        self._images = images
        self._lock = threading.RLock()
        self._supervisor = OpticalSupervisor()
        self._state = None
        self._active = None
        self._closed = False

    def _read(self):
        try:
            return _read_bounded(self._path, MAX_SETTINGS_BYTES)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            raise ValueError('The local recognition settings could not be read safely.') from None

    def _open(self):
        if self._closed or self._images.optical_availability()['status'] == 'closed':
            raise ValueError('Local recognition setup has closed.')

    def _current(self, setup_id):
        self._open()
        if (not isinstance(setup_id, str) or self._state is None
                or setup_id != self._state['id']):
            raise ValueError('This recognition setup is no longer current. Open setup again.')
        return self._state

    def _snapshot(self, state, status):
        return {'status': status, 'setupId': state['id'],
                'runtimeName': state['python'].name if state['python'] else '',
                'modelName': state['model'].name if state['model'] else '',
                'opticalRecognition': self._images.optical_availability()}

    def _invalidate(self):
        if self._active is not None:
            self._active['token'].cancel()
        self._state = None

    def begin_page(self):
        with self._lock:
            self._invalidate()

    def begin(self):
        with self._lock:
            self._open()
            baseline = self._read()
            self._invalidate()
            state = {'id': uuid.uuid4().hex, 'baseline': baseline, 'python': None, 'model': None}
            # Corrupt settings can be replaced only after explicit new choices.
            try:
                value = json.loads(baseline) if baseline is not None else None
                if (isinstance(value, dict) and set(value) == FIELDS
                        and type(value['schemaVersion']) is int and value['schemaVersion'] == 1
                        and value['method'] == 'MolScribe' and value['modelSha256'] == MODEL_SHA256
                        and all(isinstance(value[key], str) and value[key] and '\x00' not in value[key]
                                and Path(value[key]).is_absolute() for key in ('python', 'model'))):
                    state.update(python=Path(value['python']), model=Path(value['model']))
            except (ValueError, TypeError, RecursionError):
                pass
            self._state = state
            return self._snapshot(state, 'editing')

    def ensure_choice(self, setup_id, kind):
        """Call before opening the native dialog; choose rechecks after it closes."""
        with self._lock:
            state = self._current(setup_id)
            if not isinstance(kind, str) or kind not in ('python', 'model'):
                raise ValueError('Choose a recognition runtime or model file.')
            if self._active is not None and self._active['state'] is state:
                raise ValueError('Wait for model verification to finish before changing this setup.')

    def choose(self, setup_id, kind, path_or_None):
        with self._lock:
            self.ensure_choice(setup_id, kind)
            state = self._current(setup_id)
            if path_or_None is None:
                return self._snapshot(state, 'cancelled')
            selected = _selected_path(path_or_None, kind)
            state[kind] = selected
            return self._snapshot(state, 'selected')

    def _replace_existing(self, data, expected):
        def retain_backup(old):
            if len(old) > MAX_SETTINGS_BYTES:
                raise ValueError('Recognition settings exceed the supported size.')
            backup = self._path.with_suffix('.json.bak')
            atomic_write(backup, old)
            if _read_bounded(backup, MAX_SETTINGS_BYTES) != old:
                raise OSError('Recognition settings backup could not be verified.')

        digest = hashlib.sha256(data).hexdigest()
        committed = _conditional.conditional_write_existing(
            self._path, data, hashlib.sha256(expected).hexdigest(), retain_backup)
        if committed != digest:
            raise OSError('Recognition settings publication could not be verified.')

    def _restore(self, baseline, written):
        """Never overwrite a third party's newer bytes while undoing our write."""
        try:
            current = self._read()
            if current == baseline:
                return True
            if current != written or baseline is None:
                # There is no conditional delete primitive here. A newly
                # published file is retained for inspection, never unlinked
                # after a racy pathname comparison.
                return False
            self._replace_existing(baseline, written)
            return self._read() == baseline
        except (OSError, ValueError):
            return False

    def _commit(self, state, token, data):
        if self._current(state['id']) is not state or token.cancelled:
            raise ValueError('Recognition setup was cancelled.')
        if self._read() != state['baseline']:
            raise ValueError('Recognition settings changed elsewhere. Open setup again before saving.')
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if state['baseline'] is None:
                atomic_write(self._path, data, exclusive=True)
            else:
                self._replace_existing(data, state['baseline'])
            if self._read() != data:
                raise OSError('Settings readback failed.')
            if self._current(state['id']) is not state or token.cancelled:
                raise ValueError('Recognition setup was cancelled.')
        except _conditional.SaveConflict:
            raise ValueError('Recognition settings changed elsewhere. Open setup again before saving.') from None
        except _conditional.SaveUnsupported:
            raise ValueError('This location does not support safe replacement of recognition settings. Previous settings were retained.') from None
        except (OSError, ValueError):
            if not self._restore(state['baseline'], data):
                raise ValueError('Recognition settings could not be confirmed. Reopen setup before continuing.') from None
            raise ValueError('Recognition settings were not saved. Previous settings were retained.') from None

    def save(self, setup_id):
        with self._lock:
            state = self._current(setup_id)
            if self._active is not None:
                raise ValueError('Another model verification is still finishing. Try again after it stops.')
            self._images.ensure_optical_configurable()
            if state['python'] is None or state['model'] is None:
                raise ValueError('Choose both the recognition runtime and the model file.')
            python_path = _selected_path(state['python'], 'python')
            model = _selected_path(state['model'], 'model')
            value = {'schemaVersion': 1, 'method': 'MolScribe', 'modelSha256': MODEL_SHA256,
                     'python': str(python_path), 'model': str(model)}
            data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')
            if len(data) > MAX_SETTINGS_BYTES:
                raise ValueError('The selected recognition file paths are too long.')
            if self._read() != state['baseline']:
                raise ValueError('Recognition settings changed elsewhere. Open setup again before saving.')
            active = {'state': state, 'token': CancellationToken()}
            self._active = active
        try:
            verify_optical_model(model, token=active['token'], supervisor=self._supervisor)
            with self._lock:
                if self._current(setup_id) is not state or active['token'].cancelled:
                    raise ValueError('Recognition setup was cancelled.')
                # Validate the interpreter again without running it. The worker
                # rechecks the model's full identity on every later recognition.
                _selected_path(python_path, 'python')
                status = self._images.configure_optical(python_path, model,
                    lambda: self._commit(state, active['token'], data))
                self._state = None
                return {'status': 'saved', 'setupId': setup_id, 'opticalRecognition': status,
                        'modelVerified': True, 'runtimeCompatibility': 'checked-on-recognition'}
        except WorkerCancelled:
            raise ValueError('Recognition setup was cancelled.') from None
        except WorkerProcessError:
            # A verifier whose cleanup was uncertain must never be reused,
            # even after its active call has finished unwinding.
            try:
                self.close()
            finally:
                raise ValueError('Model verification could not complete safely. Restart nivraM before trying setup again.') from None
        except subprocess.TimeoutExpired:
            raise ValueError('Model verification reached its time limit. Previous settings were retained.') from None
        finally:
            with self._lock:
                if self._active is active:
                    self._active = None

    def cancel(self, setup_id):
        with self._lock:
            state = self._current(setup_id)
            result = self._snapshot(state, 'cancelled')
            self._invalidate()
            return result

    def close(self):
        with self._lock:
            self._closed = True
            self._invalidate()
        return self._supervisor.shutdown()
