import importlib
import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch


def owned_module():
    name = 'nivram.owned_process_job'
    if importlib.util.find_spec(name) is None:
        raise AssertionError('The app needs a private owned-process job before it creates children.')
    module = importlib.import_module(name)
    if not hasattr(module, 'OwnedProcessJob'):
        raise AssertionError('The private job must expose the bounded OwnedProcessJob API.')
    return module


class FakeApi:
    def __init__(self, members=(100, 200), *, open_error=False, member=True,
                 query_error=False, terminate_error=False, stay_alive=False):
        self.members = list(members)
        self.open_error = open_error
        self.member = member
        self.query_error = query_error
        self.terminate_error = terminate_error
        self.stay_alive = stay_alive
        self.calls = []

    def query(self, job, deadline=None):
        self.calls.append(('query', job))
        if self.query_error:
            raise OSError(5, 'synthetic query failure')
        return tuple(self.members), len(self.members)

    def open_process(self, pid):
        self.calls.append(('open', pid))
        if self.open_error:
            raise OSError(5, 'synthetic open failure')
        return 'handle-' + str(pid)

    def is_member(self, job, handle):
        self.calls.append(('member', job, handle))
        return self.member

    def has_exited(self, handle):
        self.calls.append(('exited', handle))
        return False

    def terminate(self, handle):
        self.calls.append(('terminate', handle))
        if self.terminate_error:
            raise OSError(5, 'synthetic terminate failure')
        if not self.stay_alive:
            self.members.remove(int(handle.split('-')[1]))

    def close_process(self, handle):
        self.calls.append(('close_process', handle))


class OwnedProcessJobTests(unittest.TestCase):
    def make_job(self, api):
        return owned_module().OwnedProcessJob(api, 'private-job', 100)

    def test_required_api_exists_without_assigning_test_host(self):
        cls = owned_module().OwnedProcessJob
        for name in ('for_current_process', 'snapshot', 'terminate_children'):
            self.assertTrue(callable(getattr(cls, name, None)), name)

    def test_graceful_empty_job_never_opens_or_terminates_host(self):
        api = FakeApi((100,))
        result = self.make_job(api).terminate_children(0.2)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(result['remaining_children'], 0)
        self.assertEqual(result['forced_count'], 0)
        self.assertFalse(any(row[0] == 'open' for row in api.calls))

    def test_termination_uses_verified_stable_handle_and_never_host(self):
        api = FakeApi()
        result = self.make_job(api).terminate_children(0.2)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['forced_count'], 1)
        self.assertEqual(result['remaining_children'], 0)
        relevant = [row for row in api.calls if row[0] != 'query']
        self.assertEqual(relevant, [('open', 200), ('member', 'private-job', 'handle-200'),
                                   ('exited', 'handle-200'), ('terminate', 'handle-200'),
                                   ('close_process', 'handle-200')])

    def test_recycled_pid_outside_exact_job_is_not_terminated(self):
        api = FakeApi(member=False)
        result = self.make_job(api).terminate_children(0.03)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['forced_count'], 0)
        self.assertFalse(any(row[0] == 'terminate' for row in api.calls))
        self.assertIn(('close_process', 'handle-200'), api.calls)

    def test_open_failure_stays_unconfirmed_without_alternative_target(self):
        api = FakeApi(open_error=True)
        result = self.make_job(api).terminate_children(0.03)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['remaining_children'], 1)
        self.assertEqual(result['forced_count'], 0)
        self.assertFalse(any(row[0] == 'terminate' for row in api.calls))

    def test_query_failure_never_opens_process(self):
        api = FakeApi(query_error=True)
        job = self.make_job(api)
        for result in (job.snapshot(), job.terminate_children(0.03)):
            self.assertFalse(result['confirmed'])
            self.assertIsNone(result['remaining_children'])
        self.assertFalse(any(row[0] == 'open' for row in api.calls))

    def test_failed_termination_closes_only_child_handle(self):
        api = FakeApi(terminate_error=True)
        result = self.make_job(api).terminate_children(0.03)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['forced_count'], 0)
        self.assertIn(('close_process', 'handle-200'), api.calls)

    def test_successful_terminate_is_not_mistaken_for_observed_exit(self):
        api = FakeApi(stay_alive=True)
        started = time.monotonic()
        result = self.make_job(api).terminate_children(0.04)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['forced_count'], 1)
        self.assertEqual(result['remaining_children'], 1)
        self.assertEqual(sum(row[0] == 'terminate' for row in api.calls), 1)

    def test_invalid_budget_does_not_inspect_or_signal_processes(self):
        api = FakeApi()
        job = self.make_job(api)
        for value in (True, False, 0, -1, 5.01, float('inf'), float('nan'), '1', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                job.terminate_children(value)
        self.assertEqual(api.calls, [])

    def test_missing_host_membership_is_unconfirmed(self):
        api = FakeApi((200,))
        result = self.make_job(api).terminate_children(0.03)
        self.assertFalse(result['confirmed'])
        self.assertFalse(any(row[0] == 'open' for row in api.calls))

    def test_factory_assignment_failure_closes_only_unassigned_job(self):
        module = owned_module()
        from unittest.mock import Mock
        api = Mock()
        api.create.return_value = 'new-job'
        api.assign_current.side_effect = OSError(5, 'synthetic assignment failure')
        with patch.object(module, '_CURRENT_JOB', None), \
                patch.object(module, '_Win32JobApi', return_value=api):
            with self.assertRaises(OSError):
                module.OwnedProcessJob.for_current_process()
        api.close_unassigned_job.assert_called_once_with('new-job')

    def test_factory_retains_assigned_job_and_reuses_it_without_closing(self):
        module = owned_module()
        from unittest.mock import Mock
        api = Mock()
        api.create.return_value = 'new-job'
        retained = []
        with patch.object(module, '_CURRENT_JOB', None), \
                patch.object(module, '_RETAINED_JOBS', retained), \
                patch.object(module, '_Win32JobApi', return_value=api):
            first = module.OwnedProcessJob.for_current_process()
            self.assertIs(first, module.OwnedProcessJob.for_current_process())
            self.assertEqual(retained, [first])
        api.assign_current.assert_called_once_with('new-job')
        api.close_unassigned_job.assert_not_called()

    def test_process_list_growth_is_bounded_and_never_accepts_overflow(self):
        module = owned_module()
        api = object.__new__(module._Win32JobApi)
        from unittest.mock import Mock
        api.k = Mock()
        capacities = []

        def overflowing_query(job, info, buffer, size, returned):
            self.assertEqual(info, 3)
            capacity = (size - module.c.sizeof(module._ProcessListHeader)) // module.c.sizeof(module.c.c_size_t)
            capacities.append(capacity)
            header = module._ProcessListHeader.from_buffer(buffer)
            header.assigned = capacity + 1
            header.listed = capacity
            return 0

        api.k.QueryInformationJobObject.side_effect = overflowing_query
        with patch.object(module.c, 'get_last_error', return_value=234, create=True):
            with self.assertRaises(OSError):
                api.query('private-job')
        self.assertLessEqual(len(capacities), module._MAX_QUERY_ATTEMPTS)
        self.assertLessEqual(max(capacities), module._MAX_PROCESSES)

    def test_process_list_error_does_not_retry_as_resize(self):
        module = owned_module()
        api = object.__new__(module._Win32JobApi)
        from unittest.mock import Mock
        api.k = Mock()
        api.k.QueryInformationJobObject.return_value = 0
        with patch.object(module.c, 'get_last_error', return_value=5, create=True), \
                patch.object(module.c, 'WinError', return_value=OSError(5, 'synthetic failure'), create=True):
            with self.assertRaises(OSError):
                api.query('private-job')
        self.assertEqual(api.k.QueryInformationJobObject.call_count, 1)


@unittest.skipUnless(os.name == 'nt', 'Windows job objects are Windows-specific.')
class WindowsOwnedProcessJobTests(unittest.TestCase):
    def test_abrupt_supervisor_exit_cleans_child_without_inheriting_job_handle(self):
        module = owned_module()
        root = str(Path(__file__).resolve().parents[1])
        script = '\n'.join([
            'import os,subprocess,sys',
            'sys.path.insert(0,' + repr(root) + ')',
            'from nivram.owned_process_job import OwnedProcessJob',
            'job=OwnedProcessJob.for_current_process()',
            'child=subprocess.Popen([sys.executable,"-I","-S","-B","-u","-c","import time; time.sleep(60)"],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=False,creationflags=0x08000000)',
            'print(child.pid,flush=True)',
            'sys.stdin.buffer.read(1)',
            'os._exit(0)',
        ])
        process = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-u', '-c', script],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        messages = queue.Queue()
        reader = threading.Thread(target=lambda: messages.put(process.stdout.readline()), daemon=True)
        reader.start()
        api = module._Win32JobApi()  # Loading APIs does not assign the test host.
        handle = None
        try:
            child_pid = int(messages.get(timeout=5))
            handle = api.open_process(child_pid)
            self.assertFalse(api.has_exited(handle))
            output, errors = process.communicate(input='x', timeout=5)
            self.assertEqual(process.returncode, 0, errors)
            deadline = time.monotonic() + 3
            while not api.has_exited(handle) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(api.has_exited(handle), 'OS host teardown must close the private job and stop its child.')
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
            reader.join(timeout=2)
            if handle is not None:
                api.close_process(handle)

    def test_real_grandchild_cleanup_preserves_unrelated_control(self):
        # The test host deliberately never calls for_current_process(). Only the
        # new synthetic supervisor is assigned, before it creates descendants.
        owned_module()
        root = str(Path(__file__).resolve().parents[1])
        leaf = 'import time; time.sleep(60)'
        branch = (
            'import subprocess,sys,time; '
            'child=subprocess.Popen([sys.executable,"-I","-S","-B","-u","-c",' + repr(leaf) + '],'
            'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000); '
            'print(child.pid,flush=True); time.sleep(60)'
        )
        supervisor = '\n'.join([
            'import json,subprocess,sys',
            'sys.path.insert(0,' + repr(root) + ')',
            'from nivram.owned_process_job import OwnedProcessJob',
            'job=OwnedProcessJob.for_current_process()',
            'before=job.snapshot()',
            'child=subprocess.Popen([sys.executable,"-I","-S","-B","-u","-c",' + repr(branch) + '],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,creationflags=0x08000000)',
            'grandchild=int(child.stdout.readline())',
            'during=job.snapshot()',
            'captured,_=job._api.query(job._job)',
            'result=job.terminate_children(2.0)',
            'child.wait(timeout=3)',
            'child.stdout.close()',
            'print(json.dumps({"before":before,"during":during,"result":result,"after":job.snapshot(),"explicitChildrenCaptured":child.pid in captured and grandchild in captured}),flush=True)',
        ])
        command = lambda script: [sys.executable, '-I', '-S', '-B', '-u', '-c', script]
        control = subprocess.Popen(command(leaf), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        process = None
        try:
            process = subprocess.Popen(command(supervisor), stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            output, errors = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, errors)
            report = json.loads(output)
            self.assertEqual(report['before']['remaining_children'], 0)
            self.assertTrue(report['explicitChildrenCaptured'], report)
            # Windows/runtime helpers may also belong to the private job. The
            # two explicit descendants, not an assumed total, establish capture.
            self.assertGreaterEqual(report['during']['remaining_children'], 2, report)
            self.assertTrue(report['result']['confirmed'], report)
            self.assertGreaterEqual(report['result']['forced_count'], 2, report)
            self.assertEqual(report['result']['remaining_children'], 0)
            self.assertEqual(report['after']['remaining_children'], 0)
            self.assertIsNone(control.poll(), 'The unrelated control must remain running.')
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()  # Only our Popen handle; job close owns descendants.
                process.communicate(timeout=5)
            if control.poll() is None:
                control.kill()
            control.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
