"""Native project saves with real storage, owned dialogs and explicit fault seams.

All paths and molecules are synthetic in an exclusive acceptance profile.
The competing replacement uses another thread while the bridge save is held.
Only the post-publication acknowledgement error is injected; actual bytes are
published by the production atomic writer and read back independently.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time

from .acceptance_editing import STATE, READY, SMILES, require, assert_save, saved_readback
from .acceptance_batch_cancel import assert_cancelled_return

CHECKS = frozenset({
    'native-save-cancel', 'native-save-confirmed-update', 'native-save-pending-edit',
    'native-save-competing-replacement', 'native-save-uncertain-publication',
    'native-save-unsupported-and-separate-copy',
})
FACTS = (
    'cancelUnchanged', 'cancelDidNotWrite', 'newSaveExact', 'conditionalUpdateExact',
    'backupExact', 'pendingSnapshotExact', 'newerEditorStillDirty', 'counterwriterJoined',
    'competingBytesRetained', 'conflictUnchanged', 'conflictBackupRetained',
    'publicationObserved', 'uncertainUnchanged', 'uncertainDestinationNotAdopted',
    'recoveryExact', 'uncertainCancelRetained', 'separateCopyExact',
    'hardlinkObserved', 'unsupportedUnchanged', 'unsupportedCancelRetained',
    'finalCopyExact', 'allPriorBytesRetained', 'observersRestored',
)
STATUSES = ('cancelled', 'saved', 'saved', 'saved', 'conflict', 'unconfirmed',
            'cancelled', 'saved', 'unsupported', 'cancelled', 'saved')
SAVE_AS = (True, True, False, False, False, True, True, True, False, True, True)
ACTIONS = ('cancel', 'select', 'select', 'cancel', 'select', 'cancel', 'select')
GESTURES = (
    'edit-import', 'edit-load', 'save-project', 'edit-save-as', 'edit-rotate-cw',
    'save-project', 'edit-rotate-cw', 'save-project', 'edit-rotate-cw',
    'save-project', 'edit-save-as', 'save-project', 'save-project', 'edit-rotate-cw',
    'save-project', 'save-project', 'save-project',
)
INSTALL_OBSERVER = """(() => {
    if(window.__nivramSaveProbe)throw Error('save observer already installed');
    const native=window.pywebview.api, original=native.save_project, calls=[];
    const wrapped=async(...args)=>{
        const row={saveAs:args[2],expectedHash:args[1],settled:false};calls.push(row);
        try {const result=await original(...args);row.result=structuredClone(result);return result;}
        catch(error){row.threw=true;throw error;}
        finally{row.settled=true;}
    };
    native.save_project=wrapped;
    window.__nivramSaveProbe={calls,original,wrapped};return true;
})()"""
REMOVE_OBSERVER = """(() => {
    const p=window.__nivramSaveProbe,n=window.pywebview.api;
    if(!p||n.save_project!==p.wrapped)return false;
    n.save_project=p.original;delete window.__nivramSaveProbe;return true;
})()"""


def unchanged(before, after):
    require(before['snapshot']['dirty'] is True, 'save_expected_unsaved_document')
    require(after == before, 'save_changed_unsaved_document_or_history')


def observation_failures(report):
    f = report.get('save_observation')
    if type(f) is not dict or type(f.get('schemaVersion')) is not int or f['schemaVersion'] != 1:
        return ['native_save_observation_unconfirmed']
    good = all(f.get(k) is True for k in FACTS)
    rows, dialogs, gestures = (f.get(k) for k in ('receipts', 'dialogs', 'gestures'))
    good = good and type(rows) is list and len(rows) == len(STATUSES)
    if good:
        for row, status, save_as in zip(rows, STATUSES, SAVE_AS):
            good = good and type(row) is dict and row.get('saveAs') is save_as and row.get('settled') is True
            result = row.get('result') if type(row) is dict else None
            good = good and type(result) is dict and result.get('status') == status and row.get('threw') is not True
            if type(result) is not dict:
                continue
            if status == 'saved':
                good = good and type(result.get('disk_hash')) is str and bool(re.fullmatch('[0-9a-f]{64}', result['disk_hash']))
                good = good and type(result.get('name')) is str and bool(result['name'])
            else:
                good = good and 'disk_hash' not in result
            if status == 'unconfirmed':
                good = good and result.get('outcome') == 'unknown'
            if status == 'unsupported':
                good = good and result.get('outcome') == 'not_committed'
    good = good and type(dialogs) is list and len(dialogs) == len(ACTIONS)
    if type(dialogs) is list:
        good = good and [r.get('action') for r in dialogs if type(r) is dict] == list(ACTIONS)
        good = good and all(type(r) is dict and all(r.get(k) is True for k in
            ('originalReturned', 'actualReturnVerified', 'fixtureExited')) for r in dialogs)
    good = good and type(gestures) is list and len(gestures) == len(GESTURES)
    if type(gestures) is list:
        good = good and [r.get('kind') for r in gestures if type(r) is dict] == list(GESTURES)
        good = good and all(type(r) is dict and r.get('status') == 'passed'
            and r.get('isTrusted') is True and r.get('userActivation') is True for r in gestures)
    good = good and type(f.get('storeCalls')) is int and f['storeCalls'] == 8
    hashes = f.get('diskHashes')
    good = good and type(hashes) is dict and set(hashes) == {'competing','uncertain','separate','final'}
    if type(hashes) is dict:
        good = good and all(type(v) is str and bool(re.fullmatch('[0-9a-f]{64}', v)) for v in hashes.values())
    return [] if good else ['native_save_observation_unconfirmed']


class SaveDialogs:
    """Exact original SAVE return, with owned real Select or Cancel actions."""
    def __init__(self, window, choose, paths, kind, remaining):
        self.window, self.original, self.records, self.count = window, window.create_file_dialog, [], 0
        self.lock = threading.RLock()
        self.plan = list(zip(ACTIONS, paths))
        require(len(self.plan) == len(ACTIONS), 'save_dialog_plan')
        def observed(*args, **kwargs):
            with self.lock:
                index = self.count; self.count += 1
            require(index < len(self.plan), 'save_extra_dialog')
            require(len(args) == 1 and type(args[0]) is type(kind) and args[0] == kind
                and kwargs == {'save_filename':'structure.nivram','file_types':('nivraM projects (*.nivram)',)},
                'save_dialog_arguments')
            action, path = self.plan[index]
            row = dict(action=action, originalReturned=False, actualReturnVerified=False, fixtureExited=False)
            with self.lock: self.records.append(row)
            choice = choose(path, timeout=remaining(), parent_io=True, action=action,
                            mode='save' if action == 'cancel' else 'file')
            try:
                result = self.original(*args, **kwargs)
                row['originalReturned'] = True
            finally:
                primary = sys.exc_info()[0] is not None
                try:
                    receipt = choice.finish()
                    require(receipt.get('worker_exit_confirmed') is True and
                        (receipt.get('cancelled') is True and receipt.get('selected') is False
                         if action == 'cancel' else receipt.get('selected') is True), 'save_picker_fixture')
                    row['fixtureExited'] = True
                except Exception:
                    if not primary: raise
            if action == 'cancel':
                assert_cancelled_return(result)
            else:
                values = [result] if type(result) is str else result
                require(type(values) in (list,tuple) and len(values) == 1 and type(values[0]) is str
                    and Path(values[0]).resolve() == path.resolve(), 'save_actual_picker_return')
            row['actualReturnVerified'] = True
            return result
        self.observed = observed

    def __enter__(self):
        self.window.create_file_dialog = self.observed
        return self

    def __exit__(self, *exc):
        require(self.window.create_file_dialog is self.observed, 'save_picker_observer_replaced')
        self.window.create_file_dialog = self.original

    def snapshot(self):
        with self.lock: return copy.deepcopy(self.records)


def run(*, evaluate, poll, choose, check, stage, report, profile, api, downloads, window):
    import webview
    from . import persistence
    from .acceptance_click import click_png_button
    from .native_dialog_worker import _fixture_paths

    deadline = time.monotonic() + 120
    facts = dict(schemaVersion=1, dialogs=[], gestures=[], receipts=[], diskHashes={})
    report['save_observation'] = facts
    root = Path(profile) / 'synthetic fixtures'
    root.mkdir(exist_ok=False)
    names = ('cancel.nivram','working.nivram','uncertain.nivram','unused.nivram',
             'separate.nivram','blocked.nivram','final.nivram','counterwriter.nivram','hardlink.nivram')
    paths = [root / name for name in names]
    for p in paths:
        _fixture_paths(Path(profile), p)
        require(not p.exists(), 'save_fixture_not_fresh')
    _, working, uncertain, _, separate, _, final, competitor, hardlink = paths
    original_save, original_atomic = api._store.save, persistence.atomic_write
    mode = {'value':None, 'calls':0, 'injected':0}
    entered, release = threading.Event(), threading.Event()
    store_idle = threading.Event(); store_idle.set()
    writer = None

    def remaining():
        value = deadline - time.monotonic()
        require(value > 0, 'save_scenario_deadline')
        return min(20, value)

    def state(revision=None):
        condition = READY
        if revision is not None: condition += ' && window.nivramDocument.getSnapshot().revision===' + str(revision)
        poll(condition, timeout=remaining())
        return evaluate(STATE, timeout=min(10, remaining()), stage='save-state')

    def click(kind):
        remaining()
        facts['gestures'].append(click_png_button(window, evaluate, kind))
        stage(kind)

    def begin(kind):
        index = evaluate('window.__nivramSaveProbe.calls.length')
        click(kind)
        return index

    def finish(index, status):
        row = poll(f"(() => {{const r=window.__nivramSaveProbe.calls[{index}];return r?.settled?r:false;}})()", timeout=remaining())
        require(type(row) is dict and row.get('threw') is not True and row.get('result', {}).get('status') == status,
            'save_unexpected_bridge_receipt')
        facts['receipts'].append(row)
        # Polling observes the continuation after the bridge promise, never
        # changes the private saving flag or marks a document saved itself.
        return row

    def perform(kind, status):
        return finish(begin(kind), status)

    def unsaved(before):
        after = state()
        unchanged(before, after)
        require(evaluate("document.getElementById('save-status').textContent") != 'Saved state',
            'save_false_saved_status')
        return after

    def saved(path, before, *, newer=None):
        data, digest = saved_readback(path, before['snapshot']['document'])
        require(api._store.open(path)['document'] == before['snapshot']['document'], 'save_store_readback')
        if newer is None:
            poll("!window.nivramDocument.getSnapshot().dirty && document.getElementById('save-status').textContent==='Saved state'", timeout=remaining())
            after = state(); assert_save(before, after)
        else:
            after = unsaved(newer)
        require(api._path == path, 'save_destination_not_adopted')
        return after, data, digest

    def observed_save(path, document, expected_disk_hash=None):
        mode['calls'] += 1
        store_idle.clear()
        try:
            if mode['value'] in ('pending', 'conflict'):
                require(Path(path) == working, 'save_barrier_wrong_path')
                entered.set()
                require(release.wait(min(10, remaining())), 'save_barrier_release_timeout')
            return original_save(path, document, expected_disk_hash)
        finally:
            store_idle.set()

    def atomic(path, data, **kwargs):
        result = original_atomic(path, data, **kwargs)
        if mode['value'] == 'uncertain' and Path(path) == uncertain:
            require(kwargs == {'exclusive':True} and mode['injected'] == 0 and uncertain.read_bytes() == data,
                'save_fault_boundary_not_published')
            mode['injected'] += 1
            facts['publicationObserved'] = True
            raise OSError('Injected acknowledgement loss after actual synthetic publication')
        return result

    api._store.save = observed_save
    persistence.atomic_write = atomic
    installed = False
    try:
        poll("typeof window.nivramDocument.getSnapshot().recoveryGeneration==='string' && !!window.nivramNative", timeout=remaining())
        require(evaluate(INSTALL_OBSERVER) is True, 'save_observer_install')
        installed = True
        before = state()
        require(before['snapshot']['document']['graph']['atoms'] == [], 'save_initial_not_empty')
        click('edit-import')
        evaluate('document.getElementById("structure-input").value=' + json.dumps(SMILES) + '; true')
        click('edit-load')
        current = state(before['snapshot']['revision'] + 2)
        require(current['snapshot']['dirty'] is True, 'save_import_not_dirty')
        with SaveDialogs(window, choose, paths[:7], webview.FileDialog.SAVE, remaining) as dialogs:
            try:
                perform('save-project', 'cancelled')
                unsaved(current)
                require(mode['calls'] == 0 and api._path is None and not any(root.iterdir()), 'save_cancel_wrote')
                facts.update(cancelUnchanged=True, cancelDidNotWrite=True)
                check('native-save-cancel', exactState=True, noStoreCall=True)

                perform('edit-save-as', 'saved')
                current, first_bytes, _ = saved(working, current)
                facts['newSaveExact'] = True
                click('edit-rotate-cw'); current = state(current['snapshot']['revision'] + 1)
                perform('save-project', 'saved')
                current, update_bytes, _ = saved(working, current)
                require(working.with_suffix('.nivram.bak').read_bytes() == first_bytes, 'save_backup_mismatch')
                facts.update(conditionalUpdateExact=True, backupExact=True)
                check('native-save-confirmed-update', actualWindowsConditionalSave=True, exactBackup=True)

                click('edit-rotate-cw'); pending = state(current['snapshot']['revision'] + 1)
                mode['value'] = 'pending'; entered.clear(); release.clear()
                index = begin('save-project')
                require(entered.wait(min(5, remaining())), 'save_pending_not_entered')
                click('edit-rotate-cw'); newer = state(pending['snapshot']['revision'] + 1)
                require(newer['snapshot']['document'] != pending['snapshot']['document'], 'save_pending_no_new_edit')
                release.set()
                finish(index, 'saved')
                mode['value'] = None
                current, _, _ = saved(working, pending, newer=newer)
                facts.update(pendingSnapshotExact=True, newerEditorStillDirty=True)
                check('native-save-pending-edit', exactSubmittedBytes=True, laterDrawingUnsaved=True)

                foreign = copy.deepcopy(current['snapshot']['document'])
                foreign['export']['width'] = 1234
                persistence.validate_project(foreign)
                foreign_bytes = json.dumps(foreign, ensure_ascii=False, allow_nan=False).encode('utf-8')
                require(foreign != current['snapshot']['document'], 'save_foreign_not_distinct')
                with competitor.open('xb') as stream: stream.write(foreign_bytes)
                backup = working.with_suffix('.nivram.bak').read_bytes()
                writer_result = []
                entered.clear(); release.clear(); mode['value'] = 'conflict'
                def replace():
                    try:
                        require(entered.wait(10), 'save_counterwriter_no_request')
                        os.replace(competitor, working)
                        writer_result.append('replaced')
                    except Exception as error:
                        writer_result.append(type(error).__name__)
                    finally: release.set()
                writer = threading.Thread(target=replace, name='nivram-synthetic-counterwriter', daemon=True)
                writer.start()
                perform('save-project', 'conflict')
                writer.join(min(5, remaining()))
                require(not writer.is_alive() and writer_result == ['replaced'], 'save_counterwriter_not_confirmed')
                mode['value'] = None
                require(working.read_bytes() == foreign_bytes and not competitor.exists(), 'save_overwrote_newer_bytes')
                require(working.with_suffix('.nivram.bak').read_bytes() == backup, 'save_conflict_changed_backup')
                unsaved(current)
                message = evaluate("document.getElementById('toast').textContent")
                require('newer version' in message and 'Save As' in message, 'save_conflict_guidance')
                facts.update(counterwriterJoined=True, competingBytesRetained=True, conflictUnchanged=True, conflictBackupRetained=True)
                facts['diskHashes']['competing'] = hashlib.sha256(foreign_bytes).hexdigest()
                check('native-save-competing-replacement', actualReplacement=True, actualConditionalRejection=True)

                mode['value'] = 'uncertain'
                perform('edit-save-as', 'unconfirmed')
                require(mode['injected'] == 1, 'save_fault_not_exercised')
                mode['value'] = None
                uncertain_bytes, facts['diskHashes']['uncertain'] = saved_readback(uncertain, current['snapshot']['document'])
                unsaved(current)
                require(api._path == working, 'save_uncertain_adopted_destination')
                message = evaluate("document.getElementById('toast').textContent")
                require('could not be confirmed' in message and 'Inspect the destination' in message and 'Save As' in message,
                    'save_uncertain_guidance')
                poll("document.getElementById('save-status').textContent==='Recovery copy saved · project unsaved'", timeout=remaining())
                copies = api._store.list_recovery()
                require(copies and api._store.recover(copies[0]['id'])['document'] == current['snapshot']['document'],
                    'save_recovery_not_exact')
                facts.update(uncertainUnchanged=True, uncertainDestinationNotAdopted=True, recoveryExact=True)
                calls = mode['calls']
                perform('save-project', 'cancelled')
                unsaved(current)
                require(mode['calls'] == calls and api._path == working and uncertain.read_bytes() == uncertain_bytes,
                    'save_uncertain_cancel_wrote')
                facts['uncertainCancelRetained'] = True
                perform('save-project', 'saved')
                current, separate_bytes, facts['diskHashes']['separate'] = saved(separate, current)
                facts['separateCopyExact'] = True
                check('native-save-uncertain-publication', injectedAfterActualPublication=True,
                    noFalseSavedState=True, recoveryExact=True, nextOrdinarySaveUsesChooser=True)

                click('edit-rotate-cw'); current = state(current['snapshot']['revision'] + 1)
                os.link(separate, hardlink)
                require(os.path.samefile(separate, hardlink) and separate.stat().st_nlink == 2, 'save_hardlink_not_created')
                facts['hardlinkObserved'] = True
                perform('save-project', 'unsupported')
                unsaved(current)
                require(api._path == separate and separate.read_bytes() == hardlink.read_bytes() == separate_bytes,
                    'save_unsupported_changed_bytes')
                require('does not support protected updates' in evaluate("document.getElementById('toast').textContent"),
                    'save_unsupported_guidance')
                facts['unsupportedUnchanged'] = True
                calls = mode['calls']
                perform('save-project', 'cancelled')
                unsaved(current)
                require(mode['calls'] == calls and api._path == separate, 'save_unsupported_cancel_wrote')
                facts['unsupportedCancelRetained'] = True
                perform('save-project', 'saved')
                current, _, facts['diskHashes']['final'] = saved(final, current)
                require(working.read_bytes() == foreign_bytes and uncertain.read_bytes() == uncertain_bytes
                    and separate.read_bytes() == hardlink.read_bytes() == separate_bytes
                    and not any(paths[i].exists() for i in (0,3,5,7)), 'save_prior_files_changed')
                facts.update(finalCopyExact=True, allPriorBytesRetained=True)
            finally:
                facts['dialogs'] = dialogs.snapshot()
    except Exception as error:
        trace = error.__traceback__
        while trace is not None:
            if Path(trace.tb_frame.f_code.co_filename).resolve() == Path(__file__).resolve():
                facts['failureLine'] = trace.tb_lineno
            trace = trace.tb_next
        if type(error) is AssertionError and re.fullmatch(r'save_[a-z_]{1,80}', str(error)):
            facts['failureCode'] = str(error)
        raise
    finally:
        release.set()
        require(store_idle.wait(15), 'save_store_cleanup_unconfirmed')
        if writer is not None:
            writer.join(5)
            require(not writer.is_alive(), 'save_counterwriter_cleanup')
        require(api._store.save is observed_save and persistence.atomic_write is atomic, 'save_store_observer_replaced')
        api._store.save, persistence.atomic_write = original_save, original_atomic
        restored = not installed or evaluate(REMOVE_OBSERVER, timeout=5, stage='save-restore-observer') is True
        facts.update(observersRestored=restored, storeCalls=mode['calls'])
    require(not observation_failures(report), 'save_final_observation_incomplete')
    check('native-save-unsupported-and-separate-copy', actualHardlinkRejected=True,
        separateCopyVerified=True, priorFilesRetained=True)

