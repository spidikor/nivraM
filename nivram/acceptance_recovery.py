"""Successful native recovery through real bridge, dialogs, files and page epochs."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import time

from .acceptance_editing import STATE,READY,SMILES,require,saved_readback,EditingDialogs
from .acceptance_resize import async_value

CHECKS=frozenset({'native-recovery-source-files','native-recovery-declined',
    'native-recovery-restored-unsaved','native-recovery-separate-save','native-recovery-host-generation'})
FACTS=('firstSavedExact','secondSavedExact','productionRecoveryExact','firstReopenedExact',
    'declineUnchanged','restoredExact','restoredDirty','destinationDetached','separateSaveExact',
    'priorFilesRetained','hostGenerationChanged','oldGenerationRejected','generationBytesRetained',
    'observersRestored')
GESTURES=('edit-import','edit-load','save-project','edit-rotate-cw','edit-save-as',
          'edit-open','recover-project','recover-project','save-project')
CONFIRM_FIELDS=('opened','closed','defaultDialogsEnabled','hasBrowserHandler','exactPromptVerified',
                'ownedControlVerified','actualResultVerified','commandCompleted')


def observation_failures(report):
    value=report.get('recovery_observation')
    if type(value) is not dict or type(value.get('schemaVersion')) is not int or value['schemaVersion']!=1:
        return ['native_recovery_observation_unconfirmed']
    good=all(value.get(k) is True for k in FACTS)
    gestures=value.get('gestures');dialogs=value.get('dialogs');confirmations=value.get('confirmations')
    good=good and type(gestures) is list and len(gestures)==len(GESTURES)
    if type(gestures) is list:
        good=good and [x.get('kind') for x in gestures if type(x) is dict]==list(GESTURES)
        good=good and all(type(x) is dict and x.get('status')=='passed' and x.get('isTrusted') is True
                        and x.get('userActivation') is True for x in gestures)
    good=good and type(dialogs) is list and len(dialogs)==4
    if type(dialogs) is list:
        good=good and [x.get('mode') for x in dialogs if type(x) is dict]==['save','save','open','save']
        good=good and all(type(x) is dict and all(x.get(k) is True for k in
            ('originalReturned','actualReturnVerified','fixtureExited')) for x in dialogs)
    good=good and type(confirmations) is list and len(confirmations)==2
    if type(confirmations) is list:
        good=good and [x.get('decision') for x in confirmations if type(x) is dict]==[False,True]
        good=good and all(type(x) is dict and all(x.get(k) is True for k in CONFIRM_FIELDS) for x in confirmations)
    return [] if good else ['native_recovery_observation_unconfirmed']


def run(*,evaluate,poll,choose,check,stage,report,profile,api,window):
    import webview
    from .acceptance_click import click_png_button
    from .acceptance_recovery_confirmation import RecoveryConfirmation

    deadline=time.monotonic()+120
    facts=dict(schemaVersion=1,gestures=[],dialogs=[],confirmations=[])
    report['recovery_observation']=facts
    report['limitations'].append('Recovery confirmations are real browser dialogs answered through the owned WebView2 SDK. Dialog dispatch/result/closure are observed; painted dialog pixels are not inspected.')
    root=Path(profile)/'synthetic fixtures';root.mkdir(exist_ok=False)
    paths=[root/name for name in ('original.nivram','comparison.nivram','recovered.nivram')]
    def remaining():
        value=deadline-time.monotonic();require(value>0,'recovery_scenario_deadline')
        return min(20,value)
    def state():
        poll(READY,timeout=remaining())
        return evaluate(STATE,timeout=min(10,remaining()),stage='recovery-state')
    def click(kind):
        facts['gestures'].append(click_png_button(window,evaluate,kind));stage(kind)
    def saved(path,document):
        poll('!window.nivramDocument.getSnapshot().dirty && document.getElementById("save-status").textContent==="Saved state"',timeout=remaining())
        require(api._path==path,'recovery_save_destination')
        data,digest=saved_readback(path,document)
        return data,digest
    poll('typeof window.nivramDocument.getSnapshot().recoveryGeneration==="string" && !!window.nivramNative',timeout=remaining())
    original=state();click('edit-import')
    evaluate('document.getElementById("structure-input").value='+json.dumps(SMILES)+'; true')
    click('edit-load')
    poll(READY+' && window.nivramDocument.getSnapshot().revision==='+str(original['snapshot']['revision']+2),timeout=remaining())
    first=state()
    dialogs=EditingDialogs(window,choose,paths,webview.FileDialog.SAVE,webview.FileDialog.OPEN,remaining)
    dialogs.plan.append((webview.FileDialog.SAVE,paths[2],'save'))
    chooser=RecoveryConfirmation(window,f'http://127.0.0.1:{api._server.server_port}/')
    try:
        chooser.attach()
        with dialogs:
            click('save-project')
            first_bytes,first_hash=saved(paths[0],first['snapshot']['document'])
            facts['firstSavedExact']=True
            first=state()
            click('edit-rotate-cw')
            poll(READY+' && window.nivramDocument.getSnapshot().revision==='+str(first['snapshot']['revision']+1),timeout=remaining())
            second=state()
            require(second['snapshot']['document']!=first['snapshot']['document'],'recovery_versions_not_distinct')
            end=time.monotonic()+5;latest=None
            while time.monotonic()<end:
                rows=api._store.list_recovery()
                if rows and api._store.recover(rows[0]['id'])['document']==second['snapshot']['document']:
                    latest=rows[0];break
                time.sleep(.05)
            require(latest is not None,'recovery_production_autosave_missing')
            record_path=api._store.recovery_dir/(latest['id']+'.json')
            record_bytes=record_path.read_bytes()
            facts['productionRecoveryExact']=True
            click('edit-save-as')
            second_bytes,second_hash=saved(paths[1],second['snapshot']['document'])
            facts['secondSavedExact']=True
            click('edit-open')
            poll(READY+' && window.nivramDocument.getSnapshot().revision>'+str(second['snapshot']['revision']),timeout=remaining())
            poll('!window.nivramDocument.getSnapshot().dirty',timeout=remaining())
            reopened=state()
            require(reopened['snapshot']['document']==first['snapshot']['document'] and api._path==paths[0],
                    'recovery_first_reopen')
            require(api._store.list_recovery()[0]==latest and record_path.read_bytes()==record_bytes,
                    'recovery_record_changed_before_prompt')
            facts['firstReopenedExact']=True
            check('native-recovery-source-files')

            message=evaluate("'Open the most recent readable recovery from '+new Date("+
                str(latest['created_ns'])+"/1e6).toLocaleString()+' as an unsaved project?'")
            chooser.arm(message,False);click('recover-project');chooser.finish()
            require(state()==reopened and api._path==paths[0],'recovery_decline_changed')
            facts['declineUnchanged']=True
            check('native-recovery-declined')

            chooser.arm(message,True);click('recover-project');chooser.finish()
            poll('document.getElementById("toast").textContent.startsWith("Recovery opened as an unsaved project.")',timeout=remaining())
            restored=state()
            facts['restorationObserved']=dict(documentExact=restored['snapshot']['document']==second['snapshot']['document'],
                revisionBefore=reopened['snapshot']['revision'],revisionAfter=restored['snapshot']['revision'],
                dirty=restored['snapshot']['dirty'],pathDetached=api._path is None,
                acknowledgementSettled=api._pending_recovery is None)
            # Opening the distinct document commits one revision; markUnsaved
            # advances again to fence receipts from the previous destination.
            require(restored['snapshot']['document']==second['snapshot']['document']
                    and restored['snapshot']['revision']==reopened['snapshot']['revision']+2,
                    'recovery_restore_not_exact')
            require(restored['snapshot']['dirty'] is True and api._path is None
                    and api._pending_recovery is None,'recovery_restore_not_unsaved')
            facts.update(restoredExact=True,restoredDirty=True,destinationDetached=True)
            check('native-recovery-restored-unsaved')

            click('save-project')
            saved(paths[2],second['snapshot']['document'])
            require(paths[0].read_bytes()==first_bytes and paths[1].read_bytes()==second_bytes
                and record_path.read_bytes()==record_bytes,'recovery_prior_bytes_changed')
            facts.update(separateSaveExact=True,priorFilesRetained=True)
            check('native-recovery-separate-save')

        old_generation=state()['snapshot']['recoveryGeneration']
        before_reload={p.name:p.read_bytes() for p in api._store.recovery_dir.iterdir()}
        stage('recovery-host-reload')
        def reload_current():
            require(str(chooser.control.CoreWebView2.Source)==chooser.url,'recovery_reload_origin')
            # The vendor load_url setter can leave an identical URI unchanged
            # after clearing its ready events. Request an actual SDK reload.
            window.events.loaded.clear()
            window.events.before_load.clear()
            window.events._pywebviewready.clear()
            chooser.control.CoreWebView2.Reload()
        chooser._ui(reload_current)
        require(window.events.loaded.wait(min(15,remaining())),'recovery_reload_not_loaded')
        poll('!!window.nivramNative && typeof window.nivramDocument?.getSnapshot().recoveryGeneration==="string"'
             ' && window.nivramDocument.getSnapshot().recoveryGeneration!=='+json.dumps(old_generation),timeout=remaining())
        fresh=state()
        response=async_value('window.pywebview.api.autosave_project('+
            json.dumps(second['snapshot']['document'])+','+json.dumps(old_generation)+',999)',evaluate,poll,remaining)
        require(response==dict(status='superseded',generation=old_generation,revision=999),'recovery_old_generation_accepted')
        require(state()==fresh and {p.name:p.read_bytes() for p in api._store.recovery_dir.iterdir()}==before_reload,
                'recovery_old_generation_changed_bytes')
        facts.update(hostGenerationChanged=True,oldGenerationRejected=True,generationBytesRetained=True)
        check('native-recovery-host-generation')
    finally:
        facts['dialogs']=dialogs.snapshot()
        try:chooser.close()
        finally:
            facts['confirmations']=copy.deepcopy(chooser.rows)
            if chooser.error:facts['confirmationError']=chooser.error
        facts['observersRestored']=window.create_file_dialog==dialogs.original and chooser.closed and not chooser.attached
    require(not observation_failures(report),'recovery_observations_incomplete')
