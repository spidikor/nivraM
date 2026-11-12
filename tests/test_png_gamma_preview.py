"""Explicit gamma + canonical primaries: independent Decimal and packed fixtures."""
import base64
from decimal import Decimal, localcontext
import hashlib
import io
import struct
import time
import unittest
from unittest.mock import patch
import zlib
from PIL import Image
from nivram import png_preview as pp
from nivram.png_preview_jobs import validate_preview_result
from test_png_adam7_preview import PASS_LABELS, filtered

CHROMA = struct.pack('>8I',31270,32900,64000,33000,30000,60000,15000,6000)
CHANNELS = {0:1,2:3,3:1,4:2,6:4}

def oracle(sample, depth, gamma):
    with localcontext() as ctx:
        ctx.prec = 60
        x = Decimal(sample)/((1 << depth)-1)
        light = x ** (Decimal(100000)/gamma)
        if light <= Decimal('0.0031308'):
            encoded = Decimal('12.92')*light
        else:
            encoded = Decimal('1.055')*light**(Decimal(5)/12)-Decimal('0.055')
        return min(255,max(0,int(encoded*255+Decimal('.5'))))

def pack(pixels, depth):
    values=[v for pixel in pixels for v in pixel]
    if depth==16:return b''.join(struct.pack('>H',v) for v in values)
    if depth==8:return bytes(values)
    bits=''.join(format(v,f'0{depth}b') for v in values)
    bits += '0' * (-len(bits)%8)
    return bytes(int(bits[i:i+8],2) for i in range(0,len(bits),8))

def fixture(rows, color, depth, interlace=0, kind=0, gamma=100000, key=None, palette=None, alpha=None, metadata=None):
    before=(pp._chunk(b'gAMA',struct.pack('>I',gamma))+pp._chunk(b'cHRM',CHROMA)
            if metadata is None else metadata)
    if palette is not None:before+=pp._chunk(b'PLTE',bytes(v for p in palette for v in p))
    if key is not None:before+=pp._chunk(b'tRNS',b''.join(struct.pack('>H',v) for v in key))
    if alpha is not None:before+=pp._chunk(b'tRNS',bytes(alpha))
    raw=bytearray()
    for number in (range(1,8) if interlace else (None,)):
        prior=None
        for y,row in enumerate(rows):
            chosen=[p for x,p in enumerate(row) if number is None or PASS_LABELS[y%8][x%8]==number]
            if not chosen:continue
            packed=pack(chosen,depth)
            if prior is None:prior=bytes(len(packed))
            raw.extend(filtered(packed,prior,kind,max(1,(CHANNELS[color]*depth+7)//8)))
            prior=packed
    return (pp.PNG_SIGNATURE+pp._chunk(b'IHDR',struct.pack('>IIBBBBB',len(rows[0]),len(rows),depth,color,0,0,interlace))
            +before+pp._chunk(b'IDAT',zlib.compress(raw))+pp._chunk(b'IEND',b''))

def read(result):
    data=base64.b64decode(result['pngBase64'])
    with Image.open(io.BytesIO(data)) as im:return list(im.getdata()),dict(im.info)

class GammaTests(unittest.TestCase):
    def test_literal_linear_colors_and_strict_output(self):
        data=fixture([[(64,128,192)]],2,8)
        result=pp.preview_png(data,mode='overview')
        self.assertEqual(read(result), ([(137,188,225)], {'srgb':1}))
        self.assertEqual(result['engine']['colorConversion']['sourceGammaInteger'],100000)
        self.assertEqual(result['bufferAccounting']['colorTableBytes'],256)
        self.assertEqual(result['sourceSha256'],hashlib.sha256(data).hexdigest())
        validate_preview_result(result,result['sourceSha256'],'overview',None,(1,1))

    def test_decimal_oracle_tables_and_full_16bit_precision(self):
        for gamma in (1,45455,100000,220000,2147483647):
            table=pp._gamma_table(8,gamma,time.monotonic()+45)
            self.assertEqual(list(table),[oracle(s,8,gamma) for s in range(256)])
        # Every original16 sample, not a sampled selection or production oracle.
        table=pp._gamma_table(16,100000,time.monotonic()+45)
        self.assertEqual(list(table),[oracle(s,16,100000) for s in range(65536)])
        self.assertNotEqual(table[128],oracle(0,8,100000))

    def test_every_core_format_filter_and_interlace(self):
        for color,depths in {0:(1,2,4,8,16),2:(8,16),3:(1,2,4,8),4:(8,16),6:(8,16)}.items():
            for depth in depths:
                maxv=(1<<depth)-1
                palette=[(0,64,128),(192,32,255)] if color==3 else None
                rows=[[tuple((x*173+y*29+c*71)%(2 if color==3 else maxv+1) for c in range(CHANNELS[color])) for x in range(9)] for y in range(11)]
                expected=[]
                for row in rows:
                    for p in row:
                        rgb=palette[p[0]] if color==3 else (p[0],)*3 if color in (0,4) else p[:3]
                        q=tuple(oracle(v,8 if color==3 else depth,100000) for v in rgb)
                        if color in (4,6):q+=((p[-1]*255+maxv//2)//maxv,)
                        if color==3:q+=((0,128)[p[0]],)
                        expected.append(q)
                for interlace in (0,1):
                    for kind in range(5):
                        with self.subTest(color=color,depth=depth,interlace=interlace,filter=kind):
                            data=fixture(rows,color,depth,interlace,kind,palette=palette,alpha=(0,128) if color==3 else None)
                            self.assertTrue(pp.preview_capability(data)['available'])
                            with patch.object(pp.Image,'open',side_effect=AssertionError('Adam7 may not decode with Pillow')) if interlace else patch.object(pp,'MAX_EDGE',pp.MAX_EDGE):
                                result=pp.preview_png(data,mode='overview')
                            self.assertEqual(read(result)[0],expected)
                            self.assertEqual(read(result)[1],{'srgb':1})

    def test_original_transparency_hidden_color_and_exact_geometry(self):
        for color in (0,2):
            key=(257,) if color==0 else (257,514,771)
            rows=[[key,tuple(v+1 for v in key),(128,)*len(key)]]
            for interlace in (0,1):
                data=fixture(rows,color,16,interlace,key=key)
                pixels,_=read(pp.preview_png(data,mode='overview'))
                self.assertEqual([p[-1] for p in pixels],[0,255,255])
                self.assertEqual(pixels[0][:-1],tuple(oracle(v,16,100000) for v in (key*3 if color==0 else key)))
                self.assertEqual(pixels[2][:3],(6,6,6))
                rect={'x':1,'y':0,'width':1,'height':1}
                result=pp.preview_png(data,mode='crop',rect=rect)
                self.assertEqual(read(result)[0],[pixels[1]])
                self.assertEqual(result['rect'],rect)
                with patch.object(pp,'OVERVIEW_EDGE',1):
                    self.assertEqual(read(pp.preview_png(data,mode='overview'))[0],[pixels[1]])
        for interlace in (0,1):
            data=fixture([[(128,32768,65535,0),(65535,128,32768,32768)]],6,16,interlace)
            self.assertEqual(read(pp.preview_png(data,mode='overview'))[0],[(6,188,255,0),(255,6,188,128)])

    def test_srgb_override_unchanged_and_conflicts_rejected(self):
        canonical=pp._chunk(b'gAMA',struct.pack('>I',45455))+pp._chunk(b'cHRM',CHROMA)
        data=fixture([[(64,128,192)]],2,8,metadata=canonical)
        self.assertEqual(read(pp.preview_png(data,mode='overview'))[0],[tuple(oracle(v,8,45455) for v in (64,128,192))])
        for intent in range(4):
            data=fixture([[(64,128,192)]],2,8,metadata=canonical+pp._chunk(b'sRGB',bytes([intent])))
            self.assertEqual(read(pp.preview_png(data,mode='overview')), ([(64,128,192)],{'srgb':intent}))
        for metadata in (pp._chunk(b'sRGB',b'\0')+pp._chunk(b'gAMA',struct.pack('>I',100000)),pp._chunk(b'gAMA',struct.pack('>I',100000))+pp._chunk(b'sRGB',b'\0')):
            self.assertFalse(pp.preview_capability(fixture([[(1,2,3)]],2,8,metadata=metadata))['available'])

    def test_malformed_missing_duplicate_conflicting_color_metadata(self):
        g=pp._chunk(b'gAMA',struct.pack('>I',100000));c=pp._chunk(b'cHRM',CHROMA)
        bad=[g,c,g+g+c,g+c+c]
        bad += [pp._chunk(b'gAMA',b)+c for b in (b'',b'\0'*4,struct.pack('>I',0x80000000),struct.pack('>I',0xffffffff),b'12345')]
        bad += [g+pp._chunk(b'cHRM',CHROMA[:-1]+b'\x71')]
        bad += [g+c+pp._chunk(kind,b'x') for kind in (b'iCCP',b'cICP',b'acTL',b'eXIf')]
        for metadata in bad:
            data=fixture([[(1,2,3)]],2,8,metadata=metadata)
            self.assertFalse(pp.preview_capability(data)['available'])
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='overview')
        late=fixture([[(0,)]],3,1,palette=[(0,0,0)],metadata=b'')
        # Insert after PLTE, before IDAT.
        at=late.index(b'IDAT')-4
        late=late[:at]+g+c+late[at:]
        self.assertFalse(pp.preview_capability(late)['available'])

    def test_rgb8_keys_and_full_stream_faults_outside_crop(self):
        for interlace in (0,1):
            data=fixture([[(64,128,192),(65,128,192)]],2,8,interlace,key=(64,128,192))
            self.assertEqual(read(pp.preview_png(data,mode='overview'))[0],[(137,188,225,0),(138,188,225,255)])
        good=fixture([[(1,2,3)],[(4,5,6)]],2,8)
        offset=good.index(b'IDAT')-4
        count=int.from_bytes(good[offset:offset+4],'big')
        compressed=good[offset+8:offset+8+count]
        raw=zlib.decompress(compressed)
        rect={'x':0,'y':0,'width':1,'height':1}
        corruptions=(zlib.compress(raw[:4]+b'\x05'+raw[5:]),
                     zlib.compress(raw+b'x'),zlib.compress(raw[:-1]),
                     compressed[:-1],compressed+zlib.compress(b'x'))
        for payload in corruptions:
            data=good[:offset]+pp._chunk(b'IDAT',payload)+pp._chunk(b'IEND',b'')
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='crop',rect=rect)
        damaged=bytearray(good);damaged[offset+8]^=1
        with self.assertRaises(pp.PngPreviewError):pp.preview_png(bytes(damaged),mode='crop',rect=rect)
        # Gamma after IDAT cannot make an otherwise unspecified source valid.
        late=good[:offset]+good[offset:offset+count+12]+pp._chunk(b'gAMA',struct.pack('>I',100000))+pp._chunk(b'IEND',b'')
        with self.assertRaises(pp.PngPreviewError):pp.preview_png(late,mode='overview')

    def test_two_row_carriers_and_selected_row_conversion(self):
        rows=[[(64,128,192)]*7 for _ in range(9)]
        data=fixture(rows,2,8,kind=4)
        original_open=pp.Image.open
        heights=[]
        def opened(source):
            image=original_open(source);heights.append(image.height);return image
        original_expand=pp._expand_color
        with patch.object(pp.Image,'open',side_effect=opened), patch.object(pp,'_expand_color',wraps=original_expand) as expanded:
            pp.preview_png(data,mode='crop',rect={'x':2,'y':4,'width':2,'height':1})
        self.assertEqual(heights,[2]*9)
        self.assertEqual(expanded.call_count,1)

    def test_table_budget_and_cooperative_deadline(self):
        data=fixture([[(128,32768,65535)]],2,16)
        result=pp.preview_png(data,mode='overview')
        budget=result['bufferAccounting']['pixelPipelineAccountedUpperBoundBytes']
        with patch.object(pp,'PIXEL_PIPELINE_BUDGET',budget-1):
            with self.assertRaises(pp.PngPreviewError):pp.preview_png(data,mode='overview')
        with patch.object(pp,'PIXEL_PIPELINE_BUDGET',budget):pp.preview_png(data,mode='overview')
        with self.assertRaises(TimeoutError):pp._gamma_table(16,100000,time.monotonic()-1)
        # Expiry during LUT construction, not only at entry.
        with patch.object(pp,'_deadline',side_effect=[None,None,TimeoutError('expired')]):
            with self.assertRaises(TimeoutError):pp._gamma_table(16,100000,time.monotonic()+45)
        encoding=pp._Encoding(8,2,None,None,gamma=100000)
        with patch.object(pp,'_deadline',side_effect=TimeoutError('expired')):
            with self.assertRaises(TimeoutError):pp._expand_color(b'\x40\x80\xc0',None,1,encoding,bytearray(range(256)),time.monotonic()+45)
        # Capability is parser-only, not LUT execution.
        with patch.object(pp,'_gamma_table',side_effect=AssertionError('No table for capability')):
            self.assertTrue(pp.preview_capability(data)['available'])

if __name__=='__main__':unittest.main()
