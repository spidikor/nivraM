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

    def test_native_chooser_only_returns_an_opaque_one_use_selection(self):
        from nivram.desktop import DesktopApi
        api = DesktopApi.__new__(DesktopApi)
        api._images = self.session
        api._window = Mock()
        api._window.create_file_dialog.return_value = [str(self.source)]
        choice = api.inspect_image('dialog-first', 'choose')
        self.assertEqual(set(choice), {'status', 'jobId', 'selectionId'})
        self.assertEqual(choice['status'], 'selected')
        self.session._jobs.inspect.assert_not_called()
        for job, key in [('another-job', choice['selectionId']), ('dialog-first', str(self.source))]:
            with self.assertRaises(ImageJobCancelled):
                api.inspect_image(job, 'inspect', key)
        result = api.inspect_image('dialog-first', 'inspect', choice['selectionId'])
        self.assertEqual(result['inspection']['originalSha256'], hashlib.sha256(self.data).hexdigest())
        self.assertEqual(api._window.create_file_dialog.call_count, 1)
        with self.assertRaises(ImageJobCancelled):
            api.inspect_image('dialog-first', 'inspect', choice['selectionId'])
        self.assertEqual(self.session._jobs.inspect.call_count, 1)
        self.assertEqual(api.inspect_image('legacy-one-call')['status'], 'inspected')

    def test_cancelled_or_replaced_native_choice_cannot_resume_or_read(self):
        for action in ('cancel', 'replace', 'reload', 'dismiss'):
            with self.subTest(action=action):
                job = 'dialog-' + action
                replacement = {}
                def pick():
                    if action == 'cancel':
                        self.session.cancel(job)
                    elif action == 'replace':
                        replacement.update(self.session.choose_source('new-dialog', lambda: self.source))
                    elif action == 'reload':
                        self.session.discard_choice()
                    else:
                        return None
                    return self.source
                if action == 'dismiss':
                    self.assertEqual(self.session.choose_source(job, pick), {'status': 'cancelled'})
                else:
                    with self.assertRaises(ImageJobCancelled):
                        self.session.choose_source(job, pick)
                if replacement:
                    self.assertEqual(self.session._choice['key'], replacement['selectionId'])
                    self.session.cancel('new-dialog')
                self.assertIsNone(self.session._choice)
        self.session._jobs.inspect.assert_not_called()

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
