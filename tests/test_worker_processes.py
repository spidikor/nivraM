import os
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from nivram.worker_processes import (CancellationToken, WorkerCancelled,
                                     WorkerOutputLimit, WorkerProcessError,
                                     WorkerSupervisor)


def command(script):
    return [sys.executable, '-u', '-c', script]


class WorkerProcessTests(unittest.TestCase):
    def setUp(self):
        self.supervisor = WorkerSupervisor(max_workers=2)
        self.threads = []

    def tearDown(self):
        self.assertTrue(self.supervisor.shutdown(timeout_seconds=2.0))
        for thread in self.threads:
            thread.join(2.0)
            self.assertFalse(thread.is_alive())

    def start_job(self, script='import time; time.sleep(60)', **options):
        outcome = {}
        def target():
            try:
                outcome['result'] = self.supervisor.run(command(script), timeout_seconds=30, **options)
            except BaseException as error:
                outcome['error'] = error
        thread = threading.Thread(target=target, daemon=True)
        self.threads.append(thread)
        thread.start()
        return thread, outcome

    def wait_active(self, count):
        deadline = time.monotonic() + 5
        while self.supervisor.active_count != count and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.supervisor.active_count, count)

    def test_utf8_input_and_both_streams_are_drained(self):
        text = 'molecule-free \u03bc test'
        result = self.supervisor.run(
            command('import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data); sys.stderr.buffer.write(b"diagnostic")'),
            input_text=text, timeout_seconds=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, text)
        self.assertEqual(result.stderr, 'diagnostic')
        self.assertEqual(self.supervisor.active_count, 0)

    def test_long_job_allowance_is_explicit_and_remains_bounded(self):
        with patch('nivram.worker_processes.subprocess.Popen') as create:
            with self.assertRaisesRegex(ValueError, 'at most 60 seconds'):
                self.supervisor.run(command('pass'), timeout_seconds=61)
            create.assert_not_called()
        longer = WorkerSupervisor(max_timeout_seconds=300)
        try:
            result = longer.run(command('print("ready")'), timeout_seconds=300)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), 'ready')
            with patch('nivram.worker_processes.subprocess.Popen') as create:
                with self.assertRaisesRegex(ValueError, 'at most 300 seconds'):
                    longer.run(command('pass'), timeout_seconds=301)
                create.assert_not_called()
        finally:
            self.assertTrue(longer.shutdown())
        for limit in (True, 0, -1, 301, float('nan'), float('inf')):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                WorkerSupervisor(max_timeout_seconds=limit)

    def test_exact_stream_limits_are_accepted_without_pipe_deadlock(self):
        result = self.supervisor.run(
            command('import sys; sys.stdout.buffer.write(b"a"*32768); sys.stderr.buffer.write(b"b"*32768)'),
            timeout_seconds=5, max_stdout_bytes=32768, max_stderr_bytes=32768,
        )
        self.assertEqual(len(result.stdout), 32768)
        self.assertEqual(len(result.stderr), 32768)

    def test_stdout_and_stderr_limits_stop_the_owned_process(self):
        for stream in ('stdout', 'stderr'):
            with self.subTest(stream=stream), self.assertRaises(WorkerOutputLimit) as caught:
                self.supervisor.run(
                    command(f'import sys,time; sys.{stream}.buffer.write(b"x"*1048576); time.sleep(60)'),
                    timeout_seconds=5, max_stdout_bytes=1024, max_stderr_bytes=1024,
                )
            self.assertNotIn(sys.executable, str(caught.exception))
            self.assertEqual(self.supervisor.active_count, 0)

    def test_output_overflow_is_rejected_even_if_child_exits_immediately(self):
        with self.assertRaises(WorkerOutputLimit):
            self.supervisor.run(command('import sys; sys.stdout.buffer.write(b"x"*1025)'),
                                timeout_seconds=5, max_stdout_bytes=1024)

    def test_input_limit_uses_utf8_bytes_and_does_not_start_process(self):
        with patch('nivram.worker_processes.subprocess.Popen') as create, \
                self.assertRaises(WorkerProcessError):
            self.supervisor.run(command('pass'), input_text='\u03bc\u03bc',
                                timeout_seconds=5, max_input_bytes=3)
        create.assert_not_called()

    def test_timeout_stops_child_and_reports_no_command_or_input(self):
        start = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            self.supervisor.run(command('import time; time.sleep(60)'),
                                input_text='private input marker', timeout_seconds=0.15)
        self.assertLess(time.monotonic() - start, 2.5)
        self.assertEqual(self.supervisor.active_count, 0)
        self.assertNotIn(sys.executable, str(caught.exception))
        self.assertNotIn('private input marker', str(caught.exception))

    def test_blocked_input_writer_is_released_on_timeout(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.supervisor.run(command('import time; time.sleep(60)'),
                                input_text='x' * 65536, timeout_seconds=0.15)
        self.assertEqual(self.supervisor.active_count, 0)

    def test_cancel_before_start_creates_no_process(self):
        token = CancellationToken()
        token.cancel()
        token.cancel()
        with patch('nivram.worker_processes.subprocess.Popen') as create, \
                self.assertRaises(WorkerCancelled):
            self.supervisor.run(command('pass'), timeout_seconds=5, cancellation_token=token)
        create.assert_not_called()

    def test_cancellation_is_specific_to_its_job(self):
        token = CancellationToken()
        first, first_outcome = self.start_job(cancellation_token=token)
        second, second_outcome = self.start_job()
        self.wait_active(2)
        token.cancel()
        first.join(2)
        self.assertFalse(first.is_alive())
        self.assertIsInstance(first_outcome.get('error'), WorkerCancelled)
        self.assertTrue(second.is_alive())
        self.assertNotIn('error', second_outcome)
        self.assertEqual(self.supervisor.active_count, 1)

    def test_shutdown_stops_all_owned_jobs_and_rejects_new_work(self):
        first, first_outcome = self.start_job()
        second, second_outcome = self.start_job()
        self.wait_active(2)
        start = time.monotonic()
        self.assertTrue(self.supervisor.shutdown(timeout_seconds=2))
        self.assertLess(time.monotonic() - start, 2.5)
        for thread, outcome in ((first, first_outcome), (second, second_outcome)):
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertIsInstance(outcome.get('error'), WorkerCancelled)
        self.assertEqual(self.supervisor.active_count, 0)
        with self.assertRaises(WorkerCancelled):
            self.supervisor.run(command('pass'), timeout_seconds=5)

    def test_unrelated_process_is_never_stopped(self):
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        unrelated = subprocess.Popen(command('import time; time.sleep(60)'),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, **options)
        try:
            self.start_job()
            self.wait_active(1)
            self.assertTrue(self.supervisor.shutdown(timeout_seconds=2))
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.kill()
            unrelated.wait(timeout=2)

    def test_capacity_limit_does_not_start_a_third_child(self):
        self.start_job()
        self.start_job()
        self.wait_active(2)
        with patch('nivram.worker_processes.subprocess.Popen') as create, \
                self.assertRaises(WorkerProcessError):
            self.supervisor.run(command('pass'), timeout_seconds=5)
        create.assert_not_called()
        self.assertEqual(self.supervisor.active_count, 2)

    def test_shutdown_returns_false_for_unconfirmed_in_progress_spawn(self):
        entered, release = threading.Event(), threading.Event()
        real_popen = subprocess.Popen
        def delayed_spawn(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test spawn gate was not released.')
            return real_popen(*args, **kwargs)
        with patch('nivram.worker_processes.subprocess.Popen', side_effect=delayed_spawn):
            thread, outcome = self.start_job()
            try:
                self.assertTrue(entered.wait(2))
                start = time.monotonic()
                self.assertFalse(self.supervisor.shutdown(timeout_seconds=0.05))
                self.assertLess(time.monotonic() - start, 0.5)
            finally:
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(outcome.get('error'), WorkerCancelled)
        self.assertEqual(self.supervisor.active_count, 0)


if __name__ == '__main__':
    unittest.main()
