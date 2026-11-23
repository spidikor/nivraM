"""Compare rendered skeletal and attached-H labels with real outlined glyphs."""
import copy
import math
import re
import unittest
import xml.etree.ElementTree as ET

from rdkit import Chem
from rdkit.Chem.Draw import rdMolDraw2D

from nivram.chemistry import import_structure
from nivram.scene import render_scene, system_arial


def atom_art(svg, index):
    return [node for node in ET.fromstring(svg).iter()
            if node.get('class') == f'atom-{index}']


def outline(node):
    path = node.attrib['d']
    values = tuple(map(float, re.findall(r'-?\d+(?:\.\d+)?', path)))
    points = tuple(zip(values[::2], values[1::2], strict=True))
    return re.findall(r'[MLQCAZ]', path), points


def geometry(node):
    commands, points = outline(node)
    xs, ys = zip(*points, strict=True)
    left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
    scale = max(right - left, bottom - top)
    return commands, tuple(((x - left) / scale, (y - top) / scale) for x, y in points)


def center(node):
    _, points = outline(node)
    xs, ys = zip(*points, strict=True)
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


class SkeletalCarbonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Draw individual reference glyphs without nivraM's carbon-label code.
        cls.reference = {}
        molecule = Chem.MolFromSmiles('C')
        for character in 'CH123478:+-':
            drawer = rdMolDraw2D.MolDraw2DSVG(300, 220, noFreetype=False)
            options = drawer.drawOptions()
            options.fontFile = system_arial()
            options.fixedFontSize = 36
            options.atomLabels[0] = character
            drawing = rdMolDraw2D.PrepareMolForDrawing(molecule)
            drawer.DrawMolecule(drawing)
            drawer.FinishDrawing()
            [glyph] = atom_art(drawer.GetDrawingText(), 0)
            cls.reference[character] = geometry(glyph)

    def assert_geometry(self, actual, expected):
        actual_commands, actual_points = actual
        expected_commands, expected_points = expected
        self.assertEqual(actual_commands, expected_commands)
        self.assertEqual(len(actual_points), len(expected_points))
        # RDKit serializes outline coordinates to tenths of a pixel.
        for point, reference in zip(actual_points, expected_points, strict=True):
            self.assertLessEqual(math.dist(point, reference), .045)

    def assert_glyphs(self, artwork, text):
        self.assertEqual(len(artwork), len(text))
        for glyph, character in zip(artwork, text, strict=True):
            self.assert_geometry(geometry(glyph), self.reference[character])

    def modes(self, graph):
        original = copy.deepcopy(graph)
        skeletal = render_scene(graph, {'skeletalCarbons': True})
        explicit = render_scene(graph, {'skeletalCarbons': False})
        self.assertEqual(render_scene(graph)['svg'], skeletal['svg'])
        self.assertEqual(original, graph)
        for key in ('formula', 'smiles', 'mass'):
            self.assertEqual(skeletal[key], explicit[key])
        for result in (skeletal, explicit):
            transform = result['transform']
            for atom, position in zip(graph['atoms'], result['positions'], strict=True):
                self.assertEqual(atom['id'], position['id'])
                for coordinate in ('x', 'y'):
                    origin, scale = transform[f'{coordinate}0'], transform[f'{coordinate}Scale']
                    self.assertAlmostEqual(position[coordinate], origin + atom[coordinate] * scale)
        return skeletal['svg'], explicit['svg']

    def test_ordinary_bonded_carbons_and_near_straight_junction(self):
        for smiles, labels in [('CCO', {0: 'CH3', 1: 'CH2'}),
                               ('C[C@H](O)F', {1: 'CH'})]:
            with self.subTest(smiles=smiles):
                skeletal, explicit = self.modes(import_structure(smiles))
                for index, label in labels.items():
                    self.assertEqual(atom_art(skeletal, index), [])
                    artwork = atom_art(explicit, index)
                    self.assert_glyphs(artwork, label)
                    if len(label) == 3:
                        self.assertGreater(center(artwork[2])[1], center(artwork[1])[1])

        graph = import_structure('CCC')
        for atom, (x, y) in zip(graph['atoms'], [(-1.5, 0), (0, 0), (1.49, .13)], strict=True):
            atom.update(x=x, y=y)
        skeletal, explicit = self.modes(graph)
        self.assert_glyphs(atom_art(skeletal, 1), 'C')
        self.assert_glyphs(atom_art(explicit, 1), 'CH2')
        self.assertEqual(atom_art(skeletal, 0), [])
        self.assertEqual(atom_art(skeletal, 2), [])

    def test_matched_isotope_charge_and_atom_map_outlines_in_both_modes(self):
        cases = (
            ('CCC', 'CH2'),
            ('C[12CH2]C', '12CH2'),
            ('C[13CH2]C', '13CH2'),
            ('C[CH-]C', 'CH-'),
            ('C[CH+]C', 'CH+'),
            ('C[CH2:37]C', 'CH2:37'),
            ('C[CH2:38]C', 'CH2:38'),
            ('C[13CH-:37]C', '13CH-:37'),
        )
        for smiles, label in cases:
            for mode, svg in enumerate(self.modes(import_structure(smiles))):
                with self.subTest(smiles=smiles, skeletal=mode == 0):
                    artwork = atom_art(svg, 1)
                    expected = '' if smiles == 'CCC' and mode == 0 else label
                    self.assert_glyphs(artwork, expected)
                    if label.startswith('1'):
                        first, second, carbon = map(center, artwork[:3])
                        self.assertLess(first[0], second[0])
                        self.assertLess(max(first[1], second[1]), carbon[1])
                    elif label[-1] in '+-':
                        self.assertLess(center(artwork[-1])[1], center(artwork[0])[1])
                    elif ':' in label:
                        colon, first, second = (center(node)[0] for node in artwork[-3:])
                        self.assertLess(colon, first)
                        self.assertLess(first, second)

    def test_radical_dots_against_closed_shell_and_each_other(self):
        for smiles, glyphs, count in [('CCC', 'CH2', 0), ('C[CH]C', 'CH', 1),
                                      ('C[C]C', '', 2), ('C[13CH:37]C', '13CH:37', 1)]:
            for mode, svg in enumerate(self.modes(import_structure(smiles))):
                with self.subTest(smiles=smiles, skeletal=mode == 0):
                    artwork = atom_art(svg, 1)
                    expected = '' if smiles == 'CCC' and mode == 0 else glyphs
                    self.assert_glyphs(artwork[:len(expected)], expected)
                    dots = artwork[len(expected):]
                    self.assertEqual(len(dots), count)
                    for dot in dots:
                        commands, points = outline(dot)
                        self.assertTrue(set(commands) <= {'M', 'L', 'Z'})
                        self.assertGreater(len(points), 20)
                        x, y = center(dot)
                        radii = [math.hypot(px - x, py - y) for px, py in points]
                        # The drawer may end a circle path with a segment to its center.
                        if radii[-1] <= .15:
                            radii.pop()
                        self.assertGreater(min(radii), 1)
                        self.assertLess(max(radii), 4)
                        self.assertLess(max(radii) - min(radii), .25)
                        self.assertIn('fill:#000000', dot.attrib['style'])
                    if count == 2:
                        self.assertGreater(math.dist(*map(center, dots)), 4)

    def test_isolated_carbon_heteroatom_and_explicit_hydrogen_keep_their_outlines(self):
        for smiles, indices in [('C', (0,)), ('[2H]OC', (0, 1))]:
            with self.subTest(smiles=smiles):
                skeletal, explicit = self.modes(import_structure(smiles))
                for index in indices:
                    left, right = atom_art(skeletal, index), atom_art(explicit, index)
                    self.assertTrue(left)
                    self.assertEqual(len(left), len(right))
                    for a, b in zip(left, right, strict=True):
                        self.assert_geometry(geometry(a), geometry(b))
                if smiles == 'C':
                    self.assert_glyphs(atom_art(skeletal, 0), 'CH4')
