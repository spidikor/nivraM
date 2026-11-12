"""Actual pinned pywebview dispatcher/expose checks, without a native UI."""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from webview.util import js_bridge_call
from webview.window import Window
from webview.event import Event

from nivram.desktop import DesktopApi
from nivram.native_bridge import NativeBridgeRejected, PUBLIC_METHODS, expose_desktop_api


class FakeWindow:
    # Exercise the real vendor registration implementation too.
    expose = Window.expose

    def __init__(self, api=None):
        self._js_api = api
        self._functions = {}
        self._expose_lock = threading.Lock()
        self.events = SimpleNamespace(loaded=threading.Event(), _pywebviewready=threading.Event())
        self.events.before_load = Event(self, True)
        self.completed = threading.Event()
        self.callback = None

    def evaluate_js(self, value):
        self.callback = value
        self.completed.set()


def stub_api():
    api = SimpleNamespace()
    for name in PUBLIC_METHODS:
        def operation(*args, _name=name):
            return {'called': _name, 'argumentCount': len(args)}
        operation.__name__ = name
        setattr(api, name, operation)
    return api


class NativeBridgeTests(unittest.TestCase):
    def test_png_byte_intake_authenticates_before_decoding_and_preserves_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            api = DesktopApi(directory)
            self.addCleanup(api._images.close)
            self.addCleanup(api._batch.close)
            self.addCleanup(api._reference_exports.close)
            self.addCleanup(api._population_exports.close)
            api._server = SimpleNamespace(api_token='synthetic_session_token')
            with patch.object(api._images, 'inspect_bytes', return_value={'status': 'inspected'}) as inspect:
                for token in (None, '', 'wrong', '\u2603', 'x' * 129, 42):
                    with self.subTest(token_type=type(token).__name__):
                        with self.assertRaises(PermissionError):
                            api.inspect_image_bytes(object(), object(), object(), token)
                inspect.assert_not_called()
                self.assertEqual(api.inspect_image_bytes('cG5n', 'clipboard.png', 'synthetic_job',
                    'synthetic_session_token'), {'status': 'inspected'})
                inspect.assert_called_once_with('cG5n', 'clipboard.png', 'synthetic_job')

    def test_explicit_public_functions_preserve_dispatch_and_arguments(self):
        window = FakeWindow()
        receipt = expose_desktop_api(window, stub_api())
        self.assertEqual(receipt, {'status': 'installed', 'applicationMethods': 38,
                                  'fallbackObjectExposed': False})
        self.assertIsNone(window._js_api)
        self.assertEqual(set(window._functions), set(PUBLIC_METHODS))
        js_bridge_call(window, 'save_project', [{'synthetic': True}, None, True], 'test')
        self.assertTrue(window.completed.wait(2))
        self.assertIn('"called": "save_project"', window.callback)
        self.assertIn('"argumentCount": 3', window.callback)
        window.completed.clear()
        js_bridge_call(window, 'inspect_image_bytes', ['cG5n', 'clipboard.png', 'job12345', 'token'], 'png')
        self.assertTrue(window.completed.wait(2))
        self.assertIn('"called": "inspect_image_bytes"', window.callback)
        self.assertIn('"argumentCount": 4', window.callback)


    def test_fragment_shelf_has_only_named_record_operations(self):
        window = FakeWindow()
        expose_desktop_api(window, stub_api())
        for name, args in (('list_fragments', []), ('save_fragment', ['test', {'atoms': [], 'bonds': []}])):
            window.completed.clear()
            js_bridge_call(window, name, args, 'fragment')
            self.assertTrue(window.completed.wait(2))
            self.assertIn('"called": "' + name + '"', window.callback)
        with self.assertRaises(NativeBridgeRejected):
            window._functions.get('_fragments.save')

    def test_optical_setup_is_exposed_without_private_store_traversal(self):
        window = FakeWindow()
        expose_desktop_api(window, stub_api())
        for name, args in (
                ('begin_optical_setup', ['token']),
                ('choose_optical_setup_file', ['opaque-id', 'model', 'token']),
                ('save_optical_setup', ['opaque-id', 'token']),
                ('cancel_optical_setup', ['opaque-id', 'token'])):
            window.completed.clear()
            js_bridge_call(window, name, args, 'setup')
            self.assertTrue(window.completed.wait(2))
            self.assertIn('"called": "' + name + '"', window.callback)
            self.assertIn('"argumentCount": ' + str(len(args)), window.callback)
        for name in ('_optical_setup.choose', '_authorize_optical_setup', 'configure_runtime'):
            with self.assertRaises(NativeBridgeRejected):
                js_bridge_call(window, name, [], 'private')

    def test_recovery_identity_is_read_only_and_explicit_tuple_reaches_real_api(self):
        with tempfile.TemporaryDirectory() as directory:
            api = DesktopApi(directory)
            self.addCleanup(api._images.close)
            self.addCleanup(api._batch.close)
            self.addCleanup(api._reference_exports.close)
            self.addCleanup(api._population_exports.close)
            generation = 'a' * 32
            result = {'status': 'recovered-copy-saved', 'id': '1-' + 'b' * 32,
                      'generation': generation, 'revision': 7}
            api._store = SimpleNamespace(begin_recovery_generation=Mock(),
                recovery_context=Mock(return_value={'generation': generation}), autosave=Mock(return_value=result))
            window = FakeWindow()
            api._set_window(window)
            expose_desktop_api(window, api)
            window.events.before_load.set()
            js_bridge_call(window, 'recovery_context', [], 'context')
            self.assertTrue(window.completed.wait(2))
            self.assertIn(generation, window.callback)
            window.completed.clear()
            document = {'synthetic': True}
            js_bridge_call(window, 'autosave_project', [document, generation, 7], 'autosave')
            self.assertTrue(window.completed.wait(2))
            api._store.autosave.assert_called_once_with(document, generation, 7)
            self.assertIn('"revision": 7', window.callback)
            self.assertIn('"status": "recovered-copy-saved"', window.callback)
            for private in ('_begin_recovery_page', '_store.begin_recovery_generation', 'begin_recovery_generation'):
                with self.assertRaises(NativeBridgeRejected):
                    js_bridge_call(window, private, [], 'invalid')
            api._store.begin_recovery_generation.assert_called_once_with()

    def test_reference_export_is_an_exact_explicit_three_argument_operation(self):
        window = FakeWindow()
        expose_desktop_api(window, stub_api())
        js_bridge_call(window, 'save_reference_export', ['json', 'bounded-text', 'synthetic-token'], 'reference')
        self.assertTrue(window.completed.wait(2))
        self.assertIn('"called": "save_reference_export"', window.callback)
        self.assertIn('"argumentCount": 3', window.callback)
        with self.assertRaises(NativeBridgeRejected):
            js_bridge_call(window, '_reference_exports.save', [], 'private')

    def test_population_export_is_an_exact_explicit_three_argument_operation(self):
        window = FakeWindow()
        expose_desktop_api(window, stub_api())
        js_bridge_call(window, 'save_population_export', ['json', 'bounded-text', 'synthetic-token'], 'population')
        self.assertTrue(window.completed.wait(2))
        self.assertIn('"called": "save_population_export"', window.callback)
        self.assertIn('"argumentCount": 3', window.callback)
        with self.assertRaises(NativeBridgeRejected):
            js_bridge_call(window, '_population_exports.save', [], 'private')

    def test_real_population_export_authenticates_before_decoding(self):
        with tempfile.TemporaryDirectory() as directory:
            api = DesktopApi(directory)
            for owner in (api._images, api._batch, api._reference_exports, api._population_exports):
                self.addCleanup(owner.close)
            api._server = SimpleNamespace(api_token='synthetic-correct-token')
            window = FakeWindow()
            window.create_file_dialog = Mock()
            api._set_window(window)
            expose_desktop_api(window, api)
            with patch('nivram.population_export.validate_payload') as decode:
                js_bridge_call(window, 'save_population_export', ['png', 'SECRET-invalid-body', 'wrong'], 'population')
                self.assertTrue(window.completed.wait(2))
                decode.assert_not_called()
            window.create_file_dialog.assert_not_called()
            self.assertIn('"status": "failed"', window.callback)
            self.assertNotIn('SECRET', window.callback)

    def test_real_reference_export_rejects_wrong_session_before_decode_or_dialog(self):
        with tempfile.TemporaryDirectory() as directory:
            api = DesktopApi(directory)
            self.addCleanup(api._images.close)
            self.addCleanup(api._batch.close)
            self.addCleanup(api._reference_exports.close)
            self.addCleanup(api._population_exports.close)
            api._server = SimpleNamespace(api_token='synthetic-correct-token')
            window = FakeWindow()
            window.create_file_dialog = Mock()
            api._set_window(window)
            expose_desktop_api(window, api)
            with patch('nivram.reference_export.validate_payload') as decode:
                js_bridge_call(window, 'save_reference_export', ['png', 'SECRET-invalid-body', 'wrong'], 'reference')
                self.assertTrue(window.completed.wait(2))
                decode.assert_not_called()
            window.create_file_dialog.assert_not_called()
            self.assertIn('"status": "failed"', window.callback)
            self.assertNotIn('SECRET', window.callback)

    def test_raw_private_save_cannot_create_an_undialogued_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = DesktopApi(root / 'profile')
            window = FakeWindow()
            expose_desktop_api(window, api)
            destination = root / 'not-dialog-selected.nivram'
            document = {'schema': 'nivram/1', 'graph': {'atoms': [], 'bonds': []},
                        'style': {}, 'export': {'width': 800, 'marginMode': 'pixels', 'margin': 2}}
            with self.assertRaises(NativeBridgeRejected):
                js_bridge_call(window, '_store.save', [str(destination), document], 'test')
            self.assertFalse(destination.exists())
            self.assertFalse(window.completed.is_set())
            self.assertIsNone(api._window)
            api._images.close()
            api._batch.close()

    def test_real_dialog_scoped_method_still_dispatches(self):
        with tempfile.TemporaryDirectory() as directory:
            api = DesktopApi(directory)
            window = FakeWindow()
            expose_desktop_api(window, api)
            js_bridge_call(window, 'list_recovery', [], 'test')
            self.assertTrue(window.completed.wait(2))
            self.assertIn("value: '[]'", window.callback)
            api._images.close()
            api._batch.close()

    def test_recovery_inventory_and_acceptance_dispatch_to_real_store_and_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            api = DesktopApi(directory)
            for manager in (api._images, api._batch, api._reference_exports, api._population_exports):
                self.addCleanup(manager.close)
            window = FakeWindow()
            expose_desktop_api(window, api)
            api._begin_recovery_page()
            document = {'schema': 'nivram/1', 'graph': {'atoms': [], 'bonds': []},
                        'style': {}, 'export': {'width': 800, 'marginMode': 'pixels', 'margin': 2}}
            saved = api.autosave_project(document, api.recovery_context()['generation'], 0)
            api._path = Path(directory) / 'previous.nivram'
            js_bridge_call(window, 'recovery_inventory', [], 'inventory')
            self.assertTrue(window.completed.wait(2))
            self.assertIn('"status": "ready"', window.callback)
            self.assertIn(saved['id'], window.callback)
            prepared = api.recover_project(saved['id'])
            self.assertIsNotNone(api._path)
            window.completed.clear()
            js_bridge_call(window, 'accept_recovery', [prepared['recovery_id']], 'accept')
            self.assertTrue(window.completed.wait(2))
            self.assertIn('"status": "accepted"', window.callback)
            self.assertIsNone(api._path)

    def test_private_dotted_and_dunder_names_never_start_vendor_worker(self):
        window = FakeWindow()
        expose_desktop_api(window, stub_api())
        with patch('webview.util.Thread') as worker:
            for name in ('_store.open', '_window.evaluate_js', '_server.shutdown',
                         'save_project.__self__._store.save', '__dict__.get',
                         '__class__.__base__.__subclasses__', 'not_an_operation',
                         'save_project.anything', 'SAVE_PROJECT', '', None, []):
                with self.subTest(name=name), self.assertRaises(NativeBridgeRejected):
                    js_bridge_call(window, name, [], 'test')
            worker.assert_not_called()
        self.assertFalse(window.completed.is_set())

    def test_additional_public_attributes_are_not_implicitly_exposed(self):
        api = stub_api()
        api.run_command = lambda: None
        api.private_store = SimpleNamespace(open=lambda *_args: None)
        window = FakeWindow()
        expose_desktop_api(window, api)
        for name in ('run_command', 'private_store.open'):
            with self.subTest(name=name), self.assertRaises(NativeBridgeRejected):
                js_bridge_call(window, name, [], 'test')

    def test_registration_cannot_be_extended_after_installation(self):
        window = FakeWindow()
        expose_desktop_api(window, stub_api())
        def private_operation():
            return None
        with self.assertRaises(TypeError):
            window.expose(private_operation)
        self.assertNotIn('private_operation', window._functions)

    def test_rich_fallback_object_is_rejected(self):
        window = FakeWindow(stub_api())
        with self.assertRaises(ValueError):
            expose_desktop_api(window, stub_api())
        self.assertEqual(window._functions, {})

    def test_installation_after_page_startup_is_rejected(self):
        for event in ('loaded', '_pywebviewready'):
            window = FakeWindow()
            getattr(window.events, event).set()
            with self.subTest(event=event), self.assertRaises(RuntimeError):
                expose_desktop_api(window, stub_api())
            self.assertEqual(window._functions, {})

    def test_duplicate_installation_is_rejected(self):
        window = FakeWindow()
        expose_desktop_api(window, stub_api())
        with self.assertRaises(RuntimeError):
            expose_desktop_api(window, stub_api())

    def test_missing_application_operation_is_rejected_before_expose(self):
        api = stub_api()
        del api.save_project
        window = FakeWindow()
        with self.assertRaises(ValueError):
            expose_desktop_api(window, api)
        self.assertEqual(window._functions, {})

    def test_changed_vendor_expose_contract_leaves_fail_closed_table(self):
        window = FakeWindow()
        def broken_expose(*functions):
            window._functions[functions[0].__name__] = functions[0]
        window.expose = broken_expose
        with self.assertRaises(RuntimeError):
            expose_desktop_api(window, stub_api())
        with self.assertRaises(NativeBridgeRejected):
            js_bridge_call(window, '__class__.__base__.__subclasses__', [], 'test')


if __name__ == '__main__':
    unittest.main()
