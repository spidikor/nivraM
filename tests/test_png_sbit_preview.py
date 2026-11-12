"""Strict sBIT admission for stored-sample display, not precision recovery.

Synthetic source chunks/pixels are independent of the production writer. The
small literal pixel tables are the oracle; tagged/untagged agreement is only an
additional check. The existing independent pass map/filter encoder supplies
filtered row bytes. No private image corpus or optical provider is used.
"""
import base64
import hashlib
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from PIL import Image

from nivram import png_preview as pp
from nivram.png_preview_jobs import ImagePreviewJobs, validate_preview_result
from test_png_adam7_preview import PASS_LABELS, filtered

SIGNATURE = b'\x89PNG\r\n\x1a\n'
DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8),
          4: (8, 16), 6: (8, 16)}
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
SBIT_CHANNELS = {0: 1, 2: 3, 3: 3, 4: 2, 6: 4}
CHROMA = struct.pack('>8I', 31270, 32900, 64000, 33000,
                     30000, 60000, 15000, 6000)


def chunk(kind, body):
    return (struct.pack('>I', len(body)) + kind + body
            + struct.pack('>I', zlib.crc32(kind + body)))


def chunks(data):
    at = 8
    while at < len(data):
        length = int.from_bytes(data[at:at+4], 'big')
        yield data[at+4:at+8], data[at+8:at+8+length]
        at += length + 12


def packed(pixels, depth):
    samples = [s for pixel in pixels for s in pixel]
    if depth == 16:
        return b''.join(struct.pack('>H', sample) for sample in samples)
    if depth == 8:
        return bytes(samples)
    bits = ''.join(format(sample, f'0{depth}b') for sample in samples)
    bits += '0' * (-len(bits) % 8)
    return bytes(int(bits[i:i+8], 2) for i in range(0, len(bits), 8))


def fixture(rows, color, depth, interlace=0, kind=0, significant=None,
            metadata=b'', palette=None, key=None, alpha=None):
    before = (chunk(b'sBIT', bytes(significant)) if significant is not None else b'') + metadata
    if palette is not None:
        before += chunk(b'PLTE', bytes(v for pixel in palette for v in pixel))
    if key is not None:
        before += chunk(b'tRNS', b''.join(struct.pack('>H', s) for s in key))
    if alpha is not None:
        before += chunk(b'tRNS', bytes(alpha))
    raw = bytearray()
    for number in (range(1, 8) if interlace else (None,)):
        previous = None
        for y, row in enumerate(rows):
            chosen = [pixel for x, pixel in enumerate(row)
                      if number is None or PASS_LABELS[y % 8][x % 8] == number]
            if not chosen:
                continue
            encoded = packed(chosen, depth)
            if previous is None:
                previous = bytes(len(encoded))
            raw.extend(filtered(encoded, previous, kind,
                                max(1, (CHANNELS[color] * depth + 7) // 8)))
            previous = encoded
    return (SIGNATURE + chunk(b'IHDR', struct.pack('>IIBBBBB', len(rows[0]), len(rows),
                                                  depth, color, 0, 0, interlace))
            + before + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def literal_case(color, depth):
    gray = {1: (0, 1, 0, 1), 2: (0, 1, 2, 3), 4: (0, 1, 8, 15),
            8: (0, 17, 128, 255), 16: (0, 129, 32768, 65535)}[depth]
    reduced = {1: (0, 255, 0, 255), 2: (0, 85, 170, 255),
               4: (0, 17, 136, 255), 8: (0, 17, 128, 255),
               16: (0, 1, 128, 255)}[depth]
    if color == 3:
        return ([(0,), (1,), (1,), (0,)],
                [(224, 132, 17, 0), (3, 251, 128, 170),
                 (3, 251, 128, 170), (224, 132, 17, 0)],
                {'palette': [(224, 132, 17), (3, 251, 128)], 'alpha': (0, 170)})
    if color in (0, 4):
        pixels = [(s,) for s in gray]
        expected = [(s, s, s) for s in reduced]
    elif depth == 8:
        pixels = [(224, 132, 17), (3, 251, 128), (255, 0, 64), (0, 255, 255)]
        expected = list(pixels)
    else:
        pixels = [(128, 129, 32768), (257, 514, 771),
                  (65535, 65406, 65407), (0, 12345, 54321)]
        expected = [(0, 1, 128), (1, 2, 3), (255, 254, 255), (0, 48, 211)]
    if color in (4, 6):
        alpha = (0, 170, 128, 255) if depth == 8 else (0, 43690, 32768, 65535)
        pixels = [p + (a,) for p, a in zip(pixels, alpha)]
        expected = [p + (a,) for p, a in zip(expected, (0, 170, 128, 255))]
    return pixels, expected, {}


def sbit(color, depth):
    limit = 8 if color == 3 else depth
    values = tuple(max(1, limit - i) for i in range(SBIT_CHANNELS[color]))
    # Arbitrary alpha fixtures declare full alpha precision. The dedicated
    # lower-precision cases use exactly scaled endpoints/partials instead.
    return values[:-1] + (depth,) if color in (4, 6) else values


def read(result):
    data = base64.b64decode(result['pngBase64'])
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        return list(image.getdata()), dict(image.info), data


class SignificantBitsTests(unittest.TestCase):
    def assert_rejected(self, data):
        self.assertFalse(pp.preview_capability(data)['available'])
        with self.assertRaises(pp.PngPreviewError):
            pp.preview_png(data, mode='overview')

    def test_all_15_formats_both_interlaces_and_five_filters(self):
        for color, depths in DEPTHS.items():
            for depth in depths:
                pixels, expected, options = literal_case(color, depth)
                rows = [[pixels[(x + y) % 4] for x in range(9)] for y in range(11)]
                wanted = [expected[(x + y) % 4] for y in range(11) for x in range(9)]
                for interlace in (0, 1):
                    for kind in range(5):
                        with self.subTest(color=color, depth=depth, interlace=interlace, filter=kind):
                            data = fixture(rows, color, depth, interlace, kind,
                                           significant=sbit(color, depth), **options)
                            # Independent loading checks pixel-stream compatibility,
                            # not sBIT validation (Pillow may ignore that chunk).
                            # Its multichannel16 truncation is not our rounding oracle.
                            with Image.open(io.BytesIO(data)) as independent:
                                independent.load()
                                self.assertEqual(independent.size, (9, 11))
                                if depth != 16:
                                    mode = 'RGBA' if color in (3, 4, 6) else 'RGB'
                                    self.assertEqual(list(independent.convert(mode).getdata()), wanted)
                            self.assertTrue(pp.preview_capability(data)['available'])
                            before = hashlib.sha256(data).hexdigest()
                            result = pp.preview_png(data, mode='overview', expected_source_sha256=before)
                            actual, info, derivative = read(result)
                            self.assertEqual(actual, wanted)
                            self.assertEqual(info, {})
                            self.assertNotIn(b'sBIT', [k for k, _ in chunks(derivative)])
                            self.assertEqual(result['sourceSha256'], before)
                            self.assertEqual(hashlib.sha256(data).hexdigest(), before)
                            validate_preview_result(result, before, 'overview', None, (9, 11))
                            control = fixture(rows, color, depth, interlace, kind, **options)
                            untagged = pp.preview_png(control, mode='overview')
                            self.assertEqual(result['pngSha256'], untagged['pngSha256'])
                            self.assertEqual(result['engine'], untagged['engine'])

    def test_stored_samples_not_original_precision_recovery(self):
        for interlace in (0, 1):
            data = fixture([[(224, 132, 17, 170), (224, 132, 17, 0)]], 6, 8,
                           interlace, significant=(5, 6, 5, 2))
            self.assertEqual(read(pp.preview_png(data, mode='overview'))[0],
                             [(224, 132, 17, 170), (224, 132, 17, 0)])
            # Palette depth is 8 even when indices occupy one bit, and tRNS
            # introduces no fourth sBIT component.
            data = fixture([[(0,), (1,)]], 3, 1, interlace, significant=(8, 8, 8),
                           palette=[(224, 132, 17), (3, 251, 128)], alpha=(0, 170))
            self.assertEqual(read(pp.preview_png(data, mode='overview'))[0],
                             [(224, 132, 17, 0), (3, 251, 128, 170)])

    def test_transparency_full_precision_and_rounding_boundaries(self):
        for interlace in (0, 1):
            for color, key, expected in (
                    (0, (257,), [(1, 1, 1, 0), (1, 1, 1, 255)]),
                    (2, (257, 514, 771), [(1, 2, 3, 0), (1, 2, 3, 255)])):
                rows = [[key, tuple(s + 1 for s in key)]]
                data = fixture(rows, color, 16, interlace, significant=(8,) * len(key), key=key)
                self.assertEqual(read(pp.preview_png(data, mode='overview'))[0], expected)
            values = (0, 128, 129, 256, 257, 65406, 65407, 65535)
            data = fixture([[(s,) for s in values]], 0, 16, interlace, significant=(7,))
            self.assertEqual(read(pp.preview_png(data, mode='overview'))[0],
                             [(s, s, s) for s in (0, 0, 1, 1, 1, 254, 255, 255)])

    def test_exact_crop_overview_and_source_identity(self):
        pixels, expected, _ = literal_case(6, 16)
        rows = [[pixels[(x + y) % 4] for x in range(9)] for y in range(11)]
        for interlace in (0, 1):
            data = fixture(rows, 6, 16, interlace, 4, significant=(8, 9, 10, 16))
            digest = hashlib.sha256(data).hexdigest()
            rect = {'x': 2, 'y': 3, 'width': 3, 'height': 2}
            result = pp.preview_png(data, mode='crop', rect=rect, expected_source_sha256=digest)
            self.assertEqual(read(result)[0], [expected[(x + y) % 4] for y in (3, 4) for x in (2, 3, 4)])
            self.assertEqual(result['rect'], rect)
            self.assertEqual(result['resampling'], 'none')
            self.assertEqual(result['sourceSha256'], digest)
            with patch.object(pp, 'OVERVIEW_EDGE', 3):
                result = pp.preview_png(data, mode='overview')
            self.assertEqual((result['width'], result['height']), (2, 3))
            self.assertEqual(read(result)[0], [expected[(x + y) % 4] for y in (1, 5, 9) for x in (2, 6)])
            self.assertEqual(result['resampling'], 'nearest-center')
            with self.assertRaises(pp.PngPreviewError):
                pp.preview_png(data, mode='overview', expected_source_sha256='0' * 64)

    def test_gamma_and_srgb_use_stored_color_not_alpha(self):
        for interlace in (0, 1):
            gamma = chunk(b'gAMA', struct.pack('>I', 100000)) + chunk(b'cHRM', CHROMA)
            data = fixture([[(128, 32768, 65535, 43690), (128, 32768, 65535, 0)]],
                           6, 16, interlace, significant=(8, 9, 10, 2), metadata=gamma)
            result = pp.preview_png(data, mode='overview')
            self.assertEqual(read(result)[:2], ([(6, 188, 255, 170), (6, 188, 255, 0)], {'srgb': 1}))
            self.assertEqual(result['engine']['colorConversion']['sourceGammaInteger'], 100000)
            canonical = chunk(b'gAMA', struct.pack('>I', 45455)) + chunk(b'cHRM', CHROMA)
            for intent in range(4):
                srgb = chunk(b'sRGB', bytes([intent])) + canonical
                for metadata, significant in ((srgb, (5, 6, 5, 2)),
                                               (srgb + chunk(b'sBIT', bytes((5, 6, 5, 2))), None)):
                    data = fixture([[(224, 132, 17, 170)]], 6, 8, interlace,
                                   significant=significant, metadata=metadata)
                    self.assertEqual(read(pp.preview_png(data, mode='overview'))[:2],
                                     ([(224, 132, 17, 170)], {'srgb': intent}))

    def test_invalid_lengths_zero_and_every_overdepth_component(self):
        for color, depths in DEPTHS.items():
            for depth in depths:
                pixels, _, options = literal_case(color, depth)
                valid = sbit(color, depth)
                limit = 8 if color == 3 else depth
                invalid = [(), valid[:-1], valid + (1,)]
                for index in range(len(valid)):
                    for value in (0, limit + 1, 255):
                        item = list(valid)
                        item[index] = value
                        invalid.append(item)
                for significant in invalid:
                    with self.subTest(color=color, depth=depth, significant=significant):
                        self.assert_rejected(fixture([pixels], color, depth,
                                                     significant=significant, **options))

    def test_one_and_maximum_significant_depths_are_legal(self):
        for color, depths in DEPTHS.items():
            for depth in depths:
                pixels, expected, options = literal_case(color, depth)
                for value in (1, 8 if color == 3 else depth):
                    if color in (4, 6) and value == 1:
                        # One significant alpha bit has only opaque/transparent
                        # endpoints; alpha zero-fill is not a conforming encoder.
                        pixels = [p[:-1] + (a,) for p, a in zip(pixels, (0, (1 << depth) - 1, 0, (1 << depth) - 1))]
                        expected = [p[:-1] + (a,) for p, a in zip(expected, (0, 255, 0, 255))]
                    for interlace in (0, 1):
                        with self.subTest(color=color, depth=depth, value=value, interlace=interlace):
                            data = fixture([pixels], color, depth, interlace,
                                           significant=(value,) * SBIT_CHANNELS[color], **options)
                            self.assertEqual(read(pp.preview_png(data, mode='overview'))[0], expected)

    def test_sbit_after_transparent_key_without_palette_is_legal(self):
        # PNG orders sBIT before PLTE/IDAT, not relative to tRNS. These
        # grayscale/truecolor files have no PLTE; their full source keys survive.
        for color in (0, 2):
            for depth in (8, 16):
                key = (17,) if color == 0 else (17, 31, 47)
                other = tuple(s + 1 for s in key)
                for interlace in (0, 1):
                    data = fixture([[key, other]], color, depth, interlace, key=key)
                    data = SIGNATURE + b''.join(
                        chunk(k, v) + (chunk(b'sBIT', bytes(sbit(color, depth))) if k == b'tRNS' else b'')
                        for k, v in chunks(data))
                    result = pp.preview_png(data, mode='overview')
                    expected = ([(17, 17, 17, 0), (18, 18, 18, 255)] if color == 0
                                else [(17, 31, 47, 0), (18, 32, 48, 255)])
                    if depth == 16:
                        expected = [(0, 0, 0, 0), (0, 0, 0, 255)]
                    self.assertEqual(read(result)[0], expected)

    def test_duplicate_late_palette_order_crc_and_length_rejected(self):
        for color in (2, 3, 6):
            pixels, _, options = literal_case(color, 8)
            options['palette'] = [(224, 132, 17), (3, 251, 128)]
            tag = chunk(b'sBIT', bytes(sbit(color, 8)))
            good = fixture([pixels], color, 8, significant=sbit(color, 8), **options)
            self.assertTrue(pp.preview_capability(good)['available'])
            self.assert_rejected(good[:33] + tag + good[33:])
            control = fixture([pixels], color, 8, **options)
            entries = list(chunks(control))
            # Includes optional PLTE on RGB/RGBA, not just indexed PLTE.
            for after in (b'PLTE', b'IDAT'):
                bad = SIGNATURE + b''.join(chunk(k, v) + (tag if k == after else b'') for k, v in entries)
                self.assert_rejected(bad)
            self.assert_rejected(SIGNATURE + tag + control[8:])
            bad = bytearray(good)
            bad[33 + len(tag) - 1] ^= 1
            self.assert_rejected(bytes(bad))
            bad = bytearray(good)
            bad[33:37] = struct.pack('>I', len(good))
            self.assert_rejected(bytes(bad))
            self.assert_rejected(good[:33 + len(tag) - 1])

    def test_sbit_does_not_admit_other_unsupported_metadata(self):
        rejected = [chunk(k, b'x') for k in (b'iCCP', b'cICP', b'mDCV', b'cLLI', b'eXIf', b'acTL', b'abCd')]
        rejected += [chunk(b'gAMA', struct.pack('>I', 100000)),
                     chunk(b'cHRM', CHROMA),
                     chunk(b'gAMA', struct.pack('>I', 100000)) + chunk(b'cHRM', CHROMA[:-1] + b'\x71'),
                     chunk(b'gAMA', struct.pack('>I', 100000)) + chunk(b'sRGB', b'\0')]
        for metadata in rejected:
            self.assert_rejected(fixture([[(1, 2, 3)]], 2, 8, significant=(8, 8, 8), metadata=metadata))

    def test_derivative_allowlist_still_rejects_sbit(self):
        source = fixture([[(1, 2, 3)]], 2, 8, significant=(8, 8, 8))
        result = pp.preview_png(source, mode='overview')
        _, _, data = read(result)
        forged = data[:33] + chunk(b'sBIT', b'\x08\x08\x08') + data[33:]
        result.update(pngBase64=base64.b64encode(forged).decode('ascii'),
                      pngSha256=hashlib.sha256(forged).hexdigest())
        with self.assertRaises(ValueError):
            validate_preview_result(result, hashlib.sha256(source).hexdigest(), 'overview', None, (1, 1))

    def test_full_stream_outside_crop_and_resource_guards(self):
        for interlace in (0, 1):
            source = fixture([[(1, 2, 3)], [(4, 5, 6)]], 2, 8, interlace, significant=(5, 6, 5))
            entries = list(chunks(source))
            stream = next(v for k, v in entries if k == b'IDAT')
            raw = zlib.decompress(stream)
            invalid = (zlib.compress(raw[:4] + b'\x05' + raw[5:]),
                       zlib.compress(raw[:-1]), zlib.compress(raw + b'x'),
                       stream[:-1], stream + zlib.compress(b''))
            for payload in invalid:
                bad = SIGNATURE + b''.join(chunk(k, payload if k == b'IDAT' else v) for k, v in entries)
                with self.assertRaises(pp.PngPreviewError):
                    pp.preview_png(bad, mode='crop', rect={'x': 0, 'y': 0, 'width': 1, 'height': 1})
            for name, bound in (('MAX_SOURCE_BYTES', len(source) - 1), ('MAX_CHUNKS', 3),
                                ('MAX_PIXELS', 1), ('MAX_EDGE', 1),
                                ('MAX_FILTERED_BYTES', len(raw) - 1),
                                ('PIXEL_PIPELINE_BUDGET', 1), ('MAX_PREVIEW_BYTES', 40)):
                with self.subTest(interlace=interlace, limit=name), patch.object(pp, name, bound):
                    with self.assertRaises(pp.PngPreviewError):
                        pp.preview_png(source, mode='overview')
            with patch.object(pp.time, 'monotonic', side_effect=[0, 100]):
                with self.assertRaises(TimeoutError):
                    pp.preview_png(source, mode='overview', deadline_seconds=1)


class SignificantBitsWorkerTests(unittest.TestCase):
    def test_actual_jobs_supervisor_default_worker_and_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            jobs = ImagePreviewJobs(Path(temporary) / 'preview-cache')
            try:
                for interlace in (0, 1):
                    source = fixture([[(128, 32768, 65535, 0), (128, 32768, 65535, 43690)]],
                                     6, 16, interlace, significant=(8, 9, 10, 2),
                                     metadata=chunk(b'gAMA', struct.pack('>I', 100000)) + chunk(b'cHRM', CHROMA))
                    for mode in ('crop', 'overview'):
                        rect = {'x': 1, 'y': 0, 'width': 1, 'height': 1} if mode == 'crop' else None
                        result = jobs.preview(source, 'sbit_image_1', f'sbit_{mode}_{interlace}', mode, rect)
                        wanted = [(6, 188, 255, 170)] if mode == 'crop' else [(6, 188, 255, 0), (6, 188, 255, 170)]
                        self.assertEqual(read(result)[:2], (wanted, {'srgb': 1}))
                        self.assertEqual(result['sourceSha256'], hashlib.sha256(source).hexdigest())
                        self.assertEqual(result['bufferAccounting']['sourceSnapshotBytes'], len(source))
                        self.assertEqual(jobs._supervisor.active_count, 0)
                        self.assertIsNone(jobs._active)
                        self.assertEqual(list(jobs.directory.iterdir()), [])
            finally:
                self.assertTrue(jobs.close())


if __name__ == '__main__':
    unittest.main()
