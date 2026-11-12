"""Local supplied-model evaluation; fixtures are analytical, not predictions."""
import copy
import dataclasses
import hashlib
import http.client
import json
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from nivram import population_jobs as jobs
from nivram.populations import population_capabilities
from nivram.server import make_server
from nivram.worker_processes import CancellationToken, WorkerCancelled
from tests.test_populations import acid_model, conditions, model, provenance, state


def request(supplied=None, *, request_id='population_test_1', text=None, **changes):
    supplied = acid_model() if supplied is None else supplied
    text = json.dumps(supplied.to_dict(), ensure_ascii=False) if text is None else text
    value = {'schemaVersion': 1, 'requestId': request_id, 'documentRevision': 12,
             'graphHash': 'a' * 64, 'modelText': text,
             'modelInputHash': hashlib.sha256(text.encode('utf-8')).hexdigest()}
    value.update(changes)
    return value


class PopulationEvaluationTests(unittest.TestCase):
    def test_analytical_grid_binding_provenance_and_inputs_unchanged(self):
        supplied = acid_model(provenance=provenance(input_identity='external identity is not the graph hash'))
        value = request(supplied)
        before = copy.deepcopy(value)
        capabilities = population_capabilities()
        result = json.loads(jobs.evaluate_request(value))
        self.assertEqual(set(result), {'schemaVersion', 'requestId', 'modelInputHash', 'envelope'})
        self.assertEqual(result['requestId'], value['requestId'])
        self.assertEqual(result['modelInputHash'], hashlib.sha256(value['modelText'].encode()).hexdigest())
        envelope = result['envelope']
        self.assertEqual(envelope['documentRevision'], 12)
        self.assertEqual(envelope['graphHash'], 'a' * 64)
        self.assertEqual(envelope['modelSignature'], supplied.signature)
        self.assertEqual(envelope['model'], json.loads(value['modelText']))
        self.assertEqual([row['index'] for row in envelope['samples']], list(range(141)))
        self.assertEqual([row['ph'] for row in envelope['samples']], [index / 10 for index in range(141)])
        self.assertEqual(envelope['samples'][70]['result']['fractions'], [.5, .5])
        for index in (60, 80):
            fractions = envelope['samples'][index]['result']['fractions']
            self.assertAlmostEqual(fractions[1] / fractions[0], 10 ** (index / 10 - 7), places=12)
        self.assertEqual(envelope['samples'][70]['result']['input_identity'], supplied.provenance.input_identity)
        self.assertEqual(value, before)
        self.assertEqual(population_capabilities(), capabilities)
        self.assertFalse(capabilities['molecular_predictor_available'])

    def test_narrow_domain_preserves_all_gaps_and_incomplete_mass_warning(self):
        supplied = acid_model(conditions=conditions(ph_min=6.9, ph_max=7.1), ensemble_complete=False)
        samples = json.loads(jobs.evaluate_request(request(supplied)))['envelope']['samples']
        self.assertEqual(len(samples), 141)
        self.assertEqual([row['index'] for row in samples if row['status'] == 'computed'], [69, 70, 71])
        for row in samples:
            if row['index'] not in (69, 70, 71):
                self.assertEqual(row['status'], 'outside_model_domain')
                self.assertIsNone(row['result'])
                self.assertIn('extrapolation', row['reason'])
        result = samples[70]['result']
        self.assertEqual(result['normalization_scope'], 'conditional_on_represented_states')
        self.assertIsNone(result['omitted_fraction'])
        self.assertTrue(any('unknown' in warning for warning in result['warnings']))

    def test_request_schema_hash_utf8_and_safe_revision_are_required(self):
        for changes in [{'schemaVersion': True}, {'requestId': 'short'}, {'requestId': '../private'},
                        {'documentRevision': True}, {'documentRevision': -1}, {'documentRevision': 2**53},
                        {'graphHash': 'A' * 64}, {'modelInputHash': 'b' * 64}, {'modelText': []}, {'extra': 1}]:
            with self.subTest(changes=changes), self.assertRaises(jobs.PopulationJobError):
                jobs.validate_request(request(**changes))
        for text in ['', '\ud800', 'a' * (jobs.MAX_MODEL_BYTES + 1), '\u00e9' * (jobs.MAX_MODEL_BYTES // 2 + 1)]:
            value = request()
            value['modelText'] = text
            with self.subTest(size=len(text)), self.assertRaises(jobs.PopulationJobError):
                jobs.validate_request(value)
        self.assertEqual(jobs.validate_request(request(documentRevision=2**53 - 1))['documentRevision'], 2**53 - 1)
        unicode_model = acid_model(provenance=provenance(input_identity='supplied \u03b1 state'))
        value = request(unicode_model)
        self.assertEqual(json.loads(jobs.evaluate_request(value))['envelope']['model']['provenance']['input_identity'], 'supplied \u03b1 state')

    def test_complete_model_only_missing_conditions_and_no_inferred_energies(self):
        original = json.loads(request()['modelText'])
        variants = [[], {'model': original}, {'pKas': [7]}, {'states': original['states']},
                    {**original, 'conditions': {}}, {**original, 'provenance': {}}]
        missing_energy = copy.deepcopy(original)
        del missing_energy['states'][0]['free_energy_at_reference']
        missing_default = copy.deepcopy(original)
        del missing_default['states'][0]['degeneracy']
        missing_transforms = copy.deepcopy(original)
        del missing_transforms['provenance']['transformations']
        variants.extend([missing_energy, missing_default, missing_transforms])
        for value in variants:
            with self.subTest(fields=list(value) if isinstance(value, dict) else 'array'):
                with self.assertRaises(jobs.PopulationJobError) as caught:
                    jobs.evaluate_request(request(text=json.dumps(value)))
                self.assertEqual(caught.exception.code, 'invalid_model')
        for text in ['{', '{"states":[],"states":[]}', '{"x":NaN}']:
            with self.subTest(text=text), self.assertRaises(jobs.PopulationJobError):
                jobs.evaluate_request(request(text=text))

    def test_4096_state_model_is_preserved_when_complete_output_fits(self):
        states = tuple(state(f's{i}', 0, energy=0, conformer_id=f'c{i}', structure_id='same-core') for i in range(4096))
        supplied = model(states, conditions=conditions(ph_min=7, ph_max=7), supported_classes=('conformer',))
        value = request(supplied)
        self.assertLess(len(value['modelText'].encode()), jobs.MAX_MODEL_BYTES)
        encoded = jobs.evaluate_request(value)
        self.assertLess(len(encoded), jobs.MAX_OUTPUT_BYTES)
        envelope = json.loads(encoded)['envelope']
        self.assertEqual(len(envelope['model']['states']), 4096)
        self.assertEqual(len(envelope['samples'][70]['result']['fractions']), 4096)
        self.assertEqual(envelope['samples'][70]['result']['fractions'][0], 1 / 4096)
        self.assertEqual(sum(row['status'] == 'computed' for row in envelope['samples']), 1)

    def test_repeated_metadata_exceeding_real_output_cap_is_rejected_without_truncation(self):
        supplied = acid_model(provenance=provenance(transformations=tuple('x' * 8192 for _ in range(100))))
        from nivram.populations import sample_grid
        with patch('nivram.populations.sample_grid', wraps=sample_grid) as samples:
            with self.assertRaises(jobs.PopulationJobError) as caught:
                jobs.evaluate_request(request(supplied))
            self.assertEqual(caught.exception.code, 'output_too_large')
            self.assertGreater(samples.call_count, 1)
            self.assertLess(samples.call_count, 141)
            self.assertTrue(all(call.args[1] == call.args[2] for call in samples.call_args_list))


class PopulationJobTests(unittest.TestCase):
    def test_real_owned_worker_is_stdlib_only_and_round_trips(self):
        # This assertion runs inside the fresh child, independent of the test
        # process importing the existing HTTP server's RDKit dependencies.
        script = ('import sys; from nivram.population_jobs import worker_main; '
                  'assert not any(k.startswith(("rdkit", "torch", "numpy")) for k in sys.modules); '
                  'raise SystemExit(worker_main())')
        service = jobs.PopulationJobs(command_factory=lambda: [sys.executable, '-B', '-s', '-c', script])
        self.addCleanup(service.shutdown)
        value = request()
        result = json.loads(service.evaluate(value))
        self.assertEqual(result['envelope']['samples'][70]['result']['fractions'], [.5, .5])
        self.assertEqual(service.snapshot()['activeJobs'], 0)
        with self.assertRaises(jobs.PopulationJobError) as caught:
            service.evaluate(value)
        self.assertEqual(caught.exception.code, 'reused_request')

    def test_cancel_before_registration_and_unique_retry_of_same_model(self):
        service = jobs.PopulationJobs()
        self.addCleanup(service.shutdown)
        value = request()
        self.assertEqual(service.cancel({'schemaVersion': 1, 'requestId': value['requestId']})['status'], 'cancelled_before_registration')
        with self.assertRaises(WorkerCancelled):
            service.evaluate(value)
        retry = {**value, 'requestId': 'population_retry_2'}
        self.assertEqual(json.loads(service.evaluate(retry))['modelInputHash'], value['modelInputHash'])

    def test_active_cancellation_busy_and_shutdown_are_owned(self):
        service = jobs.PopulationJobs(command_factory=lambda: [sys.executable, '-B', '-c', 'import sys,time;sys.stdin.buffer.read();time.sleep(30)'])
        self.addCleanup(service.shutdown)
        errors = []
        def run():
            try:
                service.evaluate(request())
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 5
        while service._supervisor.active_count == 0 and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        try:
            self.assertEqual(service._supervisor.active_count, 1)
            with self.assertRaises(jobs.PopulationJobError) as caught:
                service.evaluate(request(request_id='population_second'))
            self.assertEqual(caught.exception.code, 'busy')
            self.assertEqual(service.cancel({'schemaVersion': 1, 'requestId': 'population_test_1'})['status'], 'cancellation_requested')
        finally:
            service.shutdown()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], WorkerCancelled)
        self.assertEqual(service._supervisor.active_count, 0)
        self.assertFalse(service.snapshot()['acceptingJobs'])

    def test_timeout_and_before_start_token_cancellation(self):
        service = jobs.PopulationJobs(command_factory=lambda: [sys.executable, '-B', '-c', 'import time;time.sleep(30)'])
        self.addCleanup(service.shutdown)
        with self.assertRaises(jobs.PopulationJobError) as caught:
            service.evaluate(request(), timeout=.1)
        self.assertEqual(caught.exception.code, 'timed_out')
        self.assertEqual(service._supervisor.active_count, 0)
        token = CancellationToken(); token.cancel()
        with self.assertRaises(WorkerCancelled):
            service.evaluate(request(request_id='population_token_2'), cancellation_token=token)

    def test_history_capacity_is_bounded_without_evicting_pending_cancels(self):
        service = jobs.PopulationJobs()
        self.addCleanup(service.shutdown)
        with patch.object(jobs, 'MAX_HISTORY', 2):
            for index in range(2):
                service.cancel({'schemaVersion': 1, 'requestId': f'cancelled_{index}'})
            with self.assertRaises(jobs.PopulationJobError) as caught:
                service.cancel({'schemaVersion': 1, 'requestId': 'cancelled_third'})
            self.assertEqual(caught.exception.code, 'history_full')
            with self.assertRaises(WorkerCancelled):
                service.evaluate(request(request_id='cancelled_0'))

    def test_worker_error_and_binding_mismatch_cannot_be_returned_as_success(self):
        supervisor = Mock()
        service = jobs.PopulationJobs(supervisor=supervisor)
        for index, output in enumerate([{'schemaVersion': 1, 'error': 'invalid_model'}, {'schemaVersion': 1, 'error': 'output_too_large'}]):
            supervisor.run.return_value = subprocess.CompletedProcess([], 2, json.dumps(output), '')
            with self.assertRaises(jobs.PopulationJobError) as caught:
                service.evaluate(request(request_id=f'error_job_{index}'))
            self.assertEqual(caught.exception.code, output['error'])
        value = request(request_id='mismatch_job')
        output = json.loads(jobs.evaluate_request(value))
        output['requestId'] = 'different_job'
        supervisor.run.return_value = subprocess.CompletedProcess([], 0, json.dumps(output), '')
        with self.assertRaises(jobs.PopulationJobError) as caught:
            service.evaluate(value)
        self.assertEqual(caught.exception.code, 'worker_failed')
        self.assertEqual(supervisor.run.call_args.kwargs['timeout_seconds'], 60)
        self.assertEqual(supervisor.run.call_args.kwargs['max_stdout_bytes'], 16 * 1024 * 1024)


class PopulationHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(timeout=3)

    def post(self, path, data, extra=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        headers = {'Content-Type': 'application/json', 'X-Nivram-Token': self.server.api_token,
                   'Origin': f'http://127.0.0.1:{self.server.server_port}'}
        headers.update(extra or {})
        try:
            connection.request('POST', path, body=json.dumps(data).encode(), headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_actual_http_evaluates_supplied_model_without_changing_input(self):
        value = request(request_id='http_analytical_1')
        before = copy.deepcopy(value)
        status, result = self.post('/api/populations/evaluate', value)
        self.assertEqual(status, 200)
        self.assertEqual(result['envelope']['samples'][70]['result']['percentages'], [50, 50])
        self.assertEqual(result['envelope']['model'], json.loads(value['modelText']))
        self.assertEqual(value, before)
        self.assertEqual(self.server.population_jobs.snapshot()['activeJobs'], 0)

    def test_evaluate_and_cancel_require_existing_loopback_authentication(self):
        for path, value in [('/api/populations/evaluate', request(request_id='http_rejected_1')),
                            ('/api/populations/cancel', {'schemaVersion': 1, 'requestId': 'http_rejected_1'})]:
            for extra in [{'X-Nivram-Token': ''}, {'Origin': 'https://example.invalid'}, {'Host': 'attacker.invalid'}]:
                with self.subTest(path=path, extra=extra):
                    self.assertEqual(self.post(path, value, extra)[0], 403)

    def test_http_invalid_model_hash_and_cancel_before_registration(self):
        invalid = request(request_id='http_bad_hash_1', modelInputHash='b' * 64)
        self.assertEqual(self.post('/api/populations/evaluate', invalid)[0], 422)
        self.assertEqual(self.post('/api/populations/evaluate', request(request_id='http_bad_model_1', text='{"conditions":{}}'))[0], 422)
        status, result = self.post('/api/populations/cancel', {'schemaVersion': 1, 'requestId': 'http_cancelled_1'})
        self.assertEqual((status, result['status']), (200, 'cancelled_before_registration'))
        status, result = self.post('/api/populations/evaluate', request(request_id='http_cancelled_1'))
        self.assertEqual((status, result['status']), (409, 'cancelled'))


if __name__ == '__main__':
    unittest.main()
