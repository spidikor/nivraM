"""Parent-only WebView facts; synthetic callbacks do not qualify native exit.

The legacy constructor adapter allows a behavioral RED run after adding the
capture module but before adding optional session observations. Legacy cleanup
still executes; missing facts fail assertions instead of a keyword TypeError.
"""
from collections import deque
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from nivram.webview_cleanup import CleanupError, ManagedWebViewSession
from nivram.webview_parent_observations import (
    ParentWebViewCapture, webview_parent_diagnostic,
)


_SESSION_HAS_CAPTURE = (
    'parent_observations' in inspect.signature(ManagedWebViewSession).parameters
)
_PHASES = ('shutdown_return', 'window_loop_return', 'handle_wait_return')
_SCHEMA_KEYS = {
    'available', 'status', 'sequence', 'exit_callback_count',
    'exit_callback_stage', 'exit_callback_browser_match',
    'exit_callback_profile_match', 'exit_callback_failure',
    'first_callback_order', 'matching_exit_order', 'dispose_dispatch',
    'dispose_enter_order', 'dispose_return_order', 'dispose_on_ui',
    'post_dispose_pulse_dispatch', 'post_dispose_pulse_order',
    'post_dispose_pulse_on_ui', 'form_closed_hook_status', 'form_closed_order',
    'pinned_handle_probe_status', 'pinned_handle_exited', 'guard_tick_count',
}
_RECEIPT_KEYS = {
    'status', 'session_id', 'native_profile_matched',
    'collection_exit_observed', 'collection_identity_matched',
    'browser_exit_kind', 'control_disposed', 'runtime_cache_removed',
    'deletion_attempts', 'reason', 'owned_fallback_attempted',
    'owned_process_cleanup',
}


def _session(profile, capture=None):
    if capture is None:
        return ManagedWebViewSession(profile)
    if _SESSION_HAS_CAPTURE:
        return ManagedWebViewSession(profile, parent_observations=capture)
    capture.configure()
    return ManagedWebViewSession(profile)


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self, *args):
        for handler in tuple(self.handlers):
            handler(*args)


class Dispatcher:
    def __init__(self):
        self.mode = 'immediate'
        self.pending = deque()
        self.calls = 0

    def BeginInvoke(self, action):
        self.calls += 1
        if self.mode == 'raise':
            raise OSError('private-dispatch-caption-and-nonce')
        if self.mode == 'queued':
            self.pending.append(action)
            return None
        action()

    def pump(self):
        while self.pending:
            self.pending.popleft()()


class Control(Dispatcher):
    def __init__(self, path):
        super().__init__()
        environment = SimpleNamespace(
            UserDataFolder=str(path), BrowserProcessExited=Event(),
        )
        self.CoreWebView2 = SimpleNamespace(
            Environment=environment, BrowserProcessId=123,
        )
        self.CoreWebView2InitializationCompleted = Event()
        self.InvokeRequired = True
        self.disposal_count = 0
        self.on_dispose = None

    def Dispose(self):
        self.disposal_count += 1
        if self.on_dispose:
            self.on_dispose()


class PrivateValue:
    """Any conversion or arbitrary diagnostic hook would be a privacy failure."""
    calls = []

    def __str__(self):
        self.calls.append('str')
        raise AssertionError('private payload converted')

    def __repr__(self):
        self.calls.append('repr')
        raise AssertionError('private payload represented')

    def snapshot(self):
        self.calls.append('snapshot')
        raise AssertionError('untrusted snapshot called')

    def configure(self):
        self.calls.append('configure')
        raise AssertionError('untrusted configuration called')

    @property
    def _frozen(self):
        self.calls.append('private-frozen-hook')
        raise AssertionError('untrusted capture storage accessed')


class ParentWebViewCaptureTests(unittest.TestCase):
    def capture(self):
        value = ParentWebViewCapture()
        value.configure()
        return value

    def assertUnavailable(self, packet, status='unavailable'):
        self.assertEqual(type(packet), dict)
        self.assertEqual(set(packet), _SCHEMA_KEYS)
        self.assertIs(packet['available'], False)
        self.assertEqual(packet['status'], status)
        self.assertTrue(all(packet[key] is None
                            for key in _SCHEMA_KEYS - {'available', 'status'}))

    def test_unconfigured_capture_and_configured_defaults_are_explicit(self):
        capture = ParentWebViewCapture()
        self.assertUnavailable(capture.snapshot())
        capture.configure()
        packet = capture.snapshot()
        self.assertEqual(set(packet), _SCHEMA_KEYS)
        self.assertIs(packet['available'], True)
        self.assertEqual(packet['status'], 'valid')
        self.assertEqual(packet['sequence'], 0)
        self.assertEqual(packet['exit_callback_count'], 0)
        self.assertEqual(packet['exit_callback_stage'], 'not_entered')
        self.assertEqual(packet['dispose_dispatch'], 'not_attempted')
        self.assertEqual(packet['post_dispose_pulse_dispatch'], 'not_attempted')
        self.assertEqual(packet['form_closed_hook_status'], 'not_attempted')
        self.assertEqual(packet['pinned_handle_probe_status'], 'unavailable')
        self.assertIsNone(packet['guard_tick_count'])

    def test_phase_freezes_are_independent_immutable_scalar_copies(self):
        capture = self.capture()
        capture.dispose_dispatch('requested')
        capture.dispose_entered(on_ui=True)
        capture.dispose_returned()
        capture.pulse_dispatch('queued')
        capture.pulse_seen(on_ui=True)
        capture.form_closed_hook('installed')
        capture.guard_attached()
        capture.guard_tick()
        first = capture.freeze(_PHASES[0])
        capture.callback_entered()
        capture.callback_browser_checked(True)
        capture.callback_profile_checked(True)
        capture.callback_verified()
        capture.form_closed()
        second = capture.freeze(_PHASES[1])
        capture.pinned_handle_probe('read', exited=True)
        third = capture.freeze(_PHASES[2])
        self.assertEqual(first['exit_callback_stage'], 'not_entered')
        self.assertEqual(second['exit_callback_stage'], 'verified')
        self.assertIsNone(first['form_closed_order'])
        self.assertIsNotNone(second['form_closed_order'])
        self.assertEqual(first['guard_tick_count'], 1)
        self.assertEqual(second['pinned_handle_probe_status'], 'unavailable')
        self.assertIs(third['pinned_handle_exited'], True)
        self.assertLess(first['dispose_enter_order'], first['dispose_return_order'])
        self.assertLess(first['dispose_return_order'], first['post_dispose_pulse_order'])
        self.assertLess(first['sequence'], second['sequence'])
        self.assertGreaterEqual(third['sequence'], second['sequence'])
        original = dict(first)
        first['exit_callback_stage'] = 'private-caption'
        self.assertEqual(capture.freeze(_PHASES[0]), original)
        self.assertEqual(capture.freeze(_PHASES[1]), second)
        self.assertEqual(capture.freeze(_PHASES[2]), third)

    def test_contended_snapshot_and_freeze_do_not_wait_or_recover_late(self):
        capture = self.capture()
        records = []
        capture._lock.acquire()
        worker = threading.Thread(target=lambda: records.extend([
            capture.snapshot(), capture.freeze(_PHASES[0]),
        ]))
        try:
            worker.start()
            worker.join(.5)
            completed_without_unlock = not worker.is_alive()
        finally:
            capture._lock.release()
            worker.join(1)
        self.assertTrue(completed_without_unlock)
        self.assertEqual(len(records), 2)
        for packet in records:
            self.assertUnavailable(packet, 'busy')
        capture.callback_entered()
        self.assertUnavailable(capture.freeze(_PHASES[0]), 'busy')
        self.assertEqual(capture.snapshot()['exit_callback_stage'], 'entered')

    def test_diagnostic_rejects_private_hooks_subclasses_and_unknown_values(self):
        PrivateValue.calls = []

        class PrivateDict(dict):
            def items(self):
                PrivateValue.calls.append('items')
                raise AssertionError('private mapping hook called')

        valid = self.capture().snapshot()
        for value in (PrivateValue(), PrivateDict(valid),
                      dict(valid, private_path=PrivateValue())):
            self.assertUnavailable(webview_parent_diagnostic(value))
        for key, value in (
            ('exit_callback_stage', 'private-caption'),
            ('exit_callback_failure', 'private-exception'),
            ('pinned_handle_probe_status', 'private-command-line'),
            ('sequence', True), ('exit_callback_count', 256),
            ('guard_tick_count', 65536), ('dispose_on_ui', PrivateValue()),
        ):
            malformed = dict(valid)
            malformed[key] = value
            self.assertUnavailable(webview_parent_diagnostic(malformed))
        self.assertEqual(PrivateValue.calls, [])
        self.assertEqual(webview_parent_diagnostic(valid), valid)

    def test_pinned_handle_observations_use_only_fixed_statuses_and_booleans(self):
        capture = self.capture()
        for status, exited in (('read', False), ('read', True),
                               ('failed', None), ('unavailable', None)):
            with self.subTest(status=status, exited=exited):
                if exited is None:
                    capture.pinned_handle_probe(status)
                else:
                    capture.pinned_handle_probe(status, exited=exited)
                packet = capture.snapshot()
                self.assertEqual(packet['pinned_handle_probe_status'], status)
                self.assertIs(packet['pinned_handle_exited'], exited)
                self.assertEqual(webview_parent_diagnostic(packet), packet)

    def test_session_rejects_private_capture_hooks_and_subclasses_before_creation(self):
        self.assertTrue(_SESSION_HAS_CAPTURE,
                        'ManagedWebViewSession needs the optional capture API.')
        PrivateValue.calls = []

        class PrivateCapture(ParentWebViewCapture):
            def configure(self):
                PrivateValue.calls.append('configure-subclass')
                raise AssertionError('capture subclass hook called')

        already_configured = self.capture()
        with TemporaryDirectory() as temporary:
            for index, invalid in enumerate((PrivateValue(), PrivateCapture(),
                                             already_configured)):
                profile = Path(temporary) / str(index)
                with self.subTest(index=index), self.assertRaises(CleanupError):
                    ManagedWebViewSession(profile, parent_observations=invalid)
                self.assertFalse(profile.exists())
        self.assertEqual(PrivateValue.calls, [])

    def test_acceptance_helpers_fail_closed_without_private_hooks_or_outcome_changes(self):
        from nivram import acceptance
        phase = getattr(acceptance, 'observe_webview_parent_phase', None)
        handle = getattr(acceptance, 'observe_webview_parent_handle', None)
        self.assertTrue(callable(phase), 'Acceptance needs the optional phase helper.')
        self.assertTrue(callable(handle), 'Acceptance needs the optional handle helper.')

        def report():
            return {'status': 'failed', 'checks': ['unchanged-functional-gate'],
                    'errors': ['fixed-primary-error'], 'cleanup': {
                        'managed_session': {'status': 'retained',
                            'reason': 'runtime_collection_exit_unconfirmed'}}}

        def assert_primary_unchanged(value):
            self.assertEqual(value['status'], 'failed')
            self.assertEqual(value['checks'], ['unchanged-functional-gate'])
            self.assertEqual(value['errors'], ['fixed-primary-error'])
            self.assertEqual(value['cleanup']['managed_session'], {
                'status': 'retained', 'reason': 'runtime_collection_exit_unconfirmed'})
            self.assertNotIn('private-', json.dumps(value, sort_keys=True))

        disabled = report()
        unchanged = json.loads(json.dumps(disabled))
        phase(disabled, None, _PHASES[0])
        handle(disabled, None, 'unavailable')
        self.assertEqual(disabled, unchanged)

        class ForeignCapture(ParentWebViewCapture):
            pass

        foreign = ForeignCapture()
        foreign.configure()
        PrivateValue.calls = []
        for invalid in (PrivateValue(), foreign):
            value = report()
            phase(value, invalid, _PHASES[0])
            handle(value, invalid, 'read', exited=True)
            self.assertIs(value['cleanup']['parent_observation_failed'], True)
            self.assertUnavailable(value['cleanup']['parent_observations'][_PHASES[0]])
            assert_primary_unchanged(value)
        self.assertEqual(PrivateValue.calls, [])

        capture = self.capture()
        value = report()
        with patch.object(ParentWebViewCapture, 'freeze',
                          side_effect=OSError('private-freeze-path-and-caption')) as freeze:
            phase(value, capture, _PHASES[0])
            phase(value, capture, _PHASES[1])
        self.assertEqual(freeze.call_count, 1)
        for name in _PHASES[:2]:
            self.assertUnavailable(value['cleanup']['parent_observations'][name])
        assert_primary_unchanged(value)

        value = report()
        with patch.object(ParentWebViewCapture, 'pinned_handle_probe',
                          side_effect=OSError('private-handle-command-and-nonce')):
            handle(value, capture, 'read', exited=False)
        phase(value, capture, _PHASES[2])
        self.assertIs(value['cleanup']['parent_observation_failed'], True)
        self.assertUnavailable(value['cleanup']['parent_observations'][_PHASES[2]])
        assert_primary_unchanged(value)

        value = report()
        malformed = dict(capture.snapshot(), private_path=PrivateValue())
        with patch.object(ParentWebViewCapture, 'freeze', return_value=malformed):
            phase(value, capture, _PHASES[0])
        self.assertUnavailable(value['cleanup']['parent_observations'][_PHASES[0]])
        self.assertEqual(PrivateValue.calls, [])
        assert_primary_unchanged(value)

        value = report()
        with patch('nivram.webview_parent_observations.webview_parent_diagnostic',
                   side_effect=RuntimeError('private-projector-caption-and-path')):
            phase(value, capture, _PHASES[0])
        self.assertIs(value['cleanup']['parent_observation_failed'], True)
        packet = value['cleanup'].get('parent_observations', {}).get(_PHASES[0])
        if packet is not None:
            self.assertUnavailable(packet)
        assert_primary_unchanged(value)

        PrivateValue.calls = []
        for invalid_phase in ('private-phase-caption', PrivateValue()):
            value = report()
            phase(value, capture, invalid_phase)
            self.assertIs(value['cleanup']['parent_observation_failed'], True)
            assert_primary_unchanged(value)
        self.assertEqual(PrivateValue.calls, [])

        original_import = __import__
        def fail_observation_import(name, *args, **kwargs):
            if name == 'webview_parent_observations':
                raise ImportError('private-import-runtime-location')
            return original_import(name, *args, **kwargs)
        value = report()
        with patch('builtins.__import__', side_effect=fail_observation_import):
            phase(value, capture, _PHASES[0])
            handle(value, capture, 'read', exited=False)
        self.assertIs(value['cleanup']['parent_observation_failed'], True)
        assert_primary_unchanged(value)


class ParentWebViewSessionObservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.profile = Path(self.temporary.name) / 'profile'
        self.action_patch = patch('nivram.webview_cleanup._ui_action',
                                  side_effect=lambda callback: callback)
        self.action_patch.start()

    def tearDown(self):
        self.action_patch.stop()
        self.temporary.cleanup()

    def fixture(self, capture=True):
        observations = ParentWebViewCapture() if capture else None
        session = _session(self.profile, observations)
        control = Control(session.storage_path)
        native = Dispatcher()
        native.webview = control
        native.browser = SimpleNamespace(webview=control,
                                         clear_user_data=lambda: None)
        native.FormClosed = Event()
        window = SimpleNamespace(native=native,
                                 events=SimpleNamespace(before_show=Event()))
        session.attach(window)
        window.events.before_show.fire()
        return session, control, native, observations

    def exit(self, control, *, pid=123, folder=None, args=None, sender=None):
        environment = control.CoreWebView2.Environment
        if sender is None:
            sender = environment if folder is None else SimpleNamespace(
                UserDataFolder=str(folder))
        if args is None:
            args = SimpleNamespace(BrowserProcessId=pid,
                                   BrowserProcessExitKind='Normal')
        environment.BrowserProcessExited.fire(sender, args)

    def assertRetained(self, session, result):
        self.assertEqual(result['status'], 'retained')
        self.assertEqual(result['deletion_attempts'], 0)
        self.assertFalse(result['runtime_cache_removed'])
        self.assertTrue(session.storage_path.exists())

    def assertPrivateAbsent(self, packet):
        encoded = json.dumps(packet, sort_keys=True)
        for private in ('private-', 'BrowserProcessId', 'UserDataFolder'):
            self.assertNotIn(private, encoded)
        self.assertNotIn(str(self.profile), packet.values())

    def test_default_receipt_and_optional_capture_keep_cleanup_rules(self):
        default, control, native, _ = self.fixture(capture=False)
        control.on_dispose = lambda: self.exit(control)
        default_result = default.shutdown(.5)
        self.assertEqual(native.calls, 0)
        self.assertEqual(control.calls, 1)
        observed, control, _, capture = self.fixture()
        control.on_dispose = lambda: self.exit(control)
        observed_result = observed.shutdown(.5)
        self.assertEqual(set(default_result), _RECEIPT_KEYS)
        self.assertEqual(set(observed_result), _RECEIPT_KEYS)
        without_identity = lambda value: {key: fact for key, fact in value.items()
                                           if key != 'session_id'}
        self.assertEqual(without_identity(default_result),
                         without_identity(observed_result))
        self.assertEqual(capture.snapshot()['exit_callback_stage'], 'verified')

    def test_matching_callback_inside_dispose_precedes_disposal_return(self):
        session, control, _, capture = self.fixture()
        during = []
        def callback_during_dispose():
            during.append(capture.snapshot())
            self.exit(control)
            during.append(capture.snapshot())
        control.on_dispose = callback_during_dispose
        result = session.shutdown(.5)
        packet = capture.snapshot()
        self.assertEqual(result['status'], 'removed')
        self.assertEqual(control.disposal_count, 1)
        self.assertEqual(during[0]['exit_callback_stage'], 'not_entered')
        self.assertIsNone(during[0]['dispose_return_order'])
        self.assertEqual(during[1]['exit_callback_stage'], 'verified')
        self.assertIsNone(during[1]['dispose_return_order'])
        self.assertLess(packet['dispose_enter_order'], packet['first_callback_order'])
        self.assertLessEqual(packet['first_callback_order'], packet['matching_exit_order'])
        self.assertLess(packet['matching_exit_order'], packet['dispose_return_order'])
        self.assertIs(packet['dispose_on_ui'], True)
        self.assertPrivateAbsent(packet)

    def test_wrong_browser_callback_is_entered_then_rejected(self):
        session, control, _, capture = self.fixture()
        control.on_dispose = lambda: self.exit(control, pid=456)
        result = session.shutdown(.02)
        packet = capture.snapshot()
        self.assertRetained(session, result)
        self.assertFalse(result['collection_exit_observed'])
        self.assertEqual(packet['exit_callback_count'], 1)
        self.assertEqual(packet['exit_callback_stage'], 'rejected')
        self.assertIs(packet['exit_callback_browser_match'], False)
        self.assertIsNone(packet['exit_callback_profile_match'])
        self.assertIsNone(packet['matching_exit_order'])
        self.assertIsNone(packet['exit_callback_failure'])

    def test_wrong_profile_callback_rejects_after_browser_check(self):
        session, control, _, capture = self.fixture()
        control.on_dispose = lambda: self.exit(control, folder=self.profile)
        result = session.shutdown(.02)
        packet = capture.snapshot()
        self.assertRetained(session, result)
        self.assertEqual(packet['exit_callback_stage'], 'rejected')
        self.assertIs(packet['exit_callback_browser_match'], True)
        self.assertIs(packet['exit_callback_profile_match'], False)
        self.assertIsNone(packet['matching_exit_order'])

    def test_identity_read_failures_keep_fixed_stage_without_private_error(self):
        class FailedArgs:
            @property
            def BrowserProcessId(self):
                raise OSError('private-browser-path-and-nonce')

        class FailedSender:
            @property
            def UserDataFolder(self):
                raise OSError('private-profile-path-and-nonce')

        for stage in ('browser', 'profile'):
            with self.subTest(stage=stage):
                session, control, _, capture = self.fixture()
                if stage == 'browser':
                    control.on_dispose = lambda: self.exit(control, args=FailedArgs())
                else:
                    control.on_dispose = lambda: self.exit(control, sender=FailedSender())
                result = session.shutdown(.02)
                packet = capture.snapshot()
                self.assertRetained(session, result)
                self.assertEqual(packet['exit_callback_stage'], 'failed')
                self.assertEqual(packet['exit_callback_failure'],
                                 stage + '_identity_read_failed')
                self.assertIsNone(packet['matching_exit_order'])
                self.assertPrivateAbsent(packet)

    def test_exit_kind_read_failure_does_not_publish_verified_exit(self):
        class FailedKind:
            BrowserProcessId = 123
            @property
            def BrowserProcessExitKind(self):
                raise OSError('private-exit-kind-caption')
        session, control, _, capture = self.fixture()
        control.on_dispose = lambda: self.exit(control, args=FailedKind())
        result = session.shutdown(.02)
        packet = capture.snapshot()
        self.assertRetained(session, result)
        self.assertFalse(result['collection_exit_observed'])
        self.assertEqual(packet['exit_callback_stage'], 'failed')
        self.assertEqual(packet['exit_callback_failure'], 'exit_kind_read_failed')
        self.assertIs(packet['exit_callback_browser_match'], True)
        self.assertIs(packet['exit_callback_profile_match'], True)
        self.assertIsNone(packet['matching_exit_order'])
        self.assertPrivateAbsent(packet)

    def test_receipt_publish_failure_is_separate_from_identity_match(self):
        session, control, _, capture = self.fixture()
        original = session._update
        def fail_publication(**values):
            if values.get('collection_exit_observed'):
                raise OSError('private-receipt-publication-caption')
            original(**values)
        control.on_dispose = lambda: self.exit(control)
        with patch.object(session, '_update', side_effect=fail_publication):
            result = session.shutdown(.02)
        packet = capture.snapshot()
        self.assertRetained(session, result)
        self.assertFalse(result['collection_exit_observed'])
        self.assertEqual(packet['exit_callback_stage'], 'failed')
        self.assertEqual(packet['exit_callback_failure'], 'receipt_publish_failed')
        self.assertIs(packet['exit_callback_browser_match'], True)
        self.assertIs(packet['exit_callback_profile_match'], True)
        self.assertIsNone(packet['matching_exit_order'])
        self.assertPrivateAbsent(packet)

    def test_dispose_raises_after_entry_without_successful_return(self):
        session, control, _, capture = self.fixture()
        def fail():
            raise OSError('private-disposal-caption')
        control.on_dispose = fail
        result = session.shutdown(.02)
        packet = capture.snapshot()
        self.assertRetained(session, result)
        self.assertEqual(result['reason'], 'native_control_disposal_failed')
        self.assertIsNotNone(packet['dispose_enter_order'])
        self.assertIsNone(packet['dispose_return_order'])
        self.assertFalse(result['control_disposed'])
        self.assertPrivateAbsent(packet)

    def test_disposal_dispatch_failure_does_not_invent_ui_entry(self):
        session, control, _, capture = self.fixture()
        control.mode = 'raise'
        result = session.shutdown(.02)
        packet = capture.snapshot()
        self.assertRetained(session, result)
        self.assertEqual(result['reason'], 'native_disposal_dispatch_failed')
        self.assertEqual(packet['dispose_dispatch'], 'failed')
        self.assertIsNone(packet['dispose_enter_order'])
        self.assertIsNone(packet['dispose_return_order'])
        self.assertEqual(control.disposal_count, 0)
        self.assertPrivateAbsent(packet)

    def test_queued_disposal_without_execution_keeps_entry_unknown(self):
        session, control, _, capture = self.fixture()
        control.mode = 'queued'
        result = session.shutdown(.02)
        packet = capture.freeze(_PHASES[0])
        self.assertRetained(session, result)
        self.assertEqual(result['reason'], 'native_disposal_unconfirmed')
        self.assertEqual(packet['dispose_dispatch'], 'queued')
        self.assertIsNone(packet['dispose_enter_order'])
        self.assertIsNone(packet['dispose_return_order'])
        self.assertEqual(control.disposal_count, 0)
        control.pump()
        self.assertEqual(control.disposal_count, 1)
        self.assertEqual(capture.freeze(_PHASES[0]), packet)

    def test_post_dispose_pulse_can_be_queued_without_execution(self):
        session, control, native, capture = self.fixture()
        native.mode = 'queued'
        control.on_dispose = lambda: self.exit(control)
        result = session.shutdown(.5)
        packet = capture.freeze(_PHASES[0])
        self.assertEqual(result['status'], 'removed')
        self.assertEqual(packet['post_dispose_pulse_dispatch'], 'queued')
        self.assertIsNotNone(packet['dispose_return_order'])
        self.assertIsNone(packet['post_dispose_pulse_order'])
        self.assertIsNone(packet['post_dispose_pulse_on_ui'])
        native.pump()
        self.assertIsNotNone(capture.snapshot()['post_dispose_pulse_order'])
        self.assertIs(capture.snapshot()['post_dispose_pulse_on_ui'], True)
        self.assertEqual(capture.freeze(_PHASES[0]), packet)

    def test_pulse_dispatch_failure_does_not_change_verified_cleanup(self):
        session, control, native, capture = self.fixture()
        native.mode = 'raise'
        control.on_dispose = lambda: self.exit(control)
        result = session.shutdown(.5)
        packet = capture.snapshot()
        self.assertEqual(result['status'], 'removed')
        self.assertTrue(result['collection_identity_matched'])
        self.assertEqual(packet['post_dispose_pulse_dispatch'], 'failed')
        self.assertIsNone(packet['post_dispose_pulse_order'])
        self.assertPrivateAbsent(packet)

    def test_captured_form_closed_is_observed_without_runtime_exit_authority(self):
        session, control, native, capture = self.fixture()
        result = session.shutdown(.02)
        packet = capture.freeze(_PHASES[0])
        native.FormClosed.fire(native, None)
        later = capture.snapshot()
        self.assertRetained(session, result)
        self.assertEqual(packet['form_closed_hook_status'], 'installed')
        self.assertIsNone(packet['form_closed_order'])
        self.assertIsNotNone(later['form_closed_order'])
        self.assertEqual(capture.freeze(_PHASES[0]), packet)
        self.assertEqual(later['exit_callback_count'], 0)
        self.assertEqual(later['exit_callback_stage'], 'not_entered')
        self.assertIsNone(later['matching_exit_order'])
        self.assertFalse(session._exit.is_set())
        self.assertFalse(result['collection_exit_observed'])
        self.assertFalse(result['collection_identity_matched'])
        again = session.shutdown(.02)
        self.assertEqual(again['status'], 'retained')
        self.assertEqual(again['deletion_attempts'], 0)
        self.assertEqual(control.disposal_count, 1)
        self.assertTrue(session.storage_path.exists())
        self.assertPrivateAbsent(later)

    def test_late_verified_callback_does_not_change_frozen_timeout_evidence(self):
        session, control, _, capture = self.fixture()
        result = session.shutdown(.02)
        packet = capture.freeze(_PHASES[0])
        self.assertRetained(session, result)
        self.assertEqual(packet['exit_callback_stage'], 'not_entered')
        self.exit(control)
        self.assertEqual(capture.snapshot()['exit_callback_stage'], 'verified')
        self.assertEqual(capture.freeze(_PHASES[0]), packet)
        self.assertFalse(result['collection_exit_observed'])
        again = session.shutdown(.02)
        self.assertEqual(again['status'], 'retained')
        self.assertEqual(again['deletion_attempts'], 0)
        self.assertTrue(session.storage_path.exists())


if __name__ == '__main__':
    unittest.main()
