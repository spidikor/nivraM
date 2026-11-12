"""Synthetic Windows process trees only; no recognition model or network use.

The runner is never assigned to a test job. Stable handles are duplicated from
the adapter or opened only for explicitly created fixture children and checked
against that exact private job. No machine-wide process enumeration is used.
"""
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from nivram import optical_processes as optical
from nivram.worker_processes import CancellationToken, WorkerCancelled, WorkerOutputLimit, WorkerProcessError


MIB = 1024 ** 2
PYTHON = str(Path(sys.base_prefix) / 'python.exe') if os.name == 'nt' else sys.executable


def command(script):
    return [PYTHON, '-I', '-S', '-B', '-u', '-c', script]


class _BasicLimits(c.Structure):
    _fields_ = [('process_time', c.c_longlong), ('job_time', c.c_longlong),
                ('flags', w.DWORD), ('min_working', c.c_size_t), ('max_working', c.c_size_t),
                ('active_limit', w.DWORD), ('affinity', c.c_size_t),
                ('priority', w.DWORD), ('scheduling', w.DWORD)]


class _IoCounters(c.Structure):
    _fields_ = [(name, c.c_ulonglong) for name in ('read_ops', 'write_ops', 'other_ops',
                                                 'read_bytes', 'write_bytes', 'other_bytes')]


class _Limits(c.Structure):
    _fields_ = [('basic', _BasicLimits), ('io', _IoCounters),
                ('process_memory', c.c_size_t), ('job_memory', c.c_size_t),
                ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]


class _Accounting(c.Structure):
    _fields_ = [('user', c.c_longlong), ('kernel', c.c_longlong),
                ('period_user', c.c_longlong), ('period_kernel', c.c_longlong),
                ('faults', w.DWORD), ('total', w.DWORD), ('active', w.DWORD), ('terminated', w.DWORD)]


class _MemoryUsage(c.Structure):
    # Microsoft's hcsshim defines information class 28 with these two ULONG64 fields:
    # https://github.com/microsoft/hcsshim/blob/main/internal/winapi/jobobject.go
    _fields_ = [('job_memory', c.c_ulonglong), ('peak_job', c.c_ulonglong)]


class NativeHandles:
    """Independent Win32 observations, with explicit ownership of every handle."""
    def __init__(self):
        self.k = c.WinDLL('kernel32', use_last_error=True)
        declarations = {
            'GetCurrentProcess': ([], w.HANDLE),
            'DuplicateHandle': ([w.HANDLE, w.HANDLE, w.HANDLE, c.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD], w.BOOL),
            'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'IsProcessInJob': ([w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)], w.BOOL),
            'WaitForSingleObject': ([w.HANDLE, w.DWORD], w.DWORD),
            'GetHandleInformation': ([w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
            'SetHandleInformation': ([w.HANDLE, w.DWORD, w.DWORD], w.BOOL),
            'QueryInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            'TerminateJobObject': ([w.HANDLE, w.UINT], w.BOOL),
            'TerminateProcess': ([w.HANDLE, w.UINT], w.BOOL),
            'CreateEventW': ([c.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR], w.HANDLE),
            'CloseHandle': ([w.HANDLE], w.BOOL),
        }
        for name, (arguments, result) in declarations.items():
            function = getattr(self.k, name)
            function.argtypes, function.restype = arguments, result

    @staticmethod
    def check(value):
        if not value:
            raise c.WinError(c.get_last_error())
        return value

    def duplicate(self, handle):
        copied = w.HANDLE()
        current = self.k.GetCurrentProcess()
        self.check(self.k.DuplicateHandle(current, handle, current, c.byref(copied), 0, False, 2))
        return copied.value

    def flags(self, handle):
        flags = w.DWORD()
        self.check(self.k.GetHandleInformation(handle, c.byref(flags)))
        return flags.value

    def member(self, process, job):
        present = w.BOOL()
        self.check(self.k.IsProcessInJob(process, job, c.byref(present)))
        return bool(present.value)

    def exited(self, process):
        result = self.k.WaitForSingleObject(process, 0)
        if result not in (0, 258):
            raise c.WinError(c.get_last_error())
        return result == 0

    def limits(self, job):
        limits = _Limits()
        self.check(self.k.QueryInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits), None))
        return limits

    def active(self, job):
        accounting = _Accounting()
        self.check(self.k.QueryInformationJobObject(job, 1, c.byref(accounting), c.sizeof(accounting), None))
        return accounting.active

    def memory_usage(self, job):
        usage = _MemoryUsage()
        self.check(self.k.QueryInformationJobObject(job, 28, c.byref(usage), c.sizeof(usage), None))
        return usage


class OwnedObservation:
    def __init__(self, api, job, process):
        self.api = api
        self.original_job_flags = api.flags(job)
        self.job = api.duplicate(job)
        self.handles = []
        try:
            self.handles.append(api.duplicate(process))
        except BaseException:
            api.k.CloseHandle(self.job)
            raise

    def capture_children(self, process_ids):
        for process_id in process_ids:
            # The IDs came from Popen objects in our synthetic tree, not a scan.
            handle = self.api.check(self.api.k.OpenProcess(0x100000 | 0x1000 | 1, False, process_id))
            if not self.api.member(handle, self.job):
                self.api.k.CloseHandle(handle)
                raise AssertionError('An explicitly created descendant escaped its private job.')
            self.handles.append(handle)

    def close(self):
        if self.job is None:
            return
        # Guaranteed bounded fallback, even when the behavior under test fails.
        self.api.k.TerminateJobObject(self.job, 1)
        for handle in self.handles:
            if not self.api.exited(handle):
                self.api.k.TerminateProcess(handle, 1)
            self.api.k.WaitForSingleObject(handle, 2000)
            self.api.k.CloseHandle(handle)
        self.api.k.CloseHandle(self.job)
        self.job = None


class ObservedSupervisor(optical.OpticalSupervisor):
    def __init__(self, api, **kwargs):
        super().__init__(**kwargs)
        self.api, self.observation = api, None
        self.spawned = threading.Event()

    def _spawn(self, command, has_input, *, before_start):
        process = super()._spawn(command, has_input, before_start=before_start)
        try:
            self.observation = OwnedObservation(self.api, process._job, process._process)
        except BaseException:
            process.terminate()
            process.close()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            raise
        self.spawned.set()
        return process


def tree_script(directory, mode):
    """Three live generations; descendants deliberately retain standard pipes."""
    directory = str(directory)
    leaf = '\n'.join([
        'from pathlib import Path', 'import time',
        f'Path({directory!r}, "leaf-ready").write_text("ready")', 'time.sleep(60)',
    ])
    branch = '\n'.join([
        'from pathlib import Path', 'import json,os,subprocess,time',
        f'child=subprocess.Popen({command(leaf)!r}, stdin=subprocess.DEVNULL, creationflags=0x08000000)',
        f'root=Path({directory!r})',
        'while not (root/"leaf-ready").exists(): time.sleep(.01)',
        '(root/"ready.json").write_text(json.dumps({"children":[os.getpid(),child.pid]}))',
        'time.sleep(60)',
    ])
    return '\n'.join([
        'from pathlib import Path', 'import subprocess,sys,time',
        f'child=subprocess.Popen({command(branch)!r}, stdin=subprocess.DEVNULL, creationflags=0x08000000)',
        f'root=Path({directory!r})',
        'while not (root/"go").exists(): time.sleep(.01)',
        ('raise SystemExit(0)' if mode == 'normal' else
         f'sys.{mode}.buffer.write(b"x"*65536); sys.{mode}.buffer.flush(); time.sleep(60)' if mode in ('stdout', 'stderr')
         else 'time.sleep(60)'),
    ])


def memory_script(directory):
    """At most 208 MiB requested; the private job is lowered to 192 MiB."""
    allocator = '''import ctypes as c,json,os,time
from pathlib import Path
k=c.WinDLL("kernel32",use_last_error=True)
k.VirtualAlloc.argtypes=[c.c_void_p,c.c_size_t,c.c_ulong,c.c_ulong]; k.VirtualAlloc.restype=c.c_void_p
k.VirtualFree.argtypes=[c.c_void_p,c.c_size_t,c.c_ulong]; k.VirtualFree.restype=c.c_int
blocks=[]; rejected=False; error=0
try:
 for _ in range(COUNT):
  address=k.VirtualAlloc(None,4*1024**2,0x3000,4)
  if not address:
   rejected=True; error=c.get_last_error(); break
  blocks.append(address)
 Path(REPORT).write_text(json.dumps({"pid":os.getpid(),"committed":len(blocks)*4*1024**2,"rejected":rejected,"error":error}))
 time.sleep(60)
finally:
 for address in blocks: k.VirtualFree(address,0,0x8000)
'''
    first = allocator.replace('COUNT', '20').replace('REPORT', repr(str(directory / 'first.json')))
    second = allocator.replace('COUNT', '32').replace('REPORT', repr(str(directory / 'second.json')))
    return '\n'.join([
        'from pathlib import Path', 'import json,subprocess,time', f'root=Path({str(directory)!r})',
        f'first=subprocess.Popen({command(first)!r}, stdin=subprocess.DEVNULL, creationflags=0x08000000)',
        'while not (root/"first.json").exists(): time.sleep(.01)',
        f'second=subprocess.Popen({command(second)!r}, stdin=subprocess.DEVNULL, creationflags=0x08000000)',
        'while not (root/"second.json").exists(): time.sleep(.01)',
        '(root/"ready.json").write_text(json.dumps({"children":[first.pid,second.pid]}))', 'time.sleep(60)',
    ])


class OpticalConfigurationTests(unittest.TestCase):
    def test_memory_cannot_exceed_four_gib_and_time_cannot_exceed_ninety_seconds(self):
        for value in (0, -1, True, 1.5, 4*1024**3+1, float('inf')):
            with self.subTest(memory=value), self.assertRaises(ValueError):
                optical.OpticalSupervisor(memory_bytes=value)
        supervisor = optical.OpticalSupervisor()
        try:
            for value in (0, True, 90.001, float('inf'), float('nan')):
                with self.subTest(timeout=value), patch.object(supervisor, '_spawn') as spawn:
                    with self.assertRaises(ValueError):
                        supervisor.run(command('pass'), timeout_seconds=value)
                    spawn.assert_not_called()
        finally:
            supervisor.shutdown()


@unittest.skipUnless(os.name == 'nt' and c.sizeof(c.c_void_p) == 8, 'Requires real 64-bit Windows jobs.')
class WindowsOpticalProcessTests(unittest.TestCase):
    def setUp(self):
        self.api = NativeHandles()
        self.owned = []

    def tearDown(self):
        errors = []
        for supervisor, thread in reversed(self.owned):
            try:
                supervisor.shutdown(timeout_seconds=4)
            except BaseException as error:
                errors.append(error)
            finally:
                if supervisor.observation is not None:
                    supervisor.observation.close()
                thread.join(5)
                if thread.is_alive():
                    errors.append(AssertionError('The owned supervisor thread did not finish.'))
        if errors:
            raise errors[0]

    def launch(self, script, *, memory_bytes=192*MIB, timeout=20, token=None, **limits):
        supervisor = ObservedSupervisor(self.api, memory_bytes=memory_bytes)
        outcome = {}
        def run():
            outcome['started'] = time.monotonic()
            try:
                outcome['result'] = supervisor.run(command(script), timeout_seconds=timeout,
                                                   cancellation_token=token, **limits)
            except BaseException as error:
                outcome['error'] = error
            finally:
                outcome['finished'] = time.monotonic()
        thread = threading.Thread(target=run, daemon=True)
        self.owned.append((supervisor, thread))
        thread.start()
        self.assertTrue(supervisor.spawned.wait(4), repr(outcome))
        return supervisor, thread, outcome

    def wait_json(self, path, timeout=4):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            try:
                return json.loads(path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(.01)
        self.fail('Synthetic fixture readiness was not observed: '+path.name)

    def tree_ready(self, directory, supervisor):
        report = self.wait_json(directory / 'ready.json')
        self.assertEqual(len(report['children']), 2)
        supervisor.observation.capture_children(report['children'])
        self.assertTrue(all(not self.api.exited(handle) for handle in supervisor.observation.handles))
        self.assertGreaterEqual(self.api.active(supervisor.observation.job), 3)
        return report

    def assert_tree_finished(self, supervisor, thread, outcome, trigger):
        thread.join(max(0, trigger+4.9-time.monotonic()))
        self.assertFalse(thread.is_alive(), 'Owned-tree cleanup must finish within five seconds.')
        self.assertLess(outcome['finished']-trigger, 5)
        self.assertTrue(all(self.api.exited(handle) for handle in supervisor.observation.handles),
                        'Root, child and grandchild must have signaled process handles before completion.')
        self.assertEqual(self.api.active(supervisor.observation.job), 0)
        self.assertEqual(supervisor.active_count, 0)

    def test_cancellation_timeout_and_each_output_limit_stop_entire_tree_and_preserve_unrelated(self):
        unrelated = subprocess.Popen(command('import time; time.sleep(60)'), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            for mode in ('cancel', 'timeout', 'stdout', 'stderr'):
                with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix='optical-tree-') as name:
                    directory, token = Path(name), CancellationToken()
                    timeout = 6 if mode == 'timeout' else 20
                    supervisor, thread, outcome = self.launch(tree_script(directory, mode), timeout=timeout,
                        token=token, max_stdout_bytes=1024, max_stderr_bytes=1024)
                    try:
                        self.tree_ready(directory, supervisor)
                        if mode == 'timeout':
                            trigger = outcome['started']+timeout
                        else:
                            trigger = time.monotonic()
                            if mode == 'cancel':
                                token.cancel()
                            else:
                                (directory/'go').write_text('go')
                        self.assert_tree_finished(supervisor, thread, outcome, trigger)
                        expected = WorkerCancelled if mode == 'cancel' else subprocess.TimeoutExpired if mode == 'timeout' else WorkerOutputLimit
                        self.assertIsInstance(outcome.get('error'), expected)
                        self.assertIsNone(unrelated.poll(), 'The separately owned control must survive every cleanup.')
                    finally:
                        token.cancel()
                        supervisor.shutdown(timeout_seconds=4)
                        supervisor.observation.close()
                        thread.join(5)
        finally:
            if unrelated.poll() is None:
                unrelated.kill()
            unrelated.wait(timeout=3)

    def test_normal_root_exit_reaps_surviving_child_and_grandchild_before_returning(self):
        with tempfile.TemporaryDirectory(prefix='optical-normal-') as name:
            directory = Path(name)
            supervisor, thread, outcome = self.launch(tree_script(directory, 'normal'))
            try:
                self.tree_ready(directory, supervisor)
                trigger = time.monotonic()
                (directory/'go').write_text('go')
                self.assert_tree_finished(supervisor, thread, outcome, trigger)
                self.assertNotIn('error', outcome)
                self.assertEqual(outcome['result'].returncode, 0)
            finally:
                supervisor.shutdown(timeout_seconds=4)
                supervisor.observation.close()
                thread.join(5)

    def test_real_default_and_lowered_job_limits_are_exact_and_not_inherited(self):
        for memory in (4*1024**3, 128*MIB):
            with self.subTest(memory=memory):
                token = CancellationToken()
                supervisor, thread, outcome = self.launch('import time; time.sleep(60)', memory_bytes=memory, token=token)
                limits = self.api.limits(supervisor.observation.job)
                self.assertEqual(limits.basic.flags, 0x2200)  # Aggregate job commit + kill on final handle close.
                self.assertEqual(limits.job_memory, memory)
                self.assertEqual(limits.process_memory, 0)
                self.assertEqual(limits.basic.active_limit, 0)
                self.assertEqual(limits.basic.process_time, 0)
                self.assertEqual(limits.basic.job_time, 0)
                self.assertFalse(supervisor.observation.original_job_flags & 1)
                trigger = time.monotonic(); token.cancel()
                self.assert_tree_finished(supervisor, thread, outcome, trigger)
                self.assertIsInstance(outcome.get('error'), WorkerCancelled)

    def test_memory_ceiling_applies_to_combined_descendants_not_each_process(self):
        with tempfile.TemporaryDirectory(prefix='optical-memory-') as name:
            directory, token = Path(name), CancellationToken()
            supervisor, thread, outcome = self.launch(memory_script(directory), token=token, memory_bytes=192*MIB)
            try:
                tree = self.tree_ready(directory, supervisor)
                first, second = (self.wait_json(directory / filename) for filename in ('first.json', 'second.json'))
                self.assertEqual([first['pid'], second['pid']], tree['children'])
                self.assertEqual(first['committed'], 80*MIB)
                self.assertFalse(first['rejected'])
                self.assertTrue(second['rejected'], 'Two individually sub-limit allocations must hit their combined ceiling.')
                self.assertGreater(second['committed'], 0)
                self.assertLess(second['committed'], 128*MIB)
                self.assertNotEqual(second['error'], 0)
                limits = self.api.limits(supervisor.observation.job)
                usage = self.api.memory_usage(supervisor.observation.job)
                print('optical_memory_receipt='+json.dumps({
                    'currentJobBytes': usage.job_memory,
                    'peakJobBytes': usage.peak_job,
                    'extendedPeakJobBytes': limits.peak_job,
                    'peakProcessBytes': limits.peak_process,
                    'jobLimitBytes': limits.job_memory,
                    'processLimitBytes': limits.process_memory,
                    'successfulAllocationBytes': [first['committed'], second['committed']],
                    'rejected': [first['rejected'], second['rejected']],
                    'rejectionError': second['error'],
                }, separators=(',', ':')), flush=True)
                self.assertTrue(all(not self.api.exited(handle) for handle in supervisor.observation.handles),
                                'Both allocation holders and their root must still be alive during measurement.')
                self.assertGreaterEqual(self.api.active(supervisor.observation.job), 3)
                self.assertEqual(limits.job_memory, 192*MIB)
                self.assertEqual(limits.process_memory, 0)
                self.assertLessEqual(usage.job_memory, 192*MIB)
                self.assertGreaterEqual(usage.job_memory, first['committed']+second['committed'])
                self.assertGreater(usage.job_memory, 184*MIB)
                trigger = time.monotonic(); token.cancel()
                self.assert_tree_finished(supervisor, thread, outcome, trigger)
            finally:
                token.cancel()
                supervisor.shutdown(timeout_seconds=4)
                supervisor.observation.close()
                thread.join(5)

    def test_inheritable_unrelated_event_is_not_passed_with_the_three_standard_handles(self):
        event = self.api.check(self.api.k.CreateEventW(None, True, False, None))
        self.api.check(self.api.k.SetHandleInformation(event, 1, 1))
        supervisor = optical.OpticalSupervisor(memory_bytes=128*MIB)
        script = '\n'.join([
            'import ctypes as c,json,sys', 'from ctypes import wintypes as w',
            'k=c.WinDLL("kernel32",use_last_error=True)',
            'k.SetEvent.argtypes=[w.HANDLE]; k.SetEvent.restype=w.BOOL',
            f'signaled=bool(k.SetEvent({event}))',
            'data=sys.stdin.read()', 'sys.stderr.write("stderr works")',
            'print(json.dumps({"numericHandleAccepted":signaled,"input":data}))',
        ])
        try:
            self.assertTrue(self.api.flags(event) & 1)
            self.assertEqual(self.api.k.WaitForSingleObject(event, 0), 258)
            result = supervisor.run(command(script), input_text='owned stdin', timeout_seconds=5)
            self.assertEqual(result.returncode, 0)
            report = json.loads(result.stdout)
            self.assertEqual(report['input'], 'owned stdin')
            self.assertIsInstance(report['numericHandleAccepted'], bool)
            self.assertEqual(result.stderr, 'stderr works')
            # A matching numeric value can identify a different object in the child.
            # Actual inheritance would let SetEvent change this original parent event.
            self.assertEqual(self.api.k.WaitForSingleObject(event, 0), 258,
                             'The child must not signal the original unrelated parent event.')
        finally:
            supervisor.shutdown(timeout_seconds=4)
            self.api.k.CloseHandle(event)

    def test_setup_faults_and_last_moment_cancellation_fail_before_worker_executes(self):
        for operation in ('assign', 'verify', 'cancel', 'timeout'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory(prefix='optical-start-') as name:
                marker = Path(name)/'worker-ran'
                api, observations = optical._WindowsApi(), []
                original_assign, original_verify = api.k.AssignProcessToJobObject, api.verify

                def assign(job, process):
                    observations.append(OwnedObservation(self.api, job, process))
                    return 0 if operation == 'assign' else original_assign(job, process)

                def verify(job, memory, process=None):
                    original_verify(job, memory, process)
                    if process is not None and operation == 'verify':
                        raise OSError('Synthetic post-assignment verification failure.')

                def before_start():
                    if operation == 'cancel':
                        raise WorkerCancelled('Synthetic cancellation before resume.')
                    if operation == 'timeout':
                        raise subprocess.TimeoutExpired(['synthetic-owned-worker'], .01)

                try:
                    with patch.object(optical, '_WindowsApi', return_value=api), \
                            patch.object(api.k, 'AssignProcessToJobObject', side_effect=assign), \
                            patch.object(api, 'verify', side_effect=verify), \
                            patch.object(api.k, 'ResumeThread', wraps=api.k.ResumeThread) as resume:
                        expected = WorkerCancelled if operation == 'cancel' else subprocess.TimeoutExpired if operation == 'timeout' else WorkerProcessError
                        started = time.monotonic()
                        with self.assertRaises(expected):
                            optical._JobProcess(command(f'from pathlib import Path; Path({str(marker)!r}).write_text("ran")'),
                                                False, 128*MIB, before_start=before_start)
                        self.assertLess(time.monotonic()-started, 5)
                        resume.assert_not_called()
                    self.assertEqual(len(observations), 1)
                    self.assertTrue(self.api.exited(observations[0].handles[0]))
                    self.assertEqual(self.api.active(observations[0].job), 0)
                    self.assertFalse(marker.exists(), 'Unverified setup must never execute worker code.')
                finally:
                    for observation in observations:
                        observation.close()

    def test_each_fdopen_failure_closes_raw_descriptors_and_transferred_streams(self):
        for failing_wrap in range(1, 7):
            with self.subTest(failing_wrap=failing_wrap):
                api, descriptors, streams = optical._WindowsApi(), [], []
                original_pipe, original_fdopen = os.pipe, os.fdopen
                calls = 0

                def pipe():
                    pair = original_pipe()
                    descriptors.extend(pair)
                    return pair

                def fdopen(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == failing_wrap:
                        raise OSError('Synthetic descriptor wrapping failure.')
                    stream = original_fdopen(*args, **kwargs)
                    streams.append(stream)
                    return stream

                try:
                    with patch.object(optical, '_WindowsApi', return_value=api), \
                            patch.object(optical.os, 'pipe', side_effect=pipe), \
                            patch.object(optical.os, 'fdopen', side_effect=fdopen), \
                            patch.object(api.k, 'CreateProcessW', wraps=api.k.CreateProcessW) as create:
                        with self.assertRaises(WorkerProcessError):
                            optical._JobProcess(command('raise SystemExit(0)'), True, 128*MIB,
                                                before_start=lambda: None)
                        create.assert_not_called()
                    self.assertEqual(calls, failing_wrap)
                    self.assertTrue(all(stream.closed for stream in streams))
                    for descriptor in descriptors:
                        with self.assertRaises(OSError):
                            os.fstat(descriptor)
                finally:
                    for stream in streams:
                        stream.close()
                    for descriptor in descriptors:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass

    def test_unconfirmed_unassigned_start_is_adopted_until_shutdown_confirms_and_releases(self):
        api, observations = optical._WindowsApi(), []
        supervisor = optical.OpticalSupervisor(memory_bytes=128*MIB)
        original_terminate, original_wait = api.k.TerminateProcess, api.k.WaitForSingleObject
        terminated, unconfirmed = [], []

        def assign(job, process):
            observations.append(OwnedObservation(self.api, job, process))
            return 0

        def terminate(process, code):
            terminated.append(process)
            # The first cleanup really leaves this exact suspended helper alive.
            # A later shutdown retries the real API; no worker code is resumed.
            return 0 if len(terminated) == 1 else original_terminate(process, code)

        def wait(process, milliseconds):
            if milliseconds == 1000 and not unconfirmed:
                unconfirmed.append(process)
                return 258
            return original_wait(process, milliseconds)

        try:
            with patch.object(optical, '_WindowsApi', return_value=api), \
                    patch.object(api.k, 'AssignProcessToJobObject', side_effect=assign), \
                    patch.object(api.k, 'TerminateProcess', side_effect=terminate), \
                    patch.object(api.k, 'WaitForSingleObject', side_effect=wait), \
                    patch.object(api.k, 'ResumeThread', wraps=api.k.ResumeThread) as resume, \
                    patch.object(api.k, 'CloseHandle', wraps=api.k.CloseHandle) as close:
                with self.assertRaises(WorkerProcessError) as failure:
                    supervisor.run(command('raise SystemExit(99)'), timeout_seconds=5)
                owned = failure.exception.owned_process
                self.assertIn(owned, supervisor._active)
                self.assertFalse(owned._closed)
                self.assertFalse(owned._assigned)
                self.assertEqual(unconfirmed, [owned._process])
                self.assertFalse(self.api.exited(observations[0].handles[0]))
                resume.assert_not_called()
                started = time.monotonic()
                self.assertTrue(supervisor.shutdown(timeout_seconds=4))
                self.assertLess(time.monotonic()-started, 5)
                self.assertTrue(self.api.exited(observations[0].handles[0]))
                self.assertEqual(self.api.active(observations[0].job), 0)
                self.assertTrue(owned._closed)
                self.assertNotIn(owned, supervisor._active)
                self.assertEqual(terminated, [owned._process, owned._process])
                self.assertTrue(supervisor.shutdown(timeout_seconds=1))
                for handle in (owned._process, owned._job):
                    self.assertEqual(sum(call.args == (handle,) for call in close.call_args_list), 1)
        finally:
            for observation in observations:
                observation.close()
            supervisor.shutdown(timeout_seconds=4)


if __name__ == '__main__':
    unittest.main()
