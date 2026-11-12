"""Exact cICP Display-P3, with independently xy-derived Fraction/Decimal oracle.

Fixture byte packing and exact rational primary systems reuse test-only helpers;
no production color tables, transfer functions or matrices supply expectations.
"""
import base64
import hashlib
import itertools
import struct
import sys
import time
from array import array
from decimal import Decimal as D, localcontext
from functools import lru_cache
import unittest
from unittest.mock import patch
import zlib
from nivram import png_preview as pp
from nivram.png_preview_jobs import validate_preview_result
from test_png_gamma_preview import fixture, read
from test_png_p3_preview import MATRIX, P3, SRGB, cases, transfer

CICP = bytes((12,13,0,1))
META = pp._chunk(b'cICP',CICP)

@lru_cache(maxsize=12000)
def oracle(rgb,depth=8):
    with localcontext() as ctx:
        ctx.prec=70
        values=[D(v)/((1<<depth)-1) for v in rgb]
        light=[v/D('12.92') if v<=D('.04045') else ((v+D('.055'))/D('1.055'))**D('2.4') for v in values]
        transformed=[sum(D(a.numerator)/a.denominator*v for a,v in zip(row,light)) for row in MATRIX]
        return tuple(int(transfer(v)*255+D('.5')) for v in transformed)

def cfixture(rows,color=2,depth=8,interlace=0,kind=0,**kw):
    return fixture(rows,color,depth,interlace,kind,metadata=META,**kw)

class CICPTests(unittest.TestCase):
    def test_all_150_format_filter_interlace_cases(self):
        for color,depth,interlace,kind,rows,palette in cases():
            with self.subTest(color=color,depth=depth,interlace=interlace,filter=kind):
                maximum=(1<<depth)-1
                expected=[]
                for row in rows:
                    for pixel in row:
                        rgb=palette[pixel[0]] if color==3 else (pixel[0],)*3 if color in (0,4) else pixel[:3]
                        out=oracle(rgb,8 if color==3 else depth)
                        if color in (4,6):out+=((pixel[-1]*255+maximum//2)//maximum,)
                        if color==3:out+=((0,128)[pixel[0]],)
                        expected.append(out)
                data=cfixture(rows,color,depth,interlace,kind,palette=palette,alpha=(0,128) if color==3 else None)
                digest=hashlib.sha256(data).hexdigest()
                self.assertTrue(pp.preview_capability(data)['available'])
                result=pp.preview_png(data,mode='overview')
                self.assertEqual(read(result),(expected,{'srgb':1}))
                self.assertEqual(result['sourceSha256'],digest)
                self.assertEqual(hashlib.sha256(data).hexdigest(),digest)
                self.assertEqual(result['engine']['colorConversion'],{
                    'name':'cICP Display-P3 to sRGB','version':1,'sourceCICP':[12,13,0,1],
                    'sourceColorDepth':8 if color==3 else depth,'outputIntentPolicy':1,
                    'gamutPolicy':'clip-linear-sRGB-channels-to-[0,1]',
                    'transferPolicy':'sRGB-decode; D65/P3-to-sRGB; sRGB-encode; round-once-8bit'})
                validate_preview_result(result,digest,'overview',None,(9,11))
                rect={'x':2,'y':3,'width':3,'height':2}
                crop=pp.preview_png(data,mode='crop',rect=rect)
                self.assertEqual(read(crop)[0],[expected[y*9+x] for y in (3,4) for x in (2,3,4)])
                self.assertEqual(crop['rect'],rect)
                with patch.object(pp,'OVERVIEW_EDGE',3):
                    overview=pp.preview_png(data,mode='overview')
                self.assertEqual(read(overview)[0],[expected[y*9+x] for y in (1,5,9) for x in (2,6)])

    def test_independent_piecewise_thresholds_precision_and_gamut(self):
        # All 8-bit neutral values must round-trip exactly under the new transfer.
        ramp=[(v,v,v) for v in range(256)]
        self.assertEqual(read(pp.preview_png(cfixture([ramp]),mode='overview'))[0],ramp)
        # Near 0.04045*65535: both pieces, low bytes, rounding boundaries and gamut.
        values=(0,1,127,128,129,255,256,257,2650,2651,2652,32767,32768,65534,65535)
        pixels=[(v,v,v) for v in values]+[(v,32768,50001) for v in values]
        pixels += [(65535,0,0),(0,65535,0),(0,0,65535),(40000,20000,10000)]
        expected=[oracle(p,16) for p in pixels]
        self.assertEqual(read(pp.preview_png(cfixture([pixels],depth=16),mode='overview'))[0],expected)
        self.assertNotEqual(expected,[oracle(tuple((v+128)//257 for v in p)) for p in pixels])
        self.assertEqual(oracle((64,128,192)),(32,130,197))
        self.assertEqual(oracle((255,0,0)),(255,0,0))
        self.assertEqual(oracle((0,255,0)),(0,255,0))
        from test_png_p3_preview import oracle as power_oracle
        self.assertNotEqual(oracle((10,20,30)),power_oracle((10,20,30),8,45455))
        # Every original sample checks LUT precision independently; floating
        # values compared with explicit tolerance, final bytes checked above.
        table=pp._linear_table(16,None,time.monotonic()+45)
        with localcontext() as ctx:
            ctx.prec=50
            for n,actual in enumerate(table):
                v=D(n)/65535
                expected=v/D('12.92') if v<=D('.04045') else ((v+D('.055'))/D('1.055'))**D('2.4')
                self.assertLessEqual(abs(actual-float(expected)),6e-16)

    def test_cicp_precedence_all_chunk_orders(self):
        # Intentionally conflicting lower-priority values: cICP wins, not gamma/sRGB.
        for chroma in (P3,SRGB):
            chunks=[META,pp._chunk(b'gAMA',struct.pack('>I',100000)),pp._chunk(b'cHRM',struct.pack('>8I',*chroma)),pp._chunk(b'sRGB',b'\3')]
            for order in itertools.permutations(chunks):
                data=fixture([[(64,128,192)]],2,8,metadata=b''.join(order))
                self.assertEqual(read(pp.preview_png(data,mode='overview')),([(32,130,197)],{'srgb':1}))
            for extra in chunks[1:]:
                for order in ((META,extra),(extra,META)):
                    self.assertEqual(read(pp.preview_png(fixture([[(64,128,192)]],2,8,metadata=b''.join(order)),mode='overview'))[0],[(32,130,197)])

    def test_alpha_trns_hidden_colors_sbit_palette(self):
        for interlace in (0,1):
            for color in (0,2):
                for depth in (8,16):
                    key=(11,) if color==0 else (11,21,31)
                    rows=[[key,tuple(v+1 for v in key)]]
                    pixels=read(pp.preview_png(cfixture(rows,color,depth,interlace,key=key),mode='overview'))[0]
                    self.assertEqual([p[-1] for p in pixels],[0,255])
                    self.assertEqual([p[:3] for p in pixels],[oracle(p*3 if color==0 else p,depth) for p in rows[0]])
            for depth in (8,16):
                maximum=(1<<depth)-1
                rows=[[(maximum//4,maximum//2,maximum,a) for a in (0,maximum//2,maximum)]]
                expected=[oracle(p[:3],depth)+((p[-1]*255+maximum//2)//maximum,) for p in rows[0]]
                data=fixture(rows,6,depth,interlace,metadata=META+pp._chunk(b'sBIT',bytes([5,6,7,depth])))
                self.assertEqual(read(pp.preview_png(data,mode='overview'))[0],expected)
            data=fixture([[(0,),(1,)]],3,1,interlace,palette=[(64,128,192),(128,64,32)],alpha=(0,128),metadata=META+pp._chunk(b'sBIT',bytes([5,6,7])))
            self.assertEqual(read(pp.preview_png(data,mode='overview'))[0],[oracle((64,128,192))+(0,),oracle((128,64,32))+(128,)])

    def test_invalid_unsupported_duplicate_crc_and_order(self):
        bad=[b'',b'x',b'xxx',CICP+b'x']
        for i in range(4):
            for v in (0,1,9,12,13,16,18,255):
                if v!=CICP[i]:
                    changed=bytearray(CICP);changed[i]=v
                    # Canonical sRGB is now admitted separately, not unsupported.
                    if bytes(changed)!=bytes((1,13,0,1)):bad.append(bytes(changed))
        metadata=[pp._chunk(b'cICP',v) for v in bad]+[META+META]
        corrupt=bytearray(META);corrupt[-1]^=1;metadata.append(bytes(corrupt))
        for meta in metadata:
            data=fixture([[(1,2,3)]],2,8,metadata=meta)
            self.assertFalse(pp.preview_capability(data)['available'])
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='overview')
        # Including cICP must not bypass bad structure or other extension policies.
        for tag,payload in ((b'iCCP',b'x'),(b'mDCV',b'x'),(b'cLLI',b'x'),(b'acTL',b'x'),(b'eXIf',b'x'),(b'gAMA',b'\0'*4),(b'sRGB',b'\4'),(b'cHRM',b'\0'*32)):
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(fixture([[(1,2,3)]],2,8,metadata=META+pp._chunk(tag,payload)),mode='overview')
        for color,palette in ((2,None),(3,[(1,2,3)])):
            base=fixture([[(0,)] if color==3 else [(1,2,3)]],color,8,palette=palette,metadata=b'')
            for at in (base.index(b'IDAT')-4,len(base)-12):
                # Before IDAT is legal only if no PLTE already appeared.
                if color==2 and at==base.index(b'IDAT')-4:continue
                with self.assertRaises(pp.PngPreviewError):pp.preview_png(base[:at]+META+base[at:],mode='overview')
        lower=pp._chunk(b'gAMA',struct.pack('>I',100000))
        with self.assertRaises(pp.PngPreviewError):pp.preview_png(fixture([[(1,2,3)]],2,8,metadata=META+lower+lower),mode='overview')

    def test_full_stream_faults_outside_selection(self):
        for interlace in (0,1):
            good=cfixture([[(64,128,192)],[(128,64,32)]],interlace=interlace)
            offset=good.index(b'IDAT')-4;count=int.from_bytes(good[offset:offset+4],'big')
            compressed=good[offset+8:offset+8+count];raw=zlib.decompress(compressed)
            for payload in (zlib.compress(raw[:4]+b'\5'+raw[5:]),zlib.compress(raw+b'x'),zlib.compress(raw[:-1]),compressed[:-1],compressed+b'x',compressed+zlib.compress(b'x')):
                data=good[:offset]+pp._chunk(b'IDAT',payload)+pp._chunk(b'IEND',b'')
                with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='crop',rect={'x':0,'y':0,'width':1,'height':1})
            bad=cfixture([[(0,)],[(1,)]],3,1,interlace,palette=[(64,128,192)])
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(bad,mode='crop',rect={'x':0,'y':0,'width':1,'height':1})

    def test_budget_deadlines_and_parser_only_capability(self):
        data=cfixture([[(128,32768,50001)]],depth=16)
        result=pp.preview_png(data,mode='overview');a=result['bufferAccounting']
        self.assertEqual(a['colorTableBytes'],sys.getsizeof(array('d',[0.0])*65536))
        self.assertEqual(a['colorTableConstructionTemporaryBytes'],sys.getsizeof(array('d',[0.0])))
        with patch.object(pp,'PIXEL_PIPELINE_BUDGET',a['pixelPipelineAccountedUpperBoundBytes']-1),patch.object(pp,'_linear_table',side_effect=AssertionError('must reject before allocation')):
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='overview')
        with patch.object(pp,'_linear_table',side_effect=AssertionError('parser only')):
            self.assertTrue(pp.preview_capability(data)['available'])
        with self.assertRaises(TimeoutError):pp._linear_table(16,None,time.monotonic()-1)
        with patch.object(pp,'_deadline',side_effect=[None,None,TimeoutError('expired')]):
            with self.assertRaises(TimeoutError):pp._linear_table(16,None,time.monotonic()+45)
        encoding=pp._Encoding(8,2,None,None,cicp=CICP)
        table=pp._linear_table(8,None,time.monotonic()+45)
        with patch.object(pp,'_deadline',side_effect=[None,TimeoutError('expired')]):
            with self.assertRaises(TimeoutError):pp._expand_color(b'\x40\x80\xc0'*257,None,257,encoding,table,time.monotonic()+45)
        with patch.object(pp,'MAX_PREVIEW_BYTES',40):
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='overview')

    def test_piecewise_table_only_for_exact_cicp(self):
        from test_png_p3_preview import metadata
        for meta, expected in ((META,None),(metadata(),100000)):
            with patch.object(pp,'_linear_table',wraps=pp._linear_table) as table:
                pp.preview_png(fixture([[(64,128,192)]],2,8,metadata=meta),mode='overview')
                self.assertEqual(table.call_count,1)
                self.assertIs(table.call_args.args[1],None) if expected is None else self.assertEqual(table.call_args.args[1],expected)
        for meta in (b'',pp._chunk(b'sRGB',b'\0'),pp._chunk(b'cICP',bytes((12,13,0,0)))):
            with patch.object(pp,'_linear_table',side_effect=AssertionError('no piecewise table')):
                if b'cICP' in meta:
                    with self.assertRaises(pp.PngPreviewError):pp.preview_png(fixture([[(1,2,3)]],2,8,metadata=meta),mode='overview')
                else:pp.preview_png(fixture([[(1,2,3)]],2,8,metadata=meta),mode='overview')

    def test_selected_rows_only_and_strict_derivative(self):
        data=cfixture([[(64,128,192)]*9 for _ in range(11)],kind=4)
        with patch.object(pp,'_expand_color',wraps=pp._expand_color) as expanded:
            result=pp.preview_png(data,mode='crop',rect={'x':2,'y':5,'width':2,'height':1})
        self.assertEqual(expanded.call_count,1)
        png=base64.b64decode(result['pngBase64']);kinds=[];at=8
        while at<len(png):
            size=int.from_bytes(png[at:at+4],'big');kinds.append(png[at+4:at+8]);at+=size+12
        self.assertEqual(set(kinds),{b'IHDR',b'sRGB',b'IDAT',b'IEND'})
        self.assertNotIn(b'cICP',png)
        validate_preview_result(result,result['sourceSha256'],'crop',result['rect'],(9,11))

if __name__=='__main__':unittest.main()
