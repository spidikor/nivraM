"""Non-GUI byte-level background validation; no chemistry runtime required."""
import base64
from io import BytesIO
import struct
import threading
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
import zlib
from PIL import Image
from nivram.batch_export import _decode_png
from nivram.import_protocol import checked_export, checked_batch_settings
from nivram.population_export import validate_payload as population_payload
from nivram.reference_export import validate_payload as reference_payload


def chunk(kind, body):
    return struct.pack('>I', len(body))+kind+body+struct.pack('>I', zlib.crc32(body,zlib.crc32(kind)))


def png(width=32,height=16,*,transparent=False,kind=0,bad_border=False,color=6):
    channels=4 if color==6 else 3
    raster=bytearray((b'\x27\x55\xa7\x00' if transparent else b'\xff'*4)[:channels]*(width*height))
    for y in range(3,height-3):
        for x in range(3,width-3):
            offset=(y*width+x)*channels
            raster[offset:offset+channels]=bytes([20,30,40,127 if transparent else 255])[:channels]
    if bad_border:raster[3]=1
    rows=[];previous=bytes(width*channels)
    for y in range(height):
        row=bytes(raster[y*width*channels:(y+1)*width*channels]);filtered=[]
        for i,value in enumerate(row):
            left=row[i-channels] if i>=channels else 0;up=previous[i];corner=previous[i-channels] if i>=channels else 0
            if kind==4:
                p=left+up-corner;predictor=min((left,up,corner),key=lambda a:abs(p-a))
            else:predictor=[0,left,up,(left+up)//2][kind]
            filtered.append((value-predictor)&255)
        rows.append(bytes([kind])+bytes(filtered));previous=row
    compressed=zlib.compress(b''.join(rows))
    result=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,color,0,0,0))
    result+=b''.join(chunk(b'IDAT',compressed[i:i+7]) for i in range(0,len(compressed),7))+chunk(b'IEND',b'')
    with Image.open(BytesIO(result)) as image:
        assert image.tobytes()==bytes(raster)
    return result


def encoded(data):return base64.b64encode(data).decode('ascii')

def decode(data,**kw):return _decode_png(encoded(data),{'width':32,'marginMode':'pixels','margin':2,**kw},threading.Event())


class TransparencyTests(unittest.TestCase):
    def test_each_filter_white_default_and_transparent_margins(self):
        for kind in range(5):
            with self.subTest(kind=kind):
                white=png(kind=kind);self.assertEqual(decode(white)[0],white)
                alpha=png(transparent=True,kind=kind)
                self.assertEqual(decode(alpha,transparentBackground=True)[0],alpha)
                with self.assertRaisesRegex(ValueError,'transparent pixels'):decode(alpha)
                with self.assertRaisesRegex(ValueError,'transparent'):decode(white,transparentBackground=True)
                with self.assertRaisesRegex(ValueError,'margins'):decode(png(transparent=True,kind=kind,bad_border=True),transparentBackground=True)
        # True requests RGBA, even when the margin is zero.
        with self.assertRaises(ValueError):decode(png(color=2),transparentBackground=True,margin=0)
        self.assertEqual(decode(png(color=2))[1]['width'],32)

    def test_strict_native_settings_and_legacy_settings(self):
        settings={'width':32,'marginMode':'pixels','margin':2}
        self.assertEqual(checked_export(settings),settings)
        self.assertEqual(checked_batch_settings({'export':{**settings,'transparentBackground':True}})['export']['transparentBackground'],True)
        for bad in [None,0,1,'true',[],{}]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):checked_export({**settings,'transparentBackground':bad})
                with self.assertRaises(ValueError):checked_batch_settings({'export':{**settings,'transparentBackground':bad}})
                with self.assertRaises(ValueError):decode(png(),transparentBackground=bad)

    def test_both_native_chart_validators_capture_explicit_choice(self):
        for validate,height in [(reference_payload,560),(population_payload,720)]:
            alpha=png(1000,height,transparent=True);white=png(1000,height)
            self.assertEqual(validate('png',encoded(alpha),True),alpha)
            self.assertEqual(validate('png',encoded(white)),white)
            with self.assertRaises(ValueError):validate('png',encoded(alpha))
            for bad in ['true',1,None]:
                with self.assertRaises(ValueError):validate('png',encoded(alpha),bad)
            with self.assertRaises(ValueError):validate('json','{}',True)

    def test_native_saver_routes_alpha_only_with_explicit_boolean(self):
        from nivram.reference_export import ReferenceExportSaver
        from nivram.population_export import PopulationExportSaver
        for Saver,height in [(ReferenceExportSaver,560),(PopulationExportSaver,720)]:
            payload=encoded(png(1000,height,transparent=True))
            saver=Saver();window=SimpleNamespace(create_file_dialog=Mock(return_value=None))
            with patch.dict('sys.modules', {'webview':SimpleNamespace(FileDialog=SimpleNamespace(SAVE='save'))}):
                self.assertEqual(saver.save(window,'png',payload,'token','token',True),{'status':'cancelled'})
                self.assertEqual(window.create_file_dialog.call_count,1)
                for flag in [False,1,'true',None]:
                    self.assertEqual(saver.save(window,'png',payload,'token','token',flag)['status'],'failed')
                self.assertEqual(window.create_file_dialog.call_count,1)
            saver.close()

    def test_corruption_and_cancellation_still_reject(self):
        alpha=bytearray(png(transparent=True));alpha[-6]^=1
        with self.assertRaisesRegex(ValueError,'checksum'):decode(bytes(alpha),transparentBackground=True)
        stopped=threading.Event();stopped.set()
        with self.assertRaises(InterruptedError):_decode_png(encoded(png(transparent=True)),{'width':32,'margin':2,'marginMode':'pixels','transparentBackground':True},stopped)

if __name__=='__main__':unittest.main()
