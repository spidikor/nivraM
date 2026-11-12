"""Producer-boundary arithmetic and metadata, not molecular validity."""
import copy
import json
import math
import unittest
from unittest.mock import patch

from nivram import population_jobs as jobs
from nivram import population_verification as verification
from nivram.worker_processes import CancellationToken, WorkerCancelled
from tests.test_population_signature_binding import fixture, request, ReturnedWire


class VerificationTests(unittest.TestCase):
    def evaluate(self, value, result=None, token=None):
        wire = jobs.evaluate_request(value) if result is None else json.dumps(result).encode()
        service = jobs.PopulationJobs(supervisor=ReturnedWire(wire))
        try:
            self.assertEqual(service.evaluate(value, cancellation_token=token), wire)
        finally:
            service.shutdown()
        return json.loads(wire)

    def reject(self, value, result):
        with self.assertRaises(jobs.PopulationJobError) as caught:
            self.evaluate(value, result)
        self.assertEqual(caught.exception.code, 'worker_failed')

    def test_mutated_results_reject(self):
        value = request(); original = json.loads(jobs.evaluate_request(value))
        def mutate(key, v):
            return lambda r: r['envelope']['samples'][70]['result'].__setitem__(key, v)
        mutations = [mutate('fractions', [.75, .25]), mutate('percentages', [25., 75.]),
            mutate('log_fractions', [0., 0.]), mutate('state_ids', ['wrong', 'identity']),
            mutate('ph', 7.1), mutate('omitted_fraction', .1), mutate('omitted_mass_source', 'invented'),
            mutate('normalization_scope', 'supplied_total_mass'), mutate('warnings', []),
            mutate('model_signature', 'b' * 64), mutate('log_fraction_base', '10'),
            mutate('input_identity', 'different'), mutate('fractions', [True, .5]),
            mutate('fractions', ['0.5', .5]), mutate('fractions', [0, 1]),
            mutate('fractions', [float('inf'), .5]), mutate('fractions', [.5]),
            lambda r: r['envelope']['samples'].reverse(),
            lambda r: r['envelope']['samples'][70].update(index=True),
            lambda r: r['envelope']['samples'][70].update(ph=7),
            lambda r: r['envelope']['samples'][70].update(status='outside_model_domain', result=None, reason='No extrapolation outside the supplied model domain.'),
            lambda r: r['envelope']['samples'][70]['result']['provenance'].update(source='changed'),
            lambda r: r['envelope']['samples'][70]['result']['conditions'].update(temperature_kelvin=300),
            lambda r: r['envelope']['model']['states'].reverse(),
            lambda r: r['envelope']['model']['conditions'].update(reference_ph=7),
            lambda r: r['envelope']['model']['provenance'].update(input_identity='different')]
        for index, mutate_result in enumerate(mutations):
            with self.subTest(index=index):
                result = copy.deepcopy(original); mutate_result(result)
                self.reject(value, result)

    def test_all_units_modes_and_analytical_ratios(self):
        for unit, factor in (('J/mol', .001), ('kJ/mol', 1.), ('kcal/mol', 4.184)):
            for convention in ('explicit', 'included_in_free_energy'):
                for complete in (True, False):
                    with self.subTest(unit=unit, convention=convention, complete=complete):
                        model = fixture(); c = model['conditions']
                        c.update(energy_unit=unit, degeneracy_convention=convention)
                        model['ensemble_complete'] = complete
                        model['reference_state_id'] = model['states'][1]['state_id']
                        model['states'][0]['free_energy_at_reference'] = .00831446261815324 * c['temperature_kelvin'] * math.log(3) / factor
                        model['states'][1]['free_energy_at_reference'] = 0
                        model['states'][0]['degeneracy'] = 3 if convention == 'explicit' else 1
                        result = self.evaluate(request(model))['envelope']['samples'][70]['result']
                        self.assertAlmostEqual(result['fractions'][0], .5 if convention == 'explicit' else .25)

    def test_adjacent_large_integer_energies_remain_distinct(self):
        model = fixture()
        for s, energy in zip(model['states'], (2**60, 2**60 + 1)):
            s['free_energy_at_reference'] = energy
        model['reference_state_id'] = model['states'][1]['state_id']
        result = self.evaluate(request(model))['envelope']['samples'][70]['result']
        ratio = math.exp(1 / (.00831446261815324 * model['conditions']['temperature_kelvin']))
        self.assertAlmostEqual(result['fractions'][0] / result['fractions'][1], ratio)
        self.assertNotEqual(result['fractions'], [.5, .5])

    def test_tiny_underflow_and_signed_zero(self):
        for energy in (0, -0.0, 1000., 10000.):
            model = fixture(); model['states'][1]['free_energy_at_reference'] = energy
            value = request(model); result = self.evaluate(value)
            row = result['envelope']['samples'][70]['result']
            self.assertTrue(all(math.isfinite(v) for v in row['log_fractions']))
            if energy == 1000:
                self.assertGreater(row['fractions'][1], 0)
                row['fractions'][1] = 0.; row['percentages'][1] = 0.
                self.reject(value, result)
            if energy == 10000:
                self.assertEqual(row['fractions'][1], 0.)
                row['log_fractions'][1] -= 1
                self.reject(value, result)
        model = fixture(); model['conditions']['ionic_strength_mol_l'] = -0.0
        value = request(model); result = self.evaluate(value)
        result['envelope']['model']['conditions']['ionic_strength_mol_l'] = 0.0
        self.reject(value, result)

    def test_domain_and_conditional_warnings(self):
        model = fixture(); model['conditions'].update(ph_min=6.9, ph_max=7.1)
        model.update(ensemble_complete=False, equilibrium_scope='conditional_configuration')
        value = request(model); result = self.evaluate(value)
        self.assertEqual(sum(r['status'] == 'computed' for r in result['envelope']['samples']), 3)
        for field, replacement in (('reason', 'invented'), ('ph', 0.1), ('result', {})):
            forged = copy.deepcopy(result); forged['envelope']['samples'][0][field] = replacement
            self.reject(value, forged)
        result['envelope']['samples'][70]['result']['omitted_fraction'] = 0.
        self.reject(value, result)

    def test_malformed_original_success_is_safely_rejected(self):
        original = fixture()
        mutations = [lambda m: m.update(ensemble_complete=1), lambda m: m.update(states=[]),
            lambda m: m['conditions'].update(temperature_kelvin=0),
            lambda m: m['conditions'].update(temperature_kelvin=True),
            lambda m: m['conditions'].update(energy_unit=[]),
            lambda m: m['states'][0].update(degeneracy=0),
            lambda m: m['states'][0].update(relative_protons=1.0),
            lambda m: m['states'][0].update(free_energy_at_reference=None),
            lambda m: m['provenance'].update(created_utc='yesterday')]
        for mutation in mutations:
            bad = copy.deepcopy(original); mutation(bad)
            value = request(bad); result = json.loads(jobs.evaluate_request(request()))
            result['modelInputHash'] = value['modelInputHash']; result['envelope']['model'] = bad
            import hashlib
            sig = hashlib.sha256(json.dumps(bad, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            result['envelope']['modelSignature'] = sig
            self.reject(value, result)

    def test_maximum_states_and_exact_float_contract(self):
        model = fixture(); state = model['states'][0]
        model['states'] = [dict(state, state_id=f's{i}', conformer_id=f'c{i}') for i in range(4096)]
        model.update(reference_state_id='s4000', supported_classes=['conformer'])
        model['conditions'].update(ph_min=7., ph_max=7.)
        value = request(model); result = self.evaluate(value)
        row = result['envelope']['samples'][70]['result']
        self.assertEqual(row['fractions'], [1 / 4096] * 4096)
        row['fractions'][0] = math.nextafter(row['fractions'][0], 1.)
        self.reject(value, result)

    def test_single_state_zero_logs_and_large_finite_differences(self):
        for complete in (True, False):
            for convention in ('explicit', 'included_in_free_energy'):
                for energy in (0., -0., 1e300, -1e300):
                    with self.subTest(complete=complete, convention=convention, energy=energy):
                        model = fixture(); model['states'] = model['states'][:1]
                        model['reference_state_id'] = model['states'][0]['state_id']
                        model['states'][0]['free_energy_at_reference'] = energy
                        model['conditions']['degeneracy_convention'] = convention
                        model['ensemble_complete'] = complete
                        result = self.evaluate(request(model))['envelope']['samples'][70]['result']
                        self.assertEqual(result['fractions'], [1.])
                        self.assertEqual(result['log_fractions'], [0.])
                        self.assertEqual(math.copysign(1, result['log_fractions'][0]), 1)
        for anchor in (1e300, -1e300):
            model = fixture()
            model['states'][0]['free_energy_at_reference'] = anchor
            model['states'][1]['free_energy_at_reference'] = math.nextafter(anchor, math.inf)
            result = self.evaluate(request(model))['envelope']['samples'][70]['result']
            self.assertEqual(result['fractions'], [1., 0.])
            self.assertTrue(all(math.isfinite(v) for v in result['log_fractions']))

    def test_all_gap_domain_does_not_evaluate_overflowing_energy_differences(self):
        model = fixture()
        model['conditions'].update(ph_min=-2, ph_max=-1, reference_ph=-1)
        model['states'][0]['free_energy_at_reference'] = 10**308
        model['states'][1]['free_energy_at_reference'] = -(10**308)
        value = request(model)
        result = self.evaluate(value)
        self.assertTrue(all(row['status'] == 'outside_model_domain'
                            and row['result'] is None for row in result['envelope']['samples']))
        # A false computed row cannot use the absence of arithmetic to bypass
        # the domain contract, and the worker still rejects actual overflow.
        result['envelope']['samples'][0].update(status='computed', result={})
        self.reject(value, result)
        model['conditions'].update(ph_min=0, ph_max=1, reference_ph=0)
        with self.assertRaises(jobs.PopulationJobError) as caught:
            jobs.evaluate_request(request(model))
        self.assertEqual(caught.exception.code, 'invalid_model')

    def test_cooperative_cancellation_during_verification(self):
        value = request(); result = json.loads(jobs.evaluate_request(value))
        for count in (1, 100, 1000):
            token = CancellationToken(); calls = 0
            original = verification._require
            def require(condition):
                nonlocal calls
                calls += 1
                if calls == count: token.cancel()
                original(condition)
            with self.subTest(count=count), patch.object(verification, '_require', require):
                with self.assertRaises(WorkerCancelled):
                    self.evaluate(value, result, token)


if __name__ == '__main__':
    unittest.main()
