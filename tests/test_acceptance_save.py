"""Reject false native-save assurance; these tests do not launch native UI."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from nivram import acceptance, acceptance_save as saving
import test_acceptance_reporting as reporting


def facts():
    receipts = []
    for status, save_as in zip(saving.STATUSES, saving.SAVE_AS):
        result = {'status':status}
        if status == 'saved': result.update(disk_hash='a'*64, name='synthetic.nivram')
        elif status == 'unconfirmed': result['outcome'] = 'unknown'
        elif status == 'unsupported': result['outcome'] = 'not_committed'
        receipts.append(dict(saveAs=save_as, settled=True, result=result))
    return dict(schemaVersion=1, **dict.fromkeys(saving.FACTS, True),
        receipts=receipts, storeCalls=8,
        diskHashes=dict.fromkeys(('competing','uncertain','separate','final'), 'a'*64),
        gestures=[dict(kind=k,status='passed',isTrusted=True,userActivation=True) for k in saving.GESTURES],
        dialogs=[dict(action=a,originalReturned=True,actualReturnVerified=True,fixtureExited=True) for a in saving.ACTIONS])


def completed():
    value = reporting.AcceptanceFinalResultTests().completed_report()
    value.update(scenario='save', save_observation=facts(),
        checks=[dict(id=k,status='passed') for k in acceptance.SAVE_REQUIRED_CHECKS],
        dialog_fixture_shutdown=[dict(shutdownConfirmed=True) for _ in range(7)])
    for k in ('download_shutdown','downloads_final'): value[k]['completed'] = 0
    return value


class NativeSaveEvidenceTests(unittest.TestCase):
    def test_complete_journey_preserves_shared_acceptance_gate(self):
        value = completed()
        acceptance.finalize_acceptance_report(value)
        self.assertEqual(value['acceptance_failures'], [])
        self.assertEqual(value['status'], 'passed')

    def test_missing_functional_case_or_unconfirmed_cleanup_fails(self):
        for identifier in acceptance.SAVE_REQUIRED_CHECKS:
            with self.subTest(identifier=identifier):
                value = completed()
                value['checks'] = [r for r in value['checks'] if r['id'] != identifier]
                acceptance.finalize_acceptance_report(value)
                self.assertEqual(value['status'], 'failed')
        for location in ('cleanup','evaluation_shutdown','dialog_fixture_shutdown','worker_shutdown'):
            with self.subTest(location=location):
                value = completed(); value[location] = None
                acceptance.finalize_acceptance_report(value)
                self.assertEqual(value['status'], 'failed')

    def test_missing_or_truthy_assertions_cannot_certify_saved_state(self):
        for key in saving.FACTS:
            for invalid in (None, False, 1, 'true'):
                with self.subTest(key=key, invalid=invalid):
                    value = completed(); value['save_observation'][key] = invalid
                    self.assertTrue(saving.observation_failures(value))

    def test_false_success_hash_on_failure_or_wrong_outcome_is_rejected(self):
        for index, status in enumerate(saving.STATUSES):
            with self.subTest(index=index, status=status):
                value = completed(); row = value['save_observation']['receipts'][index]['result']
                if status == 'saved': row['disk_hash'] = 'not-a-digest'
                else: row['disk_hash'] = 'f'*64
                self.assertTrue(saving.observation_failures(value))
        for index in (5,8):
            value = completed(); value['save_observation']['receipts'][index]['result']['outcome'] = 'committed'
            self.assertTrue(saving.observation_failures(value))

    def test_uncertainty_and_unsupported_require_next_ordinary_save_to_choose(self):
        for index in (6,7,9,10):
            value = completed(); value['save_observation']['receipts'][index]['saveAs'] = False
            self.assertTrue(saving.observation_failures(value))

    def test_picker_return_and_trusted_input_are_independent_requirements(self):
        for kind, fields in (('dialogs',('originalReturned','actualReturnVerified','fixtureExited')),
                             ('gestures',('isTrusted','userActivation'))):
            for field in fields:
                value = completed(); value['save_observation'][kind][0][field] = False
                self.assertTrue(saving.observation_failures(value))
        for field in ('receipts','dialogs','gestures'):
            value = completed(); value['save_observation'][field].pop()
            self.assertTrue(saving.observation_failures(value))

    def test_unsaved_oracle_rejects_history_document_and_dirty_changes(self):
        before = dict(snapshot=dict(dirty=True,revision=7,document={'synthetic':1}), drawing='<svg/>',
                      undoDisabled=False,redoDisabled=True)
        saving.unchanged(before, copy.deepcopy(before))
        for key, change in [('dirty',False),('revision',8),('document',{'synthetic':2})]:
            after = copy.deepcopy(before); after['snapshot'][key] = change
            with self.subTest(key=key), self.assertRaises(AssertionError):
                saving.unchanged(before, after)
        for key, change in [('drawing','<svg>other</svg>'),('undoDisabled',True),('redoDisabled',False)]:
            after = copy.deepcopy(before); after[key] = change
            with self.subTest(key=key), self.assertRaises(AssertionError):
                saving.unchanged(before, after)

    def test_no_implicit_saved_document_is_accepted_by_unsaved_oracle(self):
        before = dict(snapshot=dict(dirty=False,revision=7,document={}))
        with self.assertRaisesRegex(AssertionError,'save_expected_unsaved_document'):
            saving.unchanged(before, copy.deepcopy(before))

    def test_scenario_reaches_frozen_child_without_scope_substitution(self):
        with patch.object(sys,'frozen',True,create=True), patch.object(sys,'executable','synthetic.exe'):
            command = acceptance.acceptance_child_command(Path('report.json'),Path('profile'),'save')
        self.assertEqual(command, ['synthetic.exe','--acceptance-report','report.json',
                                  '--child','--profile','profile','--scenario','save'])


if __name__ == '__main__': unittest.main()

