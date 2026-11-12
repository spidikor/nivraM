"""Real native chooser cancellations through the production batch UI.

This fixed scenario covers OPEN Cancel and OPEN select followed by FOLDER
Cancel. Mid-job cancellation, production window close, and publication after
late work remain separate journeys.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

from .acceptance_batch_png import (INPUT_FILTER, _ordinary_child, _ordinary_directory,
    _read_owned, _require, _write_fresh, assert_native_selection, dialog_diagnostic)
from .acceptance_drawing_png import STATE, READY, assert_preserved

REQUIRED_CHECKS = frozenset({
    'native-batch-cancel-synthetic-inputs', 'native-batch-open-cancelled',
    'native-batch-folder-cancelled', 'native-batch-selection-cancel-integrity',
})
INPUT_NAMES = ('cancel fixture one.nivram', 'cancel fixture two.mol')


def assert_cancelled_return(value):
    _require(value is None or (type(value) in (list, tuple) and len(value) == 0),
             'native_cancel_return_not_empty')
    return 'none' if value is None else type(value).__name__


def fixture_diagnostic(value):
    result = dialog_diagnostic(value)
    if type(value) is not dict:
        value = {}
    result.update({key: value.get(key) if type(value.get(key)) is bool else None
                   for key in ('cancelled', 'cancel_posted')})
    result['intentionalCancel'] = value.get('reason') == 'intentional_cancel'
    return result


class CancellationDialogObserver:
    """Keep actual native returns intact, with an exact one/two-call plan."""
    def __init__(self, window, choose, *, root, inputs, folder, open_kind, folder_kind, journey, timeout):
        _require(journey in ('open-cancel', 'folder-cancel'), 'cancel_journey_unknown')
        self.window, self.choose, self.root = window, choose, Path(root)
        self.inputs, self.folder = tuple(inputs), Path(folder)
        self.open_kind, self.folder_kind, self.timeout = open_kind, folder_kind, timeout
        self.plan = (('open-multiple', 'cancel'),) if journey == 'open-cancel' else (
                    ('open-multiple', 'select'), ('folder', 'cancel'))
        self.original = window.create_file_dialog
        self.records, self.count, self.attached = [], 0, False
        self._lock = threading.RLock()

        def observed(*args, **kwargs):
            with self._lock:
                index = self.count
                self.count += 1
            _require(index < len(self.plan) and len(args) == 1, 'cancel_native_dialog_sequence')
            mode, action = self.plan[index]
            kind = self.open_kind if mode == 'open-multiple' else self.folder_kind
            expected_kwargs = {'allow_multiple': True, 'file_types': INPUT_FILTER} if mode == 'open-multiple' else {}
            _require(type(args[0]) is type(kind) and args[0] == kind and kwargs == expected_kwargs,
                     'cancel_native_dialog_arguments_changed')
            target = self.inputs if mode == 'open-multiple' else self.folder
            choice = self.choose(target, mode=mode, action=action, timeout=self.timeout(), parent_io=True)
            record = {'mode': mode, 'action': action, 'originalCalled': True, 'originalReturned': False,
                      'actualReturnVerified': False}
            with self._lock:
                self.records.append(record)
            def mark(**values):
                with self._lock:
                    record.update(values)
            try:
                result = self.original(*args, **kwargs)
                mark(originalReturned=True)
            except Exception:
                mark(originalCallFailed=True)
                raise
            finally:
                failure_active = sys.exc_info()[0] is not None
                try:
                    receipt = choice.finish()
                    mark(fixture=fixture_diagnostic(receipt))
                    _require(type(receipt) is dict and receipt.get('worker_exit_confirmed') is True
                             and (receipt.get('cancelled') is True and receipt.get('selected') is False
                                  if action == 'cancel' else receipt.get('selected') is True),
                             'cancel_native_fixture_unconfirmed')
                except Exception as error:
                    mark(fixtureFailed=True, fixtureFailure=fixture_diagnostic(getattr(error, 'dialog_result', None)))
                    if not failure_active:
                        raise
            if action == 'cancel':
                mark(returnedKind=assert_cancelled_return(result), actualReturnVerified=True)
            else:
                names = assert_native_selection(mode, result, self.inputs, self.root)
                mark(returnedNames=names, actualReturnVerified=True)
            return result
        self.observed = observed

    def attach(self):
        _require(not self.attached, 'cancel_observer_already_attached')
        self.window.create_file_dialog = self.observed
        self.attached = True

    def close(self):
        if self.attached:
            _require(self.window.create_file_dialog is self.observed, 'cancel_observer_replaced')
            self.window.create_file_dialog = self.original
            self.attached = False

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self.records[:len(self.plan)])

    def assert_complete(self):
        rows = self.snapshot()
        _require(self.count == len(rows) == len(self.plan)
                 and [(row['mode'], row['action']) for row in rows] == list(self.plan)
                 and all(row.get('actualReturnVerified') is True and row.get('originalReturned') is True
                         and not row.get('fixtureFailed') for row in rows), 'cancel_native_returns_incomplete')


TERMINAL_UI = """(() => {
    const $=id=>document.getElementById(id), panel=$('batch-export-controls');
    const refresh=[...panel.querySelectorAll('button')].filter(b=>b.textContent==='Refresh profiles');
    return $('batch-export-status').textContent==='Batch selection cancelled.' &&
        !$('batch-export-button').disabled && !$('batch-export-profile').disabled &&
        $('batch-export-cancel').disabled && refresh.length===1 && !refresh[0].disabled &&
        $('batch-export-results').children.length===0 &&
        [...document.querySelectorAll('svg[aria-hidden="true"]')].filter(svg=>
            svg.style.left==='-100000px' && svg.style.position==='fixed').length===0;
})()"""


def run(*, evaluate, poll, choose, check, stage, report, profile, api, downloads, window):
    import webview
    from .acceptance_click import click_png_button
    from .persistence import validate_project

    deadline = time.monotonic() + 120
    def remaining(cap):
        value = min(cap, deadline - time.monotonic())
        if value <= 0:
            raise TimeoutError('The native batch chooser-cancel scenario exceeded its deadline.')
        return value
    def ev(expression, label):
        return evaluate(expression, timeout=remaining(10), stage=label)
    def wait(expression):
        return poll(expression, timeout=remaining(20))

    profile = _ordinary_directory(profile)
    fixtures = profile / 'synthetic fixtures'
    _require(not fixtures.is_symlink() and not fixtures.is_junction(), 'cancel_fixture_root_redirected')
    fixtures.mkdir(exist_ok=True)
    fixtures = _ordinary_child(profile, fixtures, directory=True)
    output = fixtures / 'cancel output'
    output.mkdir(exist_ok=False)
    output = _ordinary_child(fixtures, output, directory=True)
    report['batch_selection_cancel'] = {'openVerified': False, 'folderVerified': False,
        'midJobVerified': False, 'productionCloseVerified': False, 'dialogs': []}
    stage('native-batch-cancel-synthetic-setup')
    ev("""(() => {document.getElementById('import-button').click();
        document.getElementById('structure-input').value='CCO';document.getElementById('load-structure').click();
        return true;})()""", 'cancel-import-synthetic-ethanol')
    wait("!document.getElementById('import-dialog').open && " + READY +
         " && window.nivramDocument.getSnapshot().document.graph.atoms.length===3")
    ev("document.getElementById('batch-export-controls').open=true;true", 'cancel-open-batch-controls')
    baseline = ev(STATE, 'cancel-baseline')
    project = validate_project(copy.deepcopy(baseline['snapshot']['document']))
    source_bytes = {INPUT_NAMES[0]: json.dumps(project, allow_nan=False).encode('utf-8'),
                    INPUT_NAMES[1]: b'Synthetic malformed MOL; no processing should begin.\n'}
    for name, data in source_bytes.items():
        _write_fresh(fixtures, fixtures / name, data)
    sentinel = b'Preserve the existing output on chooser cancellation.\n'
    _write_fresh(output, output / 'preserved.png', sentinel)
    original_profiles = api.list_profiles()
    prior_downloads = downloads.snapshot()
    _require(all(prior_downloads.get(key) == 0 for key in ('last_id', 'accepted', 'completed', 'active', 'failures')),
             'cancel_prior_downloads_present')
    check('native-batch-cancel-synthetic-inputs', inputCount=2,
          sourceHashes={name: hashlib.sha256(data).hexdigest() for name, data in source_bytes.items()})
    for journey, check_id, flag in (
        ('open-cancel', 'native-batch-open-cancelled', 'openVerified'),
        ('folder-cancel', 'native-batch-folder-cancelled', 'folderVerified')):
        observer = CancellationDialogObserver(window, choose, root=fixtures,
            inputs=tuple(fixtures / name for name in source_bytes), folder=output,
            open_kind=webview.FileDialog.OPEN, folder_kind=webview.FileDialog.FOLDER,
            journey=journey, timeout=lambda: remaining(15))
        observer.attach()
        try:
            stage('native-batch-' + journey)
            gesture = click_png_button(window, evaluate, 'batch')
            wait(TERMINAL_UI)
            observer.assert_complete()
            with api._batch._lock:
                _require(api._batch._choosing is False and api._batch._session is None
                         and api._batch._closed is False, 'cancel_batch_session_not_idle')
            assert_preserved(baseline, ev(STATE, 'cancel-editor-preserved'))
            report['batch_selection_cancel'][flag] = True
            check(check_id, actualNativeReturnVerified=True, terminalUiVerified=True,
                  noBatchSessionCreated=True, completeEditorStateUnchanged=True, nativeGesture=gesture)
        finally:
            report['batch_selection_cancel']['dialogs'].extend(observer.snapshot())
            observer.close()
    _require(set(path.name for path in fixtures.iterdir()) == {*INPUT_NAMES, output.name}
             and set(path.name for path in output.iterdir()) == {'preserved.png'}, 'cancel_unexpected_output')
    for name, data in source_bytes.items():
        _require(_read_owned(fixtures, fixtures / name, 4_000_000) == data, 'cancel_input_changed')
    _require(_read_owned(output, output / 'preserved.png', 4096) == sentinel, 'cancel_existing_output_changed')
    _require(api.list_profiles() == original_profiles, 'cancel_profiles_changed')
    _require(downloads.snapshot() == prior_downloads, 'cancel_download_activity_changed')
    assert_preserved(baseline, ev(STATE, 'cancel-final-editor-preserved'))
    check('native-batch-selection-cancel-integrity', inputsUnchanged=True, existingOutputUnchanged=True,
          noManifestCreated=True, noNewPng=True, profilesUnchanged=True, browserDownloadCount=0,
          completeEditorStateUnchanged=True)
