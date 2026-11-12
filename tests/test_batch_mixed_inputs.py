"""S2.3 mixed failures after a completed output, then later valid input.

Real manager/files/chemical parsing; injected preparation and synthetic PNGs.
This does not exercise the guarded worker, renderer, or a live editor.
"""
import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rdkit import Chem

from nivram import batch_export as batch
from nivram.chemistry import ChemistryError, graph_to_mol, import_structure
from nivram.import_preparation import ImportPreparationError
from nivram.import_protocol import strict_json_loads
from nivram.persistence import validate_project
from test_batch_preparation import SETTINGS
from test_batch_publication_faults import synthetic_png
from test_mol_stereo_roundtrip import mol_fixture

OBSERVATIONS = []


class FixturePreparation:
    """Only a trusted test seam; no worker quota or scene-validation receipt."""
    def __init__(self):
        self.calls = []
        self.results = []

    def prepare_batch(self, text, extension, settings, *, cancellation_token=None):
        if cancellation_token is None or cancellation_token.cancelled:
            raise AssertionError('Fixture requires an active batch reservation.')
        self.calls.append({'extension': extension,
                           'text_sha256': hashlib.sha256(text.encode()).hexdigest()})
        try:
            if extension == '.nivram':
                project = validate_project(strict_json_loads(text))
            elif extension == '.mol':
                project = {'schema': 'nivram/1', 'graph': import_structure(text)}
            else:
                raise AssertionError('Unexpected fixture extension.')
            project = validate_project({**project, 'style': settings['style'],
                                        'export': settings['export']})
        except (ChemistryError, ValueError):
            self.calls[-1]['outcome'] = 'invalid_input'
            raise ImportPreparationError('invalid_input') from None
        self.calls[-1]['outcome'] = 'ready'
        result = {'project': project, 'graph': copy.deepcopy(project['graph']),
                  'scene': {'fixture': 'No molecular rendering performed.'}}
        self.results.append(copy.deepcopy(result))
        return result


class MixedBatchInputTests(unittest.TestCase):
    def test_prior_output_survives_decode_and_chemistry_failures_then_valid_mol(self):
        temporary = tempfile.TemporaryDirectory(prefix='nivram-s23-mixed-')
        root = Path(temporary.name)
        cleanup = {'temporary_directory_removed': False}

        def remove_fixture():
            temporary.cleanup()
            cleanup['temporary_directory_removed'] = not root.exists()
            self.assertTrue(cleanup['temporary_directory_removed'])

        self.addCleanup(remove_fixture)
        destination = root / 'outputs'
        destination.mkdir()
        smiles = '[13CH3][C@@H](O)C(=O)[O-]'
        project = {'schema': 'nivram/1', 'graph': import_structure(smiles)}
        original_project = copy.deepcopy(project)
        supported = mol_fixture(v3000=True, unknown=True)
        unsupported = supported.replace('M  V30 1 F ', 'M  V30 1 * ', 1)
        parsed = Chem.MolFromMolBlock(unsupported, sanitize=True, removeHs=False,
                                      strictParsing=True)
        self.assertIsNotNone(parsed, 'Unsupported fixture must be syntactically valid MOL.')
        self.assertEqual(parsed.GetAtomWithIdx(0).GetAtomicNum(), 0)
        sources = [
            ('first.nivram', json.dumps(project).encode()),
            ('invalid-utf8.mol', b'\xff\xfe invalid UTF-8'),
            ('unsupported.mol', unsupported.encode()),
            ('last.mol', supported.encode()),
        ]
        paths = []
        for name, data in sources:
            path = root / name
            path.write_bytes(data)
            paths.append(path)
        source_hashes = [hashlib.sha256(data).hexdigest() for _, data in sources]
        service = FixturePreparation()
        manager = batch.BatchExportManager(preparation_service=service)
        self.addCleanup(manager.close)
        png = synthetic_png()
        encoded = base64.b64encode(png).decode('ascii')
        chosen = copy.deepcopy(SETTINGS)
        original_settings = copy.deepcopy(chosen)
        with patch('nivram.scene.render_scene', side_effect=AssertionError('Renderer out of scope')), \
             patch('nivram.import_preparation._prepare', side_effect=AssertionError('Worker out of scope')):
            session = manager._begin_selected(paths, destination, chosen)
            session_id = session['sessionId']
            ids = [item['id'] for item in session['items']]
            self.assertEqual([item['sourceSha256'] for item in session['items']], source_hashes)
            first_ready = manager.prepare(session_id, ids[0])
            self.assertEqual(first_ready['status'], 'ready')
            self.assertEqual(first_ready['graph'], original_project['graph'])
            self.assertEqual(Chem.MolToSmiles(graph_to_mol(first_ready['graph']), True),
                             Chem.MolToSmiles(Chem.MolFromSmiles(smiles), True))
            first = manager.write_png(session_id, ids[0], encoded)['item']
            self.assertEqual(first['status'], 'exported')
            first_path = destination / first['outputName']
            manifest_path = destination / session['manifestName']
            failed = []
            for index in (1, 2):
                result = manager.prepare(session_id, ids[index])
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['item']['error'],
                                 {'code': 'invalid_input', 'message': batch.ERRORS['invalid_input']})
                self.assertEqual(result['item']['sourceSha256'], source_hashes[index])
                self.assertIsNone(result['manifestError'])
                for key in ('project', 'graph', 'scene'):
                    self.assertNotIn(key, result)
                for key in ('outputName', 'outputSha256', 'dimensions'):
                    self.assertIsNone(result['item'][key])
                self.assertEqual(len(service.calls), index,
                                 'Decode failure must not call preparation; chemical rejection must.')
                self.assertEqual(manager._session['status'], 'running')
                self.assertIsNone(manager._session['active'])
                self.assertFalse(manager._session['cancelled'].is_set())
                self.assertIsNone(manager._session['items'][index]['_bytes'])
                self.assertNotIn('_preparation', manager._session['items'][index])
                saved = json.loads(manifest_path.read_bytes())
                self.assertEqual(saved['status'], 'running')
                self.assertEqual(saved['items'][0], first)
                self.assertEqual(saved['items'][index], result['item'])
                self.assertEqual(saved['items'][3]['status'], 'pending')
                self.assertEqual(first_path.read_bytes(), png)
                self.assertEqual(hashlib.sha256(first_path.read_bytes()).hexdigest(), first['outputSha256'])
                self.assertEqual({p.name for p in destination.iterdir()},
                                 {first_path.name, manifest_path.name})
                failed.append(result['item'])

            last_ready = manager.prepare(session_id, ids[3])
            self.assertEqual(last_ready['status'], 'ready')
            graph = last_ready['graph']
            self.assertEqual(graph['bonds'][1]['stereo'], int(Chem.BondStereo.STEREOANY))
            self.assertEqual(graph['bonds'][1]['direction'], int(Chem.BondDir.EITHERDOUBLE))
            self.assertEqual(Chem.MolToSmiles(graph_to_mol(graph), True), 'FC=CF')
            self.assertEqual([(a['x'], a['y']) for a in graph['atoms']],
                             [(-1.0, 1.0), (0.0, 0.0), (1.5, 0.0), (2.5, -1.0)])
            last = manager.write_png(session_id, ids[3], encoded)['item']
            self.assertEqual(last['status'], 'exported')
            finished = manager.finish(session_id)

        self.assertEqual(finished['status'], 'completed')
        self.assertIsNone(finished['manifestError'])
        self.assertEqual(finished['items'], [first, *failed, last])
        self.assertEqual([item['status'] for item in finished['items']],
                         ['exported', 'failed', 'failed', 'exported'])
        saved = json.loads(manifest_path.read_bytes())
        self.assertEqual(saved, {'schema': 'nivram-batch-export/1', **finished})
        self.assertEqual([item['sourceSha256'] for item in saved['items']], source_hashes)
        self.assertEqual([call['text_sha256'] for call in service.calls],
                         [source_hashes[i] for i in (0, 2, 3)])
        self.assertEqual([call['outcome'] for call in service.calls], ['ready', 'invalid_input', 'ready'])
        self.assertEqual(service.results[0]['graph'], original_project['graph'])
        self.assertEqual(last_ready['graph'], service.results[1]['graph'])
        for path, (_, data) in zip(paths, sources):
            self.assertEqual(path.read_bytes(), data)
        for item in (first, last):
            data = (destination / item['outputName']).read_bytes()
            self.assertEqual(data, png)
            self.assertEqual(hashlib.sha256(data).hexdigest(), item['outputSha256'])
            self.assertEqual(item['dimensions'], {'width': 32, 'height': 12, 'padding': 2})
        self.assertEqual({p.name for p in destination.iterdir()},
                         {first['outputName'], last['outputName'], manifest_path.name})
        self.assertTrue(all(item['_bytes'] is None and '_preparation' not in item
                            for item in manager._session['items']))
        self.assertIsNone(manager._session['active'])
        self.assertEqual(project, original_project)
        self.assertEqual(chosen, original_settings)
        self.assertNotIn(str(root), json.dumps(finished))
        OBSERVATIONS.append({'case': 'mixed_failures_between_successes',
                             'items': [item['status'] for item in finished['items']],
                             'errors': [item['error']['code'] for item in failed],
                             'source_sha256': source_hashes, 'preparation_calls': service.calls,
                             'outputs': [first, last],
                             'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                             'exact_manifest_matches_receipt': True,
                             'original_sources_preserved': True, 'cleanup': cleanup,
                             'protected_worker_rendering_or_native_qualified': False})


if __name__ == '__main__':
    unittest.main()
