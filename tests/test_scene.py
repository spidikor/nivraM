import unittest,copy,xml.etree.ElementTree as ET
import re
from unittest.mock import patch
from nivram.chemistry import import_structure
from nivram.scene import render_scene
class SceneTests(unittest.TestCase):
    def test_single_neutral_glyph_anchors_follow_rotated_atom_coordinates(self):
        for smiles, atom_index in (('CC=O', 2), ('c1ncccc1', 1), ('COC', 1)):
            for rotate in (False, True):
                with self.subTest(smiles=smiles, rotate=rotate):
                    graph = import_structure(smiles)
                    if rotate:
                        for atom in graph['atoms']:
                            atom['x'], atom['y'] = -atom['y'], atom['x']
                    original = copy.deepcopy(graph)
                    with patch('nivram.scene.anchor_element_labels', side_effect=lambda svg, *_: svg):
                        unshifted = render_scene(graph)
                    scene = render_scene(graph)
                    self.assertEqual(graph, original)
                    self.assertEqual(unshifted['positions'], scene['positions'])
                    self.assertEqual(unshifted['smiles'], scene['smiles'])
                    label = next(p for p in ET.fromstring(scene['svg']).iter()
                                 if p.get('class') == f'atom-{atom_index}')
                    numbers = r'-?(?:\d+(?:\.\d*)?|\.\d+)'
                    values = list(map(float, re.findall(numbers, label.get('d'))))
                    xs, ys = values[::2], values[1::2]
                    dx, dy = map(float, re.findall(numbers, label.get('transform')))
                    point = scene['positions'][atom_index]
                    self.assertAlmostEqual((min(xs) + max(xs)) / 2 + dx, point['x'], places=3)
                    self.assertAlmostEqual((min(ys) + max(ys)) / 2 + dy,
                                           point['y'] + (max(ys) - min(ys)) / 8, places=3)
                    del label.attrib['transform']
                    before = ET.fromstring(unshifted['svg'])
                    expected = next(p for p in before.iter() if p.get('class') == f'atom-{atom_index}')
                    self.assertEqual(label.attrib, expected.attrib)
                    bond_paths = lambda svg: [p.attrib for p in ET.fromstring(svg).iter()
                                              if p.get('class', '').startswith('bond-')]
                    self.assertEqual(bond_paths(scene['svg']), bond_paths(unshifted['svg']))

    def test_annotated_and_hydrogen_labels_keep_their_engine_layout(self):
        for smiles in ('CO', 'C[NH3+]', 'CC=[18O]', 'C[O:7]C', 'C[O]', 'C#N'):
            with self.subTest(smiles=smiles):
                graph = import_structure(smiles)
                with patch('nivram.scene.anchor_element_labels', side_effect=lambda svg, *_: svg):
                    unshifted = render_scene(graph)
                self.assertEqual(render_scene(graph), unshifted)

    def test_explicit_perspective_marks_on_nonstereogenic_single_bonds(self):
        for direction in (1, 2):
            graph = import_structure('CCC')
            graph['bonds'][0]['direction'] = direction
            before = copy.deepcopy(graph)
            scene = render_scene(graph)
            paths = [p for p in ET.fromstring(scene['svg']).iter()
                     if 'bond-0' in p.attrib.get('class', '').split()]
            if direction == 1:
                self.assertTrue(any(p.get('d', '').rstrip().endswith('Z') for p in paths))
            else:
                self.assertGreater(len(paths), 2)
            self.assertEqual(scene['smiles'], 'CCC')
            self.assertEqual(graph, before)

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
