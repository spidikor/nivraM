"""Handcrafted Adam7 16-bit fixtures; no decoder supplies expected pixels.

Pass ownership uses an independently enumerated 8x8 map; the encoder operates on
big-endian bytes. All tests are local and synthetic, without process/native I/O.
"""
import hashlib
import struct
import unittest
from unittest.mock import patch
import zlib
from test_png_adam7_preview import PASS_LABELS, chunk, png, filtered, decoded
from nivram.png_preview import preview_png, preview_capability, PngPreviewError

CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}

def rows_for(w, h, color):
    return [[tuple((x*5279+y*8713+x*y*193+c*17041+127) % 65536
                    for c in range(CHANNELS[color])) for x in range(w)] for y in range(h)]

def raw_for(rows, color, filters=(0, 1, 2, 3, 4)):
    out, facts = bytearray(), []
    for number in range(1, 8):
        prior = None
        n = 0
        for y, row in enumerate(rows):
            picked = [pixel for x, pixel in enumerate(row) if PASS_LABELS[y%8][x%8] == number]
            if not picked:
                continue
            packed = b''.join(struct.pack('>H', s) for p in picked for s in p)
            if prior is None:
                prior = bytes(len(packed))
            facts.append((number, y, len(out), len(packed)))
            out.extend(filtered(packed, prior, filters[n % len(filters)], CHANNELS[color]*2))
            prior = packed
            n += 1
    return bytes(out), facts

def expected(rows, color, key=None):
    output = []
    for row in rows:
        line = []
        for p in row:
            q = tuple((v*255+32767)//65535 for v in p)
            rgb = q if color in (2, 6) else (q[0],)*3 + (q[1:] if color == 4 else ())
            if key is not None:
                rgb += (0 if p == key else 255,)
            line.append(rgb)
        output.append(line)
    return output

def source(rows, color, filters=(0,1,2,3,4), key=None, **kwargs):
    raw, facts = raw_for(rows, color, filters)
    before = chunk(b'tRNS', b''.join(struct.pack('>H', s) for s in key)) if key is not None else b''
    return png(len(rows[0]), len(rows), raw, color=color, depth=16, before=before, **kwargs), raw, facts

class Adam7SixteenTests(unittest.TestCase):
    def assert_pixels(self, data, rows, mode='overview', rect=None):
        with patch('nivram.png_preview.Image.open', side_effect=AssertionError('Production Pillow decode forbidden')):
            result = preview_png(data, mode=mode, rect=rect, expected_source_sha256=hashlib.sha256(data).hexdigest())
        size, color, pixels, metadata, payload = decoded(result)
        self.assertEqual(size, (len(rows[0]), len(rows)))
        self.assertEqual(pixels, bytes(v for row in rows for p in row for v in p))
        self.assertEqual(color, 'RGBA' if len(rows[0][0]) == 4 else 'RGB')
        self.assertEqual(metadata, {})
        self.assertEqual(result['sourceSha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(result['bufferAccounting']['maxCarrierHeight'], 0)
        self.assertIn('16', result['engine']['rowDecoder'])
        return result

    def test_all_legal_types_filters_and_low_bytes(self):
        for color in CHANNELS:
            rows = rows_for(17, 19, color)
            for kind in range(5):
                with self.subTest(color=color, filter=kind):
                    data, _, _ = source(rows,color,(kind,))
                    self.assertTrue(preview_capability(data)['available'])
                    self.assert_pixels(data, expected(rows,color))

    def test_small_odd_dimensions_and_pass_resets(self):
        for w,h in ((1,1),(1,9),(9,1),(2,3),(3,2),(5,7),(9,11)):
            for color in CHANNELS:
                with self.subTest(size=(w,h),color=color):
                    rows=rows_for(w,h,color);data,_,_=source(rows,color,(2,4,1,3,0))
                    self.assert_pixels(data,expected(rows,color))

    def test_full_precision_transparency_and_rounding_near_collisions(self):
        for color,key in ((0,(257,)),(2,(257,514,771))):
            rows = [[key,tuple(x+1 for x in key),tuple(65535 for _ in key),tuple(0 for _ in key)]]
            data,_,_=source(rows,color,key=key)
            self.assert_pixels(data,expected(rows,color,key))
            self.assertEqual(expected(rows,color,key)[0][0][:-1],expected(rows,color,key)[0][1][:-1])
            self.assertNotEqual(expected(rows,color,key)[0][0][-1],expected(rows,color,key)[0][1][-1])
        rows=[[(x,) for x in (0,128,129,256,257,65406,65407,65535)]]
        data,_,_=source(rows,0);self.assert_pixels(data,expected(rows,0))

    def test_hidden_color_and_partial_alpha_survive(self):
        for color in (4,6):
            rows=rows_for(9,11,color)
            rows[3][4]=((12345,0) if color==4 else (12345,54321,129,0))
            data,_,_=source(rows,color)
            self.assert_pixels(data,expected(rows,color))

    def test_exact_crop_and_nearest_center_overview(self):
        rect={'x':3,'y':4,'width':5,'height':3}
        for color in CHANNELS:
            rows=rows_for(17,19,color);data,_,_=source(rows,color); exp=expected(rows,color)
            result=self.assert_pixels(data,[r[3:8] for r in exp[4:7]],'crop',rect)
            self.assertEqual(result['rect'],rect);self.assertEqual(result['resampling'],'none')
            with patch('nivram.png_preview.OVERVIEW_EDGE',5):
                result=preview_png(data,mode='overview')
                ow,oh=result['width'],result['height']
                selected=[[exp[(2*y+1)*19//(2*oh)][(2*x+1)*17//(2*ow)] for x in range(ow)] for y in range(oh)]
                self.assert_pixels(data,selected)

    def test_bad_filter_outside_crop_rejects(self):
        rows=rows_for(13,9,6);_,raw,facts=source(rows,6)
        bad=bytearray(raw);bad[facts[-1][2]]=5
        with self.assertRaises(PngPreviewError):
            preview_png(png(13,9,bytes(bad),color=6,depth=16),mode='crop',rect={'x':0,'y':0,'width':1,'height':1})

    def test_stream_truncation_excess_concat_and_crc(self):
        rows=rows_for(9,11,2);data,raw,_=source(rows,2)
        for stream in (zlib.compress(raw)[:-1],zlib.compress(raw[:-1]),zlib.compress(raw+b'x'),zlib.compress(raw)+zlib.compress(b'')):
            with self.subTest(stream_length=len(stream)),self.assertRaises(PngPreviewError):
                preview_png(png(9,11,b'',color=2,depth=16,stream=stream),mode='overview')
        bad=bytearray(data);bad[29]^=1
        with self.assertRaises(PngPreviewError):preview_png(bytes(bad),mode='overview')

    def test_decompression_is_positive_scanline_bounded(self):
        rows=rows_for(17,19,6);data,_,facts=source(rows,6); real=zlib.decompressobj;calls=[]
        class Recorder:
            def __init__(self):self.inner=real()
            def decompress(self,data,max_length=0):
                calls.append(max_length)
                if not 0<max_length<=max(f[3]+1 for f in facts):raise AssertionError('Unbounded inflate')
                return self.inner.decompress(data,max_length)
            def __getattr__(self,name):return getattr(self.inner,name)
        with patch('nivram.png_preview.zlib.decompressobj',side_effect=Recorder):self.assert_pixels(data,expected(rows,6))
        self.assertTrue(calls)

    def test_limits_and_expiry_unchanged(self):
        rows=rows_for(13,9,6);data,raw,_=source(rows,6)
        for name,value in (('MAX_SOURCE_BYTES',len(data)-1),('MAX_CHUNKS',2),('MAX_FILTERED_BYTES',len(raw)-1),('PIXEL_PIPELINE_BUDGET',1),('MAX_PREVIEW_BYTES',40),('MAX_PIXELS',116),('MAX_EDGE',12)):
            with self.subTest(limit=name),patch('nivram.png_preview.'+name,value),self.assertRaises(PngPreviewError):preview_png(data,mode='overview')
        with patch('nivram.png_preview.time.monotonic',side_effect=[0,100]),self.assertRaises(TimeoutError):preview_png(data,mode='overview',deadline_seconds=1)

    def test_strict_exclusions_remain(self):
        for color in CHANNELS:
            raw=b'\0'+bytes(CHANNELS[color]*2)
            for kind in (b'gAMA',b'cHRM',b'iCCP',b'sRGB',b'sBIT',b'eXIf',b'acTL'):
                data=png(1,1,raw,color=color,depth=16,before=chunk(kind,b''))
                self.assertFalse(preview_capability(data)['available'])
                with self.assertRaises(PngPreviewError):preview_png(data,mode='overview')
        with self.assertRaises(PngPreviewError):preview_png(png(1,1,b'\0\0\0',color=3,depth=16,before=chunk(b'PLTE',b'abc')),mode='overview')
