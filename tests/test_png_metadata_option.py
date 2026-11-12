"""Independent PNG decode, strict batch flag and publication-boundary checks."""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from PIL import Image
from nivram.batch_export import BatchExportManager, _decode_png
from nivram.import_protocol import checked_batch_settings
from nivram.image_import import inspect_png_metadata, select_metadata_candidates
from nivram.png_metadata import validate_3d_coordinates
import test_batch_export as fixtures
from test_png_metadata import texts

ROOT = Path(__file__).resolve().parents[1]

class PngMetadataOptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='nivram-metadata-fixtures-')
        cls.output = Path(cls.directory.name)
        result = subprocess.run(['node', '--test', 'tests/png-metadata-option.test.mjs'], cwd=ROOT,
            env={**os.environ, 'NIVRAM_METADATA_EVIDENCE': str(cls.output)}, text=True, capture_output=True, timeout=60)
        if result.returncode:
            cls.directory.cleanup()
            raise AssertionError(result.stdout+result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_independent_decoder_pixels_and_default_exact_2d_recovery(self):
        for stem in ('2d', '3d-supplied', '3d-recovered'):
            with self.subTest(stem=stem):
                original=(self.output/f'{stem}-retained.png').read_bytes()
                removed=(self.output/f'{stem}-removed.png').read_bytes()
                with Image.open(io.BytesIO(original)) as a, Image.open(io.BytesIO(removed)) as b:
                    self.assertEqual(a.size,b.size)
                    self.assertEqual(a.mode,b.mode)
                    self.assertEqual(a.tobytes(),b.tobytes())
                    self.assertEqual(b.info,{'Software':'nivraM test'})
                    self.assertIn('nivraM Coordinates',a.info)
                    if stem.startswith('3d'):
                        record,_=validate_3d_coordinates(a.info['nivraM Coordinates'].encode())
                        self.assertIn('M  END\r\n',record['molText'])
                        self.assertFalse(record['recordedOrigin']=='generated_coordinates')
                clean=inspect_png_metadata(removed)
                self.assertEqual(clean['candidates'],[])
                if stem=='2d':
                    full=inspect_png_metadata(original)
                    graph=select_metadata_candidates(full,[full['candidates'][0]['id']])
                    self.assertEqual(graph['atoms'][0]['x'],1.2345678901234567)
                    self.assertEqual(graph['atoms'][0]['isotope'],13)
                    self.assertEqual(graph['atoms'][0]['mapNum'],2)
                    self.assertEqual(graph['atoms'][0]['charge'],1)

    def test_batch_settings_old_default_optional_strict_flag_and_no_profile_persistence(self):
        old=checked_batch_settings(fixtures.settings())
        self.assertNotIn('removeChemistryMetadata',old)
        for flag in (True,False):
            value={**fixtures.settings(),'removeChemistryMetadata':flag}
            checked=checked_batch_settings(value)
            value['removeChemistryMetadata']=not flag
            self.assertIs(checked['removeChemistryMetadata'],flag)
            self.assertNotIn('removeChemistryMetadata',checked['export'])
        for bad in (None,0,1,'true',{},[]):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                checked_batch_settings({**fixtures.settings(),'removeChemistryMetadata':bad})
        with self.assertRaises(ValueError):
            checked_batch_settings({**fixtures.settings(),'export':{**fixtures.settings()['export'],'removeChemistryMetadata':True}})

    def test_native_absence_gate_preserves_encoded_bytes(self):
        raw=fixtures.png()
        original=raw[:-12]+b''.join(fixtures.chunk(k,v) for k,v in texts())+raw[-12:]
        removed=raw[:-12]+fixtures.chunk(*texts()[0])+raw[-12:]
        options=fixtures.settings()['export']
        for flag in (False,True):
            data,_=_decode_png(base64.b64encode(removed).decode(),options,threading.Event(),remove_chemistry_metadata=flag)
            self.assertEqual(data,removed)
        self.assertEqual(_decode_png(base64.b64encode(original).decode(),options,threading.Event())[0],original)
        with self.assertRaisesRegex(ValueError,'still contains'):
            _decode_png(base64.b64encode(original).decode(),options,threading.Event(),remove_chemistry_metadata=True)
        for bad in (None,0,1,'true'):
            with self.assertRaises(ValueError):
                _decode_png(base64.b64encode(removed).decode(),options,threading.Event(),remove_chemistry_metadata=bad)

    def test_significant_bits_chunk_is_small_valid_and_preserved(self):
        raw=fixtures.png()
        for values in (bytes([8,8,8,8]),bytes([8,8,8]),bytes([8,8,8,0]),bytes([8,8,8,9]),bytes([8])*100):
            data=raw[:33]+fixtures.chunk(b'sBIT',values)+raw[33:]
            if values==bytes([8,8,8,8]):
                self.assertEqual(_decode_png(fixtures.encoded(data),fixtures.settings()['export'],threading.Event(),remove_chemistry_metadata=True)[0],data)
            else:
                with self.assertRaises(ValueError):
                    _decode_png(fixtures.encoded(data),fixtures.settings()['export'],threading.Event(),remove_chemistry_metadata=True)

    def test_batch_frozen_native_choice_rejects_metadata_before_publish_and_keeps_input(self):
        class StaticPreparation:
            def prepare_batch(self,*args,**kwargs):
                project=fixtures.project()
                return {'project':project,'graph':project['graph'],'scene':{}}
        for flag in (False,True):
            for contaminated in (False,True):
                with self.subTest(flag=flag,contaminated=contaminated),tempfile.TemporaryDirectory() as directory:
                    root=Path(directory);source=root/'original.nivram';original=json.dumps(fixtures.project()).encode();source.write_bytes(original)
                    destination=root/'outputs';destination.mkdir();settings={**fixtures.settings(),'removeChemistryMetadata':flag}
                    manager=BatchExportManager(preparation_service=StaticPreparation())
                    try:
                        batch=manager._begin_selected([source],destination,settings)
                        settings['removeChemistryMetadata']=not flag
                        item=batch['items'][0]['id'];self.assertEqual(manager.prepare(batch['sessionId'],item)['status'],'ready')
                        data=fixtures.png()
                        if contaminated:data=data[:-12]+b''.join(fixtures.chunk(k,v) for k,v in texts())+data[-12:]
                        result=manager.write_png(batch['sessionId'],item,fixtures.encoded(data))
                        if flag and contaminated:
                            self.assertEqual(result['status'],'failed');self.assertEqual(result['item']['error']['code'],'invalid_png');self.assertEqual(list(destination.glob('*.png')),[])
                        else:
                            self.assertEqual(result['status'],'exported');self.assertEqual((destination/result['item']['outputName']).read_bytes(),data)
                        manifest=json.loads((destination/batch['manifestName']).read_text())
                        self.assertIs(manifest['settings']['removeChemistryMetadata'],flag)
                        self.assertEqual(source.read_bytes(),original)
                    finally:manager.close()
