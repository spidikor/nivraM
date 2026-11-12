"""One bounded child for complete molecular import and batch preparation.

This module and its parent-side validators are stdlib-only. The worker installs
its own OS quotas before reading a submitted document or importing RDKit. Other
chemistry routes and interpreter startup are outside this resource boundary.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from .import_protocol import (MAX_SOURCE_BYTES, MAX_TRANSPORT_BYTES, MAX_RESULT_BYTES,
    MAX_STDERR_BYTES, checked_batch_settings, checked_style, strict_json_loads, validate_prepared_result)
from .hash_style import HashStyleError
from .worker_processes import (CancellationToken, WorkerSupervisor, WorkerCancelled,
                              WorkerOutputLimit, WorkerProcessError)

SCHEMA = 'nivram-preparation/1'
MEMORY_BYTES = 1024 ** 3
CPU_SECONDS = 10.0
WALL_SECONDS = 20.0
from .import_limits import RADICAL_CONSISTENCY_MESSAGE, SINGLET_MESSAGE
_ERRORS = {
    'inconsistent_mol_radical': (422, RADICAL_CONSISTENCY_MESSAGE),
    'unsupported_singlet_radical': (422, SINGLET_MESSAGE),
    'unsupported_atom_annotation': (422, 'MOL atom aliases, atom values and attachment-point annotations cannot be preserved. Use a copy with these annotations explicitly resolved or removed in the source editor. The drawing is unchanged.'),
    'invalid_input': (422, 'The input is malformed or outside the supported 2D chemistry scope. The drawing is unchanged.'),
    'empty_drawing': (422, 'The input contains no molecular drawing to export.'),
    'render_failed': (422, 'The isolated molecular drawing could not be rendered. The drawing is unchanged.'),
    'hash_style_failed': (422, 'Thicker, spaced bars could not preserve this hashed stereobond. Choose Engine spacing for Hashed stereobonds and apply appearance again; the drawing is unchanged.'),
    'busy': (429, 'Another molecular import or batch preparation is running. Try again after it finishes.'),
    'unavailable': (503, 'The protected local import worker is unavailable. The drawing is unchanged.'),
    'timed_out': (504, 'Molecular preparation exceeded its time limit. The drawing is unchanged.'),
    'worker_failed': (502, 'The local import worker did not complete safely. The drawing is unchanged.'),
}
from .population_state import PopulationStateError, _ERRORS as _STATE_ERRORS
_STATE_CODES = frozenset(('state_binding', 'state_structure', 'state_mapping', 'state_stoichiometry', 'state_identity'))
_ERRORS.update({code: _STATE_ERRORS[code] for code in _STATE_CODES})


class ImportPreparationError(ValueError):
    def __init__(self, code):
        if code not in _ERRORS:
            raise ValueError('Invalid preparation error category.')
        self.code = code
        self.http_status, message = _ERRORS[code]
        super().__init__(message)


def _command():
    if getattr(sys, 'frozen', False):
        worker = Path(sys.executable).with_name('nivram-worker.exe')
        if not worker.is_file():
            raise ImportPreparationError('unavailable')
        return [str(worker), '--prepare-import']
    return [sys.executable, '-B', '-s', '-m', 'nivram.import_preparation', '--worker']


def _source(source):
    if not isinstance(source, str) or not source or len(source) > MAX_SOURCE_BYTES:
        raise ImportPreparationError('invalid_input')
    try:
        if len(source.encode('utf-8')) > MAX_SOURCE_BYTES:
            raise ImportPreparationError('invalid_input')
    except UnicodeError:
        raise ImportPreparationError('invalid_input') from None
    return source


def _check_limits(value):
    # Accept only a receipt for this operation's exact required limits. The
    # child obtains these through OS readback, not from submitted request data.
    expected = {'installed': True, 'platform': 'windows', 'mechanism': 'job_object',
                'scope': 'current_worker_process', 'memory_bytes': MEMORY_BYTES,
                'cpu_user_time_100ns': int(CPU_SECONDS * 10_000_000),
                'cpu_seconds': CPU_SECONDS, 'active_process_limit': 1,
                'memory_metric': 'process_commit_bytes', 'cpu_metric': 'process_user_time',
                'kill_on_job_close': True, 'handle_inheritable': False,
                'assigned': True, 'verified': True, 'limit_flags': 0x210A}
    if not isinstance(value, dict) or value != expected:
        raise ValueError('Required worker resource limits were not verified.')
    # Python equality alone would accept 1 for true and false for zero.
    if any(type(value[key]) is not bool for key in ('installed', 'kill_on_job_close',
            'handle_inheritable', 'assigned', 'verified')):
        raise ValueError('Malformed resource-limit receipt.')
    for key in ('memory_bytes', 'cpu_user_time_100ns', 'active_process_limit', 'limit_flags'):
        if type(value[key]) is not int:
            raise ValueError('Malformed numeric resource limit.')
    return dict(value)


class ImportPreparationService:
    def __init__(self, *, supervisor=None, command_factory=None):
        self._supervisor = supervisor if supervisor is not None else WorkerSupervisor(max_workers=1)
        self._command_factory = command_factory or _command
        self._lock = threading.Lock()
        self._closed = False
        self._active = None
        self._last_limits = None
        self._last_status = None

    def snapshot(self):
        with self._lock:
            return {'acceptingJobs': not self._closed, 'activeJobs': int(self._active is not None),
                    'maximumActiveJobs': 1, 'lastStatus': self._last_status,
                    'lastResourceLimits': dict(self._last_limits) if self._last_limits else None}

    def _run(self, request, cancellation_token):
        token = cancellation_token if cancellation_token is not None else CancellationToken()
        with self._lock:
            if self._closed or token.cancelled:
                raise WorkerCancelled('Molecular preparation was cancelled.')
            if self._active is not None:
                raise ImportPreparationError('busy')
            self._active = token
        try:
            try:
                payload = json.dumps(request, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
                if len(payload.encode()) > MAX_TRANSPORT_BYTES:
                    raise ImportPreparationError('invalid_input')
                completed = self._supervisor.run(self._command_factory(), input_text=payload,
                    timeout_seconds=WALL_SECONDS, cancellation_token=token,
                    max_input_bytes=MAX_TRANSPORT_BYTES, max_stdout_bytes=MAX_RESULT_BYTES,
                    max_stderr_bytes=MAX_STDERR_BYTES)
                self._supervisor.check_cancelled(token)
                if completed.returncode != 0:
                    raise ImportPreparationError('worker_failed')
                envelope = strict_json_loads(completed.stdout)
                if not isinstance(envelope, dict) or envelope.get('schema') != SCHEMA:
                    raise ValueError('Invalid preparation response.')
                if envelope.get('status') == 'error':
                    allowed_errors = {'invalid_input', 'empty_drawing', 'render_failed', 'hash_style_failed', 'unavailable', 'worker_failed'}
                    if request['mode'] in ('import', 'batch'):
                        allowed_errors.update(('unsupported_atom_annotation', 'unsupported_singlet_radical', 'inconsistent_mol_radical'))
                    if request['mode'] == 'population-state':
                        allowed_errors.update(_STATE_CODES)
                    if set(envelope) != {'schema', 'status', 'code'} or envelope['code'] not in allowed_errors:
                        raise ValueError('Invalid preparation error.')
                    raise ImportPreparationError(envelope['code'])
                if set(envelope) != {'schema', 'status', 'limits', 'result'} or envelope['status'] != 'ok':
                    raise ValueError('Invalid preparation response fields.')
                limits = _check_limits(envelope['limits'])
                result = validate_prepared_result(envelope['result'], mode=request['mode'])
                self._supervisor.check_cancelled(token)
                with self._lock:
                    if self._closed or token.cancelled:
                        raise WorkerCancelled('Molecular preparation was cancelled.')
                    self._last_limits, self._last_status = limits, 'ready'
                return result
            except ImportPreparationError as error:
                with self._lock:
                    self._last_status = error.code
                raise
            except WorkerCancelled:
                with self._lock:
                    self._last_status = 'cancelled'
                raise
            except subprocess.TimeoutExpired:
                with self._lock:
                    self._last_status = 'timed_out'
                raise ImportPreparationError('timed_out') from None
            except WorkerProcessError:
                # This includes indeterminate cleanup. Do not recycle a service
                # whose ownership state was not confirmed by the supervisor.
                with self._lock:
                    self._closed = True
                    self._last_status = 'worker_failed'
                raise ImportPreparationError('worker_failed') from None
            except (OSError, UnicodeError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
                with self._lock:
                    self._last_status = 'worker_failed'
                raise ImportPreparationError('worker_failed') from None
        finally:
            with self._lock:
                if self._active is token:
                    self._active = None

    def prepare_import(self, source, style=None, *, cancellation_token=None):
        try:
            request = {'schema': SCHEMA, 'mode': 'import', 'source': _source(source), 'style': checked_style(style)}
        except (ValueError, TypeError, OverflowError):
            raise ImportPreparationError('invalid_input') from None
        return self._run(request, cancellation_token)

    def prepare_batch(self, text, extension, settings, *, cancellation_token=None):
        try:
            if extension not in ('.nivram', '.mol'):
                raise ValueError('Unsupported batch source.')
            request = {'schema': SCHEMA, 'mode': 'batch', 'source': _source(text),
                       'extension': extension, 'settings': checked_batch_settings(settings)}
        except (ValueError, TypeError, OverflowError):
            raise ImportPreparationError('invalid_input') from None
        return self._run(request, cancellation_token)

    def prepare_population_state(self, data, *, cancellation_token=None):
        from .population_state import validate_request
        request = {'schema': SCHEMA, 'mode': 'population-state', 'request': validate_request(data)}
        return self._run(request, cancellation_token)

    def shutdown(self, *, timeout_seconds=2.0):
        with self._lock:
            self._closed = True
            if self._active is not None:
                self._active.cancel()
        return self._supervisor.shutdown(timeout_seconds=timeout_seconds)


def _prepare(request):
    """Called only after quotas are installed in the child process."""
    if not isinstance(request, dict) or request.get('schema') != SCHEMA:
        raise ImportPreparationError('invalid_input')
    mode = request.get('mode')
    if mode == 'population-state':
        if set(request) != {'schema', 'mode', 'request'}:
            raise ImportPreparationError('invalid_input')
        from .population_state import prepare_state
        from rdkit import rdBase
        with rdBase.BlockLogs():
            try:
                return prepare_state(request['request'])
            except HashStyleError:
                raise ImportPreparationError('hash_style_failed') from None
            except PopulationStateError as error:
                raise ImportPreparationError(error.code) from None
    if mode == 'import':
        if set(request) != {'schema', 'mode', 'source', 'style'}:
            raise ImportPreparationError('invalid_input')
    elif mode == 'batch':
        if set(request) != {'schema', 'mode', 'source', 'extension', 'settings'}:
            raise ImportPreparationError('invalid_input')
    else:
        raise ImportPreparationError('invalid_input')
    source = _source(request['source'])
    from rdkit import rdBase
    from .chemistry import import_structure, InconsistentMolRadicalError, UnsupportedAtomAnnotationError, UnsupportedSingletRadicalError
    from .persistence import validate_project
    from .scene import render_scene
    with rdBase.BlockLogs():
        try:
            if mode == 'import':
                graph = import_structure(source)
                style = request['style']
            else:
                settings = checked_batch_settings(request['settings'])
                if request['extension'] == '.nivram':
                    # Preserve prior semantics: reject invalid original project
                    # settings even though batch settings will replace them.
                    project = validate_project(strict_json_loads(source))
                elif request['extension'] == '.mol':
                    lines = source.splitlines()
                    endings = [index for index, line in enumerate(lines) if re.fullmatch(r'\s*M\s+END\s*', line)]
                    if (len(lines) < 5 or not re.search(r'V(?:2000|3000)\s*$', lines[3])
                            or len(endings) != 1 or any(line.strip() for line in lines[endings[0] + 1:])
                            or '$$$$' in source or '\x00' in source):
                        raise ValueError('Not a single 2D MOL block.')
                    project = {'schema': 'nivram/1', 'graph': import_structure(source)}
                else:
                    raise ValueError('Unsupported batch extension.')
                project = validate_project({**project, 'style': settings['style'], 'export': settings['export']})
                graph, style = project['graph'], project['style']
                if not graph['atoms']:
                    raise ImportPreparationError('empty_drawing')
        except InconsistentMolRadicalError:
            raise ImportPreparationError('inconsistent_mol_radical') from None
        except UnsupportedSingletRadicalError:
            raise ImportPreparationError('unsupported_singlet_radical') from None
        except UnsupportedAtomAnnotationError:
            raise ImportPreparationError('unsupported_atom_annotation') from None
        except ImportPreparationError:
            raise
        except (ValueError, TypeError, KeyError, RuntimeError, OverflowError, RecursionError):
            raise ImportPreparationError('invalid_input') from None
        try:
            scene = render_scene(graph, style)
        except HashStyleError:
            raise ImportPreparationError('hash_style_failed') from None
        except (ValueError, TypeError, KeyError, RuntimeError, OverflowError, RecursionError):
            raise ImportPreparationError('render_failed') from None
    return {'graph': graph, 'scene': scene, **({'project': project} if mode == 'batch' else {})}


def worker_main():
    # Do not read stdin or import chemistry until the OS verified the limits.
    from .worker_resource_limits import install_worker_limits, ResourceLimitsUnavailable
    try:
        limits = install_worker_limits(memory_bytes=MEMORY_BYTES, cpu_seconds=CPU_SECONDS)
        _check_limits(limits)
    except (ResourceLimitsUnavailable, ValueError):
        response = {'schema': SCHEMA, 'status': 'error', 'code': 'unavailable'}
    else:
        try:
            raw = sys.stdin.buffer.read(MAX_TRANSPORT_BYTES + 1)
            if len(raw) > MAX_TRANSPORT_BYTES:
                raise ImportPreparationError('invalid_input')
            request = strict_json_loads(raw.decode('utf-8'))
            result = _prepare(request)
            response = {'schema': SCHEMA, 'status': 'ok', 'limits': limits, 'result': result}
        except ImportPreparationError as error:
            response = {'schema': SCHEMA, 'status': 'error', 'code': error.code}
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            response = {'schema': SCHEMA, 'status': 'error', 'code': 'invalid_input'}
        except Exception:
            response = {'schema': SCHEMA, 'status': 'error', 'code': 'worker_failed'}
    try:
        encoded = json.dumps(response, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(encoded) > MAX_RESULT_BYTES:
            raise ValueError('Result limit exceeded.')
    except (ValueError, TypeError, RecursionError, MemoryError):
        encoded = json.dumps({'schema': SCHEMA, 'status': 'error', 'code': 'worker_failed'}).encode()
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 0


if __name__ == '__main__':
    if sys.argv[1:] != ['--worker']:
        raise SystemExit('Unsupported local preparation operation.')
    raise SystemExit(worker_main())
