"""Decline then approve real unsaved close during two synthetic batch jobs."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import time

from .acceptance_batch_barrier import SecondPreparationBarrier
from .acceptance_batch_midjob import INPUT_NAMES, SENTINEL, SENTINEL_NAME, UI, validate_retained, validate_ui
from .acceptance_batch_png import (NativeDialogObserver, _require, _ordinary_directory, _ordinary_child,
    _write_fresh, _read_owned, _json_object, MANIFEST_NAME, MAX_MANIFEST_BYTES, MAX_PNG_BYTES, MAX_SOURCE_BYTES)
from .acceptance_close_dialog import ConfirmationObserver, post_close
from .acceptance_drawing_png import STATE, READY, OPTIONS, assert_preserved, validate_png_bytes

REQUIRED_CHECKS = frozenset({'native-close-synthetic-inputs', 'native-close-declined-batch-integrity',
    'native-close-approved-batch-integrity', 'native-production-close-finalization'})


def production_failures(report):
    """Reuse the production lifecycle gate; malformed evidence cannot qualify."""
    from .desktop import _smoke_failures
    try:
        failures = _smoke_failures(report['production_close'])
    except (KeyError, TypeError, AttributeError, ValueError):
        return ['production_close_report_invalid']
    facts = report.get('batch_close')
    if type(facts) is not dict:
        return failures + ['production_close_facts_missing']
    if (facts.get('declinedCloseVerified') is not True or facts.get('approvedCloseVerified') is not True
            or facts.get('fallbackDestroy') is not False or facts.get('verifyThreadFinished') is not True
            or facts.get('scientificWorkerInterruptionVerified') is not False or facts.get('batchManagerClosed') is not True
            or facts.get('confirmationShutdown') != {'shutdownConfirmed': True, 'methodsRestored': True}):
        failures.append('production_close_journey_unconfirmed')
    confirmations = facts.get('confirmations')
    if (type(confirmations) is not list or len(confirmations) != 2
            or any(type(row) is not dict for row in confirmations)
            or any(type(row.get('decision')) is not bool for row in confirmations)
            or [row.get('decision') for row in confirmations] != [False, True]
            or any(not all(row.get(key) is True for key in ('originalCalled', 'originalReturned',
                'actualReturnVerified', 'buttonPosted', 'workerExited')) or row.get('error') for row in confirmations)):
        failures.append('production_close_confirmations_unconfirmed')
    journeys = facts.get('journeys')
    if (type(journeys) is not list or len(journeys) != 2 or any(type(row) is not dict for row in journeys)):
        failures.append('production_close_batch_evidence_missing')
    else:
        for index, row in enumerate(journeys):
            barrier = row.get('barrierShutdown')
            if (row.get('decision') is not bool(index) or row.get('retained') is not True
                    or row.get('lateWriteRejected') is not True or row.get('exactRecoveryVerified') is not True
                    or row.get('editorVerifiedBeforeClose') is not True
                    or row.get('editorVerifiedAfterDecline') is not (index == 0)
                    or row.get('lateResponseRejectedAfterActualClose') is not (index == 1)
                    or type(barrier) is not dict or barrier.get('shutdownConfirmed') is not True
                    or barrier.get('methodsRestored') is not True or barrier.get('lateResultStatus') != 'cancelled'
                    or barrier.get('lateResultHadScene') is not False or barrier.get('holdTimedOut') is not False):
                failures.append('production_close_batch_integrity_unconfirmed')
                break
    return failures


def run(*, evaluate, poll, choose, check, stage, report, profile, api, downloads, window,
        coordinator, finalization, lifetime):
    import webview
    from .acceptance_click import click_png_button
    from .persistence import validate_project

    deadline = time.monotonic() + 120
    def remaining(cap):
        value = min(cap, deadline - time.monotonic())
        if value <= 0:
            raise TimeoutError('The native close scenario exceeded its bound.')
        return value
    def ev(expression, label):
        return evaluate(expression, timeout=remaining(10), stage=label)
    def wait(expression):
        return poll(expression, timeout=remaining(20))
    def receipt():
        with api._batch._lock:
            _require(type(api._batch._session) is dict, 'close_batch_session_missing')
            return api._batch._receipt(api._batch._session)
    def until(predicate, label, cap=10):
        limit = time.monotonic() + remaining(cap)
        while time.monotonic() < limit:
            if predicate() and time.monotonic() < limit:
                return
            time.sleep(.02)
        raise TimeoutError(label)

    facts = {'declinedCloseVerified': False, 'approvedCloseVerified': False,
        'fallbackDestroy': False, 'verifyThreadFinished': False, 'scientificWorkerInterruptionVerified': False,
        'delayLocation': 'after_real_preparation_before_batch_adoption', 'journeys': []}
    report['batch_close'] = facts
    root = _ordinary_directory(profile)
    fixtures = root / 'synthetic fixtures'
    fixtures.mkdir(exist_ok=False)
    fixtures = _ordinary_child(root, fixtures, directory=True)
    stage('native-close-setup')
    ev("""(() => {document.getElementById('import-button').click();
        document.getElementById('structure-input').value='CCO';document.getElementById('load-structure').click();return true;})()""",
        'close-import-ethanol')
    wait("!document.getElementById('import-dialog').open && " + READY +
        " && window.nivramDocument.getSnapshot().document.graph.atoms.length===3")
    ev("""(() => {const $=id=>document.getElementById(id);
        $('export-width').value='640';$('export-width').dispatchEvent(new Event('input'));
        $('margin-mode').value='pixels';$('margin-mode').dispatchEvent(new Event('input'));
        $('margin-value').value='12';$('margin-value').dispatchEvent(new Event('input'));
        $('batch-export-controls').open=true;return true;})()""", 'close-export-settings')
    wait(READY)
    baseline = ev(STATE, 'close-editor-baseline')
    project = validate_project(copy.deepcopy(baseline['snapshot']['document']))
    _require(baseline['snapshot']['dirty'] is True and project['export'] == OPTIONS, 'close_dirty_baseline_invalid')
    encoded = json.dumps(project, ensure_ascii=False, allow_nan=False).encode('utf-8')
    sources = {INPUT_NAMES[0]: encoded, INPUT_NAMES[1]: encoded + b'\n'}
    for name, data in sources.items():
        _write_fresh(fixtures, fixtures / name, data)
    settings = {'style': copy.deepcopy(project['style']), 'export': dict(OPTIONS), 'profile': None}
    profiles = api.list_profiles()
    prior_downloads = downloads.snapshot()
    _require(all(prior_downloads.get(k) == 0 for k in ('last_id', 'accepted', 'completed', 'active', 'failures')),
             'close_prior_downloads_present')
    check('native-close-synthetic-inputs', dirtyEditor=True, inputCount=2, batchJourneys=2,
        sourceHashes={name: hashlib.sha256(data).hexdigest() for name, data in sources.items()})
    confirmations = ConfirmationObserver(window)
    confirmations.attach()
    try:
        for index, approved in enumerate((False, True), 1):
            name = 'approved' if approved else 'declined'
            output = fixtures / (name + ' output')
            output.mkdir(exist_ok=False)
            output = _ordinary_child(fixtures, output, directory=True)
            _write_fresh(output, output / SENTINEL_NAME, SENTINEL)
            row = {'decision': approved, 'retained': False, 'lateWriteRejected': False}
            facts['journeys'].append(row)
            observer = NativeDialogObserver(window, choose, root=fixtures,
                inputs=tuple(fixtures / n for n in sources), folder=output,
                open_kind=webview.FileDialog.OPEN, folder_kind=webview.FileDialog.FOLDER, timeout=lambda: remaining(15))
            barrier = SecondPreparationBarrier(api._batch, hold_seconds=remaining(30))
            observer.attach()
            barrier.attach()
            try:
                stage('native-close-' + name + '-batch')
                row['startGesture'] = click_png_button(window, evaluate, 'batch')
                _require(barrier.held.wait(remaining(30)), 'close_second_response_not_held')
                observer.assert_complete()
                observer.close()
                before = receipt()
                _require(before['status'] == 'running' and [x['status'] for x in before['items']] == ['exported', 'pending'],
                         'close_batch_boundary_invalid')
                first, second = before['items']
                png = _read_owned(output, output / first['outputName'], MAX_PNG_BYTES)
                metadata, _ = validate_png_bytes(png)
                _require(metadata['sha256'] == first['outputSha256'], 'close_first_png_hash_mismatch')
                assert_preserved(baseline, ev(STATE, 'close-before-' + name))
                row['closeRequest'] = post_close(window)
                until(lambda: receipt()['status'] == 'cancelled', 'Close review did not cancel the active batch.')
                # For OK, keep the real response held until actual FormClosed.
                # For Cancel, wait for the real decision and review release.
                if approved:
                    until(lambda: lifetime.snapshot()['closed_observed'] is True, 'Actual native close not observed.')
                else:
                    until(lambda: len(confirmations.snapshot()) == index and confirmations.snapshot()[-1]['workerExited'],
                          'Declined native confirmation did not return.')
                    until(lambda: not coordinator._pending, 'Declined review did not settle.')
                    _require(not coordinator._closed and not window.events.closed.is_set()
                        and finalization.snapshot()['started'] is False and not api._batch._closed,
                        'declined_close_stopped_live_services')
                barrier.release_after_cancel()
                _require(barrier.finished.wait(remaining(10)), 'close_late_prepare_unsettled')
                barrier.assert_complete()
                confirmations.assert_count(index)
                current = receipt()
                manifest_bytes = _read_owned(output, output / MANIFEST_NAME, MAX_MANIFEST_BYTES)
                good, cancelled = validate_retained(before, current, _json_object(manifest_bytes), settings, sources, fixtures, metadata)
                late = api.write_batch_png(before['sessionId'], second['id'], base64.b64encode(png).decode('ascii'))
                _require(late == {'status': 'cancelled', 'item': cancelled} and receipt() == current,
                         'close_late_write_changed_batch')
                row['lateWriteRejected'] = True
                _require(_read_owned(output, output / good['outputName'], MAX_PNG_BYTES) == png
                    and _read_owned(output, output / MANIFEST_NAME, MAX_MANIFEST_BYTES) == manifest_bytes
                    and _read_owned(output, output / SENTINEL_NAME, MAX_SOURCE_BYTES) == SENTINEL
                    and {p.name for p in output.iterdir()} == {good['outputName'], MANIFEST_NAME, SENTINEL_NAME},
                    'close_output_or_manifest_changed')
                for filename, data in sources.items():
                    _require(_read_owned(fixtures, fixtures / filename, MAX_SOURCE_BYTES) == data, 'close_input_changed')
                with api._batch._lock:
                    _require(api._batch._session['active'] is None and all(item['_bytes'] is None
                        and '_preparation' not in item for item in api._batch._session['items']), 'close_source_buffers_retained')
                _require(api.list_profiles() == profiles, 'close_profiles_changed')
                recovery = api._store.list_recovery()
                _require(recovery and api._store.recover(recovery[0]['id'])['document'] == project
                    and api._store._recovery_revision == baseline['snapshot']['revision']
                    and api._store._recovery_generation == baseline['snapshot']['recoveryGeneration'],
                    'close_exact_recovery_unconfirmed')
                if not approved:
                    wait("!document.getElementById('batch-export-button').disabled && " + READY)
                    validate_ui(ev(UI, 'close-declined-terminal-ui'), good, cancelled)
                    assert_preserved(baseline, ev(STATE, 'close-declined-editor'))
                    _require(downloads.snapshot() == prior_downloads, 'declined_close_downloads_changed')
                    facts['declinedCloseVerified'] = True
                else:
                    _require(coordinator._closed is True, 'approved_close_coordinator_unconfirmed')
                    facts['approvedCloseVerified'] = True
                row.update(retained=True, pngSha256=metadata['sha256'],
                    manifestSha256=hashlib.sha256(manifest_bytes).hexdigest(), exactRecoveryVerified=True,
                    editorVerifiedBeforeClose=True, editorVerifiedAfterDecline=not approved,
                    lateResponseRejectedAfterActualClose=approved)
                check('native-close-' + name + '-batch-integrity', completed=1, cancelled=1,
                    retainedPng=True, noSecondPng=True, lateWriteRejected=True, exactRecoveryVerified=True,
                    productionCloseObserved=approved)
            finally:
                row['dialogs'] = observer.snapshot()
                row['barrierShutdown'] = barrier.close()
                observer.close()
                _require(row['barrierShutdown']['shutdownConfirmed'], 'close_barrier_shutdown_unconfirmed')
    finally:
        facts['confirmations'] = confirmations.snapshot()
        facts['confirmationShutdown'] = confirmations.close()
