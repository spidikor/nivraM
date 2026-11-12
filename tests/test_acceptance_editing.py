"""Discriminating native editing evidence checks without launching a window."""
import copy
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nivram import acceptance, acceptance_editing as editing
from nivram.chemistry import import_structure
from nivram.persistence import validate_project
from nivram.rotation import rotate_graph
import test_acceptance_reporting as reporting


def facts():
    return {'schemaVersion': 1, 'importRevisionDelta': 2, **dict.fromkeys(editing.FACTS, True),
        'savedSha256': 'a' * 64, 'comparisonSha256': 'b' * 64, 'reopenedSha256': 'a' * 64,
        'gestures': [{'kind': kind, 'status': 'passed', 'isTrusted': True, 'userActivation': True}
                     for kind in editing.GESTURES],
        'dialogs': [{'mode': mode, 'originalReturned': True, 'actualReturnVerified': True, 'fixtureExited': True}
                    for mode in ('save', 'save', 'open')]}


def completed():
    value = reporting.AcceptanceFinalResultTests().completed_report()
    value.update(scenario='editing', editing_observation=facts(),
        checks=[{'id': key, 'status': 'passed'} for key in acceptance.EDITING_REQUIRED_CHECKS],
        dialog_fixture_shutdown=[{'shutdownConfirmed': True} for _ in range(3)])
    for key in ('download_shutdown', 'downloads_final'):
        value[key]['completed'] = 0
    return value


class EditingEvidenceTests(unittest.TestCase):
    def test_frozen_child_forwards_exact_editing_scenario(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', 'synthetic.exe'):
            command = acceptance.acceptance_child_command(Path('report.json'), Path('profile'), 'editing')
        self.assertEqual(command, ['synthetic.exe', '--acceptance-report', 'report.json',
                                  '--child', '--profile', 'profile', '--scenario', 'editing'])

    def test_complete_fixed_journey_retains_shared_finalization(self):
        report = completed()
        acceptance.finalize_acceptance_report(report)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['acceptance_failures'], [])
        self.assertEqual(acceptance.EDITING_REQUIRED_CHECKS,
                         editing.REQUIRED_CHECKS | {'editor-and-native-bridge-ready', 'native-window-loop-returned'})

    def test_each_functional_check_is_required(self):
        for missing in acceptance.EDITING_REQUIRED_CHECKS:
            with self.subTest(missing=missing):
                report = completed()
                report['checks'] = [r for r in report['checks'] if r['id'] != missing]
                acceptance.finalize_acceptance_report(report)
                self.assertIn('required_functional_checks_missing', report['acceptance_failures'])

    def test_each_fact_must_be_a_true_boolean(self):
        for key in editing.FACTS:
            for value in (False, 1, None, 'true'):
                with self.subTest(key=key, value=value):
                    report = completed(); report['editing_observation'][key] = value
                    self.assertEqual(editing.observation_failures(report), ['native_editing_observation_unconfirmed'])

    def test_malformed_observation_is_not_accepted(self):
        variants = [None, [], {}, {'schemaVersion': True}]
        for delta in (None, True, 1, 3):
            candidate = facts(); candidate['importRevisionDelta'] = delta; variants.append(candidate)
        for key in ('dialogs', 'gestures'):
            for value in (None, {}, [None], [], facts()[key][:-1]):
                candidate = facts(); candidate[key] = value; variants.append(candidate)
        for candidate in variants:
            with self.subTest(candidate=candidate):
                self.assertTrue(editing.observation_failures({'editing_observation': candidate}))

    def test_untrusted_out_of_order_or_unconfirmed_native_actions_fail(self):
        for key, index, field, value in (
            ('gestures', 0, 'isTrusted', False), ('gestures', 4, 'userActivation', False),
            ('gestures', 2, 'kind', 'edit-undo'), ('gestures', 5, 'status', 'observed'),
            ('dialogs', 0, 'originalReturned', False), ('dialogs', 1, 'actualReturnVerified', False),
            ('dialogs', 2, 'fixtureExited', False), ('dialogs', 2, 'mode', 'save')):
            with self.subTest(key=key, field=field):
                report = completed(); report['editing_observation'][key][index][field] = value
                acceptance.finalize_acceptance_report(report)
                self.assertIn('native_editing_observation_unconfirmed', report['acceptance_failures'])

    def test_matching_stale_or_malformed_hashes_cannot_prove_reopen(self):
        for key, value in (('comparisonSha256', 'a' * 64), ('reopenedSha256', 'c' * 64),
                           ('savedSha256', ''), ('savedSha256', None), ('savedSha256', 'z' * 64)):
            report = completed(); report['editing_observation'][key] = value
            self.assertTrue(editing.observation_failures(report))

    def test_all_shared_shutdown_policy_and_download_gates_still_apply(self):
        cases = (('network_policy', 'failures', 1), ('cleanup', 'profile_directory_retained', True),
            ('evaluation_shutdown', 'workerExited', False), ('dialog_fixture_shutdown', None, [{'shutdownConfirmed': True}]),
            ('download_shutdown', 'completed', 1), ('downloads_final', 'shutdown_confirmed', False),
            ('image_preview_shutdown_confirmed', None, False), ('worker_shutdown', 'ownedProcessesExited', False),
            ('process', None, {'exit_code': 0, 'forced_termination': True}))
        for key, field, value in cases:
            with self.subTest(key=key, field=field):
                report = completed()
                if field is None: report[key] = value
                else: report[key][field] = value
                acceptance.finalize_acceptance_report(report)
                self.assertEqual(report['status'], 'failed')
                self.assertTrue(report['acceptance_failures'])

    def test_editing_subset_cannot_qualify_full_acceptance(self):
        report = completed(); report['scenario'] = 'full'
        acceptance.finalize_acceptance_report(report)
        self.assertIn('required_functional_checks_missing', report['acceptance_failures'])


class EditingStateTests(unittest.TestCase):
    def setUp(self):
        self.document = validate_project({'schema': 'nivram/1', 'graph': import_structure(editing.SMILES),
            'style': {'bondWidth': 4.2}, 'export': {'width': 2000, 'marginMode': 'percent', 'margin': 2}})
        self.before = {'snapshot': {'document': self.document, 'revision': 5, 'dirty': True,
            'recoveryGeneration': 'a' * 32}, 'drawing': '<svg/>', 'undoDisabled': False, 'redoDisabled': True}

    def test_rotation_oracle_uses_clockwise_angle_and_preserves_all_other_fields(self):
        fixture = {'graph': {'atoms': [{'id': 'a', 'x': 2, 'y': 1}, {'id': 'b', 'x': 2, 'y': -1}], 'bonds': []},
                   'style': {'example': 3}}
        actual = editing.rotation_document(fixture)
        self.assertEqual(actual['graph']['atoms'][0]['x'], 2.5)
        self.assertAlmostEqual(actual['graph']['atoms'][0]['y'], math.sqrt(3) / 2)
        self.assertEqual(actual['graph']['atoms'][1]['x'], 1.5)
        self.assertAlmostEqual(actual['graph']['atoms'][1]['y'], -math.sqrt(3) / 2)
        self.assertEqual(fixture['graph']['atoms'][0]['x'], 2)
        self.assertEqual(actual['style'], fixture['style'])

    def test_real_rotation_matches_independent_oracle_and_identity(self):
        actual = copy.deepcopy(self.document)
        actual['graph'] = rotate_graph(self.document['graph'], degrees=-30)
        editing.assert_rotation(self.document, actual)
        for change in ('coordinate', 'isotope', 'charge', 'style', 'bond'):
            candidate = copy.deepcopy(actual)
            if change == 'coordinate': candidate['graph']['atoms'][0]['x'] += .01
            elif change == 'isotope': candidate['graph']['atoms'][0]['isotope'] = 12
            elif change == 'charge': candidate['graph']['atoms'][-1]['charge'] = 0
            elif change == 'style': candidate['style']['bondWidth'] += 1
            else: candidate['graph']['bonds'][0]['order'] = 2
            with self.subTest(change=change), self.assertRaises(AssertionError):
                editing.assert_rotation(self.document, candidate)

    def test_save_changes_only_dirty_state(self):
        after = copy.deepcopy(self.before); after['snapshot']['dirty'] = False
        editing.assert_save(self.before, after)
        for kind in ('revision', 'drawing', 'history', 'generation'):
            candidate = copy.deepcopy(after)
            if kind == 'revision': candidate['snapshot']['revision'] += 1
            elif kind == 'drawing': candidate['drawing'] = '<svg changed="true"/>'
            elif kind == 'history': candidate['undoDisabled'] = True
            else: candidate['snapshot']['recoveryGeneration'] = 'b' * 32
            with self.subTest(kind=kind), self.assertRaises(AssertionError):
                editing.assert_save(self.before, candidate)

    def test_one_history_step_cannot_hide_extra_revisions_or_wrong_document(self):
        after = copy.deepcopy(self.before); after['snapshot']['revision'] = 6
        editing.assert_step(self.before, after, document=self.document, dirty=True, redo_disabled=True)
        for key, value in (('revision', 7), ('dirty', False), ('document', {}), ('recoveryGeneration', 'c' * 32)):
            candidate = copy.deepcopy(after); candidate['snapshot'][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                editing.assert_step(self.before, candidate, document=self.document, dirty=True, redo_disabled=True)


class EditingDialogTests(unittest.TestCase):
    def test_fixture_paths_pass_the_real_picker_boundary_before_any_window(self):
        from nivram.native_dialog_worker import _fixture_paths
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'instance.lock').write_bytes(b'synthetic owned fixture')
            paths = editing.prepare_paths(root)
            self.assertEqual(len(paths), 2)
            for path in paths:
                self.assertEqual(path.parent, root / 'synthetic fixtures')
                self.assertEqual(_fixture_paths(root, path), (root, path))
                self.assertFalse(path.exists())
            with self.assertRaises(ValueError):
                _fixture_paths(root, root / 'synthetic editing' / paths[0].name)

    def test_fixture_directory_reuse_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'instance.lock').write_bytes(b'synthetic owned fixture')
            editing.prepare_paths(root)
            with self.assertRaises(FileExistsError):
                editing.prepare_paths(root)

    def observer(self, returns, selected=True):
        paths = (Path('synthetic-a.nivram').resolve(), Path('synthetic-b.nivram').resolve())
        calls, choices = [], []
        def original(*args, **kwargs):
            calls.append((args, kwargs))
            return returns[len(calls) - 1]
        def choose(path, **kwargs):
            choices.append((path, kwargs))
            return SimpleNamespace(finish=lambda: {'selected': selected, 'worker_exit_confirmed': True})
        window = SimpleNamespace(create_file_dialog=original)
        return editing.EditingDialogs(window, choose, paths, 1, 2, lambda: 20), window, original, paths, calls, choices

    def test_three_exact_original_returns_are_preserved_and_observer_restored(self):
        paths = (Path('synthetic-a.nivram').resolve(), Path('synthetic-b.nivram').resolve())
        returned = [str(paths[0]), (str(paths[1]),), [str(paths[0])]]
        observer, window, original, _, calls, choices = self.observer(returned)
        with observer:
            for i in range(3):
                options = {'save_filename': 'structure.nivram'} if i < 2 else {'allow_multiple': False}
                value = window.create_file_dialog(1 if i < 2 else 2, **options, file_types=('nivraM projects (*.nivram)',))
                self.assertIs(value, returned[i])
        self.assertIs(window.create_file_dialog, original)
        self.assertEqual(len(calls), len(choices))
        self.assertTrue(all(r['actualReturnVerified'] and r['fixtureExited'] for r in observer.snapshot()))

    def test_wrong_original_return_cannot_be_replaced_by_successful_fixture(self):
        for value in (None, [], ['wrong.nivram'], [str(Path('synthetic-a.nivram').resolve()), 'extra'], 4):
            observer, window, original, *_ = self.observer([value])
            with observer, self.assertRaises(AssertionError):
                window.create_file_dialog(1, save_filename='structure.nivram', file_types=('nivraM projects (*.nivram)',))
            self.assertIs(window.create_file_dialog, original)
            self.assertFalse(observer.snapshot()[0]['actualReturnVerified'])

    def test_failed_fixture_and_wrong_dialog_arguments_fail(self):
        observer, window, _, paths, calls, _ = self.observer([str(Path('synthetic-a.nivram').resolve())], selected=False)
        with observer, self.assertRaisesRegex(AssertionError, 'fixture_failed'):
            window.create_file_dialog(1, save_filename='structure.nivram', file_types=('nivraM projects (*.nivram)',))
        self.assertEqual(len(calls), 1)
        observer, window, _, _, calls, _ = self.observer([str(paths[0])])
        with observer, self.assertRaisesRegex(AssertionError, 'arguments'):
            window.create_file_dialog(2, allow_multiple=True)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
