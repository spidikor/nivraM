"""Reject partial restart evidence without weakening shared native lifecycle gates."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from nivram import acceptance
from nivram import acceptance_recovery_restart as restart
from nivram.acceptance_recovery import CONFIRM_FIELDS
import test_acceptance_reporting as reporting


def completed():
    value=reporting.AcceptanceFinalResultTests().completed_report()
    facts=dict(schemaVersion=1,phase='durable',**dict.fromkeys(restart.FACTS,True),
        gestures=[dict(kind=k,status='passed',isTrusted=True,userActivation=True) for k in ('recover-project','save-project')],
        dialogs=[dict(mode='save',originalReturned=True,actualReturnVerified=True,fixtureExited=True)],
        confirmations=[dict(decision=True,**dict.fromkeys(CONFIRM_FIELDS,True))])
    value.update(scenario='recovery-restart',restart_observation=facts,
        checks=[dict(id=k,status='passed') for k in acceptance.RESTART_REQUIRED_CHECKS],
        dialog_fixture_shutdown=[dict(shutdownConfirmed=True)])
    for k in ('download_shutdown','downloads_final'):value[k]['completed']=0
    return value


class RestartEvidenceTests(unittest.TestCase):
    def test_normal_consumer_requires_complete_functional_and_lifecycle_evidence(self):
        for phase in restart.PHASES:
            value=completed();value['restart_observation']['phase']=phase
            acceptance.finalize_acceptance_report(value)
            self.assertEqual(value['status'],'passed')
        for key in restart.FACTS:
            value=completed();value['restart_observation'][key]=False
            acceptance.finalize_acceptance_report(value)
            self.assertEqual(value['status'],'failed',key)
        for key in acceptance.RESTART_REQUIRED_CHECKS:
            value=completed();value['checks']=[x for x in value['checks'] if x['id']!=key]
            acceptance.finalize_acceptance_report(value)
            self.assertEqual(value['status'],'failed',key)

    def test_interruption_is_never_normal_native_acceptance(self):
        value=completed();value['scenario']='recovery-producer'
        value['process']=dict(exit_code=restart.EXIT_CODE,forced_termination=False)
        acceptance.finalize_acceptance_report(value)
        self.assertEqual(value['status'],'failed')
        value=completed();value['process']=dict(exit_code=0,forced_termination=True)
        acceptance.finalize_acceptance_report(value)
        self.assertEqual(value['status'],'failed')

    def test_consumer_requires_real_confirmation_and_owned_chooser_completion(self):
        for section,fields in [('confirmations',CONFIRM_FIELDS),('dialogs',
                ('originalReturned','actualReturnVerified','fixtureExited')),('gestures',('isTrusted','userActivation'))]:
            for field in fields:
                value=completed();value['restart_observation'][section][0][field]=False
                self.assertTrue(restart.observation_failures(value),(section,field))
        for section in ('confirmations','dialogs','gestures'):
            value=completed();value['restart_observation'][section]=[]
            self.assertTrue(restart.observation_failures(value),section)

    def test_request_is_fixed_phase_and_exact_profile_sibling(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'restart-request.json';profile=Path(temporary)/'profile'
            config=dict(schemaVersion=1,phase='flushed',nonce='a'*32,sourceSha256='b'*64)
            path.write_text(json.dumps(config))
            self.assertEqual(restart.request(profile),config)
            for key,bad in [('schemaVersion',True),('phase','power-loss'),('nonce','../escape'),
                            ('sourceSha256','bad'),('profile','foreign')]:
                path.write_text(json.dumps(config|{key:bad}))
                with self.assertRaises(AssertionError):restart.request(profile)

    def test_marker_cannot_transfer_between_cases_or_publication_phases(self):
        config=dict(schemaVersion=1,phase='durable',nonce='a'*32,sourceSha256='b'*64)
        marker=dict(request=config,phase='durable',interruptionExit=72,productionAutosave=True,
            fsyncReturned=True,published=True,generation='c'*32,revision=5,recoveryPins={'sample':'d'*64},
            gestures=[dict(kind=k,status='passed',isTrusted=True,userActivation=True)
                for k in ('edit-import','edit-load','save-project','edit-rotate-cw')],
            dialogs=[dict(mode='save',originalReturned=True,actualReturnVerified=True,fixtureExited=True)])
        restart.validate_marker(marker,config)
        for key,bad in [('phase','flushed'),('nonce','e'*32),('sourceSha256','f'*64)]:
            with self.assertRaises(AssertionError):restart.validate_marker(marker,config|{key:bad})
        for key,bad in [('published',False),('fsyncReturned',False),('productionAutosave',False),
                        ('interruptionExit',0),('generation','bad'),('revision',True),('recoveryPins',{})]:
            with self.assertRaises(AssertionError):restart.validate_marker(marker|{key:bad},config)


if __name__=='__main__':unittest.main()
