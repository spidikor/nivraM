import unittest,copy,xml.etree.ElementTree as ET
from nivram.chemistry import import_structure
from nivram.scene import render_scene
class SceneTests(unittest.TestCase):
    def test_vector_and_atom_positions(self):
        g=import_structure('CCO');before=copy.deepcopy(g);s=render_scene(g)
        root=ET.fromstring(s['svg'])
        self.assertTrue(root.tag.endswith('svg'));self.assertEqual(len(s['positions']),3)
        self.assertEqual(g,before);self.assertIn('xScale',s['transform'])
        self.assertNotIn('<image',s['svg']);self.assertNotIn('@font-face',s['svg'])
        self.assertNotIn('<text',s['svg'])
        self.assertTrue(any(e.tag.endswith('path') and e.attrib.get('class')=='atom-2' for e in root))
    def test_element_palette(self):
        s=render_scene(import_structure('NCCO'))
        self.assertIn('#333399',s['svg']);self.assertIn('#FF0000',s['svg'])
    def test_reference_informed_defaults(self):
        s=render_scene(import_structure('CCO'))
        self.assertEqual(s['style']['bondWidth'],4.2)
        self.assertEqual(s['style']['labelSize'],36)
        self.assertIn('stroke-linecap:round',s['svg'])
        self.assertIn('#009900',render_scene(import_structure('CCCl'))['svg'])
    def test_fractional_label_size_rejected(self):
        with self.assertRaises(ValueError):render_scene(import_structure('CCO'),{'labelSize':22.5})
    def test_empty_scene(self):
        s=render_scene({'atoms':[],'bonds':[]});self.assertEqual(s['positions'],[])
    def test_reject_unreasonable_style(self):
        with self.assertRaises(ValueError):render_scene(import_structure('CCO'),{'bondWidth':float('nan')})
if __name__=='__main__':unittest.main()
