"""Bounded native saves of supplied-model results, not predictive validation.

Original JSON/CSV/PNG bytes are retained after validation. The reference saver
owns the dialog, conditional replacement, backup, readback and close protocol.
"""
from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading

from . import reference_export as reference
from .population_jobs import _complete_model
from .populations import evaluate_populations

MAX_BYTES = 32 * 1024 * 1024
MAX_ROWS = 3_000_000
LIMITATION = 'Supplied-model calculation; not a validated molecular prediction.'
_ROOT_KEYS = {'schemaVersion', 'exportKind', 'limitation', 'source', 'selectedSample',
              'visibleStateIds', 'referenceContext', 'populationResults'}
_HASH = re.compile('[a-f0-9]{64}\\Z')


def _reject():
    raise ValueError('Invalid bounded supplied-population export.')


def _exact(value, keys):
    if not isinstance(value, dict) or set(value) != keys:
        _reject()


def _text(value, maximum=8192):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\0' in value:
        _reject()
    value.encode('utf-8', errors='strict')


def _walk(value, depth=0, count=None):
    count = [0] if count is None else count
    count[0] += 1
    if depth > 20 or count[0] > MAX_ROWS:
        _reject()
    if isinstance(value, dict):
        if len(value) > 128:
            _reject()
        for key, item in value.items():
            _text(key, 128)
            _walk(item, depth + 1, count)
    elif isinstance(value, list):
        if len(value) > 4096:
            _reject()
        for item in value:
            _walk(item, depth + 1, count)
    elif isinstance(value, str):
        if len(value) > 8192 or '\0' in value:
            _reject()
        value.encode('utf-8', errors='strict')
    elif type(value) in (int, float):
        if not math.isfinite(value):
            _reject()
    elif value is not None and type(value) is not bool:
        _reject()


def _equivalent(actual, expected, *, approximate=False):
    """Compare JSON values, preserving booleans and allowing JSON numeric syntax."""
    if type(expected) in (int, float):
        return type(actual) in (int, float) and (math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12)
                                                if approximate else actual == expected)
    if isinstance(expected, dict):
        return (isinstance(actual, dict) and set(actual) == set(expected)
                and all(_equivalent(actual[k], v, approximate=approximate) for k, v in expected.items()))
    if isinstance(expected, (tuple, list)):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(_equivalent(a, e, approximate=approximate) for a, e in zip(actual, expected)))
    return type(actual) is type(expected) and actual == expected


def _validate_document(document):
    _walk(document)
    _exact(document, _ROOT_KEYS)
    if (type(document['schemaVersion']) is not int or document['schemaVersion'] != 1
            or document['exportKind'] != 'supplied-population-results' or document['limitation'] != LIMITATION):
        _reject()
    source = document['source']
    _exact(source, {'name', 'modelInputHash'})
    _text(source['name'], 256)
    if not isinstance(source['modelInputHash'], str) or not _HASH.fullmatch(source['modelInputHash']):
        _reject()
    selected = document['selectedSample']
    _exact(selected, {'index', 'ph'})
    if (type(selected['index']) is not int or not 0 <= selected['index'] <= 140
            or type(selected['ph']) not in (int, float) or selected['ph'] != selected['index'] / 10):
        _reject()
    if document['referenceContext'] is not None:
        reference._validate_reference({'schemaVersion': 1, 'exportKind': 'ph-reference-context',
            'selectedSample': selected, 'reference': document['referenceContext'],
            'populationResults': None, 'limitation': reference.LIMITATION})
    envelope = document['populationResults']
    _exact(envelope, {'schemaVersion', 'documentRevision', 'graphHash', 'modelSignature', 'model', 'samples'})
    if (type(envelope['schemaVersion']) is not int or envelope['schemaVersion'] != 1
            or type(envelope['documentRevision']) is not int or not 0 <= envelope['documentRevision'] < 2**53
            or any(not isinstance(envelope[key], str) or not _HASH.fullmatch(envelope[key])
                   for key in ('graphHash', 'modelSignature'))):
        _reject()
    model = _complete_model(json.dumps(envelope['model'], ensure_ascii=False, allow_nan=False))
    state_ids = [state.state_id for state in model.states]
    visible = document['visibleStateIds']
    if (not isinstance(visible, list) or any(not isinstance(item, str) for item in visible)
            or len(set(visible)) != len(visible) or not set(visible).issubset(state_ids)):
        _reject()
    samples = envelope['samples']
    if not isinstance(samples, list) or len(samples) != 141:
        _reject()
    for index, row in enumerate(samples):
        _exact(row, {'index', 'ph', 'status', 'result', 'reason'})
        if type(row['index']) is not int or row['index'] != index or type(row['ph']) not in (int, float) or row['ph'] != index / 10:
            _reject()
        inside = model.conditions.ph_min <= row['ph'] <= model.conditions.ph_max
        if row['status'] != 'computed':
            if row['result'] is not None or row['status'] != ('unavailable' if inside else 'outside_model_domain'):
                _reject()
            _text(row['reason'])
            continue
        if not inside or row['reason'] is not None or not isinstance(row['result'], dict):
            _reject()
        result = row['result']
        omitted, mass_source = result.get('omitted_fraction'), result.get('omitted_mass_source')
        # A complete model with implicit zero omitted mass has no mass source.
        # All other supplied masses must preserve their explicit source.
        implicit_zero = model.ensemble_complete and omitted == 0 and mass_source is None
        expected = evaluate_populations(model, row['ph'],
            omitted_fraction=None if implicit_zero else omitted, omitted_mass_source=mass_source).to_dict()
        _exact(result, set(expected))
        if (not isinstance(result['fractions'], list)
                or any(type(value) not in (int, float) or not 0 <= value <= 1 for value in result['fractions'])):
            _reject()
        if not math.isclose(math.fsum(result['fractions']), 1 - (omitted if omitted is not None else 0),
                            rel_tol=0, abs_tol=1e-10):
            _reject()
        # JavaScript JSON reserialization erases Python's 7.0-versus-7 spelling;
        # check the received signature consistently without inventing its preimage.
        expected['model_signature'] = envelope['modelSignature']
        for key, expected_value in expected.items():
            if key == 'warnings':
                actual = result[key]
                if (not isinstance(actual, list) or not actual
                        or any(not isinstance(item, str) or not item.strip() for item in actual)
                        or not set(expected_value).issubset(actual)):
                    _reject()
            elif not _equivalent(result[key], expected_value,
                                 approximate=key in ('fractions', 'percentages', 'log_fractions')):
                _reject()


def validate_payload(kind, payload, transparent_background=False):
    if (type(transparent_background) is not bool or (kind != 'png' and transparent_background)
            or kind not in ('json', 'csv', 'png') or not isinstance(payload, str)):
        _reject()
    try:
        if kind == 'png':
            if not payload or len(payload) > 4 * ((MAX_BYTES + 2) // 3):
                _reject()
            from .batch_export import _decode_png
            data, dimensions = _decode_png(payload, {'width': 1000, 'marginMode': 'pixels', 'margin': 0, 'transparentBackground': transparent_background}, threading.Event())
            if len(data) > MAX_BYTES or dimensions['height'] != 720:
                _reject()
            return data
        if not payload or len(payload) > MAX_BYTES:
            _reject()
        data = payload.encode('utf-8', errors='strict')
        if len(data) > MAX_BYTES:
            _reject()
        document = (json.loads(payload, object_pairs_hook=reference._pairs, parse_constant=lambda _value: _reject())
                    if kind == 'json' else reference._csv_document(payload, max_rows=MAX_ROWS, max_depth=20, max_items=4096))
        _validate_document(document)
        return data
    except (ValueError, TypeError, OverflowError, RecursionError, csv.Error, KeyError) as error:
        raise ValueError('Invalid bounded supplied-population export.') from error


class PopulationExportSaver(reference.ReferenceExportSaver):
    """Separately owned saver; does not widen the reference export boundary."""
    _basename = 'nivram-supplied-populations'
    _label = 'population'

    def _validate(self, kind, payload, transparent_background=False):
        return validate_payload(kind, payload, transparent_background)

    def _read(self, path):
        with Path(path).open('rb') as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('Population export destination exceeds its bounded size.')
        return data

    def _backup(self, destination, old):
        descriptor, name = tempfile.mkstemp(prefix='.nv-pop-', suffix='.bak', dir=destination.parent)
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
        if self._read(name) != old:
            raise OSError('Backup readback did not match.')

    def _failure(self, status):
        result = reference._failure(status)
        return {**result, 'message': result['message'].replace('reference', 'population')}
