"""Bounded, dialog-scoped pH reference exports; never a project save operation.

Text bytes are preserved exactly after format validation. Existing destinations
use the same conditional Windows writer as projects, with a unique durable copy
of the old bytes. No browser download, arbitrary path, retry, or unsafe fallback
is exposed. Importing this module performs no native I/O.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading

from . import conditional_save as conditional
from .conditional_save import conditional_write_existing
from .persistence import atomic_write

MAX_BYTES = 4_000_000
LIMITATION = 'Reference contexts and unverified comparisons only; not molecular population results.'
_MESSAGES = {
    'failed': 'The reference export could not be saved. Reopen the reference view and try again.',
    'conflict': 'The destination changed or is unavailable. Choose a different filename.',
    'unsupported': 'Protected replacement is unavailable for this destination. Choose a new filename on a local drive.',
    'unconfirmed': 'The export may have been written, but saving could not be confirmed. Inspect the destination before saving again.',
}
_ROOT_KEYS = {'schemaVersion', 'exportKind', 'selectedSample', 'reference', 'populationResults', 'limitation'}
_REFERENCE_KEYS = {'schemaVersion', 'datasetVersion', 'purpose', 'reviewedDate', 'interpretationRules',
                   'context', 'comparison', 'sources', 'overlays', 'gap'}
_FORMULA = re.compile(r'^[\s\ufeff]*[=+@-]|^[\t\r\n]')
_NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z')


def _reject():
    raise ValueError('Invalid bounded reference export.')


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _reject()
        result[key] = value
    return result


def _walk(value, depth=0, count=None):
    count = [0] if count is None else count
    count[0] += 1
    if depth > 16 or count[0] > 10_000:
        _reject()
    if isinstance(value, dict):
        if len(value) > 128:
            _reject()
        for key, child in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                _reject()
            _walk(child, depth + 1, count)
    elif isinstance(value, list):
        if len(value) > 100:
            _reject()
        for child in value:
            _walk(child, depth + 1, count)
    elif isinstance(value, str):
        if len(value) > 16_384 or '\0' in value:
            _reject()
        value.encode('utf-8', errors='strict')
    elif type(value) in (int, float):
        if not math.isfinite(value):
            _reject()
    elif value is not None and type(value) is not bool:
        _reject()


def _validate_reference(value):
    """Validate the reference transport, not a new scientific prediction model."""
    _walk(value)
    if (not isinstance(value, dict) or set(value) != _ROOT_KEYS
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['exportKind'] != 'ph-reference-context' or value['populationResults'] is not None
            or value['limitation'] != LIMITATION):
        _reject()
    sample = value['selectedSample']
    if (not isinstance(sample, dict) or set(sample) != {'index', 'ph'}
            or type(sample['index']) is not int or not 0 <= sample['index'] <= 140
            or type(sample['ph']) not in (int, float) or sample['ph'] != sample['index'] / 10):
        _reject()
    reference = value['reference']
    if (not isinstance(reference, dict) or set(reference) != _REFERENCE_KEYS
            or type(reference['schemaVersion']) is not int or reference['schemaVersion'] != 1):
        _reject()
    for key in ('datasetVersion', 'purpose', 'reviewedDate'):
        if not isinstance(reference[key], str) or not reference[key].strip():
            _reject()
    rules = reference['interpretationRules']
    if not isinstance(rules, list) or not rules or any(not isinstance(x, str) or not x.strip() for x in rules):
        _reject()
    for key in ('sources', 'overlays'):
        if not isinstance(reference[key], list) or any(not isinstance(x, dict) for x in reference[key]):
            _reject()
    for key in ('context', 'comparison', 'gap'):
        if reference[key] is not None and not isinstance(reference[key], dict):
            _reject()
    comparison = reference['comparison']
    if comparison is not None and (reference['context'] is None
            or comparison.get('source_kind') != 'user_entered' or comparison.get('evidence_status') != 'unverified'
            or comparison.get('statistic') != 'user_defined_comparison'):
        _reject()
    for overlay in reference['overlays']:
        if overlay.get('joinTrajectory') is not False:
            _reject()


def _csv_document(payload, *, max_rows=2_000, max_depth=16, max_items=100):
    """Recover only the bounded typed scalar table emitted by referenceCsv."""
    reader = csv.reader(io.StringIO(payload, newline=''), strict=True)
    if next(reader, None) != ['path', 'type', 'value', 'spreadsheet_escaped']:
        _reject()
    document, leaves = {}, set()
    for number, row in enumerate(reader, 1):
        if number > max_rows or len(row) != 4:
            _reject()
        path, kind, raw, escaped = row
        if not path.startswith('/') or len(path) > 2048 or escaped not in ('true', 'false'):
            _reject()
        if kind == 'string':
            if escaped == 'true':
                if not raw.startswith("'") or not _FORMULA.search(raw[1:]):
                    _reject()
                value = raw[1:]
            else:
                if _FORMULA.search(raw):
                    _reject()
                value = raw
        else:
            if escaped != 'false':
                _reject()
            if kind == 'number' and _NUMBER.fullmatch(raw):
                value = json.loads(raw)
            elif (kind, raw) in (('null', 'null'), ('boolean', 'true'), ('boolean', 'false'),
                                  ('array', '[]'), ('object', '{}')):
                value = json.loads(raw)
            else:
                _reject()
        encoded_parts = path[1:].split('/')
        if len(encoded_parts) > max_depth or any(not x or re.search(r'~(?![01])', x) for x in encoded_parts):
            _reject()
        parts = [x.replace('~1', '/').replace('~0', '~') for x in encoded_parts]
        current = document
        for index, key in enumerate(parts):
            prefix = tuple(parts[:index + 1])
            final = index == len(parts) - 1
            if prefix in leaves:
                _reject()
            if isinstance(current, list):
                if not re.fullmatch(r'0|[1-9][0-9]*', key):
                    _reject()
                key = int(key)
                if key > len(current) or key >= max_items:
                    _reject()
                exists = key < len(current)
            else:
                exists = key in current
            if final:
                if exists:
                    _reject()
                child = value
                leaves.add(prefix)
            elif exists:
                child = current[key]
                if not isinstance(child, (dict, list)):
                    _reject()
            else:
                child = [] if re.fullmatch(r'0|[1-9][0-9]*', parts[index + 1]) else {}
            if isinstance(current, list) and not exists:
                current.append(child)
            else:
                current[key] = child
            current = child
    return document


def validate_payload(kind, payload, transparent_background=False):
    if (type(transparent_background) is not bool or (kind != 'png' and transparent_background)
            or kind not in ('json', 'csv', 'png') or not isinstance(payload, str)):
        _reject()
    if kind == 'png':
        if not payload or len(payload) > 4 * ((MAX_BYTES + 2) // 3):
            _reject()
        try:
            data = base64.b64decode(payload, validate=True)
            if len(data) > MAX_BYTES:
                _reject()
            from .batch_export import _decode_png
            checked, dimensions = _decode_png(payload, {'width': 1000, 'marginMode': 'pixels', 'margin': 0, 'transparentBackground': transparent_background}, threading.Event())
            if dimensions['height'] != 560 or checked != data:
                _reject()
            return data
        except Exception as error:
            raise ValueError('Invalid bounded reference PNG.') from error
    if not payload or len(payload) > MAX_BYTES:
        _reject()
    try:
        data = payload.encode('utf-8', errors='strict')
        if len(data) > MAX_BYTES:
            _reject()
        document = (json.loads(payload, object_pairs_hook=_pairs, parse_constant=lambda _value: _reject())
                    if kind == 'json' else _csv_document(payload))
        _validate_reference(document)
        return data
    except (ValueError, TypeError, OverflowError, RecursionError, csv.Error) as error:
        raise ValueError('Invalid bounded reference text.') from error


def _read_bounded(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Reference destination exceeds its bounded size.')
    return data


def _failure(status):
    return {'status': status, 'message': _MESSAGES[status]}


def _retain_backup(destination, old):
    # This fresh, exclusively created short name cannot replace an unrelated .bak.
    # Retain it on failure too: a partially written backup never permits commit.
    descriptor, name = tempfile.mkstemp(prefix='.nv-ref-', suffix='.bak', dir=destination.parent)
    try:
        stream = os.fdopen(descriptor, 'wb')
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        if stream.write(old) != len(old):
            raise OSError('Incomplete backup write.')
        stream.flush()
        os.fsync(stream.fileno())
    if _read_bounded(name) != old:
        raise OSError('Backup readback did not match.')


class ReferenceExportSaver:
    """One active user-dialog operation, with an owned bounded shutdown wait."""
    _basename = 'nivram-ph-reference'
    _label = 'reference'

    def _validate(self, kind, payload, transparent_background=False):
        return validate_payload(kind, payload, transparent_background)

    def _read(self, path):
        return _read_bounded(path)

    def _backup(self, destination, old):
        return _retain_backup(destination, old)

    def _failure(self, status):
        return _failure(status)

    def __init__(self):
        self._lock = threading.Lock()
        self._closed = False
        self._active = False
        self._done = threading.Event()
        self._done.set()

    def _is_closed(self):
        with self._lock:
            return self._closed

    def close(self):
        with self._lock:
            self._closed = True
        if not self._done.wait(15):
            raise RuntimeError(f'{self._label.capitalize()} export shutdown is incomplete.')
        return {'closed': True, 'active': 0}

    def save(self, window, kind, payload, session_token, expected_token, transparent_background=False):
        if (not isinstance(session_token, str) or not isinstance(expected_token, str)
                or not 1 <= len(session_token) <= 128 or not 1 <= len(expected_token) <= 128
                or not session_token.isascii() or not expected_token.isascii()
                or not hmac.compare_digest(session_token, expected_token)):
            return self._failure('failed')
        with self._lock:
            if self._closed or self._active:
                return self._failure('failed')
            self._active = True
            self._done.clear()
        try:
            data = self._validate(kind, payload, transparent_background)
            if self._is_closed():
                return {'status': 'cancelled'}
            import webview
            paths = window.create_file_dialog(webview.FileDialog.SAVE,
                save_filename=self._basename + '.' + kind,
                file_types=(f'{kind.upper()} {self._label} (*.{kind})',))
            if not paths or self._is_closed():
                return {'status': 'cancelled'}
            if isinstance(paths, str):
                selected = paths
            elif isinstance(paths, (tuple, list)) and len(paths) == 1 and isinstance(paths[0], str):
                selected = paths[0]
            else:
                return self._failure('failed')
            destination = Path(selected)
            if not selected or not destination.is_absolute():
                return self._failure('failed')
            if destination.suffix.lower() != '.' + kind:
                destination = destination.with_name(destination.name + '.' + kind)
            try:
                # Reject device/pipe/ADS/UNC and normalization aliases before any
                # pathname read, including when the destination does not exist.
                conditional._path(destination)
            except conditional.SaveUnsupported:
                return self._failure('unsupported')
            try:
                expected = hashlib.sha256(self._read(destination)).hexdigest()
            except FileNotFoundError:
                expected = None
            except ValueError:
                return self._failure('unsupported')
            if expected is not None and not window.create_confirmation_dialog(
                    f'Replace {self._label} export?', f'Replace {destination.name}? A separate copy of the previous file will be retained.'):
                return {'status': 'cancelled'}
            if self._is_closed():
                return {'status': 'cancelled'}
            return self._publish(destination, data, expected)
        except Exception:
            # Never pass arbitrary payloads, paths or provider exception text into
            # pywebview's traceback-returning exception bridge.
            return self._failure('failed')
        finally:
            with self._lock:
                self._active = False
                self._done.set()

    def _publish(self, destination, data, expected):
        digest = hashlib.sha256(data).hexdigest()
        if expected is None:
            try:
                atomic_write(destination, data, exclusive=True)
            except FileExistsError:
                return self._failure('conflict')
            except Exception:
                return self._failure('unconfirmed')
        else:
            try:
                committed = conditional_write_existing(destination, data, expected,
                    lambda old: self._backup(destination, old))
            except conditional.SaveError as error:
                if (isinstance(error, conditional.SaveOutcomeUnknown) or error.outcome != 'not_committed'
                        or error.cleanup_errors or error.operation == 'CloseHandle'):
                    return self._failure('unconfirmed')
                if isinstance(error, conditional.SaveConflict):
                    return self._failure('conflict')
                if isinstance(error, conditional.SaveUnsupported):
                    return self._failure('unsupported')
                return self._failure('failed')
            except Exception:
                return self._failure('unconfirmed')
            if committed != digest:
                return self._failure('unconfirmed')
        try:
            if self._read(destination) != data:
                return self._failure('unconfirmed')
        except Exception:
            return self._failure('unconfirmed')
        return {'status': 'saved', 'bytes': len(data), 'sha256': digest}
