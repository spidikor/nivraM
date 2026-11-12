"""Fresh portable S2.2 receipts; no process control or execution admission.

This new schema is not the missing historical supervisor interface. Recorded
facts require a future host adapter; this module does not authenticate them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import uuid

SCHEMA = 'nivram-fresh-supervision-terminal/1'
MAX_RECEIPT_BYTES = 8192
_OUTCOMES = frozenset({'returned', 'timeout', 'cancelled', 'interrupted', 'error'})
_FACTS = ('ownership_confirmed', 'root_exit_confirmed', 'stdin_write_completed', 'stdin_closed',
          'stdout_eof', 'stderr_eof', 'output_drained', 'output_closed',
          'reader_threads_exited', 'cleanup_confirmed', 'forced_cleanup')


class TerminalFormatError(ValueError):
    """Malformed or differently bound evidence; never echoes private input."""


def _identity(attempt_id, source_sha256):
    if (type(attempt_id) is not str or type(source_sha256) is not str
            or len(attempt_id) != 36 or len(source_sha256) != 64):
        raise TerminalFormatError('Invalid receipt identity.')
    try:
        parsed = uuid.UUID(attempt_id)
    except (ValueError, AttributeError):
        raise TerminalFormatError('Invalid receipt identity.') from None
    if (str(parsed) != attempt_id or parsed.version != 4
            or re.fullmatch('[0-9a-f]{64}', source_sha256) is None):
        raise TerminalFormatError('Invalid receipt identity.')


@dataclass(frozen=True)
class TerminalEvidence:
    """Bounded facts from one attempt; None means unknown, never success."""
    attempt_id: str
    source_sha256: str
    outcome: str
    returncode: int | None = None
    ownership_confirmed: bool | None = None
    root_exit_confirmed: bool | None = None
    stdin_write_completed: bool | None = None
    stdin_closed: bool | None = None
    stdout_eof: bool | None = None
    stderr_eof: bool | None = None
    output_drained: bool | None = None
    output_closed: bool | None = None
    reader_threads_exited: bool | None = None
    cleanup_confirmed: bool | None = None
    remaining_descendants: int | None = None
    forced_cleanup: bool | None = None

    def __post_init__(self):
        _identity(self.attempt_id, self.source_sha256)
        if type(self.outcome) is not str or self.outcome not in _OUTCOMES:
            raise TerminalFormatError('Invalid terminal outcome.')
        if self.returncode is not None and (
                type(self.returncode) is not int
                or not -(2**31) <= self.returncode < 2**32):
            raise TerminalFormatError('Invalid return code.')
        for name in _FACTS:
            value = getattr(self, name)
            if value is not None and type(value) is not bool:
                raise TerminalFormatError('Invalid observation type.')
        if self.remaining_descendants is not None and (
                type(self.remaining_descendants) is not int
                or not 0 <= self.remaining_descendants <= 65535):
            raise TerminalFormatError('Invalid descendant count.')
        if self.root_exit_confirmed is False and self.returncode is not None:
            raise TerminalFormatError('Contradictory exit observations.')


@dataclass(frozen=True)
class EvidenceAssessment:
    complete_recorded_evidence: bool
    normal_exit_recorded: bool
    incomplete: tuple[str, ...]


def assess_recorded_evidence(evidence: TerminalEvidence) -> EvidenceAssessment:
    """Evaluate supplied facts only; never authorize a launch or next case."""
    if type(evidence) is not TerminalEvidence:
        raise TerminalFormatError('Expected terminal evidence.')
    evidence.__post_init__()
    missing = [name for name in _FACTS if name != 'forced_cleanup'
               and getattr(evidence, name) is not True]
    if evidence.returncode is None:
        missing.append('returncode')
    if evidence.remaining_descendants != 0:
        missing.append('remaining_descendants')
    if evidence.forced_cleanup is None:
        missing.append('forced_cleanup')
    complete = not missing
    normal = (complete and evidence.outcome == 'returned'
              and evidence.returncode == 0 and evidence.forced_cleanup is False)
    return EvidenceAssessment(complete, normal, tuple(missing))


def encode_terminal(evidence: TerminalEvidence) -> bytes:
    if type(evidence) is not TerminalEvidence:
        raise TerminalFormatError('Expected terminal evidence.')
    evidence.__post_init__()
    result = (json.dumps({'schema': SCHEMA, **asdict(evidence)}, sort_keys=True,
                         separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    if len(result) > MAX_RECEIPT_BYTES:
        raise TerminalFormatError('Receipt exceeds its byte limit.')
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TerminalFormatError('Duplicate receipt field.')
        result[key] = value
    return result


def _invalid_constant(_):
    raise TerminalFormatError('Invalid JSON constant.')


def decode_terminal(data: bytes, *, expected_attempt_id: str,
                    expected_source_sha256: str) -> TerminalEvidence:
    _identity(expected_attempt_id, expected_source_sha256)
    if type(data) is not bytes or not 0 < len(data) <= MAX_RECEIPT_BYTES:
        raise TerminalFormatError('Invalid receipt byte length.')
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object,
                           parse_constant=_invalid_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise TerminalFormatError('Invalid terminal document.') from None
    names = {field.name for field in fields(TerminalEvidence)}
    if (type(value) is not dict or set(value) != names | {'schema'}
            or type(value['schema']) is not str or value['schema'] != SCHEMA):
        raise TerminalFormatError('Invalid terminal schema.')
    evidence = TerminalEvidence(**{name: value[name] for name in names})
    if (evidence.attempt_id != expected_attempt_id
            or evidence.source_sha256 != expected_source_sha256):
        raise TerminalFormatError('Receipt identity does not match this attempt.')
    return evidence


def _read_bytes(path: Path) -> bytes:
    """Bounded regular-file read; owned directory is a caller prerequisite."""
    flags = (os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
             | getattr(os, 'O_NONBLOCK', 0))
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECEIPT_BYTES:
            raise TerminalFormatError('Invalid receipt file.')
        chunks = []
        remaining = MAX_RECEIPT_BYTES + 1
        while remaining:
            part = os.read(fd, remaining)
            if not part:
                break
            chunks.append(part)
            remaining -= len(part)
        data = b''.join(chunks)
        if len(data) > MAX_RECEIPT_BYTES:
            raise TerminalFormatError('Receipt exceeds its byte limit.')
        return data
    finally:
        os.close(fd)


def read_terminal(path, *, expected_attempt_id: str,
                  expected_source_sha256: str) -> TerminalEvidence:
    """Read valid bytes only; cannot reconstruct a writer acknowledgement."""
    _identity(expected_attempt_id, expected_source_sha256)
    return decode_terminal(_read_bytes(Path(path)), expected_attempt_id=expected_attempt_id,
                           expected_source_sha256=expected_source_sha256)


@dataclass(frozen=True)
class WriteSnapshot:
    attempt_id: str
    state: str
    expected_sha256: str
    file_created: bool
    file_synced: bool
    file_closed: bool
    readback_verified: bool
    writer_started: bool
    writer_exited: bool
    failure_stage: str | None

    @property
    def receipt_acknowledged(self) -> bool:
        return (self.state == 'acknowledged' and self.file_created and self.file_synced
                and self.file_closed and self.readback_verified)


class ReceiptWrite:
    """One retained daemon writer, one exclusive attempt file, no overwrite.

    Construct before start so the owner retains the handle if start is
    interrupted. Each instance permits only one start attempt. A wait budget is
    not a hard wall-clock guarantee and never cancels the writer. Snapshots are
    immutable observations, not atomic filesystem/thread snapshots. The owned
    directory must already exist and remain controlled by the caller. Protection
    against hostile parent-directory replacement is not claimed. File fsync
    does not establish directory-entry crash durability.
    """

    def __init__(self, directory, evidence: TerminalEvidence):
        self._data = encode_terminal(evidence)
        directory = Path(directory).resolve(strict=True)
        if not directory.is_dir():
            raise ValueError('A receipt directory is required.')
        self._path = directory / (evidence.attempt_id + '.terminal.json')
        self._attempt = evidence.attempt_id
        self._digest = hashlib.sha256(self._data).hexdigest()
        self._lock = threading.Lock()
        self._start_attempted = False
        self._state = 'not_started'
        self._created = self._synced = self._closed = self._readback = False
        self._failure = None
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name='nivram-receipt-writer')

    @property
    def path(self) -> Path:
        return self._path

    def start(self):
        with self._lock:
            if self._start_attempted:
                raise RuntimeError('This receipt already has a start attempt.')
            self._start_attempted = True
            self._state = 'pending'
        try:
            self._thread.start()
        except BaseException:
            with self._lock:
                self._state, self._failure = 'failed', 'start'
            raise
        return self

    def _run(self):
        stage = 'open'
        try:
            with self._path.open('xb') as stream:
                with self._lock:
                    self._created = True
                stage = 'write'
                if stream.write(self._data) != len(self._data):
                    raise OSError('Incomplete receipt write.')
                stage = 'flush'
                stream.flush()
                stage = 'fsync'
                os.fsync(stream.fileno())
                with self._lock:
                    self._synced = True
                stage = 'close'
            with self._lock:
                self._closed = True
            stage = 'readback'
            if _read_bytes(self._path) != self._data:
                raise OSError('Receipt readback mismatch.')
            with self._lock:
                self._readback = True
                # A start interruption remains failed even if this retained
                # thread subsequently completes the file write.
                if self._failure is None:
                    self._state = 'acknowledged'
        except BaseException:
            with self._lock:
                self._state, self._failure = 'failed', stage

    def snapshot(self) -> WriteSnapshot:
        started = self._thread.ident is not None
        exited = started and not self._thread.is_alive()
        with self._lock:
            return WriteSnapshot(self._attempt, self._state, self._digest,
                                 self._created, self._synced, self._closed, self._readback,
                                 started, exited, self._failure)

    def wait(self, timeout_seconds: float) -> WriteSnapshot:
        if (type(timeout_seconds) not in (int, float)
                or not 0 <= timeout_seconds <= 30 or not math.isfinite(timeout_seconds)):
            raise ValueError('A finite receipt wait from zero to thirty seconds is required.')
        if self._thread.ident is not None:
            self._thread.join(timeout_seconds)
        return self.snapshot()
