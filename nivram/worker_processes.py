"""Bounded ownership of direct scientific child processes.

Only Popen handles created here are stopped. No global process discovery, PID
lookup, shell, or process-tree killing is used. Normal cancellation/shutdown is
supported; an OS crash or forced termination of the parent is outside this
portable supervisor's guarantees. Popen creation itself cannot be interrupted.
"""
from __future__ import annotations

import atexit
import math
import os
import subprocess
import threading
import time
from typing import Sequence

from .native_dialog_progress import ProgressCapture
from .parent_io import ParentIoCapture


class WorkerCancelled(RuntimeError):
    """A cancellation token or the owning supervisor stopped the operation."""


class WorkerOutputLimit(ValueError):
    """A child exceeded one of its independently bounded output streams."""


class WorkerProcessError(ValueError):
    """A child could not be safely run or its I/O could not be completed."""


def timeout_boundary_diagnostic(value):
    """Keep deadline facts only; no commands, input, output, or private text."""
    keys = ('stage', 'child_created', 'child_running', 'returncode',
            'stdin_done', 'stdout_done', 'stderr_done')
    unavailable = {'available': False, **dict.fromkeys(keys)}
    if type(value) is not dict:
        return unavailable
    try:
        if any(type(key) is not str for key in value):
            return unavailable
        supplied = set(value)
        if supplied not in (set(keys), set(keys) | {'available'}):
            return unavailable
        if 'available' in value and value['available'] is not True:
            return unavailable
        returncode = value['returncode']
        if (type(value['stage']) is not str or value['stage'] != 'supervise'
                or value['child_created'] is not True
                or type(value['child_running']) is not bool
                or value['child_running'] != (returncode is None)
                or (returncode is not None and (type(returncode) is not int
                    or not -(2**31) <= returncode < 2**32))
                or any(type(value[key]) is not bool for key in
                       ('stdin_done', 'stdout_done', 'stderr_done'))):
            return unavailable
        return {'available': True, **{key: value[key] for key in keys}}
    except Exception:
        # Observation can become unavailable; it cannot replace the timeout.
        return unavailable


def cleanup_boundary_diagnostic(value):
    """Copy only the supervisor's bounded post-cleanup observations."""
    keys = ('stop_confirmed', 'stdin_done', 'stdout_done', 'stderr_done', 'threads_exited')
    unavailable = {'available': False, **dict.fromkeys(keys)}
    if type(value) is not dict or any(type(key) is not str for key in value):
        return unavailable
    if (set(value) not in (set(keys), set(keys) | {'available'})
            or ('available' in value and value['available'] is not True)
            or any(type(value[key]) is not bool for key in keys)):
        return unavailable
    return {'available': True, **{key: value[key] for key in keys}}


def worker_failure_diagnostic(value):
    """Reject arbitrary text and incoherent facts; diagnostics grant no success."""
    keys = {'category', 'primary_kind', 'primary_category', 'cleanup'}
    unavailable = {'available': False, 'category': None, 'primary_kind': None,
                   'primary_category': None, 'cleanup': cleanup_boundary_diagnostic(None)}
    categories = {'input_limit', 'capacity', 'io', 'cleanup_incomplete'}
    kinds = {'timeout', 'cancelled', 'output_limit', 'worker_process_error',
             'unicode_decode_error', 'os_error', 'value_error', 'type_error', 'other_error'}
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or set(value) not in (keys, keys | {'available'})
            or ('available' in value and value['available'] is not True)):
        return unavailable
    category, primary, prior = value['category'], value['primary_kind'], value['primary_category']
    if (type(category) is not str or category not in categories
            or (primary is not None and (type(primary) is not str or primary not in kinds))
            or (prior is not None and (type(prior) is not str or prior not in categories))
            or (prior is not None and primary != 'worker_process_error')
            or (category != 'cleanup_incomplete' and (primary is not None or prior is not None))):
        return unavailable
    cleanup = cleanup_boundary_diagnostic(value['cleanup'])
    # None denotes an error before a child existed. A sanitized unavailable
    # packet can be read again, but arbitrary malformed nested data cannot.
    if not cleanup['available'] and value['cleanup'] is not None:
        nested = value['cleanup']
        if (type(nested) is not dict or any(type(key) is not str for key in nested)
                or set(nested) != set(cleanup) or nested['available'] is not False
                or any(nested[key] is not None for key in cleanup if key != 'available')):
            return unavailable
    if category == 'cleanup_incomplete' and (not cleanup['available'] or
            (cleanup['stop_confirmed'] and cleanup['threads_exited'])):
        return unavailable
    if cleanup['available'] and (category in ('input_limit', 'capacity') or
            (category == 'io' and not (cleanup['stop_confirmed'] and cleanup['threads_exited']))):
        return unavailable
    return {'available': True, 'category': category, 'primary_kind': primary,
            'primary_category': prior, 'cleanup': cleanup}


def exception_diagnostic_attribute(error, name):
    """Read internal metadata without invoking custom mappings or key equality."""
    if ((type(error) is not WorkerProcessError and type(error) is not subprocess.TimeoutExpired)
            or type(name) is not str or name not in ('worker_failure', 'timeout_boundary')):
        return None
    attributes = vars(error)
    if type(attributes) is not dict or any(type(key) is not str for key in attributes):
        return None
    return dict.get(attributes, name)


def _process_error(message, category, *, primary=None, cleanup=None):
    """Capture an explicitly held primary error, never traverse exception chains."""
    kind, prior = None, None
    if primary is not None:
        kind = 'other_error'
        for cls, label in ((subprocess.TimeoutExpired, 'timeout'), (WorkerCancelled, 'cancelled'),
                (WorkerOutputLimit, 'output_limit'), (WorkerProcessError, 'worker_process_error'),
                (UnicodeDecodeError, 'unicode_decode_error'), (OSError, 'os_error'),
                (ValueError, 'value_error'), (TypeError, 'type_error')):
            if type(primary) is cls:
                kind = label
                break
        if type(primary) is WorkerProcessError:
            prior = worker_failure_diagnostic(exception_diagnostic_attribute(primary, 'worker_failure'))['category']
    error = WorkerProcessError(message)
    error.worker_failure = worker_failure_diagnostic({'category': category,
        'primary_kind': kind, 'primary_category': prior, 'cleanup': cleanup})
    if type(primary) is subprocess.TimeoutExpired:
        error.timeout_boundary = timeout_boundary_diagnostic(exception_diagnostic_attribute(primary, 'timeout_boundary'))
    return error


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()


def _positive_seconds(value: float, maximum: float) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and 0 < value <= maximum)


def _signal(process: subprocess.Popen, *, force: bool = False) -> None:
    if process.poll() is not None:
        return
    try:
        process.kill() if force else process.terminate()
    except OSError:
        # Exiting concurrently is normal. The caller must still observe exit.
        pass


def _wait_until(processes: Sequence[subprocess.Popen], deadline: float) -> bool:
    while any(process.poll() is None for process in processes):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.02, remaining))
    return True


def _stop_owned(processes: Sequence[subprocess.Popen], deadline: float) -> bool:
    for process in processes:
        _signal(process)
    if _wait_until(processes, min(deadline, time.monotonic() + 0.2)):
        return True
    for process in processes:
        _signal(process, force=True)
    return _wait_until(processes, deadline)


class WorkerSupervisor:
    def __init__(self, *, max_workers: int = 4, cleanup_timeout_seconds: float = 1.0,
                 max_timeout_seconds: float = 60.0) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or not 1 <= max_workers <= 16:
            raise ValueError('The scientific worker limit must be between 1 and 16.')
        if not _positive_seconds(cleanup_timeout_seconds, 5.0):
            raise ValueError('The scientific worker cleanup limit must be at most five seconds.')
        if not _positive_seconds(max_timeout_seconds, 300.0):
            raise ValueError('The scientific worker maximum time limit must be at most 300 seconds.')
        self._max_workers = max_workers
        self._cleanup_timeout = cleanup_timeout_seconds
        self._max_timeout = max_timeout_seconds
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._active: set[subprocess.Popen] = set()

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(process.poll() is None for process in self._active)

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def check_cancelled(self, cancellation_token: CancellationToken | None = None) -> None:
        if self.closed or (cancellation_token is not None and cancellation_token.cancelled):
            raise WorkerCancelled('The local scientific job was cancelled.')

    def _spawn(self, command, has_input, *, before_start):
        before_start()
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        return subprocess.Popen(
            list(command), stdin=subprocess.PIPE if has_input else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
            close_fds=True, shell=False, **options,
        )

    def _release(self, process):
        """Hook for an owner with additional native handles; direct Popen needs none."""

    def _prune_completed(self):
        # Caller holds the supervisor lock. Native release is idempotent; a
        # run() cleanup that already retained this object may release it too.
        for process in tuple(self._active):
            if process.poll() is not None:
                self._release(process)
                self._active.discard(process)

    def run(self, command: Sequence[str], *, input_text: str = '',
            timeout_seconds: float, cancellation_token: CancellationToken | None = None,
            max_input_bytes: int = 65_536, max_stdout_bytes: int = 131_072,
            max_stderr_bytes: int = 131_072, progress=None,
            parent_io=None) -> subprocess.CompletedProcess[str]:
        """Run one direct child; timeout excludes at most the cleanup budget.

        Output is collected incrementally with independent byte limits. Reader
        and writer threads cannot retain more than the configured buffers plus
        one small read block. Errors never embed commands, input, or output.
        """
        if progress is not None and type(progress) is not ProgressCapture:
            raise ValueError('Invalid native progress capture.')
        if parent_io is not None and type(parent_io) is not ParentIoCapture:
            raise ValueError('Invalid parent pipe observation capture.')
        if (isinstance(command, (str, bytes)) or not command
                or any(not isinstance(arg, str) or '\x00' in arg for arg in command)):
            raise ValueError('A scientific worker requires an explicit argument list.')
        if not _positive_seconds(timeout_seconds, self._max_timeout):
            raise ValueError(f'The scientific worker time limit must be at most {self._max_timeout:g} seconds.')
        for limit in (max_input_bytes, max_stdout_bytes, max_stderr_bytes):
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 16 * 1024 * 1024:
                raise ValueError('Invalid scientific worker byte limit.')
        if not isinstance(input_text, str):
            raise ValueError('Scientific worker input must be text.')
        payload = input_text.encode('utf-8')
        if parent_io is not None:
            ParentIoCapture.configure(parent_io, writer_expected=bool(payload),
                                      stdout_limit=max_stdout_bytes)
        if len(payload) > max_input_bytes:
            raise _process_error('The scientific worker input limit was exceeded.', 'input_limit')
        self.check_cancelled(cancellation_token)
        deadline = time.monotonic() + timeout_seconds

        def before_start():
            self.check_cancelled(cancellation_token)
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(['local-scientific-worker'], timeout_seconds)

        with self._lock:
            self.check_cancelled(cancellation_token)
            self._prune_completed()
            if len(self._active) >= self._max_workers:
                raise _process_error('The local scientific worker capacity is occupied.', 'capacity')
            process = self._spawn(command, bool(payload), before_start=before_start)
            self._active.add(process)

        stdout, stderr = bytearray(), bytearray()
        overflow, io_error = threading.Event(), threading.Event()
        done = [threading.Event(), threading.Event(), threading.Event()]

        def read_stream(stream, output: bytearray, limit: int, finished: threading.Event,
                        capture=None, channel=None) -> None:
            if parent_io is not None:
                ParentIoCapture.mark(parent_io, channel + '_reader_entered')
            try:
                while True:
                    chunk = stream.read(8192)
                    if not chunk:
                        if parent_io is not None and type(chunk) is bytes and chunk == b'':
                            ParentIoCapture.mark(parent_io, channel + '_eof')
                        break
                    available = limit - len(output)
                    output.extend(chunk[:available])
                    if parent_io is not None and channel == 'stdout':
                        ParentIoCapture.retained(parent_io, len(output))
                    if capture is not None:
                        ProgressCapture.feed(capture, chunk[:available])
                    if len(chunk) > available:
                        overflow.set()
                        break
            except OSError:
                io_error.set()
            finally:
                stream.close()
                finished.set()

        def write_input() -> None:
            if parent_io is not None:
                ParentIoCapture.mark(parent_io, 'stdin_writer_entered')
            try:
                offset = 0
                while offset < len(payload):
                    written = process.stdin.write(payload[offset:])
                    if not written:
                        raise OSError('Scientific worker input pipe stopped.')
                    offset += written
                if parent_io is not None:
                    ParentIoCapture.mark(parent_io, 'stdin_write_completed')
            except BrokenPipeError:
                pass
            except OSError:
                if process.poll() is None:
                    io_error.set()
            finally:
                process.stdin.close()
                if parent_io is not None:
                    ParentIoCapture.mark(parent_io, 'stdin_close_completed')
                done[2].set()

        threads = [
            threading.Thread(target=read_stream, args=(process.stdout, stdout, max_stdout_bytes, done[0], None, 'stdout'), daemon=True),
            threading.Thread(target=read_stream, args=(process.stderr, stderr, max_stderr_bytes, done[1], progress, 'stderr'), daemon=True),
        ]
        if payload:
            threads.append(threading.Thread(target=write_input, daemon=True))
        else:
            done[2].set()
        primary_error = None
        try:
            for thread in threads:
                thread.start()
            while True:
                self.check_cancelled(cancellation_token)
                if overflow.is_set():
                    raise WorkerOutputLimit('The local scientific worker exceeded an output limit.')
                if io_error.is_set():
                    raise _process_error('The local scientific worker could not complete its input or output.', 'io')
                returncode = process.poll()
                if returncode is not None and all(event.is_set() for event in done):
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # Preserve the existing failure and omit all payload bytes.
                    error = subprocess.TimeoutExpired(['local-scientific-worker'], timeout_seconds)
                    # Facts observed before cleanup, using this loop's last poll.
                    error.timeout_boundary = {
                        'stage': 'supervise', 'child_created': True,
                        'child_running': returncode is None, 'returncode': returncode,
                        'stdin_done': done[2].is_set(), 'stdout_done': done[0].is_set(),
                        'stderr_done': done[1].is_set(),
                    }
                    raise error
                time.sleep(min(0.02, remaining))
            self.check_cancelled(cancellation_token)
            if overflow.is_set():
                raise WorkerOutputLimit('The local scientific worker exceeded an output limit.')
            if io_error.is_set():
                raise _process_error('The local scientific worker could not complete its input or output.', 'io')
            return subprocess.CompletedProcess(
                list(command), returncode, stdout.decode('utf-8'), stderr.decode('utf-8'),
            )
        except BaseException as error:
            primary_error = error
            raise
        finally:
            # Freeze before owned cleanup can cause further child emissions.
            # This independent lock covers only bounded in-memory parsing.
            if parent_io is not None:
                outcome = ('returned' if primary_error is None else
                           'timeout' if isinstance(primary_error, subprocess.TimeoutExpired) else
                           'cancelled' if isinstance(primary_error, WorkerCancelled) else
                           'interrupted' if isinstance(primary_error, (KeyboardInterrupt, SystemExit)) else 'error')
                ParentIoCapture.freeze_primary(parent_io, outcome)
            if progress is not None:
                ProgressCapture.freeze(progress)
            cleanup_deadline = time.monotonic() + self._cleanup_timeout
            try:
                stopped = _stop_owned([process], cleanup_deadline)
                for thread in threads:
                    if thread.ident is not None:
                        thread.join(max(0.0, cleanup_deadline - time.monotonic()))
                if process.poll() is not None and self._lock.acquire(blocking=False):
                    try:
                        self._active.discard(process)
                    finally:
                        self._lock.release()
                cleanup = {'stop_confirmed': stopped, 'stdin_done': done[2].is_set(),
                           'stdout_done': done[0].is_set(), 'stderr_done': done[1].is_set(),
                           'threads_exited': not any(thread.is_alive() for thread in threads)}
                if not stopped or not cleanup['threads_exited']:
                    raise _process_error('Scientific worker cleanup could not be confirmed within its time limit.',
                                         'cleanup_incomplete', primary=primary_error, cleanup=cleanup)
                if type(primary_error) is WorkerProcessError:
                    packet = worker_failure_diagnostic(exception_diagnostic_attribute(primary_error, 'worker_failure'))
                    if packet['available']:
                        packet['cleanup'] = cleanup
                        primary_error.worker_failure = worker_failure_diagnostic(packet)
            finally:
                self._release(process)

    def shutdown(self, *, timeout_seconds: float = 2.0) -> bool:
        """Reject new work, stop owned handles, and return whether exit was seen.

        False is an unconfirmed cleanup receipt, never a claim of success. The
        event is set before waiting for a spawn/registration already in progress.
        """
        if not _positive_seconds(timeout_seconds, 10.0):
            raise ValueError('The scientific shutdown time limit must be at most ten seconds.')
        deadline = time.monotonic() + timeout_seconds
        self._closed.set()
        if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            return False
        try:
            processes = tuple(self._active)
        finally:
            self._lock.release()
        stopped = _stop_owned(processes, deadline)
        if self._lock.acquire(blocking=False):
            try:
                self._prune_completed()
            finally:
                self._lock.release()
        return stopped


DEFAULT_SUPERVISOR = WorkerSupervisor()
atexit.register(DEFAULT_SUPERVISOR.shutdown)
