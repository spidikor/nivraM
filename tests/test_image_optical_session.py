import base64
import copy
import hashlib
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
import zlib

from PIL import Image, PngImagePlugin
from rdkit import Chem

from nivram.chemistry import graph_to_mol, import_structure
from nivram.image_import import inspect_png_metadata
from nivram.image_jobs import ImageJobCancelled
from nivram.image_session import ImageImportSession
from nivram.import_preparation import SCHEMA, _prepare
from nivram.optical_import import MODEL_SHA256, OpticalImportError, optical_candidate
from nivram.png_preview import preview_png
from nivram.png_preview_jobs import validate_preview_result
from nivram.worker_processes import CancellationToken, WorkerCancelled


class FakeOpticalJobs:
    """No subprocesses: real result validation, with an observable owner token."""
    def __init__(self):
        self.availability = Mock(return_value={'status': 'configured', 'message': 'Test runtime'})
        self.cancel = Mock(return_value={'status': 'cancelled'})
        self.close = Mock(return_value=True)
        self.recognize = Mock(side_effect=self.result)
        self.reconfigure = Mock(side_effect=lambda python, model, commit: commit())
        self.dispatched = []

    def result(self, data, request_id, *, timeout, _cancellation_token):
        if not 0 < timeout <= 90:
            raise AssertionError('Recognition must receive only the remaining 90-second budget.')
        if not isinstance(_cancellation_token, CancellationToken):
            raise AssertionError('The session must supply the actual worker CancellationToken.')
        if _cancellation_token.cancelled:
            raise WorkerCancelled('Cancelled before fake worker dispatch.')
        self.dispatched.append((data, request_id, _cancellation_token))
        digest = hashlib.sha256(data).hexdigest()
        raw = {'schemaVersion': 1, 'method': 'MolScribe', 'modelSha256': MODEL_SHA256,
               'inputSha256': digest, 'smiles': 'CO',
               'molfile': Chem.MolToMolBlock(graph_to_mol(import_structure('CO'))),
               'confidence': .8}
        return {'schemaVersion': 1, 'inputSha256': digest, 'candidate': optical_candidate(raw)}


class ImageOpticalSessionTests(unittest.TestCase):
    JOB = 'optical_source_1'
    REQUEST = 'optical_request_1'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.png'
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('SMILES', 'CCO')
        image = Image.new('RGB', (8, 8), 'white')
        for x in range(8):
            for y in range(8):
                image.putpixel((x, y), (x * 30, y * 30, (x + y) * 15))
        stream = io.BytesIO()
        image.save(stream, format='PNG', pnginfo=metadata)
        self.data = stream.getvalue()
        self.source.write_bytes(self.data)
        self.inspection = inspect_png_metadata(self.data)
        self.metadata_id = self.inspection['candidates'][0]['id']
        self.optical = FakeOpticalJobs()
        self.session = ImageImportSession(self.root / 'preserved', optical_jobs=self.optical)
        self.session._jobs.inspect = Mock(side_effect=lambda *a, **kw: copy.deepcopy(self.inspection))
        self.session._preview_jobs.preview = Mock(side_effect=self.region)
        self.rect = {'x': 1, 'y': 2, 'width': 3, 'height': 4}
        self.no_process = patch('subprocess.Popen', side_effect=AssertionError('No native worker in optical session tests'))
        self.no_process.start()
        self.session.inspect(self.source, self.JOB)

    def tearDown(self):
        try:
            self.session.close()
            self.temp.cleanup()
        finally:
            self.no_process.stop()

    def region(self, data, job_id, request_id, mode, rect):
        self.assertEqual(data, self.data, 'Preview must use retained source bytes.')
        with Image.open(io.BytesIO(data)) as original:
            size = original.size
            area = rect or {'x': 0, 'y': 0, 'width': size[0], 'height': size[1]}
            cropped = original.crop((area['x'], area['y'], area['x'] + area['width'], area['y'] + area['height']))
            clean = Image.new('RGB', cropped.size)
            clean.paste(cropped)
            stream = io.BytesIO()
            clean.save(stream, format='PNG')
        encoded = stream.getvalue()
        return {'schemaVersion': 1, 'sourceSha256': hashlib.sha256(data).hexdigest(),
                'sourceWidth': size[0], 'sourceHeight': size[1], 'mode': mode, 'rect': dict(area),
                'width': area['width'], 'height': area['height'],
                'pngBase64': base64.b64encode(encoded).decode('ascii'),
                'pngSha256': hashlib.sha256(encoded).hexdigest(),
                'navigationOnly': mode == 'overview', 'engine': {}, 'bufferAccounting': {},
                'resampling': 'nearest-center' if mode == 'overview' else 'none'}

    def recognize(self, request=None):
        return self.session.recognize(self.JOB, request or self.REQUEST, self.rect)

    def assert_unpublished(self):
        self.assertFalse((self.root / 'preserved').exists())
        if self.session._current is not None:
            self.assertFalse(any(candidate['id'] == 'optical-1' for candidate in self.session._current['result']['candidates']))

    def test_singlet_failure_keeps_prior_candidates_source_and_review_state(self):
        from test_mol_radical_declarations import V2, V3, payload
        for index, block in enumerate((V2, V3)):
            before = copy.deepcopy(self.session._current['result'])
            def rejected(*args, **kwargs):
                return optical_candidate(payload(block))
            self.optical.recognize.side_effect = rejected
            with self.assertRaisesRegex(ValueError, 'singlet'):
                self.recognize(f"singlet_request_{index}")
            self.assertEqual(self.session._current['result'], before)
            self.assertEqual(self.source.read_bytes(), self.data)
            self.assert_unpublished()

    def test_recognition_and_preview_do_not_preserve_or_change_source_until_accept(self):
        before = copy.deepcopy(self.inspection)
        recognized = self.recognize()
        self.assertEqual(recognized['selectedComponents'], ['optical-1'])
        self.assertEqual(recognized['status'], 'recognized')
        self.assertEqual(self.source.read_bytes(), self.data)
        self.assertEqual(self.inspection, before)
        self.assertFalse((self.root / 'preserved').exists())
        preview = self.session.candidate(self.JOB, ['optical-1'], {})
        self.assertEqual(Chem.MolToSmiles(graph_to_mol(preview['graph'])), 'CO')
        self.assertFalse((self.root / 'preserved').exists())
        with self.assertRaisesRegex(ValueError, 'bounded source-image review'):
            self.session.prepare_accept(self.JOB, ['optical-1'], {})
        self.assertFalse((self.root / 'preserved').exists())
        accepted = self.session.prepare_accept(self.JOB, ['optical-1'], {}, recognized['region']['reviewToken'])
        digest = hashlib.sha256(self.data).hexdigest()
        self.assertEqual((self.root / 'preserved' / (digest + '.png')).read_bytes(), self.data)
        self.assertEqual(self.source.read_bytes(), self.data)
        self.assertEqual(accepted['graph'], preview['graph'])

    def test_recognition_uses_retained_crop_and_binds_exact_provenance(self):
        self.source.write_bytes(b'Changed externally after inspection')
        recognized = self.recognize()
        data, request, token = self.optical.dispatched[0]
        self.assertIsInstance(token, CancellationToken)
        self.assertFalse(token.cancelled)
        self.assertEqual(request, self.REQUEST)
        self.assertEqual(data, base64.b64decode(recognized['region']['pngBase64']))
        self.assertNotEqual(data, self.data)
        self.assertEqual(inspect_png_metadata(data)['candidates'], [])
        accepted = self.session.prepare_accept(self.JOB, ['optical-1'], {}, recognized['region']['reviewToken'])
        provenance = accepted['provenance']
        self.assertEqual(provenance['kind'], 'png-optical-reconstruction')
        self.assertEqual(provenance['selectedComponents'], ['optical-1'])
        self.assertEqual(provenance['sourceSha256'], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(provenance['sourceName'], 'source.png')
        self.assertEqual(provenance['recognition']['modelSha256'], MODEL_SHA256)
        self.assertEqual(provenance['recognition']['inputSha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(provenance['recognition']['confidenceMeaning'], 'uncalibrated model score')
        region = provenance['reviewedRegion']
        self.assertEqual(region['requestId'], self.REQUEST)
        self.assertEqual(region['rect'], self.rect)
        self.assertEqual((region['sourceWidth'], region['sourceHeight']), (8, 8))
        self.assertEqual((region['width'], region['height']), (3, 4))
        self.assertEqual(region['pngSha256'], provenance['recognition']['inputSha256'])
        self.assertEqual(region['sourceSha256'], provenance['sourceSha256'])
        self.assertEqual(region['metadataCorrespondence'], 'unverified optical recognition of this crop')
        self.assertEqual(self.source.read_bytes(), b'Changed externally after inspection')

    def test_16bit_source_derives_valid_8bit_optical_crop_and_preserves_original_on_accept(self):
        def chunk(kind, body):
            return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(body, zlib.crc32(kind)))

        # Build the original 16-bit RGBA bytes directly: Pillow's usual RGBA
        # save path would make an 8-bit source and miss this integration seam.
        alphas = (0, 65535, 0x8123, 0, 0x0101, 0xABCD, 0xFFFE, 65535)
        rows = []
        for y in range(8):
            samples = [(0x2345 + x * 997, 0x3456 + y * 983, 0x4567 + (x + y) * 577, alphas[x]) for x in range(8)]
            rows.append(b'\0' + b''.join(struct.pack('>HHHH', *pixel) for pixel in samples))
        self.data = (b'\x89PNG\r\n\x1a\n'
                     + chunk(b'IHDR', struct.pack('>IIBBBBB', 8, 8, 16, 6, 0, 0, 0))
                     + chunk(b'tEXt', b'SMILES\0CCO')
                     + chunk(b'IDAT', zlib.compress(b''.join(rows))) + chunk(b'IEND', b''))
        self.source.write_bytes(self.data)
        self.inspection = inspect_png_metadata(self.data)
        original_hash = hashlib.sha256(self.data).hexdigest()
        inspected = self.session.inspect(self.source, self.JOB)
        self.assertTrue(inspected['regionPreviewAvailable'])
        self.assertEqual(self.data[24:26], bytes((16, 6)))

        def real_derivative(data, job_id, request_id, mode, rect):
            self.assertEqual(data, self.data, 'Decode the retained original, not a changed file or an earlier derivative.')
            self.assertEqual((job_id, request_id, mode, rect), (self.JOB, self.REQUEST, 'crop', self.rect))
            result = preview_png(data, mode=mode, rect=rect, expected_source_sha256=original_hash)
            return validate_preview_result(result, original_hash, mode, rect, (8, 8))

        self.session._preview_jobs.preview = Mock(side_effect=real_derivative)
        self.source.write_bytes(b'Changed externally after 16-bit inspection')
        recognized = self.recognize()
        self.session._preview_jobs.preview.assert_called_once()
        self.optical.recognize.assert_called_once()
        self.assertFalse((self.root / 'preserved').exists())
        derived, request, token = self.optical.dispatched[0]
        derived_hash = hashlib.sha256(derived).hexdigest()
        self.assertEqual(request, self.REQUEST)
        self.assertFalse(token.cancelled)
        self.assertEqual(derived, base64.b64decode(recognized['region']['pngBase64'], validate=True))
        self.assertEqual(derived[24:26], bytes((8, 6)))
        self.assertNotEqual(derived_hash, original_hash)
        self.assertEqual(recognized['region']['sourceSha256'], original_hash)
        self.assertEqual(recognized['region']['pngSha256'], derived_hash)
        self.assertEqual(inspect_png_metadata(derived)['candidates'], [])
        with Image.open(io.BytesIO(derived)) as image:
            self.assertEqual((image.mode, image.size), ('RGBA', (3, 4)))
            self.assertEqual(image.getpixel((0, 0))[3], 255)
            self.assertTrue(0 < image.getpixel((1, 0))[3] < 255)
            self.assertEqual(image.getpixel((2, 0))[3], 0)
            self.assertTrue(any(image.getpixel((2, 0))[:3]), 'Transparent sample colours must remain unassociated.')
        with self.assertRaisesRegex(ValueError, 'bounded source-image review'):
            self.session.prepare_accept(self.JOB, ['optical-1'], {})
        accepted = self.session.prepare_accept(self.JOB, ['optical-1'], {}, recognized['region']['reviewToken'])
        provenance = accepted['provenance']
        self.assertEqual(provenance['kind'], 'png-optical-reconstruction')
        self.assertEqual(provenance['sourceSha256'], original_hash)
        self.assertEqual(provenance['recognition']['modelSha256'], MODEL_SHA256)
        self.assertEqual(provenance['recognition']['inputSha256'], derived_hash)
        self.assertEqual(provenance['reviewedRegion']['sourceSha256'], original_hash)
        self.assertEqual(provenance['reviewedRegion']['pngSha256'], derived_hash)
        self.assertEqual(provenance['reviewedRegion']['rect'], self.rect)
        self.assertEqual(provenance['reviewedRegion']['scale'], 'one-source-pixel-per-image-pixel')
        self.assertEqual((self.root / 'preserved' / (original_hash + '.png')).read_bytes(), self.data)
        self.assertEqual(self.source.read_bytes(), b'Changed externally after 16-bit inspection')

    def test_optical_selection_must_be_separate_and_exact(self):
        recognized = self.recognize()
        token = recognized['region']['reviewToken']
        for selection in [['optical-1', self.metadata_id], [self.metadata_id, 'optical-1']]:
            with self.subTest(selection=selection):
                with self.assertRaisesRegex(ValueError, 'separately'):
                    self.session.candidate(self.JOB, selection, {})
                with self.assertRaisesRegex(ValueError, 'separately'):
                    self.session.prepare_accept(self.JOB, selection, {}, token)
                with self.assertRaisesRegex(ValueError, 'separately'):
                    self.session.preview_region(self.JOB, 'mixed_request_1', self.rect, 'crop', selection)
        with self.assertRaisesRegex(ValueError, 'bounded source-image review'):
            self.session.prepare_accept(self.JOB, [self.metadata_id], {}, token)
        self.assertFalse((self.root / 'preserved').exists())

    def test_different_crop_or_overview_cannot_review_old_optical_candidate(self):
        self.recognize()
        different = {**self.rect, 'x': 2}
        before_calls = self.session._preview_jobs.preview.call_count
        for rect, mode in [(different, 'crop'), (None, 'overview')]:
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'Recognize the new crop'):
                self.session.preview_region(self.JOB, 'different_crop_1', rect, mode, ['optical-1'])
        self.assertEqual(self.session._preview_jobs.preview.call_count, before_calls)
        self.assertFalse((self.root / 'preserved').exists())

    def test_same_crop_new_review_binds_new_token_and_optical_correspondence(self):
        first = self.recognize()
        reviewed = self.session.preview_region(self.JOB, 'same_crop_request_2', self.rect, 'crop', ['optical-1'])
        self.assertIsInstance(reviewed['reviewToken'], str)
        self.assertNotEqual(reviewed['reviewToken'], first['region']['reviewToken'])
        with self.assertRaisesRegex(ValueError, 'bounded source-image review'):
            self.session.prepare_accept(self.JOB, ['optical-1'], {}, first['region']['reviewToken'])
        accepted = self.session.prepare_accept(self.JOB, ['optical-1'], {}, reviewed['reviewToken'])
        self.assertEqual(accepted['provenance']['reviewedRegion']['requestId'], 'same_crop_request_2')
        self.assertEqual(accepted['provenance']['reviewedRegion']['metadataCorrespondence'], 'unverified optical recognition of this crop')
        self.assertEqual(accepted['provenance']['recognition']['inputSha256'], reviewed['pngSha256'])
        self.optical.recognize.assert_called_once()

    def test_wrong_result_or_candidate_input_hash_is_not_published(self):
        for index, field in enumerate(['result', 'candidate']):
            def wrong(*args, **kwargs):
                result = self.optical.result(*args, **kwargs)
                if field == 'result':
                    result['inputSha256'] = '0' * 64
                else:
                    result['candidate']['recognition']['inputSha256'] = '0' * 64
                return result
            self.optical.recognize.side_effect = wrong
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'does not match'):
                self.recognize(f'wrong_hash_request_{index}')
            self.assert_unpublished()
            self.assertIsNone(self.session._current['regionReview'])

    def test_conflicting_or_aliased_prediction_retains_source_and_candidates_and_allows_manual_review(self):
        mol = graph_to_mol(import_structure('CO'))
        plain_mol = Chem.MolToMolBlock(mol)
        Chem.SetAtomAlias(mol.GetAtomWithIdx(0), 'Me')
        aliased_mol = Chem.MolToMolBlock(mol)
        for index, (smiles, molfile, supplied) in enumerate((
                ('CN', plain_mol, 'CN'), ('CO', aliased_mol, plain_mol))):
            with self.subTest(case='conflict' if index == 0 else 'alias'):
                self.optical.recognize.side_effect = self.optical.result
                previous = self.recognize(f'previous_proposal_{index}')
                before = copy.deepcopy(self.session._current['result'])
                embedded = copy.deepcopy(self.session.candidate(self.JOB, [self.metadata_id], {})['graph'])

                def invalid_prediction(data, request_id, *, timeout, _cancellation_token):
                    self.assertFalse(_cancellation_token.cancelled)
                    digest = hashlib.sha256(data).hexdigest()
                    raw = {'schemaVersion': 1, 'method': 'MolScribe', 'modelSha256': MODEL_SHA256,
                           'inputSha256': digest, 'smiles': smiles, 'molfile': molfile, 'confidence': .8}
                    return {'schemaVersion': 1, 'inputSha256': digest, 'candidate': optical_candidate(raw)}

                self.optical.recognize.side_effect = invalid_prediction
                with self.assertRaises(OpticalImportError):
                    self.recognize(f'invalid_proposal_{index}')
                self.assertEqual(self.source.read_bytes(), self.data)
                self.assertEqual(self.session._current['data'], self.data)
                self.assertEqual(self.session._current['result'], before)
                self.assertEqual(self.session.candidate(self.JOB, [self.metadata_id], {})['graph'], embedded)
                self.assertFalse((self.root / 'preserved').exists())
                self.assertIsNone(self.session._current['opticalRequest'])
                self.assertIsNone(self.session._current['opticalToken'])
                # A new attempt revokes review authority, even though the last
                # published native candidate inventory is retained unchanged.
                self.assertIsNone(self.session._current['regionReview'])
                with self.assertRaisesRegex(ValueError, 'bounded source-image review'):
                    self.session.prepare_accept(self.JOB, ['optical-1'], {}, previous['region']['reviewToken'])

                def prepare(source, style, *, cancellation_token):
                    self.assertIsInstance(cancellation_token, CancellationToken)
                    self.assertFalse(cancellation_token.cancelled)
                    return _prepare({'schema': SCHEMA, 'mode': 'import', 'source': source, 'style': style})

                # Exercise both text paths using the real shared parser, while
                # retaining this session suite's explicit no-subprocess fixture.
                with patch.object(self.session._preparation_service, 'prepare_import', side_effect=prepare):
                    drafted = self.session.prepare_manual(self.JOB, f'manual_recovery_{index}',
                                                           self.rect, supplied, {})
                self.assertEqual(drafted['status'], 'drafted')
                self.assertEqual(drafted['selectedComponents'], ['manual-1'])
                manual = next(row for row in drafted['inspection']['candidates'] if row['id'] == 'manual-1')
                self.assertEqual(manual['source'], 'user-supplied-structure')
                self.assertNotIn('recognition', manual)
                self.assertEqual(manual['graph'], import_structure(supplied))
                self.assertEqual(manual['suppliedStructure']['textSha256'], hashlib.sha256(supplied.encode()).hexdigest())
                self.assertNotEqual(drafted['region']['reviewToken'], previous['region']['reviewToken'])
                self.assertEqual(self.source.read_bytes(), self.data)
                self.assertFalse((self.root / 'preserved').exists())

    def test_cancellation_during_recognition_refuses_late_worker_result(self):
        for index, cancel_whole_source in enumerate([False, True]):
            job = f'cancel_source_{index}'
            request = f'cancel_request_{index}'
            self.session.inspect(self.source, job)
            def late(data, request_id, *, timeout, _cancellation_token):
                result = self.optical.result(data, request_id, timeout=timeout, _cancellation_token=_cancellation_token)
                if cancel_whole_source:
                    self.session.cancel(job)
                else:
                    self.session.cancel_region(job, request_id)
                self.assertTrue(_cancellation_token.cancelled)
                return result
            self.optical.recognize.side_effect = late
            with self.subTest(cancel_whole_source=cancel_whole_source), self.assertRaises(ImageJobCancelled):
                self.session.recognize(job, request, self.rect)
            self.optical.cancel.assert_any_call(request)
            self.assert_unpublished()

    def test_cancel_before_registration_starts_neither_crop_nor_optical_worker(self):
        self.session.cancel_region(self.JOB, self.REQUEST)
        with self.assertRaises(ImageJobCancelled):
            self.recognize()
        self.session._preview_jobs.preview.assert_not_called()
        self.optical.recognize.assert_not_called()
        self.assert_unpublished()

    def test_cancel_between_token_creation_and_worker_dispatch_is_observed(self):
        decode = base64.b64decode
        def cancel_before_dispatch(*args, **kwargs):
            self.session.cancel_region(self.JOB, self.REQUEST)
            return decode(*args, **kwargs)
        with patch('nivram.image_session.base64.b64decode', side_effect=cancel_before_dispatch):
            with self.assertRaises(ImageJobCancelled):
                self.recognize()
        token = self.optical.recognize.call_args.kwargs['_cancellation_token']
        self.assertTrue(token.cancelled)
        self.assertEqual(self.optical.dispatched, [])
        self.assert_unpublished()

    def test_close_rejects_recognition_before_dispatch_and_closes_optical_owner(self):
        self.assertTrue(self.session.close())
        self.optical.close.assert_called_once()
        with self.assertRaises(ImageJobCancelled):
            self.recognize()
        self.optical.recognize.assert_not_called()
        self.assert_unpublished()

    def test_close_during_recognition_cancels_token_and_refuses_result(self):
        def late(data, request_id, *, timeout, _cancellation_token):
            result = self.optical.result(data, request_id, timeout=timeout, _cancellation_token=_cancellation_token)
            self.assertTrue(self.session.close())
            self.assertTrue(_cancellation_token.cancelled)
            return result
        self.optical.recognize.side_effect = late
        with self.assertRaises(ImageJobCancelled):
            self.recognize()
        self.optical.close.assert_called_once()
        self.optical.cancel.assert_any_call(self.REQUEST)
        self.assert_unpublished()

    def test_ordinary_metadata_still_prepares_without_optical_recognition(self):
        accepted = self.session.prepare_accept(self.JOB, [self.metadata_id], {})
        self.assertEqual(Chem.MolToSmiles(graph_to_mol(accepted['graph'])), 'CCO')
        self.assertEqual(accepted['provenance']['kind'], 'png-metadata-component-reconstruction')
        self.assertNotIn('recognition', accepted['provenance'])
        self.optical.recognize.assert_not_called()
        self.session._preview_jobs.preview.assert_not_called()

    def test_setup_preserves_existing_proposal_review_and_dispatches_future_work(self):
        recognized = self.recognize()
        before = copy.deepcopy(self.session._current)
        commit = Mock()
        status = self.session.configure_optical(self.root / 'python.exe', self.root / 'model.pth', commit)
        self.assertEqual(status['status'], 'configured')
        commit.assert_called_once_with()
        self.assertEqual(self.session._current, before)
        self.session.prepare_accept(self.JOB, ['optical-1'], {}, recognized['region']['reviewToken'])
        self.recognize('optical_after_setup')
        self.assertEqual(self.optical.recognize.call_count, 2)

    def test_setup_rejected_during_crop_before_optical_dispatch(self):
        commit = Mock()
        def during_crop(*args, **kwargs):
            self.assertIsNone(self.session._current['opticalRequest'])
            with self.assertRaisesRegex(ValueError, 'Wait for local structure recognition'):
                self.session.configure_optical(self.root / 'python.exe', self.root / 'model.pth', commit)
            return self.region(*args, **kwargs)
        self.session._preview_jobs.preview.side_effect = during_crop
        self.recognize()
        commit.assert_not_called()
        self.assertIsNone(self.session._optical_operation)

    def test_cancelled_worker_keeps_reservation_until_unwound(self):
        commit = Mock()
        def cancelled_unwind(*args, **kwargs):
            self.session.cancel(self.JOB)
            self.assertIsNone(self.session._current)
            with self.assertRaisesRegex(ValueError, 'Wait for local structure recognition'):
                self.session.configure_optical(self.root / 'python.exe', self.root / 'model.pth', commit)
            raise WorkerCancelled('Unwinding')
        self.optical.recognize.side_effect = cancelled_unwind
        with self.assertRaises(ImageJobCancelled):
            self.recognize()
        commit.assert_not_called()
        self.assertIsNone(self.session._optical_operation)
        self.session.configure_optical(self.root / 'python.exe', self.root / 'model.pth', commit)
        commit.assert_called_once_with()

    def test_crop_failure_releases_configuration_reservation(self):
        self.session._preview_jobs.preview.side_effect = ValueError('Invalid crop')
        with self.assertRaises(ValueError):
            self.recognize()
        self.assertIsNone(self.session._optical_operation)
        commit = Mock()
        self.session.configure_optical(self.root / 'python.exe', self.root / 'model.pth', commit)
        commit.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
