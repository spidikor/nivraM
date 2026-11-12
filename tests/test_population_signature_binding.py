"""Original-request binding and independent worker-vector verification.

Stdlib-only real evaluator/model fixtures. No scientific modules are patched.
"""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from nivram import population_jobs as jobs
from nivram.worker_processes import CancellationToken, WorkerCancelled, WorkerSupervisor


def fixture():
    path = Path(jobs.__file__).resolve().parents[1] / 'tests/fixtures/population-analytical.json'
    return json.loads(path.read_bytes())['complete']['envelope']['model']


def request(model=None, *, text=None):
    if text is None:
        text = json.dumps(fixture() if model is None else model, ensure_ascii=False)
    return dict(schemaVersion=1, requestId='signature_binding_001', documentRevision=7,
                graphHash='a' * 64, modelText=text,
                modelInputHash=hashlib.sha256(text.encode()).hexdigest())


class ReturnedWire(WorkerSupervisor):
    """Test transport seam; production parser, model and evaluator remain real."""
    def __init__(self, wire, *, code=0, on_run=None):
        super().__init__(max_workers=1)
        self.wire, self.code, self.on_run = wire, code, on_run
        self.calls = []

    def run(self, *args, **kwargs):
        self.calls.append(kwargs)
        if self.on_run:
            self.on_run()
        return subprocess.CompletedProcess(args[0], self.code, self.wire.decode(), '')


class SignatureBindingTests(unittest.TestCase):
    def service(self, wire, **kwargs):
        supervisor = ReturnedWire(wire, **kwargs)
        service = jobs.PopulationJobs(supervisor=supervisor)
        self.addCleanup(service.shutdown)
        return service, supervisor

    def assert_rejected(self, value, wire):
        service, supervisor = self.service(wire)
        with self.assertRaises(jobs.PopulationJobError) as caught:
            service.evaluate(value)
        self.assertEqual(caught.exception.code, 'worker_failed')
        self.assertEqual(len(supervisor.calls), 1)

    def test_forged_envelope_identifier_is_rejected(self):
        value = request()
        result = json.loads(jobs.evaluate_request(value))
        result['envelope']['modelSignature'] = 'b' * 64
        self.assert_rejected(value, json.dumps(result).encode())

    def test_self_consistent_wrong_identifier_is_rejected(self):
        value = request()
        result = json.loads(jobs.evaluate_request(value))
        result['envelope']['modelSignature'] = 'b' * 64
        count = 0
        for row in result['envelope']['samples']:
            if row['result']:
                self.assertIn('model_signature', row['result'])
                row['result']['model_signature'] = 'b' * 64
                count += 1
        self.assertEqual(count, 141)
        self.assert_rejected(value, json.dumps(result).encode())

    def test_real_owned_child_forgery_is_rejected(self):
        script = ('import json,sys; from nivram.population_jobs import evaluate_request; '
                  'r=json.loads(evaluate_request(json.loads(sys.stdin.read()))); '
                  'r["envelope"]["modelSignature"]="b"*64; print(json.dumps(r))')
        service = jobs.PopulationJobs(command_factory=lambda: [sys.executable, '-B', '-S', '-c', script])
        self.addCleanup(service.shutdown)
        with self.assertRaises(jobs.PopulationJobError) as caught:
            service.evaluate(request())
        self.assertEqual(caught.exception.code, 'worker_failed')
        self.assertEqual(service._supervisor.active_count, 0)

    def test_supported_model_representations_match_real_signature_and_exact_wire(self):
        models = [fixture()]
        for number in (0, 0.0, -0.0, 5e-324, 1.7976931348623157e308, 2**53, 2**53+1):
            value = fixture(); value['conditions']['ionic_strength_mol_l'] = number
            models.append(value)
        value = fixture(); value['provenance']['input_identity'] = 'α é e\u0301 😀'
        models.append(value)
        value = fixture(); value['ensemble_complete'] = False
        models.append(value)
        def reverse(value):
            if isinstance(value, dict):
                return {k: reverse(v) for k, v in reversed(list(value.items()))}
            if isinstance(value, list):
                return [reverse(v) for v in value]
            return value
        for model in models:
            for ascii in (True, False):
                text = json.dumps(reverse(model), ensure_ascii=ascii, indent=2)
                with self.subTest(model=model['conditions']['ionic_strength_mol_l'], ascii=ascii):
                    real = jobs._complete_model(text)
                    expected = hashlib.sha256(json.dumps(jobs._json(text), sort_keys=True,
                        separators=(',', ':'), allow_nan=False).encode()).hexdigest()
                    self.assertEqual(expected, real.signature)
                    value = request(text=text)
                    # The service must retain even noncanonical successful wire formatting.
                    wire = (' \n' + json.dumps(json.loads(jobs.evaluate_request(value)),
                                              ensure_ascii=False, indent=1) + '\n').encode()
                    service, supervisor = self.service(wire)
                    self.assertEqual(service.evaluate(value), wire)
                    options = supervisor.calls[0]
                    self.assertEqual(options['timeout_seconds'], jobs.DEFAULT_TIMEOUT)
                    self.assertEqual(options['max_input_bytes'], jobs.MAX_TRANSPORT_BYTES)
                    self.assertEqual(options['max_stdout_bytes'], jobs.MAX_OUTPUT_BYTES)
                    self.assertEqual(options['max_stderr_bytes'], 8192)
                    self.assertEqual(len(supervisor.calls), 1)

    def test_numeric_and_boolean_distinctions_are_not_coerced(self):
        def signature(value):
            model = fixture(); model['conditions']['ionic_strength_mol_l'] = value
            return jobs._complete_model(json.dumps(model)).signature
        self.assertEqual(len({signature(x) for x in (0, 0.0, -0.0, 2**53, 2**53+1)}), 5)
        model = fixture(); model['conditions']['ionic_strength_mol_l'] = True
        with self.assertRaises(jobs.PopulationJobError):
            jobs._complete_model(json.dumps(model))
        # Integer negative zero is the same parsed integer; float spellings are aliases.
        base = fixture(); base['conditions']['ionic_strength_mol_l'] = 0.1
        text = json.dumps(base).replace('"ionic_strength_mol_l": 0.1', '"ionic_strength_mol_l": -0')
        self.assertEqual(jobs._complete_model(text).signature, signature(0))
        for spelling in ('0.0', '0e0', '0.000e+12'):
            text = json.dumps(base).replace('"ionic_strength_mol_l": 0.1', '"ionic_strength_mol_l": ' + spelling)
            self.assertEqual(jobs._complete_model(text).signature, signature(0.0))

    def test_missing_default_fields_and_malformed_models_keep_worker_errors(self):
        models = []
        for field in ('degeneracy',):
            model = fixture(); del model['states'][0][field]; models.append(json.dumps(model))
        model = fixture(); del model['provenance']['transformations']; models.append(json.dumps(model))
        models += ['{', '{"x":1,"x":2}', '{"x":NaN}', '{"x":1e309}']
        for text in models:
            with self.subTest(text=text[:60]):
                service = jobs.PopulationJobs()
                self.addCleanup(service.shutdown)
                with self.assertRaises(jobs.PopulationJobError) as caught:
                    service.evaluate(request(text=text))
                self.assertEqual(caught.exception.code, 'invalid_model')
        for error in ('invalid_input', 'invalid_model', 'output_too_large'):
            wire = json.dumps(dict(schemaVersion=1, error=error)).encode()
            service, _ = self.service(wire, code=2)
            with self.assertRaises(jobs.PopulationJobError) as caught:
                service.evaluate(request(text='{'))
            self.assertEqual(caught.exception.code, error)

    def test_expected_identifier_uses_frozen_original_request(self):
        value = request(); wire = jobs.evaluate_request(value)
        service, _ = self.service(wire, on_run=lambda: value.update(modelText='{}'))
        self.assertEqual(service.evaluate(value), wire)

    def test_cancellation_after_parent_parse_and_serialization_blocks_publication(self):
        value = request(); wire = jobs.evaluate_request(value)
        for stage in ('parse', 'serialize', 'hash'):
            token = CancellationToken()
            service, _ = self.service(wire)
            original_json, original_dumps = jobs._json, jobs.json.dumps
            original_hash = jobs.hashlib.sha256
            canonical = original_dumps(original_json(value['modelText']), sort_keys=True,
                                       separators=(',', ':'), allow_nan=False).encode()
            def parse(text):
                result = original_json(text)
                if text == value['modelText'] and stage == 'parse': token.cancel()
                return result
            def dumps(obj, *args, **kwargs):
                result = original_dumps(obj, *args, **kwargs)
                if kwargs.get('sort_keys') and stage == 'serialize': token.cancel()
                return result
            def sha256(data):
                result = original_hash(data)
                if data == canonical and stage == 'hash': token.cancel()
                return result
            with self.subTest(stage=stage), patch.object(jobs, '_json', parse), patch.object(jobs.json, 'dumps', dumps), patch.object(jobs.hashlib, 'sha256', sha256):
                with self.assertRaises(WorkerCancelled):
                    service.evaluate(value, cancellation_token=token)
            self.assertIsNone(service._active)

    def test_correct_identifier_with_wrong_vectors_is_rejected(self):
        value = request(); result = json.loads(jobs.evaluate_request(value))
        row = result['envelope']['samples'][70]['result']
        row['fractions'] = [.75, .25]; row['percentages'] = [75., 25.]
        row['log_fractions'] = [math.log(.75), math.log(.25)]
        wire = json.dumps(result).encode()
        self.assert_rejected(value, wire)


if __name__ == '__main__':
    unittest.main()
