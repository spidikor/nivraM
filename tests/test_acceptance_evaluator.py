import threading
import time
import unittest

from nivram.acceptance_evaluator import BoundedEvaluator, EvaluationTimeout


class AcceptanceEvaluatorTests(unittest.TestCase):
    def test_result_and_none_propagate_without_exposing_payloads(self):
        events = []
        evaluator = BoundedEvaluator(lambda expression: {'secret': expression} if expression else None, events.append)
        try:
            self.assertEqual(evaluator('private-chemical-expression'), {'secret': 'private-chemical-expression'})
            self.assertIsNone(evaluator(''))
            self.assertEqual([row['status'] for row in events], ['waiting', 'completed'] * 2)
            self.assertNotIn('private', repr(events))
            self.assertEqual([row['sequence'] for row in events], [1, 1, 2, 2])
        finally:
            self.assertTrue(evaluator.close()['workerExited'])

    def test_async_callback_contract_is_preserved_without_reporting_values(self):
        callbacks, values, events = [], [], []
        def start(_, callback=None):
            callbacks.append(callback)
            return None
        evaluator = BoundedEvaluator(start, events.append)
        try:
            self.assertIsNone(evaluator('async-synthetic', values.append))
            callbacks[0]({'synthetic': 'result'})
            self.assertEqual(values, [{'synthetic': 'result'}])
            self.assertNotIn('synthetic', repr(events))
            evaluator.close()
            callbacks[0]({'late': 'ignored'})
            self.assertEqual(values, [{'synthetic': 'result'}])
        finally:
            evaluator.close()

    def test_original_exception_propagates_after_failure_observation(self):
        failure = ValueError('private details')
        events = []
        def fail(_):
            raise failure
        evaluator = BoundedEvaluator(fail, events.append)
        try:
            with self.assertRaises(ValueError) as caught:
                evaluator('synthetic')
            self.assertIs(caught.exception, failure)
            self.assertEqual(events[-1]['status'], 'failed')
            self.assertEqual(events[-1]['errorKind'], 'ValueError')
            self.assertNotIn('private details', repr(events))
        finally:
            self.assertTrue(evaluator.close()['workerExited'])

    def test_blocked_call_times_out_and_late_result_never_replaces_failure(self):
        release, entered = threading.Event(), threading.Event()
        events, called = [], []
        def blocked(expression):
            called.append(expression)
            entered.set()
            release.wait(5)
            return True
        evaluator = BoundedEvaluator(blocked, events.append, timeout=.05)
        try:
            started = time.monotonic()
            with self.assertRaises(EvaluationTimeout):
                evaluator('save', stage='native-save-as')
            self.assertTrue(entered.is_set())
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(events[-1]['status'], 'timed-out')
            self.assertEqual(events[-1]['outcome'], 'indeterminate')
            with self.assertRaisesRegex(RuntimeError, 'no further JavaScript'):
                evaluator('cancel')
            snapshot = evaluator.close(timeout=.02)
            self.assertFalse(snapshot['workerExited'])
            self.assertTrue(snapshot['outstandingCallMayComplete'])
            release.set()
            self.assertTrue(evaluator.close(timeout=1)['workerExited'])
            self.assertEqual(called, ['save'])
            self.assertEqual(len(events), 2)
            self.assertEqual(events[-1]['status'], 'timed-out')
        finally:
            release.set()
            evaluator.close(timeout=1)

    def test_concurrent_submission_is_rejected_without_dispatch(self):
        entered, release = threading.Event(), threading.Event()
        called, errors = [], []
        def blocked(expression):
            called.append(expression)
            entered.set()
            release.wait(2)
            return 1
        evaluator = BoundedEvaluator(blocked, lambda _: None, timeout=2)
        def first():
            try:
                evaluator('first')
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=first)
        try:
            thread.start()
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(RuntimeError, 'already outstanding'):
                evaluator('second')
            release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(called, ['first'])
            self.assertEqual(errors, [])
        finally:
            release.set()
            thread.join(2)
            evaluator.close(timeout=1)

    def test_closed_evaluator_rejects_work(self):
        called = []
        evaluator = BoundedEvaluator(called.append, lambda _: None)
        self.assertTrue(evaluator.close()['workerExited'])
        with self.assertRaises(RuntimeError):
            evaluator('never')
        self.assertEqual(called, [])

    def test_failed_receipt_preserves_original_evaluation_exception(self):
        failure = ValueError('synthetic original')
        def fail(_):
            raise failure
        def observe(row):
            if row['status'] == 'failed':
                raise OSError('synthetic report failure')
        evaluator = BoundedEvaluator(fail, observe)
        try:
            with self.assertRaises(ValueError) as caught:
                evaluator('synthetic')
            self.assertIs(caught.exception, failure)
        finally:
            self.assertEqual(evaluator.close()['reportingErrorKinds'], ['OSError'])

    def test_failed_timeout_receipt_still_poisons_evaluator(self):
        release = threading.Event()
        def observe(row):
            if row['status'] == 'timed-out':
                raise OSError('synthetic report failure')
        evaluator = BoundedEvaluator(lambda _: release.wait(3), observe, timeout=.03)
        try:
            with self.assertRaises(EvaluationTimeout):
                evaluator('synthetic')
            with self.assertRaises(RuntimeError):
                evaluator('never')
        finally:
            release.set()
            self.assertEqual(evaluator.close(timeout=1)['reportingErrorKinds'], ['OSError'])

    def test_checkpoint_time_consumes_budget_and_can_prevent_dispatch(self):
        called, events = [], []
        def observe(row):
            events.append(row)
            if row['status'] == 'waiting':
                time.sleep(.03)
        evaluator = BoundedEvaluator(called.append, observe, timeout=.01)
        try:
            with self.assertRaises(EvaluationTimeout):
                evaluator('never')
            self.assertEqual(called, [])
            self.assertEqual(events[-1]['outcome'], 'not-submitted')
        finally:
            self.assertTrue(evaluator.close()['workerExited'])

    def test_invalid_deadlines_or_stage_never_dispatch(self):
        for timeout in (True, 0, -1, float('inf'), float('nan'), '1'):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                BoundedEvaluator(lambda _: None, lambda _: None, timeout=timeout)
        called = []
        evaluator = BoundedEvaluator(called.append, lambda _: None)
        try:
            with self.assertRaises(ValueError):
                evaluator('never', stage='C:/private/path')
            with self.assertRaises(ValueError):
                evaluator('never', timeout=0)
            self.assertEqual(called, [])
        finally:
            evaluator.close()


if __name__ == '__main__':
    unittest.main()
