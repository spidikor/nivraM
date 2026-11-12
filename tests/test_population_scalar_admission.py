"""Final scalar admission regressions: supplied-model arithmetic, not prediction."""
import copy
import json
import math
import unittest
from nivram import population_jobs as jobs
from nivram.population_verification import verify_population_response
from tests.test_population_signature_binding import fixture, request, ReturnedWire

KEYS = ('fractions', 'percentages', 'log_fractions')

class FloatSubclass(float):
    pass

class ScalarAdmissionTests(unittest.TestCase):
    def setup_model(self, energy=0.):
        model = fixture()
        model['conditions'].update(ph_min=7., ph_max=7.)
        model['states'][1]['free_energy_at_reference'] = energy
        value = request(model)
        wire = jobs.evaluate_request(value)
        return model, value, json.loads(wire)

    def verify(self, model, result):
        envelope = result['envelope']
        verify_population_response(model, envelope, envelope['modelSignature'], lambda: None)

    def service_rejects(self, value, result):
        service = jobs.PopulationJobs(supervisor=ReturnedWire(json.dumps(result).encode()))
        try:
            with self.assertRaises(jobs.PopulationJobError) as caught:
                service.evaluate(value)
            self.assertEqual(caught.exception.code, 'worker_failed')
            self.assertEqual(len(service._supervisor.calls), 1)
        finally:
            service.shutdown()

    def test_all_arrays_reject_non_exact_finite_floats(self):
        model, value, result = self.setup_model()
        for key in KEYS:
            expected = result['envelope']['samples'][70]['result'][key][0]
            bad_values = (True, False, 0, 1, 10**400, None, str(expected), [], {},
                          float('nan'), float('inf'), -float('inf'), FloatSubclass(expected),
                          5e-324, math.nextafter(expected, math.inf), 1.7976931348623157e308)
            for bad in bad_values:
                with self.subTest(key=key, bad_type=type(bad).__name__, bad=repr(bad)):
                    changed = copy.deepcopy(result)
                    changed['envelope']['samples'][70]['result'][key][0] = bad
                    # The baseline overflows converting a huge int to isfinite;
                    # the optimized exact-float gate raises ValueError instead.
                    with self.assertRaises((ValueError, OverflowError)):
                        self.verify(model, changed)
                    if type(bad) is not FloatSubclass:
                        self.service_rejects(value, changed)

    def test_each_zero_sign_and_small_nonzero_are_exact(self):
        model, value, result = self.setup_model(10000.)
        for key, index in (('fractions', 1), ('percentages', 1), ('log_fractions', 0)):
            self.assertEqual(result['envelope']['samples'][70]['result'][key][index], 0.)
            for bad in (-0., 5e-324):
                with self.subTest(key=key, bad=bad):
                    changed = copy.deepcopy(result)
                    changed['envelope']['samples'][70]['result'][key][index] = bad
                    with self.assertRaises(ValueError):
                        self.verify(model, changed)
                    self.service_rejects(value, changed)
        for energy in (1000., 1840.):
            model, value, result = self.setup_model(energy)
            self.verify(model, result)
            for key in ('fractions', 'percentages'):
                scalar = result['envelope']['samples'][70]['result'][key][1]
                self.assertGreater(scalar, 0.)
                self.assertLess(scalar, 1e-100)
                changed = copy.deepcopy(result)
                changed['envelope']['samples'][70]['result'][key][1] = 0.
                with self.assertRaises(ValueError):
                    self.verify(model, changed)
                self.service_rejects(value, changed)

if __name__ == '__main__':
    unittest.main()
