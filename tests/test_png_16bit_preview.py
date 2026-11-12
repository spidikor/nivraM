"""Handcrafted 16-bit PNG rows; expected pixels do not use a source decoder."""
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
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


def png(width, height, color, raw, *, before=b'', stream=None, split=37, interlace=0):
    header = struct.pack('>IIBBBBB', width, height, 16, color, 0, 0, interlace)
    compressed = zlib.compress(raw) if stream is None else stream
    return (SIGNATURE + chunk(b'IHDR', header) + before
            + b''.join(chunk(b'IDAT', compressed[i:i+split]) for i in range(0, len(compressed), split))
            + chunk(b'IEND', b''))


def encode_rows(rows, channels, filters=(0, 1, 2, 3, 4)):
    """Independent PNG encoder math, operating on big-endian sample bytes."""
    previous = bytes(len(rows[0])*channels*2)
    result = bytearray()
    for y, pixels in enumerate(rows):
        row = b''.join(struct.pack('>H', sample) for pixel in pixels for sample in pixel)
        kind = filters[y % len(filters)]
        result.append(kind)
        for index, value in enumerate(row):
            left = row[index-channels*2] if index >= channels*2 else 0
            up = previous[index]
            corner = previous[index-channels*2] if index >= channels*2 else 0
            if kind == 0:
                predictor = 0
            elif kind == 1:
                predictor = left
            elif kind == 2:
                predictor = up
            elif kind == 3:
                predictor = (left+up)//2
            else:
                distances = (abs(up-corner), abs(left-corner), abs(left+up-2*corner))
                predictor = (left, up, corner)[distances.index(min(distances))]
            result.append((value-predictor) % 256)
        previous = row
    return bytes(result)


def expected_rgba(rows, color, key=None):
    def scale(value):
        # W3C rounded linear rescaling, expressed without the implementation's
        # simplification to division by 257.
        return (value*255 + 32767)//65535
    output = []
    for row in rows:
        converted = []
        for pixel in row:
            if color in (0, 4):
                rgb = (scale(pixel[0]),)*3
            else:
                rgb = tuple(scale(value) for value in pixel[:3])
            alpha = scale(pixel[-1]) if color in (4, 6) else (0 if pixel == key else 255)
            converted.append((*rgb, alpha))
        output.append(converted)
    return output


def decoded(result):
    encoded = base64.b64decode(result['pngBase64'], validate=True)
    assert hashlib.sha256(encoded).hexdigest() == result['pngSha256']
    assert encoded[24] == 8 and encoded[25] in (2, 6)
    with Image.open(io.BytesIO(encoded)) as image:
        return image.size, image.convert('RGBA').tobytes(), dict(image.info)


class Png16BitPreviewTests(unittest.TestCase):
    def assert_pixels(self, data, expected, *, rect=None):
        result = preview_png(data, mode='overview' if rect is None else 'crop', rect=rect)
        selected = expected if rect is None else [row[rect['x']:rect['x']+rect['width']]
                    for row in expected[rect['y']:rect['y']+rect['height']]]
        self.assertEqual(decoded(result), ((len(selected[0]), len(selected)),
            bytes(channel for row in selected for pixel in row for channel in pixel), {}))
        self.assertEqual(result['sourceSha256'], hashlib.sha256(data).hexdigest())
        return result

    def test_all_four_formats_all_five_byte_filters_crop_and_overview(self):
        for color, channels in CHANNELS.items():
            rows = [[tuple((x*7717+y*13729+c*19891+x*y*127) % 65536 for c in range(channels))
                     for x in range(9)] for y in range(7)]
            expected = expected_rgba(rows, color)
            for kind in range(5):
                with self.subTest(color=color, filter=kind):
                    data = png(9, 7, color, encode_rows(rows, channels, (kind,)), split=1)
                    self.assertTrue(preview_capability(data)['available'])
                    result = self.assert_pixels(data, expected)
                    self.assert_pixels(data, expected, rect={'x': 2, 'y': 1, 'width': 6, 'height': 5})
                    self.assertEqual(result['bufferAccounting']['sourceRowBytes'], 9*channels*2)
                    self.assertEqual(result['bufferAccounting']['filteredBytesProcessed'], (9*channels*2+1)*7)

    def test_rounded_sample_and_alpha_edges_preserve_hidden_rgb(self):
        values = (0, 1, 128, 129, 255, 256, 257, 32767, 32768, 65406, 65407, 65534, 65535)
        scaled = (0, 0, 0, 1, 1, 1, 1, 127, 128, 254, 255, 255, 255)
        for color, channels in CHANNELS.items():
            with self.subTest(color=color):
                rows = [[(value,)*channels for value in values]]
                expected = [[(value, value, value, value if color in (4, 6) else 255) for value in scaled]]
                self.assert_pixels(png(len(values), 1, color, encode_rows(rows, channels)), expected)
        rows = [[(0x1234, 0x9abc, 0xfedc, 0), (0xdead, 0xbeef, 0x0102, 1)]]
        self.assert_pixels(png(2, 1, 6, encode_rows(rows, 4)), expected_rgba(rows, 6))

    def test_complete_sixteen_bit_sample_range_has_exact_rounded_output(self):
        rows = [[(y*256+x,) for x in range(256)] for y in range(256)]
        data = png(256, 256, 0, encode_rows(rows, 1), split=65536)
        self.assert_pixels(data, expected_rgba(rows, 0))

    def test_low_byte_changes_affect_later_filtered_rows_and_rounding(self):
        for color, channels in CHANNELS.items():
            for kind in (2, 3, 4):
                with self.subTest(color=color, filter=kind):
                    # Fixed high bytes straddle the round-to-nearest threshold
                    # through low bytes, including dependencies on prior rows.
                    rows = [[tuple((0x1200 if c % 2 else 0x3400) + ((x*43+y*97+c*31) % 256)
                                   for c in range(channels)) for x in range(11)] for y in range(6)]
                    data = png(11, 6, color, encode_rows(rows, channels, (kind,)))
                    self.assert_pixels(data, expected_rgba(rows, color))

    def test_transparent_keys_match_all_sixteen_bits_before_quantization(self):
        for color, key in ((0, (0x1234,)), (2, (0x1234, 0x5678, 0x9abc))):
            samples = [key, tuple(value+1 for value in key), key]
            samples.extend(tuple(value+1 if c == selected else value for c, value in enumerate(key))
                           for selected in range(len(key)))
            samples.extend(tuple(value-1 if c == selected else value for c, value in enumerate(key))
                           for selected in range(len(key)))
            rows = [samples, list(reversed(samples))]
            before = chunk(b'tRNS', b''.join(struct.pack('>H', value) for value in key))
            for kind in range(5):
                with self.subTest(color=color, filter=kind):
                    data = png(len(samples), 2, color, encode_rows(rows, CHANNELS[color], (kind,)), before=before)
                    self.assert_pixels(data, expected_rgba(rows, color, key))

    def test_overview_uses_center_samples_and_crops_do_not_rescale_geometry(self):
        width, height = 1921, 5
        rows = [[((x*113+y*151) % 65536,) for x in range(width)] for y in range(height)]
        data = png(width, height, 0, encode_rows(rows, 1))
        result = preview_png(data, mode='overview')
        expected = expected_rgba([[rows[y][(2*x+1)*1921//1920] for x in range(960)] for y in (1, 3)], 0)
        self.assertEqual(decoded(result)[:2], ((960, 2), bytes(c for row in expected for pixel in row for c in pixel)))
        self.assertEqual(result['resampling'], 'nearest-center')
        self.assertTrue(result['navigationOnly'])
        crop = self.assert_pixels(data, expected_rgba(rows, 0), rect={'x': 1919, 'y': 3, 'width': 2, 'height': 2})
        self.assertEqual(crop['resampling'], 'none')
        self.assertFalse(crop['navigationOnly'])

    def test_pillow_receives_only_eight_bit_original_width_two_row_carriers(self):
        actual_open = Image.open
        for color, channels in CHANNELS.items():
            rows = [[tuple((x*3557+y*799+c*123) % 65536 for c in range(channels))
                     for x in range(13)] for y in range(17)]
            data = png(13, 17, color, encode_rows(rows, channels, (3, 4)))
            seen = []
            def observe(source):
                encoded = source.getvalue()
                seen.append(struct.unpack('>IIBBBBB', encoded[16:29]))
                return actual_open(source)
            with self.subTest(color=color), patch('nivram.png_preview.Image.open', side_effect=observe):
                result = preview_png(data, mode='crop', rect={'x': 1, 'y': 1, 'width': 3, 'height': 3})
            self.assertEqual(seen, [(13, 2, 8, color, 0, 0, 0)]*(17*2))
            self.assertEqual(result['bufferAccounting']['maxCarrierHeight'], 2)
            self.assertLessEqual(result['bufferAccounting']['pixelPipelineAccountedUpperBoundBytes'], 16*1024*1024)

    def test_capability_checks_supported_headers_without_decoding(self):
        for color in CHANNELS:
            data = png(19, 11, color, b'', stream=b'not a zlib stream')
            with self.subTest(color=color), patch('nivram.png_preview.zlib.decompressobj', side_effect=AssertionError('No decoding')):
                self.assertEqual(preview_capability(data), {'available': True, 'reason': None, 'width': 19, 'height': 11})

    def test_exact_stream_filter_and_row_validation_includes_unselected_rows(self):
        for color, channels in CHANNELS.items():
            stride = 5*channels*2
            valid = b'\0'+bytes(stride)
            compressed = zlib.compress(valid)
            bad_streams = (zlib.compress(valid[:-1]), zlib.compress(valid+b'\0'),
                           compressed[:-1], compressed+zlib.compress(b''),
                           compressed+b'suffix', zlib.compress(b'\5'+bytes(stride)))
            for stream in bad_streams:
                with self.subTest(color=color, stream=stream), self.assertRaises(PngPreviewError):
                    preview_png(png(5, 1, color, b'', stream=stream), mode='overview')
            for last in (b'\5'+bytes(stride), b'\0'+bytes(stride-1), b'\0'+bytes(stride+1)):
                with self.subTest(color=color, last=last), self.assertRaises(PngPreviewError):
                    preview_png(png(5, 2, color, valid+last), mode='crop',
                                rect={'x': 0, 'y': 0, 'width': 1, 'height': 1})

    def test_metadata_errors_and_color_transforms_remain_rejected(self):
        for color, channels in CHANNELS.items():
            valid = b'\0'+bytes(channels*2)
            cases = []  # Legal Adam7 16-bit is covered by its dedicated fixture suite.
            cases.extend(png(1, 1, color, valid, before=chunk(kind, b'')) for kind in
                         (b'gAMA', b'cHRM', b'iCCP', b'sRGB', b'sBIT', b'eXIf', b'acTL', b'cICP'))
            if color in (4, 6):
                cases.append(png(1, 1, color, valid, before=chunk(b'tRNS', bytes(2))))
            else:
                cases.extend(png(1, 1, color, valid, before=chunk(b'tRNS', bytes(length)))
                             for length in (0, channels*2-1, channels*2+1))
            for data in cases:
                with self.subTest(color=color, chunk=data[37:41]):
                    self.assertFalse(preview_capability(data)['available'])
                    with self.assertRaises(PngPreviewError):
                        preview_png(data, mode='overview')

    def test_source_decompression_buffer_output_and_deadline_limits_are_unchanged(self):
        rows = [[(x*3001, y*7999, 65407, 129) for x in range(9)] for y in range(3)]
        data = png(9, 3, 6, encode_rows(rows, 4))
        for setting, value in (('MAX_SOURCE_BYTES', len(data)-1), ('MAX_CHUNKS', 2),
                               ('MAX_FILTERED_BYTES', (9*8+1)*3-1), ('PIXEL_PIPELINE_BUDGET', 1),
                               ('MAX_PREVIEW_BYTES', 64)):
            with self.subTest(limit=setting), patch('nivram.png_preview.'+setting, value):
                with self.assertRaises(PngPreviewError):
                    preview_png(data, mode='overview')
        with patch('nivram.png_preview.time.monotonic', side_effect=(0, 46)):
            with self.assertRaises(TimeoutError):
                preview_png(data, mode='overview')
        # Header alone rejects filtered expansion before creating a decoder.
        bomb = png(32768, 2048, 6, b'')
        with patch('nivram.png_preview.zlib.decompressobj', side_effect=AssertionError('No decoding')):
            self.assertFalse(preview_capability(bomb)['available'])
            with self.assertRaisesRegex(PngPreviewError, 'filtered output'):
                preview_png(bomb, mode='overview')

    def test_original_source_identity_and_worker_protocol_survive_reduction(self):
        from nivram.png_preview_jobs import validate_preview_result
        for color, channels in CHANNELS.items():
            rows = [[tuple((x*3111+y*5151+c*1777) % 65536 for c in range(channels))
                     for x in range(7)] for y in range(5)]
            data = png(7, 5, color, encode_rows(rows, channels),
                       before=chunk(b'tEXt', b'private\0source bytes stay separate'))
            original = bytes(data)
            for mode, rect in (('overview', None), ('crop', {'x': 2, 'y': 1, 'width': 3, 'height': 3})):
                result = preview_png(data, mode=mode, rect=rect)
                checked = validate_preview_result(result, hashlib.sha256(data).hexdigest(), mode, rect, (7, 5))
                self.assertEqual(checked['sourceSha256'], hashlib.sha256(original).hexdigest())
                self.assertEqual(decoded(result)[2], {})
            self.assertEqual(data, original)
            with self.assertRaisesRegex(PngPreviewError, 'identity'):
                preview_png(data, mode='overview', expected_source_sha256='0'*64)


if __name__ == '__main__':
    unittest.main()
