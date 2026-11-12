"""Actual native-window resizing while real local edit responses are held."""
from __future__ import annotations

import copy
import json
import math
import secrets
import time

from .acceptance_editing import STATE, READY, require, assert_step

KINDS = ('success', 'rejection', 'cancel-late', 'manual')
CHECKS = tuple('native-resize-' + kind for kind in KINDS)
LARGE = 'CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC.[NH4+].[Cl-]'
STRUCTURES = tuple('C' * (40 + index) + '.[NH4+].[Cl-]' for index in range(4))
FACTS = ('realRender200', 'canvasShrank', 'pendingViewRetained', 'pendingStateRetained',
         'settledStateVerified', 'viewVerified', 'holdRestored')
GESTURES = {
    'success': ('edit-import', 'edit-load', 'resize-style', 'resize-fit'),
    'rejection': ('edit-import', 'edit-load', 'resize-style', 'resize-fit'),
    'cancel-late': ('edit-import', 'edit-load', 'resize-style', 'resize-escape', 'resize-fit',
                    'edit-import', 'edit-load', 'resize-zoomout', 'resize-pan-right', 'resize-style'),
    'manual': ('edit-import', 'edit-load', 'resize-style', 'resize-zoomout'),
}

MEASURE = """(() => {
 const canvas=document.getElementById('canvas'),drawing=document.getElementById('drawing');
 const r=canvas.getBoundingClientRect(),b=drawing.getBBox(),m=drawing.getScreenCTM();
 if(!m)throw new Error('No drawing transform.');
 const ps=[[b.x,b.y],[b.x+b.width,b.y],[b.x,b.y+b.height],[b.x+b.width,b.y+b.height]]
  .map(([x,y])=>new DOMPoint(x,y).matrixTransform(m));
 const left=Math.min(...ps.map(p=>p.x)),right=Math.max(...ps.map(p=>p.x));
 const top=Math.min(...ps.map(p=>p.y)),bottom=Math.max(...ps.map(p=>p.y));
 return {canvas:{width:r.width,height:r.height},ink:{width:right-left,height:bottom-top},
  padding:{left:left-r.left,right:r.right-right,top:top-r.top,bottom:r.bottom-bottom},
  view:canvas.getAttribute('viewBox')};
})()"""
WORLD = r"""(async () => {
 const p=window.nivramDocument.getSnapshot().document;
 const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json',
 'X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:p.graph,style:p.style}),signal:window.__nivramResizeAsync.controller.signal});
 if(!response.ok)throw new Error('World-view inspection failed.');
 const {scene}=await response.json(),t=scene.transform;
 const [x,y,w,h]=document.getElementById('canvas').getAttribute('viewBox').split(/\s+/).map(Number);
 return {x:(x-t.x0)/t.xScale,y:(y-t.y0)/t.yScale,w:w/t.xScale,h:h/t.yScale};
})()"""
ARM_HOLD = """(() => {
 if(window.__nivramResizeHold)throw new Error('Existing response hold.');
 const original=window.fetch,s={original,arrived:false,used:false,finished:false,released:false,expired:false,
  responseStatus:null,wrapper:null,release:null};
 const barrier=new Promise(resolve=>s.release=()=>{s.released=true;resolve();});
 s.timer=setTimeout(()=>{s.expired=true;s.release();},30000);
 s.wrapper=async (...args)=>{
  const response=await original(...args);
  if(s.used || args[0]!=='/api/render')return response;
  s.used=true;s.responseStatus=response.status;
  const data=await response.clone().json();
  if(response.status!==200 || !data.graph || !data.scene)throw new Error('Held response must be a real successful render.');
  s.arrived=true;await barrier;
  try{
   if(s.expired)throw new Error('Held response exceeded its bound.');
   return __REJECT__?new Response(JSON.stringify({error:'Synthetic held render rejection'}),
    {status:422,headers:{'Content-Type':'application/json'}}):response;
  }finally{s.finished=true;clearTimeout(s.timer);}
 };
 window.__nivramResizeHold=s;window.fetch=s.wrapper;return true;
})()"""
RESTORE_HOLD = """(() => {const s=window.__nivramResizeHold;
 if(!s || window.fetch!==s.wrapper)return false;
 s.release();clearTimeout(s.timer);window.fetch=s.original;
 const valid=s.arrived&&s.finished&&!s.expired&&s.responseStatus===200;
 delete window.__nivramResizeHold;return valid;
})()"""


def async_value(expression, evaluate, poll, remaining):
    """Start one fixed observation and poll its result through the sync evaluator."""
    nonce = json.dumps(secrets.token_hex(16))
    start = """(() => {
      if(window.__nivramResizeAsync)throw new Error('Existing resize observation.');
      const s={nonce:__NONCE__,done:false,failed:false,value:null,controller:new AbortController()};
      window.__nivramResizeAsync=s;
      Promise.resolve(__EXPRESSION__).then(value=>{if(window.__nivramResizeAsync===s){s.value=value;s.done=true;}},
        ()=>{if(window.__nivramResizeAsync===s){s.failed=true;s.done=true;}});
      return true;
    })()""".replace('__NONCE__', nonce).replace('__EXPRESSION__', expression)
    try:
        require(evaluate(start, timeout=min(10, remaining()), stage='resize-async-start') is True, 'resize_async_start')
        poll('window.__nivramResizeAsync?.nonce===' + nonce + ' && window.__nivramResizeAsync.done===true', timeout=remaining())
        result = evaluate("(() => {const s=window.__nivramResizeAsync;return s?.nonce===" + nonce +
            "&&s.done?{failed:s.failed,value:s.value}:null;})()", timeout=min(10, remaining()), stage='resize-async-result')
        require(type(result) is dict and set(result) == {'failed', 'value'} and result['failed'] is False, 'resize_async_failed')
        return result['value']
    finally:
        evaluate("(() => {const s=window.__nivramResizeAsync;if(s?.nonce===" + nonce +
            "){delete window.__nivramResizeAsync;s.controller.abort();}return true;})()",
            timeout=min(2, remaining()), stage='resize-async-cleanup')


def fitted(value):
    for group in ('canvas', 'ink'):
        require(set(value[group]) == {'width', 'height'}, 'resize_measure_shape')
        require(all(type(n) in (int, float) and math.isfinite(n) and n > 0
                    for n in value[group].values()), 'resize_measure_empty')
    require(set(value['padding']) == {'left', 'right', 'top', 'bottom'}, 'resize_padding_shape')
    require(all(type(n) in (int, float) and math.isfinite(n) and n >= 23.5
                for n in value['padding'].values()), 'resize_fit_padding')


def world_equal(actual, expected):
    require(set(actual) == set(expected) == {'x', 'y', 'w', 'h'}, 'resize_world_shape')
    for key in expected:
        require(type(actual[key]) in (int, float) and type(expected[key]) in (int, float)
                and math.isfinite(actual[key]) and math.isfinite(expected[key])
                and abs(actual[key] - expected[key]) <= 1e-7, 'resize_world_changed')


def observation_failures(report):
    value = report.get('resize_observation')
    if type(value) is not dict or type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1:
        return ['resize_observation_missing']
    cases = value.get('cases')
    if type(cases) is not list or len(cases) != 4 or [c.get('kind') for c in cases if type(c) is dict] != list(KINDS):
        return ['resize_cases_incomplete']
    for case in cases:
        if any(case.get(k) is not True for k in FACTS):
            return ['resize_case_unverified']
        initial, during = case.get('initial'), case.get('during')
        try:
            fitted(initial)
            require(during['canvas']['width'] < initial['canvas']['width'] and during['view'] == initial['view'], 'resize_not_observed')
            if case['kind'] != 'manual':
                fitted(case['settled'])
        except (KeyError, TypeError, AssertionError):
            return ['resize_geometry_unverified']
        expected_delta = 1 if case['kind'] in ('success', 'manual') else 0
        if type(case.get('revisionDelta')) is not int or case['revisionDelta'] != expected_delta:
            return ['resize_revision_unverified']
        if case['kind'] == 'cancel-late' and any(case.get(k) is not True for k in (
                'escapeTrusted', 'replacementDistinct', 'lateStateRetained', 'lateViewRetained', 'followupWorldRetained')):
            return ['resize_late_response_unverified']
        if case['kind'] == 'manual' and case.get('subsequentResizeRetained') is not True:
            return ['resize_manual_view_unverified']
        gestures = case.get('gestures')
        if type(gestures) is not list or not gestures or any(type(g) is not dict or
                g.get('status') != 'passed' or g.get('isTrusted') is not True or g.get('ownershipVerified') is not True
                or (g.get('kind') != 'resize-escape' and g.get('userActivation') is not True)
                for g in gestures):
            return ['resize_gestures_unverified']
        expected = list(GESTURES[case['kind']])
        if case['kind'] == 'cancel-late':
            if type(case.get('keyboardPanelOpened')) is not bool:
                return ['resize_gestures_unverified']
            if case['keyboardPanelOpened']:
                expected.insert(8, 'resize-keyboard')
        if [g.get('kind') for g in gestures] != expected:
            return ['resize_gestures_unverified']
    return []


def run(*, evaluate, poll, check, stage, report, window, **unused):
    from .acceptance_click import click_png_button
    from .acceptance_click import NativeClickRejected
    from .acceptance_escape import escape
    deadline = time.monotonic() + 120
    facts = {'schemaVersion': 1, 'cases': []}
    report['resize_observation'] = facts

    def remaining():
        value = deadline - time.monotonic()
        require(value > 0, 'resize_scenario_deadline')
        return min(20, value)

    def js(text, label='resize-observation'):
        return evaluate(text, timeout=min(10, remaining()), stage=label)

    def frames():
        require(async_value('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r(true))))', evaluate, poll, remaining) is True,
                'resize_frames_failed')

    def world():
        return async_value(WORLD, evaluate, poll, remaining)

    def state():
        return js(STATE)

    def ready(revision=None):
        condition = READY
        if revision is not None:
            condition += ' && window.nivramDocument.getSnapshot().revision===' + str(revision)
        try:
            poll(condition, timeout=remaining())
        except Exception:
            facts['failedWait'] = {'expectedRevision': revision, 'observed': js("""(() => {
              const s=window.nivramDocument.getSnapshot();return {revision:s.revision,
              atomCount:s.document.graph.atoms.length,ready:document.getElementById('request-status').textContent==='Ready'};
            })()""", 'resize-failed-wait')}
            raise
        frames()
        return state()

    def click(kind):
        remaining()
        row['gestures'].append(click_png_button(window, evaluate, kind)); stage(kind)

    def resize(width, height):
        remaining(); window.resize(width, height); frames()

    def load(text=LARGE):
        before = ready()
        click('edit-import')
        js('document.getElementById("structure-input").value=' + json.dumps(text) + ';true')
        click('edit-load')
        return ready(before['snapshot']['revision'] + 2)

    def set_style(width):
        js('document.getElementById("bond-width").value=' + json.dumps(str(width)) + ';true')
        click('resize-style')

    def release():
        require(js('window.__nivramResizeHold.release();true') is True, 'resize_release_failed')
        poll('window.__nivramResizeHold.finished===true', timeout=remaining())

    def explicit_fit(measured, snapshot):
        click('resize-fit'); frames()
        require(js(MEASURE)['view'] == measured['view'], 'resize_auto_explicit_fit_differ')
        require(state() == snapshot, 'resize_fit_changed_document')

    def edited(before, after, width):
        expected = copy.deepcopy(before['snapshot']['document']); expected['style']['bondWidth'] = width
        assert_step(before, after, document=expected, dirty=True, redo_disabled=True)

    poll("typeof window.nivramDocument.getSnapshot().recoveryGeneration==='string'", timeout=remaining())
    for kind, width, structure in zip(KINDS, (4, 3.8, 4.2, 3.6), STRUCTURES):
        row = {'kind': kind, 'gestures': []}; facts['cases'].append(row); stage('resize-' + kind)
        resize(1280, 800); before = load(structure); initial = js(MEASURE); fitted(initial); row['initial'] = initial
        require(js(ARM_HOLD.replace('__REJECT__', 'true' if kind == 'rejection' else 'false')) is True, 'resize_hold_arm')
        try:
            set_style(width)
            poll('window.__nivramResizeHold.arrived===true', timeout=remaining())
            require(js('window.__nivramResizeHold.responseStatus') == 200, 'resize_not_real_render')
            row['realRender200'] = True
            require(js(READY) is False, 'resize_edit_not_pending')
            resize(800, 600); during = js(MEASURE); row['during'] = during
            require(during['canvas']['width'] < initial['canvas']['width'], 'resize_canvas_not_shrunk')
            require(during['view'] == initial['view'], 'resize_busy_refit_early')
            require(state() == before, 'resize_pending_state_changed')
            row.update(canvasShrank=True, pendingViewRetained=True, pendingStateRetained=True)
            if kind == 'manual':
                click('resize-zoomout'); manual = world()
                require(js(MEASURE)['view'] != initial['view'], 'resize_zoom_not_changed')
            if kind == 'cancel-late':
                try:
                    row['gestures'].append(escape(window, evaluate))
                except NativeClickRejected as error:
                    row['escapeFailureCode'] = error.code
                    raise
                row['escapeTrusted'] = True
                settled_state = ready(before['snapshot']['revision']); measured = js(MEASURE)
                require(settled_state == before, 'resize_cancel_changed_state'); fitted(measured)
                explicit_fit(measured, settled_state)
                row.update(settled=measured, revisionDelta=0, settledStateVerified=True, viewVerified=True)
                replacement = load('CCO.[Cl-]')
                require(replacement['snapshot']['document'] != before['snapshot']['document'], 'resize_replacement_not_distinct')
                require(len(replacement['snapshot']['document']['graph']['atoms']) == 4, 'resize_replacement_atom_count')
                row['replacementDistinct'] = True
                click('resize-zoomout'); zoomed = js(MEASURE)['view']
                row['keyboardPanelOpened'] = not js('document.getElementById("keyboard-editor").open')
                if row['keyboardPanelOpened']:
                    click('resize-keyboard')
                click('resize-pan-right'); manual_view = js(MEASURE)['view']
                require(manual_view != zoomed, 'resize_pan_not_changed')
                release(); ready(replacement['snapshot']['revision'])
                require(state() == replacement, 'resize_late_state_changed')
                require(js(MEASURE)['view'] == manual_view, 'resize_late_view_changed')
                row.update(lateStateRetained=True, lateViewRetained=True)
                chosen = world(); set_style(3.7)
                changed = ready(replacement['snapshot']['revision'] + 1)
                edited(replacement, changed, 3.7); world_equal(world(), chosen)
                row['followupWorldRetained'] = True
            else:
                release()
                after = ready(before['snapshot']['revision'] + (0 if kind == 'rejection' else 1))
                measured = js(MEASURE)
                if kind == 'rejection':
                    require(after == before, 'resize_rejected_state_changed')
                    require('Synthetic held render rejection' in js('document.getElementById("toast").textContent'), 'resize_rejection_not_shown')
                else:
                    edited(before, after, width)
                row.update(settled=measured, revisionDelta=after['snapshot']['revision'] - before['snapshot']['revision'], settledStateVerified=True)
                if kind == 'manual':
                    world_equal(world(), manual)
                    resize(1100, 700); resized = js(MEASURE)
                    require(resized['canvas']['width'] > measured['canvas']['width'], 'resize_second_canvas_not_grown')
                    require(resized['view'] == measured['view'] and state() == after, 'resize_manual_mode_not_retained')
                    row['subsequentResizeRetained'] = True
                else:
                    fitted(measured); explicit_fit(measured, after)
                row['viewVerified'] = True
        finally:
            row['holdRestored'] = js(RESTORE_HOLD, 'resize-hold-restore') is True
        require(row['holdRestored'], 'resize_hold_not_restored')
        check('native-resize-' + kind, realCanvasResize=True, realRender200=True, exactState=True)
    require(observation_failures(report) == [], 'resize_observation_incomplete')
