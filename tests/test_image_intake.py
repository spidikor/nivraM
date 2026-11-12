import base64
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image, PngImagePlugin

from nivram.image_import import inspect_png_metadata
from nivram.image_jobs import ImageJobCancelled
from nivram.image_session import ImageImportSession


class ImageByteIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        stream = io.BytesIO()
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('SMILES', 'CCO')
        Image.new('RGB', (8, 8), 'white').save(stream, format='PNG', pnginfo=metadata)
        self.data = stream.getvalue()
        self.encoded = base64.b64encode(self.data).decode('ascii')
        self.result = inspect_png_metadata(self.data)
        self.session = ImageImportSession(self.root / 'sources')
        self.session._jobs.inspect = Mock(return_value=self.result)
        self.no_process = patch('subprocess.Popen', side_effect=AssertionError('No worker in intake unit tests'))
        self.no_process.start()

    def tearDown(self):
        try:
            self.session.close()
            self.temp.cleanup()
        finally:
            self.no_process.stop()

    def inspect(self, data=None, name='clipboard.png', job='byte_input_1'):
        return self.session.inspect_bytes(self.encoded if data is None else data, name, job)

    def test_bytes_use_same_review_and_preserve_exact_original_only_after_accept(self):
        report = self.inspect()
        self.assertEqual(report['inspection']['originalSha256'], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(report['name'], 'clipboard.png')
        self.assertEqual(self.session._jobs.inspect.call_args.args, (self.data, 'byte_input_1'))
        self.assertFalse((self.root / 'sources').exists())
        selected = [self.result['candidates'][0]['id']]
        accepted = self.session.prepare_accept('byte_input_1', selected, {})
        digest = hashlib.sha256(self.data).hexdigest()
        self.assertEqual((self.root / 'sources' / (digest + '.png')).read_bytes(), self.data)
        self.assertEqual(accepted['provenance']['sourceName'], 'clipboard.png')

    def test_strict_payload_types_padding_and_signature_reject_before_worker(self):
        for value in ['', {}, ['abc'], 'data:image/png;base64,' + self.encoded,
                      self.encoded + '\n', '%%%=', 'aGVsbG8=', 'é']:
            with self.subTest(type=type(value).__name__):
                with self.assertRaises(ValueError):
                    self.inspect(value)
        self.session._jobs.inspect.assert_not_called()
        self.assertIsNone(self.session._pending)
        self.assertIsNone(self.session._current)

    def test_encoded_limit_rejects_before_decode_and_decoded_limit_before_worker(self):
        with patch('nivram.image_session.MAX_INPUT', len(self.data) - 1):
            with patch('nivram.image_session.base64.b64decode', side_effect=AssertionError('decode should not run')):
                with self.assertRaises(ValueError):
                    self.inspect('A' * (4 * ((len(self.data) + 1) // 3) + 4))
            with self.assertRaises(ValueError):
                self.inspect()
        self.session._jobs.inspect.assert_not_called()

    def test_filename_is_only_a_label_and_cannot_be_a_native_path(self):
        for name in ['', '../private.png', 'C:\\private.png', 'folder/private.png',
                     'x\x00.png', 'x\n.png', 'x.txt', 'a' * 252 + '.png', None]:
            with self.subTest(name=repr(name)):
                with self.assertRaises(ValueError):
                    self.inspect(name=name)
        self.session._jobs.inspect.assert_not_called()

    def test_cancel_before_decode_and_during_worker_cannot_publish(self):
        self.session.cancel('byte_input_1')
        with patch('nivram.image_session.base64.b64decode', side_effect=AssertionError('cancel before decode')):
            with self.assertRaises(ImageJobCancelled):
                self.inspect()
        def cancelled(*args, **kwargs):
            self.session.cancel('byte_input_2')
            return self.result
        self.session._jobs.inspect.side_effect = cancelled
        with self.assertRaises(ImageJobCancelled):
            self.inspect(job='byte_input_2')
        self.assertIsNone(self.session._current)
        self.assertIsNone(self.session._pending)
        self.assertFalse((self.root / 'sources').exists())

    def test_invalid_job_and_closed_session_reject_before_decoding(self):
        with patch('nivram.image_session.base64.b64decode', side_effect=AssertionError('no decode')):
            for job in [None, {}, 'tiny', '../private', 'x' * 81]:
                with self.subTest(job=repr(job)), self.assertRaises(ValueError):
                    self.inspect(job=job)
            self.session.close()
            with self.assertRaises(ImageJobCancelled):
                self.inspect()
        self.session._jobs.inspect.assert_not_called()

    def test_wrong_worker_identity_cannot_publish_or_preserve(self):
        self.session._jobs.inspect.return_value = {**self.result, 'originalSha256': '0' * 64}
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.inspect()
        self.assertIsNone(self.session._current)
        self.assertFalse((self.root / 'sources').exists())

    def test_raster_only_png_does_not_become_an_editable_candidate(self):
        stream = io.BytesIO()
        Image.new('RGB', (8, 8), 'white').save(stream, format='PNG')
        data = stream.getvalue()
        self.session._jobs.inspect.return_value = inspect_png_metadata(data)
        report = self.inspect(base64.b64encode(data).decode('ascii'))
        self.assertEqual(report['inspection']['candidates'], [])
        self.assertEqual(report['inspection']['recognition']['status'], 'not_run')
        with self.assertRaises(ValueError):
            self.session.prepare_accept('byte_input_1', [], {})
        self.assertFalse((self.root / 'sources').exists())


if __name__ == '__main__':
    unittest.main()
