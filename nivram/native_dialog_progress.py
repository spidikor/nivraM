"""Bounded observations from an opt-in native fixture's existing stderr pipe.

Records describe checkpoints, never successful selection or current execution.
The nonce binds records to an invocation but does not authenticate child claims.
No nonce, raw record, path, or arbitrary exception text enters an exported packet.
"""

import threading


MAX_LINE_BYTES = 96  # Includes the required newline.
MAX_PROGRESS_BYTES = 4096  # Does not replace the supervisor's stderr limit.
MAX_NORMAL_RECORDS = 31
LIMIT_SEQUENCE = 32
NORMAL_EVENTS = frozenset((
    'request_validate_enter', 'request_validated', 'backend_init_enter',
    'backend_ready', 'find_dialog_enter', 'identify_field_enter',
    'folder_route_enter', 'field_set_enter', 'field_readback_enter',
    'invoke_prepare_enter', 'invoke_final_checks', 'invoke_returned',
    'confirm_close_enter', 'confirm_close_returned', 'cancel_enter',
    'cancel_returned', 'backend_close_enter', 'backend_closed',
    'result_emit_enter',
    'controls_enum_enter', 'controls_enum_returned',
    'uia_bootstrap_enter', 'uia_bootstrap_returned',
    'idok_fromhandle_enter', 'idok_fromhandle_returned',
    'idok_name_enter', 'idok_name_returned',
    'edit_fromhandle_enter', 'edit_fromhandle_returned',
    'edit_name_enter', 'edit_name_returned',
))
_FIELDS = ('available', 'status', 'last_event', 'sequence', 'partial_record')
_FIELD_SET = frozenset(_FIELDS)
_UNAVAILABLE_STATUSES = frozenset(('unavailable', 'busy', 'invalid'))


def _unavailable(status='unavailable'):
    return {'available': False, 'status': status, 'last_event': None,
            'sequence': None, 'partial_record': None}


def progress_diagnostic(value):
    """Copy only a coherent exact-builtin packet without invoking custom hooks."""
    if (type(value) is not dict or len(value) != len(_FIELDS)
            or any(type(key) is not str for key in value)
            or set(value) != _FIELD_SET):
        return _unavailable()
    status = value['status']
    if type(status) is not str:
        return _unavailable()
    if status in _UNAVAILABLE_STATUSES:
        if (value['available'] is not False or value['last_event'] is not None
                or value['sequence'] is not None or value['partial_record'] is not None):
            return _unavailable()
    elif status == 'empty':
        if (value['available'] is not False or value['last_event'] is not None
                or type(value['sequence']) is not int or value['sequence'] != 0
                or type(value['partial_record']) is not bool):
            return _unavailable()
    elif status in ('valid', 'capped'):
        if (value['available'] is not True or type(value['last_event']) is not str
                or value['last_event'] not in NORMAL_EVENTS
                or type(value['sequence']) is not int
                or type(value['partial_record']) is not bool):
            return _unavailable()
        if status == 'valid' and not 1 <= value['sequence'] <= MAX_NORMAL_RECORDS:
            return _unavailable()
        if status == 'capped' and (value['sequence'] != LIMIT_SEQUENCE
                                   or value['partial_record'] is not False):
            return _unavailable()
    else:
        return _unavailable()
    return {key: value[key] for key in _FIELDS}


def _valid_nonce(value):
    return (type(value) is str and len(value) == 32
            and all(character in '0123456789abcdef' for character in value))


class ProgressCapture:
    """One bounded parser with an immutable snapshot at the primary boundary.

    feed accepts exact bytes only and processes at most 4096 bytes in total.
    freeze serializes with that bounded in-memory work; the lock never covers
    streams, process operations, callbacks, or joins. snapshot never waits for
    the parser lock. Exported dictionaries are fresh copies of fixed scalars.

    A capped packet retains the last normal event, with sequence 32 indicating
    that the diagnostic_limit marker was received. It is not a current stage.
    """

    def __init__(self, nonce):
        if not _valid_nonce(nonce):
            raise ValueError('Invalid native progress nonce.')
        self._nonce = nonce.encode('ascii')
        self._lock = threading.Lock()
        self._pending = bytearray()
        self._total_bytes = 0
        self._sequence = 0
        self._last_event = None
        self._status = 'empty'
        self._frozen = None

    def _invalidate(self):
        self._pending.clear()
        self._sequence = 0
        self._last_event = None
        self._status = 'invalid'

    def _consume_line(self, line):
        parts = line.split(b'|')
        if (len(parts) != 4 or parts[0] != b'NDP1' or parts[1] != self._nonce
                or parts[2] != str(self._sequence + 1).encode('ascii')):
            self._invalidate()
            return
        try:
            event = parts[3].decode('ascii')
        except UnicodeDecodeError:
            self._invalidate()
            return
        if event == 'diagnostic_limit':
            if self._sequence != MAX_NORMAL_RECORDS:
                self._invalidate()
                return
            self._sequence = LIMIT_SEQUENCE
            self._status = 'capped'
        elif event in NORMAL_EVENTS and self._sequence < MAX_NORMAL_RECORDS:
            self._sequence += 1
            self._last_event = event
            self._status = 'valid'
        else:
            self._invalidate()

    def feed(self, data):
        """Consume a stderr chunk; malformed input never becomes a receipt."""
        with self._lock:
            if self._frozen is not None or self._status == 'invalid':
                return
            if type(data) is not bytes or len(data) > MAX_PROGRESS_BYTES - self._total_bytes:
                self._invalidate()
                return
            self._total_bytes += len(data)
            for byte in data:
                if self._status == 'capped':
                    self._invalidate()
                    return
                if byte == 10:
                    line = bytes(self._pending)
                    self._pending.clear()
                    self._consume_line(line)
                    if self._status == 'invalid':
                        return
                else:
                    self._pending.append(byte)
                    # At 96 non-newline bytes, no completion can fit the limit.
                    if len(self._pending) >= MAX_LINE_BYTES:
                        self._invalidate()
                        return

    def _packet(self):
        if self._status == 'invalid':
            return _unavailable('invalid')
        return {'available': self._status in ('valid', 'capped'),
                'status': self._status, 'last_event': self._last_event,
                'sequence': self._sequence, 'partial_record': bool(self._pending)}

    def freeze(self):
        """Freeze once before cleanup; later feed calls cannot alter the packet."""
        with self._lock:
            if self._frozen is None:
                packet = self._packet()
                self._frozen = tuple(packet[key] for key in _FIELDS)
                self._pending.clear()
            return dict(zip(_FIELDS, self._frozen))

    def snapshot(self):
        """Return a copy immediately, or unavailable/busy while parsing."""
        frozen = self._frozen
        if frozen is not None:
            return dict(zip(_FIELDS, frozen))
        if not self._lock.acquire(blocking=False):
            return _unavailable('busy')
        try:
            if self._frozen is not None:
                return dict(zip(_FIELDS, self._frozen))
            return self._packet()
        finally:
            self._lock.release()


class ProgressEmitter:
    """Single-producer, best-effort writes to the existing binary stderr stream.

    The child calls this from its existing selection thread. There are no
    stream operations under a lock. The parent deadline still bounds a stalled
    write; small records alone do not guarantee nonblocking I/O.
    """

    def __init__(self, nonce, stream):
        if not _valid_nonce(nonce):
            raise ValueError('Invalid native progress nonce.')
        self._nonce = nonce
        self._stream = stream
        self._sequence = 0
        self._disabled = False

    def emit(self, event):
        if self._disabled:
            return
        if type(event) is not str or event not in NORMAL_EVENTS:
            self._disabled = True
            return
        self._sequence += 1
        if self._sequence == LIMIT_SEQUENCE:
            event = 'diagnostic_limit'
            self._disabled = True
        line = f'NDP1|{self._nonce}|{self._sequence}|{event}\n'.encode('ascii')
        if len(line) > MAX_LINE_BYTES:
            self._disabled = True
            return
        try:
            offset = 0
            while offset < len(line):
                written = self._stream.write(line[offset:])
                if type(written) is not int or not 1 <= written <= len(line) - offset:
                    self._disabled = True
                    return
                offset += written
            self._stream.flush()
        except Exception:
            self._disabled = True
