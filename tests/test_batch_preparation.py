"""Batch preparation delegates native work and rejects cancelled late results."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from nivram.batch_export import BatchExportManager
from nivram.import_preparation import ImportPreparationError


SETTINGS = {'style': {}, 'export': {'width': 32, 'marginMode': 'pixels', 'margin': 2}}
PROJECT = {'schema': 'nivram/1', 'graph': {
    'atoms': [{'id': 'a0', 'symbol': 'O', 'x': 0, 'y': 0}], 'bonds': []}}


class PreparationService:
    def __init__(self, *, blocked=False):
        self.started = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()
        self.calls = []
        self.shutdown_calls = 0

    def prepare_batch(self, text, extension, settings, *, cancellation_token=None):
        self.calls.append((text, extension, copy.deepcopy(settings), cancellation_token))
        self.started.set()
        if not self.release.wait(3):
            raise RuntimeError('Fixture was not released.')
        # Deliberately return after cancellation to test the adoption boundary.
        return {'project': copy.deepcopy(PROJECT), 'graph': copy.deepcopy(PROJECT['graph']),
                'scene': {'svg': '<svg/>', 'positions': []}}

    def shutdown(self, *, timeout_seconds=2.0):
        self.shutdown_calls += 1
        self.release.set()
        return True


class BatchPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.destination = self.root / 'outputs'
        self.destination.mkdir()
        self.source = self.root / 'input.nivram'
        self.source.write_text(json.dumps(PROJECT), encoding='utf-8')
        self.managers = []
        self.services = []
        self.threads = []

    def tearDown(self):
        for service in self.services:
            service.release.set()
        for thread in self.threads:
            thread.join(3)
        for manager in self.managers:
            manager.close()
        self.temp.cleanup()

    def manager(self, *, blocked=False):
        service = PreparationService(blocked=blocked)
        manager = BatchExportManager(preparation_service=service)
        self.managers.append(manager)
        self.services.append(service)
        return manager, service

    def begin(self, manager):
        return manager._begin_selected([self.source], self.destination, SETTINGS)

    def prepare_async(self, manager, batch):
        result = []
        thread = threading.Thread(target=lambda: result.append(
            manager.prepare(batch['sessionId'], batch['items'][0]['id'])))
        self.threads.append(thread)
        thread.start()
        return thread, result

    def test_host_settings_and_prepare_never_enter_native_chemistry(self):
        manager, service = self.manager()
        selected = {**SETTINGS, 'profile': {'id': 'a' * 32, 'revision': 1, 'name': ' Profile '}}
        with patch('nivram.chemistry.graph_to_mol', side_effect=AssertionError('parent native graph')), \
                patch('nivram.chemistry.import_structure', side_effect=AssertionError('parent native import')), \
                patch('nivram.scene.render_scene', side_effect=AssertionError('parent native rendering')):
            batch = manager._begin_selected([self.source], self.destination, selected)
            result = manager.prepare(batch['sessionId'], batch['items'][0]['id'])
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(len(service.calls), 1)
        self.assertEqual(service.calls[0][0], self.source.read_text(encoding='utf-8'))
        self.assertEqual(service.calls[0][1], '.nivram')
        self.assertEqual(service.calls[0][2]['profile']['name'], 'Profile')
        self.assertIsNotNone(service.calls[0][3])

    def test_mol_text_is_passed_to_service_without_parent_native_validation(self):
        manager, service = self.manager()
        source = self.root / 'input.mol'
        source.write_text('Opaque MOL test fixture, validated only by injected service.', encoding='utf-8')
        with patch('nivram.chemistry.import_structure', side_effect=AssertionError('parent native import')), \
                patch('nivram.scene.render_scene', side_effect=AssertionError('parent native rendering')):
            batch = manager._begin_selected([source], self.destination, SETTINGS)
            result = manager.prepare(batch['sessionId'], batch['items'][0]['id'])
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(service.calls[0][0], source.read_text(encoding='utf-8'))
        self.assertEqual(service.calls[0][1], '.mol')

    def test_cancel_returns_while_preparing_and_late_result_is_not_adopted(self):
        manager, service = self.manager(blocked=True)
        batch = self.begin(manager)
        thread, result = self.prepare_async(manager, batch)
        self.assertTrue(service.started.wait(1))
        cancelled = []
        cancel_thread = threading.Thread(target=lambda: cancelled.append(manager.cancel(batch['sessionId'])))
        self.threads.append(cancel_thread)
        cancel_thread.start()
        cancel_thread.join(1)
        self.assertFalse(cancel_thread.is_alive(), 'cancel must not wait for child preparation')
        self.assertEqual(cancelled[0]['status'], 'cancelled')
        self.assertTrue(service.calls[0][3].cancelled)
        service.release.set()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0]['status'], 'cancelled')
        self.assertNotIn('scene', result[0])
        self.assertIsNone(manager._session['active'])
        self.assertIsNone(manager._session['items'][0]['_bytes'])

    def test_cancelled_old_session_cannot_adopt_into_new_session(self):
        manager, service = self.manager(blocked=True)
        old = self.begin(manager)
        thread, result = self.prepare_async(manager, old)
        self.assertTrue(service.started.wait(1))
        manager.cancel(old['sessionId'])
        current = self.begin(manager)
        service.release.set()
        thread.join(1)
        self.assertEqual(result[0]['status'], 'cancelled')
        self.assertEqual(manager._session['id'], current['sessionId'])
        self.assertEqual(manager._session['items'][0]['status'], 'pending')
        self.assertIsNone(manager._session['active'])

    def test_service_token_cancellation_finalizes_session_and_releases_sources(self):
        manager, service = self.manager(blocked=True)
        batch = self.begin(manager)
        thread, result = self.prepare_async(manager, batch)
        self.assertTrue(service.started.wait(1))
        service.calls[0][3].cancel()
        service.release.set()
        thread.join(1)
        self.assertEqual(result[0]['status'], 'cancelled')
        self.assertEqual(result[0]['item']['status'], 'cancelled')
        self.assertEqual(manager._session['status'], 'cancelled')
        self.assertIsNone(manager._session['active'])
        self.assertIsNone(manager._session['items'][0]['_bytes'])

    def test_service_failure_codes_remain_safe_and_release_reservation(self):
        for code in ('invalid_input', 'empty_drawing', 'render_failed', 'busy',
                     'unavailable', 'timed_out', 'worker_failed'):
            with self.subTest(code=code):
                manager, service = self.manager()
                batch = self.begin(manager)
                with patch.object(service, 'prepare_batch', side_effect=ImportPreparationError(code)):
                    result = manager.prepare(batch['sessionId'], batch['items'][0]['id'])
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['item']['error']['code'], code)
                self.assertIsNone(manager._session['active'])
                self.assertIsNone(manager._session['items'][0]['_bytes'])
                self.assertNotIn('scene', result)

    def test_preparation_reservation_rejects_duplicate_and_early_publication(self):
        manager, service = self.manager(blocked=True)
        batch = self.begin(manager)
        item_id = batch['items'][0]['id']
        thread, result = self.prepare_async(manager, batch)
        self.assertTrue(service.started.wait(1))
        with self.assertRaises(ValueError):
            manager.prepare(batch['sessionId'], item_id)
        with self.assertRaises(ValueError):
            manager.write_png(batch['sessionId'], item_id, '')
        with self.assertRaises(ValueError):
            manager.fail(batch['sessionId'], item_id, 'render_failed')
        service.release.set()
        thread.join(1)
        self.assertEqual(result[0]['status'], 'ready')

    def test_close_cancels_active_preparation_but_does_not_shutdown_shared_service(self):
        manager, service = self.manager(blocked=True)
        batch = self.begin(manager)
        thread, result = self.prepare_async(manager, batch)
        self.assertTrue(service.started.wait(1))
        manager.close()
        self.assertTrue(service.calls[0][3].cancelled)
        self.assertEqual(service.shutdown_calls, 0)
        service.release.set()
        thread.join(1)
        self.assertEqual(result[0]['status'], 'cancelled')

    def test_close_shuts_down_owned_service(self):
        service = PreparationService()
        self.services.append(service)
        with patch('nivram.batch_export.ImportPreparationService', return_value=service):
            manager = BatchExportManager()
        manager.close()
        self.assertEqual(service.shutdown_calls, 1)


if __name__ == '__main__':
    unittest.main()
