"""Recovered editor contract exercised through the owned native input channel.

Only synthetic molecules are used. Observations never invoke editor mutation or
history APIs. Two explicitly synthetic response boundaries wrap real successful
reflection requests; normal actions use trusted production buttons and keys.
"""
from __future__ import annotations

import copy
import json
import math
import re
import statistics
import traceback
from pathlib import Path

from . import acceptance_pointer as pointer
from .acceptance_editing import require
from .acceptance_resize import (async_value, WORLD, MEASURE, fitted, world_equal,
                                ARM_HOLD, RESTORE_HOLD, LARGE)

KINDS = ('empty-menu', 'rings-scale', 'rotations', 'reflection-chiral',
         'reflection-meso', 'reflection-selection', 'reflection-reject-cancel',
         'bond-keys', 'delete-context', 'erase-partial-bond', 'palette-theme')
CHECKS = frozenset('native-editor-'+kind for kind in KINDS)
FACTS = {
 'empty-menu': ('emptyDisabled', 'escapeRetained'),
 'rings-scale': ('benzeneDefaults', 'firstRingCalibrated', 'manualViewRetained', 'smallImportCapped', 'largeImportFits'),
 'rotations': ('twelveThirtyDegreeSteps', 'fullTurnExactChemistry', 'counterclockwiseUndo', 'selectedOnly', 'undoExact'),
 'reflection-chiral': ('bothModesBothAxes', 'coordinatesExact', 'perCenterStereo', 'molRoundtrip', 'undoExact'),
 'reflection-meso': ('independentMesoControl', 'bothModesBothAxes', 'coordinatesExact', 'perCenterStereo', 'molRoundtrip', 'undoExact'),
 'reflection-selection': ('unselectedBondTargetsComponent', 'bothModesBothAxes', 'otherFragmentExact', 'undoExact'),
 'reflection-reject-cancel': ('keyboardTraversal', 'syntheticRejectionRetained', 'pendingStateRetained', 'cancelLateRetained', 'fetchRestored', 'followupValid'),
 'bond-keys': ('navigationKeyIgnored', 'threeBondOrders', 'toolRetained', 'blankSetsTool', 'numberInputIsolated'),
 'delete-context': ('filenameEditable', 'modalEditable', 'deleteAndBackspace', 'undoExact', 'emptySelectionRetained'),
 'erase-partial-bond': ('partialPreviewOnly', 'incidentBondsOnly', 'singleBondPreservesAtoms', 'wholeMarquee', 'undoExact'),
 'palette-theme': ('curatedElements', 'navigationAndSelection', 'escapeAndTab', 'outsideDismissal', 'paletteDocumentRetained',
                   'themeDocumentRetained', 'elementColorsRetained', 'neutralCanvas', 'displayOnlyHalo', 'themeRestored'),
}
BUTTONS = {**pointer.BUTTONS,
 **{'tool-ring': 'document.querySelector(\'[data-tool="ring"]\')'},
 **{name: 'document.getElementById('+json.dumps(name)+')' for name in (
    'rotate-cw','rotate-ccw','zoom-out','keyboard-clear-selection','more-elements-button','theme-toggle',
    'canvas-flip-horizontal','canvas-flip-vertical','canvas-mirror-horizontal','canvas-mirror-vertical')},
 'element-H': 'document.querySelector(\'[data-element="H"]\')'}
CALIBRATED_BOND = 70.47475694468609
CHIRAL = 'N[C@@H](C)C(=O)O'
MESO = 'C[C@H](O)[C@H](O)C'

PAINT = r"""(()=>({
 theme:document.documentElement.dataset.theme,
 canvas:getComputedStyle(document.querySelector('.canvas-wrap')).backgroundColor,
 paints:Array.from(document.querySelectorAll('#drawing path,#drawing line,#drawing polygon,#drawing polyline,#drawing text,#drawing tspan,#drawing circle,#drawing ellipse'),n=>{
  const s=getComputedStyle(n);return {tag:n.localName,classes:n.getAttribute('class')||'',inline:n.getAttribute('style')||'',
    d:n.getAttribute('d')||'',fill:s.fill,stroke:s.stroke,strokeWidth:s.strokeWidth,filter:s.filter};})
}))()"""


def chemistry(graph):
    from rdkit import Chem
    from .chemistry import graph_to_mol, export_mol
    mol=graph_to_mol(graph)
    identity=Chem.MolToSmiles(mol,isomericSmiles=True)
    centers=dict(Chem.FindMolChiralCenters(mol,force=True,includeUnassigned=True))
    restored=Chem.MolFromMolBlock(export_mol(graph),removeHs=False,strictParsing=True)
    require(restored is not None and Chem.MolToSmiles(restored,isomericSmiles=True)==identity
        and dict(Chem.FindMolChiralCenters(restored,force=True,includeUnassigned=True))==centers,
        'editor_mol_roundtrip')
    return identity,centers


def rotation(before,after,degrees,selected=None):
    expected=copy.deepcopy(before);atoms=expected['graph']['atoms']
    chosen=[a for a in atoms if selected is None or a['id'] in selected]
    require(len(chosen)>=2,'editor_rotation_fixture')
    cx,cy=(math.fsum(a[k] for a in chosen)/len(chosen) for k in ('x','y'))
    co,si=math.cos(math.radians(degrees)),math.sin(math.radians(degrees))
    for a in chosen:
        dx,dy=a['x']-cx,a['y']-cy;a['x'],a['y']=cx+co*dx-si*dy,cy+si*dx+co*dy
    actual=copy.deepcopy(after)
    require(len(actual['graph']['atoms'])==len(atoms),'editor_rotation_atoms')
    for a,b in zip(actual['graph']['atoms'],atoms):
        for k in ('x','y'):
            require(type(a[k]) in (int,float) and math.isfinite(a[k]) and abs(a[k]-b[k])<1e-9,'editor_rotation_coordinates')
            a[k]=b[k]
    require(actual==expected,'editor_rotation_other_fields')
    require(chemistry(before['graph'])==chemistry(after['graph']),'editor_rotation_chemistry')


def reflection(before,after,mode,axis,selected=None):
    old,new=before['graph'],after['graph']
    require({k:v for k,v in before.items() if k!='graph'}=={k:v for k,v in after.items() if k!='graph'},'editor_reflection_settings')
    ids=[a['id'] for a in old['atoms']];chosen=set(ids if selected is None else selected)
    require([a['id'] for a in new['atoms']]==ids and chosen<=set(ids) and chosen,'editor_reflection_atoms')
    coordinate='x' if axis=='horizontal' else 'y';other='y' if coordinate=='x' else 'x'
    extent=min(a[coordinate] for a in old['atoms'] if a['id'] in chosen)+max(a[coordinate] for a in old['atoms'] if a['id'] in chosen)
    for a,b in zip(old['atoms'],new['atoms']):
        if a['id'] not in chosen:require(a==b,'editor_other_fragment_changed');continue
        require(abs(b[coordinate]-(extent-a[coordinate]))<1e-9 and b[other]==a[other],'editor_reflection_coordinates')
        require({k:v for k,v in a.items() if k not in ('x','y','chiralTag')}==
                {k:v for k,v in b.items() if k not in ('x','y','chiralTag')},'editor_reflection_atom_metadata')
    require([{k:v for k,v in b.items() if k!='direction'} for b in old['bonds']]==
            [{k:v for k,v in b.items() if k!='direction'} for b in new['bonds']],'editor_reflection_bonds')
    old_identity,old_centers=chemistry(old);new_identity,new_centers=chemistry(new)
    expected={i:('S' if c=='R' else 'R') if mode=='mirror' and ids[i] in chosen and c in ('R','S') else c
              for i,c in old_centers.items()}
    require(new_centers==expected,'editor_reflection_stereo')
    if mode=='flip':require(new_identity==old_identity,'editor_flip_identity')
    return dict(mode=mode,axis=axis,identityUnchanged=old_identity==new_identity,
                beforeCenters=old_centers,afterCenters=new_centers,selectedCount=len(chosen))


def theme_paint(light,dark):
    require(light['theme']=='light' and dark['theme']=='dark' and dark['canvas']=='rgb(16, 16, 16)','editor_theme_surface')
    require(len(light['paints'])==len(dark['paints'])>0,'editor_theme_primitives')
    colored=bonds=halos=0
    for a,b in zip(light['paints'],dark['paints']):
        require(all(a[k]==b[k] for k in ('tag','classes','inline','d','strokeWidth')),'editor_theme_geometry')
        bond=bool(re.search(r'(?:^|\s)bond-\d+(?:\s|$)',a['classes']))
        primitive=a['tag'] in ('path','line','polygon','polyline')
        join=(a['tag']=='path' and not a['classes'] and bool(re.fullmatch(r'M [-.\d]+,[-.\d]+ L [-.\d]+,[-.\d]+ L [-.\d]+,[-.\d]+',a['d']))
              and a['fill']=='none' and a['stroke']=='rgb(0, 0, 0)')
        for k in ('stroke','fill'):
            neutral=bool(re.search(r'(?:^|;)'+k+r':#000000(?:;|$)',a['inline']))
            changed=(bond and primitive and neutral) or (join and k=='stroke')
            require(b[k]==('rgb(214, 214, 214)' if changed else a[k]),'editor_theme_element_color')
            bonds+=int(changed);colored+=int(a[k] not in ('none','rgb(0, 0, 0)'))
        if not bond and re.search(r'(?:^|\s)atom-\d+(?:\s|$)',a['classes']) and a['tag'] in ('path','text','tspan'):
            require(a['filter']=='none' and 'drop-shadow' in b['filter'],'editor_theme_label_halo');halos+=1
    require(colored>0 and bonds>0 and halos>0,'editor_theme_fixture')
    return dict(coloredProperties=colored,neutralBondProperties=bonds,labelHalos=halos)


def context_event(value,point):
    require(type(value) is dict and value.get('trusted') is True and value.get('inside') is True
            and type(value.get('button')) is int and value['button']==2,'editor_context_untrusted')
    require(all(type(value.get(k)) in (int,float) and math.isfinite(value[k]) and abs(value[k]-point[k])<.01 for k in ('x','y')),'editor_context_coordinates')


def canonical_drawing(value):
    """Ignore only generated gradient names, preserving definitions and all uses.

    bond-paint.mjs allocates fresh scoped IDs for every sanitized render. Compare
    their bijection rather than treating the allocation counter as drawing data.
    """
    require(type(value) is str and len(value)<500000,'editor_drawing_type')
    token=r'nivram-bond-[0-9a-f]{32}-[0-9]+'
    ids=re.findall(r'id="('+token+r')"',value)
    refs=re.findall(r'url\(#('+token+r')\)',value)
    require(len(ids)==len(set(ids)) and set(ids)==set(refs),'editor_gradient_links')
    names={name:'nivram-gradient-'+str(i) for i,name in enumerate(ids)}
    require(set(re.findall(token,value))==set(names),'editor_gradient_unknown')
    return re.sub(token,lambda m:names[m[0]],value)


def observation_failures(report):
    value=report.get('editor_observation')
    if type(value) is not dict or type(value.get('schemaVersion')) is not int or value['schemaVersion']!=1:return ['editor_observation_missing']
    cases=value.get('cases')
    if type(cases) is not list or len(cases)!=len(KINDS) or [r.get('kind') for r in cases if type(r) is dict]!=list(KINDS):return ['editor_cases_incomplete']
    try:
        for case in cases:
            require(all(case.get(k) is True for k in FACTS[case['kind']]),'facts')
            require(type(case.get('actions')) is list and case['actions'],'actions')
            for a in case['actions']:
                require(type(a) is dict and a.get('status')=='passed' and a.get('ownershipVerified') is True,'action')
                if a.get('type')=='button':require(a.get('kind') in BUTTONS and a.get('isTrusted') is True and a.get('userActivation') is True,'button')
                elif a.get('type')=='keys':pointer.key_events(a.get('events'),a.get('kinds'))
                elif a.get('type')=='context':context_event(a.get('observed'),a.get('point'))
                elif a.get('type')=='drag':pointer.input_api.drag_events(a['observed'],a['start'],a['end'],released=True)
                elif a.get('type')=='tap':
                    rows=pointer.input_api.pointer_events(a.get('observed'))
                    require(sum(r['type']=='pointerdown' for r in rows)==sum(r['type']=='pointerup' for r in rows)==1,'tap')
                else:require(False,'unknown-action')
            if case['kind'].startswith('reflection-') and case['kind']!='reflection-reject-cancel':
                require([(r['mode'],r['axis']) for r in case['reflections']]==[(m,a) for m in ('flip','mirror') for a in ('horizontal','vertical')],'reflection-coverage')
            if case['kind']=='reflection-reject-cancel':require(case.get('syntheticBoundaries')==['422-after-real-200','cancel-before-late-real-200'],'boundaries')
    except (AssertionError,KeyError,TypeError,ValueError,pointer.click.NativeClickRejected):return ['editor_evidence_incomplete']
    return []


class Journey(pointer.Journey):
    buttons=BUTTONS
    key_targets=(*pointer.Journey.key_targets,'export-width','structure-input')

    def __init__(self,*args):
        super().__init__(*args)
        report=args[4];report['editor_observation']=report.pop('pointer_observation')
        self.facts.pop('metricsRestored')
    def done(self):
        require(all(self.row.get(k) is True for k in FACTS[self.row['kind']]),'editor_case_incomplete')
        self.check('native-editor-'+self.row['kind'],facts=list(FACTS[self.row['kind']]))
    def context(self,point=None):
        p=self.empty() if point is None else point
        # Context targeting needs a point within the bond hit area, not a
        # subpixel boundary. Request exact whole CSS pixels and verify them.
        p={**p,'x':round(p['x']),'y':round(p['y'])}
        self.js("(()=>{const c=document.getElementById('canvas');window.__nivramEditorContext=null;c.addEventListener('contextmenu',e=>{window.__nivramEditorContext={trusted:e.isTrusted,inside:c.contains(e.target),button:e.button,x:e.clientX,y:e.clientY};},{once:true});return true;})()")
        self.driver.right_click(p);seen=self.js('window.__nivramEditorContext')
        self.row['lastContextObservation']={'observed':seen,'point':p};context_event(seen,p)
        require(self.js("document.getElementById('canvas-context-menu').hidden") is False,'editor_context_closed')
        self.record('context',observed=seen,point=p)
    def focus(self,expected):
        observed=self.js('document.activeElement.id||document.activeElement.dataset.element')
        self.row['lastFocusObservation']={'expected':expected,'observed':observed}
        require(observed==expected,'editor_focus_wrong')
    def world(self):return async_value(WORLD,self.evaluate,self.poll,self.remaining)
    def median_bond(self):
        points={p['id']:p for p in self.geometry()['points']}
        return statistics.median(math.hypot(points[b['begin']]['x']-points[b['end']]['x'],points[b['begin']]['y']-points[b['end']]['y'])
                                 for b in self.state()['snapshot']['document']['graph']['bonds'])
    def revision(self):return self.state()['snapshot']['revision']
    def reflect(self,before,mode,axis,selected=None,point=None):
        self.context(point);scope=self.js("document.getElementById('canvas-context-scope').textContent")
        if selected is not None:require(scope==str(len(selected))+' selected atoms','editor_context_component')
        self.button('canvas-'+mode+'-'+axis);after=self.ready(before['snapshot']['revision']+1)
        pointer.same_document(before,after,after['snapshot']['document'])
        row=reflection(before['snapshot']['document'],after['snapshot']['document'],mode,axis,selected)
        self.row.setdefault('reflections',[]).append(row)
        restored=self.history(after,before['snapshot']['document'])
        if canonical_drawing(restored['drawing'])!=canonical_drawing(before['drawing']):
            self.row['undoDrawingDifference']={'before':before['drawing'],'after':restored['drawing']}
        require(canonical_drawing(restored['drawing'])==canonical_drawing(before['drawing']),'editor_undo_drawing')
        return restored


def run(*,evaluate,poll,check,stage,report,window,**unused):
    j=Journey(evaluate,poll,check,stage,report,window)
    try:
        poll("typeof window.nivramDocument.getSnapshot().recoveryGeneration==='string'",timeout=j.remaining())
        j.begin('empty-menu');initial=j.ready();require(not initial['snapshot']['document']['graph']['atoms'],'editor_initial_not_empty')
        j.context();require(j.js("Array.from(document.querySelectorAll('#canvas-context-menu [role=menuitem]'),n=>n.disabled)")==[True]*6,'editor_empty_menu_enabled')
        j.fact('emptyDisabled');j.keys(['escape'],None);j.focus('canvas');require(j.ready()==initial,'editor_empty_escape_changed');j.fact('escapeRetained');j.done()

        j.begin('rings-scale');require(j.js("[document.getElementById('ring-type').value,document.getElementById('keyboard-ring-type').value]")==['benzene','benzene'],'editor_ring_defaults');j.fact('benzeneDefaults')
        j.button('tool-ring');j.geometry();b=j.geo['bounds'];p=j.point(b['left']+(b['right']-b['left'])*.55,b['top']+(b['bottom']-b['top'])*.5)
        rev=j.revision();j.tap(p);ring=j.ready(rev+1);require(len(ring['snapshot']['document']['graph']['atoms'])==6,'editor_first_ring_count')
        require(chemistry(ring['snapshot']['document']['graph'])[0]=='c1ccccc1','editor_first_ring_identity')
        distance=j.median_bond();require(abs(distance-CALIBRATED_BOND)<=CALIBRATED_BOND*.05,'editor_first_ring_scale');j.row['medianBondCss']=distance;j.fact('firstRingCalibrated')
        j.button('zoom-out');world=j.world();j.geometry();b=j.geo['bounds'];rev=j.revision();j.tap(j.point(b['left']+(b['right']-b['left'])*.2,b['top']+(b['bottom']-b['top'])*.5));two=j.ready(rev+1)
        require(len(two['snapshot']['document']['graph']['atoms'])==12,'editor_second_ring_count');world_equal(j.world(),world)
        j.button('rotate-cw');j.ready(rev+2);world_equal(j.world(),world);j.fact('manualViewRetained')
        j.load('CC');require(j.median_bond()<=CALIBRATED_BOND*1.01,'editor_small_import_scale');j.fact('smallImportCapped')
        j.load(LARGE);fitted(j.js(MEASURE));j.fact('largeImportFits');j.done()

        j.begin('rotations');_,before=j.load(CHIRAL);j.button('tool-select');start=before
        for _ in range(12):
            j.button('rotate-cw');after=j.ready(before['snapshot']['revision']+1);pointer.same_document(before,after,after['snapshot']['document'])
            rotation(before['snapshot']['document'],after['snapshot']['document'],-30);before=after
        rotation(start['snapshot']['document'],before['snapshot']['document'],360);j.fact('twelveThirtyDegreeSteps','fullTurnExactChemistry')
        j.button('rotate-ccw');after=j.ready(before['snapshot']['revision']+1);rotation(before['snapshot']['document'],after['snapshot']['document'],30)
        j.history(after,before['snapshot']['document']);j.fact('counterclockwiseUndo')
        _,before=j.load('CCCC.CC');ids=[a['id'] for a in before['snapshot']['document']['graph']['atoms'][:2]]
        j.button('resize-keyboard');j.choose('keyboard-target','atom:'+ids[0]);j.button('keyboard-select');j.choose('keyboard-target','atom:'+ids[1]);j.button('keyboard-toggle-select')
        j.button('rotate-ccw');after=j.ready(before['snapshot']['revision']+1);rotation(before['snapshot']['document'],after['snapshot']['document'],30,ids);pointer.same_document(before,after,after['snapshot']['document'])
        j.fact('selectedOnly');j.history(after,before['snapshot']['document']);j.fact('undoExact');j.done()

        for kind,smiles in [('reflection-chiral',CHIRAL),('reflection-meso',MESO)]:
            j.begin(kind);_,before=j.load(smiles)
            if kind=='reflection-meso':
                from rdkit import Chem
                original=Chem.MolFromSmiles(MESO);inverse=Chem.Mol(original)
                for a in inverse.GetAtoms():a.InvertChirality()
                control=Chem.MolFromSmiles('C[C@H](O)[C@@H](O)C');inverse_control=Chem.Mol(control)
                for a in inverse_control.GetAtoms():a.InvertChirality()
                require(dict(Chem.FindMolChiralCenters(original,force=True))=={1:'S',3:'R'}
                        and Chem.MolToSmiles(original)==Chem.MolToSmiles(inverse)
                        and Chem.MolToSmiles(control)!=Chem.MolToSmiles(inverse_control),'editor_meso_control');j.fact('independentMesoControl')
            for mode in ('flip','mirror'):
                for axis in ('horizontal','vertical'):
                    before=j.reflect(before,mode,axis)
                    require(j.row['reflections'][-1]['identityUnchanged'] is (kind=='reflection-meso' or mode=='flip'),'editor_reflection_identity')
            j.fact('bothModesBothAxes','coordinatesExact','perCenterStereo','molRoundtrip','undoExact');j.done()

        j.begin('reflection-selection');_,before=j.load(CHIRAL+'.CCO');ids=[a['id'] for a in before['snapshot']['document']['graph']['atoms'][:6]]
        j.choose('keyboard-target','atom:'+ids[0]);j.button('keyboard-select')
        for mode in ('flip','mirror'):
            for axis in ('horizontal','vertical'):
                p=j.js("(()=>{const n=document.querySelector('#drawing .bond-2'),q=n.getPointAtLength(n.getTotalLength()/2).matrixTransform(n.getScreenCTM());return {x:q.x,y:q.y,viewportWidth:innerWidth,viewportHeight:innerHeight};})()")
                before=j.reflect(before,mode,axis,ids,p)
        j.fact('unselectedBondTargetsComponent','bothModesBothAxes','otherFragmentExact','undoExact');j.done()

        j.begin('reflection-reject-cancel');_,before=j.load(CHIRAL)
        j.keys(['context-menu']);j.focus('canvas-flip-horizontal');j.keys(['down'],None);j.focus('canvas-flip-vertical');j.keys(['end'],None);j.focus('canvas-rotate-cw');j.keys(['escape'],None);j.focus('canvas');require(j.ready()==before,'editor_menu_navigation_changed');j.fact('keyboardTraversal')
        for rejected in (True,False):
            j.context();require(j.js(ARM_HOLD.replace("'/api/render'","'/api/reflect'").replace('__REJECT__','true' if rejected else 'false')) is True,'editor_hold_arm')
            j.button('canvas-mirror-horizontal');j.poll('window.__nivramResizeHold?.arrived===true',timeout=j.remaining())
            require(j.state()==before,'editor_pending_reflection_changed');j.fact('pendingStateRetained')
            if not rejected:j.keys(['escape']);require(j.ready()==before,'editor_cancel_reflection_changed')
            j.js('window.__nivramResizeHold.release();true');j.poll('window.__nivramResizeHold?.finished===true',timeout=j.remaining())
            require(j.ready()==before,'editor_rejected_or_late_reflection_changed')
            require(j.js(RESTORE_HOLD) is True,'editor_hold_not_restored')
            j.fact('syntheticRejectionRetained' if rejected else 'cancelLateRetained')
        j.row['syntheticBoundaries']=['422-after-real-200','cancel-before-late-real-200'];j.fact('fetchRestored')
        j.reflect(before,'flip','vertical');j.fact('followupValid');j.done()

        j.begin('bond-keys');_,before=j.load('CC');j.button('tool-select');points=j.geometry()['points'];p=j.point((points[0]['x']+points[1]['x'])/2,(points[0]['y']+points[1]['y'])/2);j.driver.mouse('move',p)
        j.keys(['numpad3-navigation']);require(j.ready()==before,'editor_navigation_edited_bond');j.fact('navigationKeyIgnored')
        for kind,order in [('digit2',2),('numpad3-number',3),('digit1',1)]:
            points=j.geometry()['points'];p=j.point((points[0]['x']+points[1]['x'])/2,(points[0]['y']+points[1]['y'])/2);j.driver.mouse('move',p)
            j.keys([kind]);after=j.ready(before['snapshot']['revision']+1);require(after['snapshot']['document']['graph']['bonds'][0]['order']==order,'editor_bond_order')
            require(j.js("document.querySelector('[data-tool=select]').getAttribute('aria-pressed')")=='true','editor_bond_tool_changed');before=after
        j.fact('threeBondOrders','toolRetained');j.driver.mouse('move',j.empty());j.keys(['digit2']);require(j.ready()==before,'editor_blank_key_changed_graph')
        require(j.js("document.querySelector('[data-tool=bond]').getAttribute('aria-pressed')")=='true' and j.js("document.getElementById('bond-type').value")=='2','editor_blank_key_tool');j.fact('blankSetsTool')
        j.keys(['select-all','digit3'],'export-width');after=j.ready();require(j.js("document.getElementById('export-width').value")=='3' and j.js("document.getElementById('bond-type').value")=='2','editor_number_input_changed_tool')
        require(after['snapshot']['document']['graph']==before['snapshot']['document']['graph'],'editor_number_input_changed_graph');j.fact('numberInputIsolated');j.done()

        j.begin('delete-context');_,before=j.load('CCO');j.button('keyboard-select-all');j.js("document.getElementById('filename').value='typed';true")
        j.keys(['end','backspace','delete'],'filename');require(j.js("document.getElementById('filename').value")=='type','editor_filename_not_editable')
        require(j.ready()['snapshot']['document']['graph']==before['snapshot']['document']['graph'],'editor_filename_deleted_graph');j.fact('filenameEditable')
        j.button('edit-import');j.js("document.getElementById('structure-input').value='NCC';true");j.keys(['select-all','backspace'],'structure-input')
        require(j.js("document.getElementById('structure-input').value")=='' and j.state()==before,'editor_modal_delete_changed_graph');j.keys(['escape'],None);j.fact('modalEditable')
        for key in ('delete','backspace'):
            j.button('keyboard-select-all');before=j.ready();j.keys([key]);after=j.ready(before['snapshot']['revision']+1)
            expected=copy.deepcopy(before['snapshot']['document']);expected['graph']['atoms']=[];expected['graph']['bonds']=[];pointer.same_document(before,after,expected)
            j.history(after,before['snapshot']['document'])
        j.fact('deleteAndBackspace','undoExact');j.button('keyboard-clear-selection');before=j.ready();j.keys(['backspace','delete']);require(j.ready()==before,'editor_empty_delete_changed');j.fact('emptySelectionRetained');j.done()

        j.begin('erase-partial-bond');_,before=j.load('CCCC');j.button('tool-erase');points=j.geometry()['points']
        chosen=points[:2];ids={p['id'] for p in chosen}
        a=j.point(min(p['x'] for p in chosen)-20,min(p['y'] for p in chosen)-20)
        b=j.point(max(p['x'] for p in chosen)+20,max(p['y'] for p in chosen)+20)
        require({p['id'] for p in points if a['x']<=p['x']<=b['x'] and a['y']<=p['y']<=b['y']}==ids,'editor_partial_erase_fixture')
        graph=before['snapshot']['document']['graph'];incident=[e for e in graph['bonds'] if e['begin'] in ids or e['end'] in ids]
        j.start_drag(a,b);require(j.state()==before,'editor_erase_preview_committed');j.preview(rect=1,circle=2,line=len(incident));j.fact('partialPreviewOnly')
        j.release();after=j.ready(before['snapshot']['revision']+1);expected=copy.deepcopy(before['snapshot']['document'])
        expected['graph']['atoms']=[a for a in graph['atoms'] if a['id'] not in ids]
        expected['graph']['bonds']=[e for e in graph['bonds'] if e not in incident]
        pointer.same_document(before,after,expected);j.fact('incidentBondsOnly');j.history(after,before['snapshot']['document'])
        _,before=j.load('CC.CC');graph=before['snapshot']['document']['graph'];edge=graph['bonds'][0];points={p['id']:p for p in j.geometry()['points']}
        a,b=(points[edge[k]] for k in ('begin','end'));j.tap(j.point((a['x']+b['x'])/2,(a['y']+b['y'])/2));after=j.ready(before['snapshot']['revision']+1)
        expected=copy.deepcopy(before['snapshot']['document']);expected['graph']['bonds']=[e for e in graph['bonds'] if e['id']!=edge['id']]
        pointer.same_document(before,after,expected);j.fact('singleBondPreservesAtoms');before=j.history(after,before['snapshot']['document'])
        points=j.geometry()['points'];a=j.point(min(p['x'] for p in points)-20,min(p['y'] for p in points)-20);b=j.point(max(p['x'] for p in points)+20,max(p['y'] for p in points)+20)
        after=j.drag(a,b,before['snapshot']['revision']+1);expected=copy.deepcopy(before['snapshot']['document']);expected['graph']['atoms']=[];expected['graph']['bonds']=[]
        pointer.same_document(before,after,expected);j.fact('wholeMarquee');j.history(after,before['snapshot']['document']);j.fact('undoExact');j.done()

        j.begin('palette-theme');_,before=j.load('N[C@@H](C)C(=O)O');j.keys(['escape']);before=j.ready()
        require(j.js("({total:document.querySelectorAll('#elements button[data-element]').length,visible:Array.from(document.querySelectorAll('#elements button[data-element]')).filter(n=>n.getClientRects().length>0).length})")==dict(total=32,visible=10),'editor_curated_palette');j.fact('curatedElements')
        j.button('element-H');require(j.js("document.querySelector('[data-element=H]').getAttribute('aria-pressed')")=='true','editor_palette_H')
        j.button('more-elements-button');j.focus('Li')
        for key,focus in [('right','B'),('down','Na'),('up','B'),('end','Lu'),('home','Li'),('left','Lu')]:j.keys([key],None);j.focus(focus)
        j.keys(['enter'],None);j.focus('more-elements-button');require(j.js("document.querySelector('[data-element=Lu]').getAttribute('aria-pressed')")=='true','editor_palette_Lu');j.fact('navigationAndSelection')
        j.keys(['down'],None);j.focus('Lu');j.keys(['escape'],None);j.focus('more-elements-button')
        j.keys(['enter'],None);j.keys(['tab'],None);j.focus('delete-button')
        j.button('more-elements-button');j.keys(['shift-tab'],None);j.focus('I');j.fact('escapeAndTab')
        j.button('more-elements-button');j.button('tool-select');require(j.js("document.getElementById('more-elements-options').hidden") is True,'editor_palette_outside_click')
        j.button('more-elements-button');j.keys(['home'],'filename');require(j.js("document.getElementById('more-elements-options').hidden") is True,'editor_palette_outside_focus');j.fact('outsideDismissal')
        require(j.ready()==before,'editor_palette_changed_document');j.fact('paletteDocumentRetained')
        j.poll("window.nivramTheme.getState().ready===true",timeout=j.remaining());light=j.js(PAINT);require(light['theme']=='light','editor_initial_theme')
        j.button('theme-toggle');async_value('window.nivramTheme.flush()',j.evaluate,j.poll,j.remaining);dark=j.js(PAINT);j.row['paint']=theme_paint(light,dark)
        require(j.ready()==before,'editor_theme_changed_document');j.fact('themeDocumentRetained','elementColorsRetained','neutralCanvas')
        clone=j.js("(()=>{const n=document.getElementById('drawing').cloneNode(true),s=document.createElementNS('http://www.w3.org/2000/svg','svg');s.style.position='fixed';s.style.left='-10000px';s.append(n);document.body.append(s);try{return Array.from(n.querySelectorAll('path,text,tspan'),p=>getComputedStyle(p).filter).every(f=>f==='none');}finally{s.remove();}})()")
        require(clone is True,'editor_halo_leaked_to_clone');j.fact('displayOnlyHalo')
        j.button('theme-toggle');async_value('window.nivramTheme.flush()',j.evaluate,j.poll,j.remaining);require(j.js(PAINT)==light and j.ready()==before,'editor_theme_restore');j.fact('themeRestored');j.done()
        require(observation_failures(report)==[],'editor_final_evidence')
    except Exception as error:
        j.facts['failure']={'kind':type(error).__name__,'frames':[{'module':Path(f.filename).name,'line':f.lineno,'function':f.name}
            for f in traceback.extract_tb(error.__traceback__) if Path(f.filename).name.startswith('acceptance_')]}
        if isinstance(error,AssertionError) and len(error.args)==1 and type(error.args[0]) is str:
            code=error.args[0]
            if code.startswith(('editor_','pointer_','resize_')) and code.replace('_','').isalnum() and len(code)<100:j.facts['failure']['code']=code
        raise
    finally:j.driver.close()
