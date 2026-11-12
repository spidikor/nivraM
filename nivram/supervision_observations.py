"""Pure projection of explicitly bound parent-I/O observations.

This newly authored adapter performs no observation collection, process control,
thread operation or persistence. Caller labels and bindings are declarations,
not authentication of host events or proof of a frozen/atomic boundary.
"""
from __future__ import annotations

from dataclasses import dataclass

from .parent_io import parent_io_diagnostic
from .supervision_receipts import TerminalEvidence, TerminalFormatError

PRIMARY_BOUNDARY = 'primary_pre_cleanup'
_ORIGINS = frozenset({'synthetic', 'host_reported'})
_PACKET_FIELDS = (
    'available', 'status', 'writer_expected', 'stdout_limit',
    'stdout_retained_bytes', 'stdout_reader_entered', 'stderr_reader_entered',
    'stdout_eof', 'stderr_eof', 'stdin_writer_entered',
    'stdin_write_completed', 'stdin_close_completed',
)
_PIPE_MAP = (
    ('stdin_write_completed', 'stdin_write_completed'),
    ('stdin_close_completed', 'stdin_closed'),
    ('stdout_eof', 'stdout_eof'),
    ('stderr_eof', 'stderr_eof'),
)


class ObservationFormatError(ValueError):
    """Invalid or differently bound scalar observation; no private input text."""


def _identity(attempt_id, source_sha256):
    try:
        TerminalEvidence(attempt_id, source_sha256, 'error')
    except TerminalFormatError:
        raise ObservationFormatError('Invalid observation identity.') from None


def _packet_values(packet):
    if (type(packet) is not dict or len(packet) != len(_PACKET_FIELDS)
            or any(type(key) is not str for key in packet)
            or set(packet) != set(_PACKET_FIELDS)):
        raise ObservationFormatError('Invalid parent-I/O packet.')
    # Exact dict/scalar validation avoids user-supplied mapping and value hooks.
    packet = dict.copy(packet)
    clean = parent_io_diagnostic(packet)
    if clean['status'] == 'unavailable':
        # The producer sanitizer also returns unavailable for malformed input.
        # Only an explicit canonical unavailable packet is accepted here.
        if (packet['available'] is not False or type(packet['status']) is not str
                or packet['status'] != 'unavailable'
                or any(packet[key] is not None for key in _PACKET_FIELDS
                       if key not in ('available', 'status'))):
            raise ObservationFormatError('Invalid parent-I/O packet.')
    return tuple(clean[key] for key in _PACKET_FIELDS)


@dataclass(frozen=True)
class ParentIoObservation:
    """Immutable scalar copy plus caller-declared identity, origin and boundary.

    Construct through bind_parent_io. These fields do not prove that the caller
    used the matching invocation's frozen capture. No packet freshness or host
    authentication is supplied by this record.
    """
    attempt_id: str
    source_sha256: str
    origin: str
    boundary: str
    _values: tuple

    def __post_init__(self):
        _identity(self.attempt_id, self.source_sha256)
        if type(self.origin) is not str or self.origin not in _ORIGINS:
            raise ObservationFormatError('Invalid observation origin.')
        if type(self.boundary) is not str or self.boundary != PRIMARY_BOUNDARY:
            raise ObservationFormatError('Invalid observation boundary.')
        if type(self._values) is not tuple or len(self._values) != len(_PACKET_FIELDS):
            raise ObservationFormatError('Invalid parent-I/O packet.')
        _packet_values(dict(zip(_PACKET_FIELDS, self._values)))


def bind_parent_io(packet, *, attempt_id: str, source_sha256: str,
                   origin: str, boundary: str) -> ParentIoObservation:
    """Copy a stable caller-owned scalar packet; do not collect or freeze it."""
    _identity(attempt_id, source_sha256)
    return ParentIoObservation(attempt_id, source_sha256, origin, boundary,
                               _packet_values(packet))


@dataclass(frozen=True)
class ParentIoProjection:
    """Labeled partial evidence; deliberately not a persistable terminal record.

    Extracting evidence and using the existing /1 serializer discards origin and
    boundary. This adapter provides no persistence bridge or execution admission.
    """
    evidence: TerminalEvidence
    origin: str
    boundary: str
    parent_io_status: str
    mapped_fields: tuple[str, ...]


def project_parent_io(observation: ParentIoObservation, *, expected_attempt_id: str,
                      expected_source_sha256: str, outcome: str) -> ParentIoProjection:
    """Project affirmative pipe marks into fresh, necessarily partial evidence.

    False marks mean not observed in this packet, not proof of non-occurrence.
    All other terminal facts stay unknown. No existing evidence is merged, no
    final outcome is inferred, and caller-declared synthetic provenance remains
    on the returned wrapper. A host_reported label is not host qualification.
    """
    if type(observation) is not ParentIoObservation:
        raise ObservationFormatError('Expected bound parent-I/O observation.')
    observation.__post_init__()
    _identity(expected_attempt_id, expected_source_sha256)
    if (observation.attempt_id != expected_attempt_id
            or observation.source_sha256 != expected_source_sha256):
        raise ObservationFormatError('Observation identity does not match this attempt.')
    packet = dict(zip(_PACKET_FIELDS, observation._values))
    mapped = {}
    if packet['available'] is True:
        for source, target in _PIPE_MAP:
            if source.startswith('stdin_') and packet['writer_expected'] is not True:
                continue
            if packet[source] is True:
                mapped[target] = True
    try:
        evidence = TerminalEvidence(expected_attempt_id, expected_source_sha256,
                                    outcome, **mapped)
    except TerminalFormatError:
        raise ObservationFormatError('Invalid declared terminal outcome.') from None
    return ParentIoProjection(evidence, observation.origin, observation.boundary,
                              packet['status'], tuple(mapped))
