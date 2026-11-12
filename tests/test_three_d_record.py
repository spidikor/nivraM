"""Structural metadata and 2D routing: no recovery/generation trust inferred."""
import copy
import json
import unittest
from nivram import __version__
from nivram.png_metadata import validate_3d_coordinates, validate_export_text, KEYWORD
from nivram.image_import import inspect_png_metadata
from test_coordinate_recovery import make_record, sha
from test_conformer import request
from nivram.conformer import generate
from test_image_import import png

class ThreeDRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record=make_record(generate(request()))
    def test_untrusted_version_read_current_version_export(self):
        raw=json.dumps(self.record).encode()
        self.assertEqual(validate_3d_coordinates(raw)[0]['version'],'1.0.0')
        with self.assertRaises(ValueError):validate_export_text(b'iTXt',KEYWORD.encode()+b'\0\0\0\0\0'+raw)
        r={**self.record,'version':__version__}
        self.assertEqual(validate_export_text(b'iTXt',KEYWORD.encode()+b'\0\0\0\0\0'+json.dumps(r).encode()),(KEYWORD,__version__))
    def test_malformed_private_duplicate_and_integrity_rejection(self):
        for change in [{'private':'secret'},{'molBytes':True},{'dimension':True},{'molText':self.record['molText']+' '},{'sourceAtomIds':['a','a']},{'sourceContextText':'{}'}]:
            with self.subTest(change=list(change)),self.assertRaises(ValueError):validate_3d_coordinates(json.dumps({**self.record,**change}).encode())
        raw=json.dumps(self.record).replace('"dimension": 3','"dimension": 3, "dimension": 3')
        with self.assertRaises(ValueError):validate_3d_coordinates(raw.encode())
        r=copy.deepcopy(self.record);p=json.loads(r['provenanceJson']);p['private']='secret';r['provenanceJson']=json.dumps(p);r['provenanceSha256']=sha(r['provenanceJson'])
        with self.assertRaises(ValueError):validate_3d_coordinates(json.dumps(r).encode())
    def test_2d_importer_routes_without_adoption(self):
        data=png([(b'iTXt',KEYWORD.encode()+b'\0\0\0\0\0'+json.dumps(self.record).encode())])
        result=inspect_png_metadata(data)
        self.assertEqual(result['status'],'three_d_coordinates');self.assertEqual(result['candidates'],[])
        self.assertIn('Open this PNG in the 3D viewer',result['warnings'][0])
        self.assertEqual(result['recognition']['status'],'not_run')
