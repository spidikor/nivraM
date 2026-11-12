"""Pure synthetic UI-state regressions; no application/native imports or launch."""
import importlib.util
from pathlib import Path
import unittest


_path = Path(__file__).resolve().parents[1] / 'nivram' / 'acceptance_naming.py'
_spec = importlib.util.spec_from_file_location('acceptance_naming_under_test', _path)
naming = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(naming)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def panel(name, details='', *, cancel_hidden=True):
    return {'name': name, 'details': details, 'cancelHidden': cancel_hidden}


class NamingAcceptanceTests(unittest.TestCase):
    def wait(self, states, timeout=35):
        clock = FakeClock()
        observed = iter(states)
        last = states[-1]

        def evaluate(expression):
            self.assertEqual(expression, naming.NAMING_PANEL_SNAPSHOT)
            return next(observed, last)

        result = naming.wait_for_naming(evaluate, timeout=timeout,
                                       clock=clock, sleep=clock.sleep)
        return result, clock.now

    def test_client_timeout_without_details_exposes_prior_polling_defect(self):
        failed = panel('Local naming timed out. Try again or continue editing.')
        # This is the old production predicate, not an alternate implementation.
        old_terminal = bool(failed['details']) and failed['name'] != 'Calculating locally.'
        self.assertFalse(old_terminal)
        result, elapsed = self.wait([panel('Calculating locally.', cancel_hidden=False), failed])
        self.assertEqual(result, failed)
        self.assertLess(elapsed, 1)
        self.assertEqual(naming.naming_diagnostics(result, terminal=True)['kind'], 'client-timeout')
        with self.assertRaisesRegex(AssertionError, 'Local naming timed out'):
            naming.require_naming_response_details(result)

    def test_transport_failure_is_retained_exactly_and_fails(self):
        failed = panel('Failed to fetch')
        result, elapsed = self.wait([failed])
        self.assertEqual(elapsed, 0)
        diagnostic = naming.naming_diagnostics(result, terminal=True)
        self.assertEqual(diagnostic['name'], 'Failed to fetch')
        self.assertEqual(diagnostic['details'], '')
        self.assertEqual(diagnostic['kind'], 'terminal-error')
        with self.assertRaisesRegex(AssertionError, 'Failed to fetch'):
            naming.require_naming_response_details(result)

    def test_returned_name_waits_for_frontend_finally(self):
        details = 'round trip matched · NISPO 0.1.7.'
        result, elapsed = self.wait([panel('ethanol', details, cancel_hidden=False),
                                     panel('ethanol', details)])
        self.assertGreater(elapsed, 0)
        self.assertEqual(result['name'], 'ethanol')
        naming.require_naming_response_details(result)

    def test_ethanol_without_details_cannot_pass(self):
        result, _ = self.wait([panel('ethanol')])
        with self.assertRaises(AssertionError):
            naming.require_naming_response_details(result)

    def test_no_accepted_name_preserves_capability_response(self):
        result, _ = self.wait([panel('No accepted name', 'unavailable · NISPO 0.1.7.')])
        naming.require_naming_response_details(result)
        self.assertEqual(naming.naming_diagnostics(result, terminal=True)['kind'], 'no-accepted-name')

    def test_cancelled_is_terminal_but_not_success(self):
        result, _ = self.wait([panel('Cancelled')])
        self.assertEqual(naming.naming_diagnostics(result, terminal=True)['kind'], 'cancelled')
        with self.assertRaises(AssertionError):
            naming.require_naming_response_details(result)

    def test_initial_missing_and_busy_states_are_not_terminal(self):
        for state in (None, {}, panel(''), panel('Calculate a name for the current structure.'),
                      panel('Failed to fetch', cancel_hidden=False)):
            with self.subTest(state=state):
                self.assertFalse(naming.is_naming_terminal(state))
        for label in ('Calculating locally\u2026', 'Calculating locally.'):
            for cancel_hidden in (True, False):
                with self.subTest(label=label, cancel_hidden=cancel_hidden):
                    self.assertFalse(naming.is_naming_terminal(panel(label, cancel_hidden=cancel_hidden)))

    def test_unicode_pending_label_keeps_waiting_with_cancel_hidden_or_visible(self):
        finished = panel('ethanol', 'round trip matched · NISPO 0.1.7.')
        for cancel_hidden in (True, False):
            with self.subTest(cancel_hidden=cancel_hidden):
                busy = panel('Calculating locally\u2026', cancel_hidden=cancel_hidden)
                result, elapsed = self.wait([busy, finished])
                self.assertEqual(result, finished)
                self.assertGreater(elapsed, 0)

    def test_real_wait_expiry_keeps_last_state_and_original_deadline(self):
        clock = FakeClock()
        busy = panel('Calculating locally.', cancel_hidden=False)
        with self.assertRaises(naming.NamingPanelTimeout) as caught:
            naming.wait_for_naming(lambda _: busy, timeout=35, clock=clock, sleep=clock.sleep)
        self.assertEqual(clock.now, 35)
        diagnostic = naming.naming_diagnostics(caught.exception.snapshot, terminal=False)
        self.assertEqual(diagnostic['name'], 'Calculating locally.')
        self.assertEqual(diagnostic['kind'], 'poll-timeout')
        self.assertFalse(diagnostic['cancelHidden'])

    def test_error_report_omits_paths_urls_and_credential_assignments(self):
        for text in (r'Read failed C:\Users\private\runtime\java.exe',
                     r'Read failed \\private-host\share\runtime',
                     'Read failed /home/private/runtime',
                     'Failed https://private.invalid/path',
                     'token=private-value'):
            with self.subTest(text=text):
                diagnostic = naming.naming_diagnostics(panel(text), terminal=True)
                self.assertTrue(diagnostic['nameRedacted'])
                self.assertNotIn('private', diagnostic['name'])
                diagnostic = naming.naming_diagnostics(panel('No accepted name', text), terminal=True)
                self.assertTrue(diagnostic['detailsRedacted'])
                self.assertNotIn('private', diagnostic['details'])
                with self.assertRaises(AssertionError) as caught:
                    naming.require_naming_response_details(panel(text))
                self.assertNotIn('private', str(caught.exception))

    def test_diagnostics_are_bounded_and_copy_no_extra_fields(self):
        source = panel('x' * 2000, 'y' * 10000)
        source['token'] = 'private'
        diagnostic = naming.naming_diagnostics(source, terminal=True)
        self.assertEqual(len(diagnostic['name']), 1024)
        self.assertEqual(len(diagnostic['details']), 8192)
        self.assertTrue(diagnostic['nameTruncated'])
        self.assertTrue(diagnostic['detailsTruncated'])
        self.assertNotIn('token', diagnostic)
        self.assertEqual(source['token'], 'private')

    def test_generated_unverified_ethanol_verifies_ui_only(self):
        result = naming.naming_verification(panel('ethanol', 'generated unverified \u00b7 NISPO 0.1.7.'))
        self.assertEqual(result, {'naming_ui_verified': True, 'naming_roundtrip_verified': False})

    def test_actual_matched_ethanol_status_verifies_roundtrip(self):
        result = naming.naming_verification(panel('ethanol', 'round trip matched \u00b7 NISPO 0.1.7.'))
        self.assertEqual(result, {'naming_ui_verified': True, 'naming_roundtrip_verified': True})

    def test_unexpected_names_unavailable_and_pending_never_verify(self):
        matched = 'round trip matched \u00b7 NISPO 0.1.7.'
        for snapshot in (panel('methanol', matched), panel('No accepted name', 'unavailable \u00b7 NISPO'),
                         panel('ethanol'), panel('ethanol', matched, cancel_hidden=False), None):
            with self.subTest(snapshot=snapshot):
                self.assertEqual(naming.naming_verification(snapshot),
                                 {'naming_ui_verified': False, 'naming_roundtrip_verified': False})

    def test_roundtrip_requires_exact_leading_rendered_status(self):
        for details in ('generated roundtrip matched \u00b7 NISPO 0.1.7.',
                        'round_trip_matched \u00b7 NISPO 0.1.7.',
                        'round trip matched',
                        'generated unverified \u00b7 Warning mentions round trip matched later.'):
            with self.subTest(details=details):
                result = naming.naming_verification(panel('ethanol', details))
                self.assertTrue(result['naming_ui_verified'])
                self.assertFalse(result['naming_roundtrip_verified'])


if __name__ == '__main__':
    unittest.main()
