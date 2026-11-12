"""Handcrafted PNG encodings with independently enumerated expected pixels.

Input fixtures are assembled from PNG chunks, sample packing, and filter math;
Pillow only reads the derived result and never supplies the expected pixels.
PNG3 tRNS masking follows https://www.w3.org/TR/png-3/#11tRNS.
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


def chunk(kind, payload):
    return (struct.pack('>I', len(payload)) + kind + payload
            + struct.pack('>I', zlib.crc32(kind + payload)))


def pack(samples, depth, *, padding=0):
    """Pack left-to-right, most significant sample first, including odd widths."""
    if depth == 8:
        return bytes(samples)
    output = bytearray((len(samples)*depth + 7)//8)
    for position, sample in enumerate(samples):
        shift = 8 - depth - (position*depth % 8)
        output[position*depth//8] |= sample << shift
    spare = (-len(samples)*depth) % 8
    if spare:
        output[-1] |= padding & ((1 << spare) - 1)
    return bytes(output)


def filter_rows(rows, bpp, filters=(0, 1, 2, 3, 4)):
    result = bytearray()
    previous = bytes(len(rows[0]))
    for y, row in enumerate(rows):
        kind = filters[y % len(filters)]
        result.append(kind)
        for index, value in enumerate(row):
            left = row[index-bpp] if index >= bpp else 0
            up = previous[index]
            upper_left = previous[index-bpp] if index >= bpp else 0
            if kind == 0:
                predictor = 0
            elif kind == 1:
                predictor = left
            elif kind == 2:
                predictor = up
            elif kind == 3:
                predictor = (left + up)//2
            else:
                estimate = left + up - upper_left
                distances = (abs(estimate-left), abs(estimate-up), abs(estimate-upper_left))
                predictor = (left, up, upper_left)[distances.index(min(distances))]
            result.append((value - predictor) & 255)
        previous = row
    return bytes(result)


def png(width, height, color, depth, raw, *, before=b'', after=b'', interlace=0,
        compressed=None, split=7):
    header = struct.pack('>IIBBBBB', width, height, depth, color, 0, 0, interlace)
    stream = zlib.compress(raw) if compressed is None else compressed
    # Deliberately split inside deflate blocks and checksums.
    idats = b''.join(chunk(b'IDAT', stream[start:start+split])
                     for start in range(0, len(stream), split))
    return SIGNATURE + chunk(b'IHDR', header) + before + idats + after + chunk(b'IEND', b'')


def rgba_result(result):
    payload = base64.b64decode(result['pngBase64'], validate=True)
    assert hashlib.sha256(payload).hexdigest() == result['pngSha256']
    with Image.open(io.BytesIO(payload)) as image:
        return image.size, image.convert('RGBA').tobytes(), dict(image.info)


def rgba_bytes(rows):
    return bytes(channel for row in rows for pixel in row for channel in pixel)


def palette_bytes(palette):
    return bytes(channel for color in palette for channel in color)


class PngPalettePreviewTests(unittest.TestCase):
    def assert_pixels(self, data, rows, *, rect=None):
        mode = 'overview' if rect is None else 'crop'
        result = preview_png(data, mode=mode, rect=rect)
        expected = rows if rect is None else [
            row[rect['x']:rect['x']+rect['width']]
            for row in rows[rect['y']:rect['y']+rect['height']]]
        size, pixels, metadata = rgba_result(result)
        self.assertEqual(size, (len(expected[0]), len(expected)))
        self.assertEqual(pixels, rgba_bytes(expected))
        self.assertEqual(metadata, {})
        self.assertEqual(result['sourceSha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(result['navigationOnly'], rect is None)
        return result

    def test_grayscale_all_depths_all_filters_odd_width_crop_and_overview(self):
        for depth in (1, 2, 4, 8):
            maximum = (1 << depth) - 1
            samples = [[(x*7 + y*11 + x*y*3) & maximum for x in range(11)]
                       for y in range(6)]
            rows = [pack(row, depth, padding=255) for row in samples]
            expected = [[(value*255//maximum,)*3 + (255,) for value in row]
                        for row in samples]
            for kind in range(5):
                with self.subTest(depth=depth, filter=kind):
                    data = png(11, 6, 0, depth, filter_rows(rows, 1, (kind,)), split=1)
                    self.assertTrue(preview_capability(data)['available'])
                    result = self.assert_pixels(data, expected)
                    self.assert_pixels(data, expected, rect={'x': 3, 'y': 1, 'width': 7, 'height': 4})
                    self.assertEqual(result['bufferAccounting']['sourceRowBytes'], len(rows[0]))
                    self.assertEqual(result['bufferAccounting']['filteredBytesProcessed'], (len(rows[0])+1)*6)

    def test_indexed_all_depths_all_filters_palette_alpha_and_opaque_tail(self):
        for depth in (1, 2, 4, 8):
            count = min(1 << depth, 19)
            palette = [(i*17 % 256, (i*41+13) % 256, (255-i*23) % 256) for i in range(count)]
            alpha = bytes([0, 113, 254][:min(count, 3)])
            samples = [[(x*3 + y*5 + x*y) % count for x in range(11)] for y in range(6)]
            rows = [pack(row, depth, padding=255) for row in samples]
            expected = [[palette[i] + (alpha[i] if i < len(alpha) else 255,) for i in row]
                        for row in samples]
            for kind in range(5):
                with self.subTest(depth=depth, filter=kind):
                    before = chunk(b'PLTE', palette_bytes(palette)) + chunk(b'tRNS', alpha)
                    data = png(11, 6, 3, depth, filter_rows(rows, 1, (kind,)), before=before, split=1)
                    self.assert_pixels(data, expected)
                    self.assert_pixels(data, expected, rect={'x': 1, 'y': 2, 'width': 9, 'height': 3})

    def test_grayscale_alpha_all_filters_preserve_unassociated_color_and_alpha(self):
        expected = [[((x*43+y*59) % 256,)*3 + ((0, 1, 127, 254, 255)[(x+y) % 5],)
                     for x in range(9)] for y in range(7)]
        rows = [bytes(channel for pixel in row for channel in (pixel[0], pixel[3]))
                for row in expected]
        for kind in range(5):
            with self.subTest(filter=kind):
                data = png(9, 7, 4, 8, filter_rows(rows, 2, (kind,)))
                self.assert_pixels(data, expected)
                self.assert_pixels(data, expected, rect={'x': 2, 'y': 1, 'width': 6, 'height': 5})

    def test_grayscale_transparency_matches_raw_sample_and_masks_unused_bits(self):
        for depth in (1, 2, 4, 8):
            maximum = (1 << depth) - 1
            key = maximum//2
            samples = [[0, key, maximum, (key+1) & maximum, key]]
            expected = [[(value*255//maximum,)*3 + (0 if value == key else 255,)
                         for value in samples[0]]]
            # Per PNG3, an encoder's unused upper key bits must be ignored.
            for encoded_key in (key, key | (65535 ^ maximum)):
                with self.subTest(depth=depth, key=encoded_key):
                    data = png(5, 1, 0, depth, b'\0'+pack(samples[0], depth, padding=255),
                               before=chunk(b'tRNS', struct.pack('>H', encoded_key)))
                    self.assert_pixels(data, expected)

    def test_rgb_transparency_requires_complete_exact_key_and_masks_unused_bits(self):
        samples = [(17, 83, 249), (18, 83, 249), (17, 84, 249), (17, 83, 248), (17, 83, 249)]
        expected = [[pixel + (0 if pixel == samples[0] else 255,) for pixel in samples]]
        for high in (0, 0xFF00):
            for kind in range(5):
                with self.subTest(high=high, filter=kind):
                    key = struct.pack('>HHH', *(value | high for value in samples[0]))
                    data = png(5, 1, 2, 8, filter_rows([palette_bytes(samples)], 3, (kind,)),
                               before=chunk(b'tRNS', key))
                    self.assert_pixels(data, expected)

    def test_single_pixel_packed_padding_is_not_an_extra_palette_index(self):
        palette = [(7, 23, 91)]
        for depth in (1, 2, 4):
            with self.subTest(depth=depth):
                data = png(1, 1, 3, depth, b'\0'+pack([0], depth, padding=255),
                           before=chunk(b'PLTE', palette_bytes(palette)))
                self.assert_pixels(data, [[(7, 23, 91, 255)]])

    def test_empty_indexed_transparency_is_all_opaque_and_optional_truecolor_palette_is_inert(self):
        palette = [(3, 9, 27), (5, 25, 125)]
        data = png(2, 1, 3, 1, b'\0'+pack([0, 1], 1),
                   before=chunk(b'PLTE', palette_bytes(palette))+chunk(b'tRNS', b''))
        self.assert_pixels(data, [[color+(255,) for color in palette]])
        for color, row in ((2, b'\x09\x19\x29'), (6, b'\x09\x19\x29\x39')):
            with self.subTest(color=color):
                data = png(1, 1, color, 8, b'\0'+row, before=chunk(b'PLTE', palette_bytes(palette)))
                self.assert_pixels(data, [[(9, 25, 41, 255 if color == 2 else 57)]])

    def test_packed_overview_center_sampling_and_output_bound_are_exact(self):
        width, height, depth = 1921, 5, 2
        samples = [[(x+2*y) % 4 for x in range(width)] for y in range(height)]
        data = png(width, height, 0, depth,
                   filter_rows([pack(row, depth, padding=255) for row in samples], 1))
        result = preview_png(data, mode='overview')
        # Independent geometry calculation: 1921x5 -> 960x2, centers in rows 1,3.
        expected = [[(samples[y][(2*x+1)*1921//1920]*85,)*3+(255,) for x in range(960)]
                    for y in (1, 3)]
        self.assertEqual(rgba_result(result)[:2], ((960, 2), rgba_bytes(expected)))
        self.assertEqual(result['resampling'], 'nearest-center')

    def test_malformed_palette_and_transparency_metadata_are_unavailable(self):
        palette = chunk(b'PLTE', b'\x00\x11\x22\x33\x44\x55')
        transparent = chunk(b'tRNS', b'\0')
        indexed = dict(width=1, height=1, color=3, depth=1, raw=b'\0\0')
        cases = {
            'missing palette': png(**indexed),
            'empty palette': png(**indexed, before=chunk(b'PLTE', b'')),
            'incomplete RGB entry': png(**indexed, before=chunk(b'PLTE', b'12')),
            'over 256 entries': png(**indexed, before=chunk(b'PLTE', bytes(257*3))),
            'entries exceed bit depth': png(**indexed, before=chunk(b'PLTE', bytes(3*3))),
            'duplicate palette': png(**indexed, before=palette+palette),
            'palette after IDAT': png(**indexed, after=palette),
            'transparency before palette': png(**indexed, before=transparent+palette),
            'transparency after IDAT': png(**indexed, before=palette, after=transparent),
            'duplicate transparency': png(**indexed, before=palette+transparent+transparent),
            'too many palette alpha values': png(**indexed, before=palette+chunk(b'tRNS', b'123')),
            'gray palette prohibited': png(1, 1, 0, 8, b'\0\0', before=palette),
            'gray alpha palette prohibited': png(1, 1, 4, 8, b'\0\0\0', before=palette),
            'gray alpha transparency prohibited': png(1, 1, 4, 8, b'\0\0\0', before=transparent),
            'rgba transparency prohibited': png(1, 1, 6, 8, b'\0'*5, before=transparent),
            'RGB palette after transparency': png(1, 1, 2, 8, b'\0'*4,
                                                   before=chunk(b'tRNS', bytes(6))+palette),
        }
        for color, valid_length, raw in ((0, 2, b'\0\0'), (2, 6, b'\0'*4)):
            for length in (0, 1, valid_length-1, valid_length+1):
                cases[f'tRNS color {color} length {length}'] = png(
                    1, 1, color, 8, raw, before=chunk(b'tRNS', bytes(length)))
        for name, data in cases.items():
            with self.subTest(case=name):
                self.assertFalse(preview_capability(data)['available'])
                with self.assertRaises(PngPreviewError):
                    preview_png(data, mode='overview')

    def test_invalid_palette_index_is_rejected_even_outside_crop_or_overview_sample(self):
        palette = chunk(b'PLTE', b'\x12\x34\x56\x65\x43\x21')
        for depth in (2, 4, 8):
            for point in ((0, 0), (6, 1), (0, 4)):
                with self.subTest(depth=depth, invalid=point):
                    samples = [[(x+y) % 2 for x in range(7)] for y in range(5)]
                    samples[point[1]][point[0]] = 2
                    data = png(7, 5, 3, depth, filter_rows([pack(row, depth) for row in samples], 1), before=palette)
                    self.assertTrue(preview_capability(data)['available'])
                    with self.assertRaises(PngPreviewError):
                        preview_png(data, mode='crop', rect={'x': 2, 'y': 1, 'width': 2, 'height': 2})
        samples = [0]*1920
        samples[0] = 2  # Output samples odd x only, so this pixel is not selected.
        data = png(1920, 1, 3, 2, b'\0'+pack(samples, 2), before=palette)
        with self.assertRaises(PngPreviewError):
            preview_png(data, mode='overview')

    def test_new_encodings_require_complete_exact_stream_and_valid_filter_on_all_rows(self):
        palette = chunk(b'PLTE', bytes(6))
        for color, depth, stride, before in ((0, 2, 2, b''), (3, 4, 3, palette), (4, 8, 10, b'')):
            raw = b'\0' + bytes(stride)
            stream = zlib.compress(raw)
            cases = (zlib.compress(raw[:-1]), zlib.compress(raw+b'\0'), stream[:-1],
                     stream+zlib.compress(b''), zlib.compress(bytes([5])+raw[1:]))
            for compressed in cases:
                with self.subTest(color=color, compressed=compressed):
                    data = png(5, 1, color, depth, b'', before=before, compressed=compressed)
                    with self.assertRaises(PngPreviewError):
                        preview_png(data, mode='overview')
            raw = b'\0'+bytes(stride)+b'\5'+bytes(stride)
            data = png(5, 2, color, depth, raw, before=before)
            with self.assertRaises(PngPreviewError):
                preview_png(data, mode='crop', rect={'x': 0, 'y': 0, 'width': 1, 'height': 1})

    def test_unsupported_depths_interlace_and_color_management_stay_unavailable(self):
        encodings = [(3, 16, 0)]
        encodings += [(0, 3, 0), (2, 4, 0), (4, 4, 0), (6, 4, 0), (1, 8, 0), (5, 8, 0)]
        for color, depth, interlace in encodings:
            with self.subTest(color=color, depth=depth, interlace=interlace):
                data = png(1, 1, color, depth, b'\0'*9, interlace=interlace)
                self.assertFalse(preview_capability(data)['available'])
                with self.assertRaises(PngPreviewError):
                    preview_png(data, mode='overview')
        for kind in (b'gAMA', b'cHRM', b'iCCP', b'sRGB', b'sBIT', b'cICP', b'mDCV', b'cLLI', b'eXIf', b'acTL'):
            with self.subTest(chunk=kind):
                data = png(1, 1, 0, 8, b'\0\0', before=chunk(kind, b''))
                self.assertFalse(preview_capability(data)['available'])
                with self.assertRaises(PngPreviewError):
                    preview_png(data, mode='overview')

    def test_capability_accepts_supported_headers_without_decoding_pixels(self):
        formats = [(color, depth) for color in (0, 3) for depth in (1, 2, 4, 8)] + [(4, 8)]
        for color, depth in formats:
            with self.subTest(color=color, depth=depth):
                before = chunk(b'PLTE', bytes(6)) if color == 3 else b''
                data = png(11, 7, color, depth, b'', before=before, compressed=b'invalid')
                with patch('nivram.png_preview.zlib.decompressobj', side_effect=AssertionError('must not decode')):
                    self.assertEqual(preview_capability(data), {'available': True, 'reason': None, 'width': 11, 'height': 7})

    def test_new_formats_keep_source_filtered_output_budget_and_deadline_limits(self):
        data = png(9, 3, 0, 2, filter_rows([pack([0, 1, 2]*3, 2)]*3, 1))
        for setting, value in (('MAX_SOURCE_BYTES', len(data)-1), ('MAX_CHUNKS', 2),
                               ('MAX_FILTERED_BYTES', 11), ('PIXEL_PIPELINE_BUDGET', 1),
                               ('MAX_PREVIEW_BYTES', 64)):
            with self.subTest(limit=setting), patch('nivram.png_preview.'+setting, value):
                with self.assertRaises(PngPreviewError):
                    preview_png(data, mode='overview')
        with patch('nivram.png_preview.time.monotonic', side_effect=(0, 46)):
            with self.assertRaises(TimeoutError):
                preview_png(data, mode='overview')
        for width, height in ((0, 1), (32769, 1), (1, 32769), (32768, 4000)):
            with self.subTest(dimensions=(width, height)), self.assertRaises(PngPreviewError):
                preview_png(png(width, height, 0, 1, b'\0\0'), mode='overview')

    def test_new_formats_pass_worker_validation_and_strip_source_text(self):
        from nivram.png_preview_jobs import validate_preview_result
        for color, depth, row, before in ((0, 1, pack([0, 1, 0], 1), b''),
                                          (3, 2, pack([1, 0, 1], 2), chunk(b'PLTE', bytes(6))),
                                          (4, 8, bytes([1, 0, 2, 127, 3, 255]), b'')):
            with self.subTest(color=color):
                data = png(3, 2, color, depth, filter_rows([row, row], 2 if color == 4 else 1),
                           before=before+chunk(b'tEXt', b'private\0must not survive'))
                for mode, rect in (('overview', None), ('crop', {'x': 1, 'y': 0, 'width': 2, 'height': 1})):
                    result = preview_png(data, mode=mode, rect=rect)
                    checked = validate_preview_result(result, hashlib.sha256(data).hexdigest(), mode, rect, (3, 2))
                    self.assertEqual(checked['pngSha256'], result['pngSha256'])
                    self.assertEqual(rgba_result(result)[2], {})


if __name__ == '__main__':
    unittest.main()
