"""Abrupt native-process recovery fixtures; never a power-loss simulation.

Only these explicit acceptance scenarios install publication barriers or exit.
The producer is expected to fail normal lifecycle acceptance. A separate process
must prove owned-tree cleanup before a consumer may reuse its isolated profile.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

from .acceptance_editing import READY, STATE, SMILES, EditingDialogs, require
from .acceptance_recovery_confirmation import RecoveryConfirmation
from .acceptance_recovery import CONFIRM_FIELDS
from .acceptance_resize import async_value
from .acceptance_save import saved_readback

PHASES=('durable','flushed')
EXIT_CODE=72
CHECKS=frozenset({'native-restart-evidence-retained','native-restart-generation',
                  'native-restart-restored-unsaved','native-restart-separate-save'})
FACTS=('evidenceRetained','previousFileRetained','freshGeneration','oldGenerationRejected',
       'restoredExact','restoredDirty','destinationDetached','separateSaveExact','observersRestored')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def request(profile):
    value=json.loads((Path(profile).parent/'restart-request.json').read_text(encoding='utf-8'))
    require(type(value) is dict and set(value)=={'schemaVersion','phase','nonce','sourceSha256'}
        and type(value['schemaVersion']) is int and value['schemaVersion']==1
        and value['phase'] in PHASES and type(value['nonce']) is str
        and re.fullmatch('[0-9a-f]{32}',value['nonce']) is not None
        and type(value['sourceSha256']) is str
        and re.fullmatch('[0-9a-f]{64}',value['sourceSha256']) is not None,'restart_request_invalid')
    return value


def recovery_pins(store):
    return {p.name:digest(p.read_bytes()) for p in sorted(store.recovery_dir.iterdir())}


def validate_marker(value,config):
    require(type(value) is dict and value.get('request')==config
        and value.get('phase')==config['phase'] and value.get('interruptionExit')==EXIT_CODE
        and value.get('productionAutosave') is True and value.get('fsyncReturned') is True
        and value.get('published') is (config['phase']=='durable'),'restart_marker_invalid')
    require(type(value.get('generation')) is str
        and re.fullmatch('[0-9a-f]{32}',value['generation']) is not None
        and type(value.get('revision')) is int and value['revision']>=1
        and type(value.get('recoveryPins')) is dict and bool(value['recoveryPins']),
        'restart_marker_identity')
    require(type(value.get('gestures')) is list and [x.get('kind') for x in value['gestures']]
        ==['edit-import','edit-load','save-project','edit-rotate-cw']
        and all(x.get('status')=='passed' and x.get('isTrusted') is True
            and x.get('userActivation') is True for x in value['gestures']),'restart_producer_input')
    require(type(value.get('dialogs')) is list and len(value['dialogs'])==1
        and all(value['dialogs'][0].get(k) is True for k in
            ('originalReturned','actualReturnVerified','fixtureExited')),'restart_producer_dialog')


def observation_failures(report):
    value=report.get('restart_observation')
    if type(value) is not dict:return ['native_restart_observation_unconfirmed']
    good=(type(value.get('schemaVersion')) is int and value['schemaVersion']==1
        and value.get('phase') in PHASES and all(value.get(k) is True for k in FACTS))
    gestures=value.get('gestures',[])
    good=good and type(gestures) is list and len(gestures)==2
    if type(gestures) is list:
        good=good and all(type(x) is dict and x.get('status')=='passed'
            and x.get('isTrusted') is True and x.get('userActivation') is True for x in gestures)
        good=good and [x.get('kind') for x in gestures if type(x) is dict]==['recover-project','save-project']
    confirmations=value.get('confirmations',[])
    good=good and type(confirmations) is list and len(confirmations)==1
    if type(confirmations) is list:
        good=good and all(type(x) is dict and x.get('decision') is True
            and all(x.get(k) is True for k in CONFIRM_FIELDS) for x in confirmations)
    dialogs=value.get('dialogs',[])
    good=good and type(dialogs) is list and len(dialogs)==1
    if type(dialogs) is list:
        good=good and all(type(x) is dict and x.get('mode')=='save'
            and all(x.get(k) is True for k in ('originalReturned','actualReturnVerified','fixtureExited'))
            for x in dialogs)
    return [] if good else ['native_restart_observation_unconfirmed']


def run(*,evaluate,poll,choose,check,stage,report,profile,api,window,producer=False):
    import webview
    from .acceptance_click import click_png_button
    from . import persistence

    config=request(profile);profile=Path(profile)
    deadline=time.monotonic()+90
    facts=dict(schemaVersion=1,phase=config['phase'],gestures=[],dialogs=[],confirmations=[])
    report['restart_observation']=facts
    report['limitations'].append('Explicit abrupt process exit at a production autosave boundary; no power loss or storage-controller failure is simulated. Interrupted producer cache is retained. Default confirmation events/results are observed, not painted pixels.')
    def remaining():
        value=deadline-time.monotonic();require(value>0,'restart_deadline')
        return min(20,value)
    def state():
        poll(READY,timeout=remaining())
        return evaluate(STATE,timeout=min(10,remaining()),stage='restart-state')
    def click(kind):
        facts['gestures'].append(click_png_button(window,evaluate,kind));stage(kind)
    def saved(path,document):
        poll('!window.nivramDocument.getSnapshot().dirty && document.getElementById("save-status").textContent==="Saved state"',timeout=remaining())
        require(api._path==path,'restart_save_destination')
        return saved_readback(path,document)[0]
    def wait_recovery(document):
        end=time.monotonic()+6
        while time.monotonic()<end:
            rows=api._store.list_recovery()
            if rows and api._store.recover(rows[0]['id'])['document']==document:return rows[0]
            time.sleep(.05)
        raise AssertionError('restart_production_recovery_missing')

    poll('!!window.nivramNative && typeof window.nivramDocument.getSnapshot().recoveryGeneration==="string"',timeout=remaining())
    fixture=profile/'synthetic fixtures'
    original_path=fixture/'original.nivram'
    recovered_path=fixture/'recovered.nivram'
    paths=[original_path,recovered_path]
    dialogs=EditingDialogs(window,choose,paths,webview.FileDialog.SAVE,webview.FileDialog.OPEN,remaining)
    dialogs.plan=[(webview.FileDialog.SAVE,original_path if producer else recovered_path,'save')]

    if producer:
        fixture.mkdir(exist_ok=False)
        initial=state();click('edit-import')
        evaluate('document.getElementById("structure-input").value='+json.dumps(SMILES)+'; true')
        click('edit-load')
        poll(READY+' && window.nivramDocument.getSnapshot().revision=='+str(initial['snapshot']['revision']+2),timeout=remaining())
        first=state();previous=wait_recovery(first['snapshot']['document'])
        with dialogs:
            click('save-project')
            original_bytes=saved(original_path,first['snapshot']['document'])
        facts['dialogs']=dialogs.snapshot()
        previous_bytes=(api._store.recovery_dir/(previous['id']+'.json')).read_bytes()
        ready=threading.Event();payload={}
        original_autosave=api._store.autosave
        original_atomic=persistence.atomic_write
        original_os=persistence.os
        local=threading.local()

        def interrupt(*,encoded,path,temporary=None):
            require(ready.wait(10),'restart_second_document_not_observed')
            second=payload['second']['snapshot']
            require(json.loads(encoded)['document']==second['document'],'restart_candidate_mismatch')
            require(original_path.read_bytes()==original_bytes
                and (api._store.recovery_dir/(previous['id']+'.json')).read_bytes()==previous_bytes,
                'restart_prior_evidence_changed')
            published=temporary is None
            require((path.read_bytes()==encoded if published else not path.exists())
                and (published or Path(temporary).read_bytes()==encoded),'restart_boundary_bytes')
            value=dict(request=config,phase=config['phase'],interruptionExit=EXIT_CODE,
                productionAutosave=True,fsyncReturned=True,published=published,
                generation=second['recoveryGeneration'],revision=second['revision'],
                previous=previous,previousDocument=first['snapshot']['document'],
                candidateDocument=second['document'],candidateName=path.name,
                candidateSha256=digest(encoded),temporaryName=None if published else Path(temporary).name,
                originalSha256=digest(original_bytes),recoveryPins=recovery_pins(api._store),
                gestures=copy.deepcopy(facts['gestures']),dialogs=copy.deepcopy(facts['dialogs']))
            validate_marker(value,config)
            with (profile.parent/'producer-boundary.json').open('xb') as output:
                output.write(json.dumps(value,sort_keys=True,allow_nan=False).encode('utf-8'))
                output.flush();os.fsync(output.fileno())
            os._exit(EXIT_CODE)

        class BoundaryOS:
            def __getattr__(self,name):return getattr(original_os,name)
            def fsync(self,fd):
                result=original_os.fsync(fd)
                if getattr(local,'candidate',None) is not None:local.flushed=True
                return result
            def rename(self,src,dst):
                pending=getattr(local,'candidate',None)
                if pending is not None:
                    path,encoded=pending
                    require(Path(dst)==path and Path(src).parent==path.parent and local.flushed,
                            'restart_publication_boundary')
                    interrupt(encoded=encoded,path=path,temporary=src)
                return original_os.rename(src,dst)
        def bounded_atomic(path,data,*,exclusive=False):
            path=Path(path)
            if path.parent!=api._store.recovery_dir:return original_atomic(path,data,exclusive=exclusive)
            require(exclusive is True,'restart_publication_not_exclusive')
            require(ready.wait(10),'restart_second_document_not_observed')
            require(json.loads(data)['document']==payload['second']['snapshot']['document'],'restart_unexpected_autosave')
            local.candidate=(path,data);local.flushed=False
            try:return original_atomic(path,data,exclusive=exclusive)
            finally:local.candidate=None
        def durable_autosave(document,generation,revision):
            result=original_autosave(document,generation,revision)
            require(result.get('status')=='recovered-copy-saved','restart_durable_receipt')
            path=api._store.recovery_dir/(result['id']+'.json')
            interrupt(encoded=path.read_bytes(),path=path)
            return result
        try:
            if config['phase']=='durable':api._store.autosave=durable_autosave
            else:
                persistence.os=BoundaryOS()
                persistence.atomic_write=bounded_atomic
            click('edit-rotate-cw')
            poll(READY+' && window.nivramDocument.getSnapshot().revision=='+str(first['snapshot']['revision']+1),timeout=remaining())
            payload['second']=state()
            require(payload['second']['snapshot']['document']!=first['snapshot']['document'],'restart_versions_equal')
            stage('restart-production-autosave-armed');ready.set()
            # The production two-second autosave timer, not a direct storage call,
            # reaches the scoped boundary on a native bridge request thread.
            time.sleep(12)
            raise AssertionError('restart_interruption_not_reached')
        finally:
            api._store.autosave=original_autosave
            persistence.atomic_write=original_atomic;persistence.os=original_os
        return

    marker_path=profile.parent/'producer-boundary.json'
    marker=json.loads(marker_path.read_text(encoding='utf-8'));validate_marker(marker,config)
    require(recovery_pins(api._store)==marker['recoveryPins']
        and digest(original_path.read_bytes())==marker['originalSha256'],'restart_evidence_changed_before_consumer')
    inventory=api._store.recovery_inventory()
    require(inventory['status']=='ready','restart_inventory_unavailable')
    expected=marker['candidateDocument'] if config['phase']=='durable' else marker['previousDocument']
    latest=inventory['records'][0]
    require(api._store.recover(latest['id'])['document']==expected,'restart_selected_wrong_record')
    require(inventory['issues']==dict(invalid=0,unsupported=0,unreadable=0,interrupted=int(config['phase']=='flushed')),
            'restart_inventory_diagnostics')
    facts.update(evidenceRetained=True,previousFileRetained=True)
    check('native-restart-evidence-retained')
    before=state()
    require(before['snapshot']['recoveryGeneration']!=marker['generation'],'restart_generation_reused')
    response=async_value('window.pywebview.api.autosave_project('+json.dumps(marker['candidateDocument'])+','+
        json.dumps(marker['generation'])+','+str(marker['revision']+100)+')',evaluate,poll,remaining)
    require(response==dict(status='superseded',generation=marker['generation'],revision=marker['revision']+100)
        and state()==before and recovery_pins(api._store)==marker['recoveryPins'],'restart_stale_generation_changed')
    facts.update(freshGeneration=True,oldGenerationRejected=True);check('native-restart-generation')
    chooser=RecoveryConfirmation(window,f'http://127.0.0.1:{api._server.server_port}/')
    try:
        chooser.attach()
        message=evaluate("'Open the most recent readable recovery from '+new Date("+str(latest['created_ns'])+
            "/1e6).toLocaleString()+' as an unsaved project?'")
        if config['phase']=='flushed':message='Interrupted recovery files were retained.\n\n'+message
        chooser.arm(message,True);click('recover-project');chooser.finish()
        poll('document.getElementById("toast").textContent.startsWith("Recovery opened as an unsaved project.")',timeout=remaining())
        restored=state()
        require(restored['snapshot']['document']==expected and restored['snapshot']['dirty'] is True
            and restored['snapshot']['revision']==before['snapshot']['revision']+2
            and api._path is None and api._pending_recovery is None,'restart_restore_not_exact_unsaved')
        facts.update(restoredExact=True,restoredDirty=True,destinationDetached=True)
        check('native-restart-restored-unsaved')
        with dialogs:
            click('save-project');saved(recovered_path,expected)
        current=recovery_pins(api._store)
        require(all(current.get(k)==v for k,v in marker['recoveryPins'].items())
            and digest(original_path.read_bytes())==marker['originalSha256'],'restart_prior_evidence_changed_after_save')
        facts['separateSaveExact']=True;check('native-restart-separate-save')
    finally:
        facts['dialogs']=dialogs.snapshot()
        try:chooser.close()
        finally:
            facts['confirmations']=copy.deepcopy(chooser.rows)
            if chooser.error:facts['confirmationError']=chooser.error
        facts['observersRestored']=window.create_file_dialog==dialogs.original and chooser.closed and not chooser.attached
    require(not observation_failures(report),'restart_observations_incomplete')
