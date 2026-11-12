"""Versioned, in-memory codec for explicitly labeled partial observations.

Parsing and encoding authenticate no host event and acknowledge no write. The
existing terminal receipt /1 APIs remain separate and unchanged. No filesystem,
process, worker or native operation is performed by this codec.
"""
from __future__ import annotations

import json

from .supervision_observations import ParentIoProjection, PRIMARY_BOUNDARY
from .supervision_receipts import TerminalEvidence, TerminalFormatError

SCHEMA = 'nivram-parent-io-observation-envelope/1'
MAX_ENVELOPE_BYTES = 8192
_ORIGINS = frozenset({'synthetic', 'host_reported'})
_STATUSES = frozenset({'valid', 'busy', 'unavailable'})
_PIPE_FIELDS = ('stdin_write_completed', 'stdin_closed', 'stdout_eof', 'stderr_eof')
_EVIDENCE_FIELDS = (
    'attempt_id', 'source_sha256', 'outcome', 'returncode',
    'ownership_confirmed', 'root_exit_confirmed', 'stdin_write_completed',
    'stdin_closed', 'stdout_eof', 'stderr_eof', 'output_drained', 'output_closed',
    'reader_threads_exited', 'cleanup_confirmed', 'remaining_descendants',
    'forced_cleanup',
)
_PROJECTION_FIELDS = ('evidence', 'origin', 'boundary', 'parent_io_status', 'mapped_fields')
_ENVELOPE_FIELDS = ('schema', *_PROJECTION_FIELDS)


class EnvelopeFormatError(ValueError):
    """Invalid observation envelope, without echoing private input."""


def _exact_dict(value, names):
    if (type(value) is not dict or len(value) != len(names)
            or any(type(key) is not str for key in value)
            or set(value) != set(names)):
        raise EnvelopeFormatError('Invalid observation fields.')
    return dict.copy(value)


def _record(value, kind, names):
    if type(value) is not kind:
        raise EnvelopeFormatError('Invalid observation record type.')
    try:
        attributes = object.__getattribute__(value, '__dict__')
    except AttributeError:
        raise EnvelopeFormatError('Invalid observation record.') from None
    # Do not invoke instance validators, asdict/deepcopy or arbitrary properties.
    # Frozen dataclasses are public and can contain forged or extra attributes.
    return _exact_dict(attributes, names)


def _labels(origin, boundary, status=None):
    if type(origin) is not str or len(origin) > 13 or origin not in _ORIGINS:
        raise EnvelopeFormatError('Invalid observation origin.')
    if (type(boundary) is not str or len(boundary) != len(PRIMARY_BOUNDARY)
            or boundary != PRIMARY_BOUNDARY):
        raise EnvelopeFormatError('Invalid observation boundary.')
    if status is not None and (type(status) is not str or len(status) > 11
                               or status not in _STATUSES):
        raise EnvelopeFormatError('Invalid observation status.')


def _expected(attempt_id, source_sha256, origin, boundary):
    _labels(origin, boundary)
    try:
        TerminalEvidence(attempt_id, source_sha256, 'error')
    except TerminalFormatError:
        raise EnvelopeFormatError('Invalid expected observation identity.') from None


def _mapped(value, kind):
    if (type(value) is not kind or len(value) > len(_PIPE_FIELDS)
            or any(type(name) is not str or len(name) > 21
                   or name not in _PIPE_FIELDS for name in value)):
        raise EnvelopeFormatError('Invalid mapped fields.')
    return tuple(value)


def _validated_parts(evidence_values, origin, boundary, status, mapped_fields):
    _labels(origin, boundary, status)
    # None is allowed only when checking expected labels, never as packet status.
    if status is None:
        raise EnvelopeFormatError('Invalid observation status.')
    data = _exact_dict(evidence_values, _EVIDENCE_FIELDS)
    for name in _PIPE_FIELDS:
        if data[name] is not None and data[name] is not True:
            raise EnvelopeFormatError('Invalid partial pipe observation.')
    for name in _EVIDENCE_FIELDS:
        if name not in _PIPE_FIELDS and name not in ('attempt_id', 'source_sha256', 'outcome'):
            if data[name] is not None:
                raise EnvelopeFormatError('Unsupported terminal observation.')
    if type(data['outcome']) is not str or len(data['outcome']) > 11:
        raise EnvelopeFormatError('Invalid declared outcome.')
    try:
        evidence = TerminalEvidence(**data)
    except TerminalFormatError:
        raise EnvelopeFormatError('Invalid terminal observation.') from None
    # Ensure a changed future evidence class cannot silently extend schema /1.
    _record(evidence, TerminalEvidence, _EVIDENCE_FIELDS)
    mapped = _mapped(mapped_fields, tuple)
    expected = tuple(name for name in _PIPE_FIELDS if data[name] is True)
    if mapped != expected or (status != 'valid' and mapped):
        raise EnvelopeFormatError('Inconsistent observation mapping.')
    projection = ParentIoProjection(evidence, origin, boundary, status, mapped)
    payload = {'schema': SCHEMA, 'origin': origin, 'boundary': boundary,
               'parent_io_status': status, 'mapped_fields': list(mapped), 'evidence': data}
    return projection, payload


def encode_observation(projection: ParentIoProjection) -> bytes:
    """Validate untrusted constructed records and encode labels without loss.

    Only the v007 partial projection contract is accepted; this does not accept
    bare terminal evidence, complete receipts, arbitrary dataclass subclasses
    or extra instance attributes. No persistence is attempted or acknowledged.
    """
    values = _record(projection, ParentIoProjection, _PROJECTION_FIELDS)
    evidence = _record(values['evidence'], TerminalEvidence, _EVIDENCE_FIELDS)
    _, payload = _validated_parts(evidence, values['origin'], values['boundary'],
                                   values['parent_io_status'], values['mapped_fields'])
    data = (json.dumps(payload, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True, allow_nan=False) + '\n').encode('utf-8')
    if len(data) > MAX_ENVELOPE_BYTES:
        raise EnvelopeFormatError('Observation envelope exceeds its byte limit.')
    return data


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EnvelopeFormatError('Duplicate observation field.')
        result[key] = value
    return result


def _reject_number(_):
    # This partial schema contains strings, booleans, nulls and field-name lists;
    # even a numeric return code or descendant count would upgrade its evidence.
    raise EnvelopeFormatError('Numeric observation values are unsupported.')


def decode_observation(data: bytes, *, expected_attempt_id: str,
                       expected_source_sha256: str, expected_origin: str,
                       expected_boundary: str) -> ParentIoProjection:
    """Consume a bounded labeled envelope; never report receipt or host success.

    Expected labels are caller constraints, not proof of the producer's identity.
    Decoding preserves synthetic/host_reported as declarations. It establishes
    neither host occurrence, capture freshness, write acknowledgement nor cleanup.
    """
    _expected(expected_attempt_id, expected_source_sha256, expected_origin, expected_boundary)
    if type(data) is not bytes or not 0 < len(data) <= MAX_ENVELOPE_BYTES:
        raise EnvelopeFormatError('Invalid observation byte length.')
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object,
                           parse_int=_reject_number, parse_float=_reject_number,
                           parse_constant=_reject_number)
    except (UnicodeError, ValueError, RecursionError):
        raise EnvelopeFormatError('Invalid observation document.') from None
    values = _exact_dict(value, _ENVELOPE_FIELDS)
    if type(values['schema']) is not str or values['schema'] != SCHEMA:
        raise EnvelopeFormatError('Unsupported observation schema.')
    mapped = _mapped(values['mapped_fields'], list)
    projection, _ = _validated_parts(values['evidence'], values['origin'], values['boundary'],
                                      values['parent_io_status'], mapped)
    if (projection.evidence.attempt_id != expected_attempt_id
            or projection.evidence.source_sha256 != expected_source_sha256
            or projection.origin != expected_origin
            or projection.boundary != expected_boundary):
        raise EnvelopeFormatError('Observation bindings do not match expectations.')
    return projection
