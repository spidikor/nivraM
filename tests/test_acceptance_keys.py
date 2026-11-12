"""Fixed native input ownership and untrusted-event rejection without UI input."""
import copy
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from nivram import acceptance_keys as keys, acceptance_click as click
from test_acceptance_click import FakeWin32, COORDINATES


def events(kind):
    return [dict(key=name, trusted=True, target=True, repeat=False, modified=False, at=100+i*20)
            for i, name in enumerate(keys.SEQUENCES[kind])]


def window():
    control = SimpleNamespace(GetType=lambda: SimpleNamespace(FullName='Microsoft.Web.WebView2.WinForms.WebView2'),
        IsHandleCreated=True, IsDisposed=False, Handle=SimpleNamespace(ToInt64=lambda: 11))
    return SimpleNamespace(native=SimpleNamespace(webview=control, Handle=SimpleNamespace(ToInt64=lambda: 10)))


class ElementKeyOwnershipTests(unittest.TestCase):
    def test_timed_pair_delivers_c_hover_l_without_evaluator_io_between_events(self):
        backend = FakeWin32(); trace = []; send = backend.send
        def tracked(*args): trace.append(('send', args[2])); return send(*args)
        backend.send = tracked
        values = iter([dict(revision=2, point=COORDINATES), True, COORDINATES, True, events('cl'),
                       dict(trusted=True, mouse=True, buttons=0, at=110), True, True])
        def evaluate(*args, **kwargs): trace.append(('evaluate', kwargs['stage'])); return next(values)
        def hover(*args): trace.append(('hover', 'empty'))
        with patch.object(click, '_WindowsBackend', return_value=backend), patch.object(click, '_browser_pid', return_value=backend.browser_pid), patch.object(keys, 'sdk_hover', side_effect=hover):
            result = keys.pointer_pair(window(), evaluate, Mock(), lambda: 10)
        start = trace.index(('send', 0x43)); end = len(trace)-1-trace[::-1].index(('send', 0x4C))
        self.assertEqual(trace[start:end+1], [('send', 0x43), ('send', 0x43), ('hover', 'empty'), ('send', 0x4C), ('send', 0x4C)])
        self.assertEqual([r['kind'] for r in result], ['c', 'empty', 'l'])
        self.assertTrue(backend.closed)

    def test_failed_mid_sequence_hover_never_sends_l_and_still_cleans_up(self):
        backend = FakeWin32(); evaluate = Mock(side_effect=[dict(revision=2, point=COORDINATES), True, COORDINATES, True, True, True])
        with patch.object(click, '_WindowsBackend', return_value=backend), patch.object(click, '_browser_pid', return_value=backend.browser_pid), patch.object(keys, 'sdk_hover', side_effect=click.NativeClickRejected('native_click_message_indeterminate')):
            with self.assertRaises(click.NativeClickRejected): keys.pointer_pair(window(), evaluate, Mock(), lambda: 10)
        self.assertEqual([r[2] for r in backend.messages], [0x43, 0x43]); self.assertTrue(backend.closed)
        self.assertEqual([c.kwargs['stage'] for c in evaluate.call_args_list[-2:]], ['element-pair-cleanup']*2)

    def test_timed_pair_rejects_untrusted_hover_or_wrong_key_target(self):
        for bad_hover in (True, False):
            backend = FakeWin32(); observed = events('cl')
            if not bad_hover: observed[1]['target'] = False
            evaluate = Mock(side_effect=[dict(revision=2, point=COORDINATES), True, COORDINATES, True, observed,
                dict(trusted=not bad_hover, mouse=True, buttons=0, at=110), True, True])
            with patch.object(click, '_WindowsBackend', return_value=backend), patch.object(click, '_browser_pid', return_value=backend.browser_pid), patch.object(keys, 'sdk_hover'):
                with self.assertRaises(click.NativeClickRejected): keys.pointer_pair(window(), evaluate, Mock(), lambda: 10)
            self.assertTrue(backend.closed)

    def test_failure_diagnostics_filter_arbitrary_keys_nonfinite_values_and_extra_data(self):
        value = [dict(key='private text', trusted=1, at=float('nan'), arbitrary='private data')]*8
        result = keys.key_observation(value)
        self.assertEqual(result['count'], 8); self.assertEqual(len(result['events']), 4)
        self.assertTrue(all(r['key'] == 'other' and r['trusted'] is None and r['at'] is None for r in result['events']))
        self.assertNotIn('private', json.dumps(result)); self.assertNotIn('arbitrary', json.dumps(result))

    def test_sdk_hover_calls_only_owned_controller_fixed_mouse_move(self):
        backend = FakeWin32()
        task = SimpleNamespace(IsCompleted=True, IsCanceled=False, IsFaulted=False, Result='{}')
        call = Mock(return_value=task)
        control = SimpleNamespace(InvokeRequired=False, CoreWebView2=SimpleNamespace(
            BrowserProcessId=backend.browser_pid, CallDevToolsProtocolMethodAsync=call))
        self.assertEqual(keys.sdk_hover(control, backend, 10, 11, COORDINATES),
                         {'completed': True, 'route': 'owned-webview2-sdk-hover'})
        self.assertEqual(call.call_args.args[0], 'Input.dispatchMouseEvent')
        self.assertEqual(json.loads(call.call_args.args[1]), dict(type='mouseMoved', x=200, y=125,
            button='none', buttons=0, modifiers=0, pointerType='mouse'))
        self.assertEqual(backend.messages, [])

    def test_sdk_rejects_foreign_controller_and_invalid_deadline_before_dispatch(self):
        backend = FakeWin32(); call = Mock()
        control = SimpleNamespace(InvokeRequired=False, CoreWebView2=SimpleNamespace(
            BrowserProcessId=backend.browser_pid+1, CallDevToolsProtocolMethodAsync=call))
        with self.assertRaises(click.NativeClickRejected): keys.sdk_hover(control, backend, 10, 11, COORDINATES)
        call.assert_not_called()
        for timeout in (True, 0, 6, float('nan')):
            with self.assertRaises(click.NativeClickRejected): keys.sdk_hover(None, None, 10, 11, COORDINATES, timeout)

    def test_sdk_fault_cancel_timeout_or_nonempty_response_cannot_pass(self):
        for field, value in [('IsFaulted', True), ('IsCanceled', True), ('IsCompleted', False), ('Result', '{"error":1}')]:
            backend = FakeWin32()
            task = SimpleNamespace(IsCompleted=True, IsCanceled=False, IsFaulted=False, Result='{}')
            setattr(task, field, value)
            control = SimpleNamespace(InvokeRequired=False, CoreWebView2=SimpleNamespace(
                BrowserProcessId=backend.browser_pid, CallDevToolsProtocolMethodAsync=Mock(return_value=task)))
            with self.assertRaises(click.NativeClickRejected): keys.sdk_hover(control, backend, 10, 11, COORDINATES, .03)

    def test_delayed_ui_callback_cannot_dispatch_after_timeout(self):
        backend = FakeWin32(); queued = []; call = Mock()
        control = SimpleNamespace(InvokeRequired=True, BeginInvoke=queued.append, CoreWebView2=SimpleNamespace(
            BrowserProcessId=backend.browser_pid, CallDevToolsProtocolMethodAsync=call))
        with patch.dict(sys.modules, {'System': SimpleNamespace(Action=lambda function: function)}):
            with self.assertRaises(click.NativeClickRejected): keys.sdk_hover(control, backend, 10, 11, COORDINATES, .01)
        self.assertEqual(len(queued), 1); queued[0](); call.assert_not_called()

    def test_sdk_route_replacement_after_dispatch_is_not_success(self):
        backend = FakeWin32()
        def changed(*args):
            backend.descendants[11] = 13
            return SimpleNamespace(IsCompleted=True, IsCanceled=False, IsFaulted=False, Result='{}')
        control = SimpleNamespace(InvokeRequired=False, CoreWebView2=SimpleNamespace(
            BrowserProcessId=backend.browser_pid, CallDevToolsProtocolMethodAsync=changed))
        with self.assertRaises(click.NativeClickRejected): keys.sdk_hover(control, backend, 10, 11, COORDINATES)

    def test_fixed_pair_uses_bounded_owned_messages_and_no_text_character(self):
        backend = FakeWin32(); stable = Mock()
        with patch.object(keys.time, 'sleep'):
            result = keys.send_keys(backend, 10, 11, COORDINATES, 'cl', stable)
        self.assertEqual(backend.messages, [(12, 0x100, 0x43, 0x2E0001, 1000),
            (12, 0x101, 0x43, 0xC02E0001, 1000), (12, 0x100, 0x4C, 0x260001, 1000),
            (12, 0x101, 0x4C, 0xC0260001, 1000)])
        self.assertEqual(stable.call_count, 2)
        self.assertIs(result['ownershipVerified'], True)

    def test_text_sequence_emits_backspace_n_three_character_messages(self):
        backend = FakeWin32()
        with patch.object(keys.time, 'sleep'):
            keys.send_keys(backend, 10, 11, COORDINATES, 'text', Mock())
        self.assertEqual([r[1] for r in backend.messages], [0x100, 0x102, 0x101]*3)
        self.assertEqual([r[2] for r in backend.messages if r[1] == 0x102], [8, 0x6E, 0x33])
        self.assertTrue(all(r[0] == 12 and r[4] == 1000 for r in backend.messages))

    def test_arbitrary_keys_rejected_before_window_or_backend_access(self):
        for bad in ('Ctrl+Z', 'a', '', ['cl'], None):
            with self.assertRaises(click.NativeClickRejected): keys.keys(None, None, None, bad)
            with self.assertRaises(click.NativeClickRejected): keys.send_keys(None, 10, 11, None, bad, None)

    def test_foreign_replaced_or_hidden_window_receives_nothing(self):
        for change in (lambda b: b.pids.update({12: b.pid+1}), lambda b: setattr(b, 'current_created', 0),
                       lambda b: setattr(b, 'shown', False), lambda b: b.roots.update({12: 99})):
            backend = FakeWin32(); change(backend)
            with self.assertRaises(click.NativeClickRejected): keys.send_keys(backend, 10, 11, COORDINATES, 'c', Mock())
            self.assertEqual(backend.messages, [])

    def test_replaced_route_or_viewport_before_keyup_receives_no_keyup(self):
        for change in (lambda b: b.descendants.update({11: 13}), lambda b: setattr(b, 'size', (900, 500)),
                       lambda b: b.pids.update({12: b.pid+1})):
            backend = FakeWin32(); original = backend.send
            def changed(*args):
                result = original(*args); change(backend); return result
            backend.send = changed
            with self.assertRaises(click.NativeClickRejected): keys.send_keys(backend, 10, 11, COORDINATES, 'c', Mock())
            self.assertEqual(len(backend.messages), 1)

    def test_bad_coordinates_failed_delivery_and_lost_focus_reject(self):
        backend = FakeWin32()
        with self.assertRaises(click.NativeClickRejected): keys.send_keys(backend, 10, 11, {**COORDINATES, 'x': -1}, 'c', Mock())
        self.assertEqual(backend.messages, [])
        backend.delivery = False
        with self.assertRaises(click.NativeClickRejected): keys.send_keys(backend, 10, 11, COORDINATES, 'c', Mock())
        self.assertEqual(len(backend.messages), 1)
        backend = FakeWin32()
        with self.assertRaises(RuntimeError): keys.send_keys(backend, 10, 11, COORDINATES, 'c', Mock(side_effect=RuntimeError()))
        self.assertEqual(backend.messages, [])

    def test_keys_require_trusted_observation_and_always_close_listener_and_backend(self):
        for observed in (events('c'), [], [dict(events('c')[0], trusted=False)]):
            backend = FakeWin32(); evaluate = Mock(side_effect=[COORDINATES, True, observed, True])
            with patch.object(click, '_WindowsBackend', return_value=backend), patch.object(click, '_browser_pid', return_value=backend.browser_pid):
                if observed == events('c'):
                    self.assertEqual(keys.keys(window(), evaluate, Mock(), 'c')['status'], 'passed')
                else:
                    with self.assertRaises(click.NativeClickRejected): keys.keys(window(), evaluate, Mock(), 'c')
            self.assertTrue(backend.closed)
            self.assertEqual(evaluate.call_args_list[-1].kwargs['stage'], 'element-keys-cleanup')

    def test_timeout_still_cleans_up(self):
        backend = FakeWin32(); evaluate = Mock(side_effect=[COORDINATES, True, True])
        with patch.object(click, '_WindowsBackend', return_value=backend), patch.object(click, '_browser_pid', return_value=backend.browser_pid):
            with self.assertRaises(TimeoutError): keys.keys(window(), evaluate, Mock(side_effect=TimeoutError()), 'c')
        self.assertTrue(backend.closed)
        self.assertEqual(evaluate.call_args_list[-1].kwargs['stage'], 'element-keys-cleanup')

    def test_extra_missing_wrong_modified_repeated_and_untyped_events_fail(self):
        self.assertTrue(keys.observed_keys(events('cl'), 'cl'))
        for bad in (None, {}, [], events('cl')[:1], events('cl')*2, list(reversed(events('cl')))):
            self.assertFalse(keys.observed_keys(bad, 'cl'))
        for key, bad in [('trusted', 1), ('target', False), ('repeat', True), ('modified', True),
                         ('at', True), ('at', float('nan')), ('at', float('inf')), ('at', -1), ('key', 'x')]:
            value = copy.deepcopy(events('cl')); value[0][key] = bad
            self.assertFalse(keys.observed_keys(value, 'cl'), (key, bad))


if __name__ == '__main__': unittest.main()
