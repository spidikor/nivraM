"""Controlled in-memory observation integration; never launches a process/UI."""
import io
import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from nivram import native_dialog_fixture as fixture, worker_processes as workers
from nivram import acceptance_batch_png as batch
from nivram.parent_io import ParentIoCapture
from nivram.observation_envelope import decode_observation
from nivram.supervision_receipts import assess_recorded_evidence

IDENTITY = {'attempt_id': '01234567-89ab-4cde-8fab-0123456789ab',
            'source_sha256': 'b' * 64, 'origin': 'synthetic'}
OTHER = {**IDENTITY, 'attempt_id': '11234567-89ab-4cde-8fab-0123456789ab'}


def configure(capture):
    capture.configure(writer_expected=True, stdout_limit=128)
    for field in ('stdout_reader_entered', 'stderr_reader_entered', 'stdin_writer_entered',
                  'stdin_write_completed', 'stdin_close_completed', 'stdout_eof', 'stderr_eof'):
        capture.mark(field)


def decode(raw, identity=IDENTITY):
    return decode_observation(raw.encode('ascii'),
        expected_attempt_id=identity['attempt_id'], expected_source_sha256=identity['source_sha256'],
        expected_origin=identity['origin'], expected_boundary='primary_pre_cleanup')


class BoundConsumerTests(unittest.TestCase):
    def setUp(self):
        self.launch = patch.object(subprocess, 'Popen', side_effect=AssertionError('No subprocess allowed'))
        self.launch.start(); self.addCleanup(self.launch.stop)

    def choice(self, binding=IDENTITY, parent_io=True):
        with patch.object(fixture, 'build_request', return_value={'nonce': 'a' * 32}):
            return fixture.NativeFileChoice(None, None, None, parent_io=parent_io,
                                            observation_binding=binding)

    def run_choice(self, choice, run, *, shutdown=True, validated=None):
        choice._supervisor = SimpleNamespace(run=run, shutdown=Mock(return_value=shutdown))
        with patch.object(fixture, 'worker_command', return_value=['never-launched']), \
             patch.object(fixture, 'validate_result', return_value=validated or {'selected': True}):
            choice._run()

    def returned(self, *args, **kw):
        configure(kw['parent_io']); kw['parent_io'].freeze_primary('returned')
        return subprocess.CompletedProcess(args[0], 0, '{}', '')

    def test_default_unchanged_and_no_binding(self):
        c=self.choice(None); self.run_choice(c,self.returned)
        self.assertNotIn('parent_io_observation',c.result)
        self.assertIsNone(c.observation_snapshot())
        self.assertNotIn('parent_io_observation',batch.dialog_diagnostic(c.result))

    def test_identity_copied_and_four_partial_facts_consumed(self):
        binding=dict(IDENTITY); c=self.choice(binding);binding['source_sha256']='c'*64
        self.run_choice(c,self.returned)
        raw=batch.dialog_diagnostic(c.result,observation_binding=IDENTITY)['parent_io_observation']
        projected=decode(raw)
        self.assertEqual(projected.evidence.source_sha256,IDENTITY['source_sha256'])
        self.assertEqual(len(projected.mapped_fields),4)
        self.assertIsNone(projected.evidence.cleanup_confirmed)
        self.assertIsNone(projected.evidence.root_exit_confirmed)
        self.assertFalse(assess_recorded_evidence(projected.evidence).complete_recorded_evidence)
        self.assertFalse(assess_recorded_evidence(projected.evidence).normal_exit_recorded)

    def test_two_invocations_and_mismatch_rejected(self):
        a,b=self.choice(),self.choice(OTHER)
        self.run_choice(a,self.returned);self.run_choice(b,self.returned)
        self.assertNotEqual(a.result['parent_io_observation'],b.result['parent_io_observation'])
        self.assertIsNone(batch.dialog_diagnostic(a.result,observation_binding=OTHER)['parent_io_observation'])
        self.assertEqual(decode(b.result['parent_io_observation'],OTHER).evidence.attempt_id,OTHER['attempt_id'])

    def test_invalid_binding_or_disabled_capture_rejected(self):
        for binding in ({}, {**IDENTITY,'attempt_id':'nonce'}, {**IDENTITY,'origin':'verified'},
                        {**IDENTITY,'extra':False}, {'attempt_id':False,'source_sha256':'b'*64,'origin':'synthetic'}):
            with self.subTest(binding=binding),self.assertRaises(ValueError):self.choice(binding)
        with self.assertRaises(ValueError):self.choice(IDENTITY,parent_io=False)

    def test_decode_failure_cannot_rewrite_returned_primary(self):
        c=self.choice()
        def run(*args,**kw):
            self.returned(*args,**kw)
            return subprocess.CompletedProcess(args[0],0,'not json','')
        self.run_choice(c,run)
        self.assertFalse(c.result['selected'])
        self.assertEqual(c.result['error_kind'],'json_decode_error')
        self.assertEqual(decode(c.result['parent_io_observation']).evidence.outcome,'returned')

    def test_timeout_then_cleanup_failure_retains_primary(self):
        for shutdown in (False,True):
            c=self.choice()
            def run(*args,**kw):
                configure(kw['parent_io']);kw['parent_io'].freeze_primary('timeout')
                raise workers._process_error('synthetic','cleanup_incomplete',primary=subprocess.TimeoutExpired([],1))
            self.run_choice(c,run,shutdown=shutdown)
            self.assertFalse(c.result['selected'])
            self.assertEqual(decode(c.result['parent_io_observation']).evidence.outcome,'timeout')

    def test_pending_or_early_error_has_no_primary(self):
        c=self.choice();configure(c._parent_io)
        self.assertIsNone(c.observation_snapshot())
        self.assertTrue(c.parent_io_snapshot()['available'])
        def run(*args,**kw):raise ValueError('synthetic early error before supervised boundary')
        self.run_choice(c,run)
        self.assertIsNone(c.result['parent_io_observation'])

    def test_close_first_prevents_late_primary(self):
        c=self.choice();configure(c._parent_io)
        c._supervisor=SimpleNamespace(shutdown=lambda **kw:True)
        with patch.object(fixture,'cancel_owned_dialog',return_value=False):c.close()
        c._parent_io.freeze_primary('cancelled')
        self.assertIsNone(c.observation_snapshot())

    def test_primary_first_survives_close_and_independent_snapshot_copy(self):
        c=self.choice();configure(c._parent_io);c._parent_io.freeze_primary('timeout')
        raw=c.observation_snapshot(); packet=c._parent_io.primary_snapshot();packet['packet']['stdout_eof']=False
        c._supervisor=SimpleNamespace(shutdown=lambda **kw:True)
        with patch.object(fixture,'cancel_owned_dialog',return_value=False):c.close()
        self.assertEqual(c.observation_snapshot(),raw)

    def test_busy_primary_contains_no_affirmative_facts(self):
        c=self.choice();configure(c._parent_io)
        with c._parent_io._lock:c._parent_io.freeze_primary('timeout')
        p=decode(c.observation_snapshot());self.assertEqual(p.parent_io_status,'busy');self.assertEqual(p.mapped_fields,())

    def test_actual_observer_consumes_owned_fixture_binding(self):
        c=self.choice();self.run_choice(c,self.returned)
        c.finish=lambda:dict(c.result)
        window=SimpleNamespace(create_file_dialog=lambda *a,**k:('synthetic',))
        observer=batch.NativeDialogObserver(window,lambda *a,**k:c,root='/synthetic',
            inputs=('/synthetic/a','/synthetic/b'),folder='/synthetic/out',open_kind=1,folder_kind=2,timeout=lambda:1)
        with patch.object(batch,'assert_native_selection',return_value=['synthetic']):
            observer.attach()
            try:window.create_file_dialog(1,allow_multiple=True,file_types=batch.INPUT_FILTER)
            finally:observer.close()
        raw=observer.records[0]['fixture']['parent_io_observation']
        self.assertEqual(decode(raw).evidence.attempt_id,IDENTITY['attempt_id'])

    def test_host_reported_stays_declared(self):
        binding={**IDENTITY,'origin':'host_reported'};c=self.choice(binding);self.run_choice(c,self.returned)
        p=decode(c.result['parent_io_observation'],binding)
        self.assertEqual(p.origin,'host_reported');self.assertIsNone(p.evidence.ownership_confirmed)


class Process:
    def __init__(self):
        self.stdout=io.BytesIO(b'{}');self.stderr=io.BytesIO();self.stdin=io.BytesIO();self.returncode=0
    def poll(self):return self.returncode
    def terminate(self):self.returncode=0
    kill=terminate


class RealSupervisorSyntheticBoundaryTests(unittest.TestCase):
    def run_case(self,error=None,cleanup=True):
        capture=ParentIoCapture(primary_observation=True); supervisor=workers.WorkerSupervisor()
        process=Process(); original_check=supervisor.check_cancelled;count=0
        def check(token=None):
            nonlocal count
            count+=1
            if count==3 and error is not None:raise error
            return original_check(token)
        with patch.object(subprocess,'Popen',side_effect=AssertionError('No process launch')), \
             patch.object(supervisor,'_spawn',return_value=process), \
             patch.object(supervisor,'check_cancelled',side_effect=check), \
             patch.object(workers,'_stop_owned',return_value=cleanup):
            try:supervisor.run(['not-launched'],timeout_seconds=1,parent_io=capture)
            except BaseException as caught:
                if error is None and cleanup:raise
                self.assertIsInstance(caught,BaseException)
        supervisor.shutdown()
        return capture.primary_snapshot()

    def test_real_supervisor_outcomes_not_cleanup_outcome(self):
        for error,outcome in ((None,'returned'),(subprocess.TimeoutExpired([],1),'timeout'),
                              (workers.WorkerCancelled(),'cancelled'),(KeyboardInterrupt(),'interrupted'),
                              (ValueError(),'error')):
            for cleanup in (False,True):
                with self.subTest(outcome=outcome,cleanup=cleanup):
                    self.assertEqual(self.run_case(error,cleanup)['outcome'],outcome)

    def test_early_validation_spawn_failure_does_not_claim_boundary(self):
        for command in ([],['not-launched']):
            capture=ParentIoCapture(primary_observation=True);s=workers.WorkerSupervisor()
            with patch.object(subprocess,'Popen',side_effect=AssertionError('No process launch')), \
                 patch.object(s,'_spawn',side_effect=OSError('synthetic')):
                with self.assertRaises((ValueError,OSError)):s.run(command,timeout_seconds=1,parent_io=capture)
            self.assertIsNone(capture.primary_snapshot());s.shutdown()
