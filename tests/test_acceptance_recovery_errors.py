"""Acceptance metadata checks; native execution is reported separately."""
import copy
import unittest

from nivram import acceptance, acceptance_recovery_errors as recovery
import test_acceptance_reporting as reporting


def completed():
    value = reporting.AcceptanceFinalResultTests().completed_report()
    value.update(scenario='recovery-errors',
        checks=[dict(id=k,status='passed') for k in acceptance.RECOVERY_ERRORS_REQUIRED_CHECKS],
        dialog_fixture_shutdown=[dict(shutdownConfirmed=True)],
        recovery_errors_observation=dict(schemaVersion=1, **dict.fromkeys(recovery.FACTS, True),
            gestures=[dict(kind=k,status='passed',isTrusted=True,userActivation=True) for k in recovery.GESTURES],
            dialog=dict(action='cancel',originalReturned=True,actualReturnVerified=True,workerExited=True),
            diagnosticCounts=dict(invalid=1,unsupported=2,unreadable=1,interrupted=1),
            retainedHashes=['a'*64]*5))
    for k in ('download_shutdown','downloads_final'):
        value[k]['completed'] = 0
    return value


class RecoveryErrorsEvidenceTests(unittest.TestCase):
    def test_complete_failure_journey_preserves_shared_gate(self):
        value=completed()
        acceptance.finalize_acceptance_report(value)
        self.assertEqual(value['status'],'passed')
        self.assertEqual(value['acceptance_failures'],[])

    def test_each_missing_fact_or_functional_check_rejects_claim(self):
        for key in recovery.FACTS:
            value=completed();value['recovery_errors_observation'][key]=False
            self.assertTrue(recovery.observation_failures(value),key)
        for key in recovery.CHECKS:
            value=completed();value['checks']=[r for r in value['checks'] if r['id']!=key]
            acceptance.finalize_acceptance_report(value)
            self.assertEqual(value['status'],'failed',key)

    def test_nonnative_input_or_unconfirmed_cancel_is_rejected(self):
        for field in ('isTrusted','userActivation'):
            value=completed();value['recovery_errors_observation']['gestures'][0][field]=False
            self.assertTrue(recovery.observation_failures(value))
        for field in ('originalReturned','actualReturnVerified','workerExited'):
            value=completed();value['recovery_errors_observation']['dialog'][field]=False
            self.assertTrue(recovery.observation_failures(value))

    def test_missing_retained_bytes_or_diagnostics_is_rejected(self):
        for key, bad in [('retainedHashes',[]),('retainedHashes',['not-a-hash']*5),
                         ('diagnosticCounts',{}),('diagnosticCounts',dict(invalid=0,unsupported=2,unreadable=1,interrupted=1))]:
            value=completed();value['recovery_errors_observation'][key]=bad
            self.assertTrue(recovery.observation_failures(value))

    def test_failure_journey_cannot_claim_full_native_acceptance(self):
        value=completed();value['scenario']='full'
        acceptance.finalize_acceptance_report(value)
        self.assertEqual(value['status'],'failed')

    def test_forced_exit_or_pending_worker_never_passes(self):
        value=completed();value['process']=dict(exit_code=0,forced_termination=True)
        acceptance.finalize_acceptance_report(value)
        self.assertEqual(value['status'],'failed')
        value=completed();value['worker_shutdown']['ownedProcessesExited']=False
        acceptance.finalize_acceptance_report(value)
        self.assertEqual(value['status'],'failed')


if __name__=='__main__':unittest.main()

