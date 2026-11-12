import base64
import hashlib
import io
import struct
from pathlib import Path
import tempfile
import threading
import unittest
import zlib
from unittest.mock import Mock, patch

from PIL import Image, PngImagePlugin

from nivram.image_import import inspect_png_metadata
from nivram.image_jobs import ImageJobCancelled
from nivram.image_session import ImageImportSession
from tests.test_image_session import sample_png


class ImageRegionSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.png'
        self.data = sample_png()
        self.source.write_bytes(self.data)
        self.inspection = inspect_png_metadata(self.data)
        self.inspection['image']['previewMode'] = 'crop_required'
        self.session = ImageImportSession(self.root / 'preserved')
        self.session._jobs.inspect = Mock(return_value=self.inspection)
        self.ids = [self.inspection['candidates'][0]['id']]
        self.rect = {'x': 1, 'y': 2, 'width': 3, 'height': 4}
        self.session.inspect(self.source, 'region_source_1')

    def tearDown(self):
        self.session.close()
        self.temp.cleanup()

    def region(self, data, job_id, request_id, mode, rect):
        area = rect or {'x': 0, 'y': 0, 'width': 8, 'height': 8}
        out = io.BytesIO()
        Image.new('RGB', (area['width'], area['height']), 'white').save(out, format='PNG')
        encoded = out.getvalue()
        return {'schemaVersion': 1, 'sourceSha256': hashlib.sha256(data).hexdigest(),
                'sourceWidth': 8, 'sourceHeight': 8, 'mode': mode, 'rect': area,
                'width': area['width'], 'height': area['height'],
                'pngBase64': base64.b64encode(encoded).decode('ascii'),
                'pngSha256': hashlib.sha256(encoded).hexdigest(),
                'navigationOnly': mode == 'overview', 'engine': {}, 'bufferAccounting': {},
                'resampling': 'nearest-center' if mode == 'overview' else 'none'}

    def preview(self, request='region_request_1', mode='crop', selected=None):
        self.session._preview_jobs.preview = Mock(side_effect=self.region)
        return self.session.preview_region('region_source_1', request,
            self.rect if mode == 'crop' else None, mode, self.ids if selected is None else selected)

    def test_overview_cannot_authorize_acceptance(self):
        overview = self.preview(mode='overview', selected=[])
        self.assertTrue(overview['navigationOnly'])
        self.assertIsNone(overview.get('reviewToken'))
        with self.assertRaisesRegex(ValueError, 'bounded source-image review'):
            self.session.prepare_accept('region_source_1', self.ids, {})
        self.assertFalse((self.root / 'preserved').exists())

    def test_unsupported_large_source_explains_why_region_controls_are_unavailable(self):
        body = b'gAMA' + struct.pack('>I', 45455)
        chunk = struct.pack('>I', 4) + body + struct.pack('>I', zlib.crc32(body))
        changed = self.data[:33] + chunk + self.data[33:]
        self.source.write_bytes(changed)
        self.session._jobs.inspect.return_value = inspect_png_metadata(changed)
        self.session._jobs.inspect.return_value['image']['previewMode'] = 'crop_required'
        result = self.session.inspect(self.source, 'region_source_2')
        self.assertFalse(result['regionPreviewAvailable'])
        self.assertIn(result['regionPreviewLimitation'], result['previewLimitation'])
        self.assertIn('unavailable', result['previewLimitation'])
        self.assertNotIn('Review a detail crop', result['previewLimitation'])

    def test_srgb_tagged_source_offers_region_controls(self):
        body = b'sRGB\0'
        chunk = struct.pack('>I', 1) + body + struct.pack('>I', zlib.crc32(body))
        changed = self.data[:33] + chunk + self.data[33:]
        self.source.write_bytes(changed)
        self.session._jobs.inspect.return_value = inspect_png_metadata(changed)
        self.session._jobs.inspect.return_value['image']['previewMode'] = 'crop_required'
        result = self.session.inspect(self.source, 'region_source_2')
        self.assertTrue(result['regionPreviewAvailable'])

    def test_crop_preserves_whole_components_original_bytes_and_bound_provenance(self):
        preview = self.preview()
        self.source.write_bytes(b'external source changed')
        accepted = self.session.prepare_accept('region_source_1', self.ids, {}, preview['reviewToken'])
        original_graph = self.inspection['candidates'][0]['graph']
        self.assertEqual(accepted['graph'], original_graph)
        region = accepted['provenance']['reviewedRegion']
        self.assertEqual(region['rect'], self.rect)
        self.assertEqual(region['requestId'], preview['requestId'])
        self.assertEqual(region['pngSha256'], preview['pngSha256'])
        self.assertEqual(region['scale'], 'one-source-pixel-per-image-pixel')
        self.assertNotIn('reviewToken', region)
        self.assertEqual((self.root / 'preserved' / (hashlib.sha256(self.data).hexdigest() + '.png')).read_bytes(), self.data)

    def test_fabricated_and_superseded_tokens_are_rejected(self):
        first = self.preview()
        second = self.preview('region_request_2')
        for token in (None, 'invented', first['reviewToken']):
            with self.subTest(token=token), self.assertRaises(ValueError):
                self.session.prepare_accept('region_source_1', self.ids, {}, token)
        self.assertTrue(self.session.prepare_accept('region_source_1', self.ids, {}, second['reviewToken']))

    def test_repeated_request_id_is_not_reusable(self):
        self.preview()
        with self.assertRaisesRegex(ValueError, 'already used'):
            self.preview()

    def test_completed_crop_cancellation_revokes_its_token(self):
        preview = self.preview()
        self.session.cancel_region('region_source_1', preview['requestId'])
        with self.assertRaises(ValueError):
            self.session.prepare_accept('region_source_1', self.ids, {}, preview['reviewToken'])

    def test_cancel_during_worker_cannot_publish_a_preview(self):
        def cancelled(data, job, request, mode, rect):
            self.session.cancel(job)
            return self.region(data, job, request, mode, rect)
        self.session._preview_jobs.preview = Mock(side_effect=cancelled)
        with self.assertRaises(ImageJobCancelled):
            self.session.preview_region('region_source_1', 'region_request_1', self.rect, 'crop', self.ids)
        self.assertIsNone(self.session._current)

    def test_selection_free_detail_is_not_acceptance_authority(self):
        preview = self.preview(selected=[])
        self.assertIsNone(preview.get('reviewToken'))
        with self.assertRaises(ValueError):
            self.session.prepare_accept('region_source_1', self.ids, {}, preview.get('reviewToken'))

    def test_stale_cancellation_does_not_revoke_newer_review(self):
        first = self.preview()
        second = self.preview('region_request_2')
        self.session.cancel_region('region_source_1', first['requestId'])
        self.assertTrue(self.session.prepare_accept('region_source_1', self.ids, {}, second['reviewToken']))

    def test_cancellation_before_registration_never_starts_worker(self):
        self.session.cancel_region('region_source_1', 'region_request_1')
        self.session._preview_jobs.preview = Mock()
        with self.assertRaises(ImageJobCancelled):
            self.session.preview_region('region_source_1', 'region_request_1', self.rect, 'crop', self.ids)
        self.session._preview_jobs.preview.assert_not_called()

    def test_close_during_result_prevents_publication(self):
        def closed(data, job, request, mode, rect):
            self.session.close()
            return self.region(data, job, request, mode, rect)
        self.session._preview_jobs.preview = Mock(side_effect=closed)
        with self.assertRaises(ImageJobCancelled):
            self.session.preview_region('region_source_1', 'region_request_1', self.rect, 'crop', self.ids)

    def test_small_source_crop_requires_current_token_after_opt_in(self):
        self.inspection['image']['previewMode'] = 'bounded_preview'
        self.session.inspect(self.source, 'region_source_1')
        self.assertTrue(self.session.prepare_accept('region_source_1', self.ids, {}))
        crop = self.preview()
        with self.assertRaises(ValueError):
            self.session.prepare_accept('region_source_1', self.ids, {})
        self.assertTrue(self.session.prepare_accept('region_source_1', self.ids, {}, crop['reviewToken']))

    def test_close_revokes_review_before_draining_workers(self):
        preview = self.preview()
        def drain():
            with self.assertRaises(ValueError):
                self.session.prepare_accept('region_source_1', self.ids, {}, preview['reviewToken'])
        self.session._jobs.close = Mock(side_effect=drain)
        self.session.close()
        self.assertFalse((self.root / 'preserved').exists())

    def test_exhausted_cancel_quota_still_cancels_current_request(self):
        preview = self.preview()
        self.session._current['regionCancelled'] = {f'cancelled_{n:04d}' for n in range(512)}
        self.session._preview_jobs.cancel = Mock(return_value={'status': 'cancelled'})
        self.session.cancel_region('region_source_1', preview['requestId'])
        self.session._preview_jobs.cancel.assert_any_call('region_source_1', preview['requestId'])
        self.session._preview_jobs.cancel.assert_any_call('region_source_1')
        self.assertIsNone(self.session._current)
        with self.assertRaises(ValueError):
            self.session.prepare_accept('region_source_1', self.ids, {}, preview['reviewToken'])

    def test_reinspection_during_worker_rejects_the_old_result(self):
        def reinspection(data, job, request, mode, rect):
            self.session.inspect(self.source, 'region_source_2')
            return self.region(data, job, request, mode, rect)
        self.session._preview_jobs.preview = Mock(side_effect=reinspection)
        with self.assertRaises(ImageJobCancelled):
            self.session.preview_region('region_source_1', 'region_request_1', self.rect, 'crop', self.ids)
        self.assertEqual(self.session._current['id'], 'region_source_2')

    def test_crop_token_cannot_authorize_another_or_expanded_component_selection(self):
        stream = io.BytesIO()
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('SMILES', 'CCO.[Na+]')
        Image.new('RGB', (8, 8), 'white').save(stream, format='PNG', pnginfo=metadata)
        self.data = stream.getvalue()
        self.source.write_bytes(self.data)
        self.inspection = inspect_png_metadata(self.data)
        self.inspection['image']['previewMode'] = 'crop_required'
        self.session._jobs.inspect.return_value = self.inspection
        self.session.inspect(self.source, 'region_source_1')
        self.assertEqual(len(self.inspection['candidates']), 2)
        first, second = (candidate['id'] for candidate in self.inspection['candidates'])
        reviewed = self.preview(selected=[first])

        for selection in ([second], [first, second]):
            with self.subTest(selection=selection), self.assertRaisesRegex(ValueError, 'bounded source-image review'):
                self.session.prepare_accept('region_source_1', selection, {}, reviewed['reviewToken'])
            self.assertFalse((self.root / 'preserved').exists())

        accepted = self.session.prepare_accept('region_source_1', [first], {}, reviewed['reviewToken'])
        self.assertEqual(accepted['selectedComponents'], [first])
        self.assertEqual(accepted['graph'], self.inspection['candidates'][0]['graph'])
        self.assertEqual(accepted['provenance']['selectedComponents'], [first])

    def test_old_source_cancellation_does_not_revoke_new_source_token(self):
        first = self.preview()
        self.session.inspect(self.source, 'region_source_2')
        second = self.session.preview_region('region_source_2', 'region_request_2',
                                            self.rect, 'crop', self.ids)
        self.session.cancel('region_source_1')
        self.assertEqual(self.session._current['id'], 'region_source_2')
        with self.assertRaisesRegex(ValueError, 'bounded source-image review'):
            self.session.prepare_accept('region_source_2', self.ids, {}, first['reviewToken'])
        self.assertFalse((self.root / 'preserved').exists())
        accepted = self.session.prepare_accept('region_source_2', self.ids, {}, second['reviewToken'])
        self.assertEqual(accepted['provenance']['reviewedRegion']['requestId'], 'region_request_2')

    def _assert_invalidation_during_candidate_render_rejects_acceptance(self, action):
        from nivram.scene import render_scene

        reviewed = self.preview()
        rendering = threading.Event()
        resume = threading.Event()
        outcome = {}

        def held_render(graph, style):
            rendering.set()
            if not resume.wait(5):
                raise AssertionError('Test failed to release its held candidate render.')
            return render_scene(graph, style)

        def accept():
            try:
                outcome['accepted'] = self.session.prepare_accept(
                    'region_source_1', self.ids, {}, reviewed['reviewToken'])
            except Exception as exc:
                outcome['error'] = exc

        with patch('nivram.scene.render_scene', side_effect=held_render):
            worker = threading.Thread(target=accept, daemon=True)
            worker.start()
            try:
                self.assertTrue(rendering.wait(5), 'Candidate did not reach the held render.')
                action()
                self.assertFalse((self.root / 'preserved').exists())
            finally:
                resume.set()
                worker.join(5)
            self.assertFalse(worker.is_alive(), 'Owned acceptance test thread did not exit.')

        self.assertNotIn('accepted', outcome)
        self.assertIsInstance(outcome.get('error'), ValueError)
        self.assertIn('bounded source-image review', str(outcome['error']))
        self.assertFalse((self.root / 'preserved').exists())

    def test_close_while_candidate_rendering_rejects_acceptance_before_source_write(self):
        self._assert_invalidation_during_candidate_render_rejects_acceptance(self.session.close)

    def test_cancel_while_candidate_rendering_rejects_acceptance_before_source_write(self):
        self._assert_invalidation_during_candidate_render_rejects_acceptance(
            lambda: self.session.cancel('region_source_1'))


if __name__ == '__main__':
    unittest.main()
