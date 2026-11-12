import copy
import importlib.metadata
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from nivram.chemistry import import_structure
from nivram.naming import _run_opsin, name_structure
from nivram.naming_runtime import discover_naming_runtime
from nivram.worker_processes import (CancellationToken, WorkerCancelled,
                                     WorkerOutputLimit, WorkerSupervisor)


class NamingWorkerContractTests(unittest.TestCase):
    def test_cancel_before_naming_preserves_snapshot_without_starting_worker(self):
        graph = import_structure('C[C@@H](O)C(=O)[O-]')
        before = copy.deepcopy(graph)
        token = CancellationToken()
        token.cancel()
        with patch('nivram.naming._run_nispo') as generate:
            result = name_structure(graph, cancellation_token=token)
        generate.assert_not_called()
        self.assertEqual(result['status'], 'cancelled')
        self.assertIsNone(result['name'])
        self.assertEqual(graph, before)

    def test_cancel_after_generation_prevents_adoption(self):
        token = CancellationToken()
        def generate(*args):
            token.cancel()
            return {'status': 'generated', 'name': 'ethanol',
                    'engine': {'name': 'NISPO', 'version': '0.1.7'}}
        with patch('nivram.naming._run_nispo', side_effect=generate):
            result = name_structure(import_structure('CCO'), cancellation_token=token)
        self.assertEqual(result['status'], 'cancelled')
        self.assertIsNone(result['name'])
        self.assertIsNone(result['generatedName'])

    def test_output_limit_is_a_safe_failed_result(self):
        with patch('nivram.naming._run_nispo', side_effect=WorkerOutputLimit('private details')):
            result = name_structure(import_structure('CCO'))
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['name'])
        self.assertNotIn('private details', str(result['warnings']))


class NativeNamingWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            version = importlib.metadata.version('nispo')
        except importlib.metadata.PackageNotFoundError:
            raise unittest.SkipTest('The optional evaluated NISPO runtime is not installed.')
        if version != '0.1.7':
            raise unittest.SkipTest('The evaluated NISPO runtime is not installed.')
        cls.runtime = discover_naming_runtime()
        if cls.runtime.config is None:
            raise unittest.SkipTest('The evaluated local Java/OPSIN bundle is not installed.')

    def setUp(self):
        self.supervisor = WorkerSupervisor()
        self.addCleanup(self.supervisor.shutdown)

    def test_real_nispo_and_both_java_calls_use_the_supervisor(self):
        graph = import_structure('CCO')
        before = copy.deepcopy(graph)
        with patch.object(self.supervisor, 'run', wraps=self.supervisor.run) as run:
            result = name_structure(graph, opsin_config=self.runtime.config,
                                    supervisor=self.supervisor)
        self.assertEqual(result['status'], 'round_trip_matched')
        self.assertFalse(result['preferredIUPACName'])
        self.assertEqual(run.call_count, 3)
        self.assertEqual([Path(call.args[0][0]).name.lower() for call in run.call_args_list][1:],
                         ['java.exe', 'java.exe'])
        self.assertEqual(self.supervisor.active_count, 0)
        self.assertEqual(graph, before)

    def run_and_cancel(self, function):
        token, outcome = CancellationToken(), {}
        def target():
            try:
                outcome['result'] = function(token)
            except BaseException as error:
                outcome['error'] = error
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while self.supervisor.active_count == 0 and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(self.supervisor.active_count, 1)
            token.cancel()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(self.supervisor.active_count, 0)
            return outcome
        finally:
            token.cancel()
            self.supervisor.shutdown()
            thread.join(3)

    def test_cancellation_stops_an_actual_nispo_child(self):
        graph = import_structure('CCO')
        before = copy.deepcopy(graph)
        outcome = self.run_and_cancel(lambda token: name_structure(
            graph, supervisor=self.supervisor, cancellation_token=token))
        self.assertEqual(outcome['result']['status'], 'cancelled')
        self.assertIsNone(outcome['result']['name'])
        self.assertEqual(graph, before)

    def test_cancellation_stops_an_actual_java_child(self):
        outcome = self.run_and_cancel(lambda token: _run_opsin(
            'ethanol', self.runtime.config, 10, self.supervisor, token))
        self.assertIsInstance(outcome.get('error'), WorkerCancelled)


if __name__ == '__main__':
    unittest.main()
