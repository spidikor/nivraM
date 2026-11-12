import base64
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import zlib

from PIL import Image

from nivram.png_preview_jobs import ImagePreviewJobs, validate_preview_result
from nivram.worker_processes import WorkerCancelled, WorkerOutputLimit


def png(width=3, height=2):
    stream = io.BytesIO()
    Image.new('RGBA', (width, height), (17, 31, 47, 128)).save(stream, format='PNG')
    return stream.getvalue()


def result(data, mode='crop', rect=None):
    image = png()
    return {'schemaVersion': 1, 'sourceSha256': hashlib.sha256(data).hexdigest(),
            'sourceWidth': 3, 'sourceHeight': 2, 'mode': mode,
            'rect': rect or {'x': 0, 'y': 0, 'width': 3, 'height': 2},
            'width': 3, 'height': 2, 'pngBase64': base64.b64encode(image).decode('ascii'),
            'pngSha256': hashlib.sha256(image).hexdigest(),
            'navigationOnly': mode == 'overview',
            'resampling': 'nearest-center' if mode == 'overview' else 'none',
            'engine': {'name': 'bounded PNG preview', 'version': '1'},
            'bufferAccounting': {'maxRows': 2}}


class PreviewResultTests(unittest.TestCase):
    def test_overview_must_preserve_exact_integer_geometry(self):
        source = png(1000, 500)
        value = result(source, mode='overview')
        value.update(sourceWidth=1000, sourceHeight=500,
                     rect={'x': 0, 'y': 0, 'width': 1000, 'height': 500})
        with self.assertRaises(ValueError):
            validate_preview_result(value, value['sourceSha256'], 'overview', None, (1000, 500))

    def test_resampling_contract_is_required_and_preserved(self):
        source = png()
        for mode in ('crop', 'overview'):
            value = result(source, mode=mode)
            rect = value['rect'] if mode == 'crop' else None
            checked = validate_preview_result(value, value['sourceSha256'], mode, rect, (3, 2))
            self.assertEqual(checked.get('resampling'), value['resampling'])
            for bad in (None, 'bilinear', 'none' if mode == 'overview' else 'nearest-center'):
                with self.subTest(mode=mode, bad=bad), self.assertRaises(ValueError):
                    validate_preview_result({**value, 'resampling': bad}, value['sourceSha256'], mode, rect, (3, 2))

    def test_derivative_structure_crc_and_complete_bounded_stream_are_required(self):
        def chunk(kind, body):
            return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(body, zlib.crc32(kind)))
        source = png()
        value = result(source)
        header = source[:33]
        end = chunk(b'IEND', b'')
        raw = (b'\0' + bytes(range(12))) * 2
        compressed = zlib.compress(raw)
        def image(stream):
            return header + chunk(b'IDAT', stream) + end
        good = image(compressed)
        damaged = bytearray(good)
        damaged[-1] ^= 1
        cases = [header, bytes(damaged), good + b'x',
                 image(compressed[:-1]), image(compressed + zlib.compress(b'')),
                 image(zlib.compress(raw[:-1])), image(zlib.compress(raw+b'x'*1_000_000)),
                 image(zlib.compress(b'\5'+raw[1:])),
                 header+chunk(b'IDAT', compressed[:2])+chunk(b'tEXt', b'a\0b')+chunk(b'IDAT', compressed[2:])+end]
        for index, bad in enumerate(cases):
            forged = {**value, 'pngBase64': base64.b64encode(bad).decode('ascii'),
                      'pngSha256': hashlib.sha256(bad).hexdigest()}
            with self.subTest(case=index), self.assertRaises(ValueError):
                validate_preview_result(forged, value['sourceSha256'], 'crop', value['rect'], (3, 2))

    def test_result_is_bound_to_original_and_exact_requested_crop(self):
        source = png()
        value = result(source)
        checked = validate_preview_result(value, hashlib.sha256(source).hexdigest(), 'crop', value['rect'])
        self.assertEqual(checked['pngSha256'], value['pngSha256'])
        for field, replacement in [('sourceSha256', 'a' * 64), ('width', True),
                                   ('pngSha256', 'b' * 64), ('mode', 'overview'),
                                   ('rect', {'x': 1, 'y': 0, 'width': 2, 'height': 2})]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_preview_result({**value, field: replacement},
                                        hashlib.sha256(source).hexdigest(), 'crop', value['rect'])

    def test_png_header_must_agree_with_receipt(self):
        source = png()
        value = result(source)
        value.update(width=2, rect={'x': 0, 'y': 0, 'width': 2, 'height': 2})
        with self.assertRaises(ValueError):
            validate_preview_result(value, value['sourceSha256'], 'crop', value['rect'])


class PreviewJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.jobs = ImagePreviewJobs(self.root / 'preview-cache')
        self.source = png()
        self.rect = {'x': 0, 'y': 0, 'width': 3, 'height': 2}

    def tearDown(self):
        self.jobs.close()
        self.temp.cleanup()

    def test_native_snapshot_verified_and_removed_after_owned_worker_returns(self):
        paths = []
        def worker(command, **options):
            request = json.loads(options['input_text'])
            snapshot = Path(request['sourcePath'])
            paths.append(snapshot)
            self.assertEqual(snapshot.read_bytes(), self.source)
            self.assertEqual(request['sourceSha256'], hashlib.sha256(self.source).hexdigest())
            self.assertEqual(snapshot.parent.parent.resolve(), self.jobs.directory.resolve())
            self.assertEqual(options['max_stdout_bytes'], 6 * 1024 * 1024)
            return subprocess.CompletedProcess(command, 0, json.dumps(result(self.source)), '')
        self.jobs._supervisor.run = Mock(side_effect=worker)
        value = self.jobs.preview(self.source, 'image_job_1', 'region_job_1', 'crop', self.rect)
        self.assertEqual(value['sourceSha256'], hashlib.sha256(self.source).hexdigest())
        self.assertFalse(paths[0].exists())
        self.assertFalse(paths[0].parent.exists())

    def test_cancel_before_worker_registration_never_starts_a_process(self):
        self.jobs._supervisor.run = Mock()
        self.jobs.cancel('image_job_1', 'region_job_1')
        with self.assertRaises(WorkerCancelled):
            self.jobs.preview(self.source, 'image_job_1', 'region_job_1', 'crop', self.rect)
        self.jobs._supervisor.run.assert_not_called()

    def test_cancelled_worker_cannot_publish_success_and_snapshot_is_removed(self):
        paths = []
        def worker(command, **options):
            paths.append(Path(json.loads(options['input_text'])['sourcePath']))
            self.jobs.cancel('image_job_1', 'region_job_1')
            return subprocess.CompletedProcess(command, 0, json.dumps(result(self.source)), '')
        self.jobs._supervisor.run = Mock(side_effect=worker)
        with self.assertRaises(WorkerCancelled):
            self.jobs.preview(self.source, 'image_job_1', 'region_job_1', 'crop', self.rect)
        self.assertFalse(paths[0].exists())

    def test_invalid_rectangle_rejected_before_snapshot_or_worker(self):
        self.jobs._supervisor.run = Mock()
        for rect in ({'x': True, 'y': 0, 'width': 3, 'height': 2},
                     {'x': 0, 'y': 0, 'width': 2049, 'height': 1},
                     {'x': 0, 'y': 0, 'width': 1024, 'height': 1024},
                     {'x': -1, 'y': 0, 'width': 1, 'height': 1}):
            with self.subTest(rect=rect), self.assertRaises(ValueError):
                self.jobs.preview(self.source, 'image_job_1', 'region_job_1', 'crop', rect)
        self.jobs._supervisor.run.assert_not_called()
        self.assertFalse(self.jobs.directory.exists())

    def test_worker_failure_uses_safe_message_and_cleans_snapshot(self):
        paths = []
        def worker(command, **options):
            paths.append(Path(json.loads(options['input_text'])['sourcePath']))
            return subprocess.CompletedProcess(command, 2, '', 'private source text')
        self.jobs._supervisor.run = Mock(side_effect=worker)
        with self.assertRaisesRegex(ValueError, r'^The PNG preview could not be completed within its supported limits\.$'):
            self.jobs.preview(self.source, 'image_job_1', 'region_job_1', 'crop', self.rect)
        self.assertFalse(paths[0].exists())

    def test_closed_supervisor_refuses_new_work(self):
        self.assertTrue(self.jobs.close())
        with self.assertRaises(WorkerCancelled):
            self.jobs.preview(self.source, 'image_job_1', 'region_job_1', 'crop', self.rect)

    def test_orphan_cache_byte_budget_prevents_new_snapshot_and_preserves_orphan(self):
        self.jobs.directory.mkdir()
        orphan = self.jobs.directory / 'retained.bin'
        orphan.write_bytes(b'123456')
        self.jobs._supervisor.run = Mock()
        with patch('nivram.png_preview_jobs.MAX_CACHE_BYTES', len(self.source)+5), \
                self.assertRaisesRegex(ValueError, 'cache'):
            self.jobs.preview(self.source, 'image_job_1', 'region_job_1', 'crop', self.rect)
        self.jobs._supervisor.run.assert_not_called()
        self.assertIsNone(self.jobs._active)
        self.assertEqual(orphan.read_bytes(), b'123456')
        self.assertEqual(list(self.jobs.directory.iterdir()), [orphan])

    def test_orphan_cache_entry_budget_bounds_empty_directories(self):
        self.jobs.directory.mkdir()
        for index in range(3):
            (self.jobs.directory / f'retained-{index}').mkdir()
        self.jobs._supervisor.run = Mock()
        with patch('nivram.png_preview_jobs.MAX_CACHE_ENTRIES', 4), \
                self.assertRaisesRegex(ValueError, 'cache'):
            self.jobs.preview(self.source, 'image_job_1', 'region_job_1', 'crop', self.rect)
        self.jobs._supervisor.run.assert_not_called()
        self.assertIsNone(self.jobs._active)
        self.assertEqual(len(list(self.jobs.directory.iterdir())), 3)

    def test_real_decoder_worker_supervisor_preserves_crop_pixels_and_accounting(self):
        value = self.jobs.preview(self.source, 'image_job_1', 'region_job_1', 'crop', self.rect)
        with Image.open(io.BytesIO(base64.b64decode(value['pngBase64']))) as derived:
            self.assertEqual(derived.tobytes(), bytes((17, 31, 47, 128))*6)
        self.assertEqual(value['sourceSha256'], hashlib.sha256(self.source).hexdigest())
        self.assertEqual(value['bufferAccounting']['maxCarrierHeight'], 2)
        self.assertEqual(value['bufferAccounting']['sourceSnapshotBytes'], len(self.source))
        self.assertEqual(value['resampling'], 'none')
        self.assertEqual(self.jobs._supervisor.active_count, 0)
        self.assertEqual(list(self.jobs.directory.iterdir()), [])

    def test_real_worker_keeps_srgb_declaration_and_hidden_color(self):
        stream = io.BytesIO()
        Image.new('RGBA', (3, 2), (17, 31, 47, 0)).save(stream, format='PNG')
        data = stream.getvalue()
        body = b'sRGB\x02'
        tag = struct.pack('>I', 1) + body + struct.pack('>I', zlib.crc32(body))
        source = data[:33] + tag + data[33:]
        value = self.jobs.preview(source, 'image_job_1', 'region_job_1', 'crop', self.rect)
        with Image.open(io.BytesIO(base64.b64decode(value['pngBase64']))) as derived:
            self.assertEqual(derived.info['srgb'], 2)
            self.assertEqual(derived.tobytes(), bytes((17, 31, 47, 0))*6)
        self.assertEqual(value['sourceSha256'], hashlib.sha256(source).hexdigest())
        self.assertEqual(self.jobs._supervisor.active_count, 0)
        self.assertEqual(list(self.jobs.directory.iterdir()), [])

    def test_real_worker_accepts_canonical_srgb_fallbacks(self):
        source = png()
        declarations = b''
        for kind, data in ((b'gAMA', struct.pack('>I', 45455)),
                           (b'cHRM', struct.pack('>8I', 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)),
                           (b'sRGB', b'\x01')):
            body = kind + data
            declarations += struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body))
        source = source[:33] + declarations + source[33:]
        value = self.jobs.preview(source, 'image_job_1', 'region_job_1', 'crop', self.rect)
        with Image.open(io.BytesIO(base64.b64decode(value['pngBase64']))) as derived:
            self.assertEqual(derived.info, {'srgb': 1})
            self.assertEqual(derived.tobytes(), bytes((17, 31, 47, 128))*6)
        self.assertEqual(value['sourceSha256'], hashlib.sha256(source).hexdigest())
        self.assertEqual(self.jobs._supervisor.active_count, 0)
        self.assertEqual(list(self.jobs.directory.iterdir()), [])

    def test_real_overview_worker_uses_exact_integer_geometry(self):
        source = png(2401, 2)
        value = self.jobs.preview(source, 'image_job_1', 'region_job_1', 'overview')
        self.assertEqual((value['width'], value['height']), (960, 1))
        self.assertEqual(value['rect'], {'x': 0, 'y': 0, 'width': 2401, 'height': 2})
        self.assertEqual(value['resampling'], 'nearest-center')
        self.assertIs(value['navigationOnly'], True)
        self.assertEqual(value['bufferAccounting']['filteredBytesProcessed'], (2401*4+1)*2)
        with Image.open(io.BytesIO(base64.b64decode(value['pngBase64']))) as derived:
            self.assertEqual(derived.tobytes(), bytes((17, 31, 47, 128))*960)
        self.assertEqual(self.jobs._supervisor.active_count, 0)
        self.assertEqual(list(self.jobs.directory.iterdir()), [])

    def test_real_worker_output_limit_and_timeout_remove_snapshots_after_exit(self):
        cases = ((WorkerOutputLimit, 'import sys;sys.stdin.buffer.read();sys.stdout.write("x"*2048)', 5),
                 (subprocess.TimeoutExpired, 'import time;time.sleep(30)', .15))
        for index, (error, script, timeout) in enumerate(cases):
            with self.subTest(error=error), patch('nivram.png_preview_jobs.MAX_STDOUT', 1024), self.assertRaises(error):
                self.jobs.preview(self.source, 'image_job_1', f'region_job_{index}', 'crop', self.rect,
                                  timeout=timeout, _command=[sys.executable, '-c', script])
            self.assertEqual(self.jobs._supervisor.active_count, 0)
            self.assertIsNone(self.jobs._active)
            self.assertEqual(list(self.jobs.directory.iterdir()), [])

    def test_real_running_worker_cancellation_reaps_child_and_removes_snapshot(self):
        outcome = {}
        def run():
            try:
                self.jobs.preview(self.source, 'image_job_1', 'region_job_1', 'crop', self.rect,
                                  _command=[sys.executable, '-c', 'import time;time.sleep(30)'])
            except BaseException as exc:
                outcome['error'] = exc
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            end = time.monotonic()+5
            while self.jobs._supervisor.active_count == 0 and thread.is_alive() and time.monotonic() < end:
                time.sleep(.01)
            self.assertEqual(self.jobs._supervisor.active_count, 1)
            self.jobs.cancel('image_job_1', 'region_job_1')
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertIsInstance(outcome.get('error'), WorkerCancelled)
            self.assertEqual(self.jobs._supervisor.active_count, 0)
            self.assertEqual(list(self.jobs.directory.iterdir()), [])
        finally:
            self.jobs.close()
            thread.join(3)

    def test_cleanup_failure_retains_unknown_file_and_releases_job_slot(self):
        retained = []
        def worker(command, **options):
            snapshot = Path(json.loads(options['input_text'])['sourcePath'])
            unknown = snapshot.parent / 'unknown.txt'
            unknown.write_bytes(b'not a source snapshot')
            retained.append(unknown)
            return subprocess.CompletedProcess(command, 0, json.dumps(result(self.source)), '')
        self.jobs._supervisor.run = Mock(side_effect=worker)
        with self.assertRaisesRegex(ValueError, 'cleanup could not be confirmed'):
            self.jobs.preview(self.source, 'image_job_1', 'region_job_1', 'crop', self.rect)
        self.assertIsNone(self.jobs._active)
        self.assertEqual(retained[0].read_bytes(), b'not a source snapshot')
        self.jobs._supervisor.run = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(result(self.source)), ''))
        self.assertEqual(self.jobs.preview(self.source, 'image_job_1', 'region_job_2', 'crop', self.rect)['width'], 3)


if __name__ == '__main__':
    unittest.main()
