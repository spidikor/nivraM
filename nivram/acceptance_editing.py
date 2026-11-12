"""Synthetic native rotation/history/save/reopen through production controls."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import threading
import time

REQUIRED_CHECKS = frozenset({'native-edit-import', 'native-edit-rotation-history',
    'native-edit-save-and-disk', 'native-edit-reopen-exact'})
SMILES = '[13CH3][C@@H](O)C(=O)[O-].[Na+]'
GESTURES = ('edit-import', 'edit-load', 'edit-rotate-cw', 'edit-undo', 'edit-redo',
            'edit-save-as', 'edit-undo', 'edit-save-as', 'edit-open')
FACTS = ('importIdentityVerified', 'rotationCoordinatesVerified', 'undoExact', 'redoExact',
    'savedDocumentExact', 'savePreservedState', 'comparisonDocumentDistinct',
    'comparisonSaveExact', 'reopenedDocumentExact', 'reopenOneRevision', 'firstFileRetained')
STATE = """(() => ({snapshot:window.nivramDocument.getSnapshot(),
    drawing:document.getElementById('drawing').outerHTML,
    undoDisabled:document.getElementById('undo-button').disabled,
    redoDisabled:document.getElementById('redo-button').disabled}))()"""
READY = "document.getElementById('request-status').textContent==='Ready'"


def require(value, code):
    if not value:
        raise AssertionError(code)


def identity(graph):
    from rdkit import Chem
    from .chemistry import graph_to_mol, export_mol
    expected = Chem.MolToSmiles(Chem.MolFromSmiles(SMILES), isomericSmiles=True)
    require(Chem.MolToSmiles(graph_to_mol(graph), isomericSmiles=True) == expected, 'edit_graph_identity')
    restored = Chem.MolFromMolBlock(export_mol(graph), removeHs=False, strictParsing=True)
    require(restored is not None and Chem.MolToSmiles(restored, isomericSmiles=True) == expected,
            'edit_mol_identity')


def rotation_document(document):
    """Independent clockwise 30-degree coordinate oracle, without rotate_graph."""
    out = copy.deepcopy(document)
    atoms = out['graph']['atoms']
    require(len(atoms) >= 2, 'edit_rotation_fixture')
    cx, cy = (math.fsum(a[k] for a in atoms) / len(atoms) for k in ('x', 'y'))
    cosine, sine = math.sqrt(3) / 2, -.5
    for atom in atoms:
        dx, dy = atom['x'] - cx, atom['y'] - cy
        atom['x'], atom['y'] = cx + cosine * dx - sine * dy, cy + sine * dx + cosine * dy
    return out


def assert_rotation(before, after):
    expected = rotation_document(before)
    actual = copy.deepcopy(after)
    require(len(actual['graph']['atoms']) == len(expected['graph']['atoms']), 'edit_rotation_atom_count')
    for a, b in zip(actual['graph']['atoms'], expected['graph']['atoms']):
        for key in ('x', 'y'):
            require(type(a[key]) in (int, float) and math.isfinite(a[key]) and abs(a[key] - b[key]) <= 1e-9,
                    'edit_rotation_coordinates')
            a[key] = b[key]
    require(actual == expected and after != before, 'edit_rotation_changed_other_fields')
    identity(after['graph'])


def assert_step(before, after, *, document, dirty, redo_disabled):
    expected = copy.deepcopy(before['snapshot'])
    require(type(expected['revision']) is int, 'edit_revision_type')
    expected.update(document=document, revision=expected['revision'] + 1, dirty=dirty)
    require(after['snapshot'] == expected and after['undoDisabled'] is False
            and after['redoDisabled'] is redo_disabled, 'edit_transaction_or_history_mismatch')


def assert_save(before, after):
    expected = copy.deepcopy(before)
    expected['snapshot']['dirty'] = False
    require(after == expected, 'edit_save_changed_state')


def prepare_paths(profile):
    from .native_dialog_worker import _fixture_paths
    profile = Path(profile)
    require(profile.is_dir() and not profile.is_symlink() and not profile.is_junction(), 'edit_profile_redirected')
    fixtures = profile / 'synthetic fixtures'
    fixtures.mkdir(exist_ok=False)
    paths = [fixtures / 'rotated chiral \u00fc.nivram', fixtures / 'comparison chiral \u00fc.nivram']
    for path in paths:
        _fixture_paths(profile, path)  # Reuse the exact existing picker boundary before UI actions.
        require(not path.exists(), 'edit_fixture_not_fresh')
    return paths


def observation_failures(report):
    facts = report.get('editing_observation')
    if type(facts) is not dict or type(facts.get('schemaVersion')) is not int or facts['schemaVersion'] != 1:
        return ['native_editing_observation_unconfirmed']
    good = all(facts.get(k) is True for k in FACTS)
    good = good and type(facts.get('importRevisionDelta')) is int and facts['importRevisionDelta'] == 2
    dialogs, gestures = facts.get('dialogs'), facts.get('gestures')
    good = good and type(dialogs) is list and len(dialogs) == 3
    good = good and [r.get('mode') for r in dialogs if type(r) is dict] == ['save', 'save', 'open']
    good = good and all(type(r) is dict and all(r.get(k) is True for k in (
        'originalReturned', 'actualReturnVerified', 'fixtureExited')) for r in dialogs)
    good = good and type(gestures) is list and len(gestures) == len(GESTURES)
    good = good and [r.get('kind') for r in gestures if type(r) is dict] == list(GESTURES)
    good = good and all(type(r) is dict and r.get('isTrusted') is True and r.get('userActivation') is True
                        and r.get('status') == 'passed' for r in gestures)
    hashes = [facts.get(k) for k in ('savedSha256', 'comparisonSha256', 'reopenedSha256')]
    good = good and all(type(s) is str and re.fullmatch('[0-9a-f]{64}', s) for s in hashes)
    good = good and hashes[0] == hashes[2] and hashes[0] != hashes[1]
    return [] if good else ['native_editing_observation_unconfirmed']


class EditingDialogs:
    """Observe exact original picker returns; never synthesize a selection."""
    def __init__(self, window, choose, paths, save_kind, open_kind, timeout):
        self.window, self.original, self.choose = window, window.create_file_dialog, choose
        self.plan = [(save_kind, paths[0], 'save'), (save_kind, paths[1], 'save'), (open_kind, paths[0], 'open')]
        self.timeout, self.records, self.count = timeout, [], 0
        self.lock = threading.RLock()

        def observed(*args, **kwargs):
            with self.lock:
                index = self.count; self.count += 1
            require(index < len(self.plan), 'edit_extra_dialog')
            kind, path, mode = self.plan[index]
            expected = {'file_types': ('nivraM projects (*.nivram)',)}
            if mode == 'save':
                expected['save_filename'] = 'structure.nivram'
            else:
                expected['allow_multiple'] = False
            require(len(args) == 1 and type(args[0]) is type(kind) and args[0] == kind and kwargs == expected,
                    'edit_dialog_arguments')
            row = {'mode': mode, 'originalReturned': False, 'actualReturnVerified': False, 'fixtureExited': False}
            with self.lock:
                self.records.append(row)
            try:
                choice = self.choose(path, timeout=self.timeout(), parent_io=True)
            except Exception:
                with self.lock:
                    row['fixtureSetupFailed'] = True
                raise
            try:
                result = self.original(*args, **kwargs)
                with self.lock:
                    row['originalReturned'] = True
            finally:
                primary_failure = sys.exc_info()[0] is not None
                try:
                    selected = choice.finish()
                    require(selected.get('selected') is True and selected.get('worker_exit_confirmed') is True,
                            'edit_dialog_fixture_failed')
                    with self.lock:
                        row['fixtureExited'] = True
                except Exception:
                    if not primary_failure:
                        raise
            values = [result] if type(result) is str else result
            require(type(values) in (list, tuple) and len(values) == 1 and type(values[0]) is str
                    and Path(values[0]).resolve() == path.resolve(), 'edit_original_return_mismatch')
            with self.lock:
                row['actualReturnVerified'] = True
            return result
        self.observed = observed

    def __enter__(self):
        self.window.create_file_dialog = self.observed
        return self

    def __exit__(self, *exc):
        require(self.window.create_file_dialog is self.observed, 'edit_dialog_observer_replaced')
        self.window.create_file_dialog = self.original

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.records)


def run(*, evaluate, poll, choose, check, stage, report, profile, api, downloads, window):
    import webview
    from .acceptance_click import click_png_button
    deadline = time.monotonic() + 120
    facts = {'schemaVersion': 1, 'gestures': [], 'dialogs': []}
    report['editing_observation'] = facts
    paths = prepare_paths(profile)

    def remaining():
        value = deadline - time.monotonic()
        require(value > 0, 'edit_scenario_deadline')
        return min(20, value)

    def ready(revision=None):
        condition = READY
        if revision is not None:
            condition += ' && window.nivramDocument.getSnapshot().revision===' + str(revision)
        try:
            poll(condition, timeout=remaining())
        except Exception:
            try:
                facts['failedWait'] = {'expectedRevision': revision, 'observed': evaluate("""(() => {
                    const s=window.nivramDocument?.getSnapshot(), status=document.getElementById('request-status');
                    return {revision:Number.isSafeInteger(s?.revision)?s.revision:null,
                        dirty:typeof s?.dirty==='boolean'?s.dirty:null,
                        atomCount:Array.isArray(s?.document?.graph?.atoms)?s.document.graph.atoms.length:null,
                        ready:status?.textContent==='Ready',
                        importOpen:document.getElementById('import-dialog')?.open===true,
                        errorVisible:document.getElementById('toast')?.classList.contains('error')===true};
                })()""", timeout=min(1, remaining()), stage='edit-failed-wait-observation')}
            except Exception:
                facts['failedWait'] = {'expectedRevision': revision, 'observationUnavailable': True}
            raise
        return evaluate(STATE, timeout=min(10, remaining()), stage='edit-state')

    def click(kind):
        remaining()
        gesture = click_png_button(window, evaluate, kind)
        facts['gestures'].append(gesture)
        stage(kind)

    def saved(path, before):
        poll("!window.nivramDocument.getSnapshot().dirty && document.getElementById('toast').textContent.startsWith('Project saved and verified:')", timeout=remaining())
        after = ready()
        assert_save(before, after)
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 4_000_000, 'edit_saved_file_invalid')
        data = path.read_bytes()
        require(json.loads(data.decode('utf-8-sig')) == before['snapshot']['document'], 'edit_saved_bytes_differ')
        require(api._store.open(path)['document'] == before['snapshot']['document'], 'edit_saved_store_readback')
        return after, hashlib.sha256(data).hexdigest()

    poll("typeof window.nivramDocument.getSnapshot().recoveryGeneration==='string' && !!window.nivramNative", timeout=remaining())
    before = ready()
    require(before['snapshot']['document']['graph']['atoms'] == [], 'edit_profile_not_empty')
    click('edit-import')
    evaluate('document.getElementById("structure-input").value=' + json.dumps(SMILES) + '; true')
    click('edit-load')
    # Import commits the new graph, then markUnsaved creates a new save context.
    # Both production transitions increment revision; neither adds a second edit.
    imported = ready(before['snapshot']['revision'] + 2)
    require(imported['snapshot']['dirty'] is True and imported['undoDisabled'] is False
            and imported['redoDisabled'] is True, 'edit_import_history')
    facts['importRevisionDelta'] = imported['snapshot']['revision'] - before['snapshot']['revision']
    identity(imported['snapshot']['document']['graph'])
    facts['importIdentityVerified'] = True
    check('native-edit-import', isotopeChargeAndStereoVerified=True)
    click('edit-rotate-cw')
    rotated = ready(imported['snapshot']['revision'] + 1)
    assert_rotation(imported['snapshot']['document'], rotated['snapshot']['document'])
    assert_step(imported, rotated, document=rotated['snapshot']['document'], dirty=True, redo_disabled=True)
    facts['rotationCoordinatesVerified'] = True
    click('edit-undo')
    undone = ready(rotated['snapshot']['revision'] + 1)
    assert_step(rotated, undone, document=imported['snapshot']['document'], dirty=True, redo_disabled=False)
    facts['undoExact'] = True
    click('edit-redo')
    redone = ready(undone['snapshot']['revision'] + 1)
    assert_step(undone, redone, document=rotated['snapshot']['document'], dirty=True, redo_disabled=True)
    facts['redoExact'] = True
    check('native-edit-rotation-history', clockwiseDegrees=30, undoExact=True, redoExact=True)
    with EditingDialogs(window, choose, paths, webview.FileDialog.SAVE, webview.FileDialog.OPEN, remaining) as dialogs:
        try:
            click('edit-save-as')
            first, facts['savedSha256'] = saved(paths[0], redone)
            facts.update(savedDocumentExact=True, savePreservedState=True)
            check('native-edit-save-and-disk', exactDiskAndState=True)
            # A distinct saved comparison state proves Open actually restores
            # content, while keeping unsaved-confirmation testing separate.
            click('edit-undo')
            comparison = ready(first['snapshot']['revision'] + 1)
            assert_step(first, comparison, document=imported['snapshot']['document'], dirty=True, redo_disabled=False)
            require(comparison['snapshot']['document'] != first['snapshot']['document'], 'edit_comparison_not_distinct')
            facts['comparisonDocumentDistinct'] = True
            click('edit-save-as')
            second, facts['comparisonSha256'] = saved(paths[1], comparison)
            facts['comparisonSaveExact'] = True
            click('edit-open')
            poll("document.getElementById('toast').textContent.startsWith('Opened ') && !window.nivramDocument.getSnapshot().dirty", timeout=remaining())
            reopened = ready(second['snapshot']['revision'] + 1)
            assert_step(second, reopened, document=redone['snapshot']['document'], dirty=False, redo_disabled=True)
            identity(reopened['snapshot']['document']['graph'])
            facts.update(reopenedDocumentExact=True, reopenOneRevision=True)
            _, facts['reopenedSha256'] = saved_readback(paths[0], redone['snapshot']['document'])
            require(facts['savedSha256'] == facts['reopenedSha256'], 'edit_first_file_changed')
            facts['firstFileRetained'] = True
        finally:
            facts['dialogs'] = dialogs.snapshot()
    require(not observation_failures(report), 'edit_final_observation_incomplete')
    check('native-edit-reopen-exact', exactDocument=True, distinctComparison=True, exactNativeReturns=3)


def saved_readback(path, document):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 4_000_000, 'edit_readback_file_invalid')
    data = path.read_bytes()
    require(json.loads(data.decode('utf-8-sig')) == document, 'edit_readback_document_changed')
    return data, hashlib.sha256(data).hexdigest()
