"""Ownership, input lifetime and event evidence without creating native input."""
import copy
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from nivram import acceptance_pointer_input as api, acceptance_click as click
from test_acceptance_click import FakeWin32, COORDINATES


def driver(backend=None, task=None, queued=None):
    backend = backend or FakeWin32()
    task = task or SimpleNamespace(IsCompleted=True, IsCanceled=False, IsFaulted=False, Result='{}')
    call = Mock(return_value=task)
    control = SimpleNamespace(InvokeRequired=queued is not None, BeginInvoke=None if queued is None else queued.append,
        CoreWebView2=SimpleNamespace(BrowserProcessId=backend.browser_pid, CallDevToolsProtocolMethodAsync=call))
    with patch.object(api, 'handles', return_value=(control,10,11)), patch.object(click, '_browser_pid', return_value=backend.browser_pid), patch.object(click, '_WindowsBackend', return_value=backend):
        value = api.NativePointer(None)
    return value, backend, call


def events(released=True):
    common = dict(trusted=True, inside=True, pointerType='mouse', pointerId=1, shift=False, alt=False, captured=True)
    rows = [dict(common, type='pointerdown', x=200,y=125,at=10,button=0,buttons=1),
            dict(common, type='pointermove', x=205,y=125,at=20,button=-1,buttons=1)]
    if released: rows.append(dict(common, type='pointerup', x=205,y=125,at=30,button=0,buttons=0))
    return dict(overflow=False, events=rows)


class PointerInputTests(unittest.TestCase):
    def test_enter_sends_activation_character_and_complete_release(self):
        down,up=api.key_sequence('enter')
        self.assertEqual((down['type'],down['key'],down['text']),('keyDown','Enter','\r'))
        self.assertEqual((up['type'],up['key'],up['text']),('keyUp','Enter',''))

    def test_fixed_right_click_delivers_owned_press_release_and_refuses_held_left(self):
        pointer,backend,call=driver()
        with pointer:
            pointer.right_click(COORDINATES)
            values=[json.loads(c.args[1]) for c in call.call_args_list]
            self.assertEqual([p['button'] for p in values],['none','right','right'])
            self.assertEqual([p['buttons'] for p in values],[0,2,0])
            pointer.mouse('down',COORDINATES)
            with self.assertRaises(click.NativeClickRejected):pointer.right_click(COORDINATES)
            self.assertEqual(call.call_count,4)

    def test_shift_context_and_numeric_keypad_have_fixed_meanings(self):
        context=api.key_sequence('context-menu')
        self.assertEqual([r['key'] for r in context],['Shift','F10','F10','Shift'])
        self.assertEqual([r['modifiers'] for r in context],[8,8,8,0])
        navigation=api.key_sequence('numpad3-navigation')
        self.assertEqual(navigation[0]['key'],'PageDown');self.assertEqual(navigation[0]['text'],'')
        number=api.key_sequence('numpad3-number')[1]
        self.assertEqual((number['type'],number['key'],number['text'],number['location'],number['isKeypad']),('keyDown','3','3',3,True))
        self.assertEqual(api.key_sequence('digit2')[0]['text'],'2')

    def test_down_move_up_use_css_coordinates_and_actual_button_state(self):
        pointer, backend, call = driver()
        with pointer:
            for kind in ('move','down','move','up'): pointer.mouse(kind, COORDINATES)
            self.assertFalse(pointer.held)
        values = [json.loads(c.args[1]) for c in call.call_args_list]
        self.assertEqual([p['buttons'] for p in values], [0,1,1,0])
        self.assertEqual([p['button'] for p in values], ['none','left','left','left'])
        self.assertEqual([p['force'] for p in values], [0,.5,.5,0])
        self.assertTrue(all(p['x']==200 and p['y']==125 for p in values))
        self.assertTrue(backend.closed); self.assertEqual(backend.messages, [])

    def test_invalid_transitions_and_arbitrary_macros_receive_no_input(self):
        pointer, backend, call = driver()
        with pointer:
            for kind in ('up','wheel','double-click',None):
                with self.assertRaises(click.NativeClickRejected): pointer.mouse(kind, COORDINATES)
            for kind in ('private text','Ctrl+Z','',None):
                with self.assertRaises(click.NativeClickRejected): pointer.key(kind, COORDINATES)
            with self.assertRaises(click.NativeClickRejected): pointer._call('Runtime.evaluate', {}, COORDINATES)
        call.assert_not_called()

    def test_control_and_alt_macros_release_modifiers(self):
        for name, flag in [('undo',2),('redo',2),('select-all',2),('alt-erase',1)]:
            sequence = api.key_sequence(name)
            self.assertEqual([r['type'] for r in sequence], ['rawKeyDown','rawKeyDown','keyUp','keyUp'])
            self.assertEqual([r['modifiers'] for r in sequence], [flag,flag,flag,0])
            self.assertEqual(sequence[0]['key'],sequence[-1]['key'])

    def test_metrics_only_allow_two_fixture_scales_and_clear(self):
        pointer, backend, call = driver()
        with pointer:
            for value in (1,2,None): pointer.metrics(value,COORDINATES)
            for value in (True,False,0,1.0,3,'2'):
                with self.assertRaises(click.NativeClickRejected): pointer.metrics(value,COORDINATES)
            pointer.mouse('down',COORDINATES)
            with self.assertRaises(click.NativeClickRejected): pointer.metrics(1,COORDINATES)
        self.assertEqual(call.call_count,4)
        self.assertEqual(json.loads(call.call_args_list[0].args[1]),dict(width=0,height=0,mobile=False,deviceScaleFactor=1))
        self.assertEqual(call.call_args_list[2].args,('Emulation.clearDeviceMetricsOverride','{}'))

    def test_foreign_hidden_or_replaced_window_rejected(self):
        for change in (lambda b:b.pids.update({12:b.pid+1}),lambda b:setattr(b,'shown',False),lambda b:setattr(b,'current_created',0)):
            pointer, backend, call = driver(); change(backend)
            with pointer:
                with self.assertRaises(click.NativeClickRejected): pointer.mouse('move',COORDINATES)
            call.assert_not_called()

    def test_foreign_sdk_browser_and_bad_points_receive_nothing(self):
        pointer, backend, call = driver()
        with pointer:
            with self.assertRaises(click.NativeClickRejected): pointer.mouse('move',{**COORDINATES,'x':float('nan')})
            pointer.control.CoreWebView2.BrowserProcessId += 1
            with self.assertRaises(click.NativeClickRejected): pointer.mouse('move',COORDINATES)
        call.assert_not_called()

    def test_cancel_fault_timeout_bad_result_poison_driver_and_preserve_held_state(self):
        for field,value in [('IsCanceled',True),('IsFaulted',True),('IsCompleted',False),('Result','{"error":1}')]:
            task=SimpleNamespace(IsCompleted=True,IsCanceled=False,IsFaulted=False,Result='{}');setattr(task,field,value)
            pointer,backend,call=driver(task=task)
            # A real 10 ms deadline can expire before the synchronous fake
            # controller is called. Advance the clock only after dispatch.
            with pointer, patch.object(api.time,'monotonic',side_effect=[0,0,0,1]):
                with self.assertRaises(click.NativeClickRejected): pointer._call('Input.dispatchMouseEvent',{},COORDINATES,.01)
                self.assertTrue(pointer.failed);self.assertFalse(pointer.held)
                with self.assertRaises(click.NativeClickRejected):pointer.mouse('move',COORDINATES)
            self.assertEqual(call.call_count,1)

    def test_late_ui_callback_cannot_start_after_deadline(self):
        queued=[];pointer,backend,call=driver(queued=queued)
        with pointer,patch.dict(sys.modules,{'System':SimpleNamespace(Action=lambda f:f)}):
            with self.assertRaises(click.NativeClickRejected):pointer._call('Input.dispatchMouseEvent',{},COORDINATES,.01)
            queued[0]();call.assert_not_called()

    def test_route_replacement_during_delivery_poison_driver(self):
        pointer,backend,call=driver()
        def change(*args):
            backend.descendants[11]=13
            return SimpleNamespace(IsCompleted=True,IsCanceled=False,IsFaulted=False,Result='{}')
        call.side_effect=change
        with pointer:
            with self.assertRaises(click.NativeClickRejected):pointer.mouse('down',COORDINATES)
            self.assertTrue(pointer.failed);self.assertFalse(pointer.held)

    def test_closed_driver_and_bad_deadlines_never_dispatch(self):
        pointer,backend,call=driver()
        for timeout in (True,0,-1,6,float('nan')):
            with self.assertRaises(click.NativeClickRejected):pointer._call('Input.dispatchMouseEvent',{},COORDINATES,timeout)
        pointer.close()
        with self.assertRaises(click.NativeClickRejected):pointer.mouse('move',COORDINATES)
        call.assert_not_called()

    def test_gesture_evidence_requires_trust_capture_geometry_sequence_and_release(self):
        start=COORDINATES;end={**start,'x':205}
        self.assertEqual(len(api.drag_events(events(),start,end,released=True)),3)
        self.assertEqual(len(api.drag_events(events(False),start,end,released=False)),2)
        mutations=[lambda v:v.update(overflow=True),lambda v:v['events'][0].update(trusted=False),
            lambda v:v['events'][0].update(captured=False),lambda v:v['events'][1].update(x=204),
            lambda v:v['events'][1].update(pointerId=2),lambda v:v['events'][1].update(at=float('nan')),
            lambda v:v['events'][1].update(buttons=True),lambda v:v['events'][2].update(at=9),
            lambda v:v['events'].pop(),lambda v:v['events'].append(copy.deepcopy(v['events'][0]))]
        for mutate in mutations:
            value=events();mutate(value)
            with self.assertRaises(click.NativeClickRejected):api.drag_events(value,start,end,released=True)


if __name__=='__main__':unittest.main()
