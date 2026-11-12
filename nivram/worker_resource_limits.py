"""Fail-closed, worker-only Windows process quotas.

Call ``install_worker_limits`` in the disposable worker, before reading input or
importing a native parser. This creates a new unnamed job; it never changes the
desktop host's job. The quota is committed process memory and accumulated USER
CPU time (not working-set size, CPU percentage, or wall time). Windows checks CPU
time periodically, so this is not an exact wall-clock deadline.

An assigned kill-on-close handle must remain open until OS process teardown.
There is deliberately no close method, finalizer, or atexit hook. Even an
uncertain assignment or failed readback retains the handle and rejects retries.
The caller must stop the worker on ResourceLimitsUnavailable.

The 1 GiB / 10 second defaults are provisional ceilings awaiting representative
workload calibration. Overrides can only lower those ceilings. Job quotas are
resource containment, not a security sandbox for hostile native code.
"""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
import math
import os
import threading


DEFAULT_MEMORY_BYTES = 1024 ** 3
DEFAULT_CPU_SECONDS = 10.0
_TICKS_PER_SECOND = 10_000_000
_LIMIT_FLAGS = 0x00000002 | 0x00000008 | 0x00000100 | 0x00002000
_INSTALL_LOCK = threading.Lock()
_CURRENT_LIMITS = None
_RETAINED_JOBS = []


class ResourceLimitsUnavailable(RuntimeError):
    """The worker must not accept input because quotas could not be verified."""


class _BasicLimit(c.Structure):
    _fields_ = [('PerProcessUserTimeLimit', c.c_longlong),
                ('PerJobUserTimeLimit', c.c_longlong), ('LimitFlags', w.DWORD),
                ('MinimumWorkingSetSize', c.c_size_t), ('MaximumWorkingSetSize', c.c_size_t),
                ('ActiveProcessLimit', w.DWORD), ('Affinity', c.c_size_t),
                ('PriorityClass', w.DWORD), ('SchedulingClass', w.DWORD)]


class _IoCounters(c.Structure):
    _fields_ = [(name, c.c_ulonglong) for name in (
        'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
        'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _ExtendedLimit(c.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimit), ('IoInfo', _IoCounters),
                ('ProcessMemoryLimit', c.c_size_t), ('JobMemoryLimit', c.c_size_t),
                ('PeakProcessMemoryUsed', c.c_size_t), ('PeakJobMemoryUsed', c.c_size_t)]


class _Accounting(c.Structure):
    _fields_ = [('TotalUserTime', c.c_longlong), ('TotalKernelTime', c.c_longlong),
                ('ThisPeriodTotalUserTime', c.c_longlong), ('ThisPeriodTotalKernelTime', c.c_longlong),
                ('TotalPageFaultCount', w.DWORD), ('TotalProcesses', w.DWORD),
                ('ActiveProcesses', w.DWORD), ('TotalTerminatedProcesses', w.DWORD)]


class _Win32QuotaApi:
    """Small OS adapter; constructing it does not modify the calling process."""

    def __init__(self):
        if os.name != 'nt':
            raise ResourceLimitsUnavailable('Worker resource limits require Windows.')
        self.k = c.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            'SetHandleInformation': ([w.HANDLE, w.DWORD, w.DWORD], w.BOOL),
            'GetHandleInformation': ([w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
            'SetInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            'QueryInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            'AssignProcessToJobObject': ([w.HANDLE, w.HANDLE], w.BOOL),
            'IsProcessInJob': ([w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)], w.BOOL),
            'GetCurrentProcess': ([], w.HANDLE),
            'CloseHandle': ([w.HANDLE], w.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.k, name)
            function.argtypes = arguments
            function.restype = result

    @staticmethod
    def _check(result):
        if not result:
            raise c.WinError(c.get_last_error())
        return result

    def create(self):
        # NULL security attributes: noninheritable; NULL name: a new private job.
        return self._check(self.k.CreateJobObjectW(None, None))

    def make_noninheritable(self, job):
        self._check(self.k.SetHandleInformation(job, 1, 0))

    def configure(self, job, memory_bytes, cpu_ticks):
        limits = _ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = _LIMIT_FLAGS
        limits.BasicLimitInformation.PerProcessUserTimeLimit = cpu_ticks
        limits.BasicLimitInformation.ActiveProcessLimit = 1
        limits.ProcessMemoryLimit = memory_bytes
        self._check(self.k.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))

    def query_limits(self, job):
        limits = _ExtendedLimit()
        self._check(self.k.QueryInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits), None))
        return {'limit_flags': int(limits.BasicLimitInformation.LimitFlags),
                'memory_bytes': int(limits.ProcessMemoryLimit),
                'cpu_user_time_100ns': int(limits.BasicLimitInformation.PerProcessUserTimeLimit),
                'active_process_limit': int(limits.BasicLimitInformation.ActiveProcessLimit)}

    def handle_inheritable(self, job):
        flags = w.DWORD()
        self._check(self.k.GetHandleInformation(job, c.byref(flags)))
        return bool(flags.value & 1)

    def assign_current(self, job):
        self._check(self.k.AssignProcessToJobObject(job, self.k.GetCurrentProcess()))

    def current_is_member(self, job):
        member = w.BOOL()
        self._check(self.k.IsProcessInJob(self.k.GetCurrentProcess(), job, c.byref(member)))
        return bool(member.value)

    def active_processes(self, job):
        accounting = _Accounting()
        self._check(self.k.QueryInformationJobObject(job, 1, c.byref(accounting), c.sizeof(accounting), None))
        return int(accounting.ActiveProcesses)

    def close_unassigned(self, job):
        self._check(self.k.CloseHandle(job))


def _configuration(memory_bytes, cpu_seconds):
    if (isinstance(memory_bytes, bool) or not isinstance(memory_bytes, int)
            or not 0 < memory_bytes <= DEFAULT_MEMORY_BYTES):
        raise ValueError('Worker memory limit must be an integer in (0, 1 GiB].')
    if (isinstance(cpu_seconds, bool) or not isinstance(cpu_seconds, (int, float))
            or not 0 < cpu_seconds <= DEFAULT_CPU_SECONDS or not math.isfinite(cpu_seconds)):
        raise ValueError('Worker user CPU limit must be finite and in (0, 10] seconds.')
    # Round downward so quantization never grants more CPU than requested.
    ticks = math.floor(cpu_seconds * _TICKS_PER_SECOND)
    if ticks < 1:
        raise ValueError('Worker user CPU limit must be at least 100 nanoseconds.')
    return memory_bytes, ticks


def _verified_limits(api, job, memory_bytes, cpu_ticks):
    actual = api.query_limits(job)
    expected = {'limit_flags': _LIMIT_FLAGS, 'memory_bytes': memory_bytes,
                'cpu_user_time_100ns': cpu_ticks, 'active_process_limit': 1}
    if actual != expected or api.handle_inheritable(job):
        raise ResourceLimitsUnavailable('Worker resource limit readback did not match.')
    return actual


def _receipt(state):
    api, job = state['api'], state['job']
    actual = _verified_limits(api, job, *state['configuration'])
    if not api.current_is_member(job) or api.active_processes(job) != 1:
        raise ResourceLimitsUnavailable('Worker resource limit membership could not be verified.')
    return {'installed': True, 'platform': 'windows', 'mechanism': 'job_object',
            'scope': 'current_worker_process', **actual,
            'cpu_seconds': actual['cpu_user_time_100ns'] / _TICKS_PER_SECOND,
            'memory_metric': 'process_commit_bytes', 'cpu_metric': 'process_user_time',
            'kill_on_job_close': True, 'handle_inheritable': False,
            'assigned': True, 'verified': True}


def install_worker_limits(*, memory_bytes=DEFAULT_MEMORY_BYTES, cpu_seconds=DEFAULT_CPU_SECONDS):
    """Install/query worker quotas or raise; never run an unbounded fallback.

    Repeated calls with the same effective configuration re-query the exact job.
    Changing the configuration or retrying an uncertain setup is rejected. Only
    pre-assignment failures may close a job handle. The receipt reports this
    job's configuration; an ancestor job can impose stricter effective limits.
    """
    global _CURRENT_LIMITS
    configuration = _configuration(memory_bytes, cpu_seconds)
    with _INSTALL_LOCK:
        if _CURRENT_LIMITS is not None:
            if _CURRENT_LIMITS['configuration'] != configuration:
                raise ResourceLimitsUnavailable('Worker resource limits cannot be reconfigured.')
            if _CURRENT_LIMITS['failed']:
                raise ResourceLimitsUnavailable('Worker resource limit setup previously failed.')
            try:
                return _receipt(_CURRENT_LIMITS)
            except Exception as error:
                _CURRENT_LIMITS['failed'] = True
                raise ResourceLimitsUnavailable('Worker resource limits could not be revalidated.') from error

        api = None
        job = None
        assignment_started = False
        try:
            api = _Win32QuotaApi()
            job = api.create()
            api.make_noninheritable(job)
            api.configure(job, *configuration)
            _verified_limits(api, job, *configuration)
            state = {'api': api, 'job': job, 'configuration': configuration, 'failed': True}
            # Retain BEFORE entering the assignment API: even interruption after
            # successful native assignment must never close our own kill handle.
            _RETAINED_JOBS.append(state)
            _CURRENT_LIMITS = state
            assignment_started = True
            api.assign_current(job)
            receipt = _receipt(state)
            state['failed'] = False
            return receipt
        except BaseException as error:
            if job is not None and not assignment_started:
                try:
                    api.close_unassigned(job)
                except Exception:
                    pass  # Still fail closed; no current process was assigned.
            if not isinstance(error, Exception):
                raise
            raise ResourceLimitsUnavailable('Worker resource limits could not be installed.') from error
