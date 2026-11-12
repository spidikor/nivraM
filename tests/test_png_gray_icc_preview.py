"""Independent GRAY/XYZ fixtures, original-depth alpha and fail-closed intake."""
from array import array
import hashlib
import itertools
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zlib

from nivram import png_icc as icc, png_preview as pp
from nivram.png_preview_jobs import ImagePreviewJobs, validate_preview_result
from test_png_gamma_preview import fixture, read
from test_png_icc_preview import entries, rebuild, tag, change, curve, envelope

ROOT = Path(__file__).parent / 'fixtures' / 'icc-v4-gray'
CASES = json.loads((ROOT / 'independent-fixtures.json').read_text())
PROF = (ROOT / 'linear.icc').read_bytes()


def meta(profile=PROF, **kw):
    return pp._chunk(b'iCCP', envelope(profile, **kw))


def make(rows=None, color=4, depth=8, interlace=0, kind=0, metadata=None, **kw):
    return fixture([[(64, 0)]] if rows is None else rows, color, depth,
                   interlace, kind, metadata=meta() if metadata is None else metadata, **kw)


def source_pixels(data):
    """Independent full-depth fixture decoder, no production/Pillow/CMS imports.

    Test-only small images. Decode stored samples and scatter Adam7 passes to
    independently verify the frozen GA16 encoder before using its color oracle.
    """
    width, height, depth, color, _, _, interlace = struct.unpack('>IIBBBBB', data[16:29])
    assert width * height <= 100 and depth == 16 and color == 4
    compressed = b''; offset = 8
    while offset < len(data):
        size = int.from_bytes(data[offset:offset+4], 'big')
        if data[offset+4:offset+8] == b'IDAT':
            compressed += data[offset+8:offset+8+size]
        offset += 12+size
    raw = zlib.decompress(compressed); cursor = 0
    result = [[None] * width for _ in range(height)]
    passes = [(0,0,1,1)] if not interlace else [(0,0,8,8),(4,0,8,8),(0,4,4,8),(2,0,4,4),(0,2,2,4),(1,0,2,2),(0,1,1,2)]
    for x0,y0,dx,dy in passes:
        xs = list(range(x0,width,dx)); prior = [0] * (4*len(xs))
        if not xs: continue
        for y in range(y0,height,dy):
            kind = raw[cursor]; cursor += 1
            row = []
            for i in range(len(prior)):
                left = row[i-4] if i>=4 else 0
                up = prior[i]; corner = prior[i-4] if i>=4 else 0
                if kind == 0: predictor = 0
                elif kind == 1: predictor = left
                elif kind == 2: predictor = up
                elif kind == 3: predictor = (left+up)//2
                else:
                    assert kind == 4
                    estimate = left+up-corner
                    predictor = min((left,up,corner), key=lambda v: abs(estimate-v))
                row.append((raw[cursor]+predictor) & 255); cursor += 1
            for j,x in enumerate(xs): result[y][x] = list(struct.unpack_from('>HH', bytes(row), j*4))
            prior = row
    assert cursor == len(raw)
    return result


class GrayICCPreviewTests(unittest.TestCase):
    def reject(self, data):
        self.assertFalse(pp.preview_capability(data)['available'])
        with self.assertRaises(pp.PngPreviewError): pp.preview_png(data, mode='overview')

    def assert_colors(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for got,want in zip(actual, expected):
            self.assertTrue(all(abs(a-b)<=1 for a,b in zip(got[:3],want[:3])), (got,want))
            self.assertEqual(got[3:], tuple(want[3:]))
            if want[:3] in ([0,0,0],[255,255,255]): self.assertEqual(got[:3],tuple(want[:3]))

    def test_frozen_analytical_cases_geometry_and_provenance(self):
        for case in CASES:
            with self.subTest(file=case['file']):
                data = (ROOT/case['file']).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(),case['sha256'])
                self.assertTrue(pp.preview_capability(data)['available'])
                result = pp.preview_png(data, mode='overview')
                self.assert_colors(read(result)[0], case['expectedRGBA'])
                self.assertEqual(read(result)[1], {'srgb':1})
                self.assertEqual(result['sourceSha256'],case['sha256'])
                cv=result['engine']['colorConversion']; prov=result['engine']['iccProfile']
                self.assertEqual(cv['sourceColorModel'],'GRAY')
                self.assertEqual(cv['sourceColorDepth'],case['depth'])
                self.assertEqual(cv['profileSha256'],case['profileSha256'])
                self.assertEqual(cv['outputIntentPolicy'],1)
                self.assertFalse(cv['blackPointCompensation'])
                self.assertEqual(prov['admissionPolicy'],'bounded-v4-display-GRAY-XYZ-kTRC')
                self.assertEqual(prov['sizeBytes'],len((ROOT/(case['kind']+'.icc')).read_bytes()))
                validate_preview_result(result,case['sha256'],'overview',None,(9,9))
                rect={'x':2,'y':3,'width':3,'height':2}
                want=case['expectedRGBA']
                self.assert_colors(read(pp.preview_png(data, mode='crop',rect=rect))[0], [want[y*9+x] for y in (3,4) for x in (2,3,4)])
                with patch.object(pp,'OVERVIEW_EDGE',3): small=pp.preview_png(data,mode='overview')
                self.assert_colors(read(small)[0],[want[y*9+x] for y in (1,4,7) for x in (1,4,7)])
                self.assertEqual(hashlib.sha256(data).hexdigest(),case['sha256'])

    def test_independent_ga16_packing(self):
        cases=[c for c in CASES if c['depth']==16 and c['color']==4]
        self.assertEqual(len(cases),12)
        for case in cases:
            self.assertEqual(source_pixels((ROOT/case['file']).read_bytes()),case['input'])

    def test_seventy_formats_filters_interlaces(self):
        for color,depth in [(0,d) for d in (1,2,4,8,16)]+[(4,8),(4,16)]:
            case=next(c for c in CASES if c['kind']=='linear' and c['color']==color and c['depth']==depth)
            for interlace,kind in itertools.product((0,1),range(5)):
                with self.subTest(color=color,depth=depth,interlace=interlace,filter=kind):
                    # Frozen source data and colors; only fixture packing changes.
                    key=(257 if depth==16 else ((1<<depth)-1)//4,)
                    data=make(case['input'],color,depth,interlace,kind,**({'key':key} if color==0 else {}))
                    self.assert_colors(read(pp.preview_png(data,mode='overview'))[0],case['expectedRGBA'])
        for size in (1,2,3,4):
            data=make([[(64,0)]*size]*size,interlace=1)
            self.assertEqual(read(pp.preview_png(data,mode='overview'))[0],[(137,137,137,0)]*(size*size))

    def test_frozen_negative_and_metadata_cases(self):
        for case in json.loads((ROOT/'negative-fixtures.json').read_text()):
            data=(ROOT/case['file']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(),case['sha256'])
            self.reject(make(metadata=meta(data)))
        for case in json.loads((ROOT/'metadata-fixtures.json').read_text()):
            data=(ROOT/case['file']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(),case['sha256'])
            if case['expected']=='reject':self.reject(data)
            else:self.assert_colors(read(pp.preview_png(data,mode='overview'))[0],case['expectedRGBA'])

    def test_all_metadata_permutations_and_ignored_instrumentation(self):
        chunks=[meta(),pp._chunk(b'sRGB',b'\3'),pp._chunk(b'gAMA',struct.pack('>I',1)),pp._chunk(b'cHRM',struct.pack('>8I',*pp.P3_CHROMATICITIES))]
        for order in itertools.permutations(chunks):
            self.assertEqual(read(pp.preview_png(make(metadata=b''.join(order)),mode='overview'))[0],[(137,137,137,0)])
        for lower in chunks[1:]:
            self.assertEqual(read(pp.preview_png(make(metadata=meta()+lower),mode='overview'))[0],[(137,137,137,0)])
        for cicp in (pp.CICP_SRGB,pp.CICP_DISPLAY_P3):
            for order in itertools.permutations(chunks+[pp._chunk(b'cICP',cicp)]):
                with patch.object(icc,'tables',side_effect=AssertionError('ignored table')),patch.object(icc,'convert',side_effect=AssertionError('ignored conversion')):
                    result=pp.preview_png(make(metadata=b''.join(order)),mode='overview')
                self.assertEqual(read(result)[0],[(64,64,64,0)])
                self.assertEqual(result['engine']['iccProfile']['selectedMetadata'],'cICP')
                self.assertEqual(result['bufferAccounting']['iccProfileWorkUpperBoundBytes'],icc.PROFILE_WORK_BYTES)
        for bad in (pp._chunk(b'gAMA',b'\0'*4),pp._chunk(b'cHRM',b'\0'*32)):
            self.reject(make(metadata=meta()+bad))

    def test_models_must_match_even_under_cicp(self):
        rgb=(ROOT.parent/'icc-v4'/'linear.icc').read_bytes()
        for winner in (b'',pp._chunk(b'cICP',pp.CICP_SRGB),pp._chunk(b'cICP',pp.CICP_DISPLAY_P3)):
            for color in (0,4):
                self.reject(make([[(64,)] if color==0 else [(64,0)]],color,metadata=meta(rgb)+winner))
            for color in (2,3,6):
                rows=[[(0,)]] if color==3 else [[(64,)* (4 if color==6 else 3)]]
                self.reject(make(rows,color,metadata=winner+meta(),**({'palette':[(64,64,64)]} if color==3 else {})))
        self.reject(make(metadata=meta(b'bad')+pp._chunk(b'cICP',pp.CICP_SRGB)))

    def test_original_depth_trns_alpha_and_sbit(self):
        for interlace in (0,1):
            data=make([[(257,),(258,),(128,)]],0,16,interlace,key=(257,))
            result=read(pp.preview_png(data,mode='overview'))[0]
            self.assertEqual([p[3] for p in result],[0,255,255]); self.assertEqual(result[2][:3],(6,6,6))
            self.assertEqual((257+128)//257,(258+128)//257)
            rows=[[(128,a) for a in (0,128,129,65535)]]
            data=make(rows,4,16,interlace)
            self.assertEqual(read(pp.preview_png(data,mode='overview'))[0],[(6,6,6,a) for a in (0,0,1,255)])
            for color,depth in [(0,2),(0,16),(4,8),(4,16)]:
                rows=[[(1,)] if color==0 else [(1,0)]]
                data=make(rows,color,depth,interlace)
                marked=make(rows,color,depth,interlace,metadata=meta()+pp._chunk(b'sBIT',bytes([1]*(1 if color==0 else 2))))
                self.assertEqual(read(pp.preview_png(data,mode='overview')),read(pp.preview_png(marked,mode='overview')))
            # Existing unused tRNS high-bit masking remains authoritative.
            data=make([[(1,),(2,)]],0,2,interlace,key=(65533,))
            self.assertEqual([p[3] for p in read(pp.preview_png(data,mode='overview'))[0]],[0,255])
        self.reject(make(key=(64,)))
        for depth,color in ((4,4),(1,4),(2,2),(16,3)):
            data=make();self.reject(data[:8]+pp._chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,depth,color,0,0,0))+data[33:])

    def test_gray_curves_bounds_and_plateaus(self):
        bad=[curve(b'curv',(0,)),curve(b'curv',(2561,)),curve(b'curv',(1,65535)),curve(b'curv',(0,65534)),curve(b'curv',(0,50000,40000,65535)),curve(b'curv',tuple([0]*4096+[65535])),b'curv'+b'\0'*4+struct.pack('>I',1),curve(b'para',(1,),1),curve(b'para',(0,)),curve(b'para',(11,)),curve(b'para',(2,0,0,1,0),3),curve(b'para',(2,1,-1,1,.5),3),curve(b'para',(2,1,0,1,.5),3),curve(b'para',(2,1,0,1,2),3),curve(b'para',(2,1,0,-1,.5),3),curve(b'para',(2,2,0,1,0),3)]
        for body in bad:self.reject(make(metadata=meta(tag(b'kTRC',body,PROF))))
        for body in (curve(b'curv'),curve(b'curv',(2560,)),curve(b'para',(1,)),curve(b'para',(1,1,0,1,.5),3),curve(b'curv',(0,0,32768,65535)),curve(b'curv',tuple(round(i*65535/4095) for i in range(4096)))):
            self.assertTrue(pp.preview_capability(make(metadata=meta(tag(b'kTRC',body,PROF))))['available'])
        for name,payload in entries(PROF):
            self.reject(make(metadata=meta(tag(name,change(payload,4,b'\1'),PROF))))

    def test_gray_tag_structure_and_header_bounds(self):
        parts=entries(PROF)
        for name,_ in parts:self.reject(make(metadata=meta(rebuild([(n,b) for n,b in parts if n!=name],PROF[:128]))))
        mutations=[(0,struct.pack('>I',len(PROF)-4)),(8,b'\2'),(9,b'\x50'),(10,b'\1'),(12,b'link'),(16,b'CMYK'),(20,b'Lab '),(36,b'nope'),(64,struct.pack('>I',4)),(68,b'\0'*4),(100,b'x'),(24,b'\0\0'),(26,struct.pack('>H',13)),(128,struct.pack('>I',65)),(136,struct.pack('>I',132)),(136,struct.pack('>I',253)),(140,struct.pack('>I',7)),(140,struct.pack('>I',2**32-1))]
        for off,value in mutations:self.reject(make(metadata=meta(change(PROF,off,value))))
        for name in (b'rXYZ',b'gXYZ',b'bXYZ',b'rTRC',b'gTRC',b'bTRC',b'A2B0',b'B2A0',b'D2B0',b'B2D0',b'gamt',b'pre0',b'ncl2',b'xxxx',b'chrm'):
            self.reject(make(metadata=meta(rebuild(parts+[(name,curve(b'curv'))],PROF[:128]))))
        self.reject(make(metadata=meta(rebuild(parts+[parts[0]],PROF[:128]))))
        text=parts[0][1]
        shared=rebuild([(n,text if n==b'cprt' else b) for n,b in parts],PROF[:128],shared=True)
        self.assertTrue(pp.preview_capability(make(metadata=meta(shared)))['available'])
        off=int.from_bytes(shared[136:140],'big')
        for value in (struct.pack('>I',off+4),):self.reject(make(metadata=meta(change(shared,148,value))))
        self.reject(make(metadata=meta(change(shared,152,struct.pack('>I',8)))))
        self.reject(make(metadata=meta(change(PROF+b'\0'*4,0,struct.pack('>I',len(PROF)+4)))))
        self.reject(make(metadata=meta(PROF[:-1]+b'x')))
        for off,value in [(0,b'desc'),(8,struct.pack('>I',65)),(12,struct.pack('>I',13)),(16,b'99'),(20,struct.pack('>I',3)),(24,struct.pack('>I',0)),(28,b'\xd8\0')]:
            self.reject(make(metadata=meta(tag(b'desc',change(text,off,value),PROF))))
        white=next(b for n,b in parts if n==b'wtpt')
        for body in (white[:-4],white+b'\0'*12,change(white,0,b'Lab '),change(white,8,struct.pack('>i',63193))):self.reject(make(metadata=meta(tag(b'wtpt',body,PROF))))

    def test_optional_tags_white_tolerance_intents_and_names_are_inert(self):
        xyz=b'XYZ '+b'\0'*4+struct.pack('>3i',32768,16384,65536)
        chad=b'sf32'+b'\0'*4+struct.pack('>9i',*[round(v*65536) for v in (1.1,.1,0,0,.9,0,0,.1,1.2)])
        for name,body in ((b'bkpt',xyz),(b'lumi',xyz),(b'chad',chad)):
            prof=rebuild(entries(PROF)+[(name,body)],PROF[:128])
            self.assertEqual(read(pp.preview_png(make(metadata=meta(prof)),mode='overview'))[0],[(137,137,137,0)])
            for bad in (body[:-4],change(body,0,b'xxxx'),change(body,8,struct.pack('>i',17*65536))):
                self.reject(make(metadata=meta(rebuild(entries(PROF)+[(name,bad)],PROF[:128]))))
        self.reject(make(metadata=meta(rebuild(entries(PROF)+[(b'chad',b'sf32'+b'\0'*40)],PROF[:128]))))
        for delta in (-2,2):
            white=b'XYZ '+b'\0'*4+struct.pack('>3i',63190+delta,65536+delta,54061+delta)
            self.assertEqual(read(pp.preview_png(make(metadata=meta(tag(b'wtpt',white,PROF))),mode='overview'))[0],[(137,137,137,0)])
        for intent in range(4):
            data=make(metadata=meta(change(PROF,64,struct.pack('>I',intent)),name=b'<sRGB-script>'))
            result=pp.preview_png(data,mode='overview')
            self.assertEqual(read(result)[0],[(137,137,137,0)])
            self.assertEqual(result['engine']['colorConversion']['sourceIntent'],intent)
            self.assertNotIn('<sRGB-script>',json.dumps(result))

    def test_envelope_and_inflation_limits(self):
        for name in (b'',b'x'*80,b' bad',b'bad ',b'a  b',b'a\x7fb',b'a\xa0b',b'a\x1fb'):
            self.reject(make(metadata=meta(name=name)))
        for name in (b'x'*79,b'\xa1profile\xff',b'a b'):
            self.assertTrue(pp.preview_capability(make(metadata=meta(name=name)))['available'])
        for compressed in (b'bad',zlib.compress(PROF)[:-1],zlib.compress(PROF)+b'x',zlib.compress(PROF)+zlib.compress(PROF),b'x'*(icc.MAX_COMPRESSED+1),zlib.compress(b'\0'*(icc.MAX_PROFILE+1))):
            self.reject(make(metadata=meta(compressed=compressed)))
        compressor=zlib.compressobj(zdict=b'secret')
        self.reject(make(metadata=meta(compressed=compressor.compress(PROF)+compressor.flush())))
        self.reject(make(metadata=meta(method=1)))
        self.reject(make(metadata=meta()+meta()))
        data=make();self.reject(data[:33]+data[33+len(meta()):-12]+meta()+data[-12:])
        compressed=zlib.compress(PROF)
        for name,size in (('MAX_COMPRESSED',len(compressed)),('MAX_PROFILE',len(PROF))):
            with patch.object(icc,name,size):self.assertTrue(pp.preview_capability(make())['available'])
            with patch.object(icc,name,size-1):self.reject(make())
        text=next(b for n,b in entries(PROF) if n==b'desc');extra=icc.MAX_PROFILE-len(PROF)
        text=change(text,20,struct.pack('>I',int.from_bytes(text[20:24],'big')+extra))+b'\0A'*(extra//2)
        large=tag(b'desc',text,PROF);self.assertEqual(len(large),icc.MAX_PROFILE)
        calls=[];real=zlib.decompressobj
        class Decoder:
            def __init__(self):self.d=real()
            def decompress(self,data,limit):calls.append((len(data),limit));return self.d.decompress(data,limit)
            def __getattr__(self,name):return getattr(self.d,name)
        with patch.object(icc.zlib,'decompressobj',Decoder):self.assertTrue(pp.preview_capability(make(metadata=meta(large)))['available'])
        self.assertTrue(calls);self.assertTrue(all(a<=65536 and 0<b<=65536 for a,b in calls))

    def test_single_table_exact_budgets_and_deadlines(self):
        for depth in (1,2,4,8,16):
            data=make([[(1,)]],0,depth);result=pp.preview_png(data,mode='overview');a=result['bufferAccounting']
            expected=(1<<depth)*array('d').itemsize+sys.getsizeof(array('d'))
            self.assertEqual(a['colorTableBytes'],expected)
            profile=icc._parse(PROF,lambda:None)
            tables=icc.tables(profile,depth,lambda:None);self.assertEqual(len(tables),1)
            self.assertEqual(sum(sys.getsizeof(t) for t in tables),expected)
            with patch.object(pp,'PIXEL_PIPELINE_BUDGET',a['pixelPipelineAccountedUpperBoundBytes']):pp.preview_png(data,mode='overview')
            with patch.object(pp,'PIXEL_PIPELINE_BUDGET',a['pixelPipelineAccountedUpperBoundBytes']-1),patch.object(icc,'tables',side_effect=AssertionError('allocated before budget')):
                with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='overview')
        def expired():raise TimeoutError('expired')
        for operation in (lambda:icc.parse_iccp(memoryview(envelope(PROF)),expired,icc.PROFILE_WORK_BYTES),lambda:icc._parse(PROF,expired),lambda:icc.tables(profile,16,expired)):
            with self.assertRaises(TimeoutError):operation()
        with self.assertRaises(ValueError):icc.parse_iccp(memoryview(envelope(PROF)),lambda:None,icc.PROFILE_WORK_BYTES-1)
        with patch.object(pp,'_deadline',side_effect=TimeoutError('expired')):
            with self.assertRaises(TimeoutError):pp.preview_png(make(),mode='overview')
        checks=[];icc.tables(profile,16,lambda:checks.append(True));self.assertGreaterEqual(len(checks),257)
        with patch.object(pp,'MAX_PREVIEW_BYTES',40):
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(make(),mode='overview')
        with self.assertRaises(pp.PngPreviewError):pp.preview_png(make(),mode='overview',expected_source_sha256='0'*64)

    def test_full_stream_outside_crop_and_crc(self):
        header=pp.PNG_SIGNATURE+pp._chunk(b'IHDR',struct.pack('>IIBBBBB',1,2,8,0,0,0,0))+meta()
        for raw in (b'\0a\5b',b'\0a',b'\0a\0bx'):
            data=header+pp._chunk(b'IDAT',zlib.compress(raw))+pp._chunk(b'IEND',b'')
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='crop',rect={'x':0,'y':0,'width':1,'height':1})
        stream=zlib.compress(b'\0a\0b')
        for bad in (stream[:-1],stream+b'x',stream+zlib.compress(b'')):
            # Capability deliberately validates chunks, not the compressed pixels.
            with self.assertRaises(pp.PngPreviewError):
                pp.preview_png(header+pp._chunk(b'IDAT',bad)+pp._chunk(b'IEND',b''),mode='overview')
        data=bytearray(make());data[-1]^=1;self.reject(bytes(data))

    def test_seeded_metamorphic_invariants(self):
        rng=random.Random(20261112)
        for depth in (8,16):
            maxv=(1<<depth)-1
            values=sorted(set([0,maxv]+[rng.randrange(maxv+1) for _ in range(64)]))
            rows=[[(v,) for v in values]]
            outputs=[]
            for name in ('identity','linear','gamma2','sampled'):
                profile=(ROOT/(name+'.icc')).read_bytes()
                data=make(rows,0,depth,metadata=meta(profile));out=read(pp.preview_png(data,mode='overview'))[0]
                outputs.append(out)
                self.assertEqual(out[0],(0,0,0));self.assertEqual(out[-1],(255,255,255))
                for c in range(3):self.assertEqual([p[c] for p in out],sorted(p[c] for p in out))
                self.assertTrue(all(max(p)-min(p)<=1 for p in out))
                # Reversible packing, alpha and profile record order must not change colors.
                reordered=rebuild(list(reversed(entries(profile))),profile[:128])
                for interlace in (0,1):
                    ga=make([[(v,0) for v in values]],4,depth,interlace,metadata=meta(reordered))
                    self.assertEqual([p[:3] for p in read(pp.preview_png(ga,mode='overview'))[0]],out)
                if depth==8:
                    scaled=make([[(v*257,) for v in values]],0,16,metadata=meta(profile))
                    self.assertEqual(read(pp.preview_png(scaled,mode='overview'))[0],out)
            self.assertEqual(outputs[0],outputs[1])
            self.assertTrue(all(b[c]<=a[c] for a,b in zip(outputs[1],outputs[2]) for c in range(3)))

    def test_mid_phase_deadlines_and_late_result_cancellation(self):
        # Distinguish checks inside work from merely rejecting at entry.
        def after_checks(limit):
            calls=[]
            def check():
                calls.append(True)
                if len(calls)==limit:raise TimeoutError('expired during work')
            return check,calls
        profile=icc._parse(PROF,lambda:None)
        for limit,operation in [
                (2,lambda check:icc.parse_iccp(memoryview(envelope(PROF)),check,icc.PROFILE_WORK_BYTES)),
                (5,lambda check:icc._parse(PROF,check)),
                (2,lambda check:icc.tables(profile,16,check))]:
            check,calls=after_checks(limit)
            with self.assertRaises(TimeoutError):operation(check)
            self.assertEqual(len(calls),limit)
        original=pp._expand_color
        def expire_row(row,samples,width,encoding,table,end):
            return original(row,samples,width,encoding,table,time.monotonic()-1)
        for interlace in (0,1):
            with patch.object(pp,'_expand_color',side_effect=expire_row):
                with self.assertRaises(TimeoutError):pp.preview_png(make(interlace=interlace),mode='overview')
        from nivram.worker_processes import WorkerCancelled
        data=make();completed=pp.preview_png(data,mode='overview')
        with tempfile.TemporaryDirectory() as temp:
            jobs=ImagePreviewJobs(Path(temp)/'cache')
            def late(command,**options):
                jobs.cancel('gray_late','gray_late_region')
                return subprocess.CompletedProcess(command,0,json.dumps(completed),'')
            try:
                with patch.object(jobs._supervisor,'run',side_effect=late):
                    with self.assertRaises(WorkerCancelled):jobs.preview(data,'gray_late','gray_late_region','overview')
                self.assertEqual(jobs._supervisor.active_count,0)
                self.assertEqual(list(jobs.directory.iterdir()),[])
            finally:jobs.close()

    def test_real_supervised_workers_and_cleanup(self):
        for interlace in (0,1):
            case=next(c for c in CASES if c['kind']=='linear' and c['depth']==16 and c['color']==4 and c['interlace']==interlace)
            data=(ROOT/case['file']).read_bytes()
            with tempfile.TemporaryDirectory() as temp:
                jobs=ImagePreviewJobs(Path(temp)/'cache')
                try:
                    for mode,rect in [('overview',None),('crop',{'x':0,'y':0,'width':1,'height':1})]:
                        out=jobs.preview(data,'gray_source','gray_'+mode,mode,rect)
                        self.assert_colors(read(out)[0],case['expectedRGBA'] if mode=='overview' else case['expectedRGBA'][:1])
                        validate_preview_result(out,case['sha256'],mode,rect,(9,9))
                        self.assertEqual(jobs._supervisor.active_count,0);self.assertEqual(list(jobs.directory.iterdir()),[])
                finally:jobs.close()

    def test_real_cancellation_reaps_without_publication(self):
        from nivram.worker_processes import WorkerCancelled
        data=make([[(32768,0)]*4096]*64,4,16)
        with tempfile.TemporaryDirectory() as temp:
            jobs=ImagePreviewJobs(Path(temp)/'cache');outcome={}
            def run():
                try:outcome['result']=jobs.preview(data,'gray_cancel_source','gray_cancel_region','overview')
                except BaseException as exc:outcome['error']=exc
            thread=threading.Thread(target=run,daemon=True);thread.start()
            try:
                end=time.monotonic()+5
                while jobs._supervisor.active_count==0 and thread.is_alive() and time.monotonic()<end:time.sleep(.001)
                self.assertEqual(jobs._supervisor.active_count,1)
                jobs.cancel('gray_cancel_source','gray_cancel_region');thread.join(5)
                self.assertFalse(thread.is_alive());self.assertNotIn('result',outcome)
                self.assertIsInstance(outcome.get('error'),WorkerCancelled)
                self.assertEqual(jobs._supervisor.active_count,0);self.assertEqual(list(jobs.directory.iterdir()),[])
            finally:jobs.close();thread.join(5)


if __name__=='__main__':unittest.main()
