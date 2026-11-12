"""Recovery evidence gates and native confirmation identity; no UI is launched."""
import copy
import unittest

from nivram import acceptance,acceptance_recovery as recovery
from nivram.acceptance_recovery_confirmation import validate_open,validate_closed
import test_acceptance_reporting as reporting


def completed():
    value=reporting.AcceptanceFinalResultTests().completed_report()
    facts=dict(schemaVersion=1,**dict.fromkeys(recovery.FACTS,True),
        gestures=[dict(kind=k,status='passed',isTrusted=True,userActivation=True) for k in recovery.GESTURES],
        dialogs=[dict(mode=k,originalReturned=True,actualReturnVerified=True,fixtureExited=True) for k in ('save','save','open','save')],
        confirmations=[dict(decision=v,**dict.fromkeys(recovery.CONFIRM_FIELDS,True)) for v in (False,True)])
    value.update(scenario='recovery',recovery_observation=facts,
        checks=[dict(id=k,status='passed') for k in acceptance.RECOVERY_REQUIRED_CHECKS],
        dialog_fixture_shutdown=[dict(shutdownConfirmed=True) for _ in range(4)])
    for k in ('download_shutdown','downloads_final'):value[k]['completed']=0
    return value


class RecoveryEvidenceTests(unittest.TestCase):
    def test_complete_recovery_preserves_shared_exit_and_cleanup_gate(self):
        value=completed();acceptance.finalize_acceptance_report(value)
        self.assertEqual(value['status'],'passed')
        self.assertEqual(value['acceptance_failures'],[])

    def test_each_missing_recovery_fact_or_check_is_rejected(self):
        for key in recovery.FACTS:
            value=completed();value['recovery_observation'][key]=False
            self.assertTrue(recovery.observation_failures(value),key)
        for key in recovery.CHECKS:
            value=completed();value['checks']=[r for r in value['checks'] if r['id']!=key]
            acceptance.finalize_acceptance_report(value)
            self.assertEqual(value['status'],'failed',key)

    def test_dialog_result_and_real_trusted_input_are_independent(self):
        for key in recovery.CONFIRM_FIELDS:
            value=completed();value['recovery_observation']['confirmations'][0][key]=False
            self.assertTrue(recovery.observation_failures(value),key)
        for key in ('isTrusted','userActivation'):
            value=completed();value['recovery_observation']['gestures'][0][key]=False
            self.assertTrue(recovery.observation_failures(value))
        value=completed();value['recovery_observation']['confirmations'][0]['decision']=True
        self.assertTrue(recovery.observation_failures(value))

    def test_missing_file_chooser_return_or_shutdown_cannot_pass(self):
        for key in ('originalReturned','actualReturnVerified','fixtureExited'):
            value=completed();value['recovery_observation']['dialogs'][0][key]=False
            self.assertTrue(recovery.observation_failures(value))
        value=completed();value['process']=dict(exit_code=0,forced_termination=True)
        acceptance.finalize_acceptance_report(value)
        self.assertEqual(value['status'],'failed')

    def test_confirmation_requires_exact_origin_prompt_type_and_top_session(self):
        message='Open the most recent readable recovery from synthetic as an unsaved project?'
        url='http://127.0.0.1:12345/'
        value=dict(message=message,url=url,type='confirm',hasBrowserHandler=True)
        validate_open(value,message,url)
        for key,bad in [('message',message+' changed'),('url',url+'foreign'),('type','prompt'),
                        ('hasBrowserHandler',False),('hasBrowserHandler',1),('defaultPrompt','unexpected')]:
            with self.subTest(key=key),self.assertRaises(AssertionError):
                validate_open(value|{key:bad},message,url)
        with self.assertRaises(AssertionError):validate_open(value,message,url,'foreign-frame-session')

    def test_confirmation_close_requires_observed_boolean_result(self):
        for decision in (False,True):
            validate_closed(dict(result=decision,userInput=''),decision)
            for bad in (1-decision,None,'true',not decision):
                with self.subTest(decision=decision,bad=bad),self.assertRaises(AssertionError):
                    validate_closed(dict(result=bad),decision)
        with self.assertRaises(AssertionError):validate_closed(dict(result=True,userInput='unexpected'),True)
        with self.assertRaises(AssertionError):validate_closed(dict(result=True),True,'foreign')


if __name__=='__main__':unittest.main()
