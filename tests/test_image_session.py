import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from PIL import Image, PngImagePlugin
from nivram.image_import import inspect_png_metadata
from nivram.image_session import ImageImportSession
from nivram.image_jobs import ImageJobCancelled


def sample_png():
    stream = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('SMILES', 'CCO')
    Image.new('RGB', (8, 8), 'white').save(stream, format='PNG', pnginfo=metadata)
    return stream.getvalue()


class ImageSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.png'
        self.data = sample_png()
        self.source.write_bytes(self.data)
        self.result = inspect_png_metadata(self.data)
        self.session = ImageImportSession(self.root / 'preserved')
        self.session._jobs.inspect = Mock(return_value=self.result)
        self.ids = [self.result['candidates'][0]['id']]

    def tearDown(self):
        self.session.close()
        self.temp.cleanup()

    def test_accept_preserves_inspected_bytes_despite_later_source_file_change(self):
        result = self.session.inspect(self.source, 'original-bytes')
        self.assertTrue(result['reviewAvailable'])
        self.source.write_bytes(b'a different external file now')
        candidate = self.session.prepare_accept('original-bytes', self.ids, {})
        digest = hashlib.sha256(self.data).hexdigest()
        self.assertEqual((self.root / 'preserved' / (digest + '.png')).read_bytes(), self.data)
        self.assertEqual(candidate['provenance']['sourceSha256'], digest)
        self.assertEqual(self.source.read_bytes(), b'a different external file now')

    def test_cancel_during_worker_cannot_publish_review(self):
        def worker(*args, **kwargs):
            self.session.cancel('cancel-in-flight')
            return self.result
        self.session._jobs.inspect.side_effect = worker
        with self.assertRaises(ImageJobCancelled):
            self.session.inspect(self.source, 'cancel-in-flight')
        self.assertIsNone(self.session._current)

    def test_source_without_bounded_preview_cannot_be_accepted(self):
        self.result['image']['previewMode'] = 'crop_required'
        report = self.session.inspect(self.source, 'large-source')
        self.assertFalse(report['reviewAvailable'])
        with self.assertRaisesRegex(ValueError, 'source-image review'):
            self.session.prepare_accept('large-source', self.ids, {})
        self.assertFalse((self.root / 'preserved').exists())

    def test_corrupt_preserved_source_is_not_overwritten_or_accepted(self):
        self.session.inspect(self.source, 'damaged-copy')
        directory = self.root / 'preserved'; directory.mkdir()
        destination = directory / (hashlib.sha256(self.data).hexdigest() + '.png')
        destination.write_bytes(b'damaged')
        with self.assertRaisesRegex(ValueError, 'damaged'):
            self.session.prepare_accept('damaged-copy', self.ids, {})
        self.assertEqual(destination.read_bytes(), b'damaged')


if __name__ == '__main__':
    unittest.main()
