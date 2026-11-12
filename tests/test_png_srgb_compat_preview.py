"""Canonical sRGB fallback metadata; synthetic fixtures only."""
import base64
import hashlib
import io
import itertools
import struct
import unittest

from PIL import Image
from nivram.png_preview import PngPreviewError, preview_capability, preview_png
from nivram.png_preview_jobs import validate_preview_result
from tests.test_png_srgb_preview import chunk, png
from tests.test_png_preview import raw_png
from tests.test_png_adam7_preview import png as adam7_png

GAMMA = struct.pack('>I', 45455)
CHROMA = struct.pack('>8I', 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)

class CanonicalFallbackTests(unittest.TestCase):
    def test_canonical_fallback_combinations_and_all_orders(self):
        for intent in range(4):
            srgb = chunk(b'sRGB', bytes([intent]))
            for extras in ((chunk(b'gAMA', GAMMA),), (chunk(b'cHRM', CHROMA),),
                           (chunk(b'gAMA', GAMMA), chunk(b'cHRM', CHROMA))):
                for ordering in itertools.permutations((srgb, *extras)):
                    source = png(b''.join(ordering))
                    for mode, rect in (('overview', None), ('crop', {'x': 0, 'y': 0, 'width': 1, 'height': 1})):
                        with self.subTest(intent=intent, extra=len(extras), mode=mode, ordering=ordering):
                            self.assertTrue(preview_capability(source)['available'])
                            result = preview_png(source, mode=mode, rect=rect)
                            self.assertEqual(result['sourceSha256'], hashlib.sha256(source).hexdigest())
                            checked = validate_preview_result(result, result['sourceSha256'], mode, rect, (2, 1))
                            derivative = base64.b64decode(checked['pngBase64'], validate=True)
                            self.assertEqual(derivative[33:46], srgb)
                            with Image.open(io.BytesIO(derivative)) as image:
                                self.assertEqual(image.info['srgb'], intent)
                                self.assertNotIn('gamma', image.info)
                                self.assertNotIn('chromaticity', image.info)
                                self.assertEqual(image.tobytes()[:4], b'\x11\x22\x33\x00')

    def test_indexed_and_adam7_sources_keep_pixels_and_intent(self):
        color = chunk(b'gAMA', GAMMA) + chunk(b'cHRM', CHROMA) + chunk(b'sRGB', b'\x03')
        indexed = raw_png(1, 1, b'\0\0', color=3, depth=1,
                          before=color+chunk(b'PLTE', b'\x12\x34\x56'))
        adam = adam7_png(1, 1, b'\0\x12\x34\x56', before=color)
        for source in (indexed, adam):
            with self.subTest(source=source):
                result = preview_png(source, mode='overview')
                with Image.open(io.BytesIO(base64.b64decode(result['pngBase64']))) as image:
                    self.assertEqual(image.info['srgb'], 3)
                    self.assertEqual(image.tobytes(), b'\x12\x34\x56')

    def test_standalone_wrong_duplicate_late_and_conflicting_metadata_rejected(self):
        s = chunk(b'sRGB', b'\x00'); g = chunk(b'gAMA', GAMMA); c = chunk(b'cHRM', CHROMA)
        malformed = [chunk(b'gAMA', b''), chunk(b'gAMA', struct.pack('>I',45454)),
                     chunk(b'cHRM', CHROMA[:-1]), chunk(b'cHRM', CHROMA[:-1]+b'\x71')]
        cases = [png(before) for before in (g, c, s+g+g, s+c+c, *(s+x for x in malformed))]
        cases += [png(s, after=g), png(s, after=c), png(s+g+chunk(b'iCCP', b'x\0\0junk'))]
        palette = chunk(b'PLTE', b'\x12\x34\x56')
        cases += [raw_png(1, 1, b'\0\0', color=3, depth=1,
                          before=s+palette+fallback) for fallback in (g, c)]
        for data in cases:
            with self.subTest(data=data):
                self.assertFalse(preview_capability(data)['available'])
                with self.assertRaises(PngPreviewError):
                    preview_png(data, mode='overview')

if __name__ == '__main__':
    unittest.main()
