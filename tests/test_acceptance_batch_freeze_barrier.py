"""Bounded observer lifecycle tests with a fake manager, never a native claim."""
import copy
import threading
import unittest

from nivram.acceptance_batch_freeze_barrier import PreparedResponseBarrier


class Manager:
    def __init__(self):
        self._lock = threading.RLock()
        self._session = {'status': 'running', 'cancelled': threading.Event()}
        self.result = {'status': 'ready', 'item': {'id': 'a'*32}, 'project': {'export': {'width': 640}},
                       'graph': {}, 'scene': {'paths': []}}
    def prepare(self, *args):
        return self.result


class FreezeBarrierTests(unittest.TestCase):
    def setUp(self):
        self.manager = Manager()
        self.barrier = PreparedResponseBarrier(self.manager, hold_seconds=2)
        self.threads, self.values, self.errors = [], [], []

    def tearDown(self):
        self.barrier.close()
        for thread in self.threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())

    def second(self):
        self.barrier.attach()
        self.manager.prepare()
        def call():
            try: self.values.append(self.manager.prepare())
            except Exception as error: self.errors.append(error)
        thread = threading.Thread(target=call)
        self.threads.append(thread)
        thread.start()
        return thread

    def test_exact_original_object_returned_after_external_changes_and_method_restored(self):
        expected = copy.deepcopy(self.manager.result)
        thread = self.second()
        self.assertTrue(self.barrier.held.wait(1))
        self.manager.external_file = b'edited'
        self.barrier.release_after_edits()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.errors, [])
        self.assertIs(self.values[0], self.manager.result)
        self.assertEqual(self.values[0], expected)
        self.barrier.assert_complete()
        self.assertTrue(self.barrier.close()['shutdownConfirmed'])
        self.assertNotIn('prepare', self.manager.__dict__)

    def test_cancelled_original_session_cannot_be_reported_as_running_release(self):
        self.second()
        self.assertTrue(self.barrier.held.wait(1))
        self.manager._session['cancelled'].set()
        with self.assertRaises(AssertionError): self.barrier.release_after_edits()
        self.assertFalse(self.barrier.snapshot()['releasedWhileRunning'])

    def test_cleanup_release_does_not_qualify_success(self):
        self.second()
        self.assertTrue(self.barrier.held.wait(1))
        self.assertTrue(self.barrier.close()['shutdownConfirmed'])
        with self.assertRaises(AssertionError): self.barrier.assert_complete()

    def test_mutated_response_is_rejected_after_release(self):
        thread = self.second()
        self.assertTrue(self.barrier.held.wait(1))
        self.manager.result['project']['export']['width'] = 320
        self.barrier.release_after_edits()
        thread.join(1)
        self.assertEqual(self.values, [])
        self.assertEqual(len(self.errors), 1)
        self.assertIsInstance(self.errors[0], AssertionError)
        with self.assertRaises(AssertionError): self.barrier.assert_complete()

    def test_second_original_failure_never_creates_held_ready_observation(self):
        self.manager.result = {'status': 'failed'}
        thread = self.second()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertFalse(self.barrier.held.is_set())
        self.assertEqual(len(self.errors), 1)

    def test_timeout_is_a_failure_and_observer_still_restores(self):
        self.barrier = PreparedResponseBarrier(self.manager, hold_seconds=.05)
        thread = self.second()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(self.errors), 1)
        self.assertIsInstance(self.errors[0], TimeoutError)
        self.assertTrue(self.barrier.snapshot()['holdTimedOut'])
        self.assertTrue(self.barrier.close()['shutdownConfirmed'])
        with self.assertRaises(AssertionError): self.barrier.assert_complete()

    def test_early_double_release_and_extra_calls_are_rejected(self):
        with self.assertRaises(AssertionError): self.barrier.release_after_edits()
        thread = self.second()
        self.assertTrue(self.barrier.held.wait(1))
        with self.assertRaises(RuntimeError): self.manager.prepare()
        self.barrier.release_after_edits()
        with self.assertRaises(AssertionError): self.barrier.release_after_edits()
        thread.join(1)
        with self.assertRaises(RuntimeError): self.manager.prepare()

    def test_invalid_bounds_and_duplicate_attach_fail(self):
        for value in (True, 0, -1, 31, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError): PreparedResponseBarrier(self.manager, hold_seconds=value)
        for value in (True, -1, 3, float('nan')):
            with self.subTest(value=value), self.assertRaises(ValueError): self.barrier.close(value)
        self.barrier.attach()
        with self.assertRaises(RuntimeError): self.barrier.attach()


if __name__ == '__main__':
    unittest.main()
