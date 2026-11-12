"""Owned-window click policy with a Win32 stand-in; no native input is sent."""
import copy
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nivram import acceptance_click as click


class FakeWin32:
    def __init__(self):
        self.pid = os.getpid()
        self.created = self.current_created = 42
        self.running = True
        self.browser_pid = self.pid + 100000
        self.browser_created = self.browser_current_created = 84
        self.browser_running = True
        self.pids = {10: self.pid, 11: self.pid, 12: self.pid, 13: self.pid}
        self.roots = {10: 10, 11: 10, 12: 10, 13: 10}
        self.descendants = {11: 12}
        self.mapping = (0, 0)
        self.shown = self.allowed = self.is_child = True
        self.size = (1600, 1000)
        self.messages = []
        self.after_down = None
        self.delivery = True
        self.closed = False

    def alive(self):
        return self.running

    def creation(self):
        return self.current_created

    def window(self, hwnd):
        return hwnd in self.pids

    def window_pid(self, hwnd):
        return self.pids.get(hwnd)

    def visible(self, hwnd):
        return self.shown

    def enabled(self, hwnd):
        return self.allowed

    def root(self, hwnd):
        return self.roots.get(hwnd)

    def child(self, owner, control):
        return self.is_child

    def client_size(self, hwnd):
        return self.size

    def child_from_point(self, hwnd, point):
        return self.descendants.get(hwnd, hwnd)

    def map_point(self, source, target, point):
        return point[0] + self.mapping[0], point[1] + self.mapping[1]

    def process_pinned(self, pid):
        if pid == self.pid:
            return self.alive() and self.creation() == self.created
        return pid == self.browser_pid and self.browser_running and \
            self.browser_created == self.browser_current_created

    def send(self, hwnd, message, buttons, parameter, timeout):
        self.messages.append((hwnd, message, buttons, parameter, timeout))
        if message == 0x0201 and self.after_down:
            self.after_down(self)
        return self.delivery

    def close(self):
        self.closed = True


COORDINATES = {'x': 200, 'y': 125, 'viewportWidth': 800, 'viewportHeight': 500}


class AcceptanceNativeClickOwnershipTests(unittest.TestCase):
    def test_only_exact_owned_point_descendant_receives_bounded_down_and_up(self):
        backend = FakeWin32()
        with patch.object(click.time, 'sleep') as sleep:
            result = click._send_pair(backend, 10, 11, COORDINATES)
        self.assertEqual(backend.messages,
                         [(12, 0x0201, 1, (250 << 16) | 400, 1000),
                          (12, 0x0202, 0, (250 << 16) | 400, 1000)])
        sleep.assert_called_once_with(.02)
        self.assertEqual((result['clientX'], result['clientY']), (400, 250))
        self.assertEqual(result['route'], 'owned-webview-point-descendant')
        self.assertEqual(result['descendantDepth'], 1)
        self.assertIs(result['sameProcessOwnership'], True)
        self.assertIs(result['ownershipVerified'], True)
        self.assertIs(result['sdkBrowserProcessPinned'], False)

    def test_identity_and_ancestry_failures_reject_before_any_message(self):
        cases = (
            ('other-process', lambda b: setattr(b, 'pid', b.pid + 1)),
            ('creation-changed', lambda b: setattr(b, 'current_created', 43)),
            ('process-exited', lambda b: setattr(b, 'running', False)),
            ('foreign-owner', lambda b: b.pids.update({10: b.pid + 1})),
            ('foreign-control', lambda b: b.pids.update({11: b.pid + 1})),
            ('owner-root-changed', lambda b: b.roots.update({10: 12})),
            ('control-root-changed', lambda b: b.roots.update({11: 12})),
            ('not-descendant', lambda b: setattr(b, 'is_child', False)),
            ('hidden', lambda b: setattr(b, 'shown', False)),
            ('disabled', lambda b: setattr(b, 'allowed', False)),
        )
        for name, change in cases:
            with self.subTest(name=name):
                backend = FakeWin32()
                change(backend)
                with self.assertRaises(click.NativeClickRejected):
                    click._send_pair(backend, 10, 11, COORDINATES)
                self.assertEqual(backend.messages, [])

    def test_invalid_geometry_never_sends_a_message(self):
        cases = (
            ({**COORDINATES, 'x': True}, (1600, 1000)),
            ({**COORDINATES, 'x': float('nan')}, (1600, 1000)),
            ({**COORDINATES, 'y': -1}, (1600, 1000)),
            ({**COORDINATES, 'x': 800}, (1600, 1000)),
            ({**COORDINATES, 'viewportWidth': 0}, (1600, 1000)),
            ({**COORDINATES, 'extra': 'unsupported'}, (1600, 1000)),
            (COORDINATES, (32768, 1000)),
            (COORDINATES, (1600, 500)),
        )
        for coordinates, size in cases:
            with self.subTest(coordinates=coordinates, size=size):
                backend = FakeWin32()
                backend.size = size
                with self.assertRaises(click.NativeClickRejected):
                    click._send_pair(backend, 10, 11, copy.deepcopy(coordinates))
                self.assertEqual(backend.messages, [])

    def test_ownership_rechecked_before_up_never_targets_a_replacement(self):
        backend = FakeWin32()
        backend.after_down = lambda b: b.pids.update({11: b.pid + 1})
        with patch.object(click.time, 'sleep'), self.assertRaises(click.NativeClickRejected):
            click._send_pair(backend, 10, 11, COORDINATES)
        self.assertEqual(len(backend.messages), 1)
        self.assertEqual(backend.messages[0][:3], (12, 0x0201, 1))

    def test_foreign_target_pid_is_rejected_before_any_message(self):
        backend = FakeWin32()
        backend.pids[12] = backend.browser_pid + 1
        with self.assertRaisesRegex(click.NativeClickRejected, 'foreign_target_process'):
            click._send_pair(backend, 10, 11, COORDINATES)
        self.assertEqual(backend.messages, [])

    def test_exact_sdk_browser_pid_requires_live_unchanged_pin(self):
        backend = FakeWin32()
        backend.pids[12] = backend.browser_pid
        with patch.object(click.time, 'sleep'):
            result = click._send_pair(backend, 10, 11, COORDINATES)
        self.assertEqual(len(backend.messages), 2)
        self.assertIs(result['sameProcessOwnership'], False)
        self.assertIs(result['ownershipVerified'], True)
        self.assertIs(result['sdkBrowserProcessPinned'], True)
        for name in ('browser_running', 'browser_current_created'):
            with self.subTest(name=name):
                backend = FakeWin32()
                backend.pids[12] = backend.browser_pid
                setattr(backend, name, False if name == 'browser_running' else 85)
                with self.assertRaisesRegex(click.NativeClickRejected, 'target_process_changed'):
                    click._send_pair(backend, 10, 11, COORDINATES)
                self.assertEqual(backend.messages, [])

    def test_target_replacement_after_down_never_receives_up(self):
        backend = FakeWin32()
        backend.after_down = lambda b: b.descendants.update({11: 13})
        with patch.object(click.time, 'sleep'), \
                self.assertRaisesRegex(click.NativeClickRejected, 'descendant_route_changed'):
            click._send_pair(backend, 10, 11, COORDINATES)
        self.assertEqual(len(backend.messages), 1)
        self.assertEqual(backend.messages[0][:3], (12, 0x0201, 1))

    def test_target_process_and_ancestry_rechecked_before_up(self):
        cases = (
            ('foreign-target', lambda b: b.pids.update({12: b.browser_pid + 1})),
            ('target-root', lambda b: b.roots.update({12: 13})),
            ('browser-exit', lambda b: setattr(b, 'browser_running', False)),
            ('browser-creation', lambda b: setattr(b, 'browser_current_created', 85)),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                backend = FakeWin32()
                backend.pids[12] = backend.browser_pid
                backend.after_down = mutate
                with patch.object(click.time, 'sleep'), self.assertRaises(click.NativeClickRejected):
                    click._send_pair(backend, 10, 11, COORDINATES)
                self.assertEqual(len(backend.messages), 1)

    def test_no_descendant_does_not_fall_back_to_host(self):
        backend = FakeWin32()
        backend.descendants.clear()
        with self.assertRaisesRegex(click.NativeClickRejected, 'descendant_route_unavailable'):
            click._send_pair(backend, 10, 11, COORDINATES)
        self.assertEqual(backend.messages, [])

    def test_client_point_is_mapped_and_bounded_in_target(self):
        backend = FakeWin32()
        backend.mapping = (-100, -50)
        with patch.object(click.time, 'sleep'):
            result = click._send_pair(backend, 10, 11, COORDINATES)
        self.assertEqual((result['clientX'], result['clientY']), (300, 200))
        self.assertEqual(backend.messages[0][3], (200 << 16) | 300)
        backend = FakeWin32()
        backend.mapping = (-401, 0)
        with self.assertRaisesRegex(click.NativeClickRejected, 'coordinate_mapping_failed'):
            click._send_pair(backend, 10, 11, COORDINATES)
        self.assertEqual(backend.messages, [])

    def test_depth_limit_and_cycle_reject_without_messages(self):
        backend = FakeWin32()
        for hwnd in range(12, 29):
            backend.pids[hwnd] = backend.pid
            backend.roots[hwnd] = 10
            backend.descendants[hwnd] = hwnd + 1
        with self.assertRaisesRegex(click.NativeClickRejected, 'descendant_depth_exceeded'):
            click._send_pair(backend, 10, 11, COORDINATES)
        self.assertEqual(backend.messages, [])
        backend = FakeWin32()
        backend.descendants[12] = 11
        with self.assertRaisesRegex(click.NativeClickRejected, 'descendant_route_changed'):
            click._send_pair(backend, 10, 11, COORDINATES)
        self.assertEqual(backend.messages, [])

    def test_client_resize_between_messages_rejects_old_coordinates(self):
        backend = FakeWin32()
        backend.after_down = lambda b: setattr(b, 'size', (1500, 1000))
        with patch.object(click.time, 'sleep'), self.assertRaises(click.NativeClickRejected):
            click._send_pair(backend, 10, 11, COORDINATES)
        self.assertEqual(len(backend.messages), 1)

    def test_message_timeout_is_indeterminate_and_cannot_pass(self):
        backend = FakeWin32()
        backend.delivery = False
        with self.assertRaisesRegex(click.NativeClickRejected, 'native_click_message_indeterminate'):
            click._send_pair(backend, 10, 11, COORDINATES)
        self.assertEqual(len(backend.messages), 1)


class AcceptanceNativeClickEvidenceTests(unittest.TestCase):
    def make_window(self):
        handle = lambda value: SimpleNamespace(ToInt64=lambda: value)
        control = SimpleNamespace(IsHandleCreated=True, IsDisposed=False, Handle=handle(11),
            GetType=lambda: SimpleNamespace(FullName='Microsoft.Web.WebView2.WinForms.WebView2'))
        return SimpleNamespace(native=SimpleNamespace(Handle=handle(10), webview=control))

    def evaluate(self, observation, stages):
        def run(expression, *, timeout, stage):
            stages.append(stage)
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 10 if stage in
                                 ('native-click-observation', 'native-click-listener-cleanup') else 2)
            self.assertNotIn('__BUTTON__', expression)
            self.assertNotIn('__NONCE__', expression)
            if stage == 'native-click-target':
                return dict(COORDINATES)
            if stage == 'native-click-target-stable':
                return True
            if stage == 'native-click-observation':
                return observation
            return True
        return run

    def test_native_message_delivery_requires_both_trust_and_activation(self):
        for observation in ({'trusted': False, 'activation': True},
                            {'trusted': True, 'activation': False},
                            {'trusted': 1, 'activation': True},
                            {'trusted': True, 'activation': True, 'unsupported': True}):
            with self.subTest(observation=observation):
                backend, stages = FakeWin32(), []
                with patch.object(click, '_WindowsBackend', return_value=backend), \
                        patch.object(click, '_browser_pid', return_value=backend.browser_pid), \
                        patch.object(click.time, 'sleep'), \
                        self.assertRaisesRegex(click.NativeClickRejected, 'trusted_activation_unconfirmed'):
                    click.click_png_button(self.make_window(), self.evaluate(observation, stages), 'main')
                self.assertEqual(len(backend.messages), 2)
                self.assertTrue(backend.closed)
                self.assertEqual(stages[-1], 'native-click-listener-cleanup')

    def test_trusted_gesture_returns_typed_metadata_without_download_claim(self):
        for kind in ('main', 'preview', 'batch', 'batch-cancel', 'edit-import', 'edit-load',
                     'edit-rotate-cw', 'edit-undo', 'edit-redo', 'edit-save-as', 'edit-open'):
            with self.subTest(kind=kind):
                backend, stages = FakeWin32(), []
                with patch.object(click, '_WindowsBackend', return_value=backend), \
                        patch.object(click, '_browser_pid', return_value=backend.browser_pid), \
                        patch.object(click.time, 'sleep'):
                    result = click.click_png_button(self.make_window(),
                        self.evaluate({'trusted': True, 'activation': True}, stages), kind)
                self.assertEqual(result['status'], 'passed')
                self.assertIs(result['isTrusted'], True)
                self.assertIs(result['userActivation'], True)
                self.assertEqual(result['kind'], kind)
                self.assertNotIn('download', result)
                self.assertTrue(backend.closed)

    def test_poll_stops_before_short_timeout_and_uses_bounded_sleep(self):
        backend, stages = FakeWin32(), []
        # Initial deadline10, a valid .3s read, then .2s: no near-zero submission.
        with patch.object(click, '_WindowsBackend', return_value=backend), \
                patch.object(click, '_browser_pid', return_value=backend.browser_pid), \
                patch.object(click.time, 'monotonic', side_effect=(0, 9.7, 9.8)), \
                patch.object(click.time, 'sleep') as sleep, \
                self.assertRaisesRegex(click.NativeClickRejected, 'trusted_event_unobserved'):
            click.click_png_button(self.make_window(), self.evaluate(None, stages), 'main')
        self.assertEqual(stages.count('native-click-observation'), 1)
        self.assertEqual(sleep.call_args_list[-1].args, (.1,))
        self.assertTrue(backend.closed)

    def test_sdk_pid_getter_rejects_bool_zero_and_missing_core(self):
        for core in (SimpleNamespace(BrowserProcessId=True), SimpleNamespace(BrowserProcessId=0), None):
            with self.subTest(core=core):
                control = SimpleNamespace(InvokeRequired=False, CoreWebView2=core)
                with self.assertRaisesRegex(click.NativeClickRejected, 'browser_pid_unavailable'):
                    click._browser_pid(control)
        self.assertEqual(click._browser_pid(SimpleNamespace(InvokeRequired=False,
                         CoreWebView2=SimpleNamespace(BrowserProcessId=123))), 123)

    def test_unclassified_error_text_is_not_exposed(self):
        error = click.NativeClickRejected('private arbitrary exception text')
        self.assertEqual(str(error), 'native_click_unclassified')
        self.assertEqual(error.code, 'native_click_unclassified')

    def test_unknown_button_kind_rejects_before_window_or_backend_access(self):
        with patch.object(click, '_WindowsBackend') as backend:
            with self.assertRaisesRegex(click.NativeClickRejected, 'kind_unrecognized'):
                click.click_png_button(None, None, '#arbitrary-control')
        backend.assert_not_called()


if __name__ == '__main__':
    unittest.main()
