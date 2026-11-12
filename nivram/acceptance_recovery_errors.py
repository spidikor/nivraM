"""Native recovery failure UI with real retained files and narrow I/O fault seams.

This scenario does not exercise a successful restore, the confirmation dialog,
native process restart, or power-loss durability. No confirmation is replaced.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import threading
import time
from unittest.mock import patch

from .acceptance_editing import STATE, READY, SMILES, require
from .acceptance_batch_cancel import assert_cancelled_return

CHECKS = frozenset({
    'native-recovery-empty', 'native-recovery-diagnostics',
    'native-recovery-storage-unavailable', 'native-recovery-stale-inventory',
    'native-recovery-manual-save-available',
})
FACTS = ('emptyUnchanged', 'diagnosticsExact', 'failedRecordsRetained',
         'storageFailureExplicit', 'storageFailureUnchanged', 'staleReplyRejected',
         'newerDrawingRetained', 'manualSaveCancelled', 'manualSaveUnchanged',
         'barrierWorkerExited', 'observersRestored')
GESTURES = ('recover-project', 'recover-project', 'recover-project', 'recover-project',
            'edit-import', 'edit-load', 'save-project')


def observation_failures(report):
    value = report.get('recovery_errors_observation')
    if type(value) is not dict or type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1:
        return ['native_recovery_errors_unconfirmed']
    good = all(value.get(key) is True for key in FACTS)
    rows = value.get('gestures')
    good = good and type(rows) is list and len(rows) == len(GESTURES)
    if type(rows) is list:
        good = good and [row.get('kind') for row in rows if type(row) is dict] == list(GESTURES)
        good = good and all(type(row) is dict and row.get('status') == 'passed'
            and row.get('isTrusted') is True and row.get('userActivation') is True for row in rows)
    dialog = value.get('dialog')
    good = good and type(dialog) is dict and dialog.get('action') == 'cancel'
    if type(dialog) is dict:
        good = good and all(dialog.get(key) is True for key in
            ('originalReturned', 'actualReturnVerified', 'workerExited'))
    good = good and value.get('diagnosticCounts') == dict(invalid=1, unsupported=2, unreadable=1, interrupted=1)
    hashes = value.get('retainedHashes')
    good = good and type(hashes) is list and len(hashes) == 5
    if type(hashes) is list:
        good = good and all(type(x) is str and len(x) == 64
            and all(c in '0123456789abcdef' for c in x) for x in hashes)
    return [] if good else ['native_recovery_errors_unconfirmed']


def run(*, evaluate, poll, choose, check, stage, report, profile, api, window):
    import webview
    from . import persistence
    from .acceptance_click import click_png_button

    deadline = time.monotonic() + 100
    facts = dict(schemaVersion=1, gestures=[], dialog={})
    report['recovery_errors_observation'] = facts
    report['limitations'].extend([
        'Recovery errors only: successful restore and default confirmation rendering are not exercised.',
        'Read and directory failures are injected only for the isolated recovery store; no filesystem permissions are changed.',
        'No native process interruption, restart or power-loss durability is established by this scenario.',
    ])

    def remaining():
        seconds = deadline - time.monotonic()
        require(seconds > 0, 'recovery_errors_deadline')
        return min(15, seconds)

    def state():
        poll(READY, timeout=remaining())
        return evaluate(STATE, timeout=min(10, remaining()), stage='recovery-errors-state')

    def click(kind):
        remaining()
        facts['gestures'].append(click_png_button(window, evaluate, kind))
        stage(kind)

    def notice(fragment):
        poll('document.getElementById("toast").textContent.includes(' + json.dumps(fragment) + ')',
             timeout=remaining())

    poll('!!window.nivramNative && typeof window.pywebview.api.recovery_inventory==="function"'
         ' && typeof window.pywebview.api.accept_recovery==="function"', timeout=remaining())
    before = state()
    require(before['snapshot']['document']['graph']['atoms'] == [], 'recovery_errors_initial_not_empty')
    click('recover-project')
    notice('No recovery copies')
    require(state() == before and api._path is None, 'recovery_errors_empty_changed')
    facts['emptyUnchanged'] = True
    check('native-recovery-empty')

    store = api._store
    directory = store.recovery_dir
    directory.mkdir(exist_ok=True)
    document = copy.deepcopy(before['snapshot']['document'])
    record = dict(schema='nivram-recovery/1', created_ns=1700000000000000000, document=document)
    entries = [
        ('1-' + 'a'*32 + '.json', b'{"interrupted":'),
        ('2-' + 'b'*32 + '.json', json.dumps(record | {'schema':'nivram-recovery/2'}).encode()),
        ('3-' + 'c'*32 + '.json', json.dumps(record | {'document':document | {'schema':'nivram/2'}}).encode()),
        ('4-' + 'd'*32 + '.json', json.dumps(record).encode()),
        ('.nv-retained.tmp', b'synthetic interrupted bytes'),
    ]
    retained = {}
    for name, data in entries:
        path = directory / name
        with path.open('xb') as stream:
            stream.write(data)
        retained[path] = data
    original_recover, original_inventory = store.recover, store.recovery_inventory
    entered, release, idle = threading.Event(), threading.Event(), threading.Event()
    idle.set()

    def read(key):
        if key == entries[3][0][:-5]:
            raise PermissionError('Injected isolated unreadable recovery record')
        return original_recover(key)

    def held():
        idle.clear()
        try:
            result = original_inventory()
            entered.set()
            require(release.wait(8), 'recovery_errors_barrier_timeout')
            return result
        finally:
            idle.set()

    with patch.object(store, 'recover', read):
        try:
            expected = dict(invalid=1, unsupported=2, unreadable=1, interrupted=1)
            require(original_inventory()['issues'] == expected, 'recovery_errors_fixture_inventory')
            click('recover-project')
            notice('No readable recovery copy')
            text = evaluate('document.getElementById("toast").textContent')
            require(all(x in text for x in ('Damaged', 'unsupported format', 'could not be read', 'Interrupted', 'Save As')),
                    'recovery_errors_missing_diagnostic')
            require(state() == before and api._path is None, 'recovery_errors_diagnostics_changed')
            facts.update(diagnosticsExact=True, diagnosticCounts=expected)
            check('native-recovery-diagnostics')

            original_scandir = persistence.os.scandir
            def inaccessible(path):
                if isinstance(path, (str, Path)) and Path(path) == directory:
                    raise PermissionError('Injected isolated directory failure')
                return original_scandir(path)
            with patch.object(persistence.os, 'scandir', inaccessible):
                click('recover-project')
                notice('Recovery files could not be checked')
                require('Save As' in evaluate('document.getElementById("toast").textContent'),
                        'recovery_errors_storage_advice')
                require(state() == before and api._path is None, 'recovery_errors_storage_changed')
            facts.update(storageFailureExplicit=True, storageFailureUnchanged=True)
            check('native-recovery-storage-unavailable')

            with patch.object(store, 'recovery_inventory', held):
                click('recover-project')
                require(entered.wait(2), 'recovery_errors_inventory_not_held')
                click('edit-import')
                evaluate('document.getElementById("structure-input").value=' + json.dumps(SMILES) + '; true')
                click('edit-load')
                poll(READY + ' && window.nivramDocument.getSnapshot().revision>'
                     + str(before['snapshot']['revision']), timeout=remaining())
                changed = state()
                require(changed['snapshot']['dirty'] is True
                    and changed['snapshot']['document']['graph']['atoms'], 'recovery_errors_edit_missing')
                release.set()
                require(idle.wait(2), 'recovery_errors_worker_not_exited')
                notice('The drawing changed; recovery was not applied.')
                require(state() == changed and api._path is None, 'recovery_errors_stale_overwrite')
            facts.update(staleReplyRejected=True, newerDrawingRetained=True, barrierWorkerExited=True)
            check('native-recovery-stale-inventory')

            target = Path(profile) / 'synthetic fixtures' / 'unused.nivram'
            target.parent.mkdir(exist_ok=True)
            original_dialog = window.create_file_dialog
            row = facts['dialog'] = dict(action='cancel', originalReturned=False,
                                        actualReturnVerified=False, workerExited=False)
            def cancelled(*args, **kwargs):
                require(args == (webview.FileDialog.SAVE,) and kwargs == {
                    'save_filename':'structure.nivram', 'file_types':('nivraM projects (*.nivram)',)},
                    'recovery_errors_unexpected_dialog')
                choice = choose(target, timeout=remaining(), parent_io=True, action='cancel', mode='save')
                try:
                    result = original_dialog(*args, **kwargs)
                    row['originalReturned'] = True
                    assert_cancelled_return(result)
                    row['actualReturnVerified'] = True
                    return result
                finally:
                    receipt = choice.finish()
                    row['workerExited'] = receipt.get('worker_exit_confirmed') is True
                    require(row['workerExited'] and receipt.get('cancelled') is True
                        and receipt.get('selected') is False, 'recovery_errors_cancel_unconfirmed')
            probe = """(() => {
                const native=window.pywebview.api, original=native.save_project;
                if(window.__recoveryErrorsSave)throw Error('observer exists');
                const p={original,done:false};
                p.wrapped=async(...args)=>{try {const r=await original(...args);p.status=r?.status;return r;}
                    finally{p.done=true;}};
                native.save_project=p.wrapped;window.__recoveryErrorsSave=p;return true;
            })()"""
            require(evaluate(probe) is True, 'recovery_errors_save_observer')
            try:
                with patch.object(window, 'create_file_dialog', cancelled):
                    click('save-project')
                    poll('window.__recoveryErrorsSave?.done===true', timeout=remaining())
                    require(evaluate('window.__recoveryErrorsSave.status') == 'cancelled',
                            'recovery_errors_save_not_cancelled')
                require(state() == changed and api._path is None and not target.exists(),
                        'recovery_errors_manual_save_changed')
                facts.update(manualSaveCancelled=True, manualSaveUnchanged=True)
                check('native-recovery-manual-save-available')
            finally:
                require(evaluate("""(() => {
                    const p=window.__recoveryErrorsSave,n=window.pywebview.api;
                    if(!p || n.save_project!==p.wrapped)return false;
                    n.save_project=p.original;delete window.__recoveryErrorsSave;return true;
                })()""") is True, 'recovery_errors_save_observer_not_restored')
            require(all(p.read_bytes() == data for p, data in retained.items()),
                    'recovery_errors_retained_bytes_changed')
            facts['failedRecordsRetained'] = True
            facts['retainedHashes'] = [hashlib.sha256(data).hexdigest() for data in retained.values()]
        finally:
            release.set()
            require(idle.wait(2), 'recovery_errors_barrier_still_active')
    require(store.recover == original_recover and store.recovery_inventory == original_inventory,
            'recovery_errors_observer_not_restored')
    facts['observersRestored'] = True
    require(not observation_failures(report), 'recovery_errors_observations_incomplete')

