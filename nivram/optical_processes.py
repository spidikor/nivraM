"""Windows-owned recognition trees, bounded before any worker code executes.

This is a resource/lifetime boundary, not an OS network or hostile-code sandbox.
The desktop itself is never assigned to these private jobs. Descendants inherit
the job and its aggregate committed-memory ceiling, including when nested in
the desktop's existing job. No process discovery or PID-based termination.
"""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import subprocess
import threading
import time

from .worker_processes import WorkerCancelled, WorkerProcessError, WorkerSupervisor
from .worker_resource_limits import _Accounting, _ExtendedLimit

MEMORY_BYTES = 4 * 1024 ** 3
WALL_SECONDS = 90.0
CLEANUP_SECONDS = 4.0  # Leaves room for the supervisor's cancellation observation.
_LIMIT_FLAGS = 0x00000200 | 0x00002000  # JOB_MEMORY | KILL_ON_JOB_CLOSE; no breakaway.


class _StartupInfo(c.Structure):
    _fields_ = [('cb', w.DWORD), ('lpReserved', w.LPWSTR), ('lpDesktop', w.LPWSTR),
                ('lpTitle', w.LPWSTR), ('dwX', w.DWORD), ('dwY', w.DWORD),
                ('dwXSize', w.DWORD), ('dwYSize', w.DWORD), ('dwXCountChars', w.DWORD),
                ('dwYCountChars', w.DWORD), ('dwFillAttribute', w.DWORD),
                ('dwFlags', w.DWORD), ('wShowWindow', w.WORD), ('cbReserved2', w.WORD),
                ('lpReserved2', c.POINTER(c.c_ubyte)), ('hStdInput', w.HANDLE),
                ('hStdOutput', w.HANDLE), ('hStdError', w.HANDLE)]


class _StartupInfoEx(c.Structure):
    _fields_ = [('StartupInfo', _StartupInfo), ('lpAttributeList', c.c_void_p)]


class _ProcessInfo(c.Structure):
    _fields_ = [('hProcess', w.HANDLE), ('hThread', w.HANDLE),
                ('dwProcessId', w.DWORD), ('dwThreadId', w.DWORD)]


class _WindowsApi:
    def __init__(self):
        if os.name != 'nt' or c.sizeof(c.c_void_p) != 8:
            raise WorkerProcessError('Protected recognition requires 64-bit Windows.')
        self.k = c.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            'GetHandleInformation': ([w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
            'SetInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            'QueryInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            'AssignProcessToJobObject': ([w.HANDLE, w.HANDLE], w.BOOL),
            'IsProcessInJob': ([w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)], w.BOOL),
            'TerminateJobObject': ([w.HANDLE, w.UINT], w.BOOL),
            'InitializeProcThreadAttributeList': ([c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)], w.BOOL),
            'UpdateProcThreadAttribute': ([c.c_void_p, w.DWORD, c.c_size_t, c.c_void_p, c.c_size_t, c.c_void_p, c.c_void_p], w.BOOL),
            'DeleteProcThreadAttributeList': ([c.c_void_p], None),
            'CreateProcessW': ([w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL,
                                w.DWORD, c.c_void_p, w.LPCWSTR, c.POINTER(_StartupInfoEx), c.POINTER(_ProcessInfo)], w.BOOL),
            'ResumeThread': ([w.HANDLE], w.DWORD),
            'WaitForSingleObject': ([w.HANDLE, w.DWORD], w.DWORD),
            'GetExitCodeProcess': ([w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
            'TerminateProcess': ([w.HANDLE, w.UINT], w.BOOL),
            'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'CloseHandle': ([w.HANDLE], w.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.k, name)
            function.argtypes, function.restype = arguments, result

    @staticmethod
    def check(value):
        if not value:
            raise OSError('The protected recognition process operation failed.')
        return value

    def configure(self, job, memory_bytes):
        limits = _ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = _LIMIT_FLAGS
        limits.JobMemoryLimit = memory_bytes
        self.check(self.k.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))

    def verify(self, job, memory_bytes, process=None):
        limits, handle_flags = _ExtendedLimit(), w.DWORD()
        self.check(self.k.QueryInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits), None))
        self.check(self.k.GetHandleInformation(job, c.byref(handle_flags)))
        if (limits.BasicLimitInformation.LimitFlags != _LIMIT_FLAGS
                or limits.JobMemoryLimit != memory_bytes or handle_flags.value & 1):
            raise OSError('The recognition resource limits could not be verified.')
        if process is not None:
            member = w.BOOL()
            self.check(self.k.IsProcessInJob(process, job, c.byref(member)))
            if not member.value or self.active(job) != 1:
                raise OSError('The recognition process ownership could not be verified.')

    def active(self, job):
        return self.accounting(job)['active']

    def accounting(self, job):
        accounting = _Accounting()
        self.check(self.k.QueryInformationJobObject(job, 1, c.byref(accounting), c.sizeof(accounting), None))
        return {'active': int(accounting.ActiveProcesses), 'total': int(accounting.TotalProcesses)}

    def members(self, job):
        capacity = 16
        while capacity <= 4096:
            class ProcessList(c.Structure):
                _fields_ = [('assigned', w.DWORD), ('count', w.DWORD),
                            ('ids', c.c_size_t * capacity)]
            listing = ProcessList()
            if self.k.QueryInformationJobObject(job, 3, c.byref(listing), c.sizeof(listing), None):
                if listing.count > capacity:
                    raise OSError('Invalid recognition process membership count.')
                return list(listing.ids[:listing.count])
            if c.get_last_error() != 234:  # ERROR_MORE_DATA
                raise OSError('Recognition process membership could not be read.')
            capacity *= 2
        raise OSError('Recognition process membership exceeds its tracking limit.')


class _JobProcess:
    """Minimal Popen-shaped adapter whose completion means the entire job exited."""

    def __init__(self, command, has_input, memory_bytes, *, before_start):
        if not Path(command[0]).is_absolute():
            raise WorkerProcessError('Protected recognition requires an absolute executable path.')
        self._lock = threading.RLock()
        self._api = api = _WindowsApi()
        import msvcrt
        self._job = self._process = None
        self._assigned = False
        self._members = {}
        self._returncode = None
        self._closed = False
        self.stdin = self.stdout = self.stderr = None
        children, attributes, initialized = [], None, False
        raw_fds = set()
        info = _ProcessInfo()

        def pipe():
            descriptors = os.pipe()
            raw_fds.update(descriptors)
            return descriptors

        def wrap(fd, mode):
            stream = os.fdopen(fd, mode, buffering=0)
            raw_fds.remove(fd)
            return stream

        try:
            self._job = api.check(api.k.CreateJobObjectW(None, None))
            api.configure(self._job, memory_bytes)
            api.verify(self._job, memory_bytes)
            if has_input:
                read_fd, write_fd = pipe()
                child_input = wrap(read_fd, 'rb')
                children.append(child_input)
                self.stdin = wrap(write_fd, 'wb')
            else:
                child_input = open(os.devnull, 'rb', buffering=0)
                children.append(child_input)
            for name in ('stdout', 'stderr'):
                read_fd, write_fd = pipe()
                setattr(self, name, wrap(read_fd, 'rb'))
                children.append(wrap(write_fd, 'wb'))
            handles = (w.HANDLE * 3)(*(msvcrt.get_osfhandle(stream.fileno()) for stream in children))
            for stream in children:
                os.set_inheritable(stream.fileno(), True)
            size = c.c_size_t()
            api.k.InitializeProcThreadAttributeList(None, 1, 0, c.byref(size))
            if not 0 < size.value <= 65536:
                raise OSError('The recognition handle list could not be allocated.')
            attributes = c.create_string_buffer(size.value)
            api.check(api.k.InitializeProcThreadAttributeList(attributes, 1, 0, c.byref(size)))
            initialized = True
            # PROC_THREAD_ATTRIBUTE_HANDLE_LIST: inherit exactly the three pipes.
            api.check(api.k.UpdateProcThreadAttribute(attributes, 0, 0x00020002,
                      c.cast(handles, c.c_void_p), c.sizeof(handles), None, None))
            startup = _StartupInfoEx()
            startup.StartupInfo.cb = c.sizeof(startup)
            startup.StartupInfo.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = handles
            startup.lpAttributeList = c.cast(attributes, c.c_void_p)
            # CREATE_SUSPENDED | CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT.
            api.check(api.k.CreateProcessW(command[0], c.create_unicode_buffer(subprocess.list2cmdline(command)),
                      None, None, True, 0x08080004, None, None, c.byref(startup), c.byref(info)))
            self._process = info.hProcess
            api.check(api.k.AssignProcessToJobObject(self._job, self._process))
            self._assigned = True
            api.verify(self._job, memory_bytes, self._process)
            self._capture_members()
            before_start()
            if api.k.ResumeThread(info.hThread) != 1:
                raise OSError('The protected recognition process could not be resumed.')
        except BaseException as error:
            # The exact newly created handle is safe even if job assignment failed.
            # The process has never run before verified assignment.
            cleanup_confirmed = True
            if info.hProcess:
                if self._job:
                    api.k.TerminateJobObject(self._job, 1)
                api.k.TerminateProcess(info.hProcess, 1)
                cleanup_confirmed = api.k.WaitForSingleObject(info.hProcess, 1000) == 0
                deadline = time.monotonic() + 1.0
                try:
                    while api.active(self._job) and time.monotonic() < deadline:
                        time.sleep(0.01)
                    cleanup_confirmed = cleanup_confirmed and api.active(self._job) == 0
                except OSError:
                    cleanup_confirmed = False
            if cleanup_confirmed:
                self.close()
            for stream in (self.stdin, self.stdout, self.stderr):
                if stream is not None:
                    stream.close()
            if not isinstance(error, Exception):
                raise
            if cleanup_confirmed and isinstance(error, (WorkerCancelled, subprocess.TimeoutExpired)):
                raise
            failure = WorkerProcessError('Protected local recognition could not start. The drawing is unchanged.')
            if not cleanup_confirmed:
                # The supervisor adopts this exact handle even though spawn
                # failed. Never discard an unassigned, unconfirmed process.
                failure.owned_process = self
            raise failure from error
        finally:
            if info.hThread:
                api.k.CloseHandle(info.hThread)
            if initialized:
                api.k.DeleteProcThreadAttributeList(attributes)
            for stream in children:
                stream.close()
            for fd in raw_fds:
                os.close(fd)

    def _capture_members(self):
        if not self._assigned:
            return
        try:
            for process_id in self._api.members(self._job):
                if process_id in self._members:
                    continue
                if len(self._members) >= 4096:
                    raise OSError('Recognition lifetime exceeds its process tracking limit.')
                handle = self._api.k.OpenProcess(0x100000 | 0x1000, False, process_id)
                if not handle:
                    # The process may have exited between the private job query
                    # and opening its handle. Never open a different PID source.
                    if c.get_last_error() == 87:
                        continue
                    raise OSError('Recognition member handle could not be retained.')
                member = w.BOOL()
                try:
                    self._api.check(self._api.k.IsProcessInJob(handle, self._job, c.byref(member)))
                    if not member.value:
                        raise OSError('Recognition member ownership could not be confirmed.')
                    self._members[process_id] = handle
                finally:
                    if self._members.get(process_id) != handle:
                        self._api.k.CloseHandle(handle)
        except OSError as error:
            raise WorkerProcessError('Recognition tree ownership could not be confirmed.') from error

    def poll(self):
        with self._lock:
            if self._closed:
                return self._returncode
            self._capture_members()
            wait = self._api.k.WaitForSingleObject(self._process, 0)
            if wait == 258:  # WAIT_TIMEOUT
                return None
            if wait != 0:
                raise WorkerProcessError('Recognition process exit could not be verified.')
            code = w.DWORD()
            try:
                self._api.check(self._api.k.GetExitCodeProcess(self._process, c.byref(code)))
                accounting = self._api.accounting(self._job)
                if accounting['active']:
                    # Even a normally exiting parent may leave a conversion child.
                    # Kill only this job; completion waits for accounting to reach zero.
                    self._api.check(self._api.k.TerminateJobObject(self._job, 1))
                    return None
            except OSError as error:
                raise WorkerProcessError('Recognition tree exit could not be verified.') from error
            if self._assigned and accounting['total'] != len(self._members):
                # A process can start and exit between snapshots. Do not turn
                # an incomplete lifetime inventory into a full exit receipt.
                raise WorkerProcessError('Recognition tree exit coverage could not be confirmed.')
            # Windows job accounting can reach zero while a terminated member
            # is still finishing kernel teardown. Also wait on retained, verified
            # member handles, rather than treating accounting alone as exit.
            for handle in self._members.values():
                member_wait = self._api.k.WaitForSingleObject(handle, 0)
                if member_wait == 258:
                    return None
                if member_wait != 0:
                    raise WorkerProcessError('Recognition member exit could not be verified.')
            self._returncode = int(code.value)
            return self._returncode

    def terminate(self):
        with self._lock:
            if not self._closed:
                try:
                    self._capture_members()
                finally:
                    self._api.check(self._api.k.TerminateJobObject(self._job, 1))
                if not self._assigned:
                    self._api.check(self._api.k.TerminateProcess(self._process, 1))

    kill = terminate

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            # Closing the noninherited job handle is also the last-resort tree stop.
            if self._job:
                self._api.k.CloseHandle(self._job)
            if self._process:
                self._api.k.CloseHandle(self._process)
            for handle in self._members.values():
                self._api.k.CloseHandle(handle)
            self._members.clear()


class OpticalSupervisor(WorkerSupervisor):
    def __init__(self, *, memory_bytes=MEMORY_BYTES):
        if type(memory_bytes) is not int or not 0 < memory_bytes <= MEMORY_BYTES:
            raise ValueError('Recognition memory must be a positive integer at most 4 GiB.')
        super().__init__(max_workers=1, max_timeout_seconds=WALL_SECONDS,
                         cleanup_timeout_seconds=CLEANUP_SECONDS)
        self._memory_bytes = memory_bytes

    def _spawn(self, command, has_input, *, before_start):
        try:
            return _JobProcess(command, has_input, self._memory_bytes, before_start=before_start)
        except WorkerProcessError as error:
            if hasattr(error, 'owned_process'):
                self._active.add(error.owned_process)
            raise

    def _release(self, process):
        process.close()
