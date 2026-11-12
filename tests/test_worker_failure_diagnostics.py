"""Failure provenance remains bounded and cannot certify cleanup or selection."""
import io
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from nivram import acceptance_batch_png, native_dialog_fixture, worker_processes as workers

PRIVATE='PRIVATE_COMMAND_PAYLOAD_CAPTION_PATH_NONCE'

def cleanup(**changes):
    return dict({'stop_confirmed':False,'stdin_done':True,'stdout_done':True,
                 'stderr_done':True,'threads_exited':True},**changes)

def failure(**changes):
    return dict({'category':'cleanup_incomplete','primary_kind':'timeout',
                 'primary_category':None,'cleanup':cleanup()},**changes)

def boundary():
    return {'stage':'supervise','child_created':True,'child_running':True,
            'returncode':None,'stdin_done':True,'stdout_done':False,'stderr_done':False}

class FakeProcess:
    def __init__(self, *, finished=False, bad_read=False):
        class BadRead:
            def read(self,size):
                raise OSError(PRIVATE)
            def close(self):
                pass
        self.stdin=None
        self.stdout=BadRead() if bad_read else io.BytesIO()
        self.stderr=io.BytesIO()
        self.finished=finished
    def poll(self):
        return 0 if self.finished else None
    def terminate(self):
        self.finished=True
    kill=terminate

class WorkerFailureDiagnosticTests(unittest.TestCase):
    def sanitize(self,value):
        function=getattr(workers,'worker_failure_diagnostic',None)
        self.assertTrue(callable(function),'Missing bounded worker failure sanitizer')
        return function(value)

    def test_timeout_then_cleanup_failure_keeps_original_boundary_and_failure(self):
        process=FakeProcess()
        supervisor=workers.WorkerSupervisor(max_workers=1)
        ticks=iter([0.,0.,2.])
        def clock():
            return next(ticks,2.)
        def stop(*args):
            process.finished=True
            return False
        try:
            with patch.object(workers.subprocess,'Popen',return_value=process), \
                 patch.object(workers.time,'monotonic',side_effect=clock), \
                 patch.object(workers,'_stop_owned',side_effect=stop):
                with self.assertRaises(workers.WorkerProcessError) as caught:
                    supervisor.run([PRIVATE],timeout_seconds=1)
            error=caught.exception
            metadata=getattr(error,'worker_failure',None)
            self.assertIs(type(metadata),dict,'Cleanup failure lost primary error metadata')
            self.assertEqual(metadata['category'],'cleanup_incomplete')
            self.assertEqual(metadata['primary_kind'],'timeout')
            self.assertIsNone(metadata['primary_category'])
            self.assertFalse(metadata['cleanup']['stop_confirmed'])
            expected=workers.timeout_boundary_diagnostic(error.__context__.timeout_boundary)
            self.assertEqual(workers.timeout_boundary_diagnostic(error.timeout_boundary),expected)
            self.assertTrue(expected['child_running'])
            self.assertNotIn(PRIVATE,json.dumps(metadata))
            self.assertEqual(supervisor.active_count,0)
        finally:
            self.assertTrue(supervisor.shutdown())

    def test_io_failure_is_distinct_and_retains_successful_cleanup_facts(self):
        process=FakeProcess(bad_read=True)
        supervisor=workers.WorkerSupervisor(max_workers=1)
        def stop(*args):
            process.finished=True
            return True
        try:
            with patch.object(workers.subprocess,'Popen',return_value=process), \
                 patch.object(workers,'_stop_owned',side_effect=stop):
                with self.assertRaises(workers.WorkerProcessError) as caught:
                    supervisor.run([PRIVATE],timeout_seconds=2)
            metadata=getattr(caught.exception,'worker_failure',None)
            self.assertIs(type(metadata),dict,'I/O branch is not distinguishable')
            self.assertEqual(metadata['category'],'io')
            self.assertIsNone(metadata['primary_kind'])
            self.assertTrue(metadata['cleanup']['stop_confirmed'])
            self.assertTrue(metadata['cleanup']['threads_exited'])
            self.assertNotIn(PRIVATE,json.dumps(metadata))
        finally:
            self.assertTrue(supervisor.shutdown())

    def test_cleanup_failure_after_normal_result_does_not_invent_primary_error(self):
        process=FakeProcess(finished=True)
        supervisor=workers.WorkerSupervisor()
        try:
            with patch.object(workers.subprocess,'Popen',return_value=process), \
                 patch.object(workers,'_stop_owned',return_value=False):
                with self.assertRaises(workers.WorkerProcessError) as caught:
                    supervisor.run([PRIVATE],timeout_seconds=2)
            metadata=getattr(caught.exception,'worker_failure',None)
            self.assertIs(type(metadata),dict)
            self.assertEqual(metadata['category'],'cleanup_incomplete')
            self.assertIsNone(metadata['primary_kind'])
            self.assertIsNone(metadata['primary_category'])
            self.assertFalse(workers.timeout_boundary_diagnostic(getattr(caught.exception,'timeout_boundary',None))['available'])
        finally:
            self.assertTrue(supervisor.shutdown())

    def test_pre_spawn_input_and_capacity_branches_are_distinct(self):
        supervisor=workers.WorkerSupervisor(max_workers=1)
        with patch.object(workers.subprocess,'Popen') as spawn:
            with self.assertRaises(workers.WorkerProcessError) as caught:
                supervisor.run([PRIVATE],input_text=PRIVATE,max_input_bytes=1,timeout_seconds=1)
            self.assertEqual(getattr(caught.exception,'worker_failure',{}).get('category'),'input_limit')
            process=FakeProcess()
            supervisor._active.add(process)
            try:
                with self.assertRaises(workers.WorkerProcessError) as caught:
                    supervisor.run([PRIVATE],timeout_seconds=1)
                self.assertEqual(getattr(caught.exception,'worker_failure',{}).get('category'),'capacity')
            finally:
                supervisor._active.clear()
            spawn.assert_not_called()
        self.assertTrue(supervisor.shutdown())

    def test_valid_metadata_is_deep_copied_and_bounded(self):
        raw=failure()
        observed=self.sanitize(raw)
        self.assertTrue(observed['available'])
        self.assertTrue(observed['cleanup']['available'])
        raw['cleanup']['stdin_done']=False
        self.assertTrue(observed['cleanup']['stdin_done'])
        self.assertEqual(self.sanitize(observed),observed)
        self.assertNotIn(PRIVATE,json.dumps(observed))

    def test_exact_exception_with_hostile_attribute_dict_never_executes_get(self):
        reads=[]
        class HostileAttributes(dict):
            def get(self,*args):
                reads.append('get')
                raise AssertionError(PRIVATE)
        for kind in (workers.WorkerProcessError, subprocess.TimeoutExpired):
            error=kind(PRIVATE) if kind is workers.WorkerProcessError else kind([PRIVATE],1)
            error.__dict__=HostileAttributes(worker_failure=failure(),timeout_boundary=boundary())
            with self.subTest(kind=kind.__name__):
                receipt=self.fixture(error)
                self.assertFalse(receipt['selected'])
                observed=acceptance_batch_png.dialog_diagnostic(receipt)
                self.assertFalse(observed['worker_failure']['available'])
                self.assertFalse(observed['timeout_boundary']['available'])
                factory=getattr(workers,'_process_error',None)
                self.assertTrue(callable(factory))
                wrapped=factory('bounded cleanup failure','cleanup_incomplete',primary=error,cleanup=cleanup())
                metadata=self.sanitize(wrapped.worker_failure)
                self.assertTrue(metadata['available'])
                self.assertIsNone(metadata['primary_category'])
                self.assertNotIn(PRIVATE,json.dumps(metadata))
        self.assertEqual(reads,[])

    def test_impossible_cleanup_observations_cannot_be_attached_to_other_branches(self):
        for category in ('input_limit','capacity'):
            observed=self.sanitize(failure(category=category,primary_kind=None,
                cleanup=cleanup(stop_confirmed=True)))
            self.assertFalse(observed['available'])

    def test_custom_keys_in_plain_exception_dict_never_execute_equality(self):
        reads=[]
        for field in ('worker_failure','timeout_boundary'):
            class HostileKey:
                def __hash__(self): return hash(field)
                def __eq__(self,other):
                    reads.append('equality')
                    raise AssertionError(PRIVATE)
            for kind in (workers.WorkerProcessError,subprocess.TimeoutExpired):
                error=kind(PRIVATE) if kind is workers.WorkerProcessError else kind([PRIVATE],1)
                error.__dict__={HostileKey():PRIVATE}
                observed=acceptance_batch_png.dialog_diagnostic(self.fixture(error))
                self.assertFalse(observed['worker_failure']['available'])
                self.assertFalse(observed['timeout_boundary']['available'])
                wrapped=workers._process_error('bounded','cleanup_incomplete',primary=error,cleanup=cleanup())
                self.assertTrue(self.sanitize(wrapped.worker_failure)['available'])
        self.assertEqual(reads,[])
        for observations in (cleanup(),cleanup(stop_confirmed=True,threads_exited=False)):
            observed=self.sanitize(failure(category='io',primary_kind=None,cleanup=observations))
            self.assertFalse(observed['available'])

    def test_exception_class_equality_hook_is_never_invoked(self):
        reads=[]
        class HostileMeta(type):
            def __eq__(cls,other):
                reads.append('class equality')
                raise AssertionError(PRIVATE)
        class HostileTimeout(subprocess.TimeoutExpired,metaclass=HostileMeta):
            pass
        error=HostileTimeout([PRIVATE],1)
        error.timeout_boundary=boundary()
        observed=acceptance_batch_png.dialog_diagnostic(self.fixture(error))
        self.assertFalse(observed['selected'])
        self.assertFalse(observed['timeout_boundary']['available'])
        self.assertEqual(observed['error_kind'],'timeout')
        self.assertEqual(reads,[])

    def test_extra_private_keys_invalid_types_and_incoherence_reject(self):
        invalid=[None,[],PRIVATE,{**failure(),'output':PRIVATE},
                 failure(category=PRIVATE),failure(primary_kind=PRIVATE),
                 failure(primary_category='io'),failure(category='io'),
                 failure(cleanup=cleanup(stop_confirmed=True)),
                 failure(cleanup={**cleanup(),'path':PRIVATE}),
                 failure(cleanup=cleanup(stdin_done=1)),
                 failure(cleanup=cleanup(threads_exited='false')),
                 {**failure(),'available':1}]
        for value in invalid:
            with self.subTest(value=value):
                observed=self.sanitize(value)
                self.assertFalse(observed['available'])
                self.assertNotIn(PRIVATE,json.dumps(observed,allow_nan=False))

    def test_hostile_mapping_and_exception_properties_are_not_invoked(self):
        reads=[]
        class Hostile(dict):
            def __iter__(self):
                reads.append('iter'); raise AssertionError(PRIVATE)
            def get(self,*args):
                reads.append('get'); raise AssertionError(PRIVATE)
        self.assertFalse(self.sanitize(Hostile(failure()))['available'])
        self.assertFalse(self.sanitize(failure(cleanup=Hostile(cleanup())))['available'])
        class HostileError(workers.WorkerProcessError):
            @property
            def worker_failure(self):
                reads.append('property'); raise AssertionError(PRIVATE)
        receipt=self.fixture(HostileError(PRIVATE))
        self.assertFalse(receipt['selected'])
        observed=acceptance_batch_png.dialog_diagnostic(receipt)
        self.assertFalse(observed['worker_failure']['available'])
        self.assertEqual(reads,[])

    def fixture(self,error,stopped=True):
        choice=native_dialog_fixture.NativeFileChoice.__new__(native_dialog_fixture.NativeFileChoice)
        choice.request={'input':PRIVATE}; choice.mode='folder'; choice.timeout=20
        choice.result=None; choice._token=workers.CancellationToken()
        choice._cleanup_confirmed=None; choice._cleanup_error_kind=None
        choice._supervisor=SimpleNamespace(run=Mock(side_effect=error),shutdown=Mock(return_value=stopped))
        with patch.object(native_dialog_fixture,'worker_command',return_value=[PRIVATE]):
            choice._run()
        self.assertNotIn(PRIVATE,json.dumps(choice.result,allow_nan=False))
        return choice.result

    def test_fixture_and_acceptance_preserve_primary_timeout_without_authorizing_success(self):
        error=workers.WorkerProcessError(PRIVATE)
        error.worker_failure=failure()
        error.timeout_boundary=boundary()
        for stopped in (True,False):
            receipt=self.fixture(error,stopped)
            self.assertFalse(receipt['selected'])
            self.assertEqual(receipt['error_kind'],'worker_process_error')
            self.assertEqual(receipt['reason'],'worker_failure' if stopped else 'worker_cleanup_unconfirmed')
            observed=acceptance_batch_png.dialog_diagnostic(receipt)
            self.assertEqual(observed['worker_failure']['primary_kind'],'timeout')
            self.assertTrue(observed['timeout_boundary']['available'])
            self.assertFalse(observed['worker_failure']['cleanup']['stop_confirmed'])
            self.assertEqual(observed['worker_exit_confirmed'],stopped)
            self.assertNotIn(PRIVATE,json.dumps(observed))

    def test_unauthorized_or_invalid_failure_packet_cannot_supply_timeout_facts(self):
        for metadata in ({**failure(),'command':PRIVATE},failure(category='io',primary_kind=None),None):
            error=workers.WorkerProcessError(PRIVATE)
            error.worker_failure=metadata; error.timeout_boundary=boundary()
            observed=acceptance_batch_png.dialog_diagnostic(self.fixture(error))
            self.assertFalse(observed['selected'])
            self.assertFalse(observed['timeout_boundary']['available'])
            self.assertNotIn(PRIVATE,json.dumps(observed))

if __name__=='__main__':
    unittest.main()
