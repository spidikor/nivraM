"""Bounded input for the isolated native pointer fixture; no public bridge API.

Only owned WebView2 input and page-local device metrics are available. Actual
DOM event trust, capture, coordinates and document effects need separate checks.
No global cursor, foreground, keyboard state, inspector or debugging port changes.
"""
from __future__ import annotations

import json
import math
import threading
import time

from . import acceptance_click as click
from .acceptance_keys import handles, route

METHODS = frozenset({'Input.dispatchMouseEvent', 'Input.dispatchKeyEvent',
                     'Emulation.setDeviceMetricsOverride', 'Emulation.clearDeviceMetricsOverride'})
KEYS = {
    'escape': ('Escape', 'Escape', 27, 0), 'delete': ('Delete', 'Delete', 46, 0),
    'undo': ('z', 'KeyZ', 90, 2), 'redo': ('y', 'KeyY', 89, 2),
    'select-all': ('a', 'KeyA', 65, 2), 'select-tool': ('v', 'KeyV', 86, 0),
    'erase-tool': ('e', 'KeyE', 69, 0), 'bond-tool': ('b', 'KeyB', 66, 0),
    'alt-erase': ('e', 'KeyE', 69, 1),
    'home': ('Home', 'Home', 36, 0), 'down': ('ArrowDown', 'ArrowDown', 40, 0),
    'enter': ('Enter', 'Enter', 13, 0),
    'up': ('ArrowUp','ArrowUp',38,0), 'left': ('ArrowLeft','ArrowLeft',37,0),
    'right': ('ArrowRight','ArrowRight',39,0), 'end': ('End','End',35,0),
    'tab': ('Tab','Tab',9,0), 'shift-tab': ('Tab','Tab',9,8),
    'context-menu': ('F10','F10',121,8), 'backspace': ('Backspace','Backspace',8,0),
    'digit1': ('1','Digit1',49,0), 'digit2': ('2','Digit2',50,0), 'digit3': ('3','Digit3',51,0),
    'numpad3-navigation': ('PageDown','Numpad3',34,0), 'numpad3-number': ('3','Numpad3',99,8),
}


def require(condition, code='native_click_message_indeterminate'):
    click._require(condition, code)


def mouse_payload(kind, coordinates, held, shift=False):
    require(type(kind) is str and kind in ('move', 'down', 'up'), 'native_click_kind_unrecognized')
    require(type(held) is bool and type(shift) is bool, 'native_click_kind_unrecognized')
    require(not (kind == 'down' and held) and not (kind == 'up' and not held), 'native_click_kind_unrecognized')
    next_held = kind == 'down' or (kind == 'move' and held)
    return dict(type={'move':'mouseMoved', 'down':'mousePressed', 'up':'mouseReleased'}[kind],
        x=coordinates['x'], y=coordinates['y'], button='none' if kind == 'move' and not held else 'left',
        buttons=int(next_held), force=.5 if next_held else 0, clickCount=0 if kind == 'move' else 1,
        modifiers=8 if shift else 0, pointerType='mouse')


def key_sequence(kind):
    require(type(kind) is str and kind in KEYS, 'native_click_kind_unrecognized')
    key, code, virtual, modifier = KEYS[kind]
    def event(event_type, key, code, virtual, modifiers, text=''):
        return dict(type=event_type, key=key, code=code, windowsVirtualKeyCode=virtual,
                    nativeVirtualKeyCode=virtual, modifiers=modifiers, autoRepeat=False, text=text)
    items = []
    if modifier:
        name, position, vk = {2:('Control','ControlLeft',17),1:('Alt','AltLeft',18),8:('Shift','ShiftLeft',16)}[modifier]
        items.append(event('rawKeyDown', name, position, vk, modifier))
    # Enter needs its character-bearing keyDown for native HTML activation,
    # just as number input needs text. rawKeyDown alone delivers no keypress.
    text='\r' if kind=='enter' else key if kind in ('digit1','digit2','digit3','numpad3-number') else ''
    down=event('keyDown' if text else 'rawKeyDown',key,code,virtual,modifier,text)
    up=event('keyUp',key,code,virtual,modifier)
    if code=='Numpad3':
        for value in (down,up):value.update(location=3,isKeypad=True)
    items.extend((down,up))
    if modifier: items.append(event('keyUp', name, position, vk, 0))
    return items


def metrics_command(value):
    require(value is None or type(value) is int and value in (1, 2), 'native_click_kind_unrecognized')
    if value is None: return 'Emulation.clearDeviceMetricsOverride', {}
    return 'Emulation.setDeviceMetricsOverride', dict(width=0, height=0, mobile=False, deviceScaleFactor=value)


class NativePointer:
    def __init__(self, window):
        self.control, self.owner, self.handle = handles(window)
        self.backend = click._WindowsBackend(click._browser_pid(self.control))
        self.held = self.failed = self.closed = False

    def close(self):
        if not self.closed:
            self.closed = True
            self.backend.close()

    def __enter__(self): return self
    def __exit__(self, *unused): self.close()

    def _call(self, method, parameters, coordinates, timeout=2):
        require(not self.closed and not self.failed)
        require(type(method) is str and method in METHODS, 'native_click_kind_unrecognized')
        require(type(parameters) is dict)
        require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 5)
        fixed = route(self.backend, self.owner, self.handle, coordinates)
        serialized = json.dumps(parameters, allow_nan=False)
        deadline = time.monotonic() + timeout
        done, lock, result = threading.Event(), threading.Lock(), {}

        def start():
            with lock:
                try:
                    if result.get('abandoned') or time.monotonic() >= deadline: return
                    require(not self.closed and not self.failed)
                    require(route(self.backend, self.owner, self.handle, coordinates) == fixed,
                            'native_click_descendant_route_changed')
                    require(int(self.control.CoreWebView2.BrowserProcessId) == self.backend.browser_pid,
                            'native_click_browser_pid_indeterminate')
                    result['task'] = self.control.CoreWebView2.CallDevToolsProtocolMethodAsync(method, serialized)
                except click.NativeClickRejected as error: result['code'] = error.code
                except Exception: result['code'] = 'native_click_message_indeterminate'
                finally: done.set()

        try:
            if self.control.InvokeRequired:
                from System import Action
                self.control.BeginInvoke(Action(start))
            else: start()
            require(done.wait(max(0, deadline-time.monotonic())))
            require('task' in result and 'code' not in result, result.get('code', 'native_click_message_indeterminate'))
            task = result['task']
            while not task.IsCompleted and time.monotonic() < deadline: time.sleep(.005)
            require(task.IsCompleted and not task.IsCanceled and not task.IsFaulted)
            require(json.loads(str(task.Result)) == {})
            require(route(self.backend, self.owner, self.handle, coordinates) == fixed,
                    'native_click_descendant_route_changed')
            return {'deliveryCompleted': True, 'ownershipVerified': True, 'route': 'owned-webview2-sdk'}
        except Exception:
            # A timed-out or indeterminate call may still complete. No later
            # input is allowed on this driver; the isolated host must clean up.
            self.failed = True
            raise
        finally:
            with lock: result['abandoned'] = True

    def mouse(self, kind, coordinates, *, shift=False):
        parameters = mouse_payload(kind, coordinates, self.held, shift)
        result = self._call('Input.dispatchMouseEvent', parameters, coordinates)
        self.held = bool(parameters['buttons'])
        return dict(kind=kind, held=self.held, **result)

    def key(self, kind, coordinates):
        for parameters in key_sequence(kind):
            self._call('Input.dispatchKeyEvent', parameters, coordinates)
        return dict(kind=kind, deliveryCompleted=True, ownershipVerified=True, route='owned-webview2-sdk')

    def metrics(self, value, coordinates):
        require(not self.held, 'native_click_kind_unrecognized')
        method, parameters = metrics_command(value)
        return self._call(method, parameters, coordinates)

    def right_click(self,coordinates):
        require(not self.held,'native_click_kind_unrecognized')
        self.mouse('move',coordinates)
        for kind,buttons in (('mousePressed',2),('mouseReleased',0)):
            self._call('Input.dispatchMouseEvent',dict(type=kind,x=coordinates['x'],y=coordinates['y'],
                button='right',buttons=buttons,clickCount=1,modifiers=0,pointerType='mouse',force=.5 if buttons else 0),coordinates)
        return dict(kind='right-click',deliveryCompleted=True,ownershipVerified=True,route='owned-webview2-sdk')


ARM_POINTER = """(() => {
 const canvas=document.getElementById('canvas');if(!canvas?.isConnected)return false;
 window.__nivramPointerInput?.clear();
 const s={nonce:__NONCE__,events:[],overflow:false};
 s.listener=e=>{
  if(s.events.length>=128){s.overflow=true;return;}
  s.events.push({type:e.type,x:e.clientX,y:e.clientY,at:performance.now(),pointerId:e.pointerId,
   pointerType:e.pointerType,trusted:e.isTrusted===true,button:e.button,buttons:e.buttons,
   shift:e.shiftKey,alt:e.altKey,captured:canvas.hasPointerCapture(e.pointerId),
   inside:e.target===canvas||canvas.contains(e.target)});
 };
 for(const type of ['pointerdown','pointermove','pointerup'])canvas.addEventListener(type,s.listener,{passive:true});
 s.clear=()=>{for(const type of ['pointerdown','pointermove','pointerup'])canvas.removeEventListener(type,s.listener);
  delete window.__nivramPointerInput;};
 window.__nivramPointerInput=s;return true;
})()"""
READ_POINTER = """(() => {const s=window.__nivramPointerInput;
 return s?.nonce===__NONCE__?{overflow:s.overflow,events:s.events}:null;})()"""
CLEAR_POINTER = """(() => {const s=window.__nivramPointerInput;
 if(s?.nonce===__NONCE__)s.clear();return true;})()"""


def pointer_events(value):
    require(type(value) is dict and value.get('overflow') is False, 'native_click_trusted_event_unobserved')
    rows = value.get('events')
    require(type(rows) is list and 0 < len(rows) <= 128, 'native_click_trusted_event_unobserved')
    for row in rows:
        require(type(row) is dict and row.get('type') in ('pointerdown', 'pointermove', 'pointerup')
            and row.get('trusted') is True and row.get('inside') is True and row.get('pointerType') == 'mouse'
            and type(row.get('pointerId')) is int and row['pointerId'] > 0
            and all(type(row.get(k)) in (int,float) and math.isfinite(row[k]) for k in ('x','y','at'))
            and type(row.get('buttons')) is int and row['buttons'] in (0,1)
            and all(type(row.get(k)) is bool for k in ('shift','alt','captured')),
            'native_click_trusted_event_unobserved')
    return rows


def drag_events(value, start, end, *, released):
    rows = pointer_events(value)
    downs = [r for r in rows if r['type'] == 'pointerdown']
    ups = [r for r in rows if r['type'] == 'pointerup']
    require(len(downs) == 1 and len(ups) == int(released), 'native_click_trusted_event_unobserved')
    down = downs[0]; moves = [r for r in rows if r['type'] == 'pointermove' and r['buttons'] == 1]
    require(moves and down['buttons'] == 1 and down['button'] == 0 and down['captured'] is True,
            'native_click_trusted_event_unobserved')
    require(all(r['pointerId'] == down['pointerId'] for r in rows), 'native_click_trusted_event_unobserved')
    require(all(rows[i]['at'] <= rows[i+1]['at'] for i in range(len(rows)-1)), 'native_click_trusted_event_unobserved')
    require(down['at'] <= moves[-1]['at'] and moves[-1]['captured'] is True,
            'native_click_trusted_event_unobserved')
    for row, point in ((down, start), (moves[-1], end)):
        require(all(abs(row[k]-point[k]) < .01 for k in ('x','y')), 'native_click_trusted_event_unobserved')
    if released:
        require(ups[0]['buttons'] == 0 and ups[0]['button'] == 0 and ups[0]['at'] >= moves[-1]['at']
                and all(abs(ups[0][k]-end[k]) < .01 for k in ('x','y')), 'native_click_trusted_event_unobserved')
    return rows
