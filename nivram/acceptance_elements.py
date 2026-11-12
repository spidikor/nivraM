"""Native element-entry timing, grouped history and cancellation contracts."""
from __future__ import annotations

import copy
import json
import math
import time

from .acceptance_editing import STATE, READY, require, assert_step
from .acceptance_resize import ARM_HOLD, RESTORE_HOLD
from .acceptance_keys import observed_keys

KINDS = ('queued-history', 'expiry-prefix', 'pointer-text', 'pending-undo')
CHECKS = frozenset('native-elements-' + name for name in KINDS)
ACTION_ORDER = {
 'queued-history': ['button:edit-import', 'button:edit-load', 'move:atom', 'key:cl', 'button:edit-undo', 'button:edit-redo'] * 2,
 'expiry-prefix': ['button:edit-import', 'button:edit-load', 'move:atom', 'key:c', 'key:li',
     'button:edit-undo', 'button:edit-undo', 'button:edit-redo', 'button:edit-redo', 'move:atom', 'key:xe'],
 'pointer-text': ['button:edit-import', 'button:edit-load', 'move:atom', 'key:c', 'move:empty', 'key:l', 'key:text'],
 'pending-undo': ['button:edit-import', 'button:edit-load', 'move:atom', 'key:c', 'button:edit-undo'],
}
FACTS = {
 'queued-history': ('changedFirstPreview', 'changedSecondPreview', 'changedStateRetained', 'changedRevisionExact',
     'changedUndoExact', 'changedRedoExact', 'noopFirstPreview', 'noopSecondPreview', 'noopStateRetained',
     'noopRevisionExact', 'noopUndoExact', 'noopRedoExact', 'holdsRestored'),
 'expiry-prefix': ('firstCommitExact', 'expiryObserved', 'prefixCommitExact', 'separateUndoExact',
     'separateRedoExact', 'xenonPrefixExact', 'xenonCommitExact'),
 'pointer-text': ('firstPreviewExact', 'pointerMovedWithinWindow', 'continuationRefused', 'carbonCommitExact',
     'textEdited', 'textDocumentRetained', 'holdRestored'),
 'pending-undo': ('firstPreviewExact', 'undoCancelledOnlyPending', 'previewCleared', 'lateStateRetained', 'holdRestored'),
}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def within(first, second):
    require(finite(first) and finite(second) and 0 <= second-first < 500, 'element_extension_window')
    return second-first


def document_symbol(before, symbol):
    value = copy.deepcopy(before)
    require(len(value['graph']['atoms']) == 1 and value['graph']['bonds'] == [], 'element_single_atom_fixture')
    value['graph']['atoms'][0]['symbol'] = symbol
    return value


def assert_commit(before, after, symbol, revisions):
    expected = copy.deepcopy(before['snapshot'])
    expected.update(document=document_symbol(expected['document'], symbol), revision=expected['revision']+revisions, dirty=True)
    require(after['snapshot'] == expected and after['undoDisabled'] is False and after['redoDisabled'] is True,
            'element_commit_changed_other_state')


def preview(event, *, symbol, preview, revision, pending):
    require(event['symbol'] == symbol and event['preview'] == preview and event['revision'] == revision
            and event['pending'] is pending and event['prevented'] is True, 'element_preview_not_immediate')


def observation_failures(report):
    value = report.get('elements_observation')
    if type(value) is not dict or type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1:
        return ['elements_observation_missing']
    cases = value.get('cases')
    if type(cases) is not list or len(cases) != 4 or [c.get('kind') for c in cases if type(c) is dict] != list(KINDS):
        return ['elements_cases_incomplete']
    for case in cases:
        kind = case['kind']
        if any(case.get(key) is not True for key in FACTS[kind]): return ['elements_facts_incomplete']
        actions = case.get('actions')
        if type(actions) is not list or len(actions) != len(ACTION_ORDER[kind]): return ['elements_actions_incomplete']
        names = []
        for action in actions:
            if type(action) is not dict or action.get('status') != 'passed' or action.get('ownershipVerified') is not True:
                return ['elements_actions_unverified']
            category, name = action.get('type'), action.get('kind'); names.append(f'{category}:{name}')
            if category == 'button':
                if action.get('isTrusted') is not True or action.get('userActivation') is not True:
                    return ['elements_actions_unverified']
            elif category == 'key':
                try:
                    if not observed_keys(action.get('events'), name): return ['elements_actions_unverified']
                except (KeyError, TypeError): return ['elements_actions_unverified']
            elif category == 'move':
                if (action.get('trusted') is not True or action.get('mouse') is not True
                        or action.get('route') != 'owned-webview2-sdk-hover'
                        or type(action.get('buttons')) is not int or action['buttons'] != 0):
                    return ['elements_actions_unverified']
            else: return ['elements_actions_unverified']
        if names != ACTION_ORDER[kind]: return ['elements_actions_out_of_order']
        windows = case.get('extensionMilliseconds', [])
        expected = 2 if kind in ('queued-history', 'expiry-prefix') else 1 if kind == 'pointer-text' else 0
        if type(windows) is not list or len(windows) != expected or any(not finite(n) or not 0 <= n < 500 for n in windows):
            return ['elements_timing_unverified']
        if kind == 'expiry-prefix' and (not finite(case.get('expiryMilliseconds')) or case['expiryMilliseconds'] < 500):
            return ['elements_expiry_unverified']
    return []


def run(*, evaluate, poll, check, stage, report, window, **unused):
    from .acceptance_click import click_png_button, NativeClickRejected
    from . import acceptance_keys
    deadline = time.monotonic() + 120
    facts = {'schemaVersion': 1, 'cases': []}; report['elements_observation'] = facts

    def remaining():
        value = deadline-time.monotonic(); require(value > 0, 'element_scenario_deadline')
        return min(20, value)

    def js(text, label='element-observation'):
        return evaluate(text, timeout=min(10, remaining()), stage=label)

    def state(): return js(STATE)

    def ready(revision=None):
        condition = READY
        if revision is not None: condition += ' && window.nivramDocument.getSnapshot().revision===' + str(revision)
        try: poll(condition, timeout=remaining())
        except Exception:
            facts['failedWait'] = {'expectedRevision': revision, 'observed': js("""(() => {
              const s=window.nivramDocument.getSnapshot();return {revision:s.revision,ready:document.getElementById('request-status').textContent==='Ready',
               atomCount:s.document.graph.atoms.length,symbol:s.document.graph.atoms[0]?.symbol??null,
               preview:document.querySelector('#element-entry-preview text')?.textContent??null};})()""")}
            raise
        return state()

    def record(category, action):
        row['actions'].append({'type': category, **action}); stage('element-' + category + '-' + action['kind'])
        return action

    def button(kind): return record('button', click_png_button(window, evaluate, kind))
    def key(kind):
        try: return record('key', acceptance_keys.keys(window, evaluate, poll, kind))['events']
        except NativeClickRejected as error:
            row['failedAction'] = dict(type='key', kind=kind, code=error.code,
                observation=getattr(error, 'element_observation', None))
            raise
    def move(kind): return record('move', acceptance_keys.move(window, evaluate, poll, kind, remaining))

    def load(symbol):
        before = ready(); button('edit-import')
        js('document.getElementById("structure-input").value=' + json.dumps(symbol) + ';true')
        button('edit-load'); loaded = ready(before['snapshot']['revision'] + 2)
        require(len(loaded['snapshot']['document']['graph']['atoms']) == 1 and
                loaded['snapshot']['document']['graph']['atoms'][0]['symbol'] == symbol, 'element_import_not_single_expected_atom')
        return loaded

    def hold(): require(js(ARM_HOLD.replace('__REJECT__', 'false')) is True, 'element_hold_arm')
    def arrived(): poll('window.__nivramResizeHold.arrived===true', timeout=remaining())
    def release():
        js('window.__nivramResizeHold.release();true')
        poll('window.__nivramResizeHold.finished===true', timeout=remaining())
    def restore(): return js(RESTORE_HOLD, 'element-hold-restore') is True

    def history(before, document, redo=False, redo_disabled=None):
        button('edit-redo' if redo else 'edit-undo')
        after = ready(before['snapshot']['revision'] + 1)
        assert_step(before, after, document=document, dirty=True,
                    redo_disabled=redo if redo_disabled is None else redo_disabled)
        return after

    poll("typeof window.nivramDocument.getSnapshot().recoveryGeneration==='string'", timeout=remaining())
    row = {'kind': 'queued-history', 'actions': [], 'extensionMilliseconds': []}; facts['cases'].append(row)
    restored = []
    for initial, prefix, delta in (('N', 'changed', 2), ('C', 'noop', 1)):
        before = load(initial); move('atom'); hold()
        try:
            events = key('cl')
            preview(events[0], symbol=initial, preview='C', revision=before['snapshot']['revision'], pending=True)
            preview(events[1], symbol=initial, preview='Cl', revision=before['snapshot']['revision'], pending=True)
            arrived()
            row[prefix+'FirstPreview'] = row[prefix+'SecondPreview'] = True
            row['extensionMilliseconds'].append(within(events[0]['at'], events[1]['at']))
            require(state() == before, 'element_queued_edit_committed_early'); row[prefix+'StateRetained'] = True
            release(); after = ready(before['snapshot']['revision'] + delta)
            assert_commit(before, after, 'Cl', delta); row[prefix+'RevisionExact'] = True
        finally: restored.append(restore())
        require(restored[-1], 'element_hold_not_restored')
        undone = history(after, before['snapshot']['document']); row[prefix+'UndoExact'] = True
        history(undone, after['snapshot']['document'], True); row[prefix+'RedoExact'] = True
    row['holdsRestored'] = all(restored)
    check('native-elements-queued-history', immediatePreviews=True, changedAndNoopFirstLetters=True, oneUndoGroup=True)

    row = {'kind': 'expiry-prefix', 'actions': [], 'extensionMilliseconds': []}; facts['cases'].append(row)
    before = load('N'); move('atom'); first = key('c')[0]
    carbon = ready(before['snapshot']['revision'] + 1); assert_commit(before, carbon, 'C', 1); row['firstCommitExact'] = True
    # Keep the pointer still so elapsed time alone separates the gestures.
    time.sleep(.55); remaining(); pair = key('li')
    row['expiryMilliseconds'] = pair[0]['at'] - first['at']; require(row['expiryMilliseconds'] >= 500, 'element_expiry_not_observed')
    row['expiryObserved'] = True; row['extensionMilliseconds'].append(within(pair[0]['at'], pair[1]['at']))
    lithium = ready(carbon['snapshot']['revision'] + 1); assert_commit(carbon, lithium, 'Li', 1); row['prefixCommitExact'] = True
    undone = history(lithium, carbon['snapshot']['document']); undone = history(undone, before['snapshot']['document'])
    row['separateUndoExact'] = True
    redone = history(undone, carbon['snapshot']['document'], True, redo_disabled=False)
    redone = history(redone, lithium['snapshot']['document'], True)
    row['separateRedoExact'] = True
    move('atom'); pair = key('xe'); row['extensionMilliseconds'].append(within(pair[0]['at'], pair[1]['at']))
    require(pair[0]['symbol'] == 'Li' and pair[0]['revision'] == redone['snapshot']['revision'] and pair[0]['preview'] is None,
            'element_prefix_committed_early'); row['xenonPrefixExact'] = True
    xenon = ready(redone['snapshot']['revision'] + 1); assert_commit(redone, xenon, 'Xe', 1); row['xenonCommitExact'] = True
    check('native-elements-expiry-prefix', realExpiryObserved=True, separateHistory=True, twoLetterPrefixes=True)

    row = {'kind': 'pointer-text', 'actions': [], 'extensionMilliseconds': []}; facts['cases'].append(row)
    before = load('N'); move('atom'); hold()
    try:
        first_action, moved, last_action = acceptance_keys.pointer_pair(window, evaluate, poll, remaining)
        first = record('key', first_action)['events'][0]
        record('move', moved); last = record('key', last_action)['events'][0]
        preview(first, symbol='N', preview='C', revision=before['snapshot']['revision'], pending=True); row['firstPreviewExact'] = True
        within(first['at'], moved['at']); row['extensionMilliseconds'].append(within(first['at'], last['at']))
        row['pointerMovedWithinWindow'] = True
        require(last['symbol'] == 'N' and last['revision'] == before['snapshot']['revision'] and last['preview'] == 'C',
                'element_moved_pointer_extended_symbol'); row['continuationRefused'] = True
        arrived(); release(); after = ready(before['snapshot']['revision'] + 1)
        assert_commit(before, after, 'C', 1); row['carbonCommitExact'] = True
    finally: row['holdRestored'] = restore()
    require(row['holdRestored'], 'element_hold_not_restored')
    js('document.getElementById("filename").value="CL123";true'); key('text')
    require(js('document.getElementById("filename").value') == 'CL12n3', 'element_text_input_wrong')
    row['textEdited'] = True
    require(state() == after, 'element_text_changed_document'); row['textDocumentRetained'] = True
    check('native-elements-pointer-text', pointerBoundaryObserved=True, textEntryObserved=True, documentUnchangedByText=True)

    row = {'kind': 'pending-undo', 'actions': [], 'extensionMilliseconds': []}; facts['cases'].append(row)
    before = load('N'); move('atom'); hold()
    try:
        first = key('c')[0]; arrived()
        preview(first, symbol='N', preview='C', revision=before['snapshot']['revision'], pending=True); row['firstPreviewExact'] = True
        button('edit-undo'); require(ready(before['snapshot']['revision']) == before, 'element_pending_undo_changed_earlier_document')
        row['undoCancelledOnlyPending'] = True
        require(js("document.querySelectorAll('#element-entry-preview text').length") == 0, 'element_cancel_preview_retained')
        row['previewCleared'] = True
        release(); require(ready(before['snapshot']['revision']) == before, 'element_late_response_changed_state'); row['lateStateRetained'] = True
    finally: row['holdRestored'] = restore()
    require(row['holdRestored'], 'element_hold_not_restored')
    check('native-elements-pending-undo', earlierDocumentRetained=True, lateResponseRejected=True)
    require(observation_failures(report) == [], 'elements_final_observation_incomplete')
