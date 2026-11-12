"""Dialog-scoped, bounded batch PNG export; never opens the active document.

The renderer is the existing browser exportPng function. This module snapshots
selected files, validates isolated inputs and PNG bytes, and publishes new files
without replacing existing destinations. Only opaque IDs cross the bridge.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
import threading
import uuid
import zlib

from .persistence import MAX_PROJECT_BYTES, atomic_write
from .import_preparation import (ImportPreparationError, ImportPreparationService,
                                 checked_batch_settings)
from .worker_processes import WorkerCancelled
from .png_metadata import validate_export_text, MAX_METADATA_BYTES

MAX_FILES = 64
MAX_TOTAL_SOURCE_BYTES = 64 * 1024 * 1024
MAX_PNG_BYTES = 32 * 1024 * 1024
MAX_PNG_PIXELS = 32_000_000
MAX_MANIFEST_BYTES = 256 * 1024
TERMINAL = frozenset({'exported', 'failed', 'cancelled'})
ERRORS = {
    'unsupported_type': 'Only .nivram projects and 2D .mol files are batch inputs.',
    'source_unreadable': 'The selected input could not be read.',
    'source_too_large': 'The input exceeds the 4 MB per-file limit.',
    'source_total_limit': 'The batch exceeds the 64 MiB source snapshot limit.',
    'unsupported_atom_annotation': 'MOL atom aliases, atom values and attachment-point annotations cannot be preserved. Use a copy with these annotations explicitly resolved or removed in the source editor.',
    'invalid_input': 'The input is malformed or outside the supported 2D chemistry scope.',
    'empty_drawing': 'The input contains no molecular drawing to export.',
    'render_failed': 'The isolated molecular drawing could not be rendered.',
    'hash_style_failed': 'Thicker, spaced bars could not preserve this hashed stereobond. Choose Engine spacing for Hashed stereobonds and retry; no PNG was published.',
    'busy': 'Local structure preparation is busy. This file was not prepared.',
    'unavailable': 'Bounded local structure preparation is unavailable.',
    'timed_out': 'Local structure preparation exceeded its time limit.',
    'worker_failed': 'The local structure preparation worker could not complete this file.',
    'export_failed': 'PNG rendering failed or exceeded its safe raster budget.',
    'encoding_failed': 'The rendered PNG could not be encoded for local saving.',
    'invalid_png': 'The returned PNG failed format, size, encoded-stream, background, or margin validation.',
    'write_failed': 'The PNG could not be saved and verified in the chosen folder.',
    'write_unverified': 'PNG publication could not be verified. The batch stopped; inspect the output folder before retrying.',
    'cancelled': 'This file was cancelled before a PNG was published.',
}


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8')


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _settings(value):
    # This validator is pure Python; profile references must not reach the
    # project validator's native chemistry path through an empty graph.
    return checked_batch_settings(value)


class _BatchPreparationCancellation:
    def __init__(self, event):
        self._event = event

    @property
    def cancelled(self):
        return self._event.is_set()

    def cancel(self):
        self._event.set()


def _safe_stem(name):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', Path(name).stem).strip(' .')[:100]
    if not value or re.fullmatch(r'(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', value):
        value = 'structure'
    return value


class _PublishedOutputUnverified(OSError):
    """A publication attempt or later step failed; retain its possible output name."""
    def __init__(self, output):
        super().__init__('Published output could not be fully verified.')
        self.output = output


def _publish_unique(directory, stem, suffix, data, cancelled=None):
    """Flush a temporary sibling, then atomically publish without replacement.

    Windows rename rejects an existing destination. POSIX uses an exclusive
    hard link instead because rename there would replace an existing file.
    """
    fd, temporary = tempfile.mkstemp(prefix='.nivram-batch-', suffix='.tmp', dir=directory)
    temporary = Path(temporary)
    possible_output = None
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        for number in range(1, 10_001):
            if cancelled is not None and cancelled.is_set():
                raise InterruptedError('Batch export cancelled before publication.')
            filename = f'{stem}{"" if number == 1 else f" ({number})"}{suffix}'
            destination = directory / filename
            try:
                if os.name == 'nt':
                    os.rename(temporary, destination)
                else:
                    os.link(temporary, destination)
                possible_output = destination
                return destination
            except FileExistsError:
                continue
            except OSError as error:
                # Publication may have committed before acknowledgement failed.
                # A later cleanup error must retain that uncertainty and name.
                possible_output = destination
                raise _PublishedOutputUnverified(destination) from error
        raise OSError('No unused output filename is available.')
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as error:
            if possible_output is not None:
                raise _PublishedOutputUnverified(possible_output) from error
            raise


def _unfilter_png_row(kind, filtered, previous, channels):
    """Reconstruct one 8-bit RGB(A) scanline using row-bounded buffers."""
    if kind == 0:
        return filtered
    if kind == 2:
        return bytes((value + above) & 255 for value, above in zip(filtered, previous))
    if kind not in (1, 3, 4):
        raise ValueError('Invalid PNG scanline filter.')
    # The source validator has fixed depth, channel count and row length.
    # Pillow receives only a generated two-row carrier, never the source PNG.
    from .png_preview import _unfilter
    return _unfilter(kind, filtered, previous, len(filtered)//channels, 2 if channels == 3 else 6)


def _decode_png(encoded, options, cancelled, *, remove_chemistry_metadata=False):
    """Validate a complete RGB(A) PNG and the requested white or alpha border.

    Row-sized reconstruction buffers and an accumulator bounded by one partial
    scanline plus a 64 KiB decoded block are retained; no full raster is allocated.
    Valid input bytes are returned unchanged rather than re-encoded.
    """
    if type(remove_chemistry_metadata) is not bool:
        raise ValueError('Remove chemistry metadata must be true or false.')
    if not isinstance(encoded, str) or not 0 < len(encoded) <= 4 * ((MAX_PNG_BYTES + 2) // 3):
        raise ValueError('PNG byte limit exceeded.')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('Invalid PNG encoding.') from exc
    if len(data) > MAX_PNG_BYTES or not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('Invalid PNG bytes.')
    transparent = options.get('transparentBackground', False)
    if type(transparent) is not bool:
        raise ValueError('Transparent background must be true or false.')
    padding = math.ceil(options['width'] * options['margin'] / 100
                        if options['marginMode'] == 'percent' else options['margin'])
    position, count, expanded = 8, 0, 0
    metadata_versions = {}
    width = height = row_bytes = expected = channels = None
    scanline = bytearray()
    previous = white_row = None
    rows = 0
    inflater, in_idat, ended_idat = zlib.decompressobj(), False, False
    while position < len(data):
        if cancelled.is_set():
            raise InterruptedError('Batch export cancelled during PNG validation.')
        count += 1
        if count > 8192 or position + 12 > len(data):
            raise ValueError('Invalid PNG chunk framing.')
        length = struct.unpack_from('>I', data, position)[0]
        kind = data[position+4:position+8]
        end = position + 12 + length
        if end > len(data) or not re.fullmatch(b'[A-Za-z]{4}', kind):
            raise ValueError('Invalid PNG chunk length/type.')
        payload = memoryview(data)[position+8:position+8+length]
        crc = struct.unpack_from('>I', data, position+8+length)[0]
        if zlib.crc32(payload, zlib.crc32(kind)) != crc:
            raise ValueError('PNG checksum failed.')
        if count == 1 and kind != b'IHDR':
            raise ValueError('PNG header must be first.')
        if kind == b'IHDR':
            if count != 1 or length != 13:
                raise ValueError('Invalid PNG header.')
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', payload)
            if (width != options['width'] or not 0 < height <= 32767
                    or width * height > MAX_PNG_PIXELS or height <= 2*padding
                    or depth != 8 or color not in (2, 6) or (transparent and color != 6) or compression or filtering or interlace):
                raise ValueError('Unsupported PNG dimensions or encoding.')
            channels = 4 if color == 6 else 3
            row_bytes = 1 + width * channels
            expected = row_bytes * height
            previous = bytes(row_bytes-1)
            white_row = b'\xff' * (row_bytes-1)
        elif kind == b'IDAT':
            if ended_idat:
                raise ValueError('PNG image chunks must be contiguous.')
            in_idat = True
            # Feed small compressed slices: decompressobj.unconsumed_tail can
            # otherwise copy a large, highly compressible image payload.
            for start in range(0, length, 65536):
                pending = payload[start:start+65536]
                while len(pending):
                    if cancelled.is_set():
                        raise InterruptedError('Batch export cancelled during PNG validation.')
                    block = inflater.decompress(pending, 65536)
                    if inflater.unused_data or expanded + len(block) > expected:
                        raise ValueError('PNG deflate stream exceeds its dimensions.')
                    # Reject malformed filter identifiers even if the relevant
                    # scanline is split between decompression blocks.
                    first_filter = (-expanded) % row_bytes
                    if any(block[index] > 4 for index in range(first_filter, len(block), row_bytes)):
                        raise ValueError('Invalid PNG scanline filter.')
                    expanded += len(block)
                    scanline.extend(block)
                    while len(scanline) >= row_bytes:
                        if cancelled.is_set():
                            raise InterruptedError('Batch export cancelled during PNG validation.')
                        row = _unfilter_png_row(scanline[0], bytes(scanline[1:row_bytes]), previous, channels)
                        if not transparent and channels == 4 and row[3::4] != b'\xff' * width:
                            raise ValueError('PNG export contains transparent pixels.')
                        if padding and transparent:
                            alpha = row[3::4]
                            if ((rows < padding or rows >= height-padding) and any(alpha)
                                    or any(alpha[:padding]) or any(alpha[-padding:])):
                                raise ValueError('PNG export margins must be transparent.')
                        elif padding:
                            border = padding * channels
                            if ((rows < padding or rows >= height-padding) and row != white_row
                                    or row[:border] != white_row[:border]
                                    or row[-border:] != white_row[-border:]):
                                raise ValueError('PNG export margins must be opaque white.')
                        previous = row
                        rows += 1
                        del scanline[:row_bytes]
                    pending = inflater.unconsumed_tail
        elif kind in (b'tEXt', b'iTXt', b'zTXt'):
            if in_idat:
                ended_idat = True
            if length > MAX_METADATA_BYTES + 256:
                raise ValueError('PNG metadata exceeds its limit.')
            key, version = validate_export_text(kind, bytes(payload))
            if remove_chemistry_metadata and key == 'nivraM Coordinates':
                raise ValueError('PNG still contains chemistry metadata.')
            if key in metadata_versions:
                raise ValueError('Duplicate PNG metadata.')
            metadata_versions[key] = version
            if len(set(metadata_versions.values())) > 1:
                raise ValueError('PNG producer versions disagree.')
        elif kind == b'IEND':
            if (length or end != len(data) or not in_idat or not inflater.eof
                    or expanded != expected or rows != height or scanline):
                raise ValueError('Incomplete or concatenated PNG image stream.')
            return data, {'width': width, 'height': height, 'padding': padding}
        else:
            if in_idat:
                ended_idat = True
            # Admit only bounded known canvas ancillary chunks; text is checked above.
            if kind not in (b'sRGB', b'gAMA', b'cHRM', b'pHYs', b'iCCP', b'cICP', b'mDCV', b'cLLI', b'sBIT'):
                raise ValueError('Unsupported PNG ancillary chunk.')
            if kind == b'sBIT' and (length != channels or any(value < 1 or value > 8 for value in payload)):
                raise ValueError('Invalid PNG significant-bit-depth chunk.')
            if length > 65536:
                raise ValueError('PNG ancillary chunk exceeds its limit.')
        position = end
    raise ValueError('PNG end marker is missing.')


class BatchExportManager:
    """One active session; this private manager is not itself the JS bridge."""

    def __init__(self, *, preparation_service=None):
        self._lock = threading.RLock()
        self._session = None
        self._choosing = False
        self._closed = False
        self._owns_preparation_service = preparation_service is None
        self._preparation_service = (ImportPreparationService() if preparation_service is None
                                     else preparation_service)

    def begin(self, window, settings):
        import webview
        checked = _settings(settings)
        with self._lock:
            if self._closed:
                raise ValueError('Batch export is closed.')
            if self._choosing or self._session and self._session['status'] == 'running':
                raise ValueError('Finish or cancel the active batch first.')
            self._choosing = True
        try:
            paths = window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True,
                file_types=('Molecular projects and 2D MOL (*.nivram;*.mol)',))
            if not paths:
                return {'status': 'cancelled'}
            if self._closed:
                return {'status': 'cancelled'}
            if len(paths) > MAX_FILES:
                raise ValueError(f'Choose no more than {MAX_FILES} files per batch.')
            folders = window.create_file_dialog(webview.FileDialog.FOLDER)
            if not folders:
                return {'status': 'cancelled'}
            with self._lock:
                if self._closed:
                    return {'status': 'cancelled'}
                return self._begin_selected(paths, folders[0], checked)
        finally:
            with self._lock:
                self._choosing = False

    def _begin_selected(self, paths, directory, settings):
        """Internal dialog boundary, also used with synthetic paths in tests."""
        if not 0 < len(paths) <= MAX_FILES:
            raise ValueError('Invalid batch input count.')
        checked = _settings(settings)
        target = Path(directory).resolve(strict=True)
        if not target.is_dir():
            raise ValueError('Choose an existing output folder.')
        session = {'id': uuid.uuid4().hex, 'status': 'running', 'settings': checked,
                   'directory': target, 'items': [], 'cancelled': threading.Event(),
                   'manifest': None, 'manifestHash': None, 'manifestError': None, 'active': None}
        total = 0
        for selected in paths:
            path = Path(selected)
            item = {'id': uuid.uuid4().hex, 'name': path.name, 'extension': path.suffix.lower(),
                    'status': 'pending', 'sourceSha256': None, 'outputName': None,
                    'outputSha256': None, 'dimensions': None, 'error': None, '_bytes': None}
            try:
                if item['extension'] not in ('.nivram', '.mol'):
                    self._error(item, 'unsupported_type')
                else:
                    with path.open('rb') as source:
                        data = source.read(MAX_PROJECT_BYTES + 1)
                    if len(data) > MAX_PROJECT_BYTES:
                        self._error(item, 'source_too_large')
                    elif total + len(data) > MAX_TOTAL_SOURCE_BYTES:
                        self._error(item, 'source_total_limit')
                    else:
                        item['_bytes'] = data
                        item['sourceSha256'] = _hash(data)
                        total += len(data)
            except (OSError, ValueError):
                self._error(item, 'source_unreadable')
            session['items'].append(item)
        self._session = session
        try:
            self._manifest(session, first=True)
        except OSError:
            self._release(session)
            self._session = None
            raise OSError('The batch manifest could not be created in the chosen folder.') from None
        return self._receipt(session)

    @staticmethod
    def _error(item, code):
        item.update(status='cancelled' if code == 'cancelled' else 'failed',
                    error={'code': code, 'message': ERRORS[code]}, _bytes=None)

    def _lookup(self, session_id, item_id=None):
        session = self._session
        if not isinstance(session_id, str) or not session or session['id'] != session_id:
            raise ValueError('Unknown batch session.')
        if item_id is None:
            return session
        if not isinstance(item_id, str):
            raise ValueError('Unknown batch item.')
        item = next((entry for entry in session['items'] if entry['id'] == item_id), None)
        if item is None:
            raise ValueError('Unknown batch item.')
        return session, item

    @staticmethod
    def _public_item(item):
        return copy.deepcopy({key: value for key, value in item.items() if not key.startswith('_')})

    def _receipt(self, session):
        return {'sessionId': session['id'], 'status': session['status'],
                'settings': copy.deepcopy(session['settings']),
                'items': [self._public_item(item) for item in session['items']],
                'manifestName': session['manifest'].name if session['manifest'] else None,
                'manifestError': session['manifestError']}

    def _manifest(self, session, first=False):
        value = {'schema': 'nivram-batch-export/1', **self._receipt(session)}
        # This manifest includes basenames and hashes, never source/destination paths.
        data = _json_bytes(value)
        if len(data) > MAX_MANIFEST_BYTES:
            raise OSError('Manifest byte limit exceeded.')
        if first:
            session['manifest'] = _publish_unique(session['directory'], 'nivram-batch-manifest', '.json', data)
        else:
            # Preserve an externally edited manifest rather than overwriting it.
            path = session['manifest']
            with path.open('rb') as handle:
                prior = handle.read(MAX_MANIFEST_BYTES + 1)
            if _hash(prior) != session['manifestHash']:
                raise OSError('The saved manifest changed outside this session.')
            atomic_write(path, data)
        with session['manifest'].open('rb') as handle:
            saved = handle.read(MAX_MANIFEST_BYTES + 1)
        if saved != data:
            raise OSError('Manifest write could not be verified.')
        session['manifestHash'] = _hash(data)

    def _checkpoint(self, session):
        try:
            self._manifest(session)
        except OSError:
            session['manifestError'] = 'The latest manifest could not be saved; completed PNGs were retained.'
            self._stop(session)

    def _stop(self, session):
        session['cancelled'].set()
        session['status'] = 'stopped'
        for item in session['items']:
            if item['status'] not in TERMINAL:
                self._error(item, 'cancelled')
        session['active'] = None
        self._release(session)

    def _publication_unverified(self, session, item, output):
        self._error(item, 'write_unverified')
        # A known name is useful for recovery. A digest or dimensions would
        # falsely claim successful readback, especially after external edits.
        item.update(outputName=output.name, outputSha256=None, dimensions=None)
        self._stop(session)

    @staticmethod
    def _release(session):
        for item in session['items']:
            item['_bytes'] = None
            item.pop('_preparation', None)

    def prepare(self, session_id, item_id):
        with self._lock:
            session, item = self._lookup(session_id, item_id)
            if self._closed or session['status'] != 'running' or session['cancelled'].is_set():
                return {'status': 'cancelled', 'item': self._public_item(item)}
            first = next((entry for entry in session['items'] if entry['status'] == 'pending'), None)
            if session['active'] is not None or first is not item:
                raise ValueError('Batch items must be prepared once, in order.')
            # Reserve this item before releasing the lock. Cancellation, close,
            # and subsequent sessions must be able to proceed while a child runs.
            cancellation = _BatchPreparationCancellation(session['cancelled'])
            item['_preparation'] = cancellation
            session['active'] = item['id']
            source, extension = item['_bytes'], item['extension']
            settings = copy.deepcopy(session['settings'])

        prepared, failure_code = None, None
        try:
            text = source.decode('utf-8-sig')
            prepared = self._preparation_service.prepare_batch(
                text, extension, settings, cancellation_token=cancellation)
        except WorkerCancelled:
            failure_code = 'cancelled'
        except ImportPreparationError as exc:
            failure_code = exc.code if exc.code in ERRORS else 'worker_failed'
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError, RuntimeError):
            failure_code = 'invalid_input'

        with self._lock:
            # A late response belongs only to its original reservation; it must
            # not mutate a newer session or revive a cancelled item.
            current = (self._session is session and session['active'] == item['id']
                       and item.get('_preparation') is cancellation
                       and item['status'] == 'pending')
            if (not current or self._closed or session['status'] != 'running'
                    or cancellation.cancelled):
                if current and session['status'] == 'running':
                    # Service shutdown can cancel the token without a separate
                    # batch-cancel call; finalize that session and release bytes.
                    self.cancel(session['id'])
                return {'status': 'cancelled', 'item': self._public_item(item),
                        'manifestError': session['manifestError']}
            item.pop('_preparation', None)
            if failure_code is not None:
                self._error(item, failure_code)
                session['active'] = None
            else:
                item['status'] = 'rendering'
                item['_bytes'] = None
                self._checkpoint(session)
                if session['status'] == 'running' and not cancellation.cancelled and not self._closed:
                    return {'status': 'ready', 'item': self._public_item(item),
                            'project': copy.deepcopy(prepared['project']),
                            'graph': copy.deepcopy(prepared['graph']), 'scene': prepared['scene']}
                if session['status'] == 'running':
                    self.cancel(session['id'])
                return {'status': item['status'], 'item': self._public_item(item),
                        'manifestError': session['manifestError']}
            self._checkpoint(session)
            return {'status': item['status'], 'item': self._public_item(item),
                    'manifestError': session['manifestError']}

    def write_png(self, session_id, item_id, png_base64):
        with self._lock:
            session, item = self._lookup(session_id, item_id)
            if session['status'] != 'running' or session['cancelled'].is_set():
                return {'status': 'cancelled', 'item': self._public_item(item)}
            if session['active'] != item_id or item['status'] != 'rendering':
                raise ValueError('This item is not the active batch export.')
            try:
                data, dimensions = _decode_png(png_base64, session['settings']['export'], session['cancelled'],
                                               remove_chemistry_metadata=session['settings'].get('removeChemistryMetadata', False))
            except InterruptedError:
                self._error(item, 'cancelled')
            except (ValueError, OSError, SyntaxError, OverflowError, zlib.error):
                self._error(item, 'invalid_png')
            else:
                output = None
                try:
                    output = _publish_unique(session['directory'], _safe_stem(item['name']), '.png',
                                             data, session['cancelled'])
                    digest = _hash(data)
                    with output.open('rb') as saved:
                        if _hash(saved.read(MAX_PNG_BYTES + 1)) != digest:
                            raise OSError('PNG write verification failed.')
                    item.update(status='exported', outputName=output.name,
                                outputSha256=digest, dimensions=dimensions)
                except _PublishedOutputUnverified as error:
                    self._publication_unverified(session, item, error.output)
                except InterruptedError:
                    if output is None:
                        self._error(item, 'cancelled')
                    else:
                        self._publication_unverified(session, item, output)
                except OSError:
                    if output is None:
                        self._error(item, 'write_failed')
                    else:
                        self._publication_unverified(session, item, output)
            session['active'] = None
            self._checkpoint(session)
            return {'status': 'stopped' if (item.get('error') or {}).get('code') == 'write_unverified' else item['status'],
                    'item': self._public_item(item),
                    'manifestError': session['manifestError']}

    def fail(self, session_id, item_id, error_code):
        if error_code not in ('render_failed', 'export_failed', 'encoding_failed'):
            raise ValueError('Invalid batch failure code.')
        with self._lock:
            session, item = self._lookup(session_id, item_id)
            if item['status'] in TERMINAL:
                return {'status': item['status'], 'item': self._public_item(item)}
            if session['active'] != item_id or item['status'] != 'rendering':
                raise ValueError('This item is not the active batch export.')
            self._error(item, 'cancelled' if session['cancelled'].is_set() else error_code)
            session['active'] = None
            self._checkpoint(session)
            return {'status': item['status'], 'item': self._public_item(item),
                    'manifestError': session['manifestError']}

    def cancel(self, session_id):
        # Set the signal before waiting for a preparation/write lock. Publication
        # has its own last check; a file already atomically published is retained.
        session = self._lookup(session_id)
        session['cancelled'].set()
        with self._lock:
            if session['status'] == 'running':
                session['status'] = 'cancelled'
                for item in session['items']:
                    if item['status'] not in TERMINAL:
                        self._error(item, 'cancelled')
                session['active'] = None
                self._checkpoint(session)
                self._release(session)
            return self._receipt(session)

    def finish(self, session_id):
        with self._lock:
            session = self._lookup(session_id)
            if session['status'] == 'running':
                if any(item['status'] not in TERMINAL for item in session['items']):
                    raise ValueError('The batch still has unfinished items.')
                session['status'] = 'completed'
                self._checkpoint(session)
                self._release(session)
            return self._receipt(session)

    def close(self):
        """Private application-lifecycle hook; does not accept any bridge path."""
        self._closed = True
        session = self._session
        try:
            if session and session['status'] == 'running':
                return self.cancel(session['id'])
            return None
        finally:
            # A desktop-injected shared service belongs to the server lifecycle.
            if self._owns_preparation_service:
                self._preparation_service.shutdown(timeout_seconds=2.0)
