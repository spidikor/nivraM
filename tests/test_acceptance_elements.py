"""Native element scenario evidence and independent document/history boundaries."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from nivram import acceptance, acceptance_elements as elements
from test_acceptance_keys import events
import test_acceptance_reporting as reporting


def completed():
    value = reporting.AcceptanceFinalResultTests().completed_report()
    cases = []
    for kind in elements.KINDS:
        actions = []
        for item in elements.ACTION_ORDER[kind]:
            category, name = item.split(':')
            action = dict(type=category, kind=name, status='passed', ownershipVerified=True)
            if category == 'key': action['events'] = events(name)
            elif category == 'button': action.update(isTrusted=True, userActivation=True)
            else: action.update(trusted=True, mouse=True, buttons=0, at=100, route='owned-webview2-sdk-hover')
            actions.append(action)
        count = 2 if kind in ('queued-history', 'expiry-prefix') else 1 if kind == 'pointer-text' else 0
        row = dict(kind=kind, actions=actions, extensionMilliseconds=[20]*count, **dict.fromkeys(elements.FACTS[kind], True))
        if kind == 'expiry-prefix': row['expiryMilliseconds'] = 550
        cases.append(row)
    value.update(scenario='elements', elements_observation=dict(schemaVersion=1, cases=cases),
        checks=[dict(id=k, status='passed') for k in acceptance.ELEMENTS_REQUIRED_CHECKS], dialog_fixture_shutdown=[])
    for k in ('download_shutdown', 'downloads_final'): value[k]['completed'] = 0
    return value


class ElementEvidenceTests(unittest.TestCase):
    def test_four_complete_cases_finalize_and_missing_check_fails(self):
        value = completed(); acceptance.finalize_acceptance_report(value)
        self.assertEqual(value['acceptance_failures'], [])
        value = completed(); value['checks'].pop(); acceptance.finalize_acceptance_report(value)
        self.assertIn('required_functional_checks_missing', value['acceptance_failures'])

    def test_every_fact_is_mandatory_and_strict_boolean(self):
        for i, kind in enumerate(elements.KINDS):
            for field in elements.FACTS[kind]:
                for bad in (None, False, 1, 'true'):
                    value = completed(); value['elements_observation']['cases'][i][field] = bad
                    self.assertTrue(elements.observation_failures(value), (kind, field, bad))

    def test_malformed_missing_duplicate_or_reordered_cases_fail(self):
        for bad in (None, {}, [], dict(schemaVersion=True), dict(schemaVersion=1, cases=[None]*4)):
            self.assertTrue(elements.observation_failures(dict(elements_observation=bad)))
        value = completed(); value['elements_observation']['cases'].reverse()
        self.assertTrue(elements.observation_failures(value))
        value = completed(); value['elements_observation']['cases'][1] = value['elements_observation']['cases'][0]
        self.assertTrue(elements.observation_failures(value))

    def test_actions_require_exact_order_ownership_trust_and_event_target(self):
        for field, bad in [('ownershipVerified', False), ('status', 'failed'), ('isTrusted', 1), ('userActivation', False)]:
            value = completed(); value['elements_observation']['cases'][0]['actions'][0][field] = bad
            self.assertTrue(elements.observation_failures(value))
        for index, field, bad in [(2, 'buttons', True), (2, 'mouse', False), (2, 'route', 'unowned'), (3, 'events', []), (3, 'kind', ['cl'])]:
            value = completed(); value['elements_observation']['cases'][0]['actions'][index][field] = bad
            self.assertTrue(elements.observation_failures(value))
        value = completed(); value['elements_observation']['cases'][0]['actions'].reverse()
        self.assertTrue(elements.observation_failures(value))

    def test_extension_is_strictly_under_500_and_expiry_at_least_500(self):
        for bad in (500, -1, True, float('nan'), float('inf')):
            value = completed(); value['elements_observation']['cases'][0]['extensionMilliseconds'][0] = bad
            self.assertTrue(elements.observation_failures(value))
        for bad in (499.99, True, float('inf'), None):
            value = completed(); value['elements_observation']['cases'][1]['expiryMilliseconds'] = bad
            self.assertTrue(elements.observation_failures(value))
        self.assertEqual(elements.within(10, 509.999), 499.999)
        for a, b in ((10, 510), (10, 9), (True, 10), (0, float('nan'))):
            with self.assertRaises(AssertionError): elements.within(a, b)

    def test_shared_cleanup_policy_dialog_download_and_process_gates_remain(self):
        for key, field, bad in [('dialog_fixture_shutdown', None, [dict(shutdownConfirmed=True)]),
            ('download_shutdown', 'completed', 1), ('downloads_final', 'active', 1), ('network_policy', 'failures', 1),
            ('evaluation_shutdown', 'workerExited', False), ('worker_shutdown', 'ownedProcessesExited', False),
            ('cleanup', 'profile_directory_retained', True), ('process', None, dict(exit_code=0, forced_termination=True))]:
            value = completed()
            if field is None: value[key] = bad
            else: value[key][field] = bad
            acceptance.finalize_acceptance_report(value)
            self.assertEqual(value['status'], 'failed', (key, field))

    def test_element_subset_cannot_qualify_other_scenarios(self):
        for scenario in ('full', 'editing', 'resize'):
            value = completed(); value['scenario'] = scenario; acceptance.finalize_acceptance_report(value)
            self.assertIn('required_functional_checks_missing', value['acceptance_failures'])

    def test_frozen_dispatch_keeps_element_scenario(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', 'synthetic.exe'):
            self.assertEqual(acceptance.acceptance_child_command(Path('report.json'), Path('profile'), 'elements'),
                ['synthetic.exe', '--acceptance-report', 'report.json', '--child', '--profile', 'profile', '--scenario', 'elements'])

    def test_commit_oracle_preserves_all_other_document_and_snapshot_fields(self):
        from nivram.chemistry import import_structure
        document = dict(graph=import_structure('N'), style={'synthetic': 1})
        before = dict(snapshot=dict(document=document, revision=7, dirty=False, recoveryGeneration='synthetic', path=None))
        after = copy.deepcopy(before); after['snapshot'].update(revision=9, dirty=True)
        after['snapshot']['document']['graph']['atoms'][0]['symbol'] = 'Cl'
        after.update(undoDisabled=False, redoDisabled=True)
        elements.assert_commit(before, after, 'Cl', 2)
        for mutate in (lambda x: x['snapshot'].update(revision=8), lambda x: x['snapshot'].update(path='changed'),
                       lambda x: x['snapshot']['document']['graph']['atoms'][0].update(charge=1),
                       lambda x: x.update(redoDisabled=False)):
            wrong = copy.deepcopy(after); mutate(wrong)
            with self.assertRaises(AssertionError): elements.assert_commit(before, wrong, 'Cl', 2)
        self.assertEqual(before['snapshot']['document'], document)


if __name__ == '__main__': unittest.main()
