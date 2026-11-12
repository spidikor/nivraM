"""Shared real storage behind bridge autosave and close review; synthetic UI."""
import copy
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from webview.event import Event
from nivram.desktop import DesktopApi
from nivram.lifecycle import CloseCoordinator
from nivram import persistence


def project(width):
    return {'schema': 'nivram/1', 'graph': {'atoms': [], 'bonds': []}, 'style': {},
            'export': {'width': width, 'marginMode': 'percent', 'margin': 2}}


class Window:
    def __init__(self):
        self.events = SimpleNamespace(closed=threading.Event(), before_load=Event(self, True))
        self.messages = []
        self.snapshot = None

    def evaluate_js(self, expression):
        return copy.deepcopy(self.snapshot)

    def create_confirmation_dialog(self, title, message):
        self.messages.append(message)
        return False

    def destroy(self):
        raise AssertionError('A declined close must not destroy the editor')


class RecoveryProducerIntegrationTests(unittest.TestCase):
    def test_newer_close_recovery_prevents_older_bridge_autosave_from_taking_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            api = DesktopApi(directory)
            self.addCleanup(api._images.close)
            self.addCleanup(api._batch.close)
            self.addCleanup(api._reference_exports.close)
            window = Window()
            api._set_window(window)
            window.events.before_load.set()
            generation = api.recovery_context()['generation']
            window.snapshot = {'dirty': True, 'revision': 2, 'recoveryGeneration': generation,
                               'document': project(2000)}
            close = CloseCoordinator(window, api._store, evaluation_timeout=1)
            entered, release = threading.Event(), threading.Event()
            original = persistence.validate_project
            result = {}
            def validate(document):
                value = original(document)
                if threading.current_thread() is older and not entered.is_set():
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError('Synthetic interleaving was not released')
                return value
            def submit():
                try:
                    result['receipt'] = api.autosave_project(project(1000), generation, 1)
                except BaseException as error:
                    result['error'] = error
            older = threading.Thread(target=submit, daemon=True)
            # Warm real validation before beginning the bounded interleaving.
            original(project(1000))
            with patch.object(persistence, 'validate_project', side_effect=validate):
                try:
                    older.start()
                    self.assertTrue(entered.wait(1))
                    close._review_close()
                    self.assertIn('A recovery copy was saved.', window.messages[0])
                    current = api.list_recovery()[0]
                    self.assertEqual(api._store.recover(current['id'])['document'], project(2000))
                finally:
                    release.set()
                    older.join(3)
                self.assertFalse(older.is_alive())
                if 'error' in result:
                    raise result['error']
            self.assertEqual(result['receipt'], {'status': 'superseded', 'generation': generation, 'revision': 1})
            self.assertEqual(api.list_recovery(), [current])
            self.assertFalse(close._pending)
            self.assertFalse(window.events.closed.is_set())

    def test_host_reload_revokes_old_generation_and_allows_fresh_revision_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            api = DesktopApi(directory)
            self.addCleanup(api._images.close)
            self.addCleanup(api._batch.close)
            self.addCleanup(api._reference_exports.close)
            window = Window()
            api._set_window(window)
            window.events.before_load.set()
            first = api.recovery_context()['generation']
            api.autosave_project(project(1000), first, 50)
            window.events.before_load.set()
            current = api.recovery_context()['generation']
            self.assertNotEqual(first, current)
            receipt = api.autosave_project(project(2000), current, 0)
            before = {p.name: p.read_bytes() for p in api._store.recovery_dir.iterdir()}
            self.assertEqual(api.autosave_project(project(3000), first, 51)['status'], 'superseded')
            self.assertEqual(before, {p.name: p.read_bytes() for p in api._store.recovery_dir.iterdir()})
            self.assertEqual(api.list_recovery()[0]['id'], receipt['id'])
            self.assertEqual(api._store.recover(receipt['id'])['document'], project(2000))
