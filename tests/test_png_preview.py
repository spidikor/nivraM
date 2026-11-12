"""Synthetic fixtures only; no private reference pixels are decoded here."""
import base64
import hashlib
import io
import struct
import unittest
from unittest.mock import patch
import zlib

from PIL import Image

from nivram.png_preview import PngPreviewError, preview_capability, preview_png


SIGNATURE = b'\x89PNG\r\n\x1a\n'


def chunk(kind, data):
    return (struct.pack('>I', len(data)) + kind + data
            + struct.pack('>I', zlib.crc32(data, zlib.crc32(kind))))


def filtered_row(row, previous, bpp, kind):
    encoded = bytearray(len(row))
    for index, value in enumerate(row):
        left = row[index-bpp] if index >= bpp else 0
        above = previous[index]
        upper_left = previous[index-bpp] if index >= bpp else 0
        if kind == 0:
            prediction = 0
        elif kind == 1:
            prediction = left
        elif kind == 2:
            prediction = above
        elif kind == 3:
            prediction = (left + above) // 2
        else:
            p = left + above - upper_left
            distances = (abs(p-left), abs(p-above), abs(p-upper_left))
            prediction = (left, above, upper_left)[distances.index(min(distances))]
        encoded[index] = (value - prediction) & 255
    return bytes([kind]) + encoded


def fixture(width=9, height=7, color=6, filters=(0, 1, 2, 3, 4), *, split=31):
    bpp = 4 if color == 6 else 3
    rows = [bytes((x*31 + y*47 + (x//bpp)*y*13) % 256
                  for x in range(width*bpp)) for y in range(height)]
    previous = bytes(width*bpp)
    raw = bytearray()
    for y, row in enumerate(rows):
        raw += filtered_row(row, previous, bpp, filters[y % len(filters)])
        previous = row
    compressed = zlib.compress(raw)
    header = struct.pack('>IIBBBBB', width, height, 8, color, 0, 0, 0)
    png = SIGNATURE + chunk(b'IHDR', header)
    png += b''.join(chunk(b'IDAT', compressed[i:i+split])
                    for i in range(0, len(compressed), split))
    return png + chunk(b'IEND', b''), b''.join(rows)


def decoded(result):
    data = base64.b64decode(result['pngBase64'], validate=True)
    assert hashlib.sha256(data).hexdigest() == result['pngSha256']
    with Image.open(io.BytesIO(data)) as image:
        return image.mode, image.size, image.tobytes(), image.info


def raw_png(width, height, raw, *, color=6, depth=8, interlace=0, before=b'', after=b'', compressed=None):
    header = struct.pack('>IIBBBBB', width, height, depth, color, 0, 0, interlace)
    encoded = zlib.compress(raw) if compressed is None else compressed
    return SIGNATURE + chunk(b'IHDR', header) + before + chunk(b'IDAT', encoded) + after + chunk(b'IEND', b'')


class PngPreviewTests(unittest.TestCase):
    def test_all_filters_rgb_rgba_match_independent_pixels_and_pillow_crop(self):
        for color in (2, 6):
            for kind in range(5):
                with self.subTest(color=color, filter=kind):
                    data, raw = fixture(color=color, filters=(kind,), split=1)
                    rect = {'x': 2, 'y': 1, 'width': 5, 'height': 4}
                    result = preview_png(data, mode='crop', rect=rect)
                    mode, size, pixels, info = decoded(result)
                    with Image.open(io.BytesIO(data)) as original:
                        expected = original.crop((2, 1, 7, 5)).tobytes()
                    self.assertEqual(pixels, expected)
                    bpp = 4 if color == 6 else 3
                    expected_raw = b''.join(raw[(y*9+2)*bpp:(y*9+7)*bpp] for y in range(1, 5))
                    self.assertEqual(pixels, expected_raw)
                    self.assertEqual(size, (5, 4))
                    self.assertEqual(mode, 'RGBA' if color == 6 else 'RGB')
                    self.assertEqual(info, {})
                    self.assertEqual(result['rect'], rect)
                    self.assertEqual(result['sourceSha256'], hashlib.sha256(data).hexdigest())

    def test_mixed_filter_overview_small_image_is_exact(self):
        data, expected = fixture()
        result = preview_png(data, mode='overview')
        self.assertEqual(decoded(result)[2], expected)
        self.assertTrue(result['navigationOnly'])

    def test_one_pixel_and_boundary_crops_preserve_alpha(self):
        data, raw = fixture()
        for rect in ({'x': 0, 'y': 0, 'width': 1, 'height': 1},
                     {'x': 8, 'y': 6, 'width': 1, 'height': 1},
                     {'x': 0, 'y': 0, 'width': 9, 'height': 7}):
            result = preview_png(data, mode='crop', rect=rect)
            with Image.open(io.BytesIO(data)) as original:
                expected = original.crop((rect['x'], rect['y'], rect['x']+rect['width'], rect['y']+rect['height'])).tobytes()
            self.assertEqual(decoded(result)[2], expected)
            self.assertFalse(result['navigationOnly'])
            self.assertEqual(result['resampling'], 'none')

    def test_overview_bound_and_sampling_is_explicit_navigation_only(self):
        row = bytes((n % 256 for n in range(1920*4)))
        data = raw_png(1920, 2, b'\0'+row+b'\0'+row)
        result = preview_png(data, mode='overview')
        self.assertEqual((result['width'], result['height']), (960, 1))
        self.assertEqual(decoded(result)[2], b''.join(row[x*4:x*4+4] for x in range(1, 1920, 2)))
        self.assertEqual(result['resampling'], 'nearest-center')

    def test_overview_dimensions_use_exact_integer_scaling(self):
        # Floating division/multiplication previously truncated 1074*(960/1074)
        # to 959. Exact source-pixel mapping must not depend on that rounding.
        data = raw_png(1074, 1, b'\0' + bytes(1074*4))
        result = preview_png(data, mode='overview')
        self.assertEqual((result['width'], result['height']), (960, 1))

    def test_source_identity_mismatch_rejected(self):
        data, _ = fixture()
        with self.assertRaisesRegex(PngPreviewError, 'identity'):
            preview_png(data, mode='overview', expected_source_sha256='0'*64)

    def test_crc_and_truncation_and_trailing_file_bytes_rejected(self):
        data, _ = fixture()
        damaged = bytearray(data)
        damaged[29] ^= 1
        for bad in (bytes(damaged), data[:-1], data[:-12], data+b'x'):
            with self.subTest(length=len(bad)), self.assertRaises(PngPreviewError):
                preview_png(bad, mode='overview')

    def test_zlib_truncation_checksum_concat_and_exact_length_rejected(self):
        expected = b'\0\x01\x02\x03\x04'
        compressed = zlib.compress(expected)
        streams = (compressed[:-1], compressed[:-1]+bytes([compressed[-1]^1]),
                   compressed+zlib.compress(b''), compressed+b'x',
                   zlib.compress(expected[:-1]), zlib.compress(expected+b'\0'),
                   zlib.compress(expected+b'\0'*1000000))
        for stream in streams:
            with self.subTest(length=len(stream)), self.assertRaises(PngPreviewError):
                preview_png(raw_png(1, 1, b'', compressed=stream), mode='overview')

    def test_invalid_filter_rejected(self):
        with self.assertRaisesRegex(PngPreviewError, 'filter'):
            preview_png(raw_png(1, 1, b'\5\0\0\0\0'), mode='overview')

    def test_duplicate_header_nonconsecutive_idat_and_unknown_critical_rejected(self):
        header = chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
        compressed = zlib.compress(b'\0'*5)
        bad = (
            raw_png(1, 1, b'\0'*5, before=header),
            raw_png(1, 1, b'\0'*5, before=chunk(b'ABCD', b'')),
            SIGNATURE+header+chunk(b'IDAT', compressed[:2])+chunk(b'tEXt', b'a\0b')+chunk(b'IDAT', compressed[2:])+chunk(b'IEND', b''),
            raw_png(1, 1, b'\0'*5, before=chunk(b'abca', b'')),
        )
        for data in bad:
            with self.subTest(length=len(data)), self.assertRaises(PngPreviewError):
                preview_png(data, mode='overview')

    def test_unsupported_encoding_color_and_transform_chunks_are_unavailable(self):
        cases = [raw_png(1, 1, b'', color=color, depth=depth, interlace=interlace)
                 for color, depth, interlace in ((0, 3, 0), (3, 8, 0), (6, 4, 0))]
        cases += [raw_png(1, 1, b'\0'*5, before=chunk(kind, b''))
                  for kind in (b'tRNS', b'gAMA', b'cHRM', b'iCCP', b'sRGB', b'eXIf', b'acTL', b'zzZZ')]
        for data in cases:
            with self.subTest(data=data[24:29]):
                self.assertFalse(preview_capability(data)['available'])
                with self.assertRaises(PngPreviewError):
                    preview_png(data, mode='overview')

    def test_dimensions_source_chunk_count_and_crop_caps(self):
        for width, height in ((0, 1), (32769, 1), (1, 32769), (32768, 4000)):
            with self.subTest(width=width, height=height), self.assertRaises(PngPreviewError):
                preview_png(raw_png(width, height, b''), mode='overview')
        data, _ = fixture()
        for rect in ({'x': -1, 'y': 0, 'width': 1, 'height': 1},
                     {'x': 0, 'y': 0, 'width': 10, 'height': 7},
                     {'x': 0, 'y': 0, 'width': True, 'height': 1},
                     {'x': 0, 'y': 0, 'width': 1.0, 'height': 1},
                     {'x': 0, 'y': 0, 'width': 1, 'height': 1, 'extra': 0}):
            with self.subTest(rect=rect), self.assertRaises(PngPreviewError):
                preview_png(data, mode='crop', rect=rect)
        big_header = raw_png(2049, 2049, b'')
        for rect in ({'x': 0, 'y': 0, 'width': 2049, 'height': 1},
                     {'x': 0, 'y': 0, 'width': 1001, 'height': 1000}):
            with self.assertRaisesRegex(PngPreviewError, 'Crop'):
                preview_png(big_header, mode='crop', rect=rect)
        with patch('nivram.png_preview.MAX_SOURCE_BYTES', len(data)-1):
            with self.assertRaisesRegex(PngPreviewError, '64 MiB'):
                preview_png(data, mode='overview')
        with patch('nivram.png_preview.MAX_CHUNKS', 2):
            with self.assertRaisesRegex(PngPreviewError, 'count'):
                preview_png(data, mode='overview')

    def test_output_overflow_and_deadline_fail_without_result(self):
        data, _ = fixture()
        with patch('nivram.png_preview.MAX_PREVIEW_BYTES', 64):
            with self.assertRaisesRegex(PngPreviewError, 'output limit'):
                preview_png(data, mode='overview')
        with patch('nivram.png_preview.time.monotonic', side_effect=(0, 46)):
            with self.assertRaises(TimeoutError):
                preview_png(data, mode='overview')
        for deadline in (0, 46, float('nan'), float('inf'), True):
            with self.subTest(deadline=deadline), self.assertRaises(PngPreviewError):
                preview_png(data, mode='overview', deadline_seconds=deadline)

    def test_pillow_only_opens_two_row_carriers_and_no_metadata_is_copied(self):
        data, _ = fixture(width=13, height=17, filters=(3, 4))
        data = data[:-12]+chunk(b'tEXt', b'molSource\0private chemistry')+data[-12:]
        actual_open = Image.open
        sizes = []
        def observed_open(source):
            result = actual_open(source)
            sizes.append(result.size)
            return result
        with patch('nivram.png_preview.Image.open', side_effect=observed_open):
            result = preview_png(data, mode='crop', rect={'x': 0, 'y': 0, 'width': 3, 'height': 3})
        self.assertEqual(sizes, [(13, 2)]*17)
        self.assertEqual(decoded(result)[3], {})
        self.assertEqual(result['bufferAccounting']['filteredBytesProcessed'], (13*4+1)*17)
        self.assertLess(result['bufferAccounting']['pixelPipelineAccountedUpperBoundBytes'], 16*1024*1024)

    def test_capability_does_not_decode_pixels(self):
        data = raw_png(11559, 9999, b'', compressed=b'invalid')
        with patch('nivram.png_preview.zlib.decompressobj', side_effect=AssertionError('No decode')):
            self.assertEqual(preview_capability(data), {'available': True, 'reason': None, 'width': 11559, 'height': 9999})
        with self.assertRaises(PngPreviewError):
            preview_png(data, mode='overview')

    def test_real_decoder_output_survives_worker_result_validator(self):
        from nivram.png_preview_jobs import validate_preview_result
        data, _ = fixture()
        for mode, rect in (('overview', None), ('crop', {'x': 1, 'y': 2, 'width': 3, 'height': 4})):
            with self.subTest(mode=mode):
                result = preview_png(data, mode=mode, rect=rect)
                checked = validate_preview_result(result, hashlib.sha256(data).hexdigest(), mode, rect, (9, 7))
                self.assertEqual(checked['bufferAccounting'], result['bufferAccounting'])
                self.assertEqual(checked['pngBase64'], result['pngBase64'])


def benchmark_large_synthetic():
    """Explicit Windows benchmark; not part of routine unit-test discovery.

    Generates compressed fixtures a row at a time, never a full source raster.
    Samples PROCESS_MEMORY_COUNTERS_EX.PrivateUsage (not virtual size) at 5 ms.
    Sampling can miss brief peaks; the result is not a hard process-memory cap.
    """
    import ctypes
    from ctypes import wintypes
    import platform
    import threading
    import time
    from PIL import __version__ as pillow_version

    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('peak_working', 'working', 'peak_paged', 'paged',
                'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile', 'private')]

    get = ctypes.windll.psapi.GetProcessMemoryInfo
    get.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    get.restype = wintypes.BOOL
    current = ctypes.windll.kernel32.GetCurrentProcess
    current.restype = wintypes.HANDLE
    handle = current()

    def private_bytes():
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not get(handle, ctypes.byref(counters), counters.cb):
            raise ctypes.WinError()
        return counters.private

    width, height = 11559, 9999
    stride = width*4
    pattern = bytes((i*37+i//32*17) % 256 for i in range(8192))
    filtered = (pattern*((stride+8191)//8192))[:stride]
    cases = [('mixed', (0, 1, 2, 3, 4), 'overview', None),
             ('average', (3,), 'overview', None),
             ('paeth', (4,), 'crop', {'x': 3000, 'y': 8999, 'width': 1000, 'height': 1000})]
    results = []
    for label, filters, mode, rect in cases:
        encoder = zlib.compressobj(6)
        source = bytearray(SIGNATURE+chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)))
        for y in range(height):
            part = encoder.compress(bytes([filters[y % len(filters)]])+filtered)
            if part:
                source.extend(chunk(b'IDAT', part))
        part = encoder.flush()
        if part:
            source.extend(chunk(b'IDAT', part))
        source.extend(chunk(b'IEND', b''))
        source = bytes(source)
        baseline = private_bytes()
        peak = [baseline]
        done = threading.Event()

        def sample():
            while not done.wait(.005):
                peak[0] = max(peak[0], private_bytes())

        monitor = threading.Thread(target=sample)
        monitor.start()
        start = time.perf_counter()
        try:
            result = preview_png(source, mode=mode, rect=rect, deadline_seconds=40,
                                 expected_source_sha256=hashlib.sha256(source).hexdigest())
        finally:
            elapsed = time.perf_counter()-start
            peak[0] = max(peak[0], private_bytes())
            done.set()
            monitor.join()
        if elapsed >= 40:
            raise AssertionError('Synthetic PNG processing exceeded its 40-second deadline.')
        results.append({'fixture': label, 'sourceDimensions': [width, height],
                        'sourceBytes': len(source), 'sourceSha256': hashlib.sha256(source).hexdigest(),
                        'mode': mode, 'rect': result['rect'], 'outputDimensions': [result['width'], result['height']],
                        'outputSha256': result['pngSha256'], 'elapsedSeconds': elapsed, 'deadlineSeconds': 40,
                        'privateBytesBaseline': baseline, 'privateBytesSampledPeak': peak[0],
                        'privateBytesSampledDelta': peak[0]-baseline, 'samplingIntervalMs': 5,
                        'bufferAccounting': result['bufferAccounting']})
        del source, result
    return {'schemaVersion': 1, 'status': 'passed', 'python': platform.python_version(),
            'pillow': pillow_version, 'zlib': zlib.ZLIB_RUNTIME_VERSION, 'cases': results,
            'memoryMethod': 'Windows GetProcessMemoryInfo PROCESS_MEMORY_COUNTERS_EX.PrivateUsage, sampled; not a hard upper bound.',
            'limits': 'Synthetic source only; no GUI, worker supervisor, packaged route, private corpus, OCR or recognition accuracy claim.'}


if __name__ == '__main__':
    unittest.main()
