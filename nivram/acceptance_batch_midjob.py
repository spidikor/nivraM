"""Native Cancel after one completed PNG and before one delayed preparation.

All dialogs, production rendering/writes and cancellation calls are real. The
acceptance-only barrier delays a completed response without changing it. This
scenario does not qualify production window-close or an interrupted worker.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
import time

from .acceptance_batch_barrier import SecondPreparationBarrier
from .acceptance_batch_png import (NativeDialogObserver, _require, _ordinary_directory, _ordinary_child,
    _write_fresh, _read_owned, _json_object, _json_equal, _settings_equal, _no_paths,
    MANIFEST_NAME, MAX_MANIFEST_BYTES, MAX_PNG_BYTES, MAX_SOURCE_BYTES)
from .acceptance_drawing_png import STATE, READY, OPTIONS, assert_preserved, validate_png_bytes

REQUIRED_CHECKS = frozenset({
    'native-batch-midjob-synthetic-inputs', 'native-batch-first-png-before-cancel',
    'native-batch-midjob-cancel-and-late-return', 'native-batch-cancelled-manifest-and-integrity',
})
INPUT_NAMES = ('midjob first.nivram', 'midjob second.nivram')
SENTINEL_NAME = 'existing.png'
SENTINEL = b'Existing synthetic output must survive cancellation.\n'
CANCEL_ERROR = {'code': 'cancelled', 'message': 'This file was cancelled before a PNG was published.'}


def validate_cancelled(receipt, manifest, settings, sources, root):
    keys = {'sessionId', 'status', 'settings', 'items', 'manifestName', 'manifestError'}
    _require(type(receipt) is dict and set(receipt) == keys
             and type(manifest) is dict and set(manifest) == keys | {'schema'}
             and _json_equal(manifest, {'schema': 'nivram-batch-export/1', **receipt}),
             'midjob_manifest_receipt_mismatch')
    _require(receipt['status'] == 'cancelled' and receipt['manifestError'] is None
             and receipt['manifestName'] == MANIFEST_NAME
             and type(receipt['sessionId']) is str and re.fullmatch('[a-f0-9]{32}', receipt['sessionId'])
             and _settings_equal(receipt['settings'], settings) and _no_paths(manifest, root),
             'midjob_terminal_state_or_settings_invalid')
    items = receipt['items']
    fields = {'id', 'name', 'extension', 'status', 'sourceSha256', 'outputName', 'outputSha256', 'dimensions', 'error'}
    _require(type(items) is list and len(items) == 2 and all(type(item) is dict and set(item) == fields for item in items),
             'midjob_item_shape_invalid')
    _require(all(type(item['id']) is str and re.fullmatch('[a-f0-9]{32}', item['id'])
                 and type(item['name']) is str for item in items)
             and len({item['id'] for item in items}) == 2 and {item['name'] for item in items} == set(INPUT_NAMES)
             and type(sources) is dict and set(sources) == set(INPUT_NAMES), 'midjob_input_identity_invalid')
    for item in items:
        _require(type(sources[item['name']]) is bytes and item['extension'] == '.nivram'
                 and item['sourceSha256'] == hashlib.sha256(sources[item['name']]).hexdigest(), 'midjob_source_hash_mismatch')
    good, cancelled = items
    _require(good['status'] == 'exported' and good['error'] is None
             and good['outputName'] == Path(good['name']).stem + '.png'
             and type(good['outputSha256']) is str and re.fullmatch('[a-f0-9]{64}', good['outputSha256'])
             and type(good['dimensions']) is dict and set(good['dimensions']) == {'width', 'height', 'padding'}
             and type(good['dimensions']['width']) is int and good['dimensions']['width'] == 640
             and type(good['dimensions']['height']) is int and 24 < good['dimensions']['height'] <= 4096
             and type(good['dimensions']['padding']) is int and good['dimensions']['padding'] == 12,
             'midjob_completed_item_invalid')
    _require(cancelled['status'] == 'cancelled' and cancelled['error'] == CANCEL_ERROR
             and all(cancelled[key] is None for key in ('outputName', 'outputSha256', 'dimensions')),
             'midjob_cancelled_item_published_or_misreported')
    return good, cancelled


UI = """(() => {
    const $=id=>document.getElementById(id), details=$('batch-export-controls');
    const refresh=[...details.querySelectorAll('button')].filter(b=>b.textContent==='Refresh profiles');
    const meter=details.querySelector('progress');
    return {status:$('batch-export-status').textContent, startDisabled:$('batch-export-button').disabled,
        cancelDisabled:$('batch-export-cancel').disabled, profileDisabled:$('batch-export-profile').disabled,
        refreshDisabled:refresh.length===1?refresh[0].disabled:null, meterValue:meter.value, meterMax:meter.max,
        rows:[...$('batch-export-results').children].map(row=>row.textContent),
        stageCount:[...document.querySelectorAll('svg[aria-hidden="true"]')].filter(svg=>
            svg.style.left==='-100000px'&&svg.style.position==='fixed').length};
})()"""


def validate_ui(value, good, cancelled):
    _require(type(value) is dict and value.get('status') ==
        '1 of 2 PNG files saved. Batch cancelled. Manifest: nivram-batch-manifest.json. Completed files were retained.'
        and all(value.get(key) is False for key in ('startDisabled', 'profileDisabled', 'refreshDisabled'))
        and value.get('cancelDisabled') is True and value.get('meterValue') == value.get('meterMax') == 2
        and value.get('stageCount') == 0 and value.get('rows') == [
            f"{good['name']}: exported → {good['outputName']}",
            f"{cancelled['name']}: cancelled — {CANCEL_ERROR['message']}"], 'midjob_terminal_ui_invalid')


def validate_retained(before, receipt, manifest, settings, sources, root, png_metadata):
    """Validate the final receipt at the pre-cancel observation boundary."""
    good, cancelled = validate_cancelled(receipt, manifest, settings, sources, root)
    _require(type(before) is dict and before.get('status') == 'running'
             and before.get('sessionId') == receipt['sessionId']
             and type(before.get('items')) is list and len(before['items']) == 2
             and all(type(item) is dict for item in before['items']), 'midjob_original_session_changed')
    first, pending = before['items']
    _require(good == first and pending.get('status') == 'pending' and pending.get('error') is None
             and all(pending.get(key) is None for key in ('outputName', 'outputSha256', 'dimensions'))
             and all(pending.get(key) == cancelled[key] for key in ('id', 'name', 'extension', 'sourceSha256'))
             and _settings_equal(before.get('settings'), receipt['settings']), 'midjob_original_items_changed')
    _require(type(png_metadata) is dict and good['outputSha256'] == png_metadata.get('sha256')
             and good['dimensions'] == {key: png_metadata.get(key) for key in ('width', 'height', 'padding')},
             'midjob_retained_png_provenance_mismatch')
    return good, cancelled


def run(*, evaluate, poll, choose, check, stage, report, profile, api, downloads, window):
    import webview
    from .acceptance_click import click_png_button
    from .persistence import validate_project

    deadline = time.monotonic() + 120
    def remaining(cap):
        value = min(cap, deadline - time.monotonic())
        if value <= 0:
            raise TimeoutError('The native mid-job cancellation scenario exceeded its deadline.')
        return value
    def ev(expression, label):
        return evaluate(expression, timeout=remaining(10), stage=label)
    def wait(expression, cap=20):
        return poll(expression, timeout=remaining(cap))
    def backend_receipt():
        with api._batch._lock:
            session = api._batch._session
            _require(type(session) is dict, 'midjob_session_missing')
            return api._batch._receipt(session)

    profile = _ordinary_directory(profile)
    fixtures = profile / 'synthetic fixtures'
    _require(not fixtures.is_symlink() and not fixtures.is_junction(), 'midjob_fixture_redirected')
    fixtures.mkdir(exist_ok=True)
    fixtures = _ordinary_child(profile, fixtures, directory=True)
    output = fixtures / 'midjob output'
    output.mkdir(exist_ok=False)
    output = _ordinary_child(fixtures, output, directory=True)
    facts = {'midJobVerified': False, 'productionCloseVerified': False,
             'scientificWorkerInterruptionVerified': False, 'delayLocation': 'after_real_preparation_before_batch_adoption'}
    report['batch_midjob_cancel'] = facts
    stage('native-batch-midjob-setup')
    ev("""(() => {document.getElementById('import-button').click();
        document.getElementById('structure-input').value='CCO';document.getElementById('load-structure').click();return true;})()""",
        'midjob-import-synthetic-ethanol')
    wait("!document.getElementById('import-dialog').open && " + READY +
         " && window.nivramDocument.getSnapshot().document.graph.atoms.length===3")
    ev("""(() => {const $=id=>document.getElementById(id);
        $('export-width').value='640';$('export-width').dispatchEvent(new Event('input'));
        $('margin-mode').value='pixels';$('margin-mode').dispatchEvent(new Event('input'));
        $('margin-value').value='12';$('margin-value').dispatchEvent(new Event('input'));
        $('batch-export-controls').open=true;return true;})()""", 'midjob-fixed-export-settings')
    wait(READY)
    baseline = ev(STATE, 'midjob-editor-baseline')
    project = validate_project(copy.deepcopy(baseline['snapshot']['document']))
    _require(project == baseline['snapshot']['document'] and project['export'] == OPTIONS, 'midjob_setup_changed_project')
    encoded = json.dumps(project, ensure_ascii=False, allow_nan=False).encode('utf-8')
    sources = {INPUT_NAMES[0]: encoded, INPUT_NAMES[1]: encoded + b'\n'}
    for name, data in sources.items():
        _write_fresh(fixtures, fixtures / name, data)
    _write_fresh(output, output / SENTINEL_NAME, SENTINEL)
    settings = {'style': copy.deepcopy(project['style']), 'export': dict(OPTIONS), 'profile': None}
    profiles = api.list_profiles()
    prior_downloads = downloads.snapshot()
    _require(all(prior_downloads.get(key) == 0 for key in ('last_id', 'accepted', 'completed', 'active', 'failures')),
             'midjob_prior_downloads_present')
    check('native-batch-midjob-synthetic-inputs', inputCount=2, currentDrawingSettings=True,
          sourceHashes={name: hashlib.sha256(data).hexdigest() for name, data in sources.items()})
    observer = NativeDialogObserver(window, choose, root=fixtures,
        inputs=tuple(fixtures / name for name in sources), folder=output,
        open_kind=webview.FileDialog.OPEN, folder_kind=webview.FileDialog.FOLDER, timeout=lambda: remaining(15))
    barrier = SecondPreparationBarrier(api._batch, hold_seconds=remaining(30))
    observer.attach()
    barrier.attach()
    try:
        stage('native-batch-midjob-first-export')
        gesture = click_png_button(window, evaluate, 'batch')
        _require(barrier.held.wait(remaining(30)), 'midjob_second_preparation_not_held')
        observer.assert_complete()
        observer.close()
        before_cancel = backend_receipt()
        _require(before_cancel['status'] == 'running' and len(before_cancel['items']) == 2
                 and [item['status'] for item in before_cancel['items']] == ['exported', 'pending'],
                 'midjob_cancel_boundary_invalid')
        first, second = before_cancel['items']
        _require(first['name'] in INPUT_NAMES and first['outputName'] == Path(first['name']).stem + '.png',
                 'midjob_completed_output_name_invalid')
        png = _read_owned(output, output / first['outputName'], MAX_PNG_BYTES)
        metadata, _ = validate_png_bytes(png)
        _require(first['outputSha256'] == metadata['sha256'], 'midjob_first_png_hash_mismatch')
        wait("document.getElementById('batch-export-button').disabled && !document.getElementById('batch-export-cancel').disabled")
        assert_preserved(baseline, ev(STATE, 'midjob-editor-before-cancel'))
        check('native-batch-first-png-before-cancel', originalChooserReturnsVerified=True,
              completed=1, pending=1, nativeGesture=gesture, **metadata)
        stage('native-batch-midjob-cancel-button')
        cancel_gesture = click_png_button(window, evaluate, 'batch-cancel')
        # The native bridge cancellation executes independently of the delayed
        # preparation call. Observe its real signal/status before releasing it.
        cancel_deadline = time.monotonic() + remaining(10)
        while time.monotonic() < cancel_deadline and backend_receipt()['status'] != 'cancelled':
            time.sleep(.02)
        _require(backend_receipt()['status'] == 'cancelled' and time.monotonic() < cancel_deadline,
                 'midjob_native_cancel_not_observed')
        barrier.release_after_cancel()
        _require(barrier.finished.wait(remaining(10)), 'midjob_late_prepare_unsettled')
        barrier.assert_complete()
        wait("!document.getElementById('batch-export-button').disabled && document.getElementById('batch-export-status').textContent.includes('Batch cancelled.')")
        receipt = backend_receipt()
        manifest_bytes = _read_owned(output, output / MANIFEST_NAME, MAX_MANIFEST_BYTES)
        good, cancelled = validate_retained(before_cancel, receipt, _json_object(manifest_bytes), settings, sources, fixtures, metadata)
        validate_ui(ev(UI, 'midjob-terminal-ui'), good, cancelled)
        # Submit a delayed write directly through the real desktop method to
        # verify the backend publication gate as well as the UI's aborted loop.
        late_write = api.write_batch_png(before_cancel['sessionId'], second['id'], base64.b64encode(png).decode('ascii'))
        _require(late_write == {'status': 'cancelled', 'item': cancelled}, 'midjob_late_write_not_rejected')
        _require(backend_receipt() == receipt, 'midjob_late_write_changed_session')
        facts.update(midJobVerified=True, lateWriteRejected=True, barrier=barrier.snapshot())
        check('native-batch-midjob-cancel-and-late-return', nativeCancelGesture=cancel_gesture,
              realPreparationReturnedLate=True, lateSceneNotAdopted=True, lateWriteRejected=True,
              delayLocation=facts['delayLocation'])
        _require(_read_owned(output, output / good['outputName'], MAX_PNG_BYTES) == png,
                 'midjob_completed_png_changed_after_cancel')
        _require(_read_owned(output, output / MANIFEST_NAME, MAX_MANIFEST_BYTES) == manifest_bytes,
                 'midjob_late_work_changed_manifest')
        _require(_read_owned(output, output / SENTINEL_NAME, MAX_SOURCE_BYTES) == SENTINEL, 'midjob_existing_output_changed')
        _require({path.name for path in output.iterdir()} == {good['outputName'], MANIFEST_NAME, SENTINEL_NAME},
                 'midjob_unexpected_publication_or_temporary_file')
        for name, data in sources.items():
            _require(_read_owned(fixtures, fixtures / name, MAX_SOURCE_BYTES) == data, 'midjob_source_changed')
        with api._batch._lock:
            _require(api._batch._session['active'] is None and all(item['_bytes'] is None
                and '_preparation' not in item for item in api._batch._session['items']), 'midjob_source_buffers_retained')
        _require(api.list_profiles() == profiles and downloads.snapshot() == prior_downloads, 'midjob_profile_or_download_changed')
        assert_preserved(baseline, ev(STATE, 'midjob-editor-final'))
        facts.update(pngSha256=metadata['sha256'], manifestSha256=hashlib.sha256(manifest_bytes).hexdigest())
        check('native-batch-cancelled-manifest-and-integrity', completed=1, cancelled=1,
              completedPngRetained=True, noSecondPng=True, inputsUnchanged=True, settingsUnchanged=True,
              completeEditorStateUnchanged=True, sourceBuffersReleased=True, browserDownloadCount=0)
    finally:
        failure_active = sys.exc_info()[0] is not None
        facts['dialogs'] = observer.snapshot()
        try:
            with api._batch._lock:
                current = api._batch._session
                current_id = current['id'] if current and current['status'] == 'running' else None
            if current_id is not None:
                api._batch.cancel(current_id)
        finally:
            cleanup = barrier.close()
            facts['barrierShutdown'] = cleanup
            observer.close()
            if not failure_active:
                _require(cleanup['shutdownConfirmed'] is True, 'midjob_observer_cleanup_unconfirmed')
