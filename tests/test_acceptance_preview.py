import copy
import json
import unittest

from nivram.acceptance_preview import preview_observation, validate_preview_snapshot


class AcceptancePreviewTests(unittest.TestCase):
    def setUp(self):
        self.valid = {'width': 2000, 'height': 720, 'corner': [255, 255, 255, 255],
                      'displayedWidth': 1600, 'devicePixelRatio': 1.25}

    def test_valid_fractional_dpi_snapshot_retains_exact_evidence(self):
        result = validate_preview_snapshot(self.valid)
        self.assertEqual(result, self.valid)
        result['corner'][0] = 0
        self.assertEqual(self.valid['corner'][0], 255)

    def test_uninitialized_json_null_scale_fails_assertion_before_arithmetic(self):
        self.valid['displayedWidth'] = None
        with self.assertRaisesRegex(AssertionError, 'scale'):
            validate_preview_snapshot(self.valid)
        evidence = preview_observation(self.valid)
        self.assertIsNone(evidence['displayedWidth'])
        json.dumps(evidence, allow_nan=False)

    def test_scale_rejects_non_numeric_nonfinite_or_nonpositive_values(self):
        for key in ('displayedWidth', 'devicePixelRatio'):
            for bad in (None, True, False, '1600', [], {}, 0, -1, float('nan'), float('inf'), -float('inf'), 10**400):
                with self.subTest(key=key, kind=type(bad).__name__):
                    value = {**self.valid, key: bad}
                    with self.assertRaises(AssertionError):
                        validate_preview_snapshot(value)
                    json.dumps(preview_observation(value), allow_nan=False)

    def test_dimensions_must_be_bounded_integers(self):
        for key, values in (('width', (None, True, 2000.0, 1999, '2000')),
                            ('height', (None, False, 0, -1, 720.0, 32769, 10**400))):
            for bad in values:
                with self.subTest(key=key, value=bad):
                    with self.assertRaises(AssertionError):
                        validate_preview_snapshot({**self.valid, key: bad})

    def test_white_corner_and_pixel_scale_are_asserted_independently(self):
        for corner in (None, [255]*3, [255]*5, [255, 255, 255, 0], [True]*4, [255.0]*4):
            with self.subTest(corner=corner):
                with self.assertRaisesRegex(AssertionError, 'white'):
                    validate_preview_snapshot({**self.valid, 'corner': corner})
        with self.assertRaisesRegex(AssertionError, 'physical'):
            validate_preview_snapshot({**self.valid, 'displayedWidth': 2000})

    def test_malformed_result_reports_only_bounded_numeric_and_type_evidence(self):
        for value in (None, False, 'private chemical text', [1, 2]):
            with self.assertRaises(AssertionError):
                validate_preview_snapshot(value)
            self.assertEqual(preview_observation(value), {'type': type(value).__name__})
        value = {**self.valid, 'displayedWidth': 'private chemical text',
                 'corner': ['private']*4, 'unrelated': 'private data'}
        previous = copy.deepcopy(value)
        evidence = preview_observation(value)
        self.assertNotIn('private', json.dumps(evidence))
        self.assertEqual(evidence['displayedWidthType'], 'str')
        self.assertEqual(evidence['cornerType'], 'list')
        self.assertEqual(value, previous)


if __name__ == '__main__':
    unittest.main()
