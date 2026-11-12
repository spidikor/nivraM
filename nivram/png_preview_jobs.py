"""Bounded native-owned PNG preview jobs; no browser-supplied file paths."""
from __future__ import annotations

import base64
from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile
import threading
import zlib

from .worker_processes import CancellationToken, WorkerCancelled, WorkerSupervisor

MAX_SOURCE = 64 * 1024 * 1024
MAX_PREVIEW = 4 * 1024 * 1024
MAX_STDOUT = 6 * 1024 * 1024
MAX_CACHE_BYTES = 128 * 1024 * 1024
MAX_CACHE_ENTRIES = 128
MAX_CACHE_DEPTH = 4
SIGNATURE = b'\x89PNG\r\n\x1a\n'
SAFE_FAILURE = 'The PNG preview could not be completed within its supported limits.'


def _integer(value, minimum, maximum):
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def _dimensions(data):
    if (len(data) < 33 or data[:8] != SIGNATURE or
            data[8:16] != b'\x00\x00\x00\x0dIHDR'):
        raise ValueError('A valid bounded PNG header is required for source review.')
    width, height = struct.unpack_from('>II', data, 16)
    if not 1 <= width <= 32768 or not 1 <= height <= 32768 or width * height > 128_000_000:
        raise ValueError('This PNG exceeds the bounded region-preview dimensions.')
    return width, height


def validate_region(mode, rect, source_size=None):
    if mode == 'overview':
        if rect is not None:
            raise ValueError('An overview request must cover the complete source.')
        return None
    if mode != 'crop' or not isinstance(rect, dict) or set(rect) != {'x', 'y', 'width', 'height'}:
        raise ValueError('Choose an overview or a complete integer crop rectangle.')
    if (not all(_integer(rect[key], 0, 32767) for key in ('x', 'y')) or
            not all(_integer(rect[key], 1, 2048) for key in ('width', 'height')) or
            rect['width'] * rect['height'] > 1_000_000):
        raise ValueError('A detail crop must use nonnegative integer coordinates and at most one million pixels, with each edge at most 2048 pixels.')
    if source_size and (rect['x'] + rect['width'] > source_size[0] or rect['y'] + rect['height'] > source_size[1]):
        raise ValueError('The detail crop must stay inside the original PNG.')
    return dict(rect)


def _validate_derivative_png(data, width, height):
    """Validate the derivative's complete PNG/zlib stream without a raster.

    Only the encoder's RGB/RGBA subset, optionally with one sRGB declaration,
    is admitted. Dimensions
    have already been limited to one million pixels. Decompression emits at
    most 64 KiB at once and checks every scanline filter and the exact length;
    valid filter bytes 0..4 define a decoded row for every possible byte value.
    """
    if _dimensions(data) != (width, height):
        raise ValueError
    position, chunks, produced = 8, 0, 0
    row_bytes = expected = None
    decoder = zlib.decompressobj()
    seen_data = False
    seen_srgb = False
    while position < len(data):
        chunks += 1
        if chunks > 4096 or len(data)-position < 12:
            raise ValueError
        length = struct.unpack_from('>I', data, position)[0]
        kind = data[position+4:position+8]
        stop = position+length+12
        if stop > len(data):
            raise ValueError
        body = memoryview(data)[position+8:stop-4]
        if zlib.crc32(body, zlib.crc32(kind)) != struct.unpack_from('>I', data, stop-4)[0]:
            raise ValueError
        if kind == b'IHDR':
            if chunks != 1 or length != 13:
                raise ValueError
            w, h, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', body)
            if (w, h) != (width, height) or depth != 8 or color not in (2, 6) or compression or filtering or interlace:
                raise ValueError
            row_bytes = width*(3 if color == 2 else 4)+1
            expected = row_bytes*height
        elif kind == b'sRGB':
            if row_bytes is None or seen_data or seen_srgb or length != 1 or body[0] > 3:
                raise ValueError
            seen_srgb = True
        elif kind == b'IDAT':
            if row_bytes is None:
                raise ValueError
            seen_data = True
            for offset in range(0, len(body), 65536):
                compressed = body[offset:offset+65536]
                while True:
                    plain = decoder.decompress(compressed, min(65536, expected-produced+1))
                    if len(plain) > expected-produced:
                        raise ValueError
                    if any(plain[index] > 4 for index in range((-produced) % row_bytes, len(plain), row_bytes)):
                        raise ValueError
                    produced += len(plain)
                    compressed = decoder.unconsumed_tail
                    if decoder.unused_data:
                        raise ValueError
                    if decoder.eof or not compressed and not plain:
                        break
        elif kind == b'IEND':
            if length or stop != len(data) or not seen_data or not decoder.eof or produced != expected:
                raise ValueError
            return
        else:
            # No other ancillary, palette, animation or color-transform chunks
            # are generated by the decoder; do not pass them to the UI.
            raise ValueError
        position = stop
    raise ValueError


def _check_cache_capacity(root, incoming):
    """Bound retained orphan bytes and scan work before a new snapshot exists.

    Unknown files are counted but never removed. Links/reparse points and
    excessively nested or crowded caches are retained and refused, not followed.
    This is an application quota, not a filesystem quota against external races.
    """
    failure = 'The PNG preview cache exceeds its safe limits or contains redirected entries; retained data was not removed.'
    total, count = incoming, 2  # Reserve the new directory and source file.
    pending = [(root, 0)]
    try:
        while pending:
            directory, depth = pending.pop()
            info = directory.stat(follow_symlinks=False)
            if (not stat.S_ISDIR(info.st_mode)
                    or getattr(info, 'st_file_attributes', 0) & 0x400):
                raise ValueError(failure)
            with os.scandir(directory) as entries:
                for entry in entries:
                    count += 1
                    if count > MAX_CACHE_ENTRIES:
                        raise ValueError(failure)
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                        raise ValueError(failure)
                    if stat.S_ISREG(info.st_mode):
                        total += info.st_size
                        if total > MAX_CACHE_BYTES:
                            raise ValueError(failure)
                    elif stat.S_ISDIR(info.st_mode) and depth < MAX_CACHE_DEPTH:
                        pending.append((Path(entry.path), depth+1))
                    else:
                        raise ValueError(failure)
        if total > MAX_CACHE_BYTES or count > MAX_CACHE_ENTRIES:
            raise ValueError(failure)
    except OSError as exc:
        raise ValueError(failure) from exc


def validate_preview_result(value, source_hash, mode, rect, source_size=None):
    """Check the bounded derivative before it can cross the native bridge."""
    try:
        if (not isinstance(value, dict) or type(value.get('schemaVersion')) is not int or value.get('schemaVersion') != 1 or
                value.get('sourceSha256') != source_hash or value.get('mode') != mode):
            raise ValueError
        dimensions = value['sourceWidth'], value['sourceHeight']
        if (not all(_integer(n, 1, 32768) for n in dimensions) or
                dimensions[0] * dimensions[1] > 128_000_000 or
                source_size is not None and dimensions != source_size):
            raise ValueError
        expected_rect = validate_region(mode, rect, dimensions)
        if expected_rect is None:
            expected_rect = {'x': 0, 'y': 0, 'width': dimensions[0], 'height': dimensions[1]}
        actual_rect = value['rect']
        if (not isinstance(actual_rect, dict) or set(actual_rect) != set(expected_rect) or
                any(type(actual_rect[k]) is not int for k in actual_rect) or actual_rect != expected_rect):
            raise ValueError
        width, height = value['width'], value['height']
        maximum = 960 if mode == 'overview' else 2048
        largest = max(dimensions)
        expected_size = ((max(1, dimensions[0]*960//largest), max(1, dimensions[1]*960//largest))
                         if mode == 'overview' and largest > 960 else dimensions)
        if mode == 'crop':
            expected_size = rect['width'], rect['height']
        resampling = 'nearest-center' if mode == 'overview' else 'none'
        if (not _integer(width, 1, maximum) or not _integer(height, 1, maximum) or
                width * height > 1_000_000 or (width, height) != expected_size or
                value.get('resampling') != resampling or value.get('navigationOnly') is not (mode == 'overview')):
            raise ValueError
        encoded = value['pngBase64']
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_PREVIEW + 2) // 3):
            raise ValueError
        png = base64.b64decode(encoded, validate=True)
        if (len(png) > MAX_PREVIEW or _dimensions(png) != (width, height) or
                value['pngSha256'] != hashlib.sha256(png).hexdigest()):
            raise ValueError
        _validate_derivative_png(png, width, height)
        # Allow only bounded display/provenance metadata, never arbitrary child fields.
        engine = value.get('engine', {})
        accounting = value.get('bufferAccounting', {})
        if (not isinstance(engine, dict) or len(engine) > 12 or
                not isinstance(accounting, dict) or len(accounting) > 24 or
                len(json.dumps({'engine': engine, 'bufferAccounting': accounting}, allow_nan=False)) > 4096):
            raise ValueError
        return {key: value[key] for key in ('schemaVersion', 'sourceSha256', 'sourceWidth', 'sourceHeight',
                'mode', 'rect', 'width', 'height', 'pngBase64', 'pngSha256')} | {
                'engine': engine, 'bufferAccounting': accounting, 'navigationOnly': mode == 'overview',
                'resampling': resampling}
    except (KeyError, TypeError, ValueError, OverflowError, zlib.error) as exc:
        raise ValueError('The PNG preview worker returned an invalid bounded image.') from exc


def worker_command():
    if getattr(sys, 'frozen', False):
        return [str(Path(sys.executable).with_name('nivram-worker.exe')), '--png-preview']
    return [sys.executable, '-m', 'nivram.png_preview_jobs', '--worker']


class ImagePreviewJobs:
    def __init__(self, directory):
        self.directory = Path(directory)
        self._supervisor = WorkerSupervisor(max_workers=1)
        self._lock = threading.RLock()
        self._active = None
        self._cancelled = deque(maxlen=64)

    def preview(self, data, job_id, request_id, mode, rect=None, *, timeout=45, _command=None):
        for value in (job_id, request_id):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', value):
                raise ValueError('Invalid source or region request identifier.')
        if not isinstance(data, bytes) or len(data) > MAX_SOURCE:
            raise ValueError('PNG input exceeds the 64 MiB source limit.')
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 45:
            raise ValueError('A PNG preview deadline must be at most 45 seconds.')
        dimensions = _dimensions(data)
        rect = validate_region(mode, rect, dimensions)
        key = (job_id, request_id)
        token = CancellationToken()
        with self._lock:
            self._supervisor.check_cancelled()
            if key in self._cancelled:
                raise WorkerCancelled('The PNG region review was cancelled.')
            if self._active is not None:
                raise ValueError('Another PNG region preview is running. Cancel it or wait for completion.')
            state = {'key': key, 'token': token}
            self._active = state
        directory = snapshot = None
        try:
            self._supervisor.check_cancelled(token)
            self.directory.mkdir(parents=True, exist_ok=True)
            _check_cache_capacity(self.directory, len(data))
            self._supervisor.check_cancelled(token)
            directory = Path(tempfile.mkdtemp(prefix='png-', dir=self.directory)).resolve()
            if directory.parent != self.directory.resolve():
                raise ValueError('The native source snapshot left its owned cache directory.')
            snapshot = directory / 'source.png'
            with snapshot.open('xb') as handle:
                handle.write(data)
            digest = hashlib.sha256(data).hexdigest()
            request = {'sourcePath': str(snapshot), 'sourceSha256': digest, 'mode': mode, 'rect': rect}
            output = self._supervisor.run(_command or worker_command(), input_text=json.dumps(request),
                timeout_seconds=timeout, cancellation_token=token, max_input_bytes=4096,
                max_stdout_bytes=MAX_STDOUT, max_stderr_bytes=8192)
            self._supervisor.check_cancelled(token)
            if output.returncode != 0:
                raise ValueError(SAFE_FAILURE)
            try:
                value = json.loads(output.stdout)
            except (TypeError, ValueError) as exc:
                raise ValueError(SAFE_FAILURE) from exc
            checked = validate_preview_result(value, digest, mode, rect, dimensions)
            self._supervisor.check_cancelled(token)
        finally:
            # Remove only this exact owned snapshot, after its direct worker exited.
            # Unknown files or redirected directories are retained for investigation.
            try:
                if directory is not None and self._supervisor.active_count == 0:
                    if directory.parent != self.directory.resolve() or directory.resolve() != directory:
                        raise ValueError('The source snapshot directory changed; it was retained.')
                    if snapshot is not None:
                        snapshot.unlink(missing_ok=True)
                    directory.rmdir()
            except OSError as exc:
                raise ValueError('PNG preview cleanup could not be confirmed; remaining source data was retained locally.') from exc
            finally:
                with self._lock:
                    if self._active is state:
                        self._active = None

        # Snapshot cleanup and registry retirement may outlast cancellation.
        # Only a successful attempt reaches this original-token publication check.
        self._supervisor.check_cancelled(token)
        return checked

    def cancel(self, job_id, request_id=None):
        with self._lock:
            state = self._active
            if request_id is not None:
                self._cancelled.append((job_id, request_id))
            if state and state['key'][0] == job_id and (request_id is None or state['key'][1] == request_id):
                state['token'].cancel()
                return {'status': 'cancelled'}
        return {'status': 'not_running'}

    def close(self):
        with self._lock:
            if self._active:
                self._active['token'].cancel()
        return self._supervisor.shutdown()


def worker_main():
    from .png_preview import preview_png
    try:
        request_bytes = sys.stdin.buffer.read(4097)
        if len(request_bytes) > 4096:
            return 2
        request = json.loads(request_bytes)
        if not isinstance(request, dict) or set(request) != {'sourcePath', 'sourceSha256', 'mode', 'rect'}:
            return 2
        with Path(request['sourcePath']).open('rb') as handle:
            data = handle.read(MAX_SOURCE + 1)
        if len(data) > MAX_SOURCE or hashlib.sha256(data).hexdigest() != request['sourceSha256']:
            return 2
        value = preview_png(data, mode=request['mode'], rect=request['rect'], deadline_seconds=40)
        encoded = json.dumps(value, allow_nan=False, ensure_ascii=True).encode('utf-8')
        if len(encoded) > MAX_STDOUT:
            return 2
        sys.stdout.buffer.write(encoded)
        return 0
    except Exception:
        return 2


if __name__ == '__main__':
    raise SystemExit(worker_main() if sys.argv[1:] == ['--worker'] else 2)
