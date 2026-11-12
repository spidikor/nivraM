"""Production pointer editing through the owned native WebView2 input channel."""
from __future__ import annotations

import copy
import json
import math
import secrets
import time
import traceback
from pathlib import Path

from . import acceptance_click as click
from . import acceptance_pointer_input as input_api
from .acceptance_editing import STATE, READY, require
from .acceptance_resize import async_value

KINDS = ('erase-dpr1','erase-dpr2','erase-crossing','erase-pointercancel','erase-lostcapture',
         'valence','stereo-move','selection-delete','drag-cancel','tool-keys')
CHECKS = frozenset('native-pointer-'+name for name in KINDS)
FACTS = {
 'erase-dpr1': ('thresholdsExact','previewOnly','oneEraseTransaction','undoExact'),
 'erase-dpr2': ('thresholdsExact','previewOnly','oneEraseTransaction','undoExact'),
 'erase-crossing': ('midpointOnly','previewEmpty','stateRetained','historyExact'),
 'erase-pointercancel': ('captureObserved','syntheticBoundaryObserved','previewCleared','lateReleaseRetained','historyExact'),
 'erase-lostcapture': ('captureObserved','syntheticBoundaryObserved','previewCleared','lateReleaseRetained','historyExact'),
 'valence': ('pendingBondRetainedByTool','valenceRejected','stateRetained','pendingBondCancelled','followupValid','undoExact'),
 'stereo-move': ('annotationsPresent','noopExact','uniformTranslation','selectionRetained','undoExact','redoExact'),
 'selection-delete': ('selectionDistinctFromTarget','onlySelectedMoved','inspectedTargetRetained','mixedSelection','deleteExact','undoExact'),
 'drag-cancel': ('firstMoveCommitted','undoDuringDrag','undoLateReleaseRetained','escapeCleared','escapeLateReleaseRetained'),
 'tool-keys': ('altIgnored','bareErase','bareSelect','textIsolated','modalIsolated','modalEscape','stateRetained'),
}
BUTTONS = {**{k:click._SELECTORS[k] for k in ('edit-import','edit-load','edit-undo','edit-redo','resize-keyboard')},
 **{'tool-'+k: 'document.querySelector(\'[data-tool="'+k+'"]\')' for k in ('select','bond','erase')},
 **{k:'document.getElementById('+json.dumps(k)+')' for k in
    ('keyboard-select','keyboard-toggle-select','keyboard-select-all','keyboard-start-bond','help-button')}}

GEOMETRY = """(async()=>{
 const s=window.nivramDocument.getSnapshot(),canvas=document.getElementById('canvas');
 const r=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json',
  'X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},
  body:JSON.stringify({graph:s.document.graph,style:s.document.style}),signal:window.__nivramResizeAsync.controller.signal});
 if(!r.ok)throw new Error('Pointer geometry failed');
 const {scene}=await r.json(),m=canvas.getScreenCTM(),b=canvas.getBoundingClientRect();
 if(!m)throw new Error('Pointer matrix missing');
 return {revision:s.revision,dpr:devicePixelRatio,viewportWidth:innerWidth,viewportHeight:innerHeight,
  bounds:{left:b.left,right:b.right,top:b.top,bottom:b.bottom},
  points:scene.positions.map(a=>{const p=new DOMPoint(a.x,a.y).matrixTransform(m);return {id:a.id,x:p.x,y:p.y};})};
})()"""
PREVIEW = """(()=>{const g=document.getElementById('gesture');return {
 rect:g.querySelectorAll('rect').length,circle:g.querySelectorAll('circle').length,
 line:g.querySelectorAll('line').length,total:g.children.length};})()"""
SELECTION = """(()=>{const s=document.getElementById('selection');return {
 html:s.innerHTML,circle:s.querySelectorAll('circle').length,line:s.querySelectorAll('line').length};})()"""
ARM_KEYS = """(()=>{
 const id=__TARGET__,node=id===null?document.activeElement:document.getElementById(id);
 if(!node?.isConnected)return null;
 if(id!==null){if(id!=='canvas')node.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});node.focus({preventScroll:true});}
 if(document.activeElement!==node)return null;
 const r=document.getElementById('canvas').getBoundingClientRect();
 const s={nonce:__NONCE__,events:[]};
 s.listener=e=>{s.events.push({type:e.type,key:e.key,code:e.code,trusted:e.isTrusted===true,
  target:e.target===node,repeat:e.repeat,modifiers:(e.altKey?1:0)|(e.ctrlKey?2:0)|(e.metaKey?4:0)|(e.shiftKey?8:0)});};
 for(const type of ['keydown','keyup'])document.addEventListener(type,s.listener,true);
 s.clear=()=>{for(const type of ['keydown','keyup'])document.removeEventListener(type,s.listener,true);delete window.__nivramPointerKeys;};
 window.__nivramPointerKeys=s;return {x:r.left+r.width/2,y:r.top+r.height/2,viewportWidth:innerWidth,viewportHeight:innerHeight};
})()"""


def key_events(events, kinds):
    require(type(kinds) is list and 0<len(kinds)<=128,'pointer_key_sequence_invalid')
    expected = [p for kind in kinds for p in input_api.key_sequence(kind)]
    require(type(events) is list and len(events)==len(expected), 'pointer_keys_missing')
    for event, item in zip(events, expected):
        require(type(event) is dict and event.get('trusted') is True and event.get('repeat') is False
            and event.get('type')==('keydown' if item['type'] in ('rawKeyDown','keyDown') else 'keyup')
            and event.get('key')==item['key'] and event.get('code')==item['code']
            and type(event.get('modifiers')) is int and event['modifiers']==item['modifiers']
            and (event['type']=='keyup' or event.get('target') is True), 'pointer_keys_unverified')


def scale_matches(value,expected):
    return type(value) in (int,float) and math.isfinite(value) and abs(value-expected)<1e-6


def same_document(before, after, document, *, delta=1):
    expected=copy.deepcopy(before['snapshot'])
    expected.update(document=document,revision=expected['revision']+delta)
    # Every fixture is imported through loadText, which calls markUnsaved and
    # clears the saved signature. Even an erased/undone empty drawing is dirty.
    expected['dirty']=True
    require(after['snapshot']==expected,'pointer_document_or_metadata_changed')


def translation(before, after, ids):
    a=before['graph'];b=after['graph'];require(len(a['atoms'])==len(b['atoms']), 'pointer_atom_count_changed')
    strip=lambda g:{**g,'atoms':[{k:v for k,v in atom.items() if k not in ('x','y')} for atom in g['atoms']]}
    require(strip(a)==strip(b),'pointer_noncoordinate_graph_changed')
    require({k:v for k,v in before.items() if k!='graph'}=={k:v for k,v in after.items() if k!='graph'},'pointer_document_settings_changed')
    offsets=[]
    for old,new in zip(a['atoms'],b['atoms']):
        if old['id'] in ids: offsets.append((new['x']-old['x'],new['y']-old['y']))
        else: require(old==new,'pointer_unselected_atom_moved')
    require(len(offsets)==len(ids) and offsets and all(abs(v)>1e-9 for v in offsets[0]),'pointer_translation_empty')
    require(all(abs(x-offsets[0][0])<1e-9 and abs(y-offsets[0][1])<1e-9 for x,y in offsets),'pointer_translation_nonuniform')


def observation_failures(report):
    value=report.get('pointer_observation')
    if type(value) is not dict or type(value.get('schemaVersion')) is not int or value['schemaVersion']!=1:
        return ['pointer_observation_missing']
    cases=value.get('cases')
    if type(cases) is not list or len(cases)!=len(KINDS) or [r.get('kind') for r in cases if type(r) is dict]!=list(KINDS):
        return ['pointer_cases_incomplete']
    if value.get('metricsRestored') is not True: return ['pointer_metrics_not_restored']
    try:
        for case in cases:
            require(all(case.get(k) is True for k in FACTS[case['kind']]),'facts')
            actions=case.get('actions');require(type(actions) is list and actions,'actions')
            for a in actions:
                require(type(a) is dict and a.get('status')=='passed' and a.get('ownershipVerified') is True,'action')
                if a.get('type')=='button':
                    require(a.get('kind') in BUTTONS and a.get('isTrusted') is True and a.get('userActivation') is True,'button')
                elif a.get('type')=='keys':key_events(a.get('events'),a['kinds'])
                elif a.get('type')=='drag':input_api.drag_events(a['observed'],a['start'],a['end'],released=True)
                elif a.get('type')=='tap':
                    rows=input_api.pointer_events(a['observed']); downs=[r for r in rows if r['type']=='pointerdown'];ups=[r for r in rows if r['type']=='pointerup']
                    require(len(downs)==len(ups)==1 and downs[0]['captured'] is True and downs[0]['pointerId']==ups[0]['pointerId'],'tap')
                else:require(False,'unknown-action')
            if case['kind'].startswith('erase-dpr'):
                scale=int(case['kind'][-1]);require(scale_matches(case.get('observedDpr'),scale),'dpr')
                require(case.get('distances')==[3,4,5],'thresholds')
                require(len([a for a in actions if a['type']=='drag'])==4,'threshold-drags')
            if case['kind'] in ('erase-pointercancel','erase-lostcapture'):
                boundary=case.get('boundary');require(type(boundary) is dict and boundary.get('trusted') is False
                    and boundary.get('synthetic') is True and type(boundary.get('pointerId')) is int and boundary['pointerId']>0
                    and boundary.get('type')==('pointercancel' if case['kind']=='erase-pointercancel' else 'lostpointercapture'),'boundary')
    except (AssertionError,click.NativeClickRejected,KeyError,TypeError,ValueError):return ['pointer_evidence_incomplete']
    return []


class Journey:
    buttons=BUTTONS
    key_targets=('canvas','filename','keyboard-target','bond-type',None)
    select_targets=('keyboard-target','bond-type')
    def __init__(self,evaluate,poll,check,stage,report,window):
        self.evaluate,self.poll,self.check,self.stage=evaluate,poll,check,stage
        self.deadline=time.monotonic()+240
        self.facts={'schemaVersion':1,'cases':[],'metricsRestored':False};report['pointer_observation']=self.facts
        self.driver=input_api.NativePointer(window);self.row=None;self.gesture=None

    def remaining(self):
        left=self.deadline-time.monotonic();require(left>0,'pointer_deadline');return min(20,left)
    def js(self,text):return self.evaluate(text,timeout=min(10,self.remaining()),stage='pointer-observation')
    def state(self):return self.js(STATE)
    def ready(self,revision=None):
        condition=READY
        if revision is not None:condition+=' && window.nivramDocument.getSnapshot().revision==='+str(revision)
        try:self.poll(condition,timeout=self.remaining())
        except Exception:
            self.facts['failedWait']={'expectedRevision':revision,'observed':self.js("({revision:window.nivramDocument.getSnapshot().revision,status:document.getElementById('request-status').textContent})")};raise
        return self.state()
    def begin(self,kind):
        self.row={'kind':kind,'actions':[]};self.facts['cases'].append(self.row);self.stage('pointer-'+kind)
    def fact(self,*names):
        for name in names:self.row[name]=True
    def done(self):
        require(all(self.row.get(k) is True for k in FACTS[self.row['kind']]),'pointer_case_incomplete')
        self.check('native-pointer-'+self.row['kind'],facts=list(FACTS[self.row['kind']]))
    def record(self,category,**data):self.row['actions'].append(dict(type=category,status='passed',ownershipVerified=True,**data))
    def button(self,kind):
        require(kind in self.buttons,'pointer_button_unrecognized');nonce=json.dumps(secrets.token_hex(16))
        try:
            p=self.js(click._ARM.replace('__BUTTON__',self.buttons[kind]).replace('__NONCE__',nonce))
            require(type(p) is dict,'pointer_button_unavailable')
            self.driver.mouse('move',p)
            require(self.js(click._STABLE.replace('__NONCE__',nonce)) is True,'pointer_button_moved')
            self.driver.mouse('down',p)
            require(self.js(click._STABLE.replace('__NONCE__',nonce)) is True,'pointer_button_moved')
            self.driver.mouse('up',p)
            seen=self.js(click._OBSERVE.replace('__NONCE__',nonce))
            require(type(seen) is dict and seen.get('trusted') is True and seen.get('activation') is True,'pointer_button_untrusted')
            self.record('button',kind=kind,isTrusted=True,userActivation=True)
        finally:self.js(click._CLEAR.replace('__NONCE__',nonce))
    def keys(self,kinds,target='canvas'):
        require(target in self.key_targets,'pointer_key_target')
        nonce=json.dumps(secrets.token_hex(16))
        try:
            p=self.js(ARM_KEYS.replace('__TARGET__',json.dumps(target)).replace('__NONCE__',nonce));require(type(p) is dict,'pointer_key_focus')
            for kind in kinds:self.driver.key(kind,p)
            events=self.js('window.__nivramPointerKeys.events')
            self.row['lastKeyObservation']={'events':events,'kinds':kinds};key_events(events,kinds)
            self.record('keys',kinds=kinds,events=events)
        finally:self.js("(()=>{const s=window.__nivramPointerKeys;if(s?.nonce==="+nonce+")s.clear();return true;})()")
    def choose(self,id,value):
        require(id in self.select_targets,'pointer_select_target')
        options=self.js('Array.from(document.getElementById('+json.dumps(id)+').options,o=>o.value)')
        require(type(options) is list and 0<len(options)<=128 and value in options,'pointer_select_option')
        self.keys(['home']+['down']*options.index(value),id)
        require(self.js('document.getElementById('+json.dumps(id)+').value')==value,'pointer_select_not_applied')
    def load(self,smiles):
        old=self.ready();self.button('edit-import')
        self.js('document.getElementById("structure-input").value='+json.dumps(smiles)+';true')
        self.button('edit-load');new=self.ready(old['snapshot']['revision']+2)
        require(new['snapshot']['document']!=old['snapshot']['document'],'pointer_import_not_distinct')
        return old,new
    def geometry(self):
        self.facts['operation']='geometry-observation'
        value=async_value(GEOMETRY,self.evaluate,self.poll,self.remaining)
        require(type(value) is dict and value['revision']==self.state()['snapshot']['revision'],'pointer_geometry_stale')
        self.geo=value;return value
    def point(self,x,y):
        b=self.geo['bounds'];require(b['left']<x<b['right'] and b['top']<y<b['bottom'],'pointer_point_outside_canvas')
        return dict(x=x,y=y,viewportWidth=self.geo['viewportWidth'],viewportHeight=self.geo['viewportHeight'])
    def atom(self,id):
        g=self.geometry();a=next(p for p in g['points'] if p['id']==id);return self.point(a['x'],a['y'])
    def empty(self):
        g=self.geometry();b=g['bounds'];p=self.point(math.ceil(b['left'])+10,math.ceil(b['top'])+10)
        require(all(math.hypot(a['x']-p['x'],a['y']-p['y'])>30 for a in g['points']),'pointer_empty_not_blank');return p
    def rectangle(self):
        points=self.geometry()['points'];require(len(points)==2,'pointer_pair_fixture')
        return self.point(min(p['x'] for p in points)-20,min(p['y'] for p in points)-20),self.point(max(p['x'] for p in points)+20,max(p['y'] for p in points)+20)
    def preview(self,rect=0,circle=0,line=0):
        actual=self.js(PREVIEW);require(actual==dict(rect=rect,circle=circle,line=line,total=rect+circle+line),'pointer_preview_wrong')
    def start_drag(self,start,end):
        require(self.gesture is None,'pointer_overlapping_gesture');nonce=json.dumps(secrets.token_hex(16))
        require(self.js(input_api.ARM_POINTER.replace('__NONCE__',nonce)) is True,'pointer_observer_arm')
        self.gesture=dict(nonce=nonce,start=start,end=end)
        self.driver.mouse('move',start);self.driver.mouse('down',start);self.driver.mouse('move',end)
        self.observed(False)
    def observed(self,released):
        g=self.gesture;v=self.js(input_api.READ_POINTER.replace('__NONCE__',g['nonce']))
        input_api.pointer_events(v)
        self.row['lastPointerObservation']=dict(start=g['start'],end=g['end'],observed=v)
        input_api.drag_events(v,g['start'],g['end'],released=released);return v
    def release(self):
        g=self.gesture;self.driver.mouse('up',g['end']);value=self.observed(True)
        self.record('drag',start=g['start'],end=g['end'],observed=value)
        self.js(input_api.CLEAR_POINTER.replace('__NONCE__',g['nonce']));self.gesture=None
    def drag(self,start,end,revision):
        self.start_drag(start,end);self.release();return self.ready(revision)
    def tap(self,p):
        nonce=json.dumps(secrets.token_hex(16));require(self.js(input_api.ARM_POINTER.replace('__NONCE__',nonce)) is True,'pointer_observer_arm')
        try:
            for kind in ('move','down','up'):self.driver.mouse(kind,p)
            value=self.js(input_api.READ_POINTER.replace('__NONCE__',nonce));input_api.pointer_events(value)
            self.record('tap',observed=value)
        finally:self.js(input_api.CLEAR_POINTER.replace('__NONCE__',nonce))
    def history(self,before,document,redo=False):
        self.button('edit-redo' if redo else 'edit-undo');after=self.ready(before['snapshot']['revision']+1)
        same_document(before,after,document)
        require(after['redoDisabled'] is (True if redo else False),'pointer_history_availability')
        return after


def run(*,evaluate,poll,check,stage,report,window,**unused):
    j=Journey(evaluate,poll,check,stage,report,window)
    original_scale=j.js('devicePixelRatio')
    j.facts['originalDpr']=original_scale
    try:
        poll("typeof window.nivramDocument.getSnapshot().recoveryGeneration==='string'",timeout=j.remaining())
        for scale,smiles in ((1,'CC'),(2,'[13CH3]C')):
            j.begin('erase-dpr'+str(scale));old,loaded=j.load(smiles);j.button('tool-erase')
            p=j.empty();j.driver.metrics(scale,p)
            j.facts['operation']='device-scale-observation'
            j.row['immediateDprAfterOverride']=j.js('devicePixelRatio')
            poll('Math.abs(devicePixelRatio-'+str(scale)+')<1e-6',timeout=j.remaining());j.row['observedDpr']=j.js('devicePixelRatio')
            before=j.ready();start=j.empty();j.row['distances']=[]
            for distance in (3,4,5):
                j.facts['operation']='erase-threshold-'+str(distance)
                end=j.point(start['x']+distance,start['y']);j.start_drag(start,end)
                j.preview(rect=int(distance>4));require(j.state()==before,'erase_threshold_mutated_preview')
                j.release();require(j.ready()==before,'erase_blank_drag_mutated_state');j.preview()
                j.row['distances'].append(distance)
            j.fact('thresholdsExact','previewOnly');start,end=j.rectangle();j.start_drag(start,end)
            j.preview(rect=1,circle=2,line=1);require(j.state()==before,'erase_preview_mutated_document')
            j.release();erased=j.ready(before['snapshot']['revision']+1)
            expected=copy.deepcopy(before['snapshot']['document']);expected['graph']['atoms']=[];expected['graph']['bonds']=[]
            same_document(before,erased,expected);j.preview();j.fact('oneEraseTransaction')
            j.history(erased,before['snapshot']['document']);j.fact('undoExact');j.done()
        j.driver.metrics(None,j.empty());poll('Math.abs(devicePixelRatio-'+json.dumps(original_scale)+')<1e-6',timeout=j.remaining());j.facts['metricsRestored']=True

        j.begin('erase-crossing');old,before=j.load('CO');j.button('tool-erase');g=j.geometry();a,b=g['points']
        dx,dy=b['x']-a['x'],b['y']-a['y'];length=math.hypot(dx,dy);require(length>60,'erase_crossing_bond_short')
        mx,my=(a['x']+b['x'])/2,(a['y']+b['y'])/2;half=.2*length
        distance=lambda p:abs(dx*(a['y']-p[1])-(a['x']-p[0])*dy)/length
        start=max(((mx+sx*half,my+sy*half) for sx in (-1,1) for sy in (-1,1)),key=distance)
        require(distance(start)>10 and all(math.hypot(start[0]-p['x'],start[1]-p['y'])>16 for p in (a,b)),'erase_crossing_start_not_blank')
        end=(2*mx-start[0],2*my-start[1]);inside=lambda p:min(start[0],end[0])<=p['x']<=max(start[0],end[0]) and min(start[1],end[1])<=p['y']<=max(start[1],end[1])
        require(inside(dict(x=mx,y=my)) and not inside(a) and not inside(b),'erase_crossing_geometry');j.fact('midpointOnly')
        j.start_drag(j.point(*start),j.point(*end));j.preview(rect=1);require(j.state()==before,'erase_crossing_preview_mutated');j.fact('previewEmpty')
        j.release();require(j.ready()==before,'erase_crossing_mutated');j.preview();j.fact('stateRetained')
        undone=j.history(before,old['snapshot']['document']);j.history(undone,before['snapshot']['document'],True);j.fact('historyExact');j.done()

        for kind,event,smiles in (('erase-pointercancel','pointercancel','CN'),('erase-lostcapture','lostpointercapture','CF')):
            j.begin(kind);old,before=j.load(smiles);j.button('tool-erase');start,end=j.rectangle();j.start_drag(start,end)
            j.preview(rect=1,circle=2,line=1);require(j.state()==before,'erase_cancel_preview_mutated')
            rows=j.observed(False)['events'];down=next(r for r in rows if r['type']=='pointerdown');pid=down['pointerId'];j.fact('captureObserved')
            boundary=j.js("(()=>{const c=document.getElementById('canvas');const e=new PointerEvent("+json.dumps(event)+",{pointerId:"+str(pid)+",pointerType:'mouse',bubbles:true,clientX:"+str(end['x'])+",clientY:"+str(end['y'])+"});c.dispatchEvent(e);return {synthetic:true,type:e.type,pointerId:e.pointerId,trusted:e.isTrusted};})()")
            require(boundary==dict(synthetic=True,type=event,pointerId=pid,trusted=False),'erase_cancel_boundary');j.row['boundary']=boundary;j.fact('syntheticBoundaryObserved')
            j.preview();require(j.state()==before,'erase_cancel_changed_state');j.fact('previewCleared')
            j.release();require(j.ready()==before,'erase_cancel_late_release_changed_state');j.fact('lateReleaseRetained')
            undone=j.history(before,old['snapshot']['document']);j.history(undone,before['snapshot']['document'],True);j.fact('historyExact');j.done()

        j.begin('valence');old,imported=j.load('C(C)(C)(C)C')
        if not j.js("document.getElementById('keyboard-editor').open"):j.button('resize-keyboard')
        graph=imported['snapshot']['document']['graph'];degree=lambda id:sum(id in (b['begin'],b['end']) for b in graph['bonds'])
        center=next(a for a in graph['atoms'] if degree(a['id'])==4);terminal=next(a for a in graph['atoms'] if degree(a['id'])==1)
        j.choose('keyboard-target','atom:'+center['id']);j.button('keyboard-select');j.button('keyboard-start-bond');j.button('tool-bond')
        require(j.js("document.getElementById('keyboard-finish-bond').disabled") is False,'pointer_pending_bond_lost');j.fact('pendingBondRetainedByTool')
        before=j.state();selected=j.js(SELECTION);start=j.atom(center['id']);end=j.point(start['x'],start['y']-90)
        rejected=j.drag(start,end,before['snapshot']['revision'])
        require(j.js("/valence/i.test(document.getElementById('toast').textContent)") is True,'pointer_valence_not_rejected');j.fact('valenceRejected')
        require(rejected==before and j.js(SELECTION)==selected,'pointer_rejection_changed_state');j.preview();j.fact('stateRetained')
        require(j.js("document.getElementById('keyboard-finish-bond').disabled") is True,'pointer_pending_bond_not_cancelled');j.fact('pendingBondCancelled')
        end=j.atom(terminal['id']);origin=j.atom(center['id']);length=math.hypot(end['x']-origin['x'],end['y']-origin['y'])
        target=j.point(end['x']+65*(end['x']-origin['x'])/length,end['y']+65*(end['y']-origin['y'])/length)
        after=j.drag(end,target,before['snapshot']['revision']+1)
        require(len(after['snapshot']['document']['graph']['atoms'])==6 and len(after['snapshot']['document']['graph']['bonds'])==5,'pointer_followup_bond_failed');j.fact('followupValid')
        j.history(after,before['snapshot']['document']);j.fact('undoExact');j.done()

        j.begin('stereo-move');old,before=j.load('[13CH3][C@@H](O)/C=C/[C@H](F)C(=O)[O-]');graph=before['snapshot']['document']['graph']
        require(any(a.get('isotope')==13 for a in graph['atoms']) and any(a.get('charge')==-1 for a in graph['atoms'])
            and any(a.get('chiralTag') for a in graph['atoms']) and any(b.get('stereo') for b in graph['bonds']),'pointer_stereo_fixture');j.fact('annotationsPresent')
        edge=next(b for b in graph['bonds'] if b['order']==2 and b.get('stereo'));j.choose('bond-type','2');j.button('tool-bond')
        a=j.atom(edge['begin']);b=j.atom(edge['end']);j.tap(j.point((a['x']+b['x'])/2,(a['y']+b['y'])/2))
        require(j.ready()==before,'pointer_noop_changed_stereo');j.fact('noopExact')
        j.button('keyboard-select-all');j.button('tool-select');selected=j.js(SELECTION);require(selected['circle']==len(graph['atoms']),'pointer_select_all')
        start=j.atom(graph['atoms'][0]['id']);after=j.drag(start,j.point(start['x']+20,start['y']+12),before['snapshot']['revision']+1)
        translation(before['snapshot']['document'],after['snapshot']['document'],{a['id'] for a in graph['atoms']});same_document(before,after,after['snapshot']['document']);j.fact('uniformTranslation')
        current=j.js(SELECTION);require(current['circle']==len(graph['atoms']) and current['html']!=selected['html'],'pointer_selection_lost');j.fact('selectionRetained')
        undone=j.history(after,before['snapshot']['document']);j.fact('undoExact');j.history(undone,after['snapshot']['document'],True);j.fact('redoExact');j.done()

        j.begin('selection-delete');old,before=j.load('CCCO');a,b,c,d=before['snapshot']['document']['graph']['atoms']
        j.choose('keyboard-target','atom:'+a['id']);j.button('keyboard-select');j.choose('keyboard-target','atom:'+b['id']);j.button('keyboard-toggle-select')
        j.choose('keyboard-target','atom:'+c['id']);j.button('tool-select');require(j.js(SELECTION)['circle']==2,'pointer_selection_count');j.fact('selectionDistinctFromTarget')
        start=j.atom(a['id']);moved=j.drag(start,j.point(start['x']+18,start['y']+16),before['snapshot']['revision']+1)
        translation(before['snapshot']['document'],moved['snapshot']['document'],{a['id'],b['id']});j.fact('onlySelectedMoved')
        require(j.js("document.getElementById('keyboard-target').value")=='atom:'+c['id'],'pointer_inspected_target_changed');j.fact('inspectedTargetRetained')
        remote=next(edge for edge in moved['snapshot']['document']['graph']['bonds'] if {edge['begin'],edge['end']}=={c['id'],d['id']})
        j.choose('keyboard-target','bond:'+remote['id']);j.button('keyboard-toggle-select');s=j.js(SELECTION);require(s['circle']==2 and s['line']==1,'pointer_mixed_selection');j.fact('mixedSelection')
        j.keys(['delete']);deleted=j.ready(moved['snapshot']['revision']+1);expected=copy.deepcopy(moved['snapshot']['document'])
        expected['graph']['atoms']=[c,d];expected['graph']['bonds']=[];same_document(moved,deleted,expected);j.preview();j.fact('deleteExact')
        j.history(deleted,moved['snapshot']['document']);j.fact('undoExact');j.done()

        j.begin('drag-cancel');old,original=j.load('CCO');first=original['snapshot']['document']['graph']['atoms'][0]['id'];j.button('tool-select')
        start=j.atom(first);moved=j.drag(start,j.point(start['x']+18,start['y']+14),original['snapshot']['revision']+1)
        require(moved['snapshot']['document']!=original['snapshot']['document'],'pointer_first_move_noop');j.fact('firstMoveCommitted')
        j.choose('bond-type','1');j.button('tool-bond');start=j.atom(first);j.start_drag(start,j.point(start['x'],start['y']-70));j.preview(line=1)
        j.keys(['undo']);undone=j.ready(moved['snapshot']['revision']+1);same_document(moved,undone,original['snapshot']['document']);j.preview();j.fact('undoDuringDrag')
        j.release();require(j.ready()==undone,'pointer_undo_late_commit');j.fact('undoLateReleaseRetained')
        j.button('tool-select');start=j.atom(first);j.start_drag(start,j.point(start['x']+28,start['y']+20));require(j.js(PREVIEW)['circle']>0,'pointer_move_preview_missing')
        j.keys(['escape']);j.preview();j.fact('escapeCleared');j.release();require(j.ready()==undone,'pointer_escape_late_commit');j.fact('escapeLateReleaseRetained');j.done()

        j.begin('tool-keys');old,before=j.load('CCN');j.button('tool-select')
        selected=lambda kind:j.js('document.querySelector(\'[data-tool="'+kind+'"]\').getAttribute("aria-pressed")')=='true'
        j.keys(['alt-erase']);require(selected('select'),'pointer_alt_activated_tool');j.fact('altIgnored')
        j.keys(['erase-tool']);require(selected('erase'),'pointer_erase_shortcut');j.fact('bareErase')
        j.keys(['select-tool']);require(selected('select'),'pointer_select_shortcut');j.fact('bareSelect')
        j.keys(['erase-tool'],'filename');require(selected('select'),'pointer_text_tool_changed');j.fact('textIsolated')
        j.button('help-button');require(j.js("document.getElementById('about-dialog').open") is True,'pointer_modal_not_open')
        j.keys(['erase-tool'],None);require(selected('select'),'pointer_modal_tool_changed');j.fact('modalIsolated')
        j.keys(['escape'],None);require(j.js("document.getElementById('about-dialog').open") is False,'pointer_modal_not_closed');j.fact('modalEscape')
        require(j.ready()==before,'pointer_shortcuts_changed_document');j.fact('stateRetained');j.done()
        require(observation_failures(report)==[],'pointer_final_evidence_incomplete')
    except Exception as error:
        j.facts['failure']={'kind':type(error).__name__, 'frames':[
            {'module':Path(frame.filename).name,'line':frame.lineno,'function':frame.name}
            for frame in traceback.extract_tb(error.__traceback__)
            if Path(frame.filename).name in ('acceptance_pointer.py','acceptance_pointer_input.py','acceptance_resize.py')]}
        if isinstance(error,AssertionError) and len(error.args)==1 and type(error.args[0]) is str:
            code=error.args[0]
            if code.startswith(('pointer_','erase_','resize_')) and code.replace('_','').isalnum() and len(code)<100:
                j.facts['failure']['code']=code
        raise
    finally:
        # On indeterminate input, leave the driver poisoned and let the existing
        # owned host lifecycle close its isolated process/profile. Never send a
        # global release or reuse it to pursue another gesture.
        if j.gesture is not None:j.js(input_api.CLEAR_POINTER.replace('__NONCE__',j.gesture['nonce']))
        j.driver.close()
