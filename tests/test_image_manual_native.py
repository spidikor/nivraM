from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from nivram.desktop import DesktopApi
from nivram.import_preparation import ImportPreparationService
from nivram.native_bridge import PUBLIC_METHODS


class ImageManualNativeTests(unittest.TestCase):
    def test_supplied_draft_requires_current_session_token(self):
        api = object.__new__(DesktopApi)
        api._server = SimpleNamespace(api_token='synthetic-manual-session')
        api._images = Mock()
        rect = {'x': 1, 'y': 2, 'width': 12, 'height': 15}
        for invalid in (None, '', 'wrong', 'x' * 129, '\u03bc', {}, 42):
            with self.subTest(invalid=type(invalid).__name__), self.assertRaises(PermissionError):
                api.preview_image_draft('source_job_1', 'crop_request_1', rect, 'CCO', {}, invalid)
        api._images.prepare_manual.assert_not_called()
        api._images.prepare_manual.return_value = {'status': 'drafted'}
        self.assertEqual(api.preview_image_draft('source_job_1', 'crop_request_1', rect,
            'CCO', {}, 'synthetic-manual-session'), {'status': 'drafted'})
        api._images.prepare_manual.assert_called_once_with('source_job_1', 'crop_request_1', rect, 'CCO', {})
        self.assertIn('preview_image_draft', PUBLIC_METHODS)

    def test_uninitialized_editor_cannot_prepare_a_draft(self):
        api = object.__new__(DesktopApi)
        api._server = None
        api._images = Mock()
        with self.assertRaises(PermissionError):
            api.preview_image_draft('source_job_1', 'crop_request_1', {}, 'CCO', {}, 'token')
        api._images.prepare_manual.assert_not_called()

    def test_desktop_passes_shared_preparation_service_to_image_session(self):
        service = Mock(spec=ImportPreparationService)
        with tempfile.TemporaryDirectory() as directory:
            api = DesktopApi(Path(directory), preparation_service=service)
            self.assertIs(api._images._preparation_service, service)
            self.assertFalse(api._images._owns_preparation_service)
            self.assertTrue(api._images.close())
        service.shutdown.assert_not_called()


if __name__ == '__main__':
    unittest.main()
