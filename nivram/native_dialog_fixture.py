"""Supervise one synthetic native-dialog selection outside the UI process."""
from __future__ import annotations
import json
import ctypes
import importlib.util
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

from .native_dialog_worker import (MAX_INPUT, MAX_OUTPUT, build_request,
                                  cancel_owned_dialog, validate_result)
from .native_dialog_progress import ProgressCapture, progress_diagnostic
from .parent_io import ParentIoCapture, parent_io_diagnostic
from .supervision_observations import bind_parent_io, project_parent_io, PRIMARY_BOUNDARY
from .observation_envelope import encode_observation
from .worker_processes import (CancellationToken, WorkerCancelled,
                               WorkerOutputLimit, WorkerProcessError, WorkerSupervisor,
                               timeout_boundary_diagnostic, worker_failure_diagnostic,
                               exception_diagnostic_attribute)


def _error_kind(error):
    """Return only fixed categories; never expose messages or custom class names."""
    for exception_type, kind in (
            (subprocess.TimeoutExpired, 'timeout'), (WorkerCancelled, 'cancelled'),
            (WorkerOutputLimit, 'output_limit'), (WorkerProcessError, 'worker_process_error'),
            (json.JSONDecodeError, 'json_decode_error'), (UnicodeDecodeError, 'unicode_decode_error'),
            (OSError, 'os_error'), (ValueError, 'value_error'), (TypeError, 'type_error')):
        if isinstance(error, exception_type):
            return kind
    return 'other_error'


def _source_interpreter():
    if os.name != 'nt':
        return sys.executable
    # Windows venv python.exe is a redirector process. Launch the executable
    # actually hosting this interpreter so PPID and supervised handle match.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetModuleFileNameW(None, buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError('The native fixture interpreter could not be identified.')
    return str(Path(buffer.value).resolve(strict=True))


def _source_packages():
    # A direct -I -S parent has the base interpreter's sys.prefix. The actual
    # import origin remains the explicit pinned environment supplied to it.
    specification = importlib.util.find_spec('webview')
    if specification is None or not specification.origin:
        raise RuntimeError('The pinned desktop package directory is unavailable.')
    origin = Path(specification.origin).resolve(strict=True)
    if not origin.is_file() or origin.name != '__init__.py' or origin.parent.name != 'webview':
        raise RuntimeError('The pinned desktop package origin is unsupported.')
    return origin.parents[1]


def worker_command(progress=False):
    if getattr(sys, 'frozen', False):
        flag = '--native-dialog-fixture-progress' if progress else '--native-dialog-fixture'
        return [str(Path(sys.executable).with_name('nivram-worker.exe')), flag]
    # Keep the current pinned environment's packages while avoiding its Windows
    # redirector. Paths are separate arguments to a fixed, internal bootstrap.
    flag = '--worker-progress' if progress else '--worker'
    bootstrap = ("import runpy,sys;sys.path[:0]=sys.argv[1:];"
                 "sys.argv=['native_dialog_worker'," + repr(flag) + "];"
                 "runpy.run_module('nivram.native_dialog_worker',run_name='__main__')")
    return [_source_interpreter(), '-I', '-S', '-c', bootstrap,
            str(Path(__file__).resolve().parents[1]), str(_source_packages())]


class NativeFileChoice:
    action = 'select'
    # Optional diagnostics stay disabled for a partially initialized fixture.
    _progress = None
    _parent_io = None
    _observation_binding = None

    def __init__(self, window, target, allowed_root, timeout=20, *, mode='file', progress=False,
                 parent_io=False, action='select', observation_binding=None):
        """Select one file by default; opt in to two inputs or one existing folder.

        The returned receipt verifies the owned dialog field and worker exit.
        Callers must independently verify the application's returned exact paths.
        """
        self.request = build_request(window, target, allowed_root, timeout,
                                     nonce=secrets.token_hex(16), mode=mode, action=action)
        self.mode = mode
        self.action = action
        if type(progress) is not bool:
            raise ValueError('Native progress must be explicitly enabled or disabled.')
        if type(parent_io) is not bool:
            raise ValueError('Parent I/O observations must be explicitly enabled or disabled.')
        self._progress = ProgressCapture(self.request['nonce']) if progress else None
        if observation_binding is not None:
            if (parent_io is not True or type(observation_binding) is not dict
                    or any(type(key) is not str for key in observation_binding)
                    or set(observation_binding) != {'attempt_id', 'source_sha256', 'origin'}):
                raise ValueError('An observation requires exact identity and enabled parent I/O.')
            # Immutable scalar identity copy made before the invocation. A source
            # hash is caller-declared, never derived from the PNG fixture/nonce.
            self._observation_binding = bind_parent_io(parent_io_diagnostic(None),
                **observation_binding, boundary=PRIMARY_BOUNDARY)
        self._parent_io = ParentIoCapture(
            primary_observation=self._observation_binding is not None) if parent_io else None
        self.timeout = timeout
        self.result = None
        self._token = CancellationToken()
        self._supervisor = WorkerSupervisor(max_workers=1)
        self._started = False
        self._deadline = None
        self._cancel_posted = False
        self._cleanup_confirmed = None
        self._cleanup_error_kind = None
        self.thread = threading.Thread(target=self._run, name='native-file-dialog-supervisor', daemon=True)

    def start(self):
        if self._started:
            raise RuntimeError('A native fixture selection can only start once.')
        self._started = True
        self._deadline = time.monotonic() + self.timeout
        self.thread.start()
        return self

    def _run(self):
        started = time.monotonic()
        phase, error_kind, returncode = 'command', None, None
        try:
            command = worker_command(progress=True) if self._progress is not None else worker_command()
            phase = 'request_encode'
            payload = json.dumps(self.request, allow_nan=False)
            phase = 'supervise'
            completed = self._supervisor.run(
                command, input_text=payload,
                timeout_seconds=self.timeout, cancellation_token=self._token,
                max_input_bytes=MAX_INPUT, max_stdout_bytes=MAX_OUTPUT, max_stderr_bytes=4096,
                **({**({'progress': self._progress} if self._progress is not None else {}),
                    **({'parent_io': self._parent_io} if self._parent_io is not None else {})}))
            phase = 'worker_exit'
            if type(completed.returncode) is not int or not -(2**31) <= completed.returncode < 2**32:
                raise ValueError('The native fixture worker exit code is unavailable.')
            returncode = completed.returncode
            if returncode != 0:
                error_kind = 'nonzero_exit'
                raise ValueError('The native fixture worker did not complete.')
            phase = 'result_decode'
            value = json.loads(completed.stdout)
            phase = 'result_validate'
            self.result = validate_result(value, self.request['nonce'], mode=self.mode, action=self.action)
        except Exception as error:
            error_kind = error_kind or _error_kind(error)
            reason = ('worker_timeout' if isinstance(error, subprocess.TimeoutExpired) else
                      'worker_cancelled' if isinstance(error, WorkerCancelled) else
                      'worker_output_limit' if isinstance(error, WorkerOutputLimit) else 'worker_failure')
            self.result = {'selected': False, 'reason': reason}
            if type(error) is WorkerProcessError:
                failure = worker_failure_diagnostic(exception_diagnostic_attribute(error, 'worker_failure'))
                self.result['worker_failure'] = failure
                if (failure['available'] and failure['category'] == 'cleanup_incomplete'
                        and failure['primary_kind'] == 'timeout'):
                    self.result['timeout_boundary'] = timeout_boundary_diagnostic(
                        exception_diagnostic_attribute(error, 'timeout_boundary'))
            if isinstance(error, subprocess.TimeoutExpired):
                # Only our exact standard exception can supply observational facts.
                boundary = exception_diagnostic_attribute(error, 'timeout_boundary')
                self.result['timeout_boundary'] = timeout_boundary_diagnostic(boundary)
        finally:
            if self._parent_io is not None:
                ParentIoCapture.freeze(self._parent_io)
            if self._progress is not None:
                ProgressCapture.freeze(self._progress)
            try:
                self._cleanup_confirmed = self._supervisor.shutdown(timeout_seconds=2)
            except Exception as error:
                self._cleanup_confirmed = False
                self._cleanup_error_kind = _error_kind(error)
            if self.result is None:
                self.result = {'selected': False, 'reason': 'worker_failure'}
            # The final exception still controls failure. A separate bounded
            # packet may retain a primary failure observed before cleanup.
            self.result.update(parent_phase=phase, error_kind=error_kind,
                               worker_returncode=returncode)
            self.result['child_progress'] = self.progress_snapshot()
            if self._parent_io is not None:
                self.result['parent_io'] = self.parent_io_snapshot()
            if self._observation_binding is not None:
                self.result['parent_io_observation'] = self.observation_snapshot()
            if self._cleanup_error_kind is not None:
                self.result['cleanup_error_kind'] = self._cleanup_error_kind
            self.result['worker_exit_confirmed'] = self._cleanup_confirmed
            self.result['elapsed_seconds'] = round(time.monotonic() - started, 3)
            if not self._cleanup_confirmed:
                self.result.update(selected=False, reason='worker_cleanup_unconfirmed')
                if self.action == 'cancel':
                    self.result['cancelled'] = False

    def progress_snapshot(self):
        return (ProgressCapture.snapshot(self._progress) if self._progress is not None
                else progress_diagnostic(None))

    def parent_io_snapshot(self):
        # This packet and child progress are separate, non-atomic observations.
        return (ParentIoCapture.snapshot(self._parent_io) if self._parent_io is not None
                else parent_io_diagnostic(None))

    def observation_identity(self):
        """Fresh caller-declared binding for diagnostic consumer comparison."""
        binding = self._observation_binding
        if binding is None:
            return None
        return {name: getattr(binding, name) for name in ('attempt_id', 'source_sha256', 'origin')}

    def observation_snapshot(self):
        """Return the existing codec's ASCII envelope, or no primary evidence.

        No observer snapshot or fixture finally block is promoted to primary.
        Later result decoding/cleanup can fail without rewriting this outcome.
        """
        if self._observation_binding is None or self._parent_io is None:
            return None
        primary = ParentIoCapture.primary_snapshot(self._parent_io)
        if primary is None:
            return None
        identity = NativeFileChoice.observation_identity(self)
        observation = bind_parent_io(primary['packet'], **identity, boundary=PRIMARY_BOUNDARY)
        projection = project_parent_io(observation,
            expected_attempt_id=identity['attempt_id'], expected_source_sha256=identity['source_sha256'],
            outcome=primary['outcome'])
        return encode_observation(projection).decode('ascii')

    def finish(self):
        if not self._started:
            raise RuntimeError('Start the native fixture selection before waiting.')
        self.thread.join(max(0, self._deadline + 3 - time.monotonic()))
        successful = (self.result and (self.result.get('cancelled') is True if self.action == 'cancel'
                                     else self.result.get('selected') is True))
        if (self.thread.is_alive() or not successful
                or self.result.get('worker_exit_confirmed') is not True):
            cleanup = self.close()
            error = RuntimeError('The controlled native file-dialog selection failed; see its bounded worker receipt.')
            error.dialog_result = dict(self.result or {'selected': False, 'reason': 'supervisor_incomplete'})
            error.dialog_result['cleanup'] = cleanup
            if self._parent_io is not None:
                error.dialog_result['parent_io'] = self.parent_io_snapshot()
            raise error
        return dict(self.result)

    def close(self, timeout=2):
        """Idempotent owned-child cleanup, without loading COM or UI Automation."""
        deadline = time.monotonic() + timeout
        if self._parent_io is not None:
            ParentIoCapture.freeze(self._parent_io)
        if self._progress is not None:
            ProgressCapture.freeze(self._progress)
        self._token.cancel()
        try:
            stopped = self._supervisor.shutdown(timeout_seconds=timeout)
        except Exception as error:
            stopped = False
            self._cleanup_error_kind = _error_kind(error)
        if self._started:
            self.thread.join(max(0, deadline - time.monotonic()))
        if not self._cancel_posted and not (self.result and self.result.get('selected')):
            try:
                self._cancel_posted = cancel_owned_dialog(self.request)
            except Exception:
                pass
        self._cleanup_confirmed = stopped and not self.thread.is_alive()
        return self.snapshot()

    cancel = close

    def snapshot(self):
        confirmed = self._cleanup_confirmed is True and not self.thread.is_alive()
        result = {'shutdownConfirmed': confirmed,
                'worker_exit_confirmed': confirmed,
                'supervisor_thread_exited': not self.thread.is_alive(),
                'cancel_requested': self._token.cancelled,
                'owned_dialog_cancel_posted': self._cancel_posted,
                'scope': 'direct child and exact parent-owned dialog only'}
        if self._cleanup_error_kind is not None:
            result['cleanup_error_kind'] = self._cleanup_error_kind
        return result
