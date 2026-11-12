"""One fixed Escape key pair for the owned synthetic acceptance WebView."""
from __future__ import annotations

import json
import secrets
import time

from . import acceptance_click as click


ARM = """(() => {
 const nonce=__NONCE__,node=document.getElementById('canvas');
 if(!node?.isConnected)return null;
 // Match the browser resize test's canvas-focus precondition. This changes
 // focus only inside this owned synthetic page, never the OS foreground window.
 node.focus({preventScroll:true});
 if(document.activeElement!==node)return null;
 const r=node.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;
 const hit=document.elementFromPoint(x,y);
 if(!(r.width>0&&r.height>0&&x>=0&&y>=0&&x<innerWidth&&y<innerHeight)||
  !(hit===node||node.contains(hit)))return null;
 const s={nonce,node,x,y,viewportWidth:innerWidth,viewportHeight:innerHeight,seen:false,trusted:false,escape:false};
 s.listener=e=>{s.seen=true;s.trusted=e.isTrusted===true;s.escape=e.key==='Escape';};
 document.addEventListener('keydown',s.listener,{capture:true,once:true});
 window.__nivramAcceptanceEscape=s;
 return {x,y,viewportWidth:innerWidth,viewportHeight:innerHeight};
})()"""
OBSERVE = """(() => {const s=window.__nivramAcceptanceEscape;
 return s?.nonce===__NONCE__&&s.seen?{trusted:s.trusted,escape:s.escape}:null;})()"""
CLEAR = """(() => {const s=window.__nivramAcceptanceEscape;if(s?.nonce===__NONCE__){
 document.removeEventListener('keydown',s.listener,true);delete window.__nivramAcceptanceEscape;
 }return true;})()"""
STABLE = """(() => {const s=window.__nivramAcceptanceEscape;
 if(s?.nonce!==__NONCE__||!s.node?.isConnected||document.activeElement!==s.node)return false;
 const r=s.node.getBoundingClientRect();return Math.abs(r.left+r.width/2-s.x)<.1&&
  Math.abs(r.top+r.height/2-s.y)<.1&&innerWidth===s.viewportWidth&&innerHeight===s.viewportHeight;
})()"""


def send_escape(backend, owner, control, coordinates):
    """No global key state or input: revalidate the pinned point route per message."""
    click._owned(backend, owner, control)
    size = backend.client_size(control)
    point = click._point(coordinates, size)
    route = click._descendant_route(backend, owner, control, point)
    target, _, _, _, pid = route
    for message, parameter in ((0x0100, 0x00010001), (0x0101, 0xC0010001)):
        click._owned(backend, owner, control)
        click._require(backend.client_size(control) == size, 'native_click_client_bounds_changed')
        click._require(click._descendant_route(backend, owner, control, point) == route,
                       'native_click_descendant_route_changed')
        click._target_owned(backend, owner, control, target)
        click._require(backend.send(target, message, 0x1B, parameter, 1000),
                       'native_click_message_indeterminate')
    return {'ownershipVerified': True, 'messageTimeoutMs': 1000,
            'sdkBrowserProcessPinned': pid == backend.browser_pid and pid != backend.pid}


def escape(window, evaluate):
    control = window.native.webview
    click._require(control.GetType().FullName == 'Microsoft.Web.WebView2.WinForms.WebView2'
                   and control.IsHandleCreated and not control.IsDisposed, 'native_click_control_unrecognized')
    owner, handle = int(window.native.Handle.ToInt64()), int(control.Handle.ToInt64())
    nonce = json.dumps(secrets.token_hex(16))
    backend = click._WindowsBackend(click._browser_pid(control))
    try:
        coordinates = evaluate(ARM.replace('__NONCE__', nonce), timeout=2, stage='resize-escape-arm')
        click._require(evaluate(STABLE.replace('__NONCE__', nonce), timeout=2, stage='resize-escape-focus') is True,
                       'native_click_dom_target_changed')
        metadata = send_escape(backend, owner, handle, coordinates)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            observed = evaluate(OBSERVE.replace('__NONCE__', nonce), timeout=2, stage='resize-escape-observe')
            if observed is not None:
                click._require(type(observed) is dict and set(observed) == {'trusted', 'escape'}
                               and observed['trusted'] is True and observed['escape'] is True,
                               'native_click_trusted_event_unobserved')
                return {'kind': 'resize-escape', 'status': 'passed', 'isTrusted': True, **metadata}
            time.sleep(.05)
        raise click.NativeClickRejected('native_click_trusted_event_unobserved')
    finally:
        backend.close()
        try:
            evaluate(CLEAR.replace('__NONCE__', nonce), timeout=2, stage='resize-escape-cleanup')
        except Exception:
            pass
