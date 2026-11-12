"""Bounded ICC intake: independently frozen Decimal fixtures and abuse cases.

No ImageCms, production transform, or observed output generates expected colors.
Fixture encoder/filter packing reuse is independent of color expectations.
"""
import base64
import hashlib
import itertools
import json
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zlib

from nivram import png_preview as pp, png_icc as icc
from nivram.png_preview_jobs import ImagePreviewJobs, validate_preview_result
from test_png_gamma_preview import fixture, read

ROOT=Path(__file__).parent/'fixtures'/'icc-v4'
CASES=sum((json.loads((ROOT/f).read_text()) for f in ('independent-fixtures.json','supplemental-fixtures.json')),[])
PROF=(ROOT/'linear.icc').read_bytes()


def envelope(profile=PROF,name=b'profile',method=0,compressed=None):
    return name+b'\0'+bytes([method])+(zlib.compress(profile) if compressed is None else compressed)


def meta(profile=PROF,**kw):return pp._chunk(b'iCCP',envelope(profile,**kw))


def make(rows=None,color=6,depth=8,interlace=0,kind=0,metadata=None,**kw):
    rows=rows if rows is not None else [[(64,128,192,0)]]
    return fixture(rows,color,depth,interlace,kind,metadata=meta() if metadata is None else metadata,**kw)


def entries(profile):
    return [(n,profile[o:o+s]) for n,o,s in (struct.unpack_from('>4sII',profile,132+i*12) for i in range(int.from_bytes(profile[128:132],'big')))]


def rebuild(parts,header=PROF[:128],shared=False):
    off=132+12*len(parts); records=b'';payload=b'';spans={}
    for name,data in parts:
        if shared and data in spans:
            pos=spans[data]
        else:
            pos=off;spans[data]=pos;pad=b'\0'*(-len(data)%4);payload+=data+pad;off+=len(data)+len(pad)
        records+=name+struct.pack('>II',pos,len(data))
    h=bytearray(header);struct.pack_into('>I',h,0,off)
    return bytes(h)+struct.pack('>I',len(parts))+records+payload


def tag(name,data,profile=PROF):
    return rebuild([(n,data if n==name else b) for n,b in entries(profile)],profile[:128])


def change(profile,offset,value):
    data=bytearray(profile);data[offset:offset+len(value)]=value;return bytes(data)


def curve(kind,values=(),function=0):
    if kind==b'curv':return kind+b'\0'*4+struct.pack('>I',len(values))+struct.pack('>'+'H'*len(values),*values)
    return kind+b'\0'*4+struct.pack('>HH',function,0)+struct.pack('>'+'i'*len(values),*(round(v*65536) for v in values))


class ICCPreviewTests(unittest.TestCase):
    def reject(self,data):
        self.assertFalse(pp.preview_capability(data)['available'])
        with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='overview')

    def assert_colors(self,actual,expected):
        self.assertEqual(len(actual),len(expected))
        for p,q in zip(actual,expected):
            self.assertTrue(all(abs(a-b)<=1 for a,b in zip(p[:3],q[:3])),(p,q))
            self.assertEqual(p[3:],tuple(q[3:]))

    def test_frozen_independent_fixtures_and_provenance(self):
        for case in CASES:
            with self.subTest(file=case['file']):
                data=(ROOT/case['file']).read_bytes();digest=hashlib.sha256(data).hexdigest()
                self.assertEqual(digest,case['sha256'])
                result=pp.preview_png(data,mode='overview')
                self.assert_colors(read(result)[0],case['expectedRGBA'])
                self.assertEqual(read(result)[1],{'srgb':1})
                self.assertEqual(result['sourceSha256'],digest)
                self.assertEqual(result['engine']['colorConversion']['profileSha256'],case['profileSha256'])
                self.assertEqual(result['engine']['colorConversion']['outputIntentPolicy'],1)
                self.assertFalse(result['engine']['colorConversion']['blackPointCompensation'])
                validate_preview_result(result,digest,'overview',None,(4,1))
                self.assertEqual(hashlib.sha256(data).hexdigest(),digest)
        self.assertEqual(read(pp.preview_png((ROOT/'original-srgb.png').read_bytes(),mode='overview'))[0],[(64,128,192),(255,0,128)])

    def test_formats_filters_interlace_alpha_crop_overview(self):
        # 80 combinations: RGB/RGBA8/16 and indexed1/2/4/8, five filters, two interlaces.
        formats=[(2,8),(2,16),(6,8),(6,16),(3,1),(3,2),(3,4),(3,8)]
        for color,depth in formats:
            for interlace,kind in itertools.product((0,1),range(5)):
                with self.subTest(color=color,depth=depth,interlace=interlace,filter=kind):
                    case=next(c for c in CASES if c['file']==f'linear-{16 if depth==16 else 8}.png')
                    src=[tuple(p[:3] if color==2 else p) for p in case['input']]
                    expected=[tuple(p[:3] if color==2 else p) for p in case['expectedRGBA']]
                    kw={}
                    if color==3:
                        src=[(0,),(1,)]
                        kw={'palette':[tuple(p[:3]) for p in case['input'][:2]],'alpha':(0,128)}
                        expected=[tuple(p) for p in case['expectedRGBA'][:2]]
                    rows=[[src[(x+y)%len(src)] for x in range(9)] for y in range(11)]
                    want=[expected[(x+y)%len(src)] for y in range(11) for x in range(9)]
                    data=make(rows,color,depth,interlace,kind,**kw)
                    self.assert_colors(read(pp.preview_png(data,mode='overview'))[0],want)
                    rect={'x':2,'y':3,'width':3,'height':2}
                    self.assert_colors(read(pp.preview_png(data,mode='crop',rect=rect))[0],[want[y*9+x] for y in (3,4) for x in (2,3,4)])
                    with patch.object(pp,'OVERVIEW_EDGE',3):small=pp.preview_png(data,mode='overview')
                    self.assert_colors(read(small)[0],[want[y*9+x] for y in (1,5,9) for x in (2,6)])

    def test_precision_trns_and_sbit(self):
        for interlace in (0,1):
            key=(128,32768,50001);near=(128,32769,50001)
            data=make([[key,near]],2,16,interlace,key=key)
            pixels=read(pp.preview_png(data,mode='overview'))[0]
            self.assertEqual([p[3] for p in pixels],[0,255])
            self.assertEqual([p[0] for p in pixels],[6,6])
            self.assertEqual(tuple((v+128)//257 for v in key),tuple((v+128)//257 for v in near))
            # Premature 8-bit reduction would turn the first red sample into zero.
            reduced=make([[(0,128,195)]],2,8)
            self.assertNotEqual(pixels[0][:3],read(pp.preview_png(reduced,mode='overview'))[0][0])
            marked=make([[key,near]],2,16,interlace,key=key,metadata=meta()+pp._chunk(b'sBIT',b'\x08\x09\x0a'))
            self.assertEqual(read(pp.preview_png(marked,mode='overview'))[0],pixels)

    def test_precedence_all_orders_and_cicp_no_icc_tables(self):
        chunks=[meta(),pp._chunk(b'sRGB',b'\x03'),pp._chunk(b'gAMA',struct.pack('>I',1)),pp._chunk(b'cHRM',struct.pack('>8I',*pp.P3_CHROMATICITIES))]
        for order in itertools.permutations(chunks):
            out=pp.preview_png(make(metadata=b''.join(order)),mode='overview')
            self.assertEqual(read(out)[0],[(137,188,225,0)])
        for fallback in chunks[1:]:
            self.assertEqual(read(pp.preview_png(make(metadata=meta()+fallback),mode='overview'))[0],[(137,188,225,0)])
        for c in (pp.CICP_SRGB,pp.CICP_DISPLAY_P3):
            for order in itertools.permutations(chunks+[pp._chunk(b'cICP',c)]):
                with patch.object(icc,'tables',side_effect=AssertionError('ignored ICC table')),patch.object(icc,'convert',side_effect=AssertionError('ignored ICC conversion')):
                    out=pp.preview_png(make(metadata=b''.join(order)),mode='overview')
                self.assertEqual(out['engine']['iccProfile']['selectedMetadata'],'cICP')
                self.assertEqual(read(out)[0],[(64,128,192,0)] if c==pp.CICP_SRGB else [(32,130,197,0)])
        self.reject(make(metadata=pp._chunk(b'cICP',pp.CICP_SRGB)+meta(b'bad')))

    def test_keyword_compression_and_order_rejections(self):
        for name in (b'',b'x'*80,b' bad',b'bad ',b'a  b',b'a\x7fb',b'a\xa0b',b'a\x1fb'):
            self.reject(make(metadata=meta(name=name)))
        for name in (b'x'*79,b'\xa1profile\xff',b'a b'):
            self.assertTrue(pp.preview_capability(make(metadata=meta(name=name)))['available'])
        bad=[b'a\0',b'a\0\0',envelope(method=1),envelope(compressed=zlib.compress(PROF)[:-1]),envelope(compressed=zlib.compress(PROF)+b'x'),envelope(compressed=zlib.compress(PROF)+zlib.compress(PROF)),envelope(compressed=b'no zlib')]
        compressed=bytearray(zlib.compress(PROF));compressed[-1]^=1;bad.append(envelope(compressed=bytes(compressed)))
        compressor=zlib.compressobj(zdict=b'secret');bad.append(envelope(compressed=compressor.compress(PROF)+compressor.flush()))
        for body in bad:self.reject(make(metadata=pp._chunk(b'iCCP',body)))
        self.reject(make(metadata=meta()+meta()))
        data=make();self.reject(data[:33]+data[33+len(meta()):-12]+meta()+data[-12:])
        indexed=make([[(0,)]],3,1,palette=[(64,128,192)])
        pos=indexed.index(b'PLTE')-4;end=pos+12+int.from_bytes(indexed[pos:pos+4],'big')
        self.reject(indexed[:33]+indexed[33+len(meta()):end]+meta()+indexed[end:])
        for color in (0,4):
            rows=[[(64,)] if color==0 else [(64,128)]]
            self.reject(make(rows,color,8));self.reject(make(rows,color,8,metadata=meta()+pp._chunk(b'cICP',pp.CICP_SRGB)))

    def test_header_and_tag_layout_rejections(self):
        mutations=[(0,struct.pack('>I',len(PROF)-4)),(8,b'\x02'),(9,b'\x50'),(10,b'\1'),(12,b'link'),(16,b'CMYK'),(20,b'Lab '),(36,b'nope'),(64,struct.pack('>I',4)),(68,b'\0'*4),(100,b'x'),(24,b'\0\0'),(26,struct.pack('>H',13)),(128,struct.pack('>I',65)),(136,struct.pack('>I',132)),(136,struct.pack('>I',253)),(140,struct.pack('>I',7)),(140,struct.pack('>I',2**32-1))]
        for off,value in mutations:
            with self.subTest(off=off,value=value):self.reject(make(metadata=meta(change(PROF,off,value))))
        for parts in (entries(PROF)[:-1],entries(PROF)+[entries(PROF)[0]],[(b'A2B0',b) if n==b'rTRC' else (n,b) for n,b in entries(PROF)]):self.reject(make(metadata=meta(rebuild(parts))))
        shared=rebuild(entries(PROF),shared=True)
        self.assertTrue(pp.preview_capability(make(metadata=meta(shared)))['available'])
        # Same offset with different size and partial overlap are never aliases.
        records=[struct.unpack_from('>4sII',shared,132+i*12) for i in range(int.from_bytes(shared[128:132],'big'))]
        i=next(i for i,r in enumerate(records) if r[0]==b'gTRC')
        self.reject(make(metadata=meta(change(shared,132+i*12+8,struct.pack('>I',8)))))
        self.reject(make(metadata=meta(change(shared,132+i*12+4,struct.pack('>I',records[i][1]+4)))))
        first=records[0][1];self.reject(make(metadata=meta(change(shared,first+4,b'\1'))))
        # >3 padding and nonzero trailing padding.
        self.reject(make(metadata=meta(change(PROF+b'\0'*4,0,struct.pack('>I',len(PROF)+4)))))
        self.reject(make(metadata=meta(PROF[:-1]+b'x')))

    def test_text_xyz_chad_chrm_rejections(self):
        text=next(b for n,b in entries(PROF) if n==b'desc')
        for off,value in [(0,b'desc'),(8,struct.pack('>I',65)),(12,struct.pack('>I',13)),(16,b'99'),(20,struct.pack('>I',3)),(24,struct.pack('>I',0)),(28,b'\xd8\0')]:
            self.reject(make(metadata=meta(tag(b'desc',change(text,off,value)))))
        xyz=next(b for n,b in entries(PROF) if n==b'rXYZ')
        for body in (xyz+b'\0'*12,change(xyz,0,b'Lab '),change(xyz,8,struct.pack('>i',5*65536)),b'XYZ '+b'\0'*16):
            self.reject(make(metadata=meta(tag(b'rXYZ',body))))
        self.reject(make(metadata=meta(tag(b'wtpt',change(xyz,8,b'\0'*4)))))
        chad=next(b for n,b in entries(PROF) if n==b'chad')
        for body in (chad[:-4],change(chad,0,b'XYZ '),change(chad,8,struct.pack('>i',17*65536)),b'sf32'+b'\0'*40):self.reject(make(metadata=meta(tag(b'chad',body))))
        for body in (b'chrm'+b'\0'*4+struct.pack('>HH6I',2,0,*([0]*6)),b'chrm'+b'\0'*4+struct.pack('>HH6I',3,0,*([65536]*6))):
            self.reject(make(metadata=meta(rebuild(entries(PROF)+[(b'chrm',body)]))))

    def test_unsupported_profile_types_source_intents_and_inert_names(self):
        for signature in (b'scnr',b'prtr',b'link',b'spac',b'abst',b'nmcl'):
            self.reject(make(metadata=meta(change(PROF,12,signature))))
        for signature in (b'GRAY',b'CMYK',b'4CLR'):
            self.reject(make(metadata=meta(change(PROF,16,signature))))
        for signature in (b'A2B0',b'B2A0',b'D2B0',b'B2D0',b'gamt',b'pre0',b'ncl2',b'xxxx'):
            self.reject(make(metadata=meta(rebuild(entries(PROF)+[(signature,b'curv'+b'\0'*8)]))))
        for intent in range(4):
            data=make(metadata=meta(change(PROF,64,struct.pack('>I',intent)),name=b'<script>not trusted</script>'))
            out=pp.preview_png(data,mode='overview')
            self.assertEqual(out['engine']['colorConversion']['sourceIntent'],intent)
            self.assertEqual(out['engine']['colorConversion']['outputIntentPolicy'],1)
            self.assertEqual(read(out)[0],[(137,188,225,0)])
            self.assertNotIn('<script>',json.dumps(out))

    def test_curve_bounds_and_analytic_parametric_domains(self):
        bad=[curve(b'curv',(0,)),curve(b'curv',(2561,)),curve(b'curv',(1,65535)),curve(b'curv',(0,65534)),curve(b'curv',(0,50000,40000,65535)),curve(b'curv',tuple([0]*4096+[65535])),b'curv'+b'\0'*4+struct.pack('>I',1),curve(b'para',(1,),1),curve(b'para',(0,)),curve(b'para',(11,)),curve(b'para',(2,0,0,1,0),3),curve(b'para',(2,1,-1,1,.5),3),curve(b'para',(2,1,0,1,.5),3),curve(b'para',(2,1,0,1,2),3),curve(b'para',(2,1,0,-1,.5),3),curve(b'para',(2,2,0,1,0),3)]
        for body in bad:
            self.reject(make(metadata=meta(tag(b'rTRC',body))))
        for body in (curve(b'curv'),curve(b'curv',(2560,)),curve(b'para',(1,)),curve(b'para',(1,1,0,1,.5),3),curve(b'curv',tuple(round(i*65535/4095) for i in range(4096)))):
            self.assertTrue(pp.preview_capability(make(metadata=meta(tag(b'rTRC',body))))['available'])

    def test_profile_compression_and_inflation_limits(self):
        compressed=zlib.compress(PROF)
        with patch.object(icc,'MAX_COMPRESSED',len(compressed)):
            self.assertTrue(pp.preview_capability(make())['available'])
        with patch.object(icc,'MAX_COMPRESSED',len(compressed)-1):self.reject(make())
        with patch.object(icc,'MAX_PROFILE',len(PROF)):
            self.assertTrue(pp.preview_capability(make())['available'])
        with patch.object(icc,'MAX_PROFILE',len(PROF)-1):self.reject(make())
        self.reject(make(metadata=meta(b'\0'*(icc.MAX_PROFILE+1))))
        self.reject(make(metadata=meta(compressed=b'x'*(icc.MAX_COMPRESSED+1))))

    def test_deadlines_buffers_source_identity_and_output(self):
        data=make();out=pp.preview_png(data,mode='overview');a=out['bufferAccounting']
        self.assertEqual(a['colorTableBytes'],icc.table_bytes(8))
        self.assertEqual(a['iccProfileWorkUpperBoundBytes'],icc.PROFILE_WORK_BYTES)
        with patch.object(pp,'PIXEL_PIPELINE_BUDGET',a['pixelPipelineAccountedUpperBoundBytes']-1),patch.object(icc,'tables',side_effect=AssertionError('allocated before budget')):
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='overview')
        with patch.object(pp,'_deadline',side_effect=TimeoutError('expired')):
            with self.assertRaises(TimeoutError):pp.preview_png(data,mode='overview')
        with self.assertRaises(TimeoutError):icc.parse_iccp(memoryview(envelope()),lambda:(_ for _ in ()).throw(TimeoutError()),icc.PROFILE_WORK_BYTES)
        with patch.object(pp,'MAX_PREVIEW_BYTES',40):
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='overview')
        with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='overview',expected_source_sha256='0'*64)

    def test_full_stream_validation_outside_crop(self):
        # Every palette row must validate even when not selected.
        data=make([[(0,)],[(1,)]],3,1,palette=[(1,2,3)])
        with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='crop',rect={'x':0,'y':0,'width':1,'height':1})
        header=pp.PNG_SIGNATURE+pp._chunk(b'IHDR',struct.pack('>IIBBBBB',1,2,8,2,0,0,0))+meta()
        for raw in (b'\0abc\5def',b'\0abc',b'\0abc\0defx'):
            data=header+pp._chunk(b'IDAT',zlib.compress(raw))+pp._chunk(b'IEND',b'')
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='crop',rect={'x':0,'y':0,'width':1,'height':1})
        stream=zlib.compress(b'\0abc\0def')
        for bad in (stream[:-1],stream+b'x',stream+zlib.compress(b'')):
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(header+pp._chunk(b'IDAT',bad)+pp._chunk(b'IEND',b''),mode='overview')

    def test_real_icc_cancellation_reaps_worker_without_publication(self):
        from nivram.worker_processes import WorkerCancelled
        data=make([[(128,32768,50001)]*4096]*64,2,16)
        with tempfile.TemporaryDirectory() as temp:
            jobs=ImagePreviewJobs(Path(temp)/'cache');outcome={}
            def run():
                try:outcome['result']=jobs.preview(data,'icc_cancel_source','icc_cancel_region','overview')
                except BaseException as exc:outcome['error']=exc
            thread=threading.Thread(target=run,daemon=True);thread.start()
            try:
                end=time.monotonic()+5
                while jobs._supervisor.active_count==0 and thread.is_alive() and time.monotonic()<end:time.sleep(.001)
                self.assertEqual(jobs._supervisor.active_count,1)
                jobs.cancel('icc_cancel_source','icc_cancel_region');thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertNotIn('result',outcome)
                self.assertIsInstance(outcome.get('error'),WorkerCancelled)
                self.assertEqual(jobs._supervisor.active_count,0)
                self.assertEqual(list(jobs.directory.iterdir()),[])
            finally:jobs.close();thread.join(5)

    def test_full_size_profile_boundary_and_bounded_decoder_calls(self):
        # A valid inert text payload reaches the real one-MiB boundary exactly.
        base=rebuild(entries(PROF));size=icc.MAX_PROFILE-len(base)
        text=next(b for n,b in entries(base) if n==b'desc')
        length=int.from_bytes(text[20:24],'big')
        enlarged=change(text,20,struct.pack('>I',length+size))+b'\0A'*(size//2)
        large=tag(b'desc',enlarged,base)
        self.assertEqual(len(large),icc.MAX_PROFILE)
        calls=[];real=zlib.decompressobj
        class Decoder:
            def __init__(self):self.d=real()
            def decompress(self,data,limit):
                calls.append((len(data),limit));return self.d.decompress(data,limit)
            def __getattr__(self,name):return getattr(self.d,name)
        with patch.object(icc.zlib,'decompressobj',Decoder):
            self.assertTrue(pp.preview_capability(make(metadata=meta(large)))['available'])
        self.assertTrue(calls)
        self.assertTrue(all(a<=65536 and 0<b<=65536 for a,b in calls))
        self.reject(make(metadata=meta(large+b'\0')))

    def test_real_supervised_workers(self):
        for interlace in (0,1):
            case=next(c for c in CASES if c['file']=='linear-16.png');data=make([[tuple(p) for p in case['input']]],6,16,interlace)
            with tempfile.TemporaryDirectory() as temp:
                jobs=ImagePreviewJobs(Path(temp)/'cache')
                try:
                    for mode,rect in [('overview',None),('crop',{'x':0,'y':0,'width':1,'height':1})]:
                        out=jobs.preview(data,'icc_source','icc_'+mode,mode,rect)
                        self.assert_colors(read(out)[0],case['expectedRGBA'] if mode=='overview' else case['expectedRGBA'][:1])
                        self.assertEqual(jobs._supervisor.active_count,0)
                        self.assertEqual(list(jobs.directory.iterdir()),[])
                finally:jobs.close()

if __name__=='__main__':unittest.main()
