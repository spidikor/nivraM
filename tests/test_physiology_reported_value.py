"""Source-qualified point semantics; no statistical reconstruction or clinical validation."""
import copy
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(os.environ.get('NIVRAM_PHYSIOLOGY_SOURCE', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))
from nivram.populations import PopulationError, load_physiology_presets, validate_physiology_dataset

POINTS = {
    'neuronal-cytosol-bonnet-2019': (7.09, 0.14),
    'rat-late-proximal-dubose-1983': (6.78, 0.03),
    'rat-deep-loop-bend-dubose-1983': (7.39, 0.04),
    'rat-late-distal-dubose-1983': (6.59, 0.04),
    'rat-papillary-base-dubose-1983': (6.24, 0.07),
    'rat-papillary-tip-dubose-1983': (6.12, 0.03),
}

class ReportedValueTests(unittest.TestCase):
    def setUp(self):
        self.data = load_physiology_presets()

    def test_all_six_points_do_not_assert_an_unverified_mean(self):
        self.assertEqual(self.data['dataset_version'], '2026-11-11.1')
        actual = {p['id']: p for p in self.data['presets'] if p['statistic'] == 'reported_value'}
        self.assertEqual(set(actual), set(POINTS))
        for sid, (value, uncertainty) in POINTS.items():
            p = actual[sid]
            self.assertEqual(p['ph'], dict(value=value, lower=None, upper=None, confidence_level=None))
            self.assertEqual(p['reported_uncertainty'], dict(value=uncertainty, kind='unspecified_in_abstract', plotted=False))
            self.assertEqual(p['display_kind'], 'study_marker')
            self.assertNotIn('mean', p['label'].lower())
            self.assertNotIn('mean', ' '.join(p['evidence_limitations']).lower())
            self.assertIn('central-statistic definition', ' '.join(p['evidence_limitations']))
        for source in self.data['sources']:
            if source['id'] in ('bonnet-2019', 'dubose-1983'):
                self.assertNotRegex(' '.join(source['limitations']).lower(), r'\bmeans?\b')
                self.assertIn('central-statistic definition', ' '.join(source['limitations']))

    def test_in_vivo_equilibrium_wording_and_scientific_scope(self):
        rule = self.data['interpretation_rules'][8]
        self.assertIn('equilibrium pH can also be measured in vivo', rule)
        self.assertIn('carbon-dioxide handling matters', rule)
        self.assertNotIn('collected/equilibrated', rule)
        self.assertEqual(len(self.data['presets']), 19)
        self.assertEqual(sum(p['ph'] is None for p in self.data['presets']), 11)
        self.assertFalse(self.data['default_enabled'])
        self.assertEqual(self.data['reviewed_date'], '2026-09-19')
        self.assertTrue(all(p['join_trajectory'] is False for p in self.data['presets']))
        self.assertTrue(all(s['correction_review_status'] == 'not_reviewed' for s in self.data['sources']))
        presets = {p['id']: p for p in self.data['presets']}
        self.assertEqual(presets['arterial-blood-uvm']['ph'], dict(value=None, lower=7.35, upper=7.45, confidence_level=None))
        self.assertEqual(presets['csf-merril-1961']['ph'], dict(value=7.31, lower=7.29, upper=7.33, confidence_level=0.99))
        self.assertEqual(presets['csf-merril-1961']['sample_size'], dict(value=12, unit='participants'))

    def test_reported_value_and_legacy_point_types_are_accepted_without_mutation(self):
        for statistic in ('reported_value', 'study_mean', 'single_condition'):
            data = copy.deepcopy(self.data)
            for p in data['presets']:
                if p['id'] in POINTS:
                    p['statistic'] = statistic
            before = copy.deepcopy(data)
            self.assertIs(validate_physiology_dataset(data), data)
            self.assertEqual(data, before)

    def test_malformed_reported_value_rejected_without_mutation(self):
        edits = [
            lambda p: p.update(statistic='reported_value '),
            lambda p: p.update(statistic='reported_mean'),
            lambda p: p.update(statistic='<script>alert(1)</script>'),
            lambda p: p.update(statistic=None),
            lambda p: p.update(statistic=True),
            lambda p: p.update(statistic={}),
            lambda p: p.update(display_kind='reference_band'),
            lambda p: p['ph'].update(value=None),
            lambda p: p['ph'].update(value=True),
            lambda p: p['ph'].update(value='7.09'),
            lambda p: p['ph'].update(value=float('inf')),
            lambda p: p['ph'].update(lower=6.95, upper=7.23),
            lambda p: p['ph'].update(confidence_level=0.95),
            lambda p: p['ph'].update(standard_error=0.14),
            lambda p: p['reported_uncertainty'].update(plotted=True),
            lambda p: p['reported_uncertainty'].update(kind='confidence_interval'),
            lambda p: p['reported_uncertainty'].update(value=-1),
            lambda p: p['reported_uncertainty'].update(value=True),
            lambda p: p.update(evidence_status='verified'),
            lambda p: p.update(join_trajectory=True),
            lambda p: p.update(ph=None),
        ]
        for index, edit in enumerate(edits):
            with self.subTest(index=index):
                data = copy.deepcopy(self.data)
                p = next(p for p in data['presets'] if p['id'] == 'neuronal-cytosol-bonnet-2019')
                p['statistic'] = 'reported_value'
                edit(p)
                before = copy.deepcopy(data)
                with self.assertRaises(PopulationError):
                    validate_physiology_dataset(data)
                self.assertEqual(data, before)

if __name__ == '__main__':
    unittest.main()
