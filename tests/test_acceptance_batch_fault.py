"""Real manager/filesystem fault boundaries with fake preparation and PNG bytes."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from nivram import batch_export
from nivram.acceptance_batch_fault import PublicationFaultObserver, MANIFEST_ERROR
import test_batch_preparation as preparation
import test_batch_export as png_fixture


class PublicationFaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nv-uncertain-')
        self.root = Path(self.tmp.name)
        self.output = self.root / 'out'
        self.output.mkdir()
        self.inputs = [self.root / name for name in ('a1.nivram', 'a2.nivram')]
        for path in self.inputs:
            path.write_text(json.dumps(preparation.PROJECT), encoding='utf-8')
        self.service = preparation.PreparationService()
        self.manager = batch_export.BatchExportManager(preparation_service=self.service)
        self.fault = None
        self.threads = []

    def tearDown(self):
        self.service.release.set()
        for thread in self.threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        if self.fault:
            self.assertTrue(self.fault.close()['shutdownConfirmed'])
        self.manager.close()
        self.tmp.cleanup()

    def ready(self, mode):
        self.fault = PublicationFaultObserver(self.manager, self.output, mode)
        self.fault.attach()
        self.batch = self.manager._begin_selected(self.inputs, self.output, preparation.SETTINGS)
        self.assertEqual(self.manager.prepare(self.batch['sessionId'], self.batch['items'][0]['id'])['status'], 'ready')

    def write(self):
        return self.manager.write_png(self.batch['sessionId'], self.batch['items'][0]['id'], png_fixture.encoded())

    def test_lost_write_ack_follows_actual_png_then_real_cancel_updates_manifest(self):
        self.ready('lost-ack')
        with self.assertRaisesRegex(RuntimeError, 'acknowledgement unavailable'):
            self.write()
        self.assertEqual((self.output / 'a1.png').read_bytes(), png_fixture.png())
        self.assertEqual(self.manager._session['status'], 'running')
        receipt = self.manager.cancel(self.batch['sessionId'])
        self.assertEqual(receipt['status'], 'cancelled')
        self.assertEqual([item['status'] for item in receipt['items']], ['exported', 'cancelled'])
        self.assertEqual(json.loads((self.output / receipt['manifestName']).read_bytes()),
                         {'schema': 'nivram-batch-export/1', **receipt})
        self.fault.assert_complete()

    def test_uncertain_manifest_retains_real_atomic_commit_and_distinct_terminal_memory(self):
        self.ready('manifest-uncertain')
        original_atomic = batch_export.atomic_write
        result = self.write()
        self.assertIs(batch_export.atomic_write, original_atomic)
        self.assertEqual(result['status'], 'exported')
        self.assertEqual(result['manifestError'], MANIFEST_ERROR)
        receipt = self.manager.cancel(self.batch['sessionId'])
        disk_bytes = (self.output / receipt['manifestName']).read_bytes()
        self.assertEqual(disk_bytes, self.fault.committed_manifest)
        disk = json.loads(disk_bytes)
        self.assertEqual(disk['status'], 'running')
        self.assertIsNone(disk['manifestError'])
        self.assertEqual([item['status'] for item in disk['items']], ['exported', 'pending'])
        self.assertEqual(receipt['status'], 'stopped')
        self.assertEqual([item['status'] for item in receipt['items']], ['exported', 'cancelled'])
        self.assertEqual((self.output / 'a1.png').read_bytes(), png_fixture.png())
        self.fault.assert_complete()

    def test_late_prepare_and_write_do_not_change_disk_or_memory_after_restoration(self):
        self.ready('manifest-uncertain')
        self.write()
        receipt = self.manager.cancel(self.batch['sessionId'])
        self.fault.assert_complete()
        self.assertTrue(self.fault.close()['shutdownConfirmed'])
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        second = receipt['items'][1]
        self.assertEqual(self.manager.prepare(receipt['sessionId'], second['id']), {'status': 'cancelled', 'item': second})
        self.assertEqual(self.manager.write_png(receipt['sessionId'], second['id'], png_fixture.encoded()),
                         {'status': 'cancelled', 'item': second})
        self.assertEqual(self.manager._receipt(self.manager._session), receipt)
        self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()}, before)
        self.assertTrue(all(item['_bytes'] is None for item in self.manager._session['items']))

    def test_failure_before_atomic_commit_is_not_reported_as_injected_commit(self):
        self.ready('manifest-uncertain')
        original = batch_export.atomic_write
        with patch.object(batch_export, 'atomic_write', side_effect=PermissionError('synthetic denial')) as failing:
            self.write()
            self.assertIs(batch_export.atomic_write, failing)
        self.assertIs(batch_export.atomic_write, original)
        self.manager.cancel(self.batch['sessionId'])
        self.assertEqual(self.fault.snapshot()['atomicCommits'], 0)
        self.assertEqual(self.fault.snapshot()['faultCount'], 0)
        with self.assertRaises(AssertionError): self.fault.assert_complete()

    def test_invalid_png_does_not_create_a_successful_fault_observation(self):
        self.ready('lost-ack')
        with self.assertRaises(AssertionError):
            self.manager.write_png(self.batch['sessionId'], self.batch['items'][0]['id'], 'invalid')
        self.manager.cancel(self.batch['sessionId'])
        self.assertEqual(self.fault.snapshot()['faultCount'], 0)
        self.assertFalse((self.output / 'a1.png').exists())
        with self.assertRaises(AssertionError): self.fault.assert_complete()

    def test_missing_cancel_and_unexpected_finish_cannot_qualify(self):
        self.ready('manifest-uncertain')
        self.write()
        with self.assertRaises(AssertionError): self.fault.assert_complete()
        self.manager.cancel(self.batch['sessionId'])
        self.fault.assert_complete()
        self.manager.finish(self.batch['sessionId'])
        with self.assertRaises(AssertionError): self.fault.assert_complete()

    def test_close_waits_for_active_callback_before_restoring_methods(self):
        self.ready('lost-ack')
        entered, release = threading.Event(), threading.Event()
        original = self.fault.originals['finish']
        def held_finish(*args):
            entered.set()
            if not release.wait(1): raise TimeoutError('test release missing')
            return None
        self.fault.originals['finish'] = held_finish
        thread = threading.Thread(target=lambda: self.manager.finish(self.batch['sessionId']))
        self.threads.append(thread)
        thread.start()
        self.assertTrue(entered.wait(1))
        try:
            self.assertFalse(self.fault.close(timeout=0)['shutdownConfirmed'])
            self.assertIn('prepare', self.manager.__dict__)
            with self.assertRaises(RuntimeError): self.manager.cancel(self.batch['sessionId'])
        finally:
            release.set()
            thread.join(1)
            self.fault.originals['finish'] = original
        self.assertTrue(self.fault.close()['shutdownConfirmed'])
        self.assertNotIn('prepare', self.manager.__dict__)

    def test_invalid_modes_bounds_and_duplicate_attach_fail(self):
        with self.assertRaises(ValueError): PublicationFaultObserver(self.manager, self.output, 'unknown')
        self.ready('lost-ack')
        with self.assertRaises(RuntimeError): self.fault.attach()
        for value in (True, -1, 3, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError): self.fault.close(value)


if __name__ == '__main__':
    unittest.main()
