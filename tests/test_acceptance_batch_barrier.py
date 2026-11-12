"""Real batch manager with fake preparation, no native window or worker claim."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest

from nivram.acceptance_batch_barrier import SecondPreparationBarrier
from nivram.batch_export import BatchExportManager
import test_batch_preparation as preparation
import test_batch_export as png_fixture


class BatchBarrierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nv-second-response-')
        self.root = Path(self.tmp.name)
        self.output = self.root / 'output'
        self.output.mkdir()
        self.inputs = [self.root / 'first.nivram', self.root / 'second.nivram']
        for path in self.inputs:
            path.write_text(json.dumps(preparation.PROJECT), encoding='utf-8')
        self.service = preparation.PreparationService()
        self.manager = BatchExportManager(preparation_service=self.service)
        self.barrier = SecondPreparationBarrier(self.manager, hold_seconds=2)
        self.threads, self.results, self.errors = [], [], []
        self.batch = self.manager._begin_selected(self.inputs, self.output, preparation.SETTINGS)

    def tearDown(self):
        self.manager.cancel(self.batch['sessionId'])
        self.service.release.set()
        self.barrier.close()
        for thread in self.threads:
            thread.join(2)
            self.assertFalse(thread.is_alive(), 'Test-owned preparation call must settle.')
        self.manager.close()
        self.tmp.cleanup()

    def first(self):
        self.barrier.attach()
        result = self.manager.prepare(self.batch['sessionId'], self.batch['items'][0]['id'])
        self.assertEqual(result['status'], 'ready')
        saved = self.manager.write_png(self.batch['sessionId'], self.batch['items'][0]['id'], png_fixture.encoded())
        self.assertEqual(saved['status'], 'exported')
        return self.output / saved['item']['outputName']

    def second(self):
        def call():
            try:
                self.results.append(self.manager.prepare(self.batch['sessionId'], self.batch['items'][1]['id']))
            except Exception as error:
                self.errors.append(error)
        thread = threading.Thread(target=call)
        self.threads.append(thread)
        thread.start()
        self.assertTrue(self.barrier.held.wait(1))
        return thread

    def test_real_manager_rejects_late_preparation_and_keeps_completed_png_and_manifest(self):
        path = self.first()
        before = path.read_bytes()
        thread = self.second()
        self.assertEqual(len(self.service.calls), 2)
        self.assertTrue(thread.is_alive())
        self.assertEqual([item['status'] for item in self.manager._session['items']], ['exported', 'pending'])
        self.manager.cancel(self.batch['sessionId'])
        self.barrier.release_after_cancel()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.errors, [])
        self.assertEqual(self.results[0]['status'], 'cancelled')
        self.assertNotIn('scene', self.results[0])
        self.barrier.assert_complete()
        manifest = self.manager._session['manifest']
        previous_manifest = manifest.read_bytes()
        late = self.manager.write_png(self.batch['sessionId'], self.batch['items'][1]['id'], png_fixture.encoded())
        self.assertEqual(late['status'], 'cancelled')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(manifest.read_bytes(), previous_manifest)
        self.assertEqual({file.name for file in self.output.iterdir()}, {path.name, manifest.name})
        self.assertTrue(self.barrier.close()['shutdownConfirmed'])
        self.assertNotIn('prepare', self.manager.__dict__)
        self.assertNotIn('prepare_batch', self.service.__dict__)

    def test_release_without_real_cancellation_is_rejected(self):
        self.first()
        self.second()
        with self.assertRaises(AssertionError):
            self.barrier.release_after_cancel()
        self.assertFalse(self.barrier.release.is_set())

    def test_cleanup_release_cannot_claim_intentional_cancel(self):
        self.first()
        thread = self.second()
        result = self.barrier.close()
        thread.join(1)
        self.assertTrue(result['shutdownConfirmed'])
        self.assertEqual(self.results[0]['status'], 'ready')
        self.assertFalse(result['cancelObservedBeforeRelease'])
        with self.assertRaises(AssertionError):
            self.barrier.assert_complete()

    def test_hold_timeout_cannot_claim_late_cancel_success(self):
        self.barrier = SecondPreparationBarrier(self.manager, hold_seconds=.1)
        self.first()
        thread = self.second()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(self.barrier.snapshot()['holdTimedOut'])
        self.assertEqual(len(self.errors), 1)
        self.assertIsInstance(self.errors[0], TimeoutError)
        with self.assertRaises(AssertionError):
            self.barrier.assert_complete()

    def test_release_observed_at_hold_deadline_is_rejected(self):
        class Clock:
            value = 0
            def __call__(self): return self.value
        clock = Clock()
        self.barrier = SecondPreparationBarrier(self.manager, hold_seconds=2, clock=clock)
        self.first()
        thread = self.second()
        self.manager.cancel(self.batch['sessionId'])
        clock.value = 2
        self.barrier.release_after_cancel()
        thread.join(1)
        self.assertTrue(self.barrier.snapshot()['holdTimedOut'])
        self.assertEqual(len(self.errors), 1)
        with self.assertRaises(AssertionError):
            self.barrier.assert_complete()

    def test_cleanup_does_not_report_shutdown_while_first_original_call_is_active(self):
        self.service.release.clear()
        self.barrier.attach()
        thread = threading.Thread(target=lambda: self.manager.prepare(self.batch['sessionId'], self.batch['items'][0]['id']))
        self.threads.append(thread)
        thread.start()
        self.assertTrue(self.service.started.wait(1))
        result = self.barrier.close(timeout=0)
        self.assertFalse(result['shutdownConfirmed'])
        self.assertFalse(result['methodsRestored'])
        self.manager.cancel(self.batch['sessionId'])
        self.service.release.set()
        thread.join(1)
        self.assertTrue(self.barrier.close()['shutdownConfirmed'])

    def test_invalid_bounds_and_second_attach_are_rejected(self):
        for bound in (True, 0, -1, 31, float('inf'), float('nan'), '1'):
            with self.subTest(bound=bound), self.assertRaises(ValueError):
                SecondPreparationBarrier(self.manager, hold_seconds=bound)
        self.barrier.attach()
        with self.assertRaises(RuntimeError):
            self.barrier.attach()
        for bound in (True, -1, 3, float('nan')):
            with self.subTest(bound=bound), self.assertRaises(ValueError):
                self.barrier.close(bound)

    def test_closing_restores_exact_preexisting_instance_methods(self):
        service_method, batch_method = self.service.prepare_batch, self.manager.prepare
        self.service.prepare_batch, self.manager.prepare = service_method, batch_method
        self.barrier = SecondPreparationBarrier(self.manager)
        self.barrier.attach()
        self.assertTrue(self.barrier.close()['shutdownConfirmed'])
        self.assertIs(self.service.prepare_batch, service_method)
        self.assertIs(self.manager.prepare, batch_method)

    def test_replaced_observer_is_not_overwritten_by_cleanup(self):
        self.barrier.attach()
        replacement = lambda *_: None
        self.manager.prepare = replacement
        with self.assertRaises(RuntimeError):
            self.barrier.close()
        self.assertIs(self.manager.prepare, replacement)
        self.manager.prepare = self.barrier.observed_prepare

    def test_captured_callback_cannot_enter_after_confirmed_cleanup(self):
        self.barrier.attach()
        callback = self.manager.prepare
        self.assertTrue(self.barrier.close()['shutdownConfirmed'])
        with self.assertRaises(RuntimeError):
            callback(self.batch['sessionId'], self.batch['items'][0]['id'])
        self.assertEqual(self.service.calls, [])

    def test_callback_admission_is_atomic_with_active_registration(self):
        original_lock = self.barrier._lock
        paused, proceed = threading.Event(), threading.Event()
        caller = {'id': None, 'paused': False}
        class PauseAfterFirstRegion:
            def __enter__(self):
                original_lock.acquire()
                return self
            def __exit__(self, *_):
                should_pause = threading.get_ident() == caller['id'] and not caller['paused']
                if should_pause:
                    caller['paused'] = True
                original_lock.release()
                if should_pause:
                    paused.set()
                    if not proceed.wait(2):
                        raise RuntimeError('Test-owned callback pause was not released.')
        self.barrier._lock = PauseAfterFirstRegion()
        self.barrier.attach()
        def call():
            caller['id'] = threading.get_ident()
            try:
                self.results.append(self.manager.prepare(self.batch['sessionId'], self.batch['items'][0]['id']))
            except Exception as error:
                self.errors.append(error)
        thread = threading.Thread(target=call)
        self.threads.append(thread)
        thread.start()
        try:
            self.assertTrue(paused.wait(1))
            result = self.barrier.close(timeout=0)
            self.assertFalse(result['shutdownConfirmed'])
            self.assertFalse(result['methodsRestored'])
        finally:
            proceed.set()
            thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(self.barrier.close()['shutdownConfirmed'])


if __name__ == '__main__':
    unittest.main()
