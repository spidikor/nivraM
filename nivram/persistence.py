"""Local project storage. Paths are supplied by native dialogs, never by web content."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
import uuid

from . import conditional_save as _conditional

MAX_PROJECT_BYTES = 4_000_000
MAX_RECOVERY_BYTES = MAX_PROJECT_BYTES + 4096
MAX_SAFE_INTEGER = 2**53 - 1
MAX_RECOVERY_ENTRIES = 1000


class UnsupportedRecovery(ValueError):
    """Retained recovery bytes require a format this reader cannot open."""


class SaveConflict(ValueError):
    """The destination changed since the application last read it."""


class SaveFailure(OSError):
    """Bounded public save outcome; underlying paths/errors stay out of the UI."""

    def __init__(self, status='failed', outcome='not_committed'):
        if (status not in ('unsupported', 'failed', 'unconfirmed')
                or outcome not in ('not_committed', 'unknown', 'committed')
                or (status in ('unsupported', 'failed') and outcome != 'not_committed')):
            raise ValueError('Invalid save failure category.')
        messages = {
            'unsupported': 'This destination does not support protected saving. Use Save As to choose a new file.',
            'failed': 'Saving failed before commit. Keep the drawing open and use Save As if needed.',
            'unconfirmed': 'Saving could not be fully confirmed. Keep the drawing open and inspect the destination, or use Save As.',
        }
        super().__init__(messages[status])
        self.status, self.outcome = status, outcome


def _encode(value):
    # Preserve the project format without materializing oversized indentation
    # from compact, deeply nested metadata before applying the byte budget.
    encoded = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, indent=2)
    for chunk in encoder.iterencode(value):
        remaining = MAX_PROJECT_BYTES - len(encoded)
        if len(chunk) > remaining:
            raise ValueError('Project exceeds the 4 MB local document limit.')
        data = chunk.encode('utf-8')
        if len(data) > remaining:
            raise ValueError('Project exceeds the 4 MB local document limit.')
        encoded.extend(data)
    return bytes(encoded)


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _read_bounded(path, limit):
    with Path(path).open('rb') as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Local record exceeds its supported size limit.')
    return data


def _decode_recovery(data):
    record = json.loads(data)
    if not isinstance(record, dict):
        raise ValueError('Invalid recovery record fields.')
    if record.get('schema') != 'nivram-recovery/1':
        raise UnsupportedRecovery('Unsupported recovery record format.')
    if set(record) != {'schema', 'created_ns', 'document'}:
        raise ValueError('Invalid recovery record fields.')
    stamp = record['created_ns']
    if isinstance(stamp, bool) or not isinstance(stamp, int) or not 0 < stamp < 10**20:
        raise ValueError('Invalid recovery timestamp.')
    if isinstance(record['document'], dict) and record['document'].get('schema') != 'nivram/1':
        raise UnsupportedRecovery('Unsupported recovered project format.')
    record['document'] = validate_project(record['document'])
    return record


def validate_project(document):
    from .chemistry import graph_to_mol
    from .scene import checked_style

    if not isinstance(document, dict) or document.get('schema') != 'nivram/1':
        raise ValueError('Unsupported nivraM project schema; original file was retained.')
    if set(document) - {'schema', 'version', 'graph', 'style', 'export', 'analysis'}:
        raise ValueError('Unsupported project fields; original file was retained.')
    data = _encode(document)
    if len(data) > MAX_PROJECT_BYTES:
        raise ValueError('Project exceeds the 4 MB local document limit.')
    graph_to_mol(document.get('graph'), allow_empty=True)
    checked_style(document.get('style'))
    options = document.get('export', {'width': 2000, 'marginMode': 'percent', 'margin': 2})
    if not isinstance(options, dict):
        raise ValueError('Invalid export settings.')
    if type(options.get('transparentBackground', False)) is not bool:
        raise ValueError('Transparent background must be true or false.')
    width, margin = options.get('width'), options.get('margin')
    if isinstance(width, bool) or not isinstance(width, int) or not 1 <= width <= 9999:
        raise ValueError('Export width must be an integer from 1 through 9999.')
    if isinstance(margin, bool) or not isinstance(margin, (int, float)) or not math.isfinite(margin) or margin < 0:
        raise ValueError('Invalid export margin.')
    mode = options.get('marginMode')
    if mode not in ('percent', 'pixels'):
        raise ValueError('Unsupported export margin mode.')
    padding = math.ceil(width * margin / 100) if mode == 'percent' else math.ceil(margin)
    if width - 2 * padding <= 0:
        raise ValueError('Margins leave no drawable area.')
    return json.loads(data)


def atomic_write(path, data, *, exclusive=False):
    """Publish only a fully written, flushed file in the destination directory."""
    path = Path(path)
    # Repeating the destination name can exceed Windows path/component limits
    # even when the destination itself is valid. Keep the random staging name
    # short; mkstemp still creates it exclusively in the same directory.
    fd, temporary = tempfile.mkstemp(prefix='.nv-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive:
            # A new Save As destination must still be absent at publication.
            # Windows rename refuses replacement; hard-link publication has
            # the same exclusive property on supported POSIX filesystems.
            if os.name == 'nt':
                os.rename(temporary, path)
            else:
                os.link(temporary, path)
                os.unlink(temporary)
        else:
            os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ProjectStore:
    def __init__(self, recovery_dir, history_limit=10):
        self.recovery_dir = Path(recovery_dir)
        self.history_limit = history_limit
        self._save_lock = threading.RLock()
        # Recovery has its own files and history. A pending project save must
        # not prevent a later dirty edit from publishing its recovery copy.
        self._recovery_lock = threading.RLock()
        self._recovery_generation = uuid.uuid4().hex
        self._recovery_revision = -1
        self._recovery_document_digest = None
        self._recovery_candidate = None
        self._recovery_stamp = 0

    def open(self, path):
        path = Path(path)
        data = _read_bounded(path, MAX_PROJECT_BYTES)
        document = validate_project(json.loads(data.decode('utf-8-sig')))
        return {'status': 'opened', 'document': document, 'disk_hash': _digest(data), 'name': path.name}

    def save(self, path, document, expected_disk_hash=None):
        data = _encode(validate_project(document))
        path = Path(path)
        saved_hash = _digest(data)
        with self._save_lock:
            if expected_disk_hash is not None:
                def retain_backup(old):
                    backup = path.with_suffix(path.suffix + '.bak')
                    atomic_write(backup, old)
                    if _read_bounded(backup, MAX_PROJECT_BYTES) != old:
                        raise OSError('Backup verification failed.')

                try:
                    committed_hash = _conditional.conditional_write_existing(path, data, expected_disk_hash, retain_backup)
                except _conditional.SaveError as exc:
                    outcome = exc.outcome if exc.outcome in ('not_committed', 'unknown', 'committed') else 'unknown'
                    if isinstance(exc, _conditional.SaveOutcomeUnknown):
                        outcome = 'unknown'
                    # The failed target close can be the primary error with an
                    # empty cleanup tuple. Neither branch proves clean closure.
                    if outcome != 'not_committed' or exc.cleanup_errors or exc.operation == 'CloseHandle':
                        raise SaveFailure('unconfirmed', outcome) from exc
                    if isinstance(exc, _conditional.SaveConflict):
                        raise SaveConflict('The destination changed, is missing, or is unavailable for protected saving. Use Save As or reopen it.') from exc
                    if isinstance(exc, _conditional.SaveUnsupported):
                        raise SaveFailure('unsupported', outcome) from exc
                    raise SaveFailure('failed', outcome) from exc
                if committed_hash != saved_hash:
                    raise SaveFailure('unconfirmed', 'committed')
            else:
                try:
                    atomic_write(path, data, exclusive=True)
                except FileExistsError as exc:
                    raise SaveConflict('Another file exists at the new destination. It was retained; choose Save As again.') from exc
                except OSError as exc:
                    # A publication may have succeeded before a later cleanup
                    # error. Do not retry or infer an untouched destination.
                    raise SaveFailure('unconfirmed', 'unknown') from exc
            try:
                if _digest(_read_bounded(path, MAX_PROJECT_BYTES)) != saved_hash:
                    raise OSError('Saved file verification failed.')
            except (OSError, ValueError) as exc:
                raise SaveFailure('unconfirmed', 'committed') from exc
        return {'status': 'saved', 'disk_hash': saved_hash, 'name': path.name}

    def recovery_context(self):
        with self._recovery_lock:
            return {'generation': self._recovery_generation}

    def begin_recovery_generation(self):
        """Host-owned editor-lifetime transition; never called by a submission."""
        with self._recovery_lock:
            self._recovery_generation = uuid.uuid4().hex
            self._recovery_revision = -1
            self._recovery_document_digest = None
            self._recovery_candidate = None
            # A preceding generation may have published an unconfirmed record.
            # Its reserved timestamp must survive even if inventory cannot read it.
            return {'generation': self._recovery_generation}

    def autosave(self, document, generation, revision):
        if (not isinstance(generation, str) or not re.fullmatch(r'[0-9a-f]{32}', generation)
                or type(revision) is not int or not 0 <= revision <= MAX_SAFE_INTEGER):
            raise ValueError('Invalid recovery generation or revision.')
        superseded = {'status': 'superseded', 'generation': generation, 'revision': revision}
        with self._recovery_lock:
            if generation != self._recovery_generation or revision < self._recovery_revision:
                return superseded
        document = validate_project(document)
        document_digest = _digest(json.dumps(document, ensure_ascii=False, allow_nan=False,
                                            sort_keys=True, separators=(',', ':')).encode('utf-8'))
        with self._recovery_lock:
            # Validation may have overlapped another producer or host navigation.
            if generation != self._recovery_generation or revision < self._recovery_revision:
                return superseded
            if revision == self._recovery_revision:
                if document_digest != self._recovery_document_digest:
                    raise ValueError('A recovery revision cannot identify different documents.')
            else:
                # Fence before filesystem work, including failed/uncertain writes.
                # A late older request may never become the most recent recovery.
                self._recovery_revision = revision
                self._recovery_document_digest = document_digest
                self._recovery_candidate = None
            previous = None
            if self._recovery_candidate is None:
                self.recovery_dir.mkdir(parents=True, exist_ok=True)
                previous = self._valid_recovery_records()
                stamp = max(time.time_ns(), self._recovery_stamp + 1,
                            max((item['created_ns'] for item in previous), default=0) + 1)
                self._recovery_stamp = stamp
                key = f'{stamp}-{uuid.uuid4().hex}'
                record = {'schema': 'nivram-recovery/1', 'created_ns': stamp, 'document': document}
                # Keep the existing schema and compact, bounded record encoding.
                encoded = json.dumps(record, ensure_ascii=False, allow_nan=False,
                                     separators=(',', ':')).encode('utf-8')
                if len(encoded) > MAX_RECOVERY_BYTES:
                    raise ValueError('Recovery record exceeds the supported size limit.')
                self._recovery_candidate = {'id': key, 'path': self.recovery_dir / (key + '.json'),
                                            'encoded': encoded, 'complete': False}
                candidate = self._recovery_candidate
                atomic_write(candidate['path'], encoded, exclusive=True)
                readback = _read_bounded(candidate['path'], MAX_RECOVERY_BYTES)
            else:
                candidate = self._recovery_candidate
                # Reconcile the exact candidate after an uncertain publication.
                # Only confirmed absence permits another exclusive publication.
                try:
                    readback = _read_bounded(candidate['path'], MAX_RECOVERY_BYTES)
                except FileNotFoundError:
                    atomic_write(candidate['path'], candidate['encoded'], exclusive=True)
                    candidate['complete'] = False
                    readback = _read_bounded(candidate['path'], MAX_RECOVERY_BYTES)
            if readback != candidate['encoded']:
                raise OSError('Recovery verification failed; no success receipt was issued.')
            _decode_recovery(readback)
            if not candidate['complete']:
                if previous is None:
                    previous = [item for item in self._valid_recovery_records() if item['id'] != candidate['id']]
                for old in previous[max(0, self.history_limit - 1):]:
                    (self.recovery_dir / (old['id'] + '.json')).unlink()
            # A success receipt must identify a copy that still exists after
            # rotation; never acknowledge a just-written record that was lost.
            if _read_bounded(candidate['path'], MAX_RECOVERY_BYTES) != candidate['encoded']:
                raise OSError('Recovery changed during rotation; no success receipt was issued.')
            candidate['complete'] = True
            return {'status': 'recovered-copy-saved', 'id': candidate['id'],
                    'generation': generation, 'revision': revision}

    def _valid_recovery_records(self):
        inventory = self.recovery_inventory()
        if inventory['status'] != 'ready':
            raise OSError('Recovery files could not be completely checked.')
        return inventory['records']

    def recovery_inventory(self):
        """Inspect bounded recovery evidence without migrating or removing it."""
        with self._recovery_lock:
            records = []
            issues = dict(invalid=0, unsupported=0, unreadable=0, interrupted=0)
            result = dict(schemaVersion=1, status='ready', records=records, issues=issues)
            started = False
            try:
                with os.scandir(self.recovery_dir) as entries:
                    started = True
                    for index, entry in enumerate(entries):
                        if index >= MAX_RECOVERY_ENTRIES:
                            return {**result, 'status': 'unavailable', 'records': []}
                        known = re.fullmatch(r'\d+-[0-9a-f]{32}\.json', entry.name)
                        temporary = entry.name.startswith('.nv-') and entry.name.endswith('.tmp')
                        if not known and not temporary:
                            continue
                        try:
                            info = entry.stat(follow_symlinks=False)
                            if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                                issues['unreadable'] += 1
                                continue
                            if temporary:
                                issues['interrupted'] += 1
                                continue
                            key = entry.name[:-5]
                            value = self.recover(key)
                            records.append({'id': key, 'created_ns': value['created_ns']})
                        except UnsupportedRecovery:
                            issues['unsupported'] += 1
                        except (ValueError, TypeError, RecursionError):
                            issues['invalid'] += 1
                        except OSError:
                            issues['unreadable'] += 1
            except FileNotFoundError:
                if started:
                    return {**result, 'status': 'unavailable', 'records': []}
            except OSError:
                return {**result, 'status': 'unavailable', 'records': []}
            records.sort(key=lambda item: (item['created_ns'], item['id']), reverse=True)
            return result

    def list_recovery(self):
        with self._recovery_lock:
            return self._valid_recovery_records()

    def recover(self, key):
        if not isinstance(key, str) or not re.fullmatch(r'\d+-[0-9a-f]{32}', key):
            raise ValueError('Invalid recovery identifier.')
        return _decode_recovery(_read_bounded(self.recovery_dir / (key + '.json'), MAX_RECOVERY_BYTES))
