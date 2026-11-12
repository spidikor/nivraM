from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from nivram.desktop import DesktopApi
from nivram.native_bridge import PUBLIC_METHODS


class OpticalNativeTests(unittest.TestCase):
    def test_recognition_requires_current_session_and_exposes_no_runtime_paths(self):
        api = object.__new__(DesktopApi)
        api._server = SimpleNamespace(api_token='synthetic-optical-session')
        api._images = Mock()
        rect = {'x': 1, 'y': 2, 'width': 12, 'height': 15}
        for invalid in (None, '', 'wrong', 'x' * 129, '\u03bc', {}, 42):
            with self.subTest(invalid=type(invalid).__name__), self.assertRaises(PermissionError):
                api.recognize_image('source_job_1', 'crop_request_1', rect, invalid)
        api._images.recognize.assert_not_called()
        api._images.recognize.return_value = {'status': 'recognized'}
        self.assertEqual(api.recognize_image('source_job_1', 'crop_request_1', rect,
            'synthetic-optical-session'), {'status': 'recognized'})
        api._images.recognize.assert_called_once_with('source_job_1', 'crop_request_1', rect)
        self.assertIn('recognize_image', PUBLIC_METHODS)


if __name__ == '__main__':
    unittest.main()
