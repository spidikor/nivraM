"""System Arial outlines must use measured glyphs and preserve document geometry."""
import copy
import importlib
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from nivram import scene
from nivram.chemistry import import_structure


class LabelOutlineTests(unittest.TestCase):
    def test_actual_labels_are_outlines_without_browser_font_dependency(self):
        for smiles, count in [('C=O', 1), ('[15NH3+]CC[18O-]', 10), ('ClCCBr', 4), ('N[C@H](C)O', 5)]:
            with self.subTest(smiles=smiles):
                graph = import_structure(smiles); original = copy.deepcopy(graph)
                result = scene.render_scene(graph)
                xml = ET.fromstring(result['svg'])
                labels = [e for e in xml if re.fullmatch(r'atom-\d+', e.attrib.get('class', ''))]
                self.assertEqual(len(labels), count)
                self.assertTrue(all(e.tag.endswith('}path') and e.attrib.get('d') for e in labels))
                self.assertNotIn('<text', result['svg'])
                self.assertNotIn('font-family', result['svg'])
                self.assertNotIn('@font-face', result['svg'])
                self.assertEqual(graph, original)
                transform = result['transform']
                self.assertAlmostEqual(transform['xScale'], 65)
                self.assertAlmostEqual(transform['yScale'], -65)
                for atom, position in zip(graph['atoms'], result['positions'], strict=True):
                    self.assertEqual(atom['id'], position['id'])
                    self.assertAlmostEqual(position['x'], transform['x0'] + atom['x'] * transform['xScale'])
                    self.assertAlmostEqual(position['y'], transform['y0'] + atom['y'] * transform['yScale'])

    def test_font_lookup_ignores_environment_and_current_directory(self):
        fonts = importlib.import_module('nivram.drawing_font')
        actual = fonts.system_arial()
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {'WINDIR': directory, 'SystemRoot': directory}):
            previous = Path.cwd()
            try:
                os.chdir(directory)
                self.assertEqual(fonts.system_arial(), actual)
            finally:
                os.chdir(previous)
        if fonts.sys.platform == 'win32':
            self.assertEqual(Path(actual).name.lower(), 'arial.ttf')
        self.assertTrue(Path(actual).is_absolute())

    def test_unavailable_corrupt_or_wrong_family_font_rejects(self):
        fonts = importlib.import_module('nivram.drawing_font')
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(fonts.sys, 'platform', 'win32'), mock.patch.object(fonts, '_windows_directory', return_value=Path(directory)):
            target = Path(directory)/'Fonts'/'arial.ttf'
            target.parent.mkdir()
            with self.assertRaisesRegex(ValueError, 'Arial'):
                fonts.system_arial()
            target.write_bytes(b'This is not a font.')
            with self.assertRaisesRegex(ValueError, 'Arial'):
                fonts.system_arial()
            wrong = mock.Mock(); wrong.getname.return_value = ('Telex', 'Regular')
            with mock.patch.object(fonts.ImageFont, 'truetype', return_value=wrong), self.assertRaisesRegex(ValueError, 'Arial'):
                fonts.system_arial()

    def test_font_unavailable_cannot_fall_back_to_another_typeface(self):
        fonts = importlib.import_module('nivram.drawing_font')
        with mock.patch.object(fonts.sys, 'platform', 'win32'), mock.patch.object(fonts, '_windows_directory', side_effect=ValueError('Windows Arial font is unavailable.')):
            with self.assertRaisesRegex(ValueError, 'Arial'):
                scene.render_scene(import_structure('CO'))

    def test_native_text_fallback_is_rejected(self):
        real_drawer = scene.rdMolDraw2D.MolDraw2DSVG
        def without_freetype(*args, **kwargs):
            kwargs['noFreetype'] = True
            return real_drawer(*args, **kwargs)
        with mock.patch.object(scene.rdMolDraw2D, 'MolDraw2DSVG', side_effect=without_freetype):
            with self.assertRaisesRegex(ValueError, 'FreeType|outlines'):
                scene.render_scene(import_structure('CO'))

    def test_monochrome_outlines_and_empty_scene(self):
        result = scene.render_scene(import_structure('[15NH3+]CC[18O-]'), {'color': False})
        xml = ET.fromstring(result['svg'])
        labels = [e for e in xml if re.fullmatch(r'atom-\d+', e.attrib.get('class', ''))]
        self.assertTrue(labels)
        self.assertTrue(all(e.tag.endswith('}path') and e.attrib.get('fill') == '#000000' for e in labels))
        fonts = importlib.import_module('nivram.drawing_font')
        with mock.patch.object(fonts, '_windows_directory', side_effect=ValueError('Unavailable')):
            self.assertEqual(scene.render_scene({'atoms': [], 'bonds': []})['positions'], [])


if __name__ == '__main__':
    unittest.main()
