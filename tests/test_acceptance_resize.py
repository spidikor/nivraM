"""Resize evidence, geometry and owned Escape failures without native input."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from nivram import acceptance, acceptance_resize as resize, acceptance_escape as escape, acceptance_click as click
import test_acceptance_reporting as reporting
from test_acceptance_click import FakeWin32, COORDINATES


def measured(width=1000, view='0 0 1000 600'):
    return {'canvas': {'width': width, 'height': 600}, 'ink': {'width': 500, 'height': 300},
            'padding': dict.fromkeys(('left', 'right', 'top', 'bottom'), 24), 'view': view}


def completed():
    value = reporting.AcceptanceFinalResultTests().completed_report()
    cases = []
    for kind in resize.KINDS:
        row = {'kind': kind, **dict.fromkeys(resize.FACTS, True),
            'initial': measured(), 'during': measured(800), 'settled': measured(800, '0 0 800 600'),
            'revisionDelta': 1 if kind in ('success', 'manual') else 0,
            'gestures': [{'kind': k, 'status': 'passed', 'isTrusted': True, 'userActivation': True,
                          'ownershipVerified': True} for k in resize.GESTURES[kind]]}
        if kind == 'cancel-late':
            row.update(keyboardPanelOpened=False, escapeTrusted=True, replacementDistinct=True,
                       lateStateRetained=True, lateViewRetained=True, followupWorldRetained=True)
        if kind == 'manual': row['subsequentResizeRetained'] = True
        cases.append(row)
    value.update(scenario='resize', resize_observation={'schemaVersion': 1, 'cases': cases},
        checks=[{'id': k, 'status': 'passed'} for k in acceptance.RESIZE_REQUIRED_CHECKS], dialog_fixture_shutdown=[])
    for k in ('download_shutdown', 'downloads_final'): value[k]['completed'] = 0
    return value


class ResizeEvidenceTests(unittest.TestCase):
    def test_complete_four_cases_keep_shared_finalization(self):
        value = completed(); acceptance.finalize_acceptance_report(value)
        self.assertEqual(value['acceptance_failures'], [])
        self.assertEqual(value['status'], 'passed')
        self.assertEqual(acceptance.RESIZE_REQUIRED_CHECKS, set(resize.CHECKS) | {'editor-and-native-bridge-ready', 'native-window-loop-returned'})

    def test_each_check_remains_required(self):
        for missing in acceptance.RESIZE_REQUIRED_CHECKS:
            value = completed(); value['checks'] = [r for r in value['checks'] if r['id'] != missing]
            acceptance.finalize_acceptance_report(value)
            self.assertIn('required_functional_checks_missing', value['acceptance_failures'])

    def test_each_case_and_boolean_fact_is_required(self):
        for i in range(4):
            for key in resize.FACTS:
                for bad in (None, False, 1, 'true'):
                    value = completed(); value['resize_observation']['cases'][i][key] = bad
                    self.assertTrue(resize.observation_failures(value), (i, key, bad))
            value = completed(); del value['resize_observation']['cases'][i]
            self.assertTrue(resize.observation_failures(value))

    def test_malformed_and_reordered_cases_cannot_pass(self):
        for bad in (None, {}, [], {'schemaVersion': True}, {'schemaVersion': 1, 'cases': [None]*4}):
            self.assertTrue(resize.observation_failures({'resize_observation': bad}))
        value = completed(); value['resize_observation']['cases'].reverse()
        self.assertTrue(resize.observation_failures(value))

    def test_actual_shrink_pending_view_and_revision_are_mandatory(self):
        for i in range(4):
            for field, bad in (('revisionDelta', True), ('revisionDelta', 9)):
                value = completed(); value['resize_observation']['cases'][i][field] = bad
                self.assertTrue(resize.observation_failures(value))
            for path, bad in (('width', 1000), ('view', 'different')):
                value = completed(); during = value['resize_observation']['cases'][i]['during']
                if path == 'width': during['canvas']['width'] = bad
                else: during[path] = bad
                self.assertTrue(resize.observation_failures(value))

    def test_late_response_manual_mode_and_native_events_are_required(self):
        for key in ('escapeTrusted', 'replacementDistinct', 'lateStateRetained', 'lateViewRetained', 'followupWorldRetained'):
            value = completed(); value['resize_observation']['cases'][2][key] = False
            self.assertTrue(resize.observation_failures(value))
        value = completed(); value['resize_observation']['cases'][3]['subsequentResizeRetained'] = False
        self.assertTrue(resize.observation_failures(value))
        for key, bad in (('isTrusted', False), ('ownershipVerified', False), ('userActivation', False), ('kind', 'other')):
            value = completed(); value['resize_observation']['cases'][0]['gestures'][0][key] = bad
            self.assertTrue(resize.observation_failures(value))

    def test_optional_panel_open_must_match_observed_click(self):
        value = completed(); case = value['resize_observation']['cases'][2]
        case['keyboardPanelOpened'] = True
        self.assertTrue(resize.observation_failures(value))
        row = copy.deepcopy(case['gestures'][0]); row['kind'] = 'resize-keyboard'
        case['gestures'].insert(8, row)
        self.assertEqual(resize.observation_failures(value), [])

    def test_dialog_download_process_and_shared_policy_gates_still_apply(self):
        for key, field, bad in (
            ('dialog_fixture_shutdown', None, [{'shutdownConfirmed': True}]),
            ('dialog_fixture_shutdown', None, None), ('download_shutdown', 'completed', 1),
            ('downloads_final', 'active', 1), ('network_policy', 'failures', 1),
            ('evaluation_shutdown', 'workerExited', False), ('worker_shutdown', 'ownedProcessesExited', False),
            ('cleanup', 'profile_directory_retained', True), ('process', None, {'exit_code': 0, 'forced_termination': True})):
            value = completed()
            if field is None: value[key] = bad
            else: value[key][field] = bad
            acceptance.finalize_acceptance_report(value)
            self.assertEqual(value['status'], 'failed', (key, field))

    def test_resize_subset_cannot_qualify_full_or_editing(self):
        for scenario in ('full', 'editing'):
            value = completed(); value['scenario'] = scenario
            acceptance.finalize_acceptance_report(value)
            self.assertIn('required_functional_checks_missing', value['acceptance_failures'])

    def test_frozen_dispatch_preserves_scenario(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', 'synthetic.exe'):
            self.assertEqual(acceptance.acceptance_child_command(Path('report.json'), Path('profile'), 'resize'),
                ['synthetic.exe', '--acceptance-report', 'report.json', '--child', '--profile', 'profile', '--scenario', 'resize'])


class ResizeGeometryTests(unittest.TestCase):
    def test_case_imports_are_distinct_real_structures_in_a_shared_window(self):
        from nivram.chemistry import import_structure
        graphs = [import_structure(text) for text in resize.STRUCTURES]
        self.assertEqual([len(g['atoms']) for g in graphs], [42, 43, 44, 45])
        self.assertEqual(graphs[0], import_structure(resize.STRUCTURES[0]))
        self.assertTrue(all(a != b for a, b in zip(graphs, graphs[1:])))

    def test_async_observation_requires_completed_value_and_cleans_up(self):
        evaluate = Mock(side_effect=[True, {'failed': False, 'value': {'x': 3}}, True])
        poll = Mock()
        self.assertEqual(resize.async_value('Promise.resolve({x:3})', evaluate, poll, lambda: 10), {'x': 3})
        self.assertEqual(poll.call_count, 1)
        self.assertEqual(evaluate.call_args_list[-1].kwargs['stage'], 'resize-async-cleanup')

    def test_async_failure_and_timeout_cannot_return_a_success_and_still_clean(self):
        for bad in (None, {}, {'failed': True, 'value': True}, {'failed': 0, 'value': True}):
            evaluate = Mock(side_effect=[True, bad, True])
            with self.assertRaises(AssertionError):
                resize.async_value('Promise.resolve(true)', evaluate, Mock(), lambda: 10)
            self.assertEqual(evaluate.call_args_list[-1].kwargs['stage'], 'resize-async-cleanup')
        evaluate = Mock(return_value=True)
        with self.assertRaises(TimeoutError):
            resize.async_value('Promise.resolve(true)', evaluate, Mock(side_effect=TimeoutError()), lambda: 10)
        self.assertEqual(evaluate.call_count, 2)
        self.assertEqual(evaluate.call_args_list[-1].kwargs['stage'], 'resize-async-cleanup')

    def test_nonempty_finite_geometry_and_all_four_padding_sides(self):
        resize.fitted(measured())
        for group, key, bad in (('canvas', 'width', 0), ('ink', 'height', float('nan')),
                               ('padding', 'left', 23.49), ('padding', 'right', True)):
            value = measured(); value[group][key] = bad
            with self.assertRaises(AssertionError): resize.fitted(value)

    def test_world_coordinates_preserve_finite_values_with_tight_tolerance(self):
        view = dict(x=1, y=-2, w=3, h=4)
        resize.world_equal(view, {**view, 'x': 1+1e-8})
        for key in view:
            for bad in (float('inf'), float('nan'), True, view[key]+1e-6):
                with self.assertRaises(AssertionError): resize.world_equal({**view, key: bad}, view)


class EscapeOwnershipTests(unittest.TestCase):
    def test_trusted_escape_observation_and_listener_cleanup_are_required(self):
        backend = FakeWin32()
        control = SimpleNamespace(GetType=lambda: SimpleNamespace(FullName='Microsoft.Web.WebView2.WinForms.WebView2'),
            IsHandleCreated=True, IsDisposed=False, Handle=SimpleNamespace(ToInt64=lambda: 11))
        window = SimpleNamespace(native=SimpleNamespace(webview=control, Handle=SimpleNamespace(ToInt64=lambda: 10)))
        evaluate = Mock(side_effect=[COORDINATES, True, {'trusted': True, 'escape': True}, True])
        with patch.object(click, '_WindowsBackend', return_value=backend), patch.object(click, '_browser_pid', return_value=backend.browser_pid):
            result = escape.escape(window, evaluate)
        self.assertIs(result['isTrusted'], True)
        self.assertTrue(backend.closed)
        self.assertEqual(evaluate.call_args_list[-1].kwargs['stage'], 'resize-escape-cleanup')

    def test_untrusted_wrong_or_untyped_key_observation_cannot_pass(self):
        for value in ({'trusted': False, 'escape': True}, {'trusted': True, 'escape': False},
                      {'trusted': 1, 'escape': True}, {'trusted': True, 'escape': 1}, {}):
            backend = FakeWin32()
            control = SimpleNamespace(GetType=lambda: SimpleNamespace(FullName='Microsoft.Web.WebView2.WinForms.WebView2'),
                IsHandleCreated=True, IsDisposed=False, Handle=SimpleNamespace(ToInt64=lambda: 11))
            window = SimpleNamespace(native=SimpleNamespace(webview=control, Handle=SimpleNamespace(ToInt64=lambda: 10)))
            evaluate = Mock(side_effect=[COORDINATES, True, value, True])
            with patch.object(click, '_WindowsBackend', return_value=backend), patch.object(click, '_browser_pid', return_value=backend.browser_pid):
                with self.assertRaises(click.NativeClickRejected): escape.escape(window, evaluate)
            self.assertTrue(backend.closed)
            self.assertEqual(evaluate.call_args_list[-1].kwargs['stage'], 'resize-escape-cleanup')

    def test_only_fixed_escape_pair_is_sent_to_owned_descendant(self):
        backend = FakeWin32(); result = escape.send_escape(backend, 10, 11, COORDINATES)
        self.assertEqual(backend.messages, [(12, 0x100, 0x1B, 0x10001, 1000), (12, 0x101, 0x1B, 0xC0010001, 1000)])
        self.assertIs(result['ownershipVerified'], True)

    def test_foreign_process_and_bad_coordinates_receive_no_key(self):
        for change in (lambda b: b.pids.update({12: b.pid+1}), lambda b: setattr(b, 'current_created', 0)):
            backend = FakeWin32(); change(backend)
            with self.assertRaises(click.NativeClickRejected): escape.send_escape(backend, 10, 11, COORDINATES)
            self.assertEqual(backend.messages, [])
        backend = FakeWin32()
        with self.assertRaises(click.NativeClickRejected): escape.send_escape(backend, 10, 11, {**COORDINATES, 'x': -1})
        self.assertEqual(backend.messages, [])

    def test_replaced_window_before_keyup_does_not_receive_keyup(self):
        backend = FakeWin32(); original = backend.send
        def send(*args):
            result = original(*args); backend.descendants[11] = 13; return result
        backend.send = send
        with self.assertRaises(click.NativeClickRejected): escape.send_escape(backend, 10, 11, COORDINATES)
        self.assertEqual(len(backend.messages), 1)

    def test_failed_delivery_is_not_success(self):
        backend = FakeWin32(); backend.delivery = False
        with self.assertRaises(click.NativeClickRejected): escape.send_escape(backend, 10, 11, COORDINATES)
        self.assertEqual(len(backend.messages), 1)


if __name__ == '__main__':
    unittest.main()
