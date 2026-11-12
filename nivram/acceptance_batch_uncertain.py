"""Two real native batch failures with independent disk and memory evidence."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
import time

from .acceptance_batch_fault import PublicationFaultObserver, MODES, MANIFEST_ERROR
from .acceptance_batch_png import (NativeDialogObserver, _require, _ordinary_directory, _ordinary_child,
    _write_fresh, _read_owned, _json_object, _json_equal, _settings_equal, _no_paths,
    MANIFEST_NAME, MAX_MANIFEST_BYTES, MAX_PNG_BYTES, MAX_SOURCE_BYTES)
from .acceptance_batch_midjob import UI, CANCEL_ERROR
from .acceptance_drawing_png import STATE, READY, OPTIONS, assert_preserved, validate_png_bytes, assert_rgba_equal

REQUIRED_CHECKS = frozenset({'native-uncertain-synthetic-inputs', 'native-lost-acknowledgement-stops',
    'native-uncertain-manifest-stops', 'native-uncertain-retained-files-and-editor'})
NAMES = {'lost-ack': ('a1.nivram', 'a2.nivram'), 'manifest-uncertain': ('m1.nivram', 'm2.nivram')}
SENTINEL = b'Existing synthetic output must survive uncertain publication.\n'
STOPPED_MESSAGE = 'The batch stopped before completion. Saved PNGs and the latest available manifest were retained.'


def observation_failures(report):
    facts = report.get('batch_uncertain')
    if type(facts) is not dict or facts.get('uncertaintyVerified') is not True:
        return ['uncertainty_observations_missing']
    cases = facts.get('cases')
    if type(cases) is not dict or set(cases) != set(MODES):
        return ['uncertainty_cases_missing']
    for mode, row in cases.items():
        if type(row) is not dict:
            return ['uncertainty_case_invalid']
        fault, dialogs = row.get('faultObserver'), row.get('dialogs')
        if type(fault) is not dict or type(dialogs) is not list or any(type(d) is not dict for d in dialogs):
            return ['uncertainty_observer_invalid']
        expected = {'prepareCalls': 1, 'writeCalls': 1, 'cancelCalls': 1, 'failCalls': 0, 'finishCalls': 0,
            'faultCount': 1, 'manifestCalls': 4 if mode == 'lost-ack' else 3,
            'atomicCommits': 0 if mode == 'lost-ack' else 1}
        good = (row.get('verified') is True and row.get('latePrepareRejected') is True and row.get('lateWriteRejected') is True
            and row.get('completeEditorStateUnchanged') is True and row.get('retainedFilesUnchanged') is True
            and row.get('terminalStatus') == ('cancelled' if mode == 'lost-ack' else 'stopped')
            and row.get('diskManifestStatus') == ('cancelled' if mode == 'lost-ack' else 'running')
            and row.get('diskMatchesTerminal') is (mode == 'lost-ack') and fault.get('mode') == mode
            and all(type(fault.get(key)) is int and fault[key] == value for key, value in expected.items())
            and all(fault.get(key) is True for key in ('shutdownConfirmed', 'methodsRestored', 'atomicWriterRestored', 'preparationReady'))
            and fault.get('writeResultStatus') == 'exported'
            and 'writeManifestError' in fault
            and fault.get('writeManifestError') == (None if mode == 'lost-ack' else MANIFEST_ERROR)
            and len(dialogs) == 2 and [(d.get('mode'), d.get('action')) for d in dialogs] == [('open-multiple', None), ('folder', None)]
            and all(d.get('actualReturnVerified') is True and d.get('originalReturned') is True and not d.get('fixtureFailed') for d in dialogs))
        for key in ('pngSha256', 'manifestSha256'):
            good = good and type(row.get(key)) is str and re.fullmatch('[a-f0-9]{64}', row[key]) is not None
        if not good:
            return ['uncertainty_case_unconfirmed']
    return []


def validate_terminal(mode, receipt, disk, chosen, written, settings, sources, metadata, root):
    _require(mode in MODES, 'uncertainty_mode_invalid')
    keys = {'sessionId', 'status', 'settings', 'items', 'manifestName', 'manifestError'}
    _require(type(receipt) is dict and set(receipt) == keys and type(chosen) is dict and set(chosen) == keys
        and type(disk) is dict and set(disk) == keys | {'schema'}, 'uncertainty_receipt_shape_invalid')
    _require(type(receipt['sessionId']) is str and re.fullmatch('[a-f0-9]{32}', receipt['sessionId'])
        and receipt['sessionId'] == chosen['sessionId'] and chosen['status'] == 'running'
        and chosen['manifestError'] is None and chosen['manifestName'] == receipt['manifestName'] == MANIFEST_NAME
        and receipt['status'] == ('cancelled' if mode == 'lost-ack' else 'stopped')
        and receipt['manifestError'] == (None if mode == 'lost-ack' else MANIFEST_ERROR)
        and _settings_equal(receipt['settings'], settings) and _settings_equal(chosen['settings'], settings)
        and _no_paths(disk, root) and _no_paths(receipt, root), 'uncertainty_terminal_state_invalid')
    names = NAMES[mode]
    fields = {'id', 'name', 'extension', 'status', 'sourceSha256', 'outputName', 'outputSha256', 'dimensions', 'error'}
    _require(type(sources) is dict and set(sources) == set(names)
        and all(type(data) is bytes for data in sources.values())
        and type(receipt['items']) is list and len(receipt['items']) == 2
        and type(chosen['items']) is list and len(chosen['items']) == 2, 'uncertainty_item_count_invalid')
    ids = set()
    for index, item in enumerate(receipt['items']):
        original = chosen['items'][index]
        _require(type(item) is dict and set(item) == fields and type(original) is dict and set(original) == fields
            and type(item['id']) is str and re.fullmatch('[a-f0-9]{32}', item['id']) and item['id'] not in ids
            and item['name'] == names[index] and item['extension'] == '.nivram'
            and item['sourceSha256'] == hashlib.sha256(sources[item['name']]).hexdigest()
            and all(item[key] == original[key] for key in ('id', 'name', 'extension', 'sourceSha256'))
            and original['status'] == 'pending' and original['error'] is None
            and all(original[key] is None for key in ('outputName', 'outputSha256', 'dimensions')),
            'uncertainty_original_item_changed')
        ids.add(item['id'])
    good, cancelled = receipt['items']
    _require(type(written) is dict and set(written) == {'status', 'item', 'manifestError'}
        and written['status'] == 'exported' and written['item'] == good
        and written['manifestError'] == receipt['manifestError'] and good['status'] == 'exported'
        and good['error'] is None and good['outputName'] == Path(names[0]).stem + ' (2).png',
        'uncertainty_original_write_receipt_changed')
    dimensions = good['dimensions']
    _require(type(metadata) is dict and type(metadata.get('sha256')) is str and re.fullmatch('[a-f0-9]{64}', metadata['sha256'])
        and good['outputSha256'] == metadata['sha256'] and type(dimensions) is dict
        and set(dimensions) == {'width', 'height', 'padding'} and all(type(dimensions[k]) is int for k in dimensions)
        and dimensions == {key: metadata.get(key) for key in dimensions}
        and dimensions['width'] == 640 and 24 < dimensions['height'] <= 4096 and dimensions['padding'] == 12,
        'uncertainty_actual_png_provenance_invalid')
    _require(cancelled['status'] == 'cancelled' and cancelled['error'] == CANCEL_ERROR
        and all(cancelled[key] is None for key in ('outputName', 'outputSha256', 'dimensions')),
        'uncertainty_remaining_item_not_cancelled')
    expected_disk = {'schema': 'nivram-batch-export/1', **copy.deepcopy(receipt)}
    if mode == 'manifest-uncertain':
        expected_disk.update(status='running', manifestError=None)
        expected_disk['items'][1] = copy.deepcopy(chosen['items'][1])
    _require(_json_equal(disk, expected_disk), 'uncertainty_last_committed_manifest_mismatch')
    return good, cancelled


def validate_ui(value, good, cancelled):
    _require(type(value) is dict and value.get('status') == STOPPED_MESSAGE
        and all(value.get(key) is False for key in ('startDisabled', 'profileDisabled', 'refreshDisabled'))
        and value.get('cancelDisabled') is True and value.get('meterValue') == value.get('meterMax') == 2
        and value.get('stageCount') == 0 and value.get('rows') == [
            f"{good['name']}: exported → {good['outputName']}",
            f"{cancelled['name']}: cancelled — {CANCEL_ERROR['message']}"], 'uncertainty_terminal_ui_invalid')


def run(*, evaluate, poll, choose, check, stage, report, profile, api, downloads, window):
    import webview
    from .acceptance_click import click_png_button
    from .persistence import validate_project

    deadline = time.monotonic() + 120
    def remaining(cap):
        value = min(cap, deadline - time.monotonic())
        if value <= 0:
            raise TimeoutError('The native uncertainty scenario exceeded its deadline.')
        return value
    def ev(expression, label):
        return evaluate(expression, timeout=remaining(10), stage=label)
    def wait(expression, cap=20):
        return poll(expression, timeout=remaining(cap))
    def current_receipt():
        with api._batch._lock:
            _require(type(api._batch._session) is dict, 'uncertainty_session_missing')
            return api._batch._receipt(api._batch._session)

    profile = _ordinary_directory(profile)
    fixtures = profile / 'synthetic fixtures'
    _require(not fixtures.is_symlink() and not fixtures.is_junction(), 'uncertainty_fixture_redirected')
    fixtures.mkdir(exist_ok=True)
    fixtures = _ordinary_child(profile, fixtures, directory=True)
    facts = {'uncertaintyVerified': False, 'cases': {}}
    report['batch_uncertain'] = facts
    stage('native-uncertain-setup')
    ev("""(() => {document.getElementById('import-button').click();
        document.getElementById('structure-input').value='CCO';document.getElementById('load-structure').click();return true;})()""", 'uncertain-import')
    wait("!document.getElementById('import-dialog').open && " + READY +
         " && window.nivramDocument.getSnapshot().document.graph.atoms.length===3")
    ev("""(() => {const $=id=>document.getElementById(id);
        $('export-width').value='640';$('export-width').dispatchEvent(new Event('input'));
        $('margin-mode').value='pixels';$('margin-mode').dispatchEvent(new Event('input'));
        $('margin-value').value='12';$('margin-value').dispatchEvent(new Event('input'));
        $('batch-export-controls').open=true;return true;})()""", 'uncertain-export-settings')
    wait(READY)
    baseline = ev(STATE, 'uncertain-editor-baseline')
    project = validate_project(copy.deepcopy(baseline['snapshot']['document']))
    _require(project == baseline['snapshot']['document'] and project['export'] == OPTIONS, 'uncertainty_setup_changed_project')
    encoded = json.dumps(project, ensure_ascii=False, allow_nan=False).encode('utf-8')
    settings = {'style': copy.deepcopy(project['style']), 'export': dict(OPTIONS), 'profile': None}
    profiles, prior_downloads = api.list_profiles(), downloads.snapshot()
    _require(all(prior_downloads.get(key) == 0 for key in ('last_id', 'accepted', 'completed', 'active', 'failures')),
             'uncertainty_prior_downloads_present')
    cases = {}
    for mode, names in NAMES.items():
        output = fixtures / names[0][0]
        output.mkdir(exist_ok=False)
        output = _ordinary_child(fixtures, output, directory=True)
        sources = {names[0]: encoded, names[1]: encoded + b'\n'}
        for name, data in sources.items():
            _write_fresh(fixtures, fixtures / name, data)
        sentinel_name = Path(names[0]).stem + '.png'
        _write_fresh(output, output / sentinel_name, SENTINEL)
        cases[mode] = (output, sources, sentinel_name)
    check('native-uncertain-synthetic-inputs', batches=2, inputsPerBatch=2, currentDrawingSettings=True,
          sourceHashes={name: hashlib.sha256(data).hexdigest() for _, sources, _ in cases.values() for name, data in sources.items()})
    decoded_outputs = []
    for mode, (output, sources, sentinel_name) in cases.items():
        observer = NativeDialogObserver(window, choose, root=fixtures,
            inputs=tuple(fixtures / name for name in NAMES[mode]), folder=output,
            open_kind=webview.FileDialog.OPEN, folder_kind=webview.FileDialog.FOLDER, timeout=lambda: remaining(15))
        fault = PublicationFaultObserver(api._batch, output, mode)
        row = {'verified': False}
        facts['cases'][mode] = row
        observer.attach()
        fault.attach()
        try:
            stage('native-uncertain-' + mode)
            gesture = click_png_button(window, evaluate, 'batch')
            wait("!document.getElementById('batch-export-button').disabled && document.getElementById('batch-export-status').textContent===" + json.dumps(STOPPED_MESSAGE), 30)
            observer.assert_complete()
            observer.close()
            fault.assert_complete()
            final = current_receipt()
            _require(final == fault.terminal, 'uncertainty_cancel_receipt_changed')
            cleanup = fault.close()
            _require(cleanup['shutdownConfirmed'] is True, 'uncertainty_fault_observer_cleanup_unconfirmed')
            row['faultObserver'] = cleanup
            manifest_bytes = _read_owned(output, output / MANIFEST_NAME, MAX_MANIFEST_BYTES)
            disk = _json_object(manifest_bytes)
            png = _read_owned(output, output / final['items'][0]['outputName'], MAX_PNG_BYTES)
            decoded = validate_png_bytes(png)
            good, cancelled = validate_terminal(mode, final, disk, fault.chosen, fault.written,
                settings, sources, decoded[0], fixtures)
            _require(png == fault.png, 'uncertainty_published_png_changed')
            if mode == 'manifest-uncertain':
                _require(manifest_bytes == fault.committed_manifest, 'uncertainty_last_atomic_commit_changed')
            validate_ui(ev(UI, 'uncertain-terminal-ui-' + mode), good, cancelled)
            assert_preserved(baseline, ev(STATE, 'uncertain-editor-stopped-' + mode))

            # Separate direct late-work probes after restoring the observed methods.
            # The native UI's own method counts remain independently inspectable.
            late_prepare = api.prepare_batch_item(final['sessionId'], cancelled['id'])
            late_write = api.write_batch_png(final['sessionId'], cancelled['id'], base64.b64encode(png).decode('ascii'))
            _require(late_prepare == {'status': 'cancelled', 'item': cancelled}
                and late_write == {'status': 'cancelled', 'item': cancelled}
                and current_receipt() == final, 'uncertainty_late_work_revived_session')
            _require(_read_owned(output, output / good['outputName'], MAX_PNG_BYTES) == png
                and _read_owned(output, output / MANIFEST_NAME, MAX_MANIFEST_BYTES) == manifest_bytes
                and _read_owned(output, output / sentinel_name, MAX_SOURCE_BYTES) == SENTINEL
                and {p.name for p in output.iterdir()} == {good['outputName'], MANIFEST_NAME, sentinel_name},
                'uncertainty_late_work_or_output_set_changed')
            for name, data in sources.items():
                _require(_read_owned(fixtures, fixtures / name, MAX_SOURCE_BYTES) == data, 'uncertainty_source_changed')
            with api._batch._lock:
                _require(api._batch._session['active'] is None and all(item['_bytes'] is None
                    and '_preparation' not in item for item in api._batch._session['items']), 'uncertainty_source_buffers_retained')
            _require(api.list_profiles() == profiles and downloads.snapshot() == prior_downloads,
                     'uncertainty_profiles_or_downloads_changed')
            assert_preserved(baseline, ev(STATE, 'uncertain-editor-final-' + mode))
            row.update(verified=True, latePrepareRejected=True, lateWriteRejected=True,
                completeEditorStateUnchanged=True, retainedFilesUnchanged=True, terminalStatus=final['status'],
                diskManifestStatus=disk['status'], diskMatchesTerminal=_json_equal(disk, {'schema': 'nivram-batch-export/1', **final}),
                pngSha256=decoded[0]['sha256'], manifestSha256=hashlib.sha256(manifest_bytes).hexdigest(),
                terminalReceipt=final, diskManifest=disk, pngMetadata=decoded[0])
            decoded_outputs.append(decoded)
            check('native-lost-acknowledgement-stops' if mode == 'lost-ack' else 'native-uncertain-manifest-stops',
                nativeGesture=gesture, originalChooserReturnsVerified=True, noSecondPreparation=True,
                noRenderFailureReclassification=True, noFinishCall=True, latePrepareRejected=True, lateWriteRejected=True,
                terminalStatus=final['status'], diskManifestStatus=disk['status'], **decoded[0])
        finally:
            failure_active = sys.exc_info()[0] is not None
            row['dialogs'] = observer.snapshot()
            try:
                with api._batch._lock:
                    session = api._batch._session
                    current_id = session['id'] if session and session['status'] == 'running' else None
                if current_id is not None:
                    fault.originals['cancel'](current_id)
            finally:
                cleanup = fault.close()
                row['faultObserver'] = cleanup
                observer.close()
                if not failure_active:
                    _require(cleanup['shutdownConfirmed'] is True, 'uncertainty_final_observer_cleanup_unconfirmed')
    assert_rgba_equal(decoded_outputs[0], decoded_outputs[1])
    assert_preserved(baseline, ev(STATE, 'uncertain-editor-after-both-cases'))
    facts['uncertaintyVerified'] = True
    _require(observation_failures(report) == [], 'uncertainty_final_observations_failed')
    check('native-uncertain-retained-files-and-editor', batches=2, completed=2, cancelled=2,
        sourceAndProfileSettingsUnchanged=True, completeEditorStateUnchanged=True, bothPublishedPngsRetained=True,
        independentManifestStatesVerified=True, noTemporaryStages=True, rgbaIdentical=True, browserDownloadCount=0)
