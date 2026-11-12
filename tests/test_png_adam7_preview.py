"""Authored Adam7 RGB8 fixtures; no production decoder supplies the oracle.

Safe offline after the parent gate: all tests in this module use small synthetic
in-memory PNGs, local Pillow/zlib, and scoped mocks. No files, network, GUI,
worker processes, vendor imports, or application startup are required.
Initial red selection:
    PngAdam7PreviewTests.test_tiny_literal_adam7_rgb8_is_available_and_exact

The 8x8 pass-label map and filter encoder below are fixture code, independent
of nivram.png_preview. Expected pixels never come from production helpers.
Pillow reads generated previews; the initial tiny fixture alone is also checked
with Pillow to distinguish a malformed fixture from unsupported functionality.
"""
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
# Each source coordinate belongs to exactly this pass, repeated every 8 pixels.
# This pixel-ownership map is deliberately not the decoder's stride algorithm.
PASS_LABELS = (
    (1, 6, 4, 6, 2, 6, 4, 6),
    (7, 7, 7, 7, 7, 7, 7, 7),
    (5, 6, 5, 6, 5, 6, 5, 6),
    (7, 7, 7, 7, 7, 7, 7, 7),
    (3, 6, 4, 6, 3, 6, 4, 6),
    (7, 7, 7, 7, 7, 7, 7, 7),
    (5, 6, 5, 6, 5, 6, 5, 6),
    (7, 7, 7, 7, 7, 7, 7, 7),
)


def chunk(kind, payload):
    return (struct.pack('>I', len(payload)) + kind + payload
            + struct.pack('>I', zlib.crc32(kind + payload)))


def png(width, height, raw, *, color=2, depth=8, before=b'', after=b'',
        stream=None, split=7, interlace=1):
    header = struct.pack('>IIBBBBB', width, height, depth, color, 0, 0, interlace)
    compressed = zlib.compress(raw) if stream is None else stream
    image_data = b''.join(chunk(b'IDAT', compressed[start:start+split])
                          for start in range(0, len(compressed), split))
    return (SIGNATURE + chunk(b'IHDR', header) + before + image_data
            + after + chunk(b'IEND', b''))


def filtered(row, previous, kind, bpp=3):
    """Encode PNG byte predictors independently; RGB defaults to byte stride 3."""
    encoded = bytearray([kind])
    for index, value in enumerate(row):
        left = row[index-bpp] if index >= bpp else 0
        up = previous[index]
        corner = previous[index-bpp] if index >= bpp else 0
        if kind == 0:
            predictor = 0
        elif kind == 1:
            predictor = left
        elif kind == 2:
            predictor = up
        elif kind == 3:
            predictor = (left + up)//2
        elif kind == 4:
            estimate = left + up - corner
            a, b, c = abs(estimate-left), abs(estimate-up), abs(estimate-corner)
            predictor = left if a <= b and a <= c else up if b <= c else corner
        else:
            raise ValueError('Fixture filter must be in 0..4.')
        encoded.append((value-predictor) % 256)
    return bytes(encoded)


def adam7_raw(rows, filters=(0, 1, 2, 3, 4), *, per_pass=None, bpp=3):
    """Return scanlines and (pass, full_y, filter_offset, pixel_count) facts."""
    width = len(rows[0])
    output, facts = bytearray(), []
    for pass_number in range(1, 8):
        previous = None
        pass_row = 0
        for y, source_row in enumerate(rows):
            selected = [source_row[x] for x in range(width)
                        if PASS_LABELS[y % 8][x % 8] == pass_number]
            if not selected:
                continue  # Empty passes have no row and no filter byte.
            row = bytes(channel for pixel in selected for channel in pixel)
            if previous is None:
                previous = bytes(len(row))  # Reset separately for each pass.
            schedule = filters if per_pass is None else per_pass[pass_number]
            kind = schedule[pass_row % len(schedule)]
            facts.append((pass_number, y, len(output), len(selected)))
            output.extend(filtered(row, previous, kind, bpp=bpp))
            previous = row
            pass_row += 1
    return bytes(output), facts


def coordinate_rows(width, height):
    return [[(x, y, (16*y+x) % 256) for x in range(width)] for y in range(height)]


def varied_rows(width, height):
    # Wraps byte differences and provides irregular left/up/upper-left choices.
    return [[((17*x+29*y+3*x*y+11) % 256,
              (251-13*x+47*y+5*x*y) % 256,
              (61*x+7*y+x*y+19) % 256)
             for x in range(width)] for y in range(height)]


def pixel_bytes(rows):
    return bytes(channel for row in rows for pixel in row for channel in pixel)


def decoded(result):
    payload = base64.b64decode(result['pngBase64'], validate=True)
    if hashlib.sha256(payload).hexdigest() != result['pngSha256']:
        raise AssertionError('Derived PNG digest does not match its bytes.')
    with Image.open(io.BytesIO(payload)) as image:
        return image.size, image.mode, image.tobytes(), dict(image.info), payload


class PngAdam7PreviewTests(unittest.TestCase):
    def assert_pixels(self, data, expected, *, mode='overview', rect=None):
        result = preview_png(data, mode=mode, rect=rect)
        size, output_mode, pixels, metadata, payload = decoded(result)
        self.assertEqual(size, (len(expected[0]), len(expected)))
        self.assertEqual(output_mode, 'RGB' if len(expected[0][0]) == 3 else 'RGBA')
        self.assertEqual(pixels, pixel_bytes(expected))
        self.assertEqual(metadata, {})
        self.assertEqual(payload[28], 0)  # Derivative is non-interlaced.
        self.assertEqual(result['sourceSha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(result['navigationOnly'], mode == 'overview')
        self.assertEqual(result['resampling'], 'nearest-center' if mode == 'overview' else 'none')
        return result

    def test_tiny_literal_adam7_rgb8_is_available_and_exact(self):
        # 2x2: pass 1 is (0,0), pass 6 is (1,0), pass 7 is the bottom row.
        # Passes 2..5 are empty. Each visible pass row has literal filter None.
        raw = (b'\x00\x0a\x14\x1e'
               b'\x00\x28\x32\x3c'
               b'\x00\x46\x50\x5a\x64\x6e\x78')
        expected = [[(10, 20, 30), (40, 50, 60)],
                    [(70, 80, 90), (100, 110, 120)]]
        data = png(2, 2, raw, split=1)
        with Image.open(io.BytesIO(data)) as independent:
            self.assertEqual(independent.size, (2, 2))
            self.assertEqual(independent.mode, 'RGB')
            self.assertEqual(independent.tobytes(), pixel_bytes(expected))
        self.assertEqual(preview_capability(data),
                         {'available': True, 'reason': None, 'width': 2, 'height': 2})
        self.assert_pixels(data, expected)

    def test_all_five_filters_in_every_pass_reset_previous_rows(self):
        rows = varied_rows(17, 17)
        for kind in range(5):
            with self.subTest(filter=kind):
                raw, facts = adam7_raw(rows, (kind,))
                self.assertEqual({item[0] for item in facts}, set(range(1, 8)))
                self.assertTrue(all(sum(item[0] == p for item in facts) >= 2
                                    for p in range(1, 8)))
                result = self.assert_pixels(png(17, 17, raw, split=1), rows)
                self.assertEqual(result['bufferAccounting']['filteredBytesProcessed'], len(raw))

    def test_mixed_filters_exact_literal_crop_and_nearest_center_overview(self):
        rows = coordinate_rows(13, 9)
        schedule = {1: (0, 1), 2: (2, 4), 3: (3,), 4: (4, 2, 3),
                    5: (2, 4), 6: (3, 0, 1, 2, 4), 7: (4, 3, 2, 1)}
        raw, _ = adam7_raw(rows, per_pass=schedule)
        data = png(13, 9, raw)
        rect = {'x': 2, 'y': 3, 'width': 3, 'height': 2}
        expected_crop = [[(2, 3, 50), (3, 3, 51), (4, 3, 52)],
                         [(2, 4, 66), (3, 4, 67), (4, 4, 68)]]
        result = self.assert_pixels(data, expected_crop, mode='crop', rect=rect)
        self.assertEqual(result['rect'], rect)
        self.assertEqual((result['sourceWidth'], result['sourceHeight']), (13, 9))
        # 13x9 -> 5x3: independently enumerated centers x=1,3,6,9,11; y=1,4,7.
        expected_overview = [
            [(1, 1, 17), (3, 1, 19), (6, 1, 22), (9, 1, 25), (11, 1, 27)],
            [(1, 4, 65), (3, 4, 67), (6, 4, 70), (9, 4, 73), (11, 4, 75)],
            [(1, 7, 113), (3, 7, 115), (6, 7, 118), (9, 7, 121), (11, 7, 123)],
        ]
        with patch('nivram.png_preview.OVERVIEW_EDGE', 5):
            self.assert_pixels(data, expected_overview)

    def test_small_odd_dimensions_empty_passes_and_boundary_crops(self):
        for width, height in ((1, 1), (1, 3), (3, 1), (1, 9), (9, 1),
                              (2, 2), (3, 3), (3, 5), (5, 3), (8, 8), (9, 9)):
            with self.subTest(size=(width, height)):
                rows = varied_rows(width, height)
                raw, facts = adam7_raw(rows, (4, 2, 3, 1, 0))
                self.assertEqual(sum(item[3] for item in facts), width*height)
                if (width, height) == (1, 1):
                    self.assertEqual([item[0] for item in facts], [1])
                    self.assertEqual(len(raw), 4)
                data = png(width, height, raw, split=1)
                self.assert_pixels(data, rows)
                rect = {'x': width-1, 'y': height-1, 'width': 1, 'height': 1}
                self.assert_pixels(data, [[rows[-1][-1]]], mode='crop', rect=rect)

    def test_rgb_transparency_preserves_hidden_rgb_and_existing_key_masking(self):
        rows = [[(17, 83, 249), (18, 83, 249), (17, 84, 249)],
                [(17, 83, 248), (17, 83, 249), (9, 8, 7)],
                [(17, 83, 249), (1, 2, 3), (17, 83, 249)]]
        raw, _ = adam7_raw(rows, (4, 2, 3))
        expected = [[pixel + (0 if pixel == (17, 83, 249) else 255,)
                     for pixel in row] for row in rows]
        for high in (0, 0xFF00):
            with self.subTest(high=high):
                key = struct.pack('>HHH', 17 | high, 83 | high, 249 | high)
                self.assert_pixels(png(3, 3, raw, before=chunk(b'tRNS', key)), expected)

    def test_exact_complete_stream_and_checksum_required(self):
        raw, _ = adam7_raw(varied_rows(13, 9), (4, 2, 3))
        valid = zlib.compress(raw)
        bad_adler = valid[:-1] + bytes([valid[-1] ^ 1])
        cases = {
            'short decoded data': zlib.compress(raw[:-1]),
            'excess decoded byte': zlib.compress(raw+b'\0'),
            'excess decoded scanline': zlib.compress(raw+b'\0'+bytes(13*3)),
            'truncated zlib trailer': valid[:-1],
            'bad zlib checksum': bad_adler,
            'concatenated empty stream': valid+zlib.compress(b''),
            'concatenated nonempty stream': valid+zlib.compress(b'extra'),
            'compressed suffix': valid+b'suffix',
        }
        for name, stream in cases.items():
            with self.subTest(case=name), self.assertRaises(PngPreviewError):
                preview_png(png(13, 9, b'', stream=stream, split=1), mode='crop',
                            rect={'x': 0, 'y': 0, 'width': 1, 'height': 1})

    def test_invalid_filter_is_checked_in_every_pass_outside_selected_crop(self):
        raw, facts = adam7_raw(varied_rows(13, 9), (0,))
        for pass_number in range(1, 8):
            # Last row of each pass misses source (0,0), the only selected pixel.
            fact = [item for item in facts if item[0] == pass_number][-1]
            self.assertNotEqual((pass_number, fact[1]), (1, 0))
            corrupt = bytearray(raw)
            corrupt[fact[2]] = 5
            with self.subTest(pass_number=pass_number), self.assertRaisesRegex(PngPreviewError, 'filter'):
                preview_png(png(13, 9, bytes(corrupt)), mode='crop',
                            rect={'x': 0, 'y': 0, 'width': 1, 'height': 1})

    def test_invalid_filter_is_checked_outside_downsampled_overview_rows(self):
        raw, facts = adam7_raw(coordinate_rows(13, 9), (0,))
        # Pass 7 row y=5 is omitted from overview rows 1,4,7.
        target = next(item for item in facts if item[0] == 7 and item[1] == 5)
        corrupt = bytearray(raw)
        corrupt[target[2]] = 255
        with patch('nivram.png_preview.OVERVIEW_EDGE', 5), self.assertRaisesRegex(PngPreviewError, 'filter'):
            preview_png(png(13, 9, bytes(corrupt)), mode='overview')

    def test_crc_file_truncation_and_trailing_bytes_are_rejected(self):
        raw, _ = adam7_raw(varied_rows(3, 3))
        data = png(3, 3, raw, split=65536)
        bad_crc = bytearray(data)
        # First IDAT payload begins at byte 41; retain its old CRC deliberately.
        self.assertEqual(data[37:41], b'IDAT')
        bad_crc[41] ^= 1
        for damaged in (bytes(bad_crc), data[:-1], data+b'extra'):
            with self.subTest(kind=damaged[-12:]), self.assertRaises(PngPreviewError):
                preview_png(damaged, mode='overview')

    def test_cumulative_pass_filtered_bytes_limit_is_exact_before_decode(self):
        rows = coordinate_rows(13, 9)
        raw, facts = adam7_raw(rows, (0,))
        # 117 RGB pixels + 19 pass scanline bytes. Ordinary rows would total 360.
        self.assertEqual(len(facts), 19)
        self.assertEqual(len(raw), 370)
        data = png(13, 9, raw)
        for limit in (360, 369):
            with self.subTest(limit=limit), patch('nivram.png_preview.MAX_FILTERED_BYTES', limit):
                with patch('nivram.png_preview.zlib.decompressobj',
                           side_effect=AssertionError('Must reject before decode')):
                    self.assertFalse(preview_capability(data)['available'])
                    with self.assertRaisesRegex(PngPreviewError, 'filtered'):
                        preview_png(data, mode='overview')
        with patch('nivram.png_preview.MAX_FILTERED_BYTES', 370):
            result = self.assert_pixels(data, rows)
            self.assertEqual(result['bufferAccounting']['filteredBytesProcessed'], 370)

    def test_decoder_requests_bounded_output_without_production_pillow_decode(self):
        rows = varied_rows(13, 9)
        raw, _ = adam7_raw(rows, (4,))
        data = png(13, 9, raw, split=1)
        actual_decoder = zlib.decompressobj
        requests = []
        test = self

        class ObservedDecoder:
            def __init__(self):
                self.inner = actual_decoder()

            def decompress(self, compressed, max_length=0):
                test.assertGreater(max_length, 0)
                test.assertLessEqual(max_length, 13*3+1)
                requests.append(max_length)
                output = self.inner.decompress(compressed, max_length)
                test.assertLessEqual(len(output), max_length)
                return output

            def __getattr__(self, name):
                return getattr(self.inner, name)

        with patch('nivram.png_preview.zlib.decompressobj', side_effect=ObservedDecoder), \
             patch('nivram.png_preview.Image.open', side_effect=AssertionError('Adam7 must not use Pillow')):
            result = preview_png(data, mode='crop',
                                 rect={'x': 2, 'y': 3, 'width': 3, 'height': 2})
        self.assertTrue(requests)
        self.assertEqual(decoded(result)[2], pixel_bytes([row[2:5] for row in rows[3:5]]))
        self.assertEqual(result['bufferAccounting']['maxCarrierHeight'], 0)
        self.assertLessEqual(result['bufferAccounting']['pixelPipelineAccountedUpperBoundBytes'],
                             result['bufferAccounting']['pixelPipelineBudgetBytes'])

    def test_limits_and_cooperative_deadline_are_preserved(self):
        raw, _ = adam7_raw(varied_rows(13, 9), (4,))
        data = png(13, 9, raw)
        for name, value in (('MAX_SOURCE_BYTES', len(data)-1), ('MAX_CHUNKS', 2),
                            ('PIXEL_PIPELINE_BUDGET', 1), ('MAX_PREVIEW_BYTES', 64),
                            ('MAX_PIXELS', 116), ('MAX_EDGE', 12)):
            with self.subTest(limit=name), patch('nivram.png_preview.'+name, value), \
                 self.assertRaises(PngPreviewError):
                preview_png(data, mode='overview')
        early_clock = [0.0]

        def expire_after_start():
            value = early_clock[0]
            early_clock[0] = 2.0
            return value

        with patch('nivram.png_preview.time.monotonic', side_effect=expire_after_start), \
             self.assertRaises(TimeoutError):
            preview_png(data, mode='overview', deadline_seconds=1)
        # Expire after real decompression begins, not just during header parsing.
        actual_decoder = zlib.decompressobj
        clock, calls = [0.0], []

        class ExpiringDecoder:
            def __init__(self):
                self.inner = actual_decoder()

            def decompress(self, compressed, max_length=0):
                calls.append(max_length)
                output = self.inner.decompress(compressed, max_length)
                clock[0] = 100.0
                return output

            def __getattr__(self, name):
                return getattr(self.inner, name)

        with patch('nivram.png_preview.time.monotonic', side_effect=lambda: clock[0]), \
             patch('nivram.png_preview.zlib.decompressobj', side_effect=ExpiringDecoder), \
             self.assertRaises(TimeoutError):
            preview_png(data, mode='overview', deadline_seconds=1)
        self.assertTrue(calls)

    def test_illegal_adam7_depths_and_method_are_rejected(self):
        encodings = [(0, 3), (2, 4), (3, 16), (4, 4), (6, 4)]
        for color, depth in encodings:
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
            raw = b'\0' + bytes((channels*depth+7)//8)
            before = chunk(b'PLTE', b'\x11\x22\x33') if color == 3 else b''
            data = png(1, 1, raw, color=color, depth=depth, before=before)
            with self.subTest(color=color, depth=depth):
                self.assertFalse(preview_capability(data)['available'])
                with self.assertRaises(PngPreviewError):
                    preview_png(data, mode='overview')
        with self.assertRaises(PngPreviewError):
            preview_png(png(1, 1, b'\0\1\2\3', interlace=2), mode='overview')

    def test_source_identity_metadata_and_nonmutation_survive_both_modes(self):
        rows = varied_rows(13, 9)
        raw, _ = adam7_raw(rows)
        data = png(13, 9, raw, before=chunk(b'tEXt', b'private\0must not survive'))
        original = bytes(data)
        digest = hashlib.sha256(original).hexdigest()
        rect = {'x': 2, 'y': 3, 'width': 3, 'height': 2}
        for mode, rectangle in (('overview', None), ('crop', rect)):
            with self.subTest(mode=mode):
                result = preview_png(data, mode=mode, rect=rectangle,
                                     expected_source_sha256=digest)
                expected = rows if rectangle is None else [row[2:5] for row in rows[3:5]]
                size, output_mode, pixels, metadata, payload = decoded(result)
                self.assertEqual(pixels, pixel_bytes(expected))
                self.assertEqual(size, (len(expected[0]), len(expected)))
                self.assertEqual(output_mode, 'RGB')
                self.assertEqual(metadata, {})
                self.assertNotIn(b'must not survive', payload)
                self.assertEqual(result['sourceSha256'], digest)
                self.assertEqual(result['bufferAccounting']['sourceSnapshotBytes'], len(original))
                self.assertEqual(data, original)
        with self.assertRaisesRegex(PngPreviewError, 'identity'):
            preview_png(data, mode='overview', expected_source_sha256='0'*64)
        self.assertEqual(data, original)

    def test_capability_does_not_decode_compressed_source(self):
        data = png(13, 9, b'', stream=b'not a zlib stream')
        with patch('nivram.png_preview.zlib.decompressobj',
                   side_effect=AssertionError('Capability must not decode')):
            self.assertEqual(preview_capability(data),
                             {'available': True, 'reason': None, 'width': 13, 'height': 9})


    def test_rgba8_literal_fixture_is_pillow_valid_then_preview_available_and_exact(self):
        # Literal 2x2 Adam7: pass 1 top-left, pass 6 top-right, pass 7 bottom row.
        # Alpha zero must retain hidden RGB; partial alpha is not composited.
        raw = (b'\x00\x0a\x14\x1e\x00'
               b'\x00\x28\x32\x3c\x80'
               b'\x00\x46\x50\x5a\xff\x64\x6e\x78\x40')
        expected = [[(10, 20, 30, 0), (40, 50, 60, 128)],
                    [(70, 80, 90, 255), (100, 110, 120, 64)]]
        self.assertEqual(len(raw), 19)
        data = png(2, 2, raw, color=6, split=1)
        # Independent fixture proof happens before the missing-feature assertion.
        with Image.open(io.BytesIO(data)) as independent:
            self.assertEqual(independent.size, (2, 2))
            self.assertEqual(independent.mode, 'RGBA')
            self.assertEqual(independent.tobytes(), pixel_bytes(expected))
        self.assertEqual(preview_capability(data),
                         {'available': True, 'reason': None, 'width': 2, 'height': 2})
        self.assert_pixels(data, expected)

    def test_rgba8_all_five_filters_every_pass_preserve_unassociated_alpha(self):
        rgb_rows = varied_rows(17, 17)
        rows = [[pixel + ((0, 1, 64, 128, 254, 255)[(x+3*y) % 6],)
                 for x, pixel in enumerate(row)] for y, row in enumerate(rgb_rows)]
        for kind in range(5):
            with self.subTest(filter=kind):
                raw, facts = adam7_raw(rows, (kind,), bpp=4)
                self.assertEqual({item[0] for item in facts}, set(range(1, 8)))
                self.assertTrue(all(sum(item[0] == p for item in facts) >= 2
                                    for p in range(1, 8)))
                result = self.assert_pixels(png(17, 17, raw, color=6, split=1), rows)
                self.assertEqual(result['bufferAccounting']['filteredBytesProcessed'], len(raw))
                self.assertEqual(result['bufferAccounting']['outputPixelBytes'], 17*17*4)

    def test_rgba8_small_empty_passes_and_boundary_crops_keep_all_four_bytes(self):
        for width, height in ((1, 1), (1, 3), (3, 1), (1, 9), (9, 1),
                              (2, 2), (3, 3), (3, 5), (5, 3), (9, 9)):
            with self.subTest(size=(width, height)):
                rgb_rows = varied_rows(width, height)
                rows = [[pixel + ((0, 128, 255, 64)[(x+y) % 4],)
                         for x, pixel in enumerate(row)] for y, row in enumerate(rgb_rows)]
                raw, facts = adam7_raw(rows, (4, 2, 3, 1, 0), bpp=4)
                self.assertEqual(sum(item[3] for item in facts), width*height)
                if (width, height) == (1, 1):
                    self.assertEqual([item[0] for item in facts], [1])
                    self.assertEqual(len(raw), 5)
                data = png(width, height, raw, color=6, split=1)
                self.assert_pixels(data, rows)
                rect = {'x': width-1, 'y': height-1, 'width': 1, 'height': 1}
                self.assert_pixels(data, [[rows[-1][-1]]], mode='crop', rect=rect)

    def test_rgba8_literal_crop_and_nearest_center_overview_do_not_blend_alpha(self):
        rows = [[pixel + ((0, 128, 255, 64)[(x+y) % 4],)
                 for x, pixel in enumerate(row)]
                for y, row in enumerate(coordinate_rows(13, 9))]
        schedule = {1: (0, 1), 2: (2, 4), 3: (3,), 4: (4, 2, 3),
                    5: (2, 4), 6: (3, 0, 1, 2, 4), 7: (4, 3, 2, 1)}
        raw, _ = adam7_raw(rows, per_pass=schedule, bpp=4)
        data = png(13, 9, raw, color=6)
        rect = {'x': 2, 'y': 3, 'width': 3, 'height': 2}
        expected_crop = [
            [(2, 3, 50, 128), (3, 3, 51, 255), (4, 3, 52, 64)],
            [(2, 4, 66, 255), (3, 4, 67, 64), (4, 4, 68, 0)],
        ]
        result = self.assert_pixels(data, expected_crop, mode='crop', rect=rect)
        self.assertEqual(result['rect'], rect)
        self.assertEqual((result['sourceWidth'], result['sourceHeight']), (13, 9))
        # Source columns 1,3,6,9,11 and rows 1,4,7; literal unassociated RGBA.
        expected_overview = [
            [(1, 1, 17, 255), (3, 1, 19, 0), (6, 1, 22, 64),
             (9, 1, 25, 255), (11, 1, 27, 0)],
            [(1, 4, 65, 128), (3, 4, 67, 64), (6, 4, 70, 255),
             (9, 4, 73, 128), (11, 4, 75, 64)],
            [(1, 7, 113, 0), (3, 7, 115, 255), (6, 7, 118, 128),
             (9, 7, 121, 0), (11, 7, 123, 255)],
        ]
        with patch('nivram.png_preview.OVERVIEW_EDGE', 5):
            self.assert_pixels(data, expected_overview)

    def test_rgba8_full_stream_errors_and_unselected_pass_filters_are_rejected(self):
        empty_image = png(1, 1, b'', color=6)
        self.assertEqual(preview_capability(empty_image),
                         {'available': True, 'reason': None, 'width': 1, 'height': 1})
        with self.assertRaises(PngPreviewError):
            preview_png(empty_image, mode='overview')
        rows = [[pixel + ((0, 128, 255, 64)[(x+y) % 4],)
                 for x, pixel in enumerate(row)]
                for y, row in enumerate(varied_rows(13, 9))]
        raw, facts = adam7_raw(rows, (0,), bpp=4)
        valid = zlib.compress(raw)
        bad_adler = valid[:-1] + bytes([valid[-1] ^ 1])
        streams = {
            'short decoded data': zlib.compress(raw[:-1]),
            'excess decoded byte': zlib.compress(raw+b'\0'),
            'excess RGBA scanline': zlib.compress(raw+b'\0'+bytes(13*4)),
            'truncated zlib trailer': valid[:-1],
            'bad zlib checksum': bad_adler,
            'concatenated empty stream': valid+zlib.compress(b''),
            'concatenated nonempty stream': valid+zlib.compress(b'extra'),
            'compressed suffix': valid+b'suffix',
        }
        rect = {'x': 0, 'y': 0, 'width': 1, 'height': 1}
        for name, stream in streams.items():
            with self.subTest(stream=name), self.assertRaises(PngPreviewError):
                preview_png(png(13, 9, b'', color=6, stream=stream, split=1),
                            mode='crop', rect=rect)
        for pass_number in range(1, 8):
            fact = [item for item in facts if item[0] == pass_number][-1]
            self.assertNotEqual((pass_number, fact[1]), (1, 0))
            corrupt = bytearray(raw)
            corrupt[fact[2]] = 5
            with self.subTest(pass_number=pass_number), self.assertRaisesRegex(PngPreviewError, 'filter'):
                preview_png(png(13, 9, bytes(corrupt), color=6), mode='crop', rect=rect)
        unselected = next(item for item in facts if item[0] == 7 and item[1] == 5)
        corrupt = bytearray(raw)
        corrupt[unselected[2]] = 255
        with patch('nivram.png_preview.OVERVIEW_EDGE', 5), self.assertRaisesRegex(PngPreviewError, 'filter'):
            preview_png(png(13, 9, bytes(corrupt), color=6), mode='overview')
        data = png(13, 9, raw, color=6, split=65536)
        bad_crc = bytearray(data)
        self.assertEqual(data[37:41], b'IDAT')
        bad_crc[41] ^= 1
        for name, damaged in (('CRC', bytes(bad_crc)), ('file truncation', data[:-1]),
                              ('file suffix', data+b'extra'),
                              ('RGBA tRNS', png(13, 9, raw, color=6,
                                               before=chunk(b'tRNS', bytes(6))))):
            with self.subTest(file=name), self.assertRaises(PngPreviewError):
                preview_png(damaged, mode='crop', rect=rect)

    def test_rgba8_four_byte_pass_budget_and_bounded_decode_without_pillow(self):
        rows = [[pixel + ((0, 128, 255, 64)[(x+y) % 4],)
                 for x, pixel in enumerate(row)]
                for y, row in enumerate(coordinate_rows(13, 9))]
        raw, facts = adam7_raw(rows, (4,), bpp=4)
        self.assertEqual(len(facts), 19)
        self.assertEqual(len(raw), 487)  # 13*9*4 data bytes + 19 pass filter bytes.
        data = png(13, 9, raw, color=6, split=1)
        for limit in (477, 486):  # 477 would count ordinary rows, losing pass bytes.
            with self.subTest(limit=limit), patch('nivram.png_preview.MAX_FILTERED_BYTES', limit):
                with patch('nivram.png_preview.zlib.decompressobj',
                           side_effect=AssertionError('Must reject before decode')):
                    self.assertFalse(preview_capability(data)['available'])
                    with self.assertRaisesRegex(PngPreviewError, 'filtered'):
                        preview_png(data, mode='overview')
        actual_decoder = zlib.decompressobj
        requests = []
        test = self

        class ObservedDecoder:
            def __init__(self):
                self.inner = actual_decoder()

            def decompress(self, compressed, max_length=0):
                test.assertGreater(max_length, 0)
                test.assertLessEqual(max_length, 13*4+1)
                requests.append(max_length)
                output = self.inner.decompress(compressed, max_length)
                test.assertLessEqual(len(output), max_length)
                return output

            def __getattr__(self, name):
                return getattr(self.inner, name)

        with patch('nivram.png_preview.MAX_FILTERED_BYTES', 487), \
             patch('nivram.png_preview.zlib.decompressobj', side_effect=ObservedDecoder), \
             patch('nivram.png_preview.Image.open', side_effect=AssertionError('Adam7 must not use Pillow')):
            result = preview_png(data, mode='crop',
                                 rect={'x': 2, 'y': 3, 'width': 3, 'height': 2})
        self.assertTrue(requests)
        self.assertEqual(decoded(result)[2], pixel_bytes([row[2:5] for row in rows[3:5]]))
        accounting = result['bufferAccounting']
        self.assertEqual(accounting['filteredBytesProcessed'], 487)
        self.assertEqual(accounting['sourceRowBytes'], 13*4)
        self.assertEqual(accounting['outputPixelBytes'], 3*2*4)
        self.assertEqual(accounting['maxCarrierHeight'], 0)
        self.assertLessEqual(accounting['pixelPipelineAccountedUpperBoundBytes'],
                             accounting['pixelPipelineBudgetBytes'])

    def test_rgba8_existing_limits_and_cooperative_deadline_remain_effective(self):
        rows = [[pixel + ((0, 128, 255, 64)[(x+y) % 4],)
                 for x, pixel in enumerate(row)]
                for y, row in enumerate(varied_rows(13, 9))]
        raw, _ = adam7_raw(rows, (4,), bpp=4)
        data = png(13, 9, raw, color=6)
        for name, value in (('MAX_SOURCE_BYTES', len(data)-1), ('MAX_CHUNKS', 2),
                            ('PIXEL_PIPELINE_BUDGET', 1), ('MAX_PREVIEW_BYTES', 64),
                            ('MAX_PIXELS', 116), ('MAX_EDGE', 12)):
            with self.subTest(limit=name), patch('nivram.png_preview.'+name, value), \
                 self.assertRaises(PngPreviewError):
                preview_png(data, mode='overview')
        early_clock = [0.0]

        def expire_after_start():
            value = early_clock[0]
            early_clock[0] = 2.0
            return value

        with patch('nivram.png_preview.time.monotonic', side_effect=expire_after_start), \
             self.assertRaises(TimeoutError):
            preview_png(data, mode='overview', deadline_seconds=1)
        actual_decoder = zlib.decompressobj
        clock, calls = [0.0], []

        class ExpiringDecoder:
            def __init__(self):
                self.inner = actual_decoder()

            def decompress(self, compressed, max_length=0):
                calls.append(max_length)
                output = self.inner.decompress(compressed, max_length)
                clock[0] = 100.0
                return output

            def __getattr__(self, name):
                return getattr(self.inner, name)

        with patch('nivram.png_preview.time.monotonic', side_effect=lambda: clock[0]), \
             patch('nivram.png_preview.zlib.decompressobj', side_effect=ExpiringDecoder), \
             self.assertRaises(TimeoutError):
            preview_png(data, mode='overview', deadline_seconds=1)
        self.assertTrue(calls)

    def test_rgba8_source_identity_metadata_nonmutation_and_header_only_capability(self):
        rows = [[pixel + ((0, 128, 255, 64)[(x+y) % 4],)
                 for x, pixel in enumerate(row)]
                for y, row in enumerate(varied_rows(13, 9))]
        raw, _ = adam7_raw(rows, bpp=4)
        data = png(13, 9, raw, color=6,
                   before=chunk(b'tEXt', b'private\0RGBA source remains unchanged'))
        original = bytes(data)
        digest = hashlib.sha256(original).hexdigest()
        rect = {'x': 2, 'y': 3, 'width': 3, 'height': 2}
        for mode, rectangle in (('overview', None), ('crop', rect)):
            with self.subTest(mode=mode):
                result = preview_png(data, mode=mode, rect=rectangle,
                                     expected_source_sha256=digest)
                expected = rows if rectangle is None else [row[2:5] for row in rows[3:5]]
                size, output_mode, pixels, metadata, payload = decoded(result)
                self.assertEqual(size, (len(expected[0]), len(expected)))
                self.assertEqual(output_mode, 'RGBA')
                self.assertEqual(pixels, pixel_bytes(expected))
                self.assertEqual(metadata, {})
                self.assertNotIn(b'RGBA source remains unchanged', payload)
                self.assertEqual(result['sourceSha256'], digest)
                self.assertEqual(result['bufferAccounting']['sourceSnapshotBytes'], len(original))
                self.assertEqual((result['sourceWidth'], result['sourceHeight']), (13, 9))
                self.assertEqual(data, original)
        with self.assertRaisesRegex(PngPreviewError, 'identity'):
            preview_png(data, mode='overview', expected_source_sha256='0'*64)
        self.assertEqual(data, original)
        header_only = png(13, 9, b'', color=6, stream=b'not a zlib stream')
        with patch('nivram.png_preview.zlib.decompressobj',
                   side_effect=AssertionError('Capability must not decode')):
            self.assertEqual(preview_capability(header_only),
                             {'available': True, 'reason': None, 'width': 13, 'height': 9})


"""Indexed Adam7 test addition, for insertion into test_png_adam7_preview.py.

Uses that module's existing imports and PASS_LABELS, filtered, png, chunk,
decoded and pixel_bytes helpers. Adds no imports or runner. Synthetic fixtures
are at most 17x17 pixels with at most 19 palette entries; malformed palette
payloads are at most 257 entries. No files, network or worker processes.
Initial red:
PngAdam7IndexedPreviewTests.test_indexed_literal_fixture_is_pillow_valid_then_preview_available_and_exact
"""


def indexed_adam7_raw(samples, depth, filters=(0, 1, 2, 3, 4), *,
                      padding=0, per_pass=None):
    """Pack independent pass rows MSB first, then apply byte filters with bpp=1."""
    if depth not in (1, 2, 4, 8):
        raise ValueError('Fixture depth must be 1, 2, 4 or 8.')
    width = len(samples[0])
    output, facts = bytearray(), []
    for pass_number in range(1, 8):
        previous, pass_row = None, 0
        for y, source_row in enumerate(samples):
            selected = [source_row[x] for x in range(width)
                        if PASS_LABELS[y % 8][x % 8] == pass_number]
            if not selected:
                continue
            # Bit-string packing is deliberately independent of decoder shifts.
            bits = ''.join(format(index, '0%db' % depth) for index in selected)
            if any(index < 0 or index >= 1 << depth for index in selected):
                raise ValueError('Fixture sample does not fit its bit depth.')
            spare = (-len(bits)) % 8
            if spare:
                bits += format(padding & ((1 << spare)-1), '0%db' % spare)
            row = bytes(int(bits[start:start+8], 2) for start in range(0, len(bits), 8))
            if previous is None:
                previous = bytes(len(row))
            schedule = filters if per_pass is None else per_pass[pass_number]
            kind = schedule[pass_row % len(schedule)]
            facts.append((pass_number, y, len(output), len(selected)))
            output.extend(filtered(row, previous, kind, bpp=1))
            previous, pass_row = row, pass_row+1
    return bytes(output), facts


def indexed_palette_chunks(palette, alpha=None):
    result = chunk(b'PLTE', bytes(channel for color in palette for channel in color))
    return result if alpha is None else result+chunk(b'tRNS', alpha)


def indexed_expected_rows(samples, palette, alpha=None):
    if alpha is None:
        return [[palette[index] for index in row] for row in samples]
    return [[palette[index] + (alpha[index] if index < len(alpha) else 255,)
             for index in row] for row in samples]


class PngAdam7IndexedPreviewTests(unittest.TestCase):
    def assert_indexed_pixels(self, data, expected, *, mode='overview', rect=None):
        result = preview_png(data, mode=mode, rect=rect)
        size, color_mode, pixels, metadata, payload = decoded(result)
        self.assertEqual(size, (len(expected[0]), len(expected)))
        self.assertEqual(color_mode, 'RGB' if len(expected[0][0]) == 3 else 'RGBA')
        self.assertEqual(pixels, pixel_bytes(expected))
        self.assertEqual(metadata, {})
        self.assertEqual(payload[28], 0)
        self.assertEqual(result['sourceSha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(result['navigationOnly'], mode == 'overview')
        self.assertEqual(result['resampling'], 'nearest-center' if mode == 'overview' else 'none')
        return result

    def test_indexed_literal_fixture_is_pillow_valid_then_preview_available_and_exact(self):
        # Literal depth-2 pass 1 index 0, pass 6 index 1, pass 7 indices 2,3.
        raw = b'\x00\x00\x00\x40\x00\xb0'
        palette = b'\x0a\x14\x1e\x28\x32\x3c\x46\x50\x5a\x64\x6e\x78'
        data = png(2, 2, raw, color=3, depth=2, split=1,
                   before=chunk(b'PLTE', palette)+chunk(b'tRNS', b'\x00\x80'))
        expected = [[(10, 20, 30, 0), (40, 50, 60, 128)],
                    [(70, 80, 90, 255), (100, 110, 120, 255)]]
        self.assertEqual(len(raw), 6)
        with Image.open(io.BytesIO(data)) as independent:
            self.assertEqual(independent.size, (2, 2))
            self.assertEqual(independent.mode, 'P')
            self.assertEqual(independent.convert('RGBA').tobytes(), pixel_bytes(expected))
        self.assertEqual(preview_capability(data),
                         {'available': True, 'reason': None, 'width': 2, 'height': 2})
        self.assert_indexed_pixels(data, expected)

    def test_indexed_all_depths_all_five_filters_and_pass_resets(self):
        for depth in (1, 2, 4, 8):
            count = min(1 << depth, 19)
            palette = [((17*i+11) % 256, (41*i+13) % 256, (255-23*i) % 256)
                       for i in range(count)]
            alpha = bytes([0, 128, 254][:min(count, 3)])
            samples = [[(3*x+5*y+x*y) % count for x in range(17)] for y in range(17)]
            expected = indexed_expected_rows(samples, palette, alpha)
            for kind in range(5):
                with self.subTest(depth=depth, filter=kind):
                    raw, facts = indexed_adam7_raw(samples, depth, (kind,), padding=255)
                    self.assertEqual({item[0] for item in facts}, set(range(1, 8)))
                    self.assertTrue(all(sum(item[0] == p for item in facts) >= 2
                                        for p in range(1, 8)))
                    data = png(17, 17, raw, color=3, depth=depth, split=1,
                               before=indexed_palette_chunks(palette, alpha))
                    result = self.assert_indexed_pixels(data, expected)
                    self.assertEqual(result['bufferAccounting']['filteredBytesProcessed'], len(raw))
                    self.assert_indexed_pixels(
                        data, [row[2:13] for row in expected[3:8]], mode='crop',
                        rect={'x': 2, 'y': 3, 'width': 11, 'height': 5})

    def test_indexed_small_odd_sizes_empty_passes_and_boundary_crops(self):
        palette = [(7, 23, 91), (203, 19, 5)]
        for depth in (1, 2, 4, 8):
            for width, height in ((1, 1), (1, 3), (3, 1), (2, 2), (3, 5), (5, 3), (9, 9)):
                with self.subTest(depth=depth, size=(width, height)):
                    samples = [[(x+y) % 2 for x in range(width)] for y in range(height)]
                    raw, facts = indexed_adam7_raw(samples, depth, (4, 2, 3, 1, 0), padding=255)
                    self.assertEqual(sum(item[3] for item in facts), width*height)
                    if (width, height) == (1, 1):
                        self.assertEqual([item[0] for item in facts], [1])
                        self.assertEqual(len(raw), 2)
                    expected = indexed_expected_rows(samples, palette)
                    data = png(width, height, raw, color=3, depth=depth, split=1,
                               before=indexed_palette_chunks(palette))
                    self.assert_indexed_pixels(data, expected)
                    self.assert_indexed_pixels(
                        data, [[expected[-1][-1]]], mode='crop',
                        rect={'x': width-1, 'y': height-1, 'width': 1, 'height': 1})

    def test_indexed_literal_crop_and_nearest_center_overview(self):
        palette = [(10, 20, 30), (40, 50, 60), (70, 80, 90), (100, 110, 120)]
        samples = [[(x+y) % 4 for x in range(13)] for y in range(9)]
        schedule = {1: (0, 1), 2: (2, 4), 3: (3,), 4: (4, 2, 3),
                    5: (2, 4), 6: (3, 0, 1, 2, 4), 7: (4, 3, 2, 1)}
        raw, _ = indexed_adam7_raw(samples, 2, per_pass=schedule, padding=255)
        data = png(13, 9, raw, color=3, depth=2,
                   before=indexed_palette_chunks(palette, b'\x00\x80'))
        expected_crop = [
            [(40, 50, 60, 128), (70, 80, 90, 255), (100, 110, 120, 255)],
            [(70, 80, 90, 255), (100, 110, 120, 255), (10, 20, 30, 0)],
        ]
        rect = {'x': 2, 'y': 3, 'width': 3, 'height': 2}
        self.assertEqual(self.assert_indexed_pixels(data, expected_crop, mode='crop', rect=rect)['rect'], rect)
        # Independently enumerated source centers x=1,3,6,9,11 and y=1,4,7.
        expected_overview = [
            [(70, 80, 90, 255), (10, 20, 30, 0), (100, 110, 120, 255),
             (70, 80, 90, 255), (10, 20, 30, 0)],
            [(40, 50, 60, 128), (100, 110, 120, 255), (70, 80, 90, 255),
             (40, 50, 60, 128), (100, 110, 120, 255)],
            [(10, 20, 30, 0), (70, 80, 90, 255), (40, 50, 60, 128),
             (10, 20, 30, 0), (70, 80, 90, 255)],
        ]
        with patch('nivram.png_preview.OVERVIEW_EDGE', 5):
            self.assert_indexed_pixels(data, expected_overview)

    def test_indexed_padding_bits_ignore_high_indices_and_empty_trns_is_opaque(self):
        palette = [(7, 23, 91)]
        for depth in (1, 2, 4):
            for size in ((1, 1), (3, 5), (13, 9)):
                samples = [[0]*size[0] for _ in range(size[1])]
                raw, facts = indexed_adam7_raw(samples, depth, (4, 2, 3, 1, 0), padding=255)
                self.assertTrue(any((item[3]*depth) % 8 for item in facts))
                for alpha in (None, b''):
                    with self.subTest(depth=depth, size=size, alpha=alpha):
                        data = png(size[0], size[1], raw, color=3, depth=depth,
                                   before=indexed_palette_chunks(palette, alpha))
                        self.assert_indexed_pixels(data, indexed_expected_rows(samples, palette, alpha))

    def test_indexed_invalid_actual_indices_outside_crop_and_sparse_overview(self):
        palette = [(7, 23, 91)]
        # One actual source pixel in each pass, all outside crop (0,0,1,1).
        points = ((8, 8), (12, 8), (8, 4), (10, 8), (12, 6), (11, 8), (12, 7))
        self.assertEqual([PASS_LABELS[y % 8][x % 8] for x, y in points], list(range(1, 8)))
        for depth in (1, 2, 4, 8):
            for x, y in points:
                samples = [[0]*13 for _ in range(9)]
                samples[y][x] = 1  # Representable at every depth, absent from this PLTE.
                raw, _ = indexed_adam7_raw(samples, depth, padding=255)
                data = png(13, 9, raw, color=3, depth=depth,
                           before=indexed_palette_chunks(palette))
                with self.subTest(depth=depth, invalid=(x, y), mode='crop'):
                    self.assertTrue(preview_capability(data)['available'])
                    with self.assertRaises(PngPreviewError):
                        preview_png(data, mode='crop', rect={'x': 0, 'y': 0, 'width': 1, 'height': 1})
            # Omitted row, omitted column on a sampled row, and another omitted row.
            for x, y in ((0, 0), (0, 4), (1, 5)):
                samples = [[0]*13 for _ in range(9)]
                samples[y][x] = 1
                raw, _ = indexed_adam7_raw(samples, depth)
                data = png(13, 9, raw, color=3, depth=depth,
                           before=indexed_palette_chunks(palette))
                with self.subTest(depth=depth, invalid=(x, y), mode='overview'), \
                     patch('nivram.png_preview.OVERVIEW_EDGE', 5), self.assertRaises(PngPreviewError):
                    preview_png(data, mode='overview')

    def test_indexed_malformed_palette_transparency_and_ordering_are_unavailable(self):
        palette = chunk(b'PLTE', b'\x07\x17\x5b\xcb\x13\x05')
        alpha = chunk(b'tRNS', b'\x00')
        cases = {
            'missing PLTE': (b'', b''),
            'empty PLTE': (chunk(b'PLTE', b''), b''),
            'incomplete palette entry': (chunk(b'PLTE', b'12'), b''),
            'over 256 entries': (chunk(b'PLTE', bytes(257*3)), b''),
            'palette exceeds depth': (chunk(b'PLTE', bytes(3*3)), b''),
            'duplicate PLTE': (palette+palette, b''),
            'PLTE after IDAT': (b'', palette),
            'tRNS before PLTE': (alpha+palette, b''),
            'tRNS after IDAT': (palette, alpha),
            'duplicate tRNS': (palette+alpha+alpha, b''),
            'alpha count exceeds palette': (palette+chunk(b'tRNS', b'\x00\x80\xff'), b''),
        }
        for name, (before, after) in cases.items():
            data = png(1, 1, b'\x00\x00', color=3, depth=1, before=before, after=after)
            with self.subTest(case=name):
                self.assertFalse(preview_capability(data)['available'])
                with self.assertRaises(PngPreviewError):
                    preview_png(data, mode='overview')
        # Preserve the historical missing-PLTE depth-8/interlace-1 rejection.
        missing = png(1, 1, b'\x00\x00', color=3, depth=8)
        self.assertFalse(preview_capability(missing)['available'])
        with self.assertRaises(PngPreviewError):
            preview_png(missing, mode='overview')

    def test_indexed_full_stream_filter_crc_and_file_errors_are_rejected(self):
        before = indexed_palette_chunks([(7, 23, 91), (203, 19, 5)])
        samples = [[(x+y) % 2 for x in range(13)] for y in range(9)]
        raw, facts = indexed_adam7_raw(samples, 2, (0,))
        valid = zlib.compress(raw)
        cases = {
            'empty decoded stream': zlib.compress(b''),
            'truncated decoded row': zlib.compress(raw[:-1]),
            'excess decoded byte': zlib.compress(raw+b'\0'),
            'truncated zlib trailer': valid[:-1],
            'bad zlib checksum': valid[:-1]+bytes([valid[-1] ^ 1]),
            'concatenated empty stream': valid+zlib.compress(b''),
            'concatenated nonempty stream': valid+zlib.compress(b'extra'),
            'compressed suffix': valid+b'extra',
        }
        rect = {'x': 0, 'y': 0, 'width': 1, 'height': 1}
        for name, stream in cases.items():
            with self.subTest(stream=name), self.assertRaises(PngPreviewError):
                preview_png(png(13, 9, b'', color=3, depth=2, before=before, stream=stream, split=1),
                            mode='crop', rect=rect)
        for pass_number in range(1, 8):
            fact = [item for item in facts if item[0] == pass_number][-1]
            self.assertNotEqual((pass_number, fact[1]), (1, 0))
            corrupt = bytearray(raw)
            corrupt[fact[2]] = 5
            with self.subTest(pass_number=pass_number), self.assertRaisesRegex(PngPreviewError, 'filter'):
                preview_png(png(13, 9, bytes(corrupt), color=3, depth=2, before=before),
                            mode='crop', rect=rect)
        unselected = next(item for item in facts if item[0] == 7 and item[1] == 5)
        corrupt = bytearray(raw)
        corrupt[unselected[2]] = 255
        with patch('nivram.png_preview.OVERVIEW_EDGE', 5), self.assertRaisesRegex(PngPreviewError, 'filter'):
            preview_png(png(13, 9, bytes(corrupt), color=3, depth=2, before=before), mode='overview')
        data = png(13, 9, raw, color=3, depth=2, before=before, split=65536)
        idat_type = 33+len(before)+4
        self.assertEqual(data[idat_type:idat_type+4], b'IDAT')
        damaged_idat, damaged_palette = bytearray(data), bytearray(data)
        damaged_idat[idat_type+4] ^= 1
        self.assertEqual(data[37:41], b'PLTE')
        damaged_palette[41] ^= 1
        for name, damaged in (('IDAT CRC', bytes(damaged_idat)), ('PLTE CRC', bytes(damaged_palette)),
                              ('file truncation', data[:-1]), ('file suffix', data+b'extra')):
            with self.subTest(file=name), self.assertRaises(PngPreviewError):
                preview_png(damaged, mode='crop', rect=rect)

    def test_indexed_exact_packed_pass_budgets_and_existing_output_guards(self):
        palette = [(7, 23, 91), (203, 19, 5)]
        samples = [[(x+y) % 2 for x in range(13)] for y in range(9)]
        expected = indexed_expected_rows(samples, palette, b'\x00\x80')
        for depth, total in ((1, 42), (2, 57), (4, 82), (8, 136)):
            raw, facts = indexed_adam7_raw(samples, depth, (4,))
            self.assertEqual(len(facts), 19)
            self.assertEqual(len(raw), total)
            data = png(13, 9, raw, color=3, depth=depth,
                       before=indexed_palette_chunks(palette, b'\x00\x80'))
            with self.subTest(depth=depth), patch('nivram.png_preview.MAX_FILTERED_BYTES', total-1), \
                 patch('nivram.png_preview.zlib.decompressobj',
                       side_effect=AssertionError('Must reject filtered budget before decode')):
                self.assertFalse(preview_capability(data)['available'])
                with self.assertRaisesRegex(PngPreviewError, 'filtered'):
                    preview_png(data, mode='overview')
            with patch('nivram.png_preview.MAX_FILTERED_BYTES', total):
                result = self.assert_indexed_pixels(data, expected)
            accounting = result['bufferAccounting']
            self.assertEqual(accounting['filteredBytesProcessed'], total)
            self.assertEqual(accounting['sourceRowBytes'], (13*depth+7)//8)
            self.assertEqual(accounting['outputPixelBytes'], 13*9*4)
        for name, value in (('MAX_SOURCE_BYTES', len(data)-1), ('MAX_CHUNKS', 2),
                            ('PIXEL_PIPELINE_BUDGET', 1), ('MAX_PREVIEW_BYTES', 64),
                            ('MAX_PIXELS', 116), ('MAX_EDGE', 12)):
            with self.subTest(limit=name), patch('nivram.png_preview.'+name, value), \
                 self.assertRaises(PngPreviewError):
                preview_png(data, mode='overview')

    def test_indexed_bounded_decompression_without_production_pillow_decode(self):
        palette = [(7, 23, 91), (203, 19, 5)]
        samples = [[(x+y) % 2 for x in range(13)] for y in range(9)]
        expected = indexed_expected_rows(samples, palette, b'\x00\x80')
        actual_decoder = zlib.decompressobj
        test = self
        for depth in (1, 2, 4, 8):
            raw, _ = indexed_adam7_raw(samples, depth, padding=255)
            data = png(13, 9, raw, color=3, depth=depth, split=1,
                       before=indexed_palette_chunks(palette, b'\x00\x80'))
            requests, bound = [], (13*depth+7)//8+1

            class ObservedDecoder:
                def __init__(self):
                    self.inner = actual_decoder()

                def decompress(self, compressed, max_length=0):
                    test.assertGreater(max_length, 0)
                    test.assertLessEqual(max_length, bound)
                    requests.append(max_length)
                    output = self.inner.decompress(compressed, max_length)
                    test.assertLessEqual(len(output), max_length)
                    return output

                def __getattr__(self, name):
                    return getattr(self.inner, name)

            with self.subTest(depth=depth), \
                 patch('nivram.png_preview.zlib.decompressobj', side_effect=ObservedDecoder), \
                 patch('nivram.png_preview.Image.open', side_effect=AssertionError('No Pillow Adam7 decode')):
                result = preview_png(data, mode='crop',
                                     rect={'x': 2, 'y': 3, 'width': 3, 'height': 2})
            self.assertTrue(requests)
            self.assertEqual(decoded(result)[:3],
                             ((3, 2), 'RGBA', pixel_bytes([row[2:5] for row in expected[3:5]])))
            accounting = result['bufferAccounting']
            self.assertEqual(accounting['maxCarrierHeight'], 0)
            self.assertEqual(accounting['outputPixelBytes'], 3*2*4)
            self.assertLessEqual(accounting['pixelPipelineAccountedUpperBoundBytes'],
                                 accounting['pixelPipelineBudgetBytes'])

    def test_indexed_cooperative_deadline_before_and_during_decompression(self):
        samples = [[(x+y) % 2 for x in range(13)] for y in range(9)]
        raw, _ = indexed_adam7_raw(samples, 2, (4,))
        data = png(13, 9, raw, color=3, depth=2, split=1,
                   before=indexed_palette_chunks([(7, 23, 91), (203, 19, 5)]))
        early_clock = [0.0]

        def expire_after_start():
            value = early_clock[0]
            early_clock[0] = 2.0
            return value

        with patch('nivram.png_preview.time.monotonic', side_effect=expire_after_start), \
             self.assertRaises(TimeoutError):
            preview_png(data, mode='overview', deadline_seconds=1)
        actual_decoder = zlib.decompressobj
        clock, calls = [0.0], []

        class ExpiringDecoder:
            def __init__(self):
                self.inner = actual_decoder()

            def decompress(self, compressed, max_length=0):
                calls.append(max_length)
                output = self.inner.decompress(compressed, max_length)
                clock[0] = 100.0
                return output

            def __getattr__(self, name):
                return getattr(self.inner, name)

        with patch('nivram.png_preview.time.monotonic', side_effect=lambda: clock[0]), \
             patch('nivram.png_preview.zlib.decompressobj', side_effect=ExpiringDecoder), \
             self.assertRaises(TimeoutError):
            preview_png(data, mode='overview', deadline_seconds=1)
        self.assertTrue(calls)

    def test_indexed_source_identity_metadata_nonmutation_and_header_only_capability(self):
        palette = [(7, 23, 91), (203, 19, 5)]
        samples = [[(x+y) % 2 for x in range(13)] for y in range(9)]
        raw, _ = indexed_adam7_raw(samples, 2, padding=255)
        before = indexed_palette_chunks(palette, b'\x00\x80')
        data = png(13, 9, raw, color=3, depth=2,
                   before=before+chunk(b'tEXt', b'private\0indexed source remains unchanged'))
        original, digest = bytes(data), hashlib.sha256(data).hexdigest()
        expected = indexed_expected_rows(samples, palette, b'\x00\x80')
        rect = {'x': 2, 'y': 3, 'width': 3, 'height': 2}
        for mode, rectangle in (('overview', None), ('crop', rect)):
            with self.subTest(mode=mode):
                result = preview_png(data, mode=mode, rect=rectangle, expected_source_sha256=digest)
                pixels_expected = expected if rectangle is None else [row[2:5] for row in expected[3:5]]
                size, color_mode, pixels, metadata, payload = decoded(result)
                self.assertEqual(size, (len(pixels_expected[0]), len(pixels_expected)))
                self.assertEqual(color_mode, 'RGBA')
                self.assertEqual(pixels, pixel_bytes(pixels_expected))
                self.assertEqual(metadata, {})
                self.assertNotIn(b'indexed source remains unchanged', payload)
                self.assertEqual(result['sourceSha256'], digest)
                self.assertEqual(result['bufferAccounting']['sourceSnapshotBytes'], len(original))
                self.assertEqual((result['sourceWidth'], result['sourceHeight']), (13, 9))
                self.assertEqual(data, original)
        with self.assertRaisesRegex(PngPreviewError, 'identity'):
            preview_png(data, mode='overview', expected_source_sha256='0'*64)
        self.assertEqual(data, original)
        for depth in (1, 2, 4, 8):
            header_only = png(13, 9, b'', color=3, depth=depth,
                              before=before, stream=b'not a zlib stream')
            with self.subTest(depth=depth), patch('nivram.png_preview.zlib.decompressobj',
                                                side_effect=AssertionError('Capability must not decode')):
                self.assertEqual(preview_capability(header_only),
                                 {'available': True, 'reason': None, 'width': 13, 'height': 9})


"""Additive grayscale Adam7 tests for test_png_adam7_preview.py.

Insert after the existing fixture helpers and before unittest.main(). Reuses
that module's imports, indexed_adam7_raw (generic packed samples), adam7_raw,
png, chunk, decoded and pixel_bytes. No new imports, runner or worker.
Valid fixtures are at most 17x17. Expected pixels use authored source samples,
literal oracles and integer gray expansion; never production decoder helpers.
Cancellation belongs to the existing supervisor/job regression checks:
preview_png exposes a cooperative deadline, not a cancellation token.
Initial red:
PngAdam7GrayPreviewTests.test_gray_literal_fixture_is_pillow_valid_then_preview_available_and_exact
"""


ADAM7_GRAY_FORMATS = ((0, 1), (0, 2), (0, 4), (0, 8), (4, 8))


def gray_adam7_rows(width, height, color, depth):
    mask = (1 << depth)-1
    samples = [[(17*x+29*y+3*x*y+11) & mask for x in range(width)]
               for y in range(height)]
    if color == 0:
        return samples
    return [[(gray, (0, 1, 64, 128, 254, 255)[(x+3*y) % 6])
             for x, gray in enumerate(row)] for y, row in enumerate(samples)]


def gray_adam7_raw(rows, color, depth, filters=(0, 1, 2, 3, 4), *,
                   padding=255, per_pass=None):
    if color == 0:
        return indexed_adam7_raw(rows, depth, filters, padding=padding, per_pass=per_pass)
    if (color, depth) != (4, 8):
        raise ValueError('Fixture only supports packed/8-bit gray and gray-alpha8.')
    return adam7_raw(rows, filters, per_pass=per_pass, bpp=2)


def gray_adam7_expected(rows, color, depth, *, transparent=None):
    if color == 4:
        return [[(gray, gray, gray, alpha) for gray, alpha in row] for row in rows]
    scale = {1: 255, 2: 85, 4: 17, 8: 1}[depth]
    if transparent is None:
        return [[(sample*scale,)*3 for sample in row] for row in rows]
    return [[(sample*scale,)*3 + (0 if sample == transparent else 255,)
             for sample in row] for row in rows]


class PngAdam7GrayPreviewTests(unittest.TestCase):
    def assert_gray_pixels(self, data, expected, *, mode='overview', rect=None):
        result = preview_png(data, mode=mode, rect=rect)
        size, color_mode, pixels, metadata, payload = decoded(result)
        self.assertEqual(size, (len(expected[0]), len(expected)))
        self.assertEqual(color_mode, 'RGB' if len(expected[0][0]) == 3 else 'RGBA')
        self.assertEqual(pixels, pixel_bytes(expected))
        self.assertEqual(metadata, {})
        self.assertEqual(payload[28], 0)
        self.assertEqual(result['sourceSha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(result['navigationOnly'], mode == 'overview')
        self.assertEqual(result['resampling'], 'nearest-center' if mode == 'overview' else 'none')
        return result

    def test_gray_literal_fixture_is_pillow_valid_then_preview_available_and_exact(self):
        # Depth-2 samples [[0,1],[2,3]]: pass 1, pass 6, then pass 7.
        raw = b'\x00\x00\x00\x40\x00\xb0'
        expected = [[(0, 0, 0), (85, 85, 85)],
                    [(170, 170, 170), (255, 255, 255)]]
        data = png(2, 2, raw, color=0, depth=2, split=1)
        self.assertEqual(len(raw), 6)
        with Image.open(io.BytesIO(data)) as independent:
            self.assertEqual(independent.size, (2, 2))
            self.assertEqual(independent.mode, 'L')
            self.assertEqual(independent.convert('RGB').tobytes(), pixel_bytes(expected))
        self.assertEqual(preview_capability(data),
                         {'available': True, 'reason': None, 'width': 2, 'height': 2})
        self.assert_gray_pixels(data, expected)

    def test_grayalpha_literal_hidden_gray_and_unassociated_alpha_are_exact(self):
        raw = b'\x00\x11\x00\x00\x53\x80\x00\xf9\xff\x21\x40'
        expected = [[(17, 17, 17, 0), (83, 83, 83, 128)],
                    [(249, 249, 249, 255), (33, 33, 33, 64)]]
        self.assertEqual(len(raw), 11)
        data = png(2, 2, raw, color=4, depth=8, split=1)
        with Image.open(io.BytesIO(data)) as independent:
            self.assertEqual(independent.size, (2, 2))
            self.assertEqual(independent.mode, 'LA')
            self.assertEqual(independent.convert('RGBA').tobytes(), pixel_bytes(expected))
        self.assertTrue(preview_capability(data)['available'])
        self.assert_gray_pixels(data, expected)
        self.assert_gray_pixels(data, [[(17, 17, 17, 0)]], mode='crop',
                                rect={'x': 0, 'y': 0, 'width': 1, 'height': 1})

    def test_gray_all_five_formats_all_five_filters_reset_every_pass(self):
        for color, depth in ADAM7_GRAY_FORMATS:
            rows = gray_adam7_rows(17, 17, color, depth)
            expected = gray_adam7_expected(rows, color, depth)
            for kind in range(5):
                with self.subTest(color=color, depth=depth, filter=kind):
                    raw, facts = gray_adam7_raw(rows, color, depth, (kind,))
                    self.assertEqual({item[0] for item in facts}, set(range(1, 8)))
                    self.assertTrue(all(sum(item[0] == p for item in facts) >= 2
                                        for p in range(1, 8)))
                    data = png(17, 17, raw, color=color, depth=depth, split=1)
                    result = self.assert_gray_pixels(data, expected)
                    self.assertEqual(result['bufferAccounting']['filteredBytesProcessed'], len(raw))
                    self.assert_gray_pixels(
                        data, [row[2:13] for row in expected[3:8]], mode='crop',
                        rect={'x': 2, 'y': 3, 'width': 11, 'height': 5})

    def test_gray_small_odd_dimensions_empty_passes_and_boundary_crops(self):
        for color, depth in ADAM7_GRAY_FORMATS:
            for width, height in ((1, 1), (1, 3), (3, 1), (2, 2), (3, 5), (5, 3), (9, 9)):
                with self.subTest(color=color, depth=depth, size=(width, height)):
                    rows = gray_adam7_rows(width, height, color, depth)
                    raw, facts = gray_adam7_raw(rows, color, depth, (4, 2, 3, 1, 0))
                    self.assertEqual(sum(item[3] for item in facts), width*height)
                    if (width, height) == (1, 1):
                        self.assertEqual([item[0] for item in facts], [1])
                        self.assertEqual(len(raw), 3 if color == 4 else 2)
                    expected = gray_adam7_expected(rows, color, depth)
                    data = png(width, height, raw, color=color, depth=depth, split=1)
                    self.assert_gray_pixels(data, expected)
                    self.assert_gray_pixels(
                        data, [[expected[-1][-1]]], mode='crop',
                        rect={'x': width-1, 'y': height-1, 'width': 1, 'height': 1})

    def test_gray_exact_crop_and_nearest_center_overview_use_literal_coordinates(self):
        literal_crop = [[50, 51, 52], [66, 67, 68]]
        literal_overview = [[17, 19, 22, 25, 27], [65, 67, 70, 73, 75],
                            [113, 115, 118, 121, 123]]
        crop_alpha = [[128, 255, 64], [255, 64, 0]]
        overview_alpha = [[255, 0, 64, 255, 0], [128, 64, 255, 128, 64],
                          [0, 255, 128, 0, 255]]
        schedule = {1: (0, 1), 2: (2, 4), 3: (3,), 4: (4, 2, 3),
                    5: (2, 4), 6: (3, 0, 1, 2, 4), 7: (4, 3, 2, 1)}
        rect = {'x': 2, 'y': 3, 'width': 3, 'height': 2}
        for color, depth in ADAM7_GRAY_FORMATS:
            mask, scale = (1 << depth)-1, {1: 255, 2: 85, 4: 17, 8: 1}[depth]
            if color == 0:
                rows = [[(16*y+x) & mask for x in range(13)] for y in range(9)]
                expected_crop = [[((gray & mask)*scale,)*3 for gray in row] for row in literal_crop]
                expected_overview = [[((gray & mask)*scale,)*3 for gray in row]
                                     for row in literal_overview]
            else:
                rows = [[(16*y+x, (0, 128, 255, 64)[(x+y) % 4]) for x in range(13)]
                        for y in range(9)]
                expected_crop = [[(gray,)*3+(crop_alpha[y][x],) for x, gray in enumerate(row)]
                                 for y, row in enumerate(literal_crop)]
                expected_overview = [[(gray,)*3+(overview_alpha[y][x],) for x, gray in enumerate(row)]
                                     for y, row in enumerate(literal_overview)]
            raw, _ = gray_adam7_raw(rows, color, depth, per_pass=schedule)
            data = png(13, 9, raw, color=color, depth=depth)
            with self.subTest(color=color, depth=depth):
                result = self.assert_gray_pixels(data, expected_crop, mode='crop', rect=rect)
                self.assertEqual(result['rect'], rect)
                self.assertEqual((result['sourceWidth'], result['sourceHeight']), (13, 9))
                with patch('nivram.png_preview.OVERVIEW_EDGE', 5):
                    self.assert_gray_pixels(data, expected_overview)

    def test_gray_trns_matches_original_samples_before_scaling_and_masks_high_bits(self):
        for depth in (1, 2, 4, 8):
            mask = (1 << depth)-1
            target = 17 if depth == 8 else 1
            first = [0, target, mask, target ^ 1, 0]
            rows = [first, list(reversed(first)), first]
            raw, _ = gray_adam7_raw(rows, 0, depth, (4, 2, 3))
            expected = gray_adam7_expected(rows, 0, depth, transparent=target)
            for high in (0, 0xFFFF ^ mask):
                with self.subTest(depth=depth, high=high):
                    data = png(5, 3, raw, color=0, depth=depth,
                               before=chunk(b'tRNS', struct.pack('>H', target | high)))
                    self.assert_gray_pixels(data, expected)
                    self.assert_gray_pixels(
                        data, [row[1:4] for row in expected[1:3]], mode='crop',
                        rect={'x': 1, 'y': 1, 'width': 3, 'height': 2})
            # For depths below 8, transparent raw sample 1 expands to 255/85/17.
            self.assertEqual(expected[0][1][-1], 0)
            self.assertEqual(expected[0][1][:3], ({1: 255, 2: 85, 4: 17, 8: 17}[depth],)*3)

    def test_gray_packed_padding_is_not_an_extra_pixel_or_transparency_sample(self):
        for depth in (1, 2, 4):
            rows = [[(x+y) % 2 for x in range(13)] for y in range(9)]
            expected = gray_adam7_expected(rows, 0, depth, transparent=1)
            raws = []
            for padding in (0, 255):
                raw, facts = gray_adam7_raw(rows, 0, depth, (4, 2, 3, 1, 0), padding=padding)
                raws.append(raw)
                self.assertTrue(any(item[3]*depth % 8 for item in facts))
                with self.subTest(depth=depth, padding=padding):
                    self.assert_gray_pixels(png(13, 9, raw, color=0, depth=depth,
                                                before=chunk(b'tRNS', b'\x00\x01')), expected)
            self.assertNotEqual(raws[0], raws[1])

    def test_gray_invalid_transparency_and_prohibited_palette_are_unavailable(self):
        cases = []
        valid_trns = chunk(b'tRNS', b'\x00\x01')
        for length in (0, 1, 3, 6):
            cases.append(('gray tRNS length '+str(length), 0, 2,
                          chunk(b'tRNS', bytes(length)), b''))
        cases.extend([
            ('duplicate gray tRNS', 0, 2, valid_trns+valid_trns, b''),
            ('gray tRNS after IDAT', 0, 2, b'', valid_trns),
            ('gray PLTE', 0, 2, chunk(b'PLTE', b'\x01\x02\x03'), b''),
            ('gray-alpha PLTE', 4, 8, chunk(b'PLTE', b'\x01\x02\x03'), b''),
            ('gray-alpha empty tRNS', 4, 8, chunk(b'tRNS', b''), b''),
            ('gray-alpha tRNS', 4, 8, valid_trns, b''),
            ('gray-alpha tRNS after IDAT', 4, 8, b'', valid_trns),
        ])
        for name, color, depth, before, after in cases:
            raw = b'\x00\x00\xff' if color == 4 else b'\x00\x40'
            data = png(1, 1, raw, color=color, depth=depth, before=before, after=after)
            with self.subTest(case=name):
                self.assertFalse(preview_capability(data)['available'])
                with self.assertRaises(PngPreviewError):
                    preview_png(data, mode='overview')

    def test_gray_full_stream_errors_unselected_filters_crc_and_old_malformed_cases(self):
        # Exact historical palette-test fixtures: now admitted header, excess data.
        for color in (0, 4):
            data = png(1, 1, b'\0'*9, color=color, depth=8)
            with self.subTest(old_malformed_color=color):
                self.assertEqual(preview_capability(data),
                                 {'available': True, 'reason': None, 'width': 1, 'height': 1})
                with self.assertRaises(PngPreviewError):
                    preview_png(data, mode='overview')
        rect = {'x': 0, 'y': 0, 'width': 1, 'height': 1}
        for color, depth in ADAM7_GRAY_FORMATS:
            empty = png(1, 1, b'', color=color, depth=depth)
            with self.subTest(empty_color=color, depth=depth):
                self.assertTrue(preview_capability(empty)['available'])
                with self.assertRaises(PngPreviewError):
                    preview_png(empty, mode='overview')
            rows = gray_adam7_rows(13, 9, color, depth)
            raw, facts = gray_adam7_raw(rows, color, depth, (0,))
            valid = zlib.compress(raw)
            streams = {
                'decoded truncation': zlib.compress(raw[:-1]),
                'decoded excess': zlib.compress(raw+b'\0'),
                'zlib truncation': valid[:-1],
                'zlib checksum': valid[:-1]+bytes([valid[-1] ^ 1]),
                'concatenated empty': valid+zlib.compress(b''),
                'concatenated nonempty': valid+zlib.compress(b'extra'),
                'compressed suffix': valid+b'extra',
            }
            for name, stream in streams.items():
                with self.subTest(color=color, depth=depth, stream=name), self.assertRaises(PngPreviewError):
                    preview_png(png(13, 9, b'', color=color, depth=depth, stream=stream, split=1),
                                mode='crop', rect=rect)
            # Representative packed and two-byte rows; inspect every pass.
            if (color, depth) in ((0, 2), (4, 8)):
                for pass_number in range(1, 8):
                    fact = [item for item in facts if item[0] == pass_number][-1]
                    self.assertNotEqual((pass_number, fact[1]), (1, 0))
                    corrupt = bytearray(raw)
                    corrupt[fact[2]] = 5
                    with self.subTest(color=color, pass_number=pass_number), \
                         self.assertRaisesRegex(PngPreviewError, 'filter'):
                        preview_png(png(13, 9, bytes(corrupt), color=color, depth=depth),
                                    mode='crop', rect=rect)
                unselected = next(item for item in facts if item[0] == 7 and item[1] == 5)
                corrupt = bytearray(raw)
                corrupt[unselected[2]] = 255
                with patch('nivram.png_preview.OVERVIEW_EDGE', 5), \
                     self.assertRaisesRegex(PngPreviewError, 'filter'):
                    preview_png(png(13, 9, bytes(corrupt), color=color, depth=depth), mode='overview')
            data = png(13, 9, raw, color=color, depth=depth, split=65536)
            self.assertEqual(data[37:41], b'IDAT')
            corrupt = bytearray(data)
            corrupt[41] ^= 1
            for name, damaged in (('CRC', bytes(corrupt)), ('file truncation', data[:-1]),
                                  ('file suffix', data+b'extra')):
                with self.subTest(color=color, depth=depth, file=name), self.assertRaises(PngPreviewError):
                    preview_png(damaged, mode='crop', rect=rect)

    def test_gray_exact_cumulative_filtered_and_output_limits(self):
        totals = {(0, 1): 42, (0, 2): 57, (0, 4): 82, (0, 8): 136, (4, 8): 253}
        for color, depth in ADAM7_GRAY_FORMATS:
            rows = gray_adam7_rows(13, 9, color, depth)
            raw, facts = gray_adam7_raw(rows, color, depth, (4,))
            total = totals[(color, depth)]
            self.assertEqual(len(facts), 19)
            self.assertEqual(len(raw), total)
            data = png(13, 9, raw, color=color, depth=depth)
            with self.subTest(color=color, depth=depth), \
                 patch('nivram.png_preview.MAX_FILTERED_BYTES', total-1), \
                 patch('nivram.png_preview.zlib.decompressobj',
                       side_effect=AssertionError('Filtered budget must reject before decode')):
                self.assertFalse(preview_capability(data)['available'])
                with self.assertRaisesRegex(PngPreviewError, 'filtered'):
                    preview_png(data, mode='overview')
            with patch('nivram.png_preview.MAX_FILTERED_BYTES', total):
                result = self.assert_gray_pixels(data, gray_adam7_expected(rows, color, depth))
            accounting = result['bufferAccounting']
            self.assertEqual(accounting['filteredBytesProcessed'], total)
            self.assertEqual(accounting['sourceRowBytes'], 26 if color == 4 else (13*depth+7)//8)
            self.assertEqual(accounting['outputPixelBytes'], 13*9*(4 if color == 4 else 3))
        for name, value in (('MAX_SOURCE_BYTES', len(data)-1), ('MAX_CHUNKS', 2),
                            ('PIXEL_PIPELINE_BUDGET', 1), ('MAX_PREVIEW_BYTES', 64),
                            ('MAX_PIXELS', 116), ('MAX_EDGE', 12)):
            with self.subTest(limit=name), patch('nivram.png_preview.'+name, value), \
                 self.assertRaises(PngPreviewError):
                preview_png(data, mode='overview')
        for name, value in (('MAX_CROP_PIXELS', 5), ('MAX_CROP_EDGE', 2)):
            with self.subTest(crop_limit=name), patch('nivram.png_preview.'+name, value), \
                 self.assertRaises(PngPreviewError):
                preview_png(data, mode='crop', rect={'x': 2, 'y': 3, 'width': 3, 'height': 2})

    def test_gray_bounded_decompress_output_without_production_pillow(self):
        actual_decoder = zlib.decompressobj
        test = self
        for color, depth in ADAM7_GRAY_FORMATS:
            rows = gray_adam7_rows(13, 9, color, depth)
            raw, _ = gray_adam7_raw(rows, color, depth)
            data = png(13, 9, raw, color=color, depth=depth, split=1)
            expected = gray_adam7_expected(rows, color, depth)
            requests = []
            bound = 27 if color == 4 else (13*depth+7)//8+1

            class ObservedDecoder:
                def __init__(self):
                    self.inner = actual_decoder()

                def decompress(self, compressed, max_length=0):
                    test.assertGreater(max_length, 0)
                    test.assertLessEqual(max_length, bound)
                    requests.append(max_length)
                    output = self.inner.decompress(compressed, max_length)
                    test.assertLessEqual(len(output), max_length)
                    return output

                def __getattr__(self, name):
                    return getattr(self.inner, name)

            with self.subTest(color=color, depth=depth), \
                 patch('nivram.png_preview.zlib.decompressobj', side_effect=ObservedDecoder), \
                 patch('nivram.png_preview.Image.open', side_effect=AssertionError('No Pillow Adam7 decoding')):
                result = preview_png(data, mode='crop', rect={'x': 2, 'y': 3, 'width': 3, 'height': 2})
            self.assertTrue(requests)
            self.assertEqual(decoded(result)[:3],
                             ((3, 2), 'RGBA' if color == 4 else 'RGB',
                              pixel_bytes([row[2:5] for row in expected[3:5]])))
            accounting = result['bufferAccounting']
            self.assertEqual(accounting['maxCarrierHeight'], 0)
            self.assertEqual(accounting['outputPixelBytes'], 3*2*(4 if color == 4 else 3))
            self.assertLessEqual(accounting['pixelPipelineAccountedUpperBoundBytes'],
                                 accounting['pixelPipelineBudgetBytes'])

    def test_gray_cooperative_deadline_before_and_during_decompression(self):
        # Token cancellation is tested at the existing job/supervisor boundary.
        actual_decoder = zlib.decompressobj
        for color, depth in ((0, 2), (4, 8)):
            raw, _ = gray_adam7_raw(gray_adam7_rows(13, 9, color, depth), color, depth, (4,))
            data = png(13, 9, raw, color=color, depth=depth, split=1)
            early_clock = [0.0]

            def expire_after_start():
                value = early_clock[0]
                early_clock[0] = 2.0
                return value

            with self.subTest(color=color, stage='before'), \
                 patch('nivram.png_preview.time.monotonic', side_effect=expire_after_start), \
                 self.assertRaises(TimeoutError):
                preview_png(data, mode='overview', deadline_seconds=1)
            clock, calls = [0.0], []

            class ExpiringDecoder:
                def __init__(self):
                    self.inner = actual_decoder()

                def decompress(self, compressed, max_length=0):
                    calls.append(max_length)
                    output = self.inner.decompress(compressed, max_length)
                    clock[0] = 100.0
                    return output

                def __getattr__(self, name):
                    return getattr(self.inner, name)

            with self.subTest(color=color, stage='during'), \
                 patch('nivram.png_preview.time.monotonic', side_effect=lambda: clock[0]), \
                 patch('nivram.png_preview.zlib.decompressobj', side_effect=ExpiringDecoder), \
                 self.assertRaises(TimeoutError):
                preview_png(data, mode='overview', deadline_seconds=1)
            self.assertTrue(calls)

    def test_gray_source_identity_metadata_nonmutation_and_header_only_capability(self):
        for color, depth in ADAM7_GRAY_FORMATS:
            rows = gray_adam7_rows(13, 9, color, depth)
            raw, _ = gray_adam7_raw(rows, color, depth)
            data = png(13, 9, raw, color=color, depth=depth,
                       before=chunk(b'tEXt', b'private\0gray source remains unchanged'))
            original, digest = bytes(data), hashlib.sha256(data).hexdigest()
            expected = gray_adam7_expected(rows, color, depth)
            rect = {'x': 2, 'y': 3, 'width': 3, 'height': 2}
            for mode, rectangle in (('overview', None), ('crop', rect)):
                with self.subTest(color=color, depth=depth, mode=mode):
                    result = preview_png(data, mode=mode, rect=rectangle, expected_source_sha256=digest)
                    wanted = expected if rectangle is None else [row[2:5] for row in expected[3:5]]
                    size, color_mode, pixels, metadata, payload = decoded(result)
                    self.assertEqual(size, (len(wanted[0]), len(wanted)))
                    self.assertEqual(color_mode, 'RGBA' if color == 4 else 'RGB')
                    self.assertEqual(pixels, pixel_bytes(wanted))
                    self.assertEqual(metadata, {})
                    self.assertNotIn(b'gray source remains unchanged', payload)
                    self.assertEqual(result['sourceSha256'], digest)
                    self.assertEqual(result['bufferAccounting']['sourceSnapshotBytes'], len(original))
                    self.assertEqual((result['sourceWidth'], result['sourceHeight']), (13, 9))
                    self.assertEqual(data, original)
            with self.assertRaisesRegex(PngPreviewError, 'identity'):
                preview_png(data, mode='overview', expected_source_sha256='0'*64)
            self.assertEqual(data, original)
            header_only = png(13, 9, b'', color=color, depth=depth, stream=b'not a zlib stream')
            with self.subTest(header_color=color, depth=depth), \
                 patch('nivram.png_preview.zlib.decompressobj',
                       side_effect=AssertionError('Capability must not decompress')):
                self.assertEqual(preview_capability(header_only),
                                 {'available': True, 'reason': None, 'width': 13, 'height': 9})


if __name__ == '__main__':
    unittest.main()
