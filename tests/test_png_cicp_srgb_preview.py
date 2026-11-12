"""Canonical cICP sRGB identity: independent integer sample expectations."""
import base64
import hashlib
import itertools
import struct
import time
import unittest
from unittest.mock import patch
import zlib
from nivram import png_preview as pp
from nivram.png_preview_jobs import validate_preview_result
from test_png_gamma_preview import fixture, read
from test_png_p3_preview import cases, P3, SRGB

CICP = bytes((1,13,0,1))
META = pp._chunk(b'cICP', CICP)

def scaled(v, depth):
    maximum = (1 << depth)-1
    return (v*255 + maximum//2)//maximum

def make(rows, color=2, depth=8, interlace=0, **kw):
    return fixture(rows,color,depth,interlace,metadata=META,**kw)

class CICPsRGBTests(unittest.TestCase):
    def test_all_core_formats_filters_geometry_and_provenance(self):
        for color,depth,interlace,kind,rows,palette in cases():
            with self.subTest(color=color,depth=depth,interlace=interlace,filter=kind):
                expected=[]
                for row in rows:
                    for p in row:
                        rgb=palette[p[0]] if color==3 else (p[0],)*3 if color in (0,4) else p[:3]
                        q=tuple(scaled(v,8 if color==3 else depth) for v in rgb)
                        if color in (4,6):q+=(scaled(p[-1],depth),)
                        if color==3:q+=((0,128)[p[0]],)
                        expected.append(q)
                data=fixture(rows,color,depth,interlace,kind,metadata=META,palette=palette,alpha=(0,128) if color==3 else None)
                digest=hashlib.sha256(data).hexdigest()
                self.assertTrue(pp.preview_capability(data)['available'])
                result=pp.preview_png(data,mode='overview')
                self.assertEqual(read(result),(expected,{'srgb':1}))
                self.assertEqual(result['sourceSha256'],digest)
                self.assertEqual(hashlib.sha256(data).hexdigest(),digest)
                self.assertNotIn('colorConversion',result['engine'])
                self.assertEqual(result['engine']['colorInterpretation'],{
                    'name':'cICP sRGB','version':1,'sourceCICP':[1,13,0,1],
                    'sourceColorDepth':8 if color==3 else depth,'outputIntentPolicy':1,
                    'samplePolicy':'stored-samples; scale-to-8bit; no-transfer-or-gamut-conversion'})
                self.assertNotIn('colorTableBytes',result['bufferAccounting'])
                validate_preview_result(result,digest,'overview',None,(9,11))
                rect={'x':2,'y':3,'width':3,'height':2}
                crop=pp.preview_png(data,mode='crop',rect=rect)
                self.assertEqual(read(crop)[0],[expected[y*9+x] for y in (3,4) for x in (2,3,4)])
                with patch.object(pp,'OVERVIEW_EDGE',3):small=pp.preview_png(data,mode='overview')
                self.assertEqual(read(small)[0],[expected[y*9+x] for y in (1,5,9) for x in (2,6)])

    def test_precedence_all_orders_and_no_conversion_allocation(self):
        for chroma in (P3,SRGB):
            chunks=[META,pp._chunk(b'gAMA',struct.pack('>I',100000)),pp._chunk(b'cHRM',struct.pack('>8I',*chroma)),pp._chunk(b'sRGB',b'\3')]
            variants=list(itertools.permutations(chunks))
            variants += [(META,extra) for extra in chunks[1:]]+[(extra,META) for extra in chunks[1:]]
            for order in variants:
                data=fixture([[(64,128,192)]],2,8,metadata=b''.join(order))
                with patch.object(pp,'_linear_table',side_effect=AssertionError('identity')),patch.object(pp,'_gamma_table',side_effect=AssertionError('identity')):
                    result=pp.preview_png(data,mode='overview')
                self.assertEqual(read(result),([(64,128,192)],{'srgb':1}))
                self.assertFalse(pp._source(data,time.monotonic()+10)[2].p3)
                png=base64.b64decode(result['pngBase64']);kinds=[];at=8
                while at<len(png):
                    n=int.from_bytes(png[at:at+4],'big');kinds.append(png[at+4:at+8]);at+=n+12
                self.assertEqual(kinds[:2],[b'IHDR',b'sRGB'])
                self.assertEqual(kinds[-1],b'IEND')
                self.assertTrue(kinds[2:-1] and all(k==b'IDAT' for k in kinds[2:-1]))

    def test_every_16bit_gray_value_and_8bit_rgb_values(self):
        # Exhaustive scalar scaling without relying on production helpers.
        # Legal bounded source geometry; one exact crop retains all values.
        rows=[[(y*256+x,) for x in range(256)] for y in range(256)]
        for interlace in (0,1):
            data=make(rows,0,16,interlace)
            result=pp.preview_png(data,mode='crop',rect={'x':0,'y':0,'width':256,'height':256})
            self.assertEqual(read(result)[0],[(scaled(v,16),)*3 for v in range(65536)])
            ramp=[(v,255-v,(v*17)%256) for v in range(256)]
            self.assertEqual(read(pp.preview_png(make([ramp],interlace=interlace),mode='overview'))[0],ramp)

    def test_alpha_trns_hidden_rgb_sbit(self):
        for interlace in (0,1):
            for color in (0,2):
                for depth in (8,16):
                    key=(128,) if color==0 else (128,257 if depth==16 else 129,50001 if depth==16 else 200)
                    rows=[[key,tuple(v+1 for v in key)]]
                    expected=[tuple(scaled(v,depth) for v in (p*3 if color==0 else p))+(a,) for p,a in zip(rows[0],(0,255))]
                    self.assertEqual(read(pp.preview_png(make(rows,color,depth,interlace,key=key),mode='overview'))[0],expected)
            for depth in (8,16):
                maximum=(1<<depth)-1
                rows=[[(maximum//4,maximum//2,maximum,a) for a in (0,maximum//2,maximum)]]
                data=fixture(rows,6,depth,interlace,metadata=META+pp._chunk(b'sBIT',bytes([5,6,7,depth])))
                self.assertEqual(read(pp.preview_png(data,mode='overview'))[0],[tuple(scaled(v,depth) for v in p) for p in rows[0]])

    def test_fail_closed_values_layout_and_lower_structure(self):
        invalid=[b'',b'x',b'xxx',CICP+b'x']
        for i in range(4):
            for value in range(256):
                changed=bytearray(CICP);changed[i]=value
                if bytes(changed) not in (CICP,bytes((12,13,0,1))):invalid.append(bytes(changed))
        metas=[pp._chunk(b'cICP',v) for v in invalid]+[META+META,META+pp._chunk(b'cICP',bytes((12,13,0,1)))]
        bad=bytearray(META);bad[-1]^=1;metas.append(bytes(bad))
        for tag,payload in ((b'iCCP',b'x'),(b'mDCV',b'x'),(b'cLLI',b'x'),(b'acTL',b'x'),(b'eXIf',b'x'),(b'gAMA',b'\0'*4),(b'sRGB',b'\4'),(b'cHRM',b'\0'*32)):
            metas.append(META+pp._chunk(tag,payload))
        lower=pp._chunk(b'gAMA',struct.pack('>I',100000));metas.append(META+lower+lower)
        for meta in metas:
            data=fixture([[(1,2,3)]],2,8,metadata=meta)
            self.assertFalse(pp.preview_capability(data)['available'])
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='overview')
        for color,palette in ((2,None),(3,[(1,2,3)])):
            base=fixture([[(0,)] if color==3 else [(1,2,3)]],color,8,palette=palette,metadata=b'')
            for at in (base.index(b'IDAT')-4,len(base)-12):
                if color==2 and at==base.index(b'IDAT')-4:continue
                with self.assertRaises(pp.PngPreviewError):pp.preview_png(base[:at]+META+base[at:],mode='overview')

    def test_stream_faults_outside_selection_and_limits(self):
        for interlace in (0,1):
            good=make([[(64,128,192)],[(128,64,32)]],interlace=interlace)
            at=good.index(b'IDAT')-4;n=int.from_bytes(good[at:at+4],'big');compressed=good[at+8:at+8+n];raw=zlib.decompress(compressed)
            for payload in (zlib.compress(raw[:4]+b'\5'+raw[5:]),zlib.compress(raw+b'x'),zlib.compress(raw[:-1]),compressed[:-1],compressed+b'x',compressed+zlib.compress(b'x')):
                data=good[:at]+pp._chunk(b'IDAT',payload)+pp._chunk(b'IEND',b'')
                with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='crop',rect={'x':0,'y':0,'width':1,'height':1})
            bad=make([[(0,)],[(1,)]],3,1,interlace,palette=[(64,128,192)])
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(bad,mode='crop',rect={'x':0,'y':0,'width':1,'height':1})
        good=make([[(64,128,192)]])
        a=pp.preview_png(good,mode='overview')['bufferAccounting']
        with patch.object(pp,'PIXEL_PIPELINE_BUDGET',a['pixelPipelineAccountedUpperBoundBytes']-1):
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(good,mode='overview')
        with patch.object(pp,'_deadline',side_effect=TimeoutError('expired')):
            with self.assertRaises(TimeoutError):pp.preview_png(good,mode='overview')
        with patch.object(pp,'MAX_PREVIEW_BYTES',40):
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(good,mode='overview')

    def test_real_supervised_worker_normal_path(self):
        import tempfile
        from pathlib import Path
        from nivram.png_preview_jobs import ImagePreviewJobs
        for interlace in (0,1):
            data=make([[(128,32768,50001,0),(129,257,65535,32768)]],6,16,interlace)
            with tempfile.TemporaryDirectory() as temp:
                jobs=ImagePreviewJobs(Path(temp)/'cache')
                try:
                    for mode,rect in (('overview',None),('crop',{'x':0,'y':0,'width':1,'height':1})):
                        result=jobs.preview(data,'source_cicp_srgb','region_'+mode,mode,rect)
                        expected=[(0,128,195,0),(1,1,255,128)] if mode=='overview' else [(0,128,195,0)]
                        self.assertEqual(read(result),(expected,{'srgb':1}))
                        self.assertEqual(result['sourceSha256'],hashlib.sha256(data).hexdigest())
                        self.assertEqual(result['engine']['colorInterpretation']['sourceCICP'],[1,13,0,1])
                        self.assertEqual(jobs._supervisor.active_count,0)
                        self.assertEqual(list(jobs.directory.iterdir()),[])
                finally:jobs.close()

if __name__=='__main__':unittest.main()
