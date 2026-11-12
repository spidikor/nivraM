"""Native setup authorization and chooser boundaries; no user profile or UI."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from webview.util import parse_file_type

from nivram.desktop import DesktopApi
from nivram.native_bridge import PUBLIC_METHODS


class OpticalSetupNativeTests(unittest.TestCase):
    def setUp(self):
        self.api = object.__new__(DesktopApi)
        self.api._server = SimpleNamespace(api_token='synthetic-setup-session')
        self.api._optical_setup = Mock()
        self.api._window = Mock()
        self.token = self.api._server.api_token

    def test_all_setup_operations_authenticate_before_store_or_dialog(self):
        operations = (
            lambda token: self.api.begin_optical_setup(token),
            lambda token: self.api.choose_optical_setup_file('opaque', 'python', token),
            lambda token: self.api.save_optical_setup('opaque', token),
            lambda token: self.api.cancel_optical_setup('opaque', token),
        )
        for operation in operations:
            for invalid in (None, '', 'wrong', 'x' * 129, '\u03bc', {}, 42):
                with self.subTest(operation=operation, invalid=type(invalid).__name__):
                    with self.assertRaises(PermissionError):
                        operation(invalid)
        self.assertEqual(self.api._optical_setup.mock_calls, [])
        self.api._window.create_file_dialog.assert_not_called()

    def test_native_selection_is_validated_before_and_after_dialog(self):
        order = []
        self.api._optical_setup.ensure_choice.side_effect = lambda *args: order.append('ensure')
        self.api._window.create_file_dialog.side_effect = lambda *args, **kwargs: (
            order.append('dialog') or [r'C:\chosen\python.exe'])
        self.api._optical_setup.choose.side_effect = lambda *args: (
            order.append('choose') or {'status': 'selected', 'runtimeName': 'python.exe'})
        with patch('webview.FileDialog', SimpleNamespace(OPEN='open')):
            result = self.api.choose_optical_setup_file('opaque', 'python', self.token)
        self.assertEqual(order, ['ensure', 'dialog', 'choose'])
        self.api._optical_setup.ensure_choice.assert_called_once_with('opaque', 'python')
        self.api._optical_setup.choose.assert_called_once_with('opaque', 'python', r'C:\chosen\python.exe')
        self.assertEqual(result, {'status': 'selected', 'runtimeName': 'python.exe'})
        self.assertNotIn('C:', str(result))
        filters = self.api._window.create_file_dialog.call_args.kwargs['file_types']
        self.assertEqual([parse_file_type(value) for value in filters], [('Python runtime', '*.exe')])

    def test_invalid_stale_choice_cannot_open_dialog(self):
        self.api._optical_setup.ensure_choice.side_effect = ValueError('Setup is no longer current.')
        with self.assertRaises(ValueError):
            self.api.choose_optical_setup_file('stale', 'model', self.token)
        self.api._window.create_file_dialog.assert_not_called()
        self.api._optical_setup.choose.assert_not_called()

    def test_cancelled_chooser_keeps_selection_in_store(self):
        self.api._window.create_file_dialog.return_value = None
        self.api._optical_setup.choose.return_value = {'status': 'cancelled', 'modelName': 'old.pth'}
        result = self.api.choose_optical_setup_file('opaque', 'model', self.token)
        self.api._optical_setup.choose.assert_called_once_with('opaque', 'model', None)
        self.assertEqual(result['modelName'], 'old.pth')
        filters = self.api._window.create_file_dialog.call_args.kwargs['file_types']
        self.assertEqual([parse_file_type(value) for value in filters], [('MolScribe model', '*.pth')])

    def test_setup_dispatch_accepts_only_opaque_ids_and_current_session(self):
        for method, store_method, args in (
            ('begin_optical_setup', 'begin', ()),
            ('save_optical_setup', 'save', ('opaque',)),
            ('cancel_optical_setup', 'cancel', ('opaque',)),
        ):
            native = getattr(self.api, method)
            target = getattr(self.api._optical_setup, store_method)
            target.return_value = {'status': store_method}
            self.assertEqual(native(*args, self.token), {'status': store_method})
            target.assert_called_once_with(*args)
            self.assertIn(method, PUBLIC_METHODS)
        self.assertIn('choose_optical_setup_file', PUBLIC_METHODS)
        with self.assertRaises(TypeError):
            self.api.save_optical_setup('opaque', self.token, r'C:\injected\model.pth')

    def test_new_editor_page_invalidates_staged_setup(self):
        self.api._store = Mock()
        self.api._appearance = Mock()
        self.api._begin_recovery_page()
        self.api._optical_setup.begin_page.assert_called_once_with()
        self.assertTrue(self.api._recovery_page_ready)


if __name__ == '__main__':
    unittest.main()
