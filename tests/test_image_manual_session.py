from tests.import_binding_fixtures import prepare_fixture
import base64
import copy
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from rdkit import Chem

from nivram.chemistry import graph_to_mol, import_structure
from nivram.image_import import inspect_png_metadata
from nivram.image_jobs import ImageJobCancelled
from nivram.image_session import ImageImportSession, MANUAL_CORRESPONDENCE
from nivram.import_preparation import ImportPreparationService, SCHEMA, _prepare
from nivram.import_protocol import checked_style
from nivram.worker_processes import CancellationToken, WorkerCancelled


class ImageManualSessionTests(unittest.TestCase):
    JOB = 'manual_source_1'
    REQUEST = 'manual_request_1'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'ordinary.png'
        stream = io.BytesIO()
        Image.new('RGB', (8, 8), 'white').save(stream, format='PNG')
        self.data = stream.getvalue()
        self.source.write_bytes(self.data)
        self.inspection = inspect_png_metadata(self.data)
        self.assertEqual(self.inspection['candidates'], [])
        self.preparation = Mock(spec=ImportPreparationService)
        self.preparation.prepare_import.side_effect = self.prepare
        self.preparation.shutdown.return_value = True
        self.session = ImageImportSession(self.root / 'preserved', preparation_service=self.preparation)
        self.session._jobs.inspect = Mock(side_effect=lambda data, *a, **kw: inspect_png_metadata(data))
        self.session._preview_jobs.preview = Mock(side_effect=self.region)
        self.rect = {'x': 1, 'y': 2, 'width': 3, 'height': 4}
        self.no_process = patch('subprocess.Popen', side_effect=AssertionError('Unexpected native worker'))
        self.no_process.start()
        self.session.inspect(self.source, self.JOB)

    def tearDown(self):
        try:
            self.session.close()
            self.temp.cleanup()
        finally:
            self.no_process.stop()

    def prepare(self, source, style, *, cancellation_token):
        self.assertIsInstance(cancellation_token, CancellationToken)
        if cancellation_token.cancelled:
            raise WorkerCancelled('Cancelled before preparation.')
        return prepare_fixture({'schema': SCHEMA, 'mode': 'import', 'source': source, 'style': style})

    def region(self, data, job_id, request_id, mode, rect):
        with Image.open(io.BytesIO(data)) as original:
            width, height = original.size
            area = rect or {'x': 0, 'y': 0, 'width': width, 'height': height}
            cropped = original.crop((area['x'], area['y'], area['x'] + area['width'], area['y'] + area['height']))
            stream = io.BytesIO()
            cropped.save(stream, format='PNG')
        encoded = stream.getvalue()
        return {'schemaVersion': 1, 'sourceSha256': hashlib.sha256(data).hexdigest(),
            'sourceWidth': width, 'sourceHeight': height, 'mode': mode, 'rect': dict(area),
            'width': area['width'], 'height': area['height'], 'pngBase64': base64.b64encode(encoded).decode('ascii'),
            'pngSha256': hashlib.sha256(encoded).hexdigest(), 'navigationOnly': mode == 'overview',
            'engine': {}, 'bufferAccounting': {}, 'resampling': 'nearest-center' if mode == 'overview' else 'none'}

    def draft(self, source='CCO', request=None, job=None):
        return self.session.prepare_manual(job or self.JOB, request or self.REQUEST, self.rect, source, {})

    def accept(self, drafted, style=None):
        return self.session.prepare_accept(self.JOB, ['manual-1'], style or {}, drafted['region']['reviewToken'])

    def assert_not_preserved(self):
        self.assertFalse((self.root / 'preserved').exists())

    def test_ordinary_png_supplied_text_has_distinct_source_bound_provenance(self):
        source = '  CCO  '
        drafted = self.draft(source)
        self.assertEqual(drafted['status'], 'drafted')
        self.assertEqual(drafted['selectedComponents'], ['manual-1'])
        candidate = drafted['inspection']['candidates'][0]
        self.assertEqual(candidate['source'], 'user-supplied-structure')
        self.assertNotIn('recognition', candidate)
        self.assertEqual(drafted['inspection']['recognition'], self.inspection['recognition'])
        self.assertEqual(candidate['suppliedStructure'], {
            'textSha256': hashlib.sha256(source.encode()).hexdigest(),
            'coordinates': 'from structure text; not traced from image'})
        self.preparation.prepare_import.assert_called_once()
        self.assertEqual(self.preparation.prepare_import.call_args.args, (source, checked_style({})))
        self.assert_not_preserved()
        with self.assertRaisesRegex(ValueError, 'bounded source-image review'):
            self.session.prepare_accept(self.JOB, ['manual-1'], {})
        self.assert_not_preserved()
        self.source.write_bytes(b'Externally changed after inspection')
        accepted = self.accept(drafted)
        provenance = accepted['provenance']
        self.assertEqual(provenance['kind'], 'png-user-supplied-structure')
        self.assertNotIn('recognition', provenance)
        self.assertEqual(provenance['suppliedStructure'], candidate['suppliedStructure'])
        self.assertEqual(provenance['suppliedGraph'], accepted['graph'])
        self.assertEqual(provenance['reviewedRegion']['rect'], self.rect)
        self.assertEqual(provenance['reviewedRegion']['metadataCorrespondence'], MANUAL_CORRESPONDENCE)
        self.assertEqual(provenance['reviewedRegion']['pngSha256'], drafted['region']['pngSha256'])
        digest = hashlib.sha256(self.data).hexdigest()
        self.assertEqual(provenance['sourceSha256'], digest)
        self.assertEqual((self.root / 'preserved' / (digest + '.png')).read_bytes(), self.data)
        self.assertEqual(self.source.read_bytes(), b'Externally changed after inspection')

    def test_cached_preview_and_acceptance_do_not_reparse_or_render_in_host(self):
        drafted = self.draft()
        with (patch('nivram.image_session.select_metadata_candidates', side_effect=AssertionError('Host chemistry')),
                patch('nivram.scene.render_scene', side_effect=AssertionError('Host rendering'))):
            preview = self.session.candidate(self.JOB, ['manual-1'], {})
            original = copy.deepcopy(preview)
            preview['graph']['atoms'][0]['symbol'] = 'Xe'
            self.assertEqual(self.session.candidate(self.JOB, ['manual-1'], {}), original)
            accepted = self.accept(drafted)
        self.assertEqual(accepted['graph'], original['graph'])
        self.preparation.prepare_import.assert_called_once()
        with self.assertRaisesRegex(ValueError, 'style changed'):
            self.session.candidate(self.JOB, ['manual-1'], {'color': False})

    def test_single_2d_mol_preserves_coordinates_and_text_identity(self):
        mol = graph_to_mol(import_structure('C[C@H](N)C(=O)O'))
        source = Chem.MolToMolBlock(mol)
        drafted = self.draft(source)
        accepted = self.accept(drafted)
        self.assertEqual(accepted['graph'], import_structure(source))
        self.assertEqual(accepted['provenance']['suppliedStructure']['textSha256'], hashlib.sha256(source.encode()).hexdigest())

    def test_malformed_or_oversized_text_cannot_create_a_draft(self):
        for source in (None, '', ' ', 'C' * 1_000_001, '\u00e9' * 500_001):
            with self.subTest(source_type=type(source).__name__), self.assertRaises(ValueError):
                self.draft(source)
        self.preparation.prepare_import.assert_not_called()
        self.session._preview_jobs.preview.assert_not_called()
        with self.assertRaises(ValueError):
            self.draft('not a valid SMILES')
        self.assertEqual(self.session._current['result']['candidates'], [])
        self.assertIsNone(self.session._current['manualToken'])
        self.assertIsNone(self.session._current['regionReview'])
        self.assert_not_preserved()

    def test_manual_selection_is_exclusive(self):
        drafted = self.draft()
        for selected in (['manual-1', 'optical-1'], ['metadata-1', 'manual-1'], ['manual-1', 'manual-1']):
            with self.subTest(selected=selected):
                with self.assertRaisesRegex(ValueError, 'separately'):
                    self.session.candidate(self.JOB, selected, {})
                with self.assertRaisesRegex(ValueError, 'separately'):
                    self.session.preview_region(self.JOB, 'mixed_manual_1', self.rect, 'crop', selected)
                with self.assertRaisesRegex(ValueError, 'separately'):
                    self.session.prepare_accept(self.JOB, selected, {}, drafted['region']['reviewToken'])
        self.assert_not_preserved()

    def test_crop_binding_new_token_and_request_nonreuse(self):
        first = self.draft()
        with self.assertRaisesRegex(ValueError, 'already used'):
            self.draft()
        for mode, rect in (('crop', {**self.rect, 'x': 2}), ('overview', None)):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'new crop'):
                self.session.preview_region(self.JOB, 'different_manual_crop', rect, mode, ['manual-1'])
        reviewed = self.session.preview_region(self.JOB, 'same_manual_crop_2', self.rect, 'crop', ['manual-1'])
        self.assertNotEqual(first['region']['reviewToken'], reviewed['reviewToken'])
        with self.assertRaisesRegex(ValueError, 'bounded source-image review'):
            self.accept(first)
        second = self.draft('CO', request='manual_request_2')
        with self.assertRaisesRegex(ValueError, 'bounded source-image review'):
            self.session.prepare_accept(self.JOB, ['manual-1'], {}, reviewed['reviewToken'])
        self.assert_not_preserved()
        self.assertEqual(Chem.MolToSmiles(graph_to_mol(self.accept(second)['graph'])), 'CO')

    def test_invalid_new_draft_retains_previous_candidate_but_revokes_acceptance(self):
        first = self.draft()
        graph = copy.deepcopy(first['inspection']['candidates'][0]['graph'])
        with self.assertRaises(ValueError):
            self.draft('invalid text', request='invalid_replacement_2')
        self.assertEqual(self.session.candidate(self.JOB, ['manual-1'], {})['graph'], graph)
        with self.assertRaisesRegex(ValueError, 'bounded source-image review'):
            self.accept(first)
        self.assert_not_preserved()

    def test_pre_cancelled_and_closed_requests_do_not_start_preparation(self):
        self.session.cancel_region(self.JOB, self.REQUEST)
        with self.assertRaises(ImageJobCancelled):
            self.draft()
        self.assertTrue(self.session.close())
        with self.assertRaises(ImageJobCancelled):
            self.draft(request='after_close_request')
        self.preparation.prepare_import.assert_not_called()
        self.session._preview_jobs.preview.assert_not_called()

    def test_all_lifecycle_invalidations_cancel_owned_parse_and_reject_late_result(self):
        for index, action in enumerate(('region', 'source', 'close', 'new-source', 'new-region')):
            with self.subTest(action=action):
                if self.session._closed:
                    self.session = ImageImportSession(self.root / 'preserved', preparation_service=self.preparation)
                    self.session._jobs.inspect = Mock(side_effect=lambda data, *a, **kw: inspect_png_metadata(data))
                    self.session._preview_jobs.preview = Mock(side_effect=self.region)
                job, request = f'manual_source_{index + 10}', f'manual_request_{index + 10}'
                self.session.inspect(self.source, job)
                def late(source, style, *, cancellation_token):
                    result = self.prepare(source, style, cancellation_token=cancellation_token)
                    if action == 'region':
                        self.session.cancel_region(job, request)
                    elif action == 'source':
                        self.session.cancel(job)
                    elif action == 'close':
                        self.session.close()
                    elif action == 'new-source':
                        self.session.inspect(self.source, 'replacement_source')
                    else:
                        self.session.preview_region(job, 'replacement_region', self.rect, 'crop', [])
                    self.assertTrue(cancellation_token.cancelled)
                    return result
                self.preparation.prepare_import.side_effect = late
                with self.assertRaises(ImageJobCancelled):
                    self.draft(request=request, job=job)
                if self.session._current is not None:
                    self.assertEqual(self.session._current['result']['candidates'], [])
                self.assert_not_preserved()

    def test_source_replacement_between_crop_and_parser_registration_is_rejected(self):
        preview_region = self.session.preview_region
        def switched(*args, **kwargs):
            result = preview_region(*args, **kwargs)
            self.session.inspect(self.source, self.JOB)
            return result
        with patch.object(self.session, 'preview_region', side_effect=switched):
            with self.assertRaises(ImageJobCancelled):
                self.draft()
        self.preparation.prepare_import.assert_not_called()
        self.assert_not_preserved()

    def test_injected_preparation_service_is_not_shut_down_by_image_session(self):
        self.assertTrue(self.session.close())
        self.preparation.shutdown.assert_not_called()
        owned = Mock(spec=ImportPreparationService)
        owned.shutdown.return_value = True
        with patch('nivram.image_session.ImportPreparationService', return_value=owned):
            standalone = ImageImportSession(self.root / 'standalone')
            self.assertTrue(standalone.close())
        owned.shutdown.assert_called_once_with(timeout_seconds=2.0)

    @unittest.skipUnless(sys.platform == 'win32', 'The protected worker requires Windows Job Object limits.')
    def test_actual_protected_worker_prepares_ordinary_png_draft(self):
        self.no_process.stop()
        service = ImportPreparationService()
        self.session._preparation_service = service
        try:
            drafted = self.draft('C[C@H](N)C(=O)O')
            preview = self.session.candidate(self.JOB, ['manual-1'], {})
            self.assertEqual(Chem.MolToSmiles(graph_to_mol(preview['graph'])), 'C[C@H](N)C(=O)O')
            self.assertEqual(drafted['inspection']['recognition'], {'status': 'not_run', 'method': None})
            status = service.snapshot()
            self.assertEqual(status['activeJobs'], 0)
            self.assertEqual(status['lastStatus'], 'ready')
            self.assertTrue(status['lastResourceLimits']['verified'])
            self.assert_not_preserved()
        finally:
            self.assertTrue(service.shutdown(timeout_seconds=2.0))
            self.no_process.start()


if __name__ == '__main__':
    unittest.main()
