"""Behavioral scenario-deadline checks with fake UI time, no native launch.

The real scenario setup, dialog observer, exact synthetic-path checks, and
terminal polling boundary run here. Chemical setup and UI execution are fakes;
these tests establish no chemical, PNG-publication, or Windows-cleanup result.
"""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nivram import acceptance_batch_png as batch


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TerminalWaitAccepted(Exception):
    """Stop before unrelated artifact checks once terminal polling succeeds."""


class BatchScenarioDeadlineTests(unittest.TestCase):
    def exercise(self, *, exhaust_before_terminal=False):
        import webview

        temporary = tempfile.TemporaryDirectory(prefix='nv-batch-budget-')
        self.addCleanup(temporary.cleanup)
        profile = Path(temporary.name).resolve()
        profiles = profile / 'profiles'
        profiles.mkdir()
        clock = Clock()
        report = {}
        calls = {'setup_poll_timeouts': [], 'fixture_timeouts': [],
                 'dialog_modes': [], 'terminal_poll_timeouts': [], 'checked': []}
        document = {'schema': 'nivram/1',
            'graph': {'atoms': [{'id': str(i), 'symbol': 'C', 'x': i, 'y': 0,
                                 'charge': 0} for i in range(4)], 'bonds': []},
            'style': {'bondWidth': 4.2, 'hashBondStyle': 'spaced'},
            'export': dict(batch.OPTIONS)}
        record = {'id': 'd'*32, 'revision': 1, 'name': batch.PROFILE_NAME,
                  'style': copy.deepcopy(document['style']), 'export': dict(batch.OPTIONS)}
        listing = {'invalidRecords': 0, 'profiles': [record]}
        active = False

        def evaluate(expression, *, timeout, stage):
            nonlocal active
            if stage == 'batch-active-settings-and-refresh':
                active = True
            if stage == 'batch-document-snapshot':
                value = copy.deepcopy(document)
                if active:
                    value['export'] = dict(batch.ACTIVE_OPTIONS)
                return {'snapshot': {'document': value, 'revision': 1,
                                     'dirty': False, 'savedIdentity': None,
                                     'recoveryGeneration': 0},
                        'drawing': '<g/>', 'undoDisabled': True, 'redoDisabled': True}
            return True

        class Choice:
            def __init__(self, target, mode, timeout):
                calls['fixture_timeouts'].append(timeout)
                self.target, self.mode = target, mode

            def finish(self):
                return {'selected': True, 'worker_exit_confirmed': True}

        def choose(target, *, mode, timeout, parent_io):
            self.assertIs(parent_io, True)
            return Choice(target, mode, timeout)

        class Window:
            def create_file_dialog(self, kind, **kwargs):
                fixtures = profile / 'synthetic fixtures'
                if kind == webview.FileDialog.OPEN:
                    calls['dialog_modes'].append('open-multiple')
                    clock.advance(13.469)
                    return tuple(str(fixtures / name) for name in (batch.VALID_NAME, batch.INVALID_NAME))
                self_test.assertEqual(kind, webview.FileDialog.FOLDER)
                calls['dialog_modes'].append('folder')
                clock.advance(8)
                return (str(fixtures / 'batch PNG outputs \u00fc'),)

        self_test = self
        window = Window()
        original = window.create_file_dialog

        def click(window_arg, evaluate_arg, purpose):
            self.assertIs(window_arg, window)
            self.assertEqual(purpose, 'batch')
            if exhaust_before_terminal:
                clock.advance(120.001)
            return {'verified': True}

        def poll(expression, *, timeout):
            if "textContent.startsWith('1 of 2 PNG files saved.')" not in expression:
                calls['setup_poll_timeouts'].append(timeout)
                return True
            calls['terminal_poll_timeouts'].append(timeout)
            polling_deadline = clock() + timeout
            # Call the scenario's real observer around both unchanged real-return
            # fakes. Each selection is under its own 15-second allowance.
            window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True,
                                      file_types=batch.INPUT_FILTER)
            window.create_file_dialog(webview.FileDialog.FOLDER)
            clock.advance(1)  # Export/UI settlement after the two chooser returns.
            if clock() > polling_deadline:
                raise TimeoutError('Synthetic completion exceeded terminal polling budget.')
            raise TerminalWaitAccepted()

        api = SimpleNamespace(_profiles=SimpleNamespace(directory=profiles),
                              list_profiles=lambda: copy.deepcopy(listing))
        downloads = SimpleNamespace(snapshot=lambda: {'last_id': 0, 'accepted': 0, 'completed': 0})
        fake_store = SimpleNamespace(list=lambda: copy.deepcopy(listing))
        error = None
        original_clock = time.monotonic
        original_batch_time = batch.time
        # Patch this consumer's clock, leaving dependency initialization on real time.
        with patch.object(batch, 'time', SimpleNamespace(monotonic=clock)):
            self.assertIs(time.monotonic, original_clock,
                          'shared interpreter clock must stay real')
            with patch('nivram.profiles.ProfileStore', return_value=fake_store), \
                    patch('nivram.persistence.validate_project', side_effect=copy.deepcopy), \
                    patch.object(batch, '_semantic_hash_classes', return_value=None), \
                    patch('nivram.acceptance_click.click_png_button', side_effect=click):
                try:
                    batch.run(evaluate=evaluate, poll=poll, choose=choose,
                              check=lambda identifier, **facts: calls['checked'].append(identifier),
                              stage=lambda label: None, report=report, profile=profile,
                              api=api, downloads=downloads, window=window)
                except Exception as caught:
                    error = caught
        self.assertIs(time.monotonic, original_clock)
        self.assertIs(batch.time, original_batch_time)
        self.assertEqual(window.create_file_dialog, original)
        return error, report, calls, clock()

    def test_two_valid_slow_chooser_returns_fit_terminal_completion_budget(self):
        error, report, calls, elapsed = self.exercise()
        self.assertIsInstance(error, TerminalWaitAccepted,
            f'Whole-operation wait rejected valid selections: {type(error).__name__}')
        self.assertAlmostEqual(elapsed, 22.469)
        self.assertEqual(calls['dialog_modes'], ['open-multiple', 'folder'])
        self.assertEqual(calls['fixture_timeouts'], [15, 15])
        self.assertTrue(calls['setup_poll_timeouts'])
        self.assertTrue(all(value == 20 for value in calls['setup_poll_timeouts']))
        self.assertEqual(calls['terminal_poll_timeouts'], [40])
        self.assertTrue(all(value['actualReturnVerified'] is True
                            for value in report['batch_png_dialogs']))
        self.assertFalse(report['batch_png_scenario']['nativeBatchVerified'])

    def test_exhausted_overall_scenario_deadline_rejects_before_chooser(self):
        error, report, calls, elapsed = self.exercise(exhaust_before_terminal=True)
        self.assertIsInstance(error, TimeoutError)
        self.assertEqual(str(error), 'The focused native batch scenario exceeded its deadline.')
        self.assertAlmostEqual(elapsed, 120.001)
        self.assertEqual(calls['terminal_poll_timeouts'], [])
        self.assertEqual(calls['fixture_timeouts'], [])
        self.assertEqual(calls['dialog_modes'], [])
        self.assertTrue(all(value == 20 for value in calls['setup_poll_timeouts']))
        self.assertFalse(report['batch_png_scenario']['nativeBatchVerified'])


if __name__ == '__main__':
    unittest.main()
