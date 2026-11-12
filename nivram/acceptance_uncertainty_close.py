"""Close the unchanged uncertainty workflow through the real production lifecycle."""
from __future__ import annotations

import copy
import hashlib
import os
import stat
from pathlib import Path
import re
import time

from .acceptance_batch_png import _ordinary_child, _ordinary_directory, _require, MAX_PNG_BYTES
from .acceptance_batch_uncertain import observation_failures, NAMES, MANIFEST_NAME
from .acceptance_close_dialog import ConfirmationObserver, post_close
from .acceptance_drawing_png import STATE


def read_fixture_file(root, path):
    path = _ordinary_child(root, path)
    def identity(value):
        _require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1,
                 'uncertainty_close_fixture_alias')
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns
    before = identity(path.lstat())
    with path.open('rb') as stream:
        _require(identity(os.fstat(stream.fileno())) == before,
                 'uncertainty_close_fixture_replaced_before_read')
        data = stream.read(MAX_PNG_BYTES + 1)
        _require(identity(os.fstat(stream.fileno())) == before,
                 'uncertainty_close_fixture_changed_during_read')
    _require(identity(path.lstat()) == before and 0 < len(data) <= MAX_PNG_BYTES
             and len(data) == before[2], 'uncertainty_close_fixture_changed_after_read')
    return data


def fixture_inventory(profile):
    """Read only the bounded synthetic fixture tree, rejecting redirected entries."""
    root = _ordinary_directory(profile)
    fixtures = _ordinary_child(root, root / 'synthetic fixtures', directory=True)
    rows = []
    entries = list(fixtures.iterdir())
    _require(len(entries) <= 8, 'uncertainty_close_fixture_bound')
    for entry in sorted(entries):
        directory = entry.is_dir()
        _ordinary_child(fixtures, entry, directory=directory)
        children = list(entry.iterdir()) if directory else [entry]
        _require(len(children) <= 4, 'uncertainty_close_output_bound')
        if directory:
            rows.append({'path': entry.relative_to(fixtures).as_posix(), 'kind': 'directory'})
        for path in sorted(children):
            data = read_fixture_file(entry if directory else fixtures, path)
            rows.append({'path': path.relative_to(fixtures).as_posix(), 'kind': 'file',
                         'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    _require(sum(row['kind'] == 'file' for row in rows) == 10,
             'uncertainty_close_fixture_file_count')
    return rows


def valid_inventory(rows):
    if type(rows) is not list or len(rows) != 12:
        return False
    expected = {}
    for names in NAMES.values():
        folder = names[0][0]
        expected[folder] = 'directory'
        for name in names:
            expected[name] = 'file'
        for name in (Path(names[0]).stem + '.png', Path(names[0]).stem + ' (2).png', MANIFEST_NAME):
            expected[folder + '/' + name] = 'file'
    seen = set()
    for row in rows:
        if type(row) is not dict or type(row.get('path')) is not str:
            return False
        path = row['path']
        if path in seen or path not in expected or row.get('kind') != expected[path]:
            return False
        seen.add(path)
        if row['kind'] == 'directory':
            if set(row) != {'path', 'kind'}:
                return False
        elif (set(row) != {'path', 'kind', 'bytes', 'sha256'} or type(row['bytes']) is not int
              or not 0 < row['bytes'] <= MAX_PNG_BYTES or type(row['sha256']) is not str
              or re.fullmatch('[a-f0-9]{64}', row['sha256']) is None):
            return False
    return seen == set(expected)


def journey_failures(facts):
    if type(facts) is not dict:
        return ['uncertainty_close_facts_missing']
    required = ('workflowVerifiedBeforeClose', 'approvedCloseVerified', 'actualClosedObserved',
                'exactRecoveryVerified', 'retainedFilesVerifiedAfterClose', 'verifyThreadFinished',
                'batchManagerClosed')
    if (any(facts.get(key) is not True for key in required)
            or facts.get('fallbackDestroy') is not False
            or facts.get('confirmationShutdown') != {'shutdownConfirmed': True, 'methodsRestored': True}
            or facts.get('closeRequest') != {'message': 'WM_CLOSE', 'ownedWindowVerified': True, 'posted': True}):
        return ['uncertainty_close_journey_unconfirmed']
    rows = facts.get('confirmations')
    if (type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict
            or rows[0].get('decision') is not True
            or any(rows[0].get(key) is not True for key in (
                'originalCalled', 'originalReturned', 'actualReturnVerified', 'buttonPosted', 'workerExited'))
            or rows[0].get('error')):
        return ['uncertainty_close_confirmation_unconfirmed']
    before, after = facts.get('fixturesBefore'), facts.get('fixturesAfter')
    if not valid_inventory(before) or before != after:
        return ['uncertainty_close_retained_files_unconfirmed']
    seconds = facts.get('completionSeconds')
    if type(seconds) not in (int, float) or not 0 <= seconds < 40:
        return ['uncertainty_close_completion_bound']
    return []


def production_failures(report):
    from .desktop import _smoke_failures
    try:
        failures = _smoke_failures(report['production_close'])
    except (KeyError, TypeError, AttributeError, ValueError):
        failures = ['production_close_report_invalid']
    return failures + observation_failures(report) + journey_failures(report.get('uncertainty_close'))


def request_close(*, evaluate, report, profile, api, window, coordinator, lifetime):
    from .persistence import validate_project
    started = time.monotonic()
    deadline = started + 40
    _require(observation_failures(report) == [], 'uncertainty_workflow_not_verified_before_close')
    baseline = evaluate(STATE, timeout=10, stage='uncertainty-before-production-close')
    project = validate_project(copy.deepcopy(baseline['snapshot']['document']))
    _require(baseline['snapshot']['dirty'] is True, 'uncertainty_close_requires_dirty_editor')
    facts = {'workflowVerifiedBeforeClose': True, 'approvedCloseVerified': False,
             'actualClosedObserved': False, 'exactRecoveryVerified': False,
             'retainedFilesVerifiedAfterClose': False, 'fallbackDestroy': False,
             'verifyThreadFinished': False, 'batchManagerClosed': False,
             'fixturesBefore': fixture_inventory(profile)}
    report['uncertainty_close'] = facts
    confirmations = ConfirmationObserver(window, decisions=(True,))
    confirmations.attach()
    try:
        facts['closeRequest'] = post_close(window)
        while time.monotonic() < deadline:
            if window.events.closed.is_set() and lifetime.snapshot()['closed_observed'] is True:
                break
            time.sleep(.02)
        _require(time.monotonic() < deadline and window.events.closed.is_set()
                 and lifetime.snapshot()['closed_observed'] is True and coordinator._closed is True,
                 'uncertainty_actual_production_close_unconfirmed')
        confirmations.assert_count(1)
        facts.update(approvedCloseVerified=True, actualClosedObserved=True)
        recovery = api._store.list_recovery()
        _require(recovery and api._store.recover(recovery[0]['id'])['document'] == project
                 and api._store._recovery_revision == baseline['snapshot']['revision']
                 and api._store._recovery_generation == baseline['snapshot']['recoveryGeneration'],
                 'uncertainty_close_exact_recovery_unconfirmed')
        facts['exactRecoveryVerified'] = True
        facts['fixturesAfter'] = fixture_inventory(profile)
        _require(facts['fixturesBefore'] == facts['fixturesAfter'], 'uncertainty_close_changed_retained_files')
        facts['retainedFilesVerifiedAfterClose'] = True
    finally:
        facts['confirmations'] = confirmations.snapshot()
        facts['confirmationShutdown'] = confirmations.close()
        facts['completionSeconds'] = time.monotonic() - started
    _require(facts['completionSeconds'] < 40, 'uncertainty_close_completion_bound')
