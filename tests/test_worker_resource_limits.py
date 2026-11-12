import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch

from nivram import worker_resource_limits as limits


class FakeApi:
    def __init__(self, fail=None, *, mismatch=None, member=True, active=1, inheritable=False):
        self.fail = fail
        self.mismatch = mismatch
        self.member = member
        self.active = active
        self.inheritable = inheritable
        self.calls = []
        self.values = None

    def call(self, name):
        self.calls.append(name)
        if self.fail == name:
            raise OSError(5, 'synthetic OS failure')
        if self.fail == ('interrupt', name):
            raise KeyboardInterrupt()

    def create(self):
        self.call('create')
        return 'private-worker-job'

    def make_noninheritable(self, job):
        self.call('noninheritable')

    def configure(self, job, memory_bytes, cpu_ticks):
        self.call('configure')
        self.values = dict(limit_flags=0x210A, memory_bytes=memory_bytes,
                           cpu_user_time_100ns=cpu_ticks, active_process_limit=1)

    def query_limits(self, job):
        self.call('query')
        actual = dict(self.values)
        if self.mismatch:
            actual[self.mismatch] += 1
        return actual

    def handle_inheritable(self, job):
        self.call('handle')
        return self.inheritable

    def assign_current(self, job):
        self.call('assign')

    def current_is_member(self, job):
        self.call('membership')
        return self.member

    def active_processes(self, job):
        self.call('active')
        return self.active

    def close_unassigned(self, job):
        self.call('close')


class WorkerResourceLimitsTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(limits, '_CURRENT_LIMITS', None),
                        patch.object(limits, '_RETAINED_JOBS', [])]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def install(self, api, **kwargs):
        with patch.object(limits, '_Win32QuotaApi', return_value=api):
            return limits.install_worker_limits(**kwargs)

    def test_exact_install_order_and_receipt(self):
        api = FakeApi()
        receipt = self.install(api)
        self.assertEqual(api.calls, ['create', 'noninheritable', 'configure', 'query', 'handle',
                                     'assign', 'query', 'handle', 'membership', 'active'])
        self.assertEqual(receipt['limit_flags'], 8458)
        self.assertEqual(receipt['memory_bytes'], 1073741824)
        self.assertEqual(receipt['cpu_user_time_100ns'], 100000000)
        self.assertEqual(receipt['active_process_limit'], 1)
        self.assertEqual(receipt['memory_metric'], 'process_commit_bytes')
        self.assertEqual(receipt['cpu_metric'], 'process_user_time')
        self.assertTrue(receipt['verified'])
        self.assertFalse(receipt['handle_inheritable'])
        self.assertEqual(json.loads(json.dumps(receipt)), receipt)
        self.assertEqual(len(limits._RETAINED_JOBS), 1)

    def test_same_configuration_revalidates_without_resetting_cpu_budget(self):
        api = FakeApi()
        first = self.install(api, memory_bytes=64 * 1024 ** 2, cpu_seconds=0.5)
        api.calls.clear()
        self.assertEqual(self.install(api, memory_bytes=64 * 1024 ** 2, cpu_seconds=0.5), first)
        self.assertEqual(api.calls, ['query', 'handle', 'membership', 'active'])

    def test_conflicting_configuration_never_changes_existing_job(self):
        api = FakeApi()
        self.install(api)
        api.calls.clear()
        with self.assertRaises(limits.ResourceLimitsUnavailable):
            self.install(api, cpu_seconds=1)
        self.assertEqual(api.calls, [])

    def test_invalid_or_unbounded_configuration_rejected_before_os_calls(self):
        api = FakeApi()
        for kwargs in ({'memory_bytes': 0}, {'memory_bytes': -1}, {'memory_bytes': True},
                       {'memory_bytes': 1.5}, {'memory_bytes': 2 ** 40},
                       {'cpu_seconds': 0}, {'cpu_seconds': True}, {'cpu_seconds': float('inf')},
                       {'cpu_seconds': float('nan')}, {'cpu_seconds': 11},
                       {'cpu_seconds': 10 ** 1000}, {'cpu_seconds': 1e-8}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.install(api, **kwargs)
        self.assertEqual(api.calls, [])

    def test_cpu_quantization_never_grants_more_than_request(self):
        api = FakeApi()
        receipt = self.install(api, cpu_seconds=0.123456789)
        self.assertEqual(receipt['cpu_user_time_100ns'], 1234567)
        self.assertLessEqual(receipt['cpu_seconds'], 0.123456789)

    def test_unsupported_os_fails_closed(self):
        with patch.object(limits.os, 'name', 'posix'):
            with self.assertRaises(limits.ResourceLimitsUnavailable):
                limits.install_worker_limits()
        self.assertIsNone(limits._CURRENT_LIMITS)

    def test_preassignment_errors_close_only_unassigned_handle(self):
        for operation in ('create', 'noninheritable', 'configure', 'query', 'handle'):
            api = FakeApi(fail=operation)
            with self.subTest(operation=operation), self.assertRaises(limits.ResourceLimitsUnavailable):
                self.install(api)
            self.assertNotIn('assign', api.calls)
            self.assertEqual(api.calls.count('close'), int(operation != 'create'))
            self.assertIsNone(limits._CURRENT_LIMITS)

    def test_each_limit_mismatch_prevents_assignment(self):
        for key in ('limit_flags', 'memory_bytes', 'cpu_user_time_100ns', 'active_process_limit'):
            api = FakeApi(mismatch=key)
            with self.subTest(key=key), self.assertRaises(limits.ResourceLimitsUnavailable):
                self.install(api)
            self.assertNotIn('assign', api.calls)
            self.assertEqual(api.calls[-1], 'close')

    def test_inheritable_handle_prevents_assignment(self):
        api = FakeApi(inheritable=True)
        with self.assertRaises(limits.ResourceLimitsUnavailable):
            self.install(api)
        self.assertNotIn('assign', api.calls)
        self.assertIn('close', api.calls)

    def test_postassignment_failure_retains_handle_and_poisoned_state(self):
        for operation in ('assign', 'membership', 'active'):
            api = FakeApi(fail=operation)
            with patch.object(limits, '_CURRENT_LIMITS', None), patch.object(limits, '_RETAINED_JOBS', []):
                with self.subTest(operation=operation), self.assertRaises(limits.ResourceLimitsUnavailable):
                    self.install(api)
                self.assertNotIn('close', api.calls)
                self.assertEqual(len(limits._RETAINED_JOBS), 1)
                previous = list(api.calls)
                with self.assertRaises(limits.ResourceLimitsUnavailable):
                    self.install(api)
                self.assertEqual(api.calls, previous)

    def test_async_assignment_interruption_never_closes_possible_current_job(self):
        api = FakeApi(fail=('interrupt', 'assign'))
        with self.assertRaises(KeyboardInterrupt):
            self.install(api)
        self.assertNotIn('close', api.calls)
        self.assertEqual(len(limits._RETAINED_JOBS), 1)
        with self.assertRaises(limits.ResourceLimitsUnavailable):
            self.install(api)

    def test_query_failure_after_assignment_never_closes_current_job(self):
        api = FakeApi()
        assign = api.assign_current
        def assigned_then_unqueryable(job):
            assign(job)
            api.fail = 'query'
        api.assign_current = assigned_then_unqueryable
        with self.assertRaises(limits.ResourceLimitsUnavailable):
            self.install(api)
        self.assertNotIn('close', api.calls)

    def test_failed_revalidation_cannot_be_ignored_by_trying_again(self):
        api = FakeApi()
        self.install(api)
        api.mismatch = 'memory_bytes'
        with self.assertRaises(limits.ResourceLimitsUnavailable):
            self.install(api)
        api.mismatch = None
        with self.assertRaises(limits.ResourceLimitsUnavailable):
            self.install(api)
        self.assertNotIn('close', api.calls)

    def test_membership_and_active_count_are_required(self):
        for kwargs in ({'member': False}, {'active': 0}, {'active': 2}):
            with patch.object(limits, '_CURRENT_LIMITS', None), patch.object(limits, '_RETAINED_JOBS', []):
                api = FakeApi(**kwargs)
                with self.subTest(kwargs=kwargs), self.assertRaises(limits.ResourceLimitsUnavailable):
                    self.install(api)
                self.assertNotIn('close', api.calls)

    def test_adapter_creates_unnamed_job_and_sets_only_specific_limits(self):
        api = object.__new__(limits._Win32QuotaApi)
        api.k = Mock()
        api.k.CreateJobObjectW.return_value = 123
        self.assertEqual(api.create(), 123)
        api.k.CreateJobObjectW.assert_called_once_with(None, None)
        api.make_noninheritable(123)
        api.k.SetHandleInformation.assert_called_once_with(123, 1, 0)
        captured = []
        def set_limits(job, info_class, pointer, size):
            native = ctypes.cast(pointer, ctypes.POINTER(limits._ExtendedLimit)).contents
            captured.append((job, info_class, size, native.BasicLimitInformation.LimitFlags,
                             native.BasicLimitInformation.PerProcessUserTimeLimit,
                             native.BasicLimitInformation.ActiveProcessLimit,
                             native.ProcessMemoryLimit, native.JobMemoryLimit))
            return 1
        api.k.SetInformationJobObject.side_effect = set_limits
        api.configure(123, 67108864, 5000000)
        self.assertEqual(captured, [(123, 9, ctypes.sizeof(limits._ExtendedLimit),
                                     8458, 5000000, 1, 67108864, 0)])

    def test_adapter_reads_native_limits_handle_flags_and_current_membership(self):
        api = object.__new__(limits._Win32QuotaApi)
        api.k = Mock()
        api.k.GetCurrentProcess.return_value = 'current-only'
        def query(job, info_class, pointer, size, returned):
            self.assertEqual(job, 'exact-private-job')
            if info_class == 9:
                native = ctypes.cast(pointer, ctypes.POINTER(limits._ExtendedLimit)).contents
                native.BasicLimitInformation.LimitFlags = 8458
                native.BasicLimitInformation.PerProcessUserTimeLimit = 5000000
                native.BasicLimitInformation.ActiveProcessLimit = 1
                native.ProcessMemoryLimit = 67108864
            else:
                self.assertEqual(info_class, 1)
                ctypes.cast(pointer, ctypes.POINTER(limits._Accounting)).contents.ActiveProcesses = 1
            return 1
        def member(process, job, pointer):
            self.assertEqual((process, job), ('current-only', 'exact-private-job'))
            ctypes.cast(pointer, ctypes.POINTER(limits.w.BOOL)).contents.value = 1
            return 1
        api.k.QueryInformationJobObject.side_effect = query
        api.k.IsProcessInJob.side_effect = member
        self.assertEqual(api.query_limits('exact-private-job'), {
            'limit_flags': 8458, 'memory_bytes': 67108864,
            'cpu_user_time_100ns': 5000000, 'active_process_limit': 1})
        self.assertTrue(api.current_is_member('exact-private-job'))
        self.assertEqual(api.active_processes('exact-private-job'), 1)
        self.assertFalse(api.handle_inheritable('exact-private-job'))


@unittest.skipUnless(os.name == 'nt', 'Worker quotas require Windows job objects.')
class WindowsWorkerResourceLimitsTests(unittest.TestCase):
    def test_low_limits_enforced_inside_existing_host_job_and_controls_survive(self):
        # Only disposable helpers install real quotas. This test runner never
        # assigns itself. Every cleanup target is a handle returned by Popen.
        from nivram.native_dialog_fixture import _source_interpreter
        # A venv redirector's Popen handle does not identify the interpreter
        # that installs the quota. Launch the actual current host explicitly.
        interpreter = _source_interpreter()
        root = str(Path(__file__).resolve().parents[1])
        command = lambda script: [interpreter, '-I', '-S', '-B', '-u', '-c', script]
        worker_base = '\n'.join([
            'import ctypes as c,json,sys',
            'sys.path.insert(0,' + repr(root) + ')',
            'worker_executable=' + repr(interpreter),
            'identity_api=c.WinDLL("kernel32",use_last_error=True)',
            'identity_api.GetCurrentProcessId.argtypes=[]; identity_api.GetCurrentProcessId.restype=c.c_ulong',
            'worker_self_identity=int(identity_api.GetCurrentProcessId())',
            'from nivram.worker_resource_limits import install_worker_limits',
        ]) + '\n'
        memory = worker_base + '\n'.join([
            'receipt=install_worker_limits(memory_bytes=64*1024**2,cpu_seconds=2)',
            'k=c.WinDLL("kernel32",use_last_error=True)',
            'k.VirtualAlloc.argtypes=[c.c_void_p,c.c_size_t,c.c_ulong,c.c_ulong]; k.VirtualAlloc.restype=c.c_void_p',
            'k.VirtualFree.argtypes=[c.c_void_p,c.c_size_t,c.c_ulong]; k.VirtualFree.restype=c.c_int',
            'blocks=[]; rejected=False; error=0',
            'try:',
            ' for _ in range(16):',
            '  address=k.VirtualAlloc(None,4*1024**2,0x3000,4)',
            '  if not address:',
            '   rejected=True; error=c.get_last_error(); break',
            '  blocks.append(address)',
            'finally:',
            ' for address in blocks: assert k.VirtualFree(address,0,0x8000)',
            'assert rejected and 0<len(blocks)<16, (rejected,len(blocks),error)',
            'print(json.dumps({"workerSelfIdentity":worker_self_identity,"receipt":receipt,"allocationRejected":rejected,"committedTestBytes":len(blocks)*4*1024**2,"allocationError":error}),flush=True)',
        ])
        cpu = worker_base + '\n'.join([
            'import time',
            'receipt=install_worker_limits(memory_bytes=64*1024**2,cpu_seconds=0.5)',
            'print(json.dumps({"workerSelfIdentity":worker_self_identity,"receipt":receipt,"burnStarted":True}),flush=True)',
            # Windows checks user CPU periodically. Duty cycling lets that
            # check occur without burning a full core throughout the interval.
            'while True:',
            ' end=time.perf_counter()+0.01',
            ' while time.perf_counter()<end: pass',
            ' time.sleep(0.04)',
        ])
        child_limit = worker_base + '\n'.join([
            'receipt=install_worker_limits(memory_bytes=64*1024**2,cpu_seconds=2)',
            'import subprocess',
            'blocked=False',
            'try:',
            ' child=subprocess.Popen([worker_executable,"-I","-S","-B","-c","raise SystemExit(0)"],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)',
            'except OSError:',
            ' blocked=True',
            'else:',
            ' try: blocked=child.wait(timeout=3)!=0',
            ' finally:',
            '  if child.poll() is None: child.kill(); child.wait(timeout=3)',
            'assert blocked',
            'print(json.dumps({"workerSelfIdentity":worker_self_identity,"receipt":receipt,"childCreationBlocked":blocked}),flush=True)',
        ])
        supervisor = '\n'.join([
            'import ctypes as c,json,subprocess,sys,time',
            'from ctypes import wintypes as w',
            'sys.path.insert(0,' + repr(root) + ')',
            'worker_executable=' + repr(interpreter),
            'from nivram.owned_process_job import OwnedProcessJob',
            'job=OwnedProcessJob.for_current_process()',
            'k=c.WinDLL("kernel32",use_last_error=True)',
            'k.GetProcessId.argtypes=[w.HANDLE]; k.GetProcessId.restype=w.DWORD',
            'k.GetProcessTimes.argtypes=[w.HANDLE,c.POINTER(w.FILETIME),c.POINTER(w.FILETIME),c.POINTER(w.FILETIME),c.POINTER(w.FILETIME)]; k.GetProcessTimes.restype=w.BOOL',
            'before=job.snapshot(); results=[]',
            'control=subprocess.Popen([worker_executable,"-I","-S","-B","-c","import time; time.sleep(30)"],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)',
            'try:',
            ' for mode,script in ' + repr([('memory', memory), ('cpu', cpu), ('children', child_limit)]) + ':',
            '  start=time.monotonic()',
            '  process=subprocess.Popen([worker_executable,"-I","-S","-B","-u","-c",script],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=0x08000000)',
            '  try: output,errors=process.communicate(timeout=12)',
            '  except subprocess.TimeoutExpired:',
            '   process.kill(); process.communicate(timeout=3); raise',
            '  created,exited,kernel,user=w.FILETIME(),w.FILETIME(),w.FILETIME(),w.FILETIME()',
            '  assert k.GetProcessTimes(int(process._handle),c.byref(created),c.byref(exited),c.byref(kernel),c.byref(user))',
            '  user_seconds=((user.dwHighDateTime<<32)|user.dwLowDateTime)/10000000',
            '  worker_output=json.loads(output); worker_identity=worker_output.pop("workerSelfIdentity",None)',
            '  handle_identity=int(k.GetProcessId(int(process._handle)))',
            '  assert type(worker_identity) is int and 0<worker_identity<2**32 and handle_identity>0',
            '  same_worker_handle=handle_identity==worker_identity',
            '  results.append({"mode":mode,"exitCode":process.returncode,"seconds":time.monotonic()-start,"userCpuSeconds":user_seconds,"sameWorkerHandle":same_worker_handle,"output":worker_output,"errors":errors})',
            '  assert control.poll() is None',
            'finally:',
            ' control.kill(); control.wait(timeout=3)',
            'deadline=time.monotonic()+3; after=job.snapshot()',
            'while not after["confirmed"] and time.monotonic()<deadline:',
            ' time.sleep(0.02); after=job.snapshot()',
            'print(json.dumps({"before":before,"results":results,"after":after,"siblingControlSurvived":True}),flush=True)',
        ])
        control = subprocess.Popen(command('import time; time.sleep(40)'), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        process = None
        try:
            start = time.monotonic()
            process = subprocess.Popen(command(supervisor), stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            output, errors = process.communicate(timeout=28)
            self.assertEqual(process.returncode, 0, errors)
            report = json.loads(output)
            self.assertTrue(report['before']['confirmed'], report)
            self.assertTrue(report['after']['confirmed'], report)
            self.assertTrue(report['siblingControlSurvived'], report)
            self.assertIsNone(control.poll(), 'The unrelated control must survive all limit violations.')
            self.assertLess(time.monotonic() - start, 28)
            rows = {row['mode']: row for row in report['results']}
            self.assertTrue(all(row['sameWorkerHandle'] is True for row in rows.values()), report)
            self.assertEqual(rows['memory']['exitCode'], 0, report)
            self.assertTrue(rows['memory']['output']['allocationRejected'])
            self.assertLess(rows['memory']['output']['committedTestBytes'], 64 * 1024 ** 2)
            self.assertEqual(rows['cpu']['exitCode'], 0xC0000044, report)  # STATUS_QUOTA_EXCEEDED
            self.assertTrue(rows['cpu']['output']['burnStarted'])
            self.assertGreaterEqual(rows['cpu']['userCpuSeconds'], 0.5, report)
            self.assertLess(rows['cpu']['seconds'], 12)
            self.assertEqual(rows['children']['exitCode'], 0, report)
            self.assertTrue(rows['children']['output']['childCreationBlocked'])
            for row in rows.values():
                self.assertEqual(row['errors'], '', report)
                self.assertTrue(row['output']['receipt']['verified'])
            # Kept in test logs as execution evidence, without unrelated PIDs.
            print('WORKER_RESOURCE_REAL_EVIDENCE=' + json.dumps(report, sort_keys=True), flush=True)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()  # Its private host job cleans its descendants.
                process.communicate(timeout=5)
            if control.poll() is None:
                control.kill()
            control.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
