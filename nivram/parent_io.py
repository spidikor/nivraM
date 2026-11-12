"""Optional bounded parent pipe observations, separate from completion authority.

Only scalar facts are retained. Snapshots never wait for the capture lock and a
frozen busy observation remains unknown. Different stream facts are collected
separately; this record is not an atomic view at the deadline.
"""
import threading


MAX_CAPTURE_BYTES = 16 * 1024 * 1024
_MARKS = ('stdout_reader_entered', 'stderr_reader_entered', 'stdout_eof',
          'stderr_eof', 'stdin_writer_entered', 'stdin_write_completed',
          'stdin_close_completed')
_FACTS = ('writer_expected', 'stdout_limit', 'stdout_retained_bytes', *_MARKS)
_FIELDS = ('available', 'status', *_FACTS)


def _unavailable(status='unavailable'):
    return {'available': False, 'status': status, **dict.fromkeys(_FACTS)}


def parent_io_diagnostic(value):
    """Copy an exact scalar schema; reject arbitrary text and custom hooks."""
    if (type(value) is not dict or len(value) != len(_FIELDS)
            or any(type(key) is not str for key in value)
            or set(value) != set(_FIELDS) or type(value['status']) is not str):
        return _unavailable()
    status = value['status']
    if status in ('unavailable', 'busy'):
        if value['available'] is not False or any(value[key] is not None for key in _FACTS):
            return _unavailable()
        return _unavailable(status)
    if (status != 'valid' or value['available'] is not True
            or type(value['writer_expected']) is not bool
            or type(value['stdout_limit']) is not int
            or not 1 <= value['stdout_limit'] <= MAX_CAPTURE_BYTES
            or type(value['stdout_retained_bytes']) is not int
            or not 0 <= value['stdout_retained_bytes'] <= value['stdout_limit']
            or any(type(value[key]) is not bool for key in _MARKS)
            or (value['stdout_eof'] and not value['stdout_reader_entered'])
            or (value['stderr_eof'] and not value['stderr_reader_entered'])
            or (value['stdout_retained_bytes'] > 0 and not value['stdout_reader_entered'])
            or ((value['stdin_write_completed'] or value['stdin_close_completed'])
                and not value['stdin_writer_entered'])
            or (not value['writer_expected'] and any(value[key] for key in
                ('stdin_writer_entered', 'stdin_write_completed', 'stdin_close_completed')))):
        return _unavailable()
    return {key: value[key] for key in _FIELDS}


class ParentIoCapture:
    """One invocation's observations; no stream operations occur under its lock.

    configure/mark/retained hold the lock only over fixed in-memory scalars.
    snapshot and freeze each make one nonblocking lock attempt. A contended
    freeze publishes a permanently unknown packet before cleanup can begin.
    Frozen records are fresh copies; later observation updates cannot rewrite
    their primary boundary. Reusing a configured capture is rejected.
    """

    def __init__(self, *, primary_observation=False):
        if type(primary_observation) is not bool:
            raise ValueError('Primary observation must be explicitly enabled or disabled.')
        self._primary_observation = primary_observation
        self._lock = threading.Lock()
        self._facts = None
        self._configured = False
        self._frozen = {}

    def configure(self, *, writer_expected, stdout_limit):
        if (type(writer_expected) is not bool or type(stdout_limit) is not int
                or not 1 <= stdout_limit <= MAX_CAPTURE_BYTES):
            raise ValueError('Invalid parent pipe observation bounds.')
        with self._lock:
            if self._configured:
                raise ValueError('A parent pipe observation can only be configured once.')
            self._configured = True
            if not self._frozen:
                self._facts = {'available': True, 'status': 'valid',
                    'writer_expected': writer_expected, 'stdout_limit': stdout_limit,
                    'stdout_retained_bytes': 0, **{key: False for key in _MARKS}}

    def mark(self, field):
        if type(field) is not str or field not in _MARKS:
            raise ValueError('Invalid parent pipe observation field.')
        with self._lock:
            if not self._frozen and self._facts is not None:
                self._facts[field] = True

    def retained(self, count):
        if type(count) is not int or not 0 <= count <= MAX_CAPTURE_BYTES:
            raise ValueError('Invalid retained stdout count.')
        with self._lock:
            if not self._frozen and self._facts is not None:
                if not self._facts['stdout_retained_bytes'] <= count <= self._facts['stdout_limit']:
                    raise ValueError('Invalid retained stdout count.')
                self._facts['stdout_retained_bytes'] = count

    def snapshot(self):
        record = self._frozen.get('record')
        if record is not None:
            return parent_io_diagnostic(record[0])
        if not self._lock.acquire(blocking=False):
            return _unavailable('busy')
        try:
            record = self._frozen.get('record')
            return parent_io_diagnostic(record[0] if record is not None else self._facts)
        finally:
            self._lock.release()

    def _freeze_record(self, outcome):
        record = self._frozen.get('record')
        if record is not None:
            return record
        if not self._lock.acquire(blocking=False):
            # Publish packet and boundary outcome together. A prior ordinary
            # close/freeze wins and cannot subsequently be upgraded to primary.
            return self._frozen.setdefault('record', (_unavailable('busy'), outcome))
        try:
            return self._frozen.setdefault('record', (parent_io_diagnostic(self._facts), outcome))
        finally:
            self._lock.release()

    def freeze(self):
        return parent_io_diagnostic(ParentIoCapture._freeze_record(self, None)[0])

    def freeze_primary(self, outcome):
        """Called only at the supervisor's existing pre-cleanup finally boundary.

        This records no process ownership/exit/cleanup authority. The first
        freeze wins; an earlier close suppresses primary observation entirely.
        """
        if type(outcome) is not str or outcome not in (
                'returned', 'timeout', 'cancelled', 'interrupted', 'error'):
            raise ValueError('Invalid primary observation outcome.')
        return parent_io_diagnostic(ParentIoCapture._freeze_record(
            self, outcome if self._primary_observation else None)[0])

    def primary_snapshot(self):
        """No live fallback; None until an explicitly primary freeze won."""
        record = self._frozen.get('record')
        if record is None or record[1] is None:
            return None
        return {'packet': parent_io_diagnostic(record[0]), 'outcome': record[1]}
