"""Extra atom-label clearance: legacy appearance and bounded style transport."""
import copy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET

from nivram import import_preparation as preparation
from nivram import import_protocol as protocol
from nivram import population_state
from nivram.chemistry import import_structure
from nivram.persistence import validate_project
from nivram.profiles import ProfileStore
from nivram.scene import checked_style, render_scene
from tests.test_import_preparation import FakeSupervisor, response
from tests.test_population_state import request as state_request

LEGACY_STYLE = {'bondWidth': 4.2, 'labelSize': 36, 'bondSpacing': .14, 'color': True}
STYLE_KEYS = set(LEGACY_STYLE) | {'labelPadding', 'hashBondStyle'}
EXPORT = {'width': 640, 'marginMode': 'percent', 'margin': 2}
# Captured from the delivered appearance-build-fb9d4a9e0cd4-90a76cb3 renderer,
# scene.py SHA750c3eadf569f40c27bf6205e7da234d574d6d98219310fc60a1c018bc3d86ff,
# using pinned RDKit2025.09.4. Intentionally superseded by the measured-Arial
# outline fix: these old text-rendering hashes capture the centering defect.
SUPERSEDED_TEXT_SVG = {
    'CO': 'abd2f468e9d6eab7e5e73bcc3c42884675e076392a9ee088bcef6e1327f4b566',
    'NCCO': '6aa1672ab17bb9d2606dc07d143ab5645c9edb45bb187475ecb42dd6fa905f0b',
    'C=O': '9fdb8c5f418e2e4fcdfc39e46eb9267b9bd000082d41cdc42ba8fe2bce11cb58',
    'C#N': 'd0519141062d834b9ff4993697437d4aa2b0b7d02247a70bc93d3521c21109f6',
}


def imported(smiles='CO', padding=.145):
    return preparation._prepare({'schema': preparation.SCHEMA, 'mode': 'import',
                                 'source': smiles, 'style': {**LEGACY_STYLE, 'labelPadding': padding}})


class LabelPaddingTests(unittest.TestCase):
    def test_old_four_field_styles_default_and_use_corrected_outline_renderer(self):
        original = copy.deepcopy(LEGACY_STYLE)
        for smiles, superseded in SUPERSEDED_TEXT_SVG.items():
            with self.subTest(smiles=smiles):
                graph = import_structure(smiles)
                old = render_scene(graph, LEGACY_STYLE)
                explicit = render_scene(graph, {**LEGACY_STYLE, 'labelPadding': .04})
                self.assertEqual(old['style']['labelPadding'], .04)
                self.assertEqual(set(old['style']), STYLE_KEYS)
                self.assertNotEqual(hashlib.sha256(old['svg'].encode()).hexdigest(), superseded)
                self.assertNotIn('<text', old['svg'])
                self.assertTrue(any(re.fullmatch(r'atom-\d+', e.attrib.get('class', ''))
                                    and e.tag.endswith('}path') for e in ET.fromstring(old['svg'])))
                for key in ('svg', 'positions', 'transform', 'width', 'height'):
                    self.assertEqual(old[key], explicit[key])
        self.assertEqual(LEGACY_STYLE, original)
        self.assertEqual(checked_style(None)['labelPadding'], .04)
        self.assertEqual(protocol.checked_style(None)['labelPadding'], .04)

    def test_renderer_and_pure_protocol_accept_exact_finite_boundaries(self):
        for value in (0, .001, .04, .145, .2):
            with self.subTest(value=value):
                original = {**LEGACY_STYLE, 'labelPadding': value}
                native, pure = checked_style(original), protocol.checked_style(original)
                self.assertEqual(native, pure)
                self.assertEqual(set(native), STYLE_KEYS)
                self.assertEqual(native['labelPadding'], value)
                self.assertEqual(original['labelPadding'], value)

    def test_invalid_types_nonfinite_and_outside_bounds_reject(self):
        for value in (True, False, None, '', '0.04', [], {}, -.0001, .2001,
                      float('nan'), float('inf'), float('-inf')):
            for validator in (checked_style, protocol.checked_style):
                with self.subTest(value=value, validator=validator.__module__), self.assertRaises(ValueError):
                    validator({**LEGACY_STYLE, 'labelPadding': value})

    def test_clearance_trims_actual_bond_without_moving_graph_or_labels(self):
        graph = import_structure('CO'); before = copy.deepcopy(graph)
        zero = render_scene(graph, {**LEGACY_STYLE, 'labelPadding': 0})
        wide = render_scene(graph, {**LEGACY_STYLE, 'labelPadding': .2})
        self.assertEqual(graph, before)
        for key in ('positions', 'transform', 'width', 'height'):
            self.assertEqual(zero[key], wide[key])
        roots = [ET.fromstring(value['svg']) for value in (zero, wide)]
        labels = [[(e.tag, e.attrib, e.text) for e in root.iter() if re.fullmatch(r'atom-\d+', e.attrib.get('class', ''))] for root in roots]
        self.assertTrue(labels[0])
        self.assertEqual(labels[0], labels[1])
        lengths, starts = [], []
        for root in roots:
            paths = [e for e in root if e.attrib.get('class') == 'bond-0 atom-0 atom-1']
            self.assertEqual(len(paths), 2)
            coordinates = [list(map(float, re.findall(r'-?\d+(?:\.\d+)?', p.attrib['d']))) for p in paths]
            self.assertTrue(all(len(row) == 4 for row in coordinates))
            lengths.append(sum(((x2-x1)**2+(y2-y1)**2)**.5 for x1,y1,x2,y2 in coordinates))
            starts.append(coordinates[0][:2])
        self.assertEqual(starts[0], starts[1]); self.assertLess(lengths[1], lengths[0])
        carbon = import_structure('CC')
        self.assertEqual(render_scene(carbon, {'labelPadding': 0})['svg'], render_scene(carbon, {'labelPadding': .2})['svg'])

    def test_empty_scene_carries_validated_clearance(self):
        result = render_scene({'atoms': [], 'bonds': []}, {'labelPadding': .2})
        self.assertEqual(result['style']['labelPadding'], .2)
        self.assertEqual(result['positions'], [])

    def test_child_result_requires_exact_expanded_style_keys(self):
        value = imported()
        self.assertEqual(value['scene']['style'], {**LEGACY_STYLE, 'labelPadding': .145, 'hashBondStyle': 'engine'})
        self.assertIs(protocol.validate_prepared_result(value, mode='import'), value)
        for change in ('missing', 'extra', 'invalid'):
            broken = copy.deepcopy(value)
            if change == 'missing':
                broken['scene']['style'].pop('labelPadding')
            elif change == 'extra':
                broken['scene']['style']['unknownStyle'] = .1
            else:
                broken['scene']['style']['labelPadding'] = .2001
            with self.subTest(change=change), self.assertRaises(ValueError):
                protocol.validate_prepared_result(broken, mode='import')

    def test_batch_settings_and_legacy_project_use_requested_clearance(self):
        graph = import_structure('NCCO')
        project = {'schema': 'nivram/1', 'graph': graph, 'style': LEGACY_STYLE, 'export': EXPORT}
        original = copy.deepcopy(project); validate_project(project); self.assertEqual(project, original)
        settings = {'style': {**LEGACY_STYLE, 'labelPadding': .145}, 'export': EXPORT,
                    'profile': {'id': 'a'*32, 'revision': 1, 'name': 'Exact clearance'}}
        checked = protocol.checked_batch_settings(settings)
        self.assertEqual(checked['style']['labelPadding'], .145)
        value = preparation._prepare({'schema': preparation.SCHEMA, 'mode': 'batch',
                                      'source': json.dumps(project), 'extension': '.nivram', 'settings': checked})
        self.assertEqual(value['scene']['style'], {**settings['style'], 'hashBondStyle': 'engine'})
        self.assertEqual(value['project']['style'], {**settings['style'], 'hashBondStyle': 'engine'})
        self.assertEqual(value['graph'], graph)
        self.assertIs(protocol.validate_prepared_result(value, mode='batch'), value)
        with self.assertRaises(ValueError):
            protocol.checked_batch_settings({'style': {'labelPadding': .1, 'unknownStyle': 0}})

    def test_profile_load_defaults_old_record_without_rewriting_and_saves_new_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); store = ProfileStore(root)
            old = {'schema': 'nivram-profile/1', 'id': 'b'*32, 'revision': 1, 'name': 'Legacy',
                   'style': LEGACY_STYLE, 'export': EXPORT}
            target = root / ('b'*32 + '-000001.json'); original = json.dumps(old).encode(); target.write_bytes(original)
            self.assertEqual(store.list()['profiles'][0]['style']['labelPadding'], .04)
            self.assertEqual(target.read_bytes(), original)
            saved = store.save('Clearance', {'labelPadding': .145}, EXPORT)
            self.assertEqual(saved['style']['labelPadding'], .145)
            self.assertIn(saved, store.list()['profiles'])
            names = sorted(p.name for p in root.iterdir())
            with self.assertRaises(ValueError):
                store.save('Invalid', {'labelPadding': .2001}, EXPORT)
            self.assertEqual(sorted(p.name for p in root.iterdir()), names)

    def test_native_preparation_marshals_exact_nondefault_style(self):
        value = imported(); supervisor = FakeSupervisor(response(value))
        service = preparation.ImportPreparationService(supervisor=supervisor, command_factory=lambda: ['owned-fixture'])
        try:
            result = service.prepare_import('CO', {'labelPadding': .145})
            request = json.loads(supervisor.calls[0]['input_text'])
            self.assertEqual(request['style'], {**LEGACY_STYLE, 'labelPadding': .145, 'hashBondStyle': 'engine'})
            self.assertEqual(result['scene']['style'], request['style'])
            with self.assertRaises(preparation.ImportPreparationError):
                service.prepare_import('CO', {'labelPadding': True})
            self.assertEqual(len(supervisor.calls), 1)
        finally:
            self.assertTrue(service.shutdown())

    def test_population_state_accepts_new_key_and_rejects_unknown_key(self):
        data = state_request(); data['style'] = {**LEGACY_STYLE, 'labelPadding': .145}
        before = copy.deepcopy(data)
        self.assertEqual(population_state.validate_request(data)['style'], {**data['style'], 'hashBondStyle': 'engine'})
        result = population_state.prepare_state(data)
        self.assertEqual(result['scene']['style'], {**data['style'], 'hashBondStyle': 'engine'})
        self.assertIs(protocol.validate_prepared_result(result, mode='population-state'), result)
        self.assertEqual(data, before)
        broken = copy.deepcopy(data); broken['style']['unknownStyle'] = .1
        with self.assertRaises(ValueError):
            population_state.validate_request(broken)


if __name__ == '__main__':
    unittest.main()
