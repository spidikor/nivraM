"""Strict sRGB-only input and derivative coverage with synthetic pixels."""
import base64
import hashlib
import io
import struct
import unittest
import zlib

from PIL import Image
from nivram.png_preview import PngPreviewError, preview_capability, preview_png
from nivram.png_preview_jobs import _validate_derivative_png, validate_preview_result

SIG = b'\x89PNG\r\n\x1a\n'
def chunk(kind, body):
    return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(body, zlib.crc32(kind)))
def png(before=b'', after=b''):
    return (SIG + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 1, 8, 6, 0, 0, 0)) + before
            + chunk(b'IDAT', zlib.compress(b'\0\x11\x22\x33\x00\x44\x55\x66\xff')) + after + chunk(b'IEND', b''))

class SrgbPreviewTests(unittest.TestCase):
    def test_all_intents_carry_through_crop_and_validator(self):
        for intent in range(4):
            data = png(chunk(b'sRGB', bytes([intent])))
            for mode, rect in [('overview', None), ('crop', {'x': 0, 'y': 0, 'width': 1, 'height': 1})]:
                with self.subTest(intent=intent, mode=mode):
                    self.assertTrue(preview_capability(data)['available'])
                    output = preview_png(data, mode=mode, rect=rect)
                    self.assertEqual(output['sourceSha256'], hashlib.sha256(data).hexdigest())
                    checked = validate_preview_result(output, output['sourceSha256'], mode, rect, (2, 1))
                    derived = base64.b64decode(checked['pngBase64'], validate=True)
                    self.assertEqual(derived[33:46], chunk(b'sRGB', bytes([intent])))
                    with Image.open(io.BytesIO(derived)) as image:
                        self.assertEqual(image.info['srgb'], intent)
                        self.assertEqual(image.tobytes()[:4], b'\x11\x22\x33\x00')

    def test_malformed_or_conflicting_source_rejected(self):
        valid = chunk(b'sRGB', b'\x00')
        cases = [png(chunk(b'sRGB', body)) for body in (b'', b'\x04', b'\x00\x00')]
        cases += [png(valid + valid), png(after=valid),
                  png(valid + chunk(b'gAMA', struct.pack('>I', 45454))),
                  png(valid + chunk(b'iCCP', b'profile\0\0garbage'))]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(PngPreviewError):
                preview_png(data, mode='overview')

    def test_derivative_validator_rejects_bad_or_misplaced_tag(self):
        tag = chunk(b'sRGB', b'\x00')
        for data in (png(chunk(b'sRGB', b'\x04')), png(tag + tag), png(after=tag),
                     png(chunk(b'gAMA', struct.pack('>I', 45455)))):
            with self.subTest(data=data), self.assertRaises(ValueError):
                _validate_derivative_png(data, 2, 1)

if __name__ == '__main__':
    unittest.main()
