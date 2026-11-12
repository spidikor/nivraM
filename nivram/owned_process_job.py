"""Bounded cleanup of descendants captured by a private Windows job.

Call ``for_current_process`` once, before the desktop host starts any children.
Windows then assigns subsequently created descendants to this private job. No
process names, parent-PID scans, external PIDs, or breakaway flags are used.

The job includes the host itself. Its noninheritable kill-on-close handle MUST
remain open until OS process teardown: explicitly closing it would kill the host.
There is deliberately no close method, finalizer, or atexit handler. Retaining a
job does not replace the WebView2 collection-exit event required for cache deletion.
"""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
import math
import os
import threading
import time


_CURRENT_JOB = None
_RETAINED_JOBS = []
_FACTORY_LOCK = threading.Lock()
_MAX_PROCESSES = 8192
_MAX_QUERY_ATTEMPTS = 8


class _Accounting(c.Structure):
    _fields_ = [('TotalUserTime', c.c_longlong), ('TotalKernelTime', c.c_longlong),
                ('ThisPeriodTotalUserTime', c.c_longlong), ('ThisPeriodTotalKernelTime', c.c_longlong),
                ('TotalPageFaultCount', w.DWORD), ('TotalProcesses', w.DWORD),
                ('ActiveProcesses', w.DWORD), ('TotalTerminatedProcesses', w.DWORD)]


class _BasicLimit(c.Structure):
    _fields_ = [('PerProcessUserTimeLimit', c.c_longlong), ('PerJobUserTimeLimit', c.c_longlong),
                ('LimitFlags', w.DWORD), ('MinimumWorkingSetSize', c.c_size_t),
                ('MaximumWorkingSetSize', c.c_size_t), ('ActiveProcessLimit', w.DWORD),
                ('Affinity', c.c_size_t), ('PriorityClass', w.DWORD), ('SchedulingClass', w.DWORD)]


class _IoCounters(c.Structure):
    _fields_ = [(name, c.c_ulonglong) for name in (
        'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
        'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _ExtendedLimit(c.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimit), ('IoInfo', _IoCounters),
                ('ProcessMemoryLimit', c.c_size_t), ('JobMemoryLimit', c.c_size_t),
                ('PeakProcessMemoryUsed', c.c_size_t), ('PeakJobMemoryUsed', c.c_size_t)]


class _ProcessListHeader(c.Structure):
    _fields_ = [('assigned', w.DWORD), ('listed', w.DWORD)]


class _Win32JobApi:
    def __init__(self):
        if os.name != 'nt':
            raise OSError('Owned process jobs require Windows.')
        self.k = c.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            'SetHandleInformation': ([w.HANDLE, w.DWORD, w.DWORD], w.BOOL),
            'SetInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            'AssignProcessToJobObject': ([w.HANDLE, w.HANDLE], w.BOOL),
            'GetCurrentProcess': ([], w.HANDLE),
            'QueryInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'IsProcessInJob': ([w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)], w.BOOL),
            'WaitForSingleObject': ([w.HANDLE, w.DWORD], w.DWORD),
            'TerminateProcess': ([w.HANDLE, w.UINT], w.BOOL),
            'CloseHandle': ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.k, name)
            function.argtypes = args
            function.restype = result

    @staticmethod
    def _check(result):
        if not result:
            raise c.WinError(c.get_last_error())
        return result

    def create(self):
        job = self._check(self.k.CreateJobObjectW(None, None))
        try:
            # Clear HANDLE_FLAG_INHERIT explicitly. Descendants inherit job
            # membership through Windows, never a handle keeping the job alive.
            self._check(self.k.SetHandleInformation(job, 1, 0))
            limits = _ExtendedLimit()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE only.
            self._check(self.k.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))
            return job
        except BaseException:
            self.k.CloseHandle(job)  # Host has not been assigned yet.
            raise

    def assign_current(self, job):
        self._check(self.k.AssignProcessToJobObject(job, self.k.GetCurrentProcess()))

    def close_unassigned_job(self, job):
        self._check(self.k.CloseHandle(job))

    def query(self, job, deadline=None):
        capacity = 64
        for _ in range(_MAX_QUERY_ATTEMPTS):
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('Owned job query deadline reached.')
            size = c.sizeof(_ProcessListHeader) + capacity * c.sizeof(c.c_size_t)
            buffer = c.create_string_buffer(size)
            success = self.k.QueryInformationJobObject(job, 3, buffer, size, None)
            error = c.get_last_error() if not success else 0
            header = _ProcessListHeader.from_buffer(buffer)
            if not success and error != 234:  # ERROR_MORE_DATA is the only resize case.
                raise c.WinError(error)
            if header.assigned > _MAX_PROCESSES or header.listed > capacity:
                raise OSError('Owned job process list exceeds its bounded capacity.')
            if not success or header.assigned > header.listed:
                next_capacity = max(capacity * 2, int(header.assigned))
                if next_capacity > _MAX_PROCESSES:
                    raise OSError('Owned job process list exceeds its bounded capacity.')
                capacity = next_capacity
                continue
            if header.assigned != header.listed:
                raise OSError('Owned job process list is inconsistent.')
            array = (c.c_size_t * header.listed).from_buffer(buffer, c.sizeof(_ProcessListHeader))
            members = tuple(int(pid) for pid in array)
            accounting = _Accounting()
            self._check(self.k.QueryInformationJobObject(
                job, 1, c.byref(accounting), c.sizeof(accounting), None))
            return members, int(accounting.ActiveProcesses)
        raise OSError('Owned job process list did not stabilize within its query bound.')

    def open_process(self, pid):
        # PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE | PROCESS_TERMINATE.
        return self._check(self.k.OpenProcess(0x1000 | 0x100000 | 0x0001, False, pid))

    def is_member(self, job, handle):
        member = w.BOOL()
        self._check(self.k.IsProcessInJob(handle, job, c.byref(member)))
        return bool(member.value)

    def has_exited(self, handle):
        result = self.k.WaitForSingleObject(handle, 0)
        if result == 0:
            return True
        if result == 258:
            return False
        raise c.WinError(c.get_last_error())

    def terminate(self, handle):
        self._check(self.k.TerminateProcess(handle, 1))

    def close_process(self, handle):
        self._check(self.k.CloseHandle(handle))


def _error(operation, exception=None):
    code = getattr(exception, 'winerror', None) or getattr(exception, 'errno', None)
    return {'operation': operation, 'code': code if isinstance(code, int) else None}


class OwnedProcessJob:
    """Private host ownership, with JSON-safe receipts containing no process IDs.

    ``snapshot()`` reports observed membership, without terminating anything.
    ``terminate_children(timeout_seconds)`` is a last resort after normal shutdown
    and after the caller has stopped spawning work. Its budget must be in (0, 5].
    ``confirmed`` is true only when the job is observed to contain just the host
    and no operation failed. ``forced_count`` counts accepted termination calls,
    independently of exit confirmation. ``remaining_children`` is null if the
    latest membership query could not be verified. Native calls themselves are
    synchronous; bounds apply to retries and waits, not a stalled Windows kernel.
    """

    def __init__(self, api, job, host_pid):
        self._api = api
        self._job = job
        self._host_pid = host_pid
        self._termination_lock = threading.Lock()

    @classmethod
    def for_current_process(cls):
        """Assign the current host before its first child; fail startup on error."""
        global _CURRENT_JOB
        with _FACTORY_LOCK:
            if _CURRENT_JOB is not None:
                return _CURRENT_JOB
            api = _Win32JobApi()
            job = api.create()
            try:
                owned = cls(api, job, os.getpid())
            except BaseException:
                api.close_unassigned_job(job)
                raise
            try:
                api.assign_current(job)
            except OSError:
                # Only a reported API failure proves assignment did not occur.
                # An asynchronous interruption must not close a possibly
                # self-assigned kill-on-close handle.
                api.close_unassigned_job(job)
                raise
            # Do not close this handle, even if later startup fails. OS process
            # teardown closes it and thereby cleans up this host's descendants.
            _RETAINED_JOBS.append(owned)
            _CURRENT_JOB = owned
            return owned

    def _inspect(self, deadline=None):
        members, active = self._api.query(self._job, deadline)
        if (self._host_pid not in members or len(members) != len(set(members))
                or any(not isinstance(pid, int) or pid <= 0 for pid in members)
                or not isinstance(active, int) or active < 1):
            raise OSError('Owned job membership could not be verified.')
        children = tuple(pid for pid in members if pid != self._host_pid)
        # Membership and accounting are separate observations. Concurrent exits
        # may reduce one count first; both must show host-only before confirmation.
        return children, max(len(children), active - 1)

    @staticmethod
    def _receipt(remaining, forced, errors):
        confirmed = remaining == 0 and not errors
        return {'installed': True, 'status': 'confirmed' if confirmed else 'unconfirmed',
                'confirmed': confirmed, 'forced_count': forced,
                'remaining_children': remaining, 'errors': errors}

    def snapshot(self):
        try:
            _, remaining = self._inspect()
            return self._receipt(remaining, 0, [])
        except Exception as error:
            return self._receipt(None, 0, [_error('query', error)])

    def terminate_children(self, timeout_seconds=2.0):
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 5):
            raise ValueError('Owned child cleanup timeout must be finite and in (0, 5] seconds.')
        deadline = time.monotonic() + timeout_seconds
        if not self._termination_lock.acquire(timeout=timeout_seconds):
            return self._receipt(None, 0, [_error('cleanup_busy')])
        forced = 0
        remaining = None
        errors = []
        attempted = set()

        def record(operation, exception=None):
            # Keep diagnostic memory bounded even if every captured member fails.
            if len(errors) < 16:
                errors.append(_error(operation, exception))

        try:
            while time.monotonic() < deadline:
                try:
                    children, remaining = self._inspect(deadline)
                except Exception as error:
                    remaining = None
                    record('query', error)
                    break
                if remaining == 0:
                    break
                for pid in children:
                    if time.monotonic() >= deadline:
                        break
                    if pid == self._host_pid or pid in attempted:
                        continue
                    attempted.add(pid)
                    handle = None
                    operation = 'open'
                    try:
                        handle = self._api.open_process(pid)
                        operation = 'membership'
                        if not self._api.is_member(self._job, handle):
                            record('membership')
                            continue
                        operation = 'exit_check'
                        if self._api.has_exited(handle):
                            continue
                        if time.monotonic() >= deadline:
                            break
                        operation = 'terminate'
                        self._api.terminate(handle)
                        forced += 1
                    except Exception as error:
                        record(operation, error)
                    finally:
                        if handle is not None:
                            try:
                                self._api.close_process(handle)
                            except Exception as error:
                                record('close_process', error)
                pause = min(0.02, max(0.0, deadline - time.monotonic()))
                if pause:
                    time.sleep(pause)
            if remaining != 0 and not errors:
                record('deadline')
            return self._receipt(remaining, forced, errors)
        finally:
            self._termination_lock.release()
