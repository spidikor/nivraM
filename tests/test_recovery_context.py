"""Host-owned page identities through the pinned synchronous vendor event."""
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from webview.event import Event
from nivram.desktop import DesktopApi


class RecoveryContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.api = DesktopApi(temporary.name)
        self.addCleanup(self.api._images.close)
        self.addCleanup(self.api._batch.close)
        self.addCleanup(self.api._reference_exports.close)
        self.generation = 'a' * 32
        self.store = SimpleNamespace(
            begin_recovery_generation=Mock(),
            recovery_context=Mock(side_effect=lambda: {'generation': self.generation}),
            autosave=Mock(return_value={'status': 'superseded'}))
        self.api._store = self.store
        self.window = SimpleNamespace()
        self.window.events = SimpleNamespace(before_load=Event(self.window, True))
        self.api._set_window(self.window)

    def test_context_and_autosave_are_unavailable_until_host_page_event(self):
        with self.assertRaises(ValueError):
            self.api.recovery_context()
        with self.assertRaises(ValueError):
            self.api.autosave_project({}, self.generation, 1)
        self.store.autosave.assert_not_called()
        self.window.events.before_load.set()
        self.store.begin_recovery_generation.assert_called_once_with()
        self.assertEqual(self.api.recovery_context(), {'generation': self.generation})
        self.assertEqual(self.api.autosave_project({'fixture': True}, self.generation, 7), {'status': 'superseded'})
        self.store.autosave.assert_called_once_with({'fixture': True}, self.generation, 7)

    def test_each_native_page_event_resets_context_before_returning(self):
        def reset():
            self.generation = ('b' if self.generation[0] == 'a' else 'c') * 32
        self.store.begin_recovery_generation.side_effect = reset
        self.window.events.before_load.set()
        self.assertEqual(self.api.recovery_context()['generation'], 'b' * 32)
        self.window.events.before_load.set()
        self.assertEqual(self.api.recovery_context()['generation'], 'c' * 32)
        self.assertEqual(self.store.begin_recovery_generation.call_count, 2)

    def test_failed_host_transition_cannot_reuse_previous_page_context(self):
        self.window.events.before_load.set()
        self.store.begin_recovery_generation.side_effect = OSError('synthetic failure')
        # The real vendor event logs handler exceptions and continues. The API
        # must therefore revoke its own ready flag before a fallible reset.
        with self.assertLogs('pywebview', level='ERROR'):
            self.window.events.before_load.set()
        with self.assertRaises(ValueError):
            self.api.recovery_context()
        with self.assertRaises(ValueError):
            self.api.autosave_project({}, self.generation, 1)
        self.store.autosave.assert_not_called()
