"""Optional hash appearance: actual renderer invariants and strict shape boundary.

Fixtures are synthetic chemistry and vector shapes, never reference images.
These checks do not establish colored-hash calibration or drawing-corpus parity.
"""
import copy
import json
import math
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from rdkit import Chem
from rdkit.Chem.Draw import rdMolDraw2D

from nivram import import_preparation as preparation
from nivram import import_protocol as protocol
from nivram.batch_export import BatchExportManager
from nivram.chemistry import graph_to_mol, import_structure
from nivram.hash_style import HashStyleError, spaced_hashes
from nivram.profiles import ProfileStore
from nivram.scene import checked_style, render_scene
from tests.test_import_preparation import FakeSupervisor


LEGACY_STYLE = {'bondWidth': 4.2, 'labelSize': 36, 'bondSpacing': .14,
                'labelPadding': .04, 'color': True}
EXPORT = {'width': 640, 'marginMode': 'percent', 'margin': 2}
SVG_NS = 'http://www.w3.org/2000/svg'
NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
LINE = re.compile(rf'^M\s*({NUMBER})[ ,]+({NUMBER})\s*L\s*({NUMBER})[ ,]+({NUMBER})\s*$')
PATH = re.compile(r'<path\b[^>]*\bclass=[\'"]([^\'"]+)[\'"][^>]*?/\s*>', re.S)


def drawing_mol(graph):
    return rdMolDraw2D.PrepareMolForDrawing(Chem.Mol(graph_to_mol(graph)),
                                           kekulize=True, addChiralHs=False,
                                           wedgeBonds=True, forceCoords=False)


def hash_classes(mol):
    return {f'bond-{bond.GetIdx()} atom-{bond.GetBeginAtomIdx()} atom-{bond.GetEndAtomIdx()}'
            for bond in mol.GetBonds() if bond.GetBondDir() == Chem.BondDir.BEGINDASH}


def styles(path):
    declarations = path.attrib['style'].split(';')
    return dict(item.split(':', 1) for item in declarations if item)


def bars(svg, cls):
    return [element for element in ET.fromstring(svg)
            if element.tag == f'{{{SVG_NS}}}path' and element.attrib.get('class') == cls]


def endpoints(path):
    match = LINE.fullmatch(path.attrib['d'])
    if not match:
        raise AssertionError('Expected a finite two-point hash path: ' + path.attrib['d'])
    values = tuple(map(float, match.groups()))
    if not all(math.isfinite(value) for value in values):
        raise AssertionError('Nonfinite hash coordinates')
    return values[:2], values[2:]


def center(path):
    a, b = endpoints(path)
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def without_hashes(svg, classes):
    # Collapse each whole hash run; inter-bar whitespace necessarily changes
    # when the count changes. All bytes outside the first/last tags must match.
    spans = []
    for cls in classes:
        matches = [match for match in PATH.finditer(svg) if match.group(1) == cls]
        if not matches:
            raise AssertionError('Missing semantic hash group: ' + cls)
        spans.append((matches[0].start(), matches[-1].end(), f'[{cls}]'))
    for begin, end, marker in sorted(spans, reverse=True):
        svg = svg[:begin] + marker + svg[end:]
    return svg


def synthetic_mol():
    mol = Chem.MolFromSmiles('CC')
    mol.GetBondWithIdx(0).SetBondDir(Chem.BondDir.BEGINDASH)
    return mol


def synthetic_bar(index, *, cls='bond-0 atom-0 atom-1', d=None, extra=''):
    x, half = index * 10, index + 1
    d = d or f'M {x},{-half} L {x},{half}'
    color = '#000000' if index < 4 else '#333399'
    return (f'<path class="{cls}" d="{d}" '
            f'style="fill:none;fill-rule:evenodd;stroke:{color};stroke-width:2px;'
            f'stroke-linecap:round;stroke-linejoin:round;stroke-opacity:1{extra}"/>')


def synthetic_svg(paths=None):
    return f'<svg xmlns="{SVG_NS}">' + ''.join(paths or [synthetic_bar(i) for i in range(7)]) + '</svg>'


class _HashAssertions:
    def assert_regular_hash(self, original, adjusted, width):
        self.assertGreaterEqual(len(original), 3)
        self.assertGreaterEqual(len(adjusted), 3)
        self.assertEqual(adjusted[0].attrib['d'], original[0].attrib['d'])
        self.assertEqual(adjusted[-1].attrib['d'], original[-1].attrib['d'])
        start, end = center(original[0]), center(original[-1])
        span = distance(start, end)
        self.assertGreater(span, 0)
        axis = ((end[0]-start[0])/span, (end[1]-start[1])/span)
        step = span/(len(adjusted)-1)
        self.assertGreater(step, width+.2, 'Round-cap strokes need a positive axial gap.')
        error = abs(step - 3.4*width)
        for count in (len(adjusted)-1, len(adjusted)+1):
            if count >= 3 and span/(count-1) > width+.2:
                self.assertLessEqual(error, abs(span/(count-1)-3.4*width)+.01)
        lengths, colors, old_centers = [], [], [center(path) for path in original]
        for index, path in enumerate(adjusted):
            a, b = endpoints(path)
            midpoint = center(path)
            expected = (start[0]+axis[0]*step*index, start[1]+axis[1]*step*index)
            self.assertLessEqual(distance(midpoint, expected), .15)
            self.assertLessEqual(abs((b[0]-a[0])*axis[0]+(b[1]-a[1])*axis[1]), .2)
            length = distance(a, b)
            self.assertGreater(length, 0)
            lengths.append(length)
            paint = styles(path)
            self.assertEqual(paint['fill'], 'none')
            self.assertEqual(float(paint['stroke-width'].removesuffix('px')), width)
            self.assertEqual(paint['stroke-linecap'], 'round')
            self.assertNotIn('url(', path.attrib['style'])
            colors.append(paint['stroke'])
            projected = lambda point: (point[0]-start[0])*axis[0]+(point[1]-start[1])*axis[1]
            closest = min(range(len(original)), key=lambda i: (abs(projected(midpoint)-projected(old_centers[i])), i))
            self.assertEqual(paint['stroke'], styles(original[closest])['stroke'])
        self.assertTrue(all(b > a for a, b in zip(lengths, lengths[1:])), lengths)
        runs = [value for i, value in enumerate(colors) if not i or value != colors[i-1]]
        self.assertEqual(len(runs), len(set(runs)), 'Colors must not oscillate along the taper.')
        self.assertEqual(colors[0], styles(original[0])['stroke'])
        self.assertEqual(colors[-1], styles(original[-1])['stroke'])

class HashStyleTests(_HashAssertions, unittest.TestCase):
    def test_legacy_and_explicit_engine_styles_preserve_exact_renderer_svg(self):
        before = copy.deepcopy(LEGACY_STYLE)
        for smiles in ('CO', 'N[C@H](C)O', 'N[C@@H](O)S'):
            with self.subTest(smiles=smiles):
                graph = import_structure(smiles)
                implicit = render_scene(graph, LEGACY_STYLE)
                explicit = render_scene(graph, {**LEGACY_STYLE, 'hashBondStyle': 'engine'})
                self.assertEqual(implicit['style']['hashBondStyle'], 'engine')
                for key in ('svg', 'positions', 'transform', 'width', 'height'):
                    self.assertEqual(implicit[key], explicit[key])
        self.assertEqual(LEGACY_STYLE, before)
        self.assertEqual(checked_style(None)['hashBondStyle'], 'engine')
        self.assertEqual(protocol.checked_style(None)['hashBondStyle'], 'engine')

    def test_style_enum_is_exact_and_consistent_across_worker_boundary(self):
        for value in ('engine', 'spaced'):
            native = checked_style({'hashBondStyle': value})
            self.assertEqual(native, protocol.checked_style({'hashBondStyle': value}))
            self.assertEqual(native['hashBondStyle'], value)
        for value in (True, False, None, '', 'Spaced', 'spaced ', 'engine ', 0, 1, [], {}, float('nan')):
            for validator in (checked_style, protocol.checked_style):
                with self.subTest(value=value, validator=validator.__module__), self.assertRaises(ValueError):
                    validator({'hashBondStyle': value})
        for validator in (checked_style, protocol.checked_style):
            with self.subTest(validator=validator.__module__), self.assertRaises(ValueError):
                validator({'hashBondStyle': 'spaced', 'unknownStyle': 'engine'})

    def test_actual_black_and_two_color_hashes_keep_chemistry_endpoints_and_nonhash_bytes(self):
        for smiles in ('N[C@H](C)O', 'N[C@@H](O)S'):
            for width in (.5, .99, 1., 1.01, 2.4, 4.2, 5):
                with self.subTest(smiles=smiles, width=width):
                    graph = import_structure(smiles); before = copy.deepcopy(graph)
                    classes = hash_classes(drawing_mol(graph))
                    self.assertEqual(len(classes), 1, 'Fixture must contain a semantic BEGINDASH bond.')
                    engine = render_scene(graph, {'bondWidth': width, 'hashBondStyle': 'engine'})
                    spaced = render_scene(graph, {'bondWidth': width, 'hashBondStyle': 'spaced'})
                    self.assertEqual(graph, before)
                    for key in ('positions', 'transform', 'width', 'height', 'formula', 'mass', 'smiles'):
                        self.assertEqual(engine[key], spaced[key], key)
                    self.assertEqual(set(engine['properties']), set(spaced['properties']))
                    self.assertEqual({key: value for key, value in engine['properties'].items() if key != 'calculatedAt'},
                                     {key: value for key, value in spaced['properties'].items() if key != 'calculatedAt'})
                    self.assertEqual(without_hashes(engine['svg'], classes), without_hashes(spaced['svg'], classes))
                    self.assertNotIn('<linearGradient', spaced['svg'])
                    for cls in classes:
                        original, adjusted = bars(engine['svg'], cls), bars(spaced['svg'], cls)
                        if width in (.99, 1., 1.01):
                            expected_stroke = '1.0px' if width < 1 else '0.5px'
                            self.assertEqual({styles(path)['stroke-width'] for path in original}, {expected_stroke})
                        self.assert_regular_hash(original, adjusted, width)
                        self.assertTrue(all(path.attrib['class'] == cls for path in adjusted))
                        if smiles == 'N[C@@H](O)S':
                            self.assertEqual({styles(path)['stroke'] for path in adjusted}, {'#000000', '#333399'})

    def test_hash_direction_survives_rotated_and_reflected_coordinate_frames(self):
        graph = import_structure('N[C@@H](O)S')
        for angle, reflection in ((.7, 1), (-1.2, 1), (.3, -1)):
            changed = copy.deepcopy(graph)
            for atom in changed['atoms']:
                x, y = atom['x'], atom['y']
                atom['x'] = reflection*(x*math.cos(angle)-y*math.sin(angle))
                atom['y'] = x*math.sin(angle)+y*math.cos(angle)
            before = copy.deepcopy(changed)
            engine = render_scene(changed, {'hashBondStyle': 'engine'})
            spaced = render_scene(changed, {'hashBondStyle': 'spaced'})
            self.assertEqual(changed, before)
            self.assertEqual(engine['positions'], spaced['positions'])
            self.assertEqual(engine['smiles'], spaced['smiles'])
            classes = hash_classes(drawing_mol(changed))
            if classes:
                for cls in classes:
                    self.assert_regular_hash(bars(engine['svg'], cls), bars(spaced['svg'], cls), 4.2)
            else:
                # Reflecting coordinates can change the prepared depiction to a
                # filled wedge while the graph's stereochemistry stays intact.
                self.assertEqual(engine['svg'], spaced['svg'])

    def test_filled_wedges_and_ordinary_bonds_are_byte_identical_in_both_modes(self):
        for smiles in ('CO', 'C=O', 'C#N', 'N[C@@H](C)O', 'N[C@H](O)S'):
            with self.subTest(smiles=smiles):
                graph = import_structure(smiles)
                self.assertFalse(hash_classes(drawing_mol(graph)))
                engine = render_scene(graph, {'hashBondStyle': 'engine'})
                spaced = render_scene(graph, {'hashBondStyle': 'spaced'})
                self.assertEqual(engine['svg'], spaced['svg'])
        empty = render_scene({'atoms': [], 'bonds': []}, {'hashBondStyle': 'spaced'})
        self.assertEqual(empty['style']['hashBondStyle'], 'spaced')
        self.assertEqual(empty['positions'], [])

class HashStyleBoundaryTests(_HashAssertions, unittest.TestCase):
    def test_parent_accepts_only_strict_fixed_hash_error_envelope(self):
        valid = {'schema': preparation.SCHEMA, 'status': 'error', 'code': 'hash_style_failed'}
        cases = [(valid, 'hash_style_failed'), ({**valid, 'detail': 'private fixture detail'}, 'worker_failed'),
                 ({**valid, 'code': 'unknown'}, 'worker_failed'), ({**valid, 'schema': 'unsupported'}, 'worker_failed')]
        for envelope, expected in cases:
            with self.subTest(envelope=envelope):
                supervisor = FakeSupervisor(envelope)
                service = preparation.ImportPreparationService(supervisor=supervisor, command_factory=lambda: ['owned-fixture'])
                try:
                    with self.assertRaises(preparation.ImportPreparationError) as raised:
                        service.prepare_import('N[C@H](C)O', {'hashBondStyle': 'spaced'})
                    self.assertEqual(raised.exception.code, expected)
                    self.assertNotIn('private fixture detail', str(raised.exception))
                    if expected == 'hash_style_failed': self.assertIn('Engine spacing', str(raised.exception))
                    self.assertEqual(len(supervisor.calls), 1)
                finally:
                    self.assertTrue(service.shutdown())

    def test_batch_hash_failure_has_fixed_recovery_hint_and_publishes_no_png(self):
        class FailureService:
            def prepare_batch(self, *args, **kwargs):
                raise preparation.ImportPreparationError('hash_style_failed')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); destination = root/'outputs'; destination.mkdir()
            source = root/'synthetic.nivram'
            source.write_text(json.dumps({'schema': 'nivram/1', 'graph': import_structure('N[C@H](C)O'),
                                          'style': LEGACY_STYLE, 'export': EXPORT}), encoding='utf-8')
            before = source.read_bytes(); manager = BatchExportManager(preparation_service=FailureService())
            try:
                batch = manager._begin_selected([source], destination, {'style': {'hashBondStyle': 'spaced'}, 'export': EXPORT})
                result = manager.prepare(batch['sessionId'], batch['items'][0]['id'])
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['item']['error']['code'], 'hash_style_failed')
                self.assertIn('Engine spacing', result['item']['error']['message'])
                self.assertIn('no PNG was published', result['item']['error']['message'])
                self.assertNotIn(str(root), json.dumps(result))
                self.assertFalse(list(destination.glob('*.png')))
                self.assertEqual(source.read_bytes(), before)
                manifest = json.loads((destination/batch['manifestName']).read_text(encoding='utf-8'))
                self.assertEqual(manifest['items'][0]['error'], result['item']['error'])
            finally:
                manager.close()

    def test_preparation_retains_fixed_hash_failure_and_generic_render_category(self):
        graph = import_structure('N[C@H](C)O')
        project = {'schema': 'nivram/1', 'graph': graph, 'style': LEGACY_STYLE, 'export': EXPORT}
        requests = [
            {'schema': preparation.SCHEMA, 'mode': 'import', 'source': 'N[C@H](C)O',
             'style': {'hashBondStyle': 'spaced'}},
            {'schema': preparation.SCHEMA, 'mode': 'batch', 'source': json.dumps(project),
             'extension': '.nivram', 'settings': {'style': {'hashBondStyle': 'spaced'}, 'export': EXPORT}},
        ]
        for request in requests:
            for error, expected in ((HashStyleError('private fixture detail'), 'hash_style_failed'),
                                    (ValueError('private fixture detail'), 'render_failed')):
                with self.subTest(mode=request['mode'], expected=expected):
                    with patch('nivram.scene.render_scene', side_effect=error):
                        with self.assertRaises(preparation.ImportPreparationError) as raised:
                            preparation._prepare(request)
                    self.assertEqual(raised.exception.code, expected)
                    self.assertEqual(raised.exception.http_status, 422)
                    self.assertNotIn('private fixture detail', str(raised.exception))
                    if expected == 'hash_style_failed':
                        self.assertIn('Engine spacing', str(raised.exception))
                        self.assertIn('unchanged', str(raised.exception))

    def test_semantic_helper_preserves_unrelated_bytes_and_skips_nonhash_bonds(self):
        source = synthetic_svg()
        drawing = synthetic_mol(); before = drawing.ToBinary()
        adjusted = spaced_hashes(source, drawing, 4)
        self.assert_regular_hash(bars(source, 'bond-0 atom-0 atom-1'), bars(adjusted, 'bond-0 atom-0 atom-1'), 4)
        self.assertEqual(drawing.ToBinary(), before)
        plain = Chem.MolFromSmiles('CC')
        self.assertEqual(spaced_hashes(source, plain, 4), source)
        marker = '<!-- untouched -->\n<path class="atom-1" d="M 1,2 L 3,4" style="fill:#333399"/>'
        decorated = source.replace('</svg>', marker+'</svg>')
        self.assertEqual(without_hashes(decorated, hash_classes(drawing)),
                         without_hashes(spaced_hashes(decorated, drawing, 4), hash_classes(drawing)))

    def test_ambiguous_malformed_or_unfit_hashes_reject_with_engine_recovery(self):
        original = [synthetic_bar(i) for i in range(7)]
        sources = {
            'missing semantic group': synthetic_svg([synthetic_bar(i, cls='bond-1 atom-0 atom-1') for i in range(7)]),
            'split atom classes': synthetic_svg([synthetic_bar(0, cls='bond-0 atom-1 atom-0'), *original[1:]]),
            'duplicate bond class token': synthetic_svg([synthetic_bar(0, cls='bond-0 bond-0 atom-0 atom-1'), *original[1:]]),
            'too few bars': synthetic_svg(original[:2]),
            'curve': synthetic_svg([synthetic_bar(0, d='M 0,-1 Q 1,0 0,1'), *original[1:]]),
            'duplicate bar': synthetic_svg([*original[:3], original[2], *original[3:]]),
            'bent center line': synthetic_svg([*original[:3], synthetic_bar(3, d='M 30,6 L 30,14'), *original[4:]]),
            'not transverse': synthetic_svg([*original[:3], synthetic_bar(3, d='M 26,0 L 34,0'), *original[4:]]),
            'nonmonotonic taper': synthetic_svg([*original[:3], synthetic_bar(3, d='M 30,-.5 L 30,.5'), *original[4:]]),
            'transparent stroke': synthetic_svg([original[0].replace('stroke-opacity:1', 'stroke-opacity:0.5'), *original[1:]]),
            'dash array': synthetic_svg([synthetic_bar(0, extra=';stroke-dasharray:2 2'), *original[1:]]),
            'duplicate paint': synthetic_svg([synthetic_bar(0, extra=';stroke:#000000'), *original[1:]]),
            'resource paint': synthetic_svg([original[0].replace('stroke:#000000', 'stroke:url(#x)'), *original[1:]]),
            'unsupported fill': synthetic_svg([original[0].replace('fill:none', 'fill:#000000'), *original[1:]]),
            'inherited transform': f'<svg xmlns="{SVG_NS}"><g transform="translate(1 1)">'+''.join(original)+'</g></svg>',
            'root transform': synthetic_svg().replace('<svg ', '<svg transform="translate(1 1)" ', 1),
            'root opacity': synthetic_svg().replace('<svg ', '<svg opacity="0.5" ', 1),
            'root inherited style': synthetic_svg().replace('<svg ', '<svg style="stroke-opacity:0.5" ', 1),
            'nonadjacent group': synthetic_svg([*original[:3], '<path class="atom-1" d="M 0,0 L 1,1"/>', *original[3:]]),
            'extra same-class closed path': synthetic_svg([*original, synthetic_bar(7, d='M 0,0 L 1,1 L 2,0 Z')]),
            'three colors': synthetic_svg([*original[:2], original[2].replace('#000000', '#FF0000'), *original[3:]]),
            'cannot fit three full-width marks': synthetic_svg([synthetic_bar(i, d=f'M {i},-{i+1} L {i},{i+1}') for i in range(3)]),
        }
        for name, source in sources.items():
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, r'(?i)engine'):
                spaced_hashes(source, synthetic_mol(), 4)
        # Reject the opposite branch's stroke width; do not broaden tolerance.
        for width, invalid_stroke in ((.5, .25), (.99, .495), (1., 1.), (1.01, 1.01)):
            source = synthetic_svg().replace('stroke-width:2px', f'stroke-width:{invalid_stroke}px')
            with self.subTest(width=width, invalid_stroke=invalid_stroke), self.assertRaisesRegex(ValueError, r'(?i)engine'):
                spaced_hashes(source, synthetic_mol(), width)
        for width in (.49, 5.01, True, None, '4', float('nan'), float('inf')):
            with self.subTest(width=width), self.assertRaisesRegex(ValueError, r'(?i)engine'):
                spaced_hashes(synthetic_svg(), synthetic_mol(), width)

    def test_worker_result_and_batch_carry_exact_enum_and_reject_missing_keys(self):
        value = preparation._prepare({'schema': preparation.SCHEMA, 'mode': 'import',
                                      'source': 'N[C@H](C)O', 'style': {'hashBondStyle': 'spaced'}})
        self.assertEqual(value['scene']['style']['hashBondStyle'], 'spaced')
        self.assertIs(protocol.validate_prepared_result(value, mode='import'), value)
        for change in ('missing', 'extra', 'invalid'):
            broken = copy.deepcopy(value)
            if change == 'missing': broken['scene']['style'].pop('hashBondStyle')
            elif change == 'extra': broken['scene']['style']['unknownStyle'] = 'engine'
            else: broken['scene']['style']['hashBondStyle'] = 'unknown'
            with self.subTest(change=change), self.assertRaises(ValueError):
                protocol.validate_prepared_result(broken, mode='import')
        graph = import_structure('N[C@@H](O)S')
        project = {'schema': 'nivram/1', 'graph': graph, 'style': LEGACY_STYLE, 'export': EXPORT}
        settings = protocol.checked_batch_settings({'style': {'hashBondStyle': 'spaced'}, 'export': EXPORT})
        batch = preparation._prepare({'schema': preparation.SCHEMA, 'mode': 'batch',
                                      'source': json.dumps(project), 'extension': '.nivram', 'settings': settings})
        self.assertEqual(batch['graph'], graph)
        self.assertEqual(batch['scene']['style']['hashBondStyle'], 'spaced')
        self.assertEqual(batch['project']['style']['hashBondStyle'], 'spaced')
        self.assertIs(protocol.validate_prepared_result(batch, mode='batch'), batch)

    def test_legacy_profile_defaults_without_rewriting_and_new_profile_retains_enum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); store = ProfileStore(root)
            old = {'schema': 'nivram-profile/1', 'id': 'b'*32, 'revision': 1,
                   'name': 'Legacy', 'style': LEGACY_STYLE, 'export': EXPORT}
            target = root / ('b'*32 + '-000001.json')
            original = json.dumps(old).encode(); target.write_bytes(original)
            self.assertEqual(store.list()['profiles'][0]['style']['hashBondStyle'], 'engine')
            self.assertEqual(target.read_bytes(), original)
            saved = store.save('Spaced hashes', {'hashBondStyle': 'spaced'}, EXPORT)
            self.assertEqual(saved['style']['hashBondStyle'], 'spaced')
            self.assertIn(saved, store.list()['profiles'])
            names = sorted(path.name for path in root.iterdir())
            with self.assertRaises(ValueError): store.save('Invalid', {'hashBondStyle': 'unknown'}, EXPORT)
            with self.assertRaises(ValueError):
                store.save('Unknown field', {'hashBondStyle': 'engine', 'unknownStyle': 1}, EXPORT)
            self.assertEqual(sorted(path.name for path in root.iterdir()), names)


if __name__ == '__main__':
    unittest.main()
