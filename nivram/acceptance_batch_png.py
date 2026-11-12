"""Fixed two-input real-native batch completion scenario; no cancellation claim.

The shared acceptance harness owns the protected window, evaluator, fixture
workers and cleanup. The dialog observer invokes the original native method and
returns its actual result unchanged; it never substitutes chooser return values.
"""
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

from .acceptance_drawing_png import STATE, READY, OPTIONS, SMILES, assert_preserved, validate_png_bytes, _semantic_hash_classes
from .worker_processes import timeout_boundary_diagnostic, worker_failure_diagnostic
from .native_dialog_progress import progress_diagnostic
from .parent_io import parent_io_diagnostic
from .supervision_observations import PRIMARY_BOUNDARY
from .observation_envelope import decode_observation, encode_observation, EnvelopeFormatError, MAX_ENVELOPE_BYTES
from .native_dialog_worker import operation_failure_diagnostic
from .native_exception_diagnostic import validate_exception_detail

REQUIRED_CHECKS = frozenset({
    'native-batch-profile-and-synthetic-inputs',
    'native-batch-two-file-open-and-folder-selection',
    'native-batch-partial-success-and-frozen-manifest',
    'native-batch-png-and-document-integrity',
})
PROFILE_NAME = 'Synthetic native batch PNG'
ACTIVE_OPTIONS = {'width': 480, 'marginMode': 'pixels', 'margin': 8, 'transparentBackground': False}
VALID_NAME = 'native batch valid \u00fc.nivram'
INVALID_NAME = 'native batch invalid \u00fc.mol'
SENTINEL_NAME = 'native batch valid \u00fc.png'
OUTPUT_NAME = 'native batch valid \u00fc (2).png'
MANIFEST_NAME = 'nivram-batch-manifest.json'
SENTINEL_BYTES = b'Owned synthetic collision sentinel; preserve these exact bytes.\n'
INVALID_BYTES = b'CCO\n'
MAX_MANIFEST_BYTES = 256 * 1024
MAX_SOURCE_BYTES = 4_000_000
MAX_PNG_BYTES = 32 * 1024 * 1024
INPUT_FILTER = ('Molecular projects and 2D MOL (*.nivram;*.mol)',)
INVALID_MESSAGE = 'The input is malformed or outside the supported 2D chemistry scope.'
DIAGNOSTIC_REASONS = frozenset({
    'supported_folder_control_not_found', 'supported_filename_control_not_found',
    'parent_or_owner_changed', 'ambiguous_owned_dialog', 'ambiguous_filename_field',
    'filename_readback_mismatch', 'submission_failed', 'native_operation_failed', 'folder_submit_route_changed',
    'selection_identity_changed_during_close', 'owned_dialog_changed_during_close',
    'dialog_close_unconfirmed', 'worker_timeout', 'worker_cancelled', 'worker_output_limit',
    'worker_failure', 'worker_cleanup_unconfirmed', 'supervisor_incomplete',
})
DIAGNOSTIC_STAGES = frozenset({'validate', 'find-dialog', 'identify-field', 'set-filename', 'submit', 'confirm-close'})
DIAGNOSTIC_PHASES = frozenset({'command', 'request_encode', 'supervise', 'worker_exit', 'result_decode', 'result_validate'})
DIAGNOSTIC_ERROR_KINDS = frozenset({'timeout', 'cancelled', 'output_limit', 'worker_process_error',
    'json_decode_error', 'unicode_decode_error', 'os_error', 'value_error', 'type_error', 'other_error', 'nonzero_exit'})
DIAGNOSTIC_UI_STATUSES = frozenset({'complete', 'selection-cancelled', 'stopped', 'choosing', 'processing', 'unavailable'})


def _diagnostic_enum(value, choices):
    if value is None:
        return None
    return value if type(value) is str and value in choices else 'unrecognized'


def _diagnostic_int(value, lower, upper):
    return value if type(value) is int and lower <= value <= upper else None


def _diagnostic_bool(value):
    return value if type(value) is bool else None


def dialog_diagnostic(value, *, observation_binding=None):
    """Keep only fixed worker categories and bounded scalar facts, including on failure."""
    available = type(value) is dict and all(type(key) is str for key in value)
    value = value if available else {}
    elapsed = value.get('elapsed_seconds')
    control_count = _diagnostic_int(value.get('control_count'), 0, 256)
    label_counts = value.get('label_counts')
    labels_valid = (type(label_counts) is dict and all(type(key) is str for key in label_counts)
        and set(label_counts) == {'filename', 'folder', 'other'}
        and all(type(number) is int and 0 <= number <= 256 for number in label_counts.values())
        and control_count is not None and sum(label_counts.values()) <= control_count)
    observation = {}
    if observation_binding is not None:
        observation['parent_io_observation'] = None
        raw = value.get('parent_io_observation')
        if (type(observation_binding) is dict
                and all(type(key) is str for key in observation_binding)
                and set(observation_binding) == {'attempt_id', 'source_sha256', 'origin'}
                and type(raw) is str and 0 < len(raw) <= MAX_ENVELOPE_BYTES and raw.isascii()):
            try:
                decoded = decode_observation(raw.encode('ascii'),
                    expected_attempt_id=observation_binding['attempt_id'],
                    expected_source_sha256=observation_binding['source_sha256'],
                    expected_origin=observation_binding['origin'], expected_boundary=PRIMARY_BOUNDARY)
                observation['parent_io_observation'] = encode_observation(decoded).decode('ascii')
            except EnvelopeFormatError:
                pass
    cleanup = value.get('cleanup')
    cleanup = cleanup if type(cleanup) is dict and all(type(key) is str for key in cleanup) else {}
    return {
        'available': available,
        **observation,
        **{key: _diagnostic_bool(value.get(key)) for key in (
            'selected', 'filename_field_verified', 'dialog_close_confirmed', 'worker_exit_confirmed')},
        'exception_detail': validate_exception_detail(value.get('exception_detail')),
        'operation_failure': operation_failure_diagnostic(value.get('operation_failure')),
        'reason': _diagnostic_enum(value.get('reason'), DIAGNOSTIC_REASONS),
        'stage': _diagnostic_enum(value.get('stage'), DIAGNOSTIC_STAGES),
        'selection_mode': _diagnostic_enum(value.get('selection_mode'), frozenset({'file', 'open-multiple', 'folder'})),
        'selection_count': _diagnostic_int(value.get('selection_count'), 0, 2),
        **{key: _diagnostic_int(value.get(key), 0, 256) for key in ('control_count', 'dialog_count')},
        'label_counts': {key: label_counts[key] if labels_valid else None for key in ('filename', 'folder', 'other')},
        'idok_label_kind': _diagnostic_enum(value.get('idok_label_kind'), frozenset({'folder', 'open', 'save', 'other', 'unavailable'})),
        'parent_phase': _diagnostic_enum(value.get('parent_phase'), DIAGNOSTIC_PHASES),
        **{key: _diagnostic_enum(value.get(key), DIAGNOSTIC_ERROR_KINDS) for key in ('error_kind', 'cleanup_error_kind')},
        'worker_returncode': _diagnostic_int(value.get('worker_returncode'), -(2**31), 2**32-1),
        'timeout_boundary': timeout_boundary_diagnostic(value.get('timeout_boundary')),
        'worker_failure': worker_failure_diagnostic(value.get('worker_failure')),
        'child_progress': progress_diagnostic(value.get('child_progress')),
        'parent_io': parent_io_diagnostic(value.get('parent_io')),
        'elapsed_seconds': elapsed if type(elapsed) in (int, float) and 0 <= elapsed <= 60 and math.isfinite(elapsed) else None,
        'cleanup': {
            **{key: _diagnostic_bool(cleanup.get(key)) for key in (
                'shutdownConfirmed', 'worker_exit_confirmed', 'supervisor_thread_exited',
                'cancel_requested', 'owned_dialog_cancel_posted')},
            'cleanup_error_kind': _diagnostic_enum(cleanup.get('cleanup_error_kind'), DIAGNOSTIC_ERROR_KINDS),
        },
    }


def ui_diagnostic(value):
    """No UI text, row contents, captions, paths, or arbitrary supplied keys leave this gate."""
    available = type(value) is dict
    value = value if available else {}
    return {'available': available,
        'status': _diagnostic_enum(value.get('status', 'unavailable'), DIAGNOSTIC_UI_STATUSES),
        **{key: _diagnostic_bool(value.get(key)) for key in (
            'startDisabled', 'profileDisabled', 'cancelDisabled', 'refreshDisabled')},
        **{key: _diagnostic_int(value.get(key), 0, 64) for key in (
            'meterMax', 'meterValue', 'rowCount', 'stageCount')},
    }


def capture_wait_failure(report, evaluate):
    """An unavailable diagnostic must never replace the original acceptance failure."""
    try:
        value = evaluate(UI_DIAGNOSTIC, 'batch-completion-wait-failure-ui')
    except Exception:
        value = None
    report['batch_png_wait_failure_ui'] = ui_diagnostic(value)


def _require(value, code):
    if not value:
        raise AssertionError(code)


def _ordinary_directory(value):
    path = Path(value)
    _require(path.is_absolute() and path.is_dir() and not path.is_symlink()
             and not path.is_junction(), 'batch_evidence_directory_unavailable')
    return path


def _ordinary_child(root, value, *, directory=False):
    root = _ordinary_directory(root)
    path = Path(value)
    _require(path.is_absolute() and path.parent == root and not path.is_symlink()
             and not path.is_junction(), 'batch_evidence_path_outside_scope')
    _require(path.is_dir() if directory else path.is_file(), 'batch_evidence_target_unavailable')
    _require(path.resolve(strict=True).parent == root.resolve(strict=True), 'batch_evidence_path_redirected')
    return path


def _read_owned(root, path, limit):
    path = _ordinary_child(root, path)
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    _require(0 < len(data) <= limit, 'batch_evidence_file_limit')
    return data


def _write_fresh(root, path, data):
    root = _ordinary_directory(root)
    path = Path(path)
    _require(path.is_absolute() and path.parent == root and not path.exists()
             and not path.is_symlink() and not path.is_junction()
             and type(data) is bytes and 0 < len(data) <= MAX_SOURCE_BYTES,
             'batch_fixture_target_not_fresh')
    with path.open('xb') as stream:
        stream.write(data)
    _require(_read_owned(root, path, MAX_SOURCE_BYTES) == data, 'batch_fixture_write_readback_failed')


def assert_native_selection(mode, actual, expected, root):
    """Validate real native return values, independently of typed-field receipts."""
    _require(mode in ('open-multiple', 'folder') and type(actual) in (tuple, list)
             and type(expected) in (tuple, list), 'batch_native_selection_shape')
    length = 2 if mode == 'open-multiple' else 1
    _require(len(actual) == len(expected) == length and all(type(value) is str for value in actual),
             'batch_native_selection_count_or_type')
    selected = [_ordinary_child(root, value, directory=mode == 'folder').resolve(strict=True) for value in actual]
    targets = [_ordinary_child(root, value, directory=mode == 'folder').resolve(strict=True) for value in expected]
    _require(len(set(selected)) == length and set(selected) == set(targets), 'batch_native_selection_mismatch')
    return [Path(value).name for value in actual]


class NativeDialogObserver:
    """Two finite fixture selections around unchanged real window dialog calls."""
    def __init__(self, window, choose, *, root, inputs, folder, open_kind, folder_kind, timeout):
        self.window, self.choose = window, choose
        self.root, self.inputs, self.folder = Path(root), tuple(inputs), Path(folder)
        self.open_kind, self.folder_kind, self.timeout = open_kind, folder_kind, timeout
        self.original = window.create_file_dialog
        self.records = []
        self._fixtures = []
        self._record_lock = threading.RLock()
        self.count = 0
        self.attached = False

        def observed(*args, **kwargs):
            with self._record_lock:
                index = self.count
                self.count += 1
            _require(len(args) == 1 and index in (0, 1), 'batch_native_dialog_sequence')
            if index == 0:
                _require(type(args[0]) is type(self.open_kind) and args[0] == self.open_kind
                         and kwargs.get('allow_multiple') is True
                         and kwargs == {'allow_multiple': True, 'file_types': INPUT_FILTER},
                         'batch_native_open_arguments_changed')
                mode, target, expected = 'open-multiple', self.inputs, self.inputs
            else:
                _require(type(args[0]) is type(self.folder_kind) and args[0] == self.folder_kind and not kwargs,
                         'batch_native_folder_arguments_changed')
                mode, target, expected = 'folder', self.folder, (self.folder,)
            fixture = self.choose(target, mode=mode, timeout=self.timeout(), parent_io=True)
            record = {'mode': mode, 'originalCalled': True, 'originalReturned': False}
            with self._record_lock:
                self.records.append(record)
                self._fixtures.append(fixture)

            def mark(**values):
                with self._record_lock:
                    record.update(values)

            def diagnostic(value):
                from .native_dialog_fixture import NativeFileChoice
                binding = (NativeFileChoice.observation_identity(fixture)
                           if type(fixture) is NativeFileChoice else None)
                return dialog_diagnostic(value, observation_binding=binding)

            def finish():
                try:
                    selection = fixture.finish()
                except Exception as error:
                    try:
                        value = getattr(error, 'dialog_result', None)
                    except Exception:
                        value = None
                    mark(fixtureFinishFailed=True, fixtureFailure=diagnostic(value))
                    raise
                mark(fixture=diagnostic(selection))
                _require(type(selection) is dict and selection.get('selected') is True,
                         'batch_native_dialog_fixture_unconfirmed')

            try:
                result = self.original(*args, **kwargs)
                mark(originalReturned=True)
            except Exception:
                mark(originalCallFailed=True)
                try:
                    finish()
                except Exception:
                    pass  # Preserve the original native-call exception; finish facts are already recorded.
                raise
            else:
                try:
                    finish()
                except Exception:
                    # Observe the real return without changing fixture-failure
                    # propagation or the existing actualReturnVerified gate.
                    matches = False
                    try:
                        assert_native_selection(mode, result, expected, self.root)
                        matches = True
                    except Exception:
                        pass
                    mark(originalReturnAfterFixtureFailure={
                        'matchesExpectedSelection': matches,
                        'emptySelection': result is None or (type(result) in (tuple, list) and len(result) == 0),
                    })
                    raise
            names = assert_native_selection(mode, result, expected, self.root)
            mark(returnedNames=names, actualReturnVerified=True)
            return result

        self.observed = observed

    def attach(self):
        _require(not self.attached, 'batch_dialog_observer_already_attached')
        self.window.create_file_dialog = self.observed
        self.attached = True

    def close(self):
        if self.attached:
            _require(self.window.create_file_dialog is self.observed, 'batch_dialog_observer_changed')
            self.window.create_file_dialog = self.original
            self.attached = False

    def assert_complete(self):
        _require(self.count == len(self.records) == 2
                 and [value['mode'] for value in self.records] == ['open-multiple', 'folder']
                 and all(value.get('originalCalled') is True and value.get('originalReturned') is True
                         and value.get('actualReturnVerified') is True for value in self.records),
                 'batch_native_dialog_returns_incomplete')

    def snapshot(self):
        # All records are authored above from fixed modes/booleans, sanitized
        # receipts, and names verified against exact owned synthetic targets.
        with self._record_lock:
            records = copy.deepcopy(self.records[:2])
            fixtures = tuple(self._fixtures[:2])
        for record, fixture in zip(records, fixtures):
            if record.get('originalReturned') is False:
                try:
                    pending = dialog_diagnostic(getattr(fixture, 'result', None))
                except Exception:
                    pending = dialog_diagnostic(None)
                record['resultAtSnapshot'] = pending
                # Pending native calls can outlast the fixture's result writer.
                # Only the exact fixture type may provide the independent packet.
                from .native_dialog_fixture import NativeFileChoice
                record['childProgressAtSnapshot'] = progress_diagnostic(
                    NativeFileChoice.progress_snapshot(fixture) if type(fixture) is NativeFileChoice else None)
            if 'fixture' not in record and 'fixtureFailure' not in record:
                # A returned chooser may still be awaiting fixture completion.
                # Keep this independent packet separate from child progress.
                from .native_dialog_fixture import NativeFileChoice
                record['parentIoAtSnapshot'] = parent_io_diagnostic(
                    NativeFileChoice.parent_io_snapshot(fixture) if type(fixture) is NativeFileChoice else None)
        return records


def retain_dialog_records(report, observer, *, failure_active):
    if observer is None:
        return
    report['batch_png_dialogs'] = observer.snapshot()
    try:
        observer.close()
    except Exception:
        report['batch_png_dialog_observer_cleanup'] = {'restored': False}
        if not failure_active:
            raise
    else:
        report['batch_png_dialog_observer_cleanup'] = {'restored': True}


def _json_object(data):
    def pairs(values):
        result = {}
        for key, value in values:
            _require(key not in result, 'batch_manifest_duplicate_field')
            result[key] = value
        return result
    def invalid_constant(value):
        raise AssertionError('batch_manifest_nonfinite_value')
    _require(type(data) is bytes and 0 < len(data) <= MAX_MANIFEST_BYTES, 'batch_manifest_byte_limit')
    try:
        result = json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (UnicodeError, ValueError) as error:
        raise AssertionError('batch_manifest_invalid_json') from error
    _require(type(result) is dict, 'batch_manifest_object_required')
    return result


def _no_paths(value, root):
    if type(value) is dict:
        return all(_no_paths(key, root) and _no_paths(child, root) for key, child in value.items())
    if type(value) is list:
        return all(_no_paths(child, root) for child in value)
    if type(value) is str:
        return not (str(root).casefold() in value.casefold() or value.startswith('/')
                    or re.search(r'[A-Za-z]:[\\/]|\\\\', value))
    return True


def _json_equal(left, right):
    try:
        return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)
    except (ValueError, TypeError):
        return False


def _settings_equal(left, right):
    # Browser JSON may serialize an integral float as an integer. Preserve
    # numeric values while refusing Python's True == 1 equivalence.
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left is right
    if type(left) is dict and type(right) is dict:
        return set(left) == set(right) and all(_settings_equal(left[key], right[key]) for key in left)
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    return type(left) is type(right) and left == right


def validate_receipt(receipt, manifest, settings, source_bytes, root):
    from .import_protocol import checked_batch_settings
    keys = {'sessionId', 'status', 'settings', 'items', 'manifestName', 'manifestError'}
    _require(type(receipt) is dict and set(receipt) == keys
             and type(manifest) is dict and set(manifest) == keys | {'schema'}
             and manifest['schema'] == 'nivram-batch-export/1'
             and _json_equal(manifest, {'schema': 'nivram-batch-export/1', **receipt}), 'batch_manifest_receipt_mismatch')
    try:
        checked_batch_settings(receipt['settings'])
    except (ValueError, TypeError, KeyError, OverflowError):
        raise AssertionError('batch_settings_schema_invalid') from None
    _require(type(receipt['sessionId']) is str and re.fullmatch('[0-9a-f]{32}', receipt['sessionId'])
             and receipt['status'] == 'completed' and receipt['manifestError'] is None
             and receipt['manifestName'] == MANIFEST_NAME and _settings_equal(receipt['settings'], settings)
             and _no_paths(manifest, root), 'batch_terminal_settings_or_manifest_invalid')
    _require(type(source_bytes) is dict and set(source_bytes) == {VALID_NAME, INVALID_NAME}
             and all(type(data) is bytes and 0 < len(data) <= MAX_SOURCE_BYTES for data in source_bytes.values()),
             'batch_input_identity_invalid')
    items = receipt['items']
    item_keys = {'id', 'name', 'extension', 'status', 'sourceSha256', 'outputName', 'outputSha256', 'dimensions', 'error'}
    _require(type(items) is list and len(items) == 2
             and all(type(item) is dict and set(item) == item_keys for item in items), 'batch_item_receipt_shape')
    _require(all(type(item['id']) is str and re.fullmatch('[0-9a-f]{32}', item['id'])
                 and type(item['name']) is str for item in items)
             and len({item['id'] for item in items}) == 2
             and {item['name'] for item in items} == set(source_bytes), 'batch_item_identity_invalid')
    by_name = {item['name']: item for item in items}
    for name, item in by_name.items():
        _require(item['extension'] == Path(name).suffix
                 and item['sourceSha256'] == hashlib.sha256(source_bytes[name]).hexdigest(), 'batch_source_hash_mismatch')
    good, bad = by_name[VALID_NAME], by_name[INVALID_NAME]
    dimensions = good['dimensions']
    _require(good['status'] == 'exported' and good['outputName'] == OUTPUT_NAME and good['error'] is None
             and type(good['outputSha256']) is str and re.fullmatch('[0-9a-f]{64}', good['outputSha256'])
             and type(dimensions) is dict and set(dimensions) == {'width', 'height', 'padding'}
             and type(dimensions['width']) is int and dimensions['width'] == 640
             and type(dimensions['height']) is int and 24 < dimensions['height'] <= 4096
             and type(dimensions['padding']) is int and dimensions['padding'] == 12, 'batch_success_receipt_invalid')
    _require(bad['status'] == 'failed' and bad['error'] == {'code': 'invalid_input', 'message': INVALID_MESSAGE}
             and bad['outputName'] is None and bad['outputSha256'] is None and bad['dimensions'] is None,
             'batch_invalid_input_published_or_misreported')
    return good


def validate_artifacts(fixtures, output, receipt, settings, source_bytes, before, after):
    fixtures = _ordinary_directory(fixtures)
    output = _ordinary_child(fixtures, output, directory=True)
    manifest_data = _read_owned(output, output / MANIFEST_NAME, MAX_MANIFEST_BYTES)
    manifest = _json_object(manifest_data)
    good = validate_receipt(receipt, manifest, settings, source_bytes, fixtures)
    png_data = _read_owned(output, output / good['outputName'], MAX_PNG_BYTES)
    metadata, pixels = validate_png_bytes(png_data)
    _require(good['outputSha256'] == metadata['sha256']
             and good['dimensions'] == {key: metadata[key] for key in ('width', 'height', 'padding')},
             'batch_png_disk_identity_mismatch')
    _require(_read_owned(output, output / SENTINEL_NAME, MAX_SOURCE_BYTES) == SENTINEL_BYTES,
             'batch_existing_output_changed')
    for name, expected in source_bytes.items():
        _require(_read_owned(fixtures, fixtures / name, MAX_SOURCE_BYTES) == expected, 'batch_source_bytes_changed')
    entries = list(output.iterdir())
    _require(len(entries) == 3 and {entry.name for entry in entries} == {MANIFEST_NAME, SENTINEL_NAME, OUTPUT_NAME}
             and all(entry.is_file() and not entry.is_symlink() and not entry.is_junction() for entry in entries),
             'batch_output_set_or_temporary_files_invalid')
    assert_preserved(before, after)
    return {'manifestName': MANIFEST_NAME, 'manifestSha256': hashlib.sha256(manifest_data).hexdigest(),
            'outputName': OUTPUT_NAME, 'sourceHashes': {name: hashlib.sha256(data).hexdigest() for name, data in source_bytes.items()},
            'collisionSentinelSha256': hashlib.sha256(SENTINEL_BYTES).hexdigest(), **metadata}, pixels


PROGRESS_ARM = """(() => {
    const details=document.getElementById('batch-export-controls');
    const state={sawChoosing:false,sawBusy:false,sawProcessing:false,events:0};
    const observe=()=>{state.events=Math.min(128,state.events+1);
        const start=document.getElementById('batch-export-button'), cancel=document.getElementById('batch-export-cancel');
        const status=document.getElementById('batch-export-status').textContent;
        const meter=details.querySelector('progress[aria-label="Batch files processed"]');
        if(start.disabled && !cancel.disabled)state.sawBusy=true;
        if(status==='Choose input files, then an output folder in the native dialogs.')state.sawChoosing=true;
        if(meter.max===2 && /of 2 files processed/.test(status))state.sawProcessing=true;};
    const old=window.__nivramAcceptanceBatch;if(old?.observer)old.observer.disconnect();
    state.observer=new MutationObserver(observe);state.observer.observe(details,
        {subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['disabled','value','max']});
    window.__nivramAcceptanceBatch=state;return true;
})()"""
UI = """(() => {
    const details=document.getElementById('batch-export-controls'),
        refresh=[...details.querySelectorAll('button')].filter(button=>button.textContent==='Refresh profiles'),
        meter=details.querySelector('progress[aria-label="Batch files processed"]'), probe=window.__nivramAcceptanceBatch;
    return {status:document.getElementById('batch-export-status').textContent,
        startDisabled:document.getElementById('batch-export-button').disabled,
        profileDisabled:document.getElementById('batch-export-profile').disabled,
        cancelDisabled:document.getElementById('batch-export-cancel').disabled,
        refreshDisabled:refresh.length===1?refresh[0].disabled:null,
        meterMax:meter.max,meterValue:meter.value,
        rows:[...document.querySelectorAll('#batch-export-results > li')].map(row=>row.textContent),
        stageCount:[...document.querySelectorAll('svg[aria-hidden="true"]')].filter(svg=>svg.style.left==='-100000px'&&svg.style.position==='fixed').length,
        progress:probe?{sawChoosing:probe.sawChoosing,sawBusy:probe.sawBusy,sawProcessing:probe.sawProcessing,events:probe.events}:null};
})()"""
UI_DIAGNOSTIC = """(() => {
    const details=document.getElementById('batch-export-controls'),
        statusNode=document.getElementById('batch-export-status'),
        refresh=details?[...details.querySelectorAll('button')].filter(button=>button.textContent==='Refresh profiles'):[],
        meter=details?.querySelector('progress[aria-label="Batch files processed"]');
    const raw=statusNode?.textContent;
    const status=!statusNode?'unavailable':
        raw==='Choose input files, then an output folder in the native dialogs.'?'choosing':
        raw==='Batch selection cancelled.'?'selection-cancelled':
        raw==='The batch stopped before completion. Saved PNGs and the latest available manifest were retained.'?'stopped':
        /^\\d{1,2} of \\d{1,2} PNG files saved\\./.test(raw)?'complete':
        /^\\d{1,2} of \\d{1,2} files processed(?:[:.])/.test(raw)?'processing':'unrecognized';
    const disabled=id=>{const node=document.getElementById(id);return node?node.disabled:null;};
    return {status,startDisabled:disabled('batch-export-button'),profileDisabled:disabled('batch-export-profile'),
        cancelDisabled:disabled('batch-export-cancel'),refreshDisabled:refresh.length===1?refresh[0].disabled:null,
        meterMax:meter?.max??null,meterValue:meter?.value??null,
        rowCount:document.querySelectorAll('#batch-export-results > li').length,
        stageCount:[...document.querySelectorAll('svg[aria-hidden="true"]')].filter(svg=>svg.style.left==='-100000px'&&svg.style.position==='fixed').length};
})()"""


def validate_ui(value):
    keys = {'status', 'startDisabled', 'profileDisabled', 'cancelDisabled', 'refreshDisabled',
            'meterMax', 'meterValue', 'rows', 'stageCount', 'progress'}
    _require(type(value) is dict and set(value) == keys and value['status'] ==
             f'1 of 2 PNG files saved. Manifest: {MANIFEST_NAME}. Completed files were retained.'
             and value['startDisabled'] is False and value['profileDisabled'] is False
             and value['refreshDisabled'] is False and value['cancelDisabled'] is True
             and value['meterMax'] == 2 and value['meterValue'] == 2
             and type(value['stageCount']) is int and value['stageCount'] == 0, 'batch_terminal_ui_invalid')
    expected_rows = {f'{VALID_NAME}: exported \u2192 {OUTPUT_NAME}', f'{INVALID_NAME}: failed \u2014 {INVALID_MESSAGE}'}
    _require(type(value['rows']) is list and len(value['rows']) == 2 and set(value['rows']) == expected_rows,
             'batch_ui_item_results_invalid')
    progress = value['progress']
    _require(type(progress) is dict and set(progress) == {'sawChoosing', 'sawBusy', 'sawProcessing', 'events'}
             and all(progress[key] is True for key in ('sawChoosing', 'sawBusy', 'sawProcessing'))
             and type(progress['events']) is int and 1 <= progress['events'] <= 128, 'batch_native_progress_unobserved')
    return copy.deepcopy(progress)


def run(*, evaluate, poll, choose, check, stage, report, profile, api, downloads, window):
    from .acceptance_click import click_png_button, NativeClickRejected
    from .profiles import ProfileStore
    from .persistence import validate_project
    import webview
    deadline = time.monotonic() + 120
    def remaining(cap):
        value = min(cap, deadline - time.monotonic())
        if value <= 0:
            raise TimeoutError('The focused native batch scenario exceeded its deadline.')
        return value
    def ev(expression, label):
        return evaluate(expression, timeout=remaining(10), stage=label)
    def wait(expression, cap=20):
        return poll(expression, timeout=remaining(cap))
    def state():
        return ev(STATE, 'batch-document-snapshot')

    profile = _ordinary_directory(profile)
    fixtures = profile / 'synthetic fixtures'
    _require(not fixtures.is_symlink() and not fixtures.is_junction(), 'batch_fixture_root_redirected')
    fixtures.mkdir(exist_ok=True)
    fixtures = _ordinary_child(profile, fixtures, directory=True)
    output = fixtures / 'batch PNG outputs \u00fc'
    _require(not output.exists() and not output.is_symlink() and not output.is_junction(), 'batch_output_folder_not_fresh')
    output.mkdir()
    output = _ordinary_child(fixtures, output, directory=True)
    report['batch_png_scenario'] = {'fixture': SMILES, 'inputs': [VALID_NAME, INVALID_NAME],
        'export': dict(OPTIONS), 'activeExport': dict(ACTIVE_OPTIONS), 'nativeBatchVerified': False,
        'nativeCancellationVerified': False, 'concurrentSettingsFreezeVerified': False,
        'validMolNativeExportVerified': False, 'scope': 'Two real native chooser returns and one profile-selected batch PNG with one invalid MOL; no corpus calibration.'}
    observer = None
    try:
        stage('native-batch-synthetic-import-and-profile')
        ev("""(() => {document.getElementById('import-button').click();document.getElementById('structure-input').value='N[C@H](C)O';
            document.getElementById('load-structure').click();return true;})()""", 'batch-synthetic-import-buttons')
        wait("!document.getElementById('import-dialog').open && " + READY + " && window.nivramDocument.getSnapshot().document.graph.atoms.length===4")
        imported = state()
        ev("""(() => {const $=id=>document.getElementById(id);
            $('export-width').value='640';$('export-width').dispatchEvent(new Event('input'));
            $('margin-mode').value='pixels';$('margin-mode').dispatchEvent(new Event('input'));
            $('margin-value').value='12';$('margin-value').dispatchEvent(new Event('input'));
            $('bond-width').value='4.2';$('hash-bond-style').value='spaced';$('apply-style').click();return true;})()""", 'batch-profile-settings-buttons')
        wait(READY + " && window.nivramDocument.getSnapshot().document.style.hashBondStyle==='spaced'")
        chosen = state()
        _require(chosen['snapshot']['document']['graph'] == imported['snapshot']['document']['graph']
                 and chosen['snapshot']['document']['style']['bondWidth'] == 4.2
                 and chosen['snapshot']['document']['export'] == OPTIONS, 'batch_profile_setup_changed_graph_or_settings')
        project = validate_project(copy.deepcopy(chosen['snapshot']['document']))
        _require(project == chosen['snapshot']['document'], 'batch_synthetic_project_validation_changed_document')
        _semantic_hash_classes(project['graph'])  # Actual synthetic isomer identity; no reference image or appearance tuning.
        source_bytes = {VALID_NAME: json.dumps(project, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8'), INVALID_NAME: INVALID_BYTES}
        for name, data in source_bytes.items():
            _write_fresh(fixtures, fixtures / name, data)
        _write_fresh(output, output / SENTINEL_NAME, SENTINEL_BYTES)
        wait("!!document.getElementById('save-profile-button') && !!document.getElementById('batch-export-button')")
        before_save = state()
        ev("document.querySelector('.profile-controls').open=true;document.getElementById('profile-name').value='Synthetic native batch PNG';document.getElementById('save-profile-button').click();true", 'batch-profile-save-button')
        wait("document.getElementById('profile-choice').options.length===2 && !document.getElementById('apply-profile-button').disabled")
        assert_preserved(before_save, state())
        profile_directory = Path(api._profiles.directory)
        _require(profile_directory.resolve(strict=True).is_relative_to(profile.resolve(strict=True))
                 and not profile_directory.is_symlink() and not profile_directory.is_junction(), 'batch_profile_store_outside_isolation')
        listing = ProfileStore(profile_directory).list()
        _require(listing == api.list_profiles() and listing['invalidRecords'] == 0 and len(listing['profiles']) == 1,
                 'batch_profile_fresh_readback_failed')
        record = listing['profiles'][0]
        _require(record['name'] == PROFILE_NAME and record['revision'] == 1 and record['export'] == OPTIONS
                 and record['style'] == project['style'], 'batch_profile_record_settings_changed')
        ev("""(() => {const $=id=>document.getElementById(id);$('export-width').value='480';$('export-width').dispatchEvent(new Event('input'));
            $('margin-value').value='8';$('margin-value').dispatchEvent(new Event('input'));
            $('batch-export-controls').open=true;const r=[...$('batch-export-controls').querySelectorAll('button')].filter(b=>b.textContent==='Refresh profiles');
            if(r.length!==1)return false;r[0].click();return true;})()""", 'batch-active-settings-and-refresh')
        wait("[...document.getElementById('batch-export-profile').options].some(option=>option.value===" + json.dumps(f"{record['id']}/1") + ")")
        ev("document.getElementById('batch-export-profile').value=" + json.dumps(f"{record['id']}/1") + ";document.getElementById('batch-export-profile').dispatchEvent(new Event('change'));true", 'batch-profile-selection')
        baseline = state()
        _require(baseline['snapshot']['document']['graph'] == project['graph']
                 and baseline['snapshot']['document']['style'] == project['style']
                 and baseline['snapshot']['document']['export'] == ACTIVE_OPTIONS, 'batch_deliberate_active_settings_invalid')
        settings = {'style': copy.deepcopy(record['style']), 'export': copy.deepcopy(record['export']),
                    'profile': {key: record[key] for key in ('id', 'revision', 'name')}}
        check('native-batch-profile-and-synthetic-inputs', profileId=record['id'], revision=1,
              freshDiskReadback=True, profileExport=dict(OPTIONS), activeExport=dict(ACTIVE_OPTIONS),
              syntheticProjectGraphPreserved=True, sourceHashes={name: hashlib.sha256(data).hexdigest() for name, data in source_bytes.items()})
        prior_downloads = downloads.snapshot()
        _require(prior_downloads['last_id'] == prior_downloads['accepted'] == prior_downloads['completed'] == 0,
                 'batch_unexpected_prior_native_download')
        ev(PROGRESS_ARM, 'batch-progress-observer-arm')
        observer = NativeDialogObserver(window, choose, root=fixtures,
            inputs=tuple(fixtures / name for name in source_bytes), folder=output,
            open_kind=webview.FileDialog.OPEN, folder_kind=webview.FileDialog.FOLDER,
            timeout=lambda: remaining(15))
        observer.attach()
        stage('native-batch-two-file-choose-and-export')
        try:
            gesture = click_png_button(window, evaluate, 'batch')
        except NativeClickRejected as error:
            report['batch_png_click_failure'] = {'code': error.code}
            raise
        try:
            wait("!document.getElementById('batch-export-button').disabled && document.getElementById('batch-export-status').textContent.startsWith('1 of 2 PNG files saved.')", cap=40)
        except Exception:
            capture_wait_failure(report, ev)
            raise
        observer.assert_complete()
        observer.close()
        report['batch_png_dialogs'] = observer.snapshot()
        check('native-batch-two-file-open-and-folder-selection', originalCalls=2, actualReturnedPathsVerified=True,
              returnedFileNames=observer.records[0]['returnedNames'], returnedFolderName=observer.records[1]['returnedNames'][0],
              nativeGesture=gesture)
        ui = ev(UI, 'batch-terminal-ui-and-progress')
        progress = validate_ui(ui)
        with api._batch._lock:
            session = api._batch._session
            _require(type(session) is dict and session['status'] == 'completed' and session['active'] is None
                     and all(item['_bytes'] is None and '_preparation' not in item for item in session['items']),
                     'batch_private_session_not_settled')
            receipt = api._batch._receipt(session)
        manifest = _json_object(_read_owned(output, output / MANIFEST_NAME, MAX_MANIFEST_BYTES))
        validate_receipt(receipt, manifest, settings, source_bytes, fixtures)
        check('native-batch-partial-success-and-frozen-manifest', exported=1, failed=1, finalStatus='completed',
              failureCode='invalid_input', exactProfileAndSettings=True, activeSettingsDiffer=True,
              progressObserved=progress, manifestName=MANIFEST_NAME, outputName=OUTPUT_NAME)
        metadata, _ = validate_artifacts(fixtures, output, receipt, settings, source_bytes, baseline, state())
        _require(ProfileStore(profile_directory).list() == listing == api.list_profiles(), 'batch_profile_records_changed')
        final_downloads = downloads.snapshot()
        _require(final_downloads['last_id'] == final_downloads['accepted'] == final_downloads['completed'] == 0
                 and final_downloads['blocked'] == final_downloads['failures'] == final_downloads['active'] == 0,
                 'batch_unexpected_browser_download_activity')
        report['batch_png_output'] = metadata
        report['batch_png_scenario']['nativeBatchVerified'] = True
        check('native-batch-png-and-document-integrity', inputsUnchanged=True, existingOutputUnchanged=True,
              completeEditorStateUnchanged=True, profileRecordsUnchanged=True, sourceBuffersReleased=True,
              noTemporaryStages=True, browserDownloadCount=0, **metadata)
    finally:
        retain_dialog_records(report, observer, failure_active=sys.exc_info()[0] is not None)
        try:
            ev("(() => {const s=window.__nivramAcceptanceBatch;s?.observer?.disconnect();delete window.__nivramAcceptanceBatch;return true;})()", 'batch-progress-observer-cleanup')
        except Exception:
            pass
