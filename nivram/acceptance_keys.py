"""Fixed synthetic element keys and hover events confined to the owned WebView."""
from __future__ import annotations

import json
import math
import secrets
import threading
import time

from . import acceptance_click as click
from .acceptance_resize import async_value

# Key, virtual key, scan code, optional text-control character. No modifiers,
# global input, foreground activation, arbitrary selectors or arbitrary keys.
KEYS = {'c': ('c', 0x43, 0x2E, 0x63), 'l': ('l', 0x4C, 0x26, 0x6C),
        'i': ('i', 0x49, 0x17, 0x69), 'x': ('x', 0x58, 0x2D, 0x78),
        'e': ('e', 0x45, 0x12, 0x65), 'n': ('n', 0x4E, 0x31, 0x6E),
        '3': ('3', 0x33, 0x04, 0x33), 'backspace': ('backspace', 8, 0x0E, 8)}
SEQUENCES = {'c': ('c',), 'l': ('l',), 'cl': ('c', 'l'), 'li': ('l', 'i'),
             'xe': ('x', 'e'), 'text': ('backspace', 'n', '3')}

ATOM_POINT = """(async () => {
 const p=window.nivramDocument.getSnapshot(),a=p.document.graph.atoms;
 if(a.length!==1)throw new Error('Element fixture requires one atom.');
 const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json',
  'X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},
  body:JSON.stringify({graph:p.document.graph,style:p.document.style}),signal:window.__nivramResizeAsync.controller.signal});
 if(!response.ok)throw new Error('Atom coordinate inspection failed.');
 const {scene}=await response.json(),position=scene.positions.find(v=>v.id===a[0].id);
 const matrix=document.getElementById('canvas').getScreenCTM();
 if(!position||!matrix)throw new Error('Atom coordinate unavailable.');
 const s=new DOMPoint(position.x,position.y).matrixTransform(matrix);
 return {revision:p.revision,point:{x:s.x,y:s.y,viewportWidth:innerWidth,viewportHeight:innerHeight}};
})()"""
EMPTY_POINT = """(() => {const r=document.getElementById('canvas').getBoundingClientRect();
 return {revision:window.nivramDocument.getSnapshot().revision,
 point:{x:r.left+15,y:r.top+15,viewportWidth:innerWidth,viewportHeight:innerHeight}};})()"""
ARM_MOVE = """(() => {
 const node=document.getElementById('canvas'),anchor=__ANCHOR__,p=anchor.point;
 if(window.nivramDocument.getSnapshot().revision!==anchor.revision)return false;
 node.focus({preventScroll:true});
 if(document.activeElement!==node||innerWidth!==p.viewportWidth||innerHeight!==p.viewportHeight)return false;
 const hit=document.elementFromPoint(p.x,p.y);
 if(hit!==node&&!node.contains(hit))return false;
 window.__nivramElementHover?.clear();
 const h={point:p,events:[]};
 h.listener=e=>{h.events.push({type:e.type,at:performance.now(),x:e.clientX??null,y:e.clientY??null,trusted:e.isTrusted});
  if(h.events.length>16)h.events.shift();};
 for(const type of ['pointermove','pointerleave'])node.addEventListener(type,h.listener);
 window.addEventListener('blur',h.listener);
 h.clear=()=>{for(const type of ['pointermove','pointerleave'])node.removeEventListener(type,h.listener);
  window.removeEventListener('blur',h.listener);delete window.__nivramElementHover;};
 window.__nivramElementHover=h;
 const s={nonce:__NONCE__,node,seen:null};
 s.listener=e=>{if(Math.abs(e.clientX-p.x)<=2&&Math.abs(e.clientY-p.y)<=2)
  s.seen={trusted:e.isTrusted===true,mouse:e.pointerType==='mouse',buttons:e.buttons,at:performance.now()};};
 node.addEventListener('pointermove',s.listener,true);window.__nivramElementMove=s;return true;
})()"""
MOVE_RESULT = """(() => {const s=window.__nivramElementMove;return s?.nonce===__NONCE__?s.seen:null;})()"""
MOVE_CLEAR = """(() => {const s=window.__nivramElementMove;if(s?.nonce===__NONCE__){
 s.node.removeEventListener('pointermove',s.listener,true);delete window.__nivramElementMove;}return true;})()"""
ARM_KEYS = """(() => {
 const node=document.getElementById(__TARGET__);
 // Preserve the canvas transform used by the immediately preceding hover.
 // Text controls may need scrolling; the hovered canvas already has a visible target.
 if(node.id==='filename')node.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});
 node.focus({preventScroll:true});
 if(document.activeElement!==node)return null;
 const r=node.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;
 const s={nonce:__NONCE__,node,x,y,viewportWidth:innerWidth,viewportHeight:innerHeight,events:[]};
 s.listener=e=>{const item={key:e.key.toLowerCase(),trusted:e.isTrusted===true,repeat:e.repeat===true,
  modified:e.ctrlKey||e.altKey||e.metaKey||e.shiftKey,at:performance.now(),target:e.target===node,
  composing:e.isComposing,dialogsOpen:!!document.querySelector('dialog[open]'),
  hoverPoint:window.__nivramElementHover?.point??null,hoverEvents:window.__nivramElementHover?.events.slice()??[]};
  queueMicrotask(()=>{const p=window.nivramDocument.getSnapshot();
   Object.assign(item,{preview:document.querySelector('#element-entry-preview text')?.textContent??null,
    revision:p.revision,symbol:p.document.graph.atoms[0]?.symbol??null,
    pending:document.getElementById('request-status').textContent!=='Ready',prevented:e.defaultPrevented});
   s.events.push(item);});};
 // Observe after the production document-bubble handler has processed the key.
 window.addEventListener('keydown',s.listener);window.__nivramElementKeys=s;
 return {x,y,viewportWidth:innerWidth,viewportHeight:innerHeight};
})()"""
KEY_STABLE = """(() => {const s=window.__nivramElementKeys;
 if(s?.nonce!==__NONCE__||!s.node?.isConnected||document.activeElement!==s.node)return false;
 const r=s.node.getBoundingClientRect();return Math.abs(r.left+r.width/2-s.x)<.1&&
  Math.abs(r.top+r.height/2-s.y)<.1&&innerWidth===s.viewportWidth&&innerHeight===s.viewportHeight;})()"""
KEY_RESULT = """(() => {const s=window.__nivramElementKeys;return s?.nonce===__NONCE__?s.events:null;})()"""
KEY_CLEAR = """(() => {const s=window.__nivramElementKeys;if(s?.nonce===__NONCE__){
 window.removeEventListener('keydown',s.listener);delete window.__nivramElementKeys;}
 window.__nivramElementHover?.clear();return true;})()"""


def handles(window):
    control = window.native.webview
    click._require(control.GetType().FullName == 'Microsoft.Web.WebView2.WinForms.WebView2'
        and control.IsHandleCreated and not control.IsDisposed, 'native_click_control_unrecognized')
    return control, int(window.native.Handle.ToInt64()), int(control.Handle.ToInt64())


def route(backend, owner, control, coordinates):
    click._owned(backend, owner, control)
    size = backend.client_size(control)
    point = click._point(coordinates, size)
    return size, point, click._descendant_route(backend, owner, control, point)


def send(backend, owner, control, coordinates, fixed, message, key, parameter):
    click._require(route(backend, owner, control, coordinates) == fixed, 'native_click_descendant_route_changed')
    target = fixed[2][0]
    click._target_owned(backend, owner, control, target)
    click._require(backend.send(target, message, key, parameter, 1000), 'native_click_message_indeterminate')


def send_keys(backend, owner, control, coordinates, kind, before_key):
    click._require(type(kind) is str and kind in SEQUENCES, 'native_click_kind_unrecognized')
    fixed = route(backend, owner, control, coordinates)
    for name in SEQUENCES[kind]:
        before_key()
        _, vk, scan, character = KEYS[name]
        parameter = (scan << 16) | 1
        send(backend, owner, control, coordinates, fixed, 0x0100, vk, parameter)
        if kind == 'text':
            send(backend, owner, control, coordinates, fixed, 0x0102, character, parameter)
        send(backend, owner, control, coordinates, fixed, 0x0101, vk, parameter | 0xC0000000)
        time.sleep(.02)
    return {'ownershipVerified': True, 'messageTimeoutMs': 1000, 'messages': len(SEQUENCES[kind]) * (3 if kind == 'text' else 2)}


def observed_keys(value, kind):
    if type(value) is not list or len(value) != len(SEQUENCES[kind]): return False
    return all(type(row) is dict and row.get('key') == name and row.get('trusted') is True
        and row.get('target') is True and row.get('repeat') is False and row.get('modified') is False
        and type(row.get('at')) in (int, float) and math.isfinite(row['at']) and row['at'] >= 0
        for row, name in zip(value, SEQUENCES[kind]))


def key_observation(value):
    """Bounded typed diagnostics; never retain arbitrary user-entered text."""
    if type(value) is not list: return {'count': None, 'events': []}
    events = []
    for item in value[:4]:
        if type(item) is not dict: continue
        row = {'key': item.get('key') if type(item.get('key')) is str and item['key'] in KEYS else 'other'}
        for field in ('trusted', 'target', 'repeat', 'modified', 'composing', 'dialogsOpen', 'prevented', 'pending'):
            row[field] = item.get(field) if type(item.get(field)) is bool else None
        row['at'] = item.get('at') if type(item.get('at')) in (int, float) and math.isfinite(item['at']) else None
        events.append(row)
    return {'count': len(value), 'events': events}


def sdk_hover(control, backend, owner, handle, coordinates, timeout=2):
    """One fixed SDK mouseMoved call on this owned control, with bounded waits.

    Win32 mouse messages were followed immediately by pointerleave while the
    real desktop pointer remained elsewhere. SDK input stays inside the owned
    WebView. No remote debugging port, inspector, global cursor or bridge API.
    """
    click._require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 5,
                   'native_click_message_indeterminate')
    fixed = route(backend, owner, handle, coordinates)
    deadline = time.monotonic() + timeout
    done, lock, result = threading.Event(), threading.Lock(), {}
    parameters = json.dumps(dict(type='mouseMoved', x=coordinates['x'], y=coordinates['y'],
                                button='none', buttons=0, modifiers=0, pointerType='mouse'), allow_nan=False)

    def start():
        with lock:
            try:
                if result.get('abandoned') or time.monotonic() >= deadline: return
                click._require(route(backend, owner, handle, coordinates) == fixed,
                               'native_click_descendant_route_changed')
                click._require(int(control.CoreWebView2.BrowserProcessId) == backend.browser_pid,
                               'native_click_browser_pid_indeterminate')
                result['task'] = control.CoreWebView2.CallDevToolsProtocolMethodAsync('Input.dispatchMouseEvent', parameters)
            except Exception:
                result['failed'] = True
            finally:
                done.set()

    try:
        if control.InvokeRequired:
            from System import Action
            control.BeginInvoke(Action(start))
        else: start()
        click._require(done.wait(max(0, deadline-time.monotonic())), 'native_click_message_indeterminate')
        click._require('task' in result and not result.get('failed'), 'native_click_message_indeterminate')
        task = result['task']
        while not task.IsCompleted and time.monotonic() < deadline: time.sleep(.005)
        click._require(task.IsCompleted and not task.IsCanceled and not task.IsFaulted,
                       'native_click_message_indeterminate')
        click._require(json.loads(str(task.Result)) == {}, 'native_click_message_indeterminate')
        click._require(route(backend, owner, handle, coordinates) == fixed,
                       'native_click_descendant_route_changed')
        return {'completed': True, 'route': 'owned-webview2-sdk-hover'}
    finally:
        with lock: result['abandoned'] = True


def keys(window, evaluate, poll, kind):
    click._require(type(kind) is str and kind in SEQUENCES, 'native_click_kind_unrecognized')
    control, owner, handle = handles(window)
    nonce = json.dumps(secrets.token_hex(16)); backend = click._WindowsBackend(click._browser_pid(control))
    observed = None
    try:
        expression = ARM_KEYS.replace('__NONCE__', nonce).replace('__TARGET__', json.dumps('filename' if kind == 'text' else 'canvas'))
        coordinates = evaluate(expression, timeout=2, stage='element-keys-arm')
        def stable():
            click._require(evaluate(KEY_STABLE.replace('__NONCE__', nonce), timeout=2, stage='element-keys-focus') is True,
                           'native_click_dom_target_changed')
        metadata = send_keys(backend, owner, handle, coordinates, kind, stable)
        poll('window.__nivramElementKeys?.events.length>=' + str(len(SEQUENCES[kind])), timeout=5)
        observed = evaluate(KEY_RESULT.replace('__NONCE__', nonce), timeout=2, stage='element-keys-observe')
        click._require(observed_keys(observed, kind), 'native_click_trusted_event_unobserved')
        return {'kind': kind, 'status': 'passed', 'events': observed, **metadata}
    except click.NativeClickRejected as error:
        error.element_observation = key_observation(observed)
        raise
    finally:
        backend.close()
        try: evaluate(KEY_CLEAR.replace('__NONCE__', nonce), timeout=2, stage='element-keys-cleanup')
        except Exception: pass


def pointer_pair(window, evaluate, poll, remaining):
    """C, owned empty-space hover, L without report I/O inside the 500 ms interval.

    Focus is checked before the sequence; both key targets and the actual hover
    are independently observed afterward. Every message rechecks native ownership.
    """
    anchor = evaluate(EMPTY_POINT, timeout=2, stage='element-pair-empty')
    click._require(type(anchor) is dict and set(anchor) == {'revision', 'point'}, 'native_click_coordinates_invalid')
    control, owner, handle = handles(window)
    nonce = json.dumps(secrets.token_hex(16)); backend = click._WindowsBackend(click._browser_pid(control))
    observed = None
    try:
        click._require(evaluate(ARM_MOVE.replace('__ANCHOR__', json.dumps(anchor)).replace('__NONCE__', nonce),
            timeout=2, stage='element-pair-hover-arm') is True, 'native_click_dom_target_changed')
        coordinates = evaluate(ARM_KEYS.replace('__NONCE__', nonce).replace('__TARGET__', '"canvas"'),
            timeout=2, stage='element-pair-keys-arm')
        click._require(evaluate(KEY_STABLE.replace('__NONCE__', nonce), timeout=2,
            stage='element-pair-focus') is True, 'native_click_dom_target_changed')
        first = send_keys(backend, owner, handle, coordinates, 'c', lambda: None)
        sdk_hover(control, backend, owner, handle, anchor['point'], min(2, remaining()))
        last = send_keys(backend, owner, handle, coordinates, 'l', lambda: None)
        poll('window.__nivramElementKeys?.events.length>=2 && window.__nivramElementMove?.seen!==null', timeout=5)
        observed = evaluate(KEY_RESULT.replace('__NONCE__', nonce), timeout=2, stage='element-pair-keys-observe')
        moved = evaluate(MOVE_RESULT.replace('__NONCE__', nonce), timeout=2, stage='element-pair-hover-observe')
        click._require(observed_keys(observed, 'cl'), 'native_click_trusted_event_unobserved')
        click._require(type(moved) is dict and moved.get('trusted') is True and moved.get('mouse') is True
            and type(moved.get('buttons')) is int and moved['buttons'] == 0, 'native_click_trusted_event_unobserved')
        return ({'kind': 'c', 'status': 'passed', 'events': observed[:1], **first},
                {'kind': 'empty', 'status': 'passed', 'ownershipVerified': True, 'route': 'owned-webview2-sdk-hover', **moved},
                {'kind': 'l', 'status': 'passed', 'events': observed[1:], **last})
    except click.NativeClickRejected as error:
        error.element_observation = key_observation(observed)
        raise
    finally:
        backend.close()
        for expression in (KEY_CLEAR, MOVE_CLEAR):
            try: evaluate(expression.replace('__NONCE__', nonce), timeout=2, stage='element-pair-cleanup')
            except Exception: pass


def move(window, evaluate, poll, kind, remaining):
    click._require(kind in ('atom', 'empty'), 'native_click_kind_unrecognized')
    anchor = async_value(ATOM_POINT, evaluate, poll, remaining) if kind == 'atom' else evaluate(EMPTY_POINT, timeout=2, stage='element-empty-point')
    click._require(type(anchor) is dict and set(anchor) == {'revision', 'point'}, 'native_click_coordinates_invalid')
    control, owner, handle = handles(window)
    nonce = json.dumps(secrets.token_hex(16)); backend = click._WindowsBackend(click._browser_pid(control))
    try:
        click._require(evaluate(ARM_MOVE.replace('__ANCHOR__', json.dumps(anchor)).replace('__NONCE__', nonce),
            timeout=2, stage='element-hover-arm') is True, 'native_click_dom_target_changed')
        point = anchor['point']
        # A short, bounded waypoint makes repeated hovering observable even if
        # Chromium suppresses a duplicate move to the preceding mouse position.
        for coordinates in ({**point, 'x': point['x'] + 8}, point):
            fixed = route(backend, owner, handle, coordinates)
            sdk_hover(control, backend, owner, handle, coordinates, min(2, remaining()))
            time.sleep(.02)
        poll('window.__nivramElementMove?.seen!==null', timeout=5)
        observed = evaluate(MOVE_RESULT.replace('__NONCE__', nonce), timeout=2, stage='element-hover-observe')
        click._require(type(observed) is dict and observed.get('trusted') is True and observed.get('mouse') is True
            and type(observed.get('buttons')) is int and observed['buttons'] == 0, 'native_click_trusted_event_unobserved')
        return {'kind': kind, 'status': 'passed', 'ownershipVerified': True,
                'route': 'owned-webview2-sdk-hover', **observed}
    finally:
        backend.close()
        try: evaluate(MOVE_CLEAR.replace('__NONCE__', nonce), timeout=2, stage='element-hover-cleanup')
        except Exception: pass
