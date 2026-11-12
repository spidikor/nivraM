"""Bounded explicit-power D65/P3 acceptance, not broad Display-P3 support.

Pixel oracle solves exact primary/white systems with Fraction, then uses Decimal70.
Only fixture packing/filtering and PNG result reading are shared with old tests.
"""
import hashlib
import struct
import sys
import time
from array import array
import unittest
import zlib
from decimal import Decimal as D, localcontext
from fractions import Fraction as F
from functools import lru_cache
from unittest.mock import patch
from nivram import png_preview as pp
from nivram.png_preview_jobs import validate_preview_result
from test_png_gamma_preview import fixture, read, CHANNELS

P3 = (31270,32900,68000,32000,26500,69000,15000,6000)
SRGB = (31270,32900,64000,33000,30000,60000,15000,6000)
FORMATS = {0:(1,2,4,8,16),2:(8,16),3:(1,2,4,8),4:(8,16),6:(8,16)}

def solve(a,b):
    rows=[list(row)+[v] for row,v in zip(a,b)]
    for i in range(3):
        pivot=next(j for j in range(i,3) if rows[j][i])
        rows[i],rows[pivot]=rows[pivot],rows[i]
        factor=rows[i][i]; rows[i]=[v/factor for v in rows[i]]
        for j in range(3):
            if j!=i:
                factor=rows[j][i]; rows[j]=[v-factor*w for v,w in zip(rows[j],rows[i])]
    return tuple(row[-1] for row in rows)

def primary_matrix(chroma):
    wx,wy,*p=(F(v,100000) for v in chroma)
    columns=[(p[i]/p[i+1],F(1),(1-p[i]-p[i+1])/p[i+1]) for i in (0,2,4)]
    basis=tuple(zip(*columns))
    weights=solve(basis,(wx/wy,F(1),(1-wx-wy)/wy))
    return tuple(tuple(v*s for v,s in zip(row,weights)) for row in basis)

SRC=primary_matrix(P3)
DST=primary_matrix(SRGB)
MATRIX=tuple(zip(*(solve(DST,col) for col in zip(*SRC))))

def transfer(v):
    v=max(D(0),min(D(1),v))
    return D('12.92')*v if v<=D('.0031308') else D('1.055')*v**(D(5)/12)-D('.055')

@lru_cache(maxsize=12000)
def oracle(rgb,depth=8,gamma=100000):
    with localcontext() as ctx:
        ctx.prec=70
        light=[(D(v)/((1<<depth)-1))**(D(100000)/gamma) for v in rgb]
        transformed=[sum(D(a.numerator)/a.denominator*v for a,v in zip(row,light)) for row in MATRIX]
        return tuple(int(transfer(v)*255+D('.5')) for v in transformed)

def metadata(gamma=100000,chroma=P3):
    return pp._chunk(b'gAMA',struct.pack('>I',gamma))+pp._chunk(b'cHRM',struct.pack('>8I',*chroma))

def pfixture(rows,color,depth,interlace=0,kind=0,gamma=100000,**kw):
    return fixture(rows,color,depth,interlace,kind,metadata=metadata(gamma),**kw)

def cases():
    for color,depths in FORMATS.items():
        for depth in depths:
            maximum=(1<<depth)-1
            palette=[(64,128,192),(128,64,32)] if color==3 else None
            rows=[[tuple((x*173+y*791+c*977)%(2 if color==3 else maximum+1) for c in range(CHANNELS[color])) for x in range(9)] for y in range(11)]
            for interlace in (0,1):
                for kind in range(5):
                    yield color,depth,interlace,kind,rows,palette

class P3Tests(unittest.TestCase):
    def test_independent_matrix_and_literal_controls(self):
        self.assertEqual(SRC,((F(608311,1250200),F(189793,714400),F(198249,1000160)),(F(35783,156275),F(247089,357200),F(198249,2500400)),(F(0),F(32229,714400),F(5220557,5000800))))
        self.assertTrue(all(sum(row)==1 for row in MATRIX))
        self.assertEqual(oracle((64,128,192)),(122,190,229))
        for rgb in ((0,0,0),(255,255,255),(128,128,128),(64,128,192),(128,64,32),(255,0,0),(0,255,0),(0,0,255)):
            result=pp.preview_png(pfixture([[rgb]],2,8),mode='overview')
            self.assertEqual(read(result),( [oracle(rgb)], {'srgb':1}))

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
                data=pfixture(rows,color,depth,interlace,kind,palette=palette,alpha=(0,128) if color==3 else None)
                digest=hashlib.sha256(data).hexdigest()
                self.assertTrue(pp.preview_capability(data)['available'])
                result=pp.preview_png(data,mode='overview')
                self.assertEqual(read(result),(expected,{'srgb':1}))
                self.assertEqual(result['sourceSha256'],digest)
                self.assertEqual(hashlib.sha256(data).hexdigest(),digest)
                conversion=result['engine']['colorConversion']
                self.assertEqual(conversion['name'],'explicit-gamma D65/P3-primary to sRGB')
                self.assertEqual(conversion['version'],1)
                self.assertEqual(conversion['sourceGammaInteger'],100000)
                self.assertEqual(conversion['outputIntentPolicy'],1)
                self.assertEqual(conversion['transferPolicy'],'declared-power-to-linear; sRGB-encode; round-once-8bit')
                self.assertEqual(conversion['sourceColorDepth'],8 if color==3 else depth)
                self.assertEqual(tuple(conversion['sourceChromaticities']),P3)
                self.assertEqual(conversion['gamutPolicy'],'clip-linear-sRGB-channels-to-[0,1]')
                validate_preview_result(result,digest,'overview',None,(9,11))
                rect={'x':2,'y':3,'width':3,'height':2}
                crop=pp.preview_png(data,mode='crop',rect=rect)
                self.assertEqual(read(crop)[0],[expected[y*9+x] for y in (3,4) for x in (2,3,4)])
                self.assertEqual(crop['rect'],rect)
                with patch.object(pp,'OVERVIEW_EDGE',3):
                    overview=pp.preview_png(data,mode='overview')
                # 9x11 -> 2x3, nearest center coordinates (2,6)x(1,5,9).
                self.assertEqual(read(overview)[0],[expected[y*9+x] for y in (1,5,9) for x in (2,6)])

    def test_gamma_extremes_low_bytes_transfer_and_rounding(self):
        values={0,1,127,128,129,204,205,206,255,256,257,32767,32768,65534,65535}
        # Adjacent original-depth neutrals around 8-bit rounding thresholds.
        with localcontext() as ctx:
            ctx.prec=70
            for k in (0,1,9,10,11,63,127,191,254):
                encoded=(D(k)+D('.5'))/255
                linear=encoded/D('12.92') if encoded<=D('.04045') else ((encoded+D('.055'))/D('1.055'))**D('2.4')
                n=int(linear*65535); values.update((n,max(0,n-1),min(65535,n+1)))
        rows=[[(v,v,v) for v in sorted(values)]+[(128,32768,50001),(129,32769,50002),(65535,32768,0),(0,32768,65535)]]
        for gamma in (1,45455,100000,220000,2147483647):
            with self.subTest(gamma=gamma):
                result=pp.preview_png(pfixture(rows,2,16,gamma=gamma),mode='overview')
                self.assertEqual(read(result)[0],[oracle(p,16,gamma) for p in rows[0]])
        self.assertNotEqual(oracle((128,128,128),16),oracle((0,0,0),8))
        precise=[oracle(p,16) for p in rows[0]]
        prematurely_reduced=[oracle(tuple((v*255+32767)//65535 for v in p),8) for p in rows[0]]
        self.assertNotEqual(precise,prematurely_reduced)
        for gamma in (1,45455,100000,220000,2147483647):
            ramp=[(v,v,v) for v in range(256)]
            self.assertEqual(read(pp.preview_png(pfixture([ramp],2,8,gamma=gamma),mode='overview'))[0],[oracle(p,8,gamma) for p in ramp])
        self.assertNotEqual(oracle((64,128,192),8,45455),(64,128,192))

    def test_alpha_trns_hidden_rgb_and_sbit(self):
        for interlace in (0,1):
            for color in (0,2):
                key=(257,) if color==0 else (257,514,771)
                rows=[[key,tuple(v+1 for v in key)]]
                data=pfixture(rows,color,16,interlace,key=key)
                pixels=read(pp.preview_png(data,mode='overview'))[0]
                self.assertEqual([p[-1] for p in pixels],[0,255])
                self.assertEqual([p[:3] for p in pixels],[oracle(p*3 if color==0 else p,16) for p in rows[0]])
            for depth in (8,16):
                maximum=(1<<depth)-1
                rows=[[(maximum//4,maximum//2,maximum,alpha) for alpha in (0,maximum//2,maximum)]]
                data=pfixture(rows,6,depth,interlace)
                expected=[oracle(p[:3],depth)+((p[-1]*255+maximum//2)//maximum,) for p in rows[0]]
                self.assertEqual(read(pp.preview_png(data,mode='overview'))[0],expected)
                marked=fixture(rows,6,depth,interlace,metadata=metadata()+pp._chunk(b'sBIT',bytes([5,6,7,4])))
                self.assertEqual(read(pp.preview_png(marked,mode='overview'))[0],expected)
            # Palette sBIT describes 8-bit PLTE values, not 1-bit indices.
            indexed=fixture([[(0,),(1,)]],3,1,interlace,palette=[(64,128,192),(128,64,32)],alpha=(0,128),metadata=metadata()+pp._chunk(b'sBIT',bytes([5,6,7])))
            self.assertEqual(read(pp.preview_png(indexed,mode='overview'))[0],[oracle((64,128,192))+(0,),oracle((128,64,32))+(128,)])

    def test_metadata_rejections(self):
        g=pp._chunk(b'gAMA',struct.pack('>I',100000)); c=pp._chunk(b'cHRM',struct.pack('>8I',*P3))
        bad=[g,c,g+g+c,g+c+c]
        for i in range(8):
            altered=list(P3);altered[i]+=1
            bad.append(g+pp._chunk(b'cHRM',struct.pack('>8I',*altered)))
        bad += [pp._chunk(b'gAMA',v)+c for v in (b'',b'123',b'12345',struct.pack('>I',0),struct.pack('>I',0x80000000),struct.pack('>I',0xffffffff))]
        bad += [g+pp._chunk(b'cHRM',v) for v in (b'',b'x'*31,b'x'*33)]
        for tag in (b'iCCP',b'cICP',b'mDCV',b'cLLI',b'acTL',b'eXIf'):
            bad.append(g+c+pp._chunk(tag,b'x'))
        for order in ((b's',b'g',b'c'),(b's',b'c',b'g'),(b'g',b's',b'c'),(b'g',b'c',b's'),(b'c',b's',b'g'),(b'c',b'g',b's')):
            chunks={b'g':pp._chunk(b'gAMA',struct.pack('>I',45455)),b'c':c,b's':pp._chunk(b'sRGB',b'\0')}
            bad.append(b''.join(chunks[k] for k in order))
        for blob in (g,c):
            broken=bytearray(blob);broken[-1]^=1
            bad.append(bytes(broken)+(c if blob==g else g))
        for n,meta in enumerate(bad):
            with self.subTest(case=n):
                data=fixture([[(1,2,3)]],2,8,metadata=meta)
                self.assertFalse(pp.preview_capability(data)['available'])
                with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='overview')
        # Either order is legal if both precede PLTE/IDAT.
        self.assertTrue(pp.preview_capability(fixture([[(1,2,3)]],2,8,metadata=c+g))['available'])
        for late_tag in (g,c):
            early=c if late_tag==g else g
            base=fixture([[(0,)]],3,1,palette=[(64,128,192)],metadata=early)
            at=base.index(b'IDAT')-4
            for pos in (at,len(base)-12):
                bad_data=base[:pos]+late_tag+base[pos:]
                with self.assertRaises(pp.PngPreviewError):pp.preview_png(bad_data,mode='overview')

    def test_corruption_outside_crop_and_stream_faults(self):
        good=pfixture([[(64,128,192)],[(128,64,32)]],2,8)
        offset=good.index(b'IDAT')-4; count=int.from_bytes(good[offset:offset+4],'big')
        compressed=good[offset+8:offset+8+count]; raw=zlib.decompress(compressed)
        rect={'x':0,'y':0,'width':1,'height':1}
        payloads=(zlib.compress(raw[:4]+b'\5'+raw[5:]),zlib.compress(raw+b'x'),zlib.compress(raw[:-1]),compressed[:-1],compressed+b'x',compressed+zlib.compress(b'x'))
        for payload in payloads:
            data=good[:offset]+pp._chunk(b'IDAT',payload)+pp._chunk(b'IEND',b'')
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='crop',rect=rect)
        broken=bytearray(good);broken[offset+8]^=1
        with self.assertRaises(pp.PngPreviewError):pp.preview_png(bytes(broken),mode='crop',rect=rect)
        for interlace in (0,1):
            bad=pfixture([[(0,)],[(1,)]],3,1,interlace,palette=[(64,128,192)])
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(bad,mode='crop',rect=rect)

    def test_only_selected_rows_receive_matrix_work(self):
        rows=[[(64,128,192)]*9 for _ in range(11)]
        original=pp._expand_color
        original_open=pp.Image.open
        heights=[]
        def opened(source):
            im=original_open(source);heights.append(im.height);return im
        data=pfixture(rows,2,8,kind=4)
        with patch.object(pp,'_expand_color',wraps=original) as expanded, patch.object(pp.Image,'open',side_effect=opened):
            pp.preview_png(data,mode='crop',rect={'x':2,'y':5,'width':2,'height':1})
        self.assertEqual(expanded.call_count,1)
        self.assertEqual(heights,[2]*11)
        # Capability must remain parser-only, with no numerical allocation.
        with patch.object(pp,'_linear_table',side_effect=AssertionError('no table for capability')):
            self.assertTrue(pp.preview_capability(data)['available'])

    def test_measured_table_budget_and_cooperative_expiry(self):
        for depth in (8,16):
            data=pfixture([[(128,128,128)]],2,depth)
            result=pp.preview_png(data,mode='overview')
            accounting=result['bufferAccounting']
            measured=sys.getsizeof(array('d',[0.0])*(1<<depth))
            self.assertEqual(accounting['colorTableBytes'],measured)
            self.assertEqual(accounting['colorTableConstructionTemporaryBytes'],sys.getsizeof(array('d',[0.0])))
            budget=accounting['pixelPipelineAccountedUpperBoundBytes']
            with patch.object(pp,'PIXEL_PIPELINE_BUDGET',budget-1):
                with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='overview')
            with patch.object(pp,'PIXEL_PIPELINE_BUDGET',budget):
                self.assertEqual(read(pp.preview_png(data,mode='overview')),read(result))
        with self.assertRaises(TimeoutError):pp._linear_table(16,100000,time.monotonic()-1)
        with patch.object(pp,'_deadline',side_effect=[None,None,TimeoutError('expired')]):
            with self.assertRaises(TimeoutError):pp._linear_table(16,100000,time.monotonic()+45)
        encoding=pp._Encoding(8,2,None,None,gamma=100000,chromaticities=P3)
        table=pp._linear_table(8,100000,time.monotonic()+45)
        # Expire after the first 256 pixels, not merely on function entry.
        with patch.object(pp,'_deadline',side_effect=[None,TimeoutError('expired')]):
            with self.assertRaises(TimeoutError):pp._expand_color(b'\x40\x80\xc0'*257,None,257,encoding,table,time.monotonic()+45)
        with patch.object(pp,'_deadline',side_effect=[None,TimeoutError('expired')]):
            with self.assertRaises(TimeoutError):pp._encode(bytearray(6),1,2,2,time.monotonic()+45,1)

    def test_unused_packed_padding_is_not_a_pixel(self):
        for color in (0,3):
            data=pfixture([[(0,)]*9],color,1,palette=[(64,128,192)] if color==3 else None)
            offset=data.index(b'IDAT')-4
            count=int.from_bytes(data[offset:offset+4],'big')
            raw=bytearray(zlib.decompress(data[offset+8:offset+8+count]))
            raw[-1]|=127  # Seven unused bits may be nonzero, including out-of-palette values.
            altered=data[:offset]+pp._chunk(b'IDAT',zlib.compress(raw))+pp._chunk(b'IEND',b'')
            self.assertEqual(read(pp.preview_png(altered,mode='overview')),read(pp.preview_png(data,mode='overview')))

if __name__=='__main__':unittest.main()
