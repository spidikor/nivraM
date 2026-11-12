"""Native saved-profile/source edits after preparation, with real PNG publication.

The fixture edits only new files in its isolated profile. It does not claim the
separate acknowledgement-loss or manifest-uncertainty scenarios.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import sys
import time

from .acceptance_batch_freeze_barrier import PreparedResponseBarrier
from .acceptance_batch_png import (NativeDialogObserver, _require, _ordinary_directory, _ordinary_child,
    _write_fresh, _read_owned, _json_object, _json_equal, _settings_equal, _no_paths,
    MANIFEST_NAME, MAX_MANIFEST_BYTES, MAX_PNG_BYTES, MAX_SOURCE_BYTES)
from .acceptance_batch_midjob import UI
from .acceptance_drawing_png import STATE, READY, OPTIONS, assert_preserved, validate_png_bytes, assert_rgba_equal

REQUIRED_CHECKS = frozenset({'native-freeze-profile-and-inputs', 'native-freeze-prepared-boundary',
    'native-freeze-edits-and-delayed-return', 'native-freeze-png-manifest-and-editor-integrity'})
INPUT_NAMES = ('f1.nivram', 'f2.nivram')
OUTPUT_NAMES = ('f1 (2).png', 'f2.png')
PROFILE_NAME = 'Synthetic batch freeze'
SENTINEL_NAME = 'f1.png'
SENTINEL = b'Existing synthetic output must survive the name collision.\n'
EDITED_SOURCE = b'An intentionally invalid replacement after real preparation.\n'


def observation_failures(report):
    facts = report.get('batch_freeze')
    if type(facts) is not dict:
        return ['freeze_observations_missing']
    barrier, dialogs = facts.get('barrierShutdown'), facts.get('dialogs')
    if type(barrier) is not dict or type(dialogs) is not list or any(type(row) is not dict for row in dialogs):
        return ['freeze_observations_invalid']
    good = (facts.get('freezeVerified') is True and facts.get('uncertainPublicationVerified') is False
        and facts.get('delayLocation') == 'after_real_preparation_adoption_before_bridge_return'
        and barrier.get('shutdownConfirmed') is True and barrier.get('methodsRestored') is True
        and type(barrier.get('calls')) is int and barrier['calls'] == 2 and barrier.get('holdTimedOut') is False
        and all(barrier.get(key) is True for key in ('held', 'released', 'finished', 'secondReady',
            'releasedWhileRunning', 'resultUnchanged'))
        and len(dialogs) == 2 and [(r.get('mode'), r.get('action')) for r in dialogs] == [('open-multiple', None), ('folder', None)]
        and all(r.get('actualReturnVerified') is True and r.get('originalReturned') is True and not r.get('fixtureFailed') for r in dialogs))
    for key in ('originalSourceSha256', 'editedSourceSha256', 'originalProfileSha256',
                'editedProfileSha256', 'newRevisionSha256', 'manifestSha256'):
        good = good and type(facts.get(key)) is str and re.fullmatch('[a-f0-9]{64}', facts[key]) is not None
    good = good and facts.get('originalSourceSha256') != facts.get('editedSourceSha256') \
        and facts.get('originalProfileSha256') != facts.get('editedProfileSha256')
    return [] if good else ['freeze_observations_unconfirmed']


def validate_completed(receipt, manifest, settings, sources, before, metadata, root):
    keys = {'sessionId', 'status', 'settings', 'items', 'manifestName', 'manifestError'}
    _require(type(receipt) is dict and set(receipt) == keys and type(manifest) is dict
        and set(manifest) == keys | {'schema'} and _json_equal(manifest, {'schema': 'nivram-batch-export/1', **receipt}),
        'freeze_manifest_receipt_mismatch')
    _require(type(before) is dict and set(before) == keys and before['status'] == 'running'
        and receipt['status'] == 'completed' and receipt['manifestError'] is None
        and receipt['manifestName'] == MANIFEST_NAME and type(receipt['sessionId']) is str
        and re.fullmatch('[a-f0-9]{32}', receipt['sessionId']) and receipt['sessionId'] == before['sessionId']
        and _settings_equal(receipt['settings'], settings) and _settings_equal(before['settings'], settings)
        and _no_paths(manifest, root), 'freeze_terminal_identity_or_settings_invalid')
    fields = {'id', 'name', 'extension', 'status', 'sourceSha256', 'outputName', 'outputSha256', 'dimensions', 'error'}
    _require(type(sources) is dict and set(sources) == set(INPUT_NAMES) and type(metadata) is list and len(metadata) == 2
        and type(receipt['items']) is list and len(receipt['items']) == 2
        and type(before['items']) is list and len(before['items']) == 2, 'freeze_item_count_invalid')
    ids = set()
    for index, item in enumerate(receipt['items']):
        prior, png = before['items'][index], metadata[index]
        _require(type(item) is dict and set(item) == fields and type(prior) is dict and set(prior) == fields
            and type(item['id']) is str and re.fullmatch('[a-f0-9]{32}', item['id'])
            and item['id'] not in ids and item['name'] == INPUT_NAMES[index]
            and item['extension'] == '.nivram' and item['status'] == 'exported' and item['error'] is None
            and item['outputName'] == OUTPUT_NAMES[index]
            and item['sourceSha256'] == hashlib.sha256(sources[item['name']]).hexdigest()
            and all(item[key] == prior[key] for key in ('id', 'name', 'extension', 'sourceSha256')),
            'freeze_item_provenance_invalid')
        ids.add(item['id'])
        dimensions = item['dimensions']
        _require(type(png) is dict and type(png.get('sha256')) is str and re.fullmatch('[a-f0-9]{64}', png['sha256'])
            and item['outputSha256'] == png['sha256'] and type(dimensions) is dict
            and set(dimensions) == {'width', 'height', 'padding'}
            and all(type(dimensions[key]) is int for key in dimensions)
            and dimensions == {key: png.get(key) for key in dimensions}
            and dimensions['width'] == 640 and 24 < dimensions['height'] <= 4096 and dimensions['padding'] == 12,
            'freeze_png_provenance_invalid')
        _require((index == 0 and prior == item) or (index == 1 and prior['status'] == 'rendering'
            and prior['error'] is None and all(prior[key] is None for key in ('outputName', 'outputSha256', 'dimensions'))),
            'freeze_observed_boundary_changed')
    return receipt['items']


def validate_ui(value, items):
    _require(type(value) is dict and value.get('status') ==
        '2 of 2 PNG files saved. Manifest: nivram-batch-manifest.json. Completed files were retained.'
        and all(value.get(key) is False for key in ('startDisabled', 'profileDisabled', 'refreshDisabled'))
        and value.get('cancelDisabled') is True and value.get('meterValue') == value.get('meterMax') == 2
        and value.get('stageCount') == 0 and value.get('rows') == [
            f"{item['name']}: exported → {item['outputName']}" for item in items], 'freeze_terminal_ui_invalid')


def run(*, evaluate, poll, choose, check, stage, report, profile, api, downloads, window):
    import webview
    from .acceptance_click import click_png_button
    from .persistence import validate_project, atomic_write
    from .profiles import ProfileStore, validate_profile

    deadline = time.monotonic() + 120
    def remaining(cap):
        value = min(cap, deadline - time.monotonic())
        if value <= 0:
            raise TimeoutError('The native freeze scenario exceeded its deadline.')
        return value
    def ev(expression, label):
        return evaluate(expression, timeout=remaining(10), stage=label)
    def wait(expression, cap=20):
        return poll(expression, timeout=remaining(cap))
    def receipt():
        with api._batch._lock:
            _require(type(api._batch._session) is dict, 'freeze_session_missing')
            return api._batch._receipt(api._batch._session)

    profile = _ordinary_directory(profile)
    fixtures = profile / 'synthetic fixtures'
    _require(not fixtures.is_symlink() and not fixtures.is_junction(), 'freeze_fixture_redirected')
    fixtures.mkdir(exist_ok=True)
    fixtures = _ordinary_child(profile, fixtures, directory=True)
    output = fixtures / 'out'
    output.mkdir(exist_ok=False)
    output = _ordinary_child(fixtures, output, directory=True)
    facts = {'freezeVerified': False, 'uncertainPublicationVerified': False,
             'delayLocation': 'after_real_preparation_adoption_before_bridge_return'}
    report['batch_freeze'] = facts
    stage('native-freeze-setup')
    ev("""(() => {document.getElementById('import-button').click();
        document.getElementById('structure-input').value='CCO';document.getElementById('load-structure').click();return true;})()""", 'freeze-import')
    wait("!document.getElementById('import-dialog').open && " + READY +
         " && window.nivramDocument.getSnapshot().document.graph.atoms.length===3")
    ev("""(() => {const $=id=>document.getElementById(id);
        $('export-width').value='640';$('export-width').dispatchEvent(new Event('input'));
        $('margin-mode').value='pixels';$('margin-mode').dispatchEvent(new Event('input'));
        $('margin-value').value='12';$('margin-value').dispatchEvent(new Event('input'));return true;})()""", 'freeze-profile-settings')
    wait(READY)
    before_save = ev(STATE, 'freeze-profile-baseline')
    project = validate_project(copy.deepcopy(before_save['snapshot']['document']))
    _require(project == before_save['snapshot']['document'] and project['export'] == OPTIONS, 'freeze_setup_changed_project')
    ev("document.querySelector('.profile-controls').open=true;document.getElementById('profile-name').value="
        + json.dumps(PROFILE_NAME) + ";document.getElementById('save-profile-button').click();true", 'freeze-save-profile')
    wait("document.getElementById('profile-choice').options.length===2 && !document.getElementById('apply-profile-button').disabled")
    assert_preserved(before_save, ev(STATE, 'freeze-profile-saved'))
    store = _ordinary_child(profile, Path(api._profiles.directory), directory=True)
    listing = ProfileStore(store).list()
    _require(listing == api.list_profiles() and listing['invalidRecords'] == 0 and len(listing['profiles']) == 1,
             'freeze_profile_fresh_readback_failed')
    record = listing['profiles'][0]
    _require(record['revision'] == 1 and record['name'] == PROFILE_NAME and record['export'] == OPTIONS
             and record['style'] == project['style'], 'freeze_profile_settings_invalid')
    record_path = _ordinary_child(store, store / f"{record['id']}-000001.json")
    original_record_bytes = _read_owned(store, record_path, 16384)
    _require(_json_object(original_record_bytes) == record, 'freeze_profile_disk_identity_invalid')
    encoded = json.dumps(project, ensure_ascii=False, allow_nan=False).encode('utf-8')
    sources = {INPUT_NAMES[0]: encoded, INPUT_NAMES[1]: encoded + b'\n'}
    for name, data in sources.items():
        _write_fresh(fixtures, fixtures / name, data)
    _write_fresh(output, output / SENTINEL_NAME, SENTINEL)
    ev("""(() => {const $=id=>document.getElementById(id);
        $('export-width').value='480';$('export-width').dispatchEvent(new Event('input'));
        $('margin-value').value='8';$('margin-value').dispatchEvent(new Event('input'));
        $('batch-export-controls').open=true;const r=[...$('batch-export-controls').querySelectorAll('button')].filter(b=>b.textContent==='Refresh profiles');
        if(r.length!==1)return false;r[0].click();return true;})()""", 'freeze-active-settings-refresh')
    selected = json.dumps(f"{record['id']}/1")
    wait("[...document.getElementById('batch-export-profile').options].some(option=>option.value===" + selected + ")")
    ev("document.getElementById('batch-export-profile').value=" + selected +
        ";document.getElementById('batch-export-profile').dispatchEvent(new Event('change'));true", 'freeze-select-profile')
    baseline = ev(STATE, 'freeze-editor-baseline')
    _require(baseline['snapshot']['document']['graph'] == project['graph']
        and baseline['snapshot']['document']['export'] == {'width': 480, 'marginMode': 'pixels', 'margin': 8, 'transparentBackground': False},
        'freeze_active_export_not_distinct')
    settings = {'style': copy.deepcopy(record['style']), 'export': dict(OPTIONS),
                'profile': {key: record[key] for key in ('id', 'revision', 'name')}}
    prior_downloads = downloads.snapshot()
    _require(all(prior_downloads.get(key) == 0 for key in ('last_id', 'accepted', 'completed', 'active', 'failures')),
             'freeze_prior_downloads_present')
    check('native-freeze-profile-and-inputs', profileSavedThroughNativeUI=True, freshDiskReadback=True,
          originalProfileSha256=hashlib.sha256(original_record_bytes).hexdigest(), inputCount=2)
    observer = NativeDialogObserver(window, choose, root=fixtures,
        inputs=tuple(fixtures / name for name in INPUT_NAMES), folder=output,
        open_kind=webview.FileDialog.OPEN, folder_kind=webview.FileDialog.FOLDER, timeout=lambda: remaining(15))
    barrier = PreparedResponseBarrier(api._batch, hold_seconds=remaining(30))
    observer.attach()
    barrier.attach()
    try:
        stage('native-freeze-start')
        gesture = click_png_button(window, evaluate, 'batch')
        _require(barrier.held.wait(remaining(30)), 'freeze_second_result_not_held')
        observer.assert_complete()
        observer.close()
        before = receipt()
        _require(before['status'] == 'running' and [i['status'] for i in before['items']] == ['exported', 'rendering']
            and _settings_equal(before['settings'], settings), 'freeze_prepared_boundary_invalid')
        first_png = _read_owned(output, output / OUTPUT_NAMES[0], MAX_PNG_BYTES)
        first_decoded = validate_png_bytes(first_png)
        assert_preserved(baseline, ev(STATE, 'freeze-editor-at-boundary'))
        check('native-freeze-prepared-boundary', nativeGesture=gesture, completed=1, prepared=1,
              originalChooserReturnsVerified=True, collisionPreserved=True, **first_decoded[0])

        # Explicit, bounded edits to this scenario's new synthetic files only.
        changed_source = _ordinary_child(fixtures, fixtures / INPUT_NAMES[1])
        atomic_write(changed_source, EDITED_SOURCE)
        revised = api.save_profile(PROFILE_NAME, record['style'], dict(OPTIONS, width=320), record['id'], 1)
        revised_path = _ordinary_child(store, store / f"{record['id']}-000002.json")
        revised_bytes = _read_owned(store, revised_path, 16384)
        _require(revised['revision'] == 2 and revised['export']['width'] == 320
            and _json_object(revised_bytes) == revised, 'freeze_new_revision_not_saved')
        changed_record = validate_profile({**record, 'export': dict(OPTIONS, width=960)})
        changed_record_bytes = json.dumps(changed_record, ensure_ascii=False, allow_nan=False).encode('utf-8')
        atomic_write(record_path, changed_record_bytes)
        changed_listing = ProfileStore(store).list()
        _require(_read_owned(fixtures, changed_source, MAX_SOURCE_BYTES) == EDITED_SOURCE
            and _read_owned(store, record_path, 16384) == changed_record_bytes
            and changed_listing == api.list_profiles() and changed_listing['invalidRecords'] == 0
            and {row['revision']: row['export']['width'] for row in changed_listing['profiles']} == {1: 960, 2: 320},
            'freeze_external_edits_not_observed_on_disk')
        _require(receipt() == before, 'freeze_edits_changed_batch_before_release')
        assert_preserved(baseline, ev(STATE, 'freeze-editor-after-edits'))
        facts.update(originalSourceSha256=hashlib.sha256(sources[INPUT_NAMES[1]]).hexdigest(),
            editedSourceSha256=hashlib.sha256(EDITED_SOURCE).hexdigest(),
            originalProfileSha256=hashlib.sha256(original_record_bytes).hexdigest(),
            editedProfileSha256=hashlib.sha256(changed_record_bytes).hexdigest(),
            newRevisionSha256=hashlib.sha256(revised_bytes).hexdigest())
        barrier.release_after_edits()
        _require(barrier.finished.wait(remaining(10)), 'freeze_delayed_result_unsettled')
        barrier.assert_complete()
        wait("!document.getElementById('batch-export-button').disabled && document.getElementById('batch-export-status').textContent.startsWith('2 of 2 PNG files saved.')")
        check('native-freeze-edits-and-delayed-return', sourceFileReplaced=True, selectedRevisionFileChanged=True,
            newRevisionSaved=True, frozenSettingsUnchanged=True, delayedResponseUnchanged=True, **barrier.snapshot())

        final = receipt()
        manifest_bytes = _read_owned(output, output / MANIFEST_NAME, MAX_MANIFEST_BYTES)
        second_png = _read_owned(output, output / OUTPUT_NAMES[1], MAX_PNG_BYTES)
        second_decoded = validate_png_bytes(second_png)
        items = validate_completed(final, _json_object(manifest_bytes), settings, sources, before,
            [first_decoded[0], second_decoded[0]], fixtures)
        assert_rgba_equal(first_decoded, second_decoded)
        validate_ui(ev(UI, 'freeze-terminal-ui'), items)
        _require(_read_owned(output, output / OUTPUT_NAMES[0], MAX_PNG_BYTES) == first_png
            and _read_owned(output, output / SENTINEL_NAME, MAX_SOURCE_BYTES) == SENTINEL
            and {p.name for p in output.iterdir()} == {MANIFEST_NAME, SENTINEL_NAME, *OUTPUT_NAMES},
            'freeze_existing_or_final_outputs_changed')
        _require(_read_owned(fixtures, changed_source, MAX_SOURCE_BYTES) == EDITED_SOURCE
            and _read_owned(fixtures, fixtures / INPUT_NAMES[0], MAX_SOURCE_BYTES) == sources[INPUT_NAMES[0]]
            and _read_owned(store, record_path, 16384) == changed_record_bytes
            and _read_owned(store, revised_path, 16384) == revised_bytes and api.list_profiles() == changed_listing,
            'freeze_export_overwrote_external_changes')
        with api._batch._lock:
            _require(api._batch._session['active'] is None and all(item['_bytes'] is None
                and '_preparation' not in item for item in api._batch._session['items']), 'freeze_source_buffers_retained')
        _require(downloads.snapshot() == prior_downloads, 'freeze_browser_download_changed')
        assert_preserved(baseline, ev(STATE, 'freeze-editor-final'))
        facts.update(freezeVerified=True, barrier=barrier.snapshot(), pngMetadata=[first_decoded[0], second_decoded[0]],
            manifestSha256=hashlib.sha256(manifest_bytes).hexdigest())
        check('native-freeze-png-manifest-and-editor-integrity', completed=2, originalSourceHashesPreserved=True,
            originalProfileIdentityPreserved=True, rgbaIdentical=True, exactPngProvenance=True,
            completeEditorStateUnchanged=True, externalEditsRetained=True, sourceBuffersReleased=True, browserDownloadCount=0)
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
                _require(cleanup['shutdownConfirmed'] is True, 'freeze_observer_cleanup_unconfirmed')
