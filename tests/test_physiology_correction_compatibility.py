"""Fresh synthetic P-validator-only tests, not reconstructed card304 evidence."""
import copy
import json
from pathlib import Path
import unittest

from nivram.populations import PopulationError, validate_physiology_dataset


class FreshCorrectionReviewCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset_path = Path(__file__).resolve().parents[1] / "nivram/data/physiology.json"
        cls.original_bytes = cls.dataset_path.read_bytes()
        cls.fixture = json.loads(cls.original_bytes)
        cls.fixture_before = copy.deepcopy(cls.fixture)

    def tearDown(self):
        self.assertEqual(self.fixture, self.fixture_before)
        self.assertEqual(self.dataset_path.read_bytes(), self.original_bytes)

    def assert_accepted_without_mutation(self, data):
        before = copy.deepcopy(data)
        self.assertIs(validate_physiology_dataset(data), data)
        self.assertEqual(data, before)

    def assert_rejected_without_mutation(self, values):
        for value in values:
            with self.subTest(value=value):
                data = copy.deepcopy(self.fixture)
                for source in data["sources"][:-1]:
                    source["correction_review_status"] = "not_reviewed"
                data["sources"][-1]["correction_review_status"] = value
                before = copy.deepcopy(data)
                with self.assertRaisesRegex(PopulationError, "Correction-review status"):
                    validate_physiology_dataset(data)
                self.assertEqual(data, before)

    def test_absent_status_is_valid_without_inserting_defaults(self):
        data = copy.deepcopy(self.fixture)
        for source in data["sources"]:
            source.pop("correction_review_status", None)
        self.assert_accepted_without_mutation(data)
        self.assertTrue(all("correction_review_status" not in s for s in data["sources"]))

    def test_unknown_is_accepted_without_mutation(self):
        data = copy.deepcopy(self.fixture)
        for source in data["sources"]:
            source["correction_review_status"] = "unknown"
        self.assert_accepted_without_mutation(data)

    def test_not_reviewed_is_accepted_without_mutation(self):
        data = copy.deepcopy(self.fixture)
        for source in data["sources"]:
            source["correction_review_status"] = "not_reviewed"
        self.assert_accepted_without_mutation(data)

    def test_mixed_valid_statuses_remain_distinct_and_unmodified(self):
        data = copy.deepcopy(self.fixture)
        self.assertEqual(len(data["sources"]), 6)
        for index, source in enumerate(data["sources"]):
            if index % 3 == 0:
                source.pop("correction_review_status", None)
            else:
                source["correction_review_status"] = ("unknown", "not_reviewed")[index % 3 - 1]
        self.assert_accepted_without_mutation(data)

    def test_null_is_rejected_without_mutation(self):
        self.assert_rejected_without_mutation([None])

    def test_non_strings_are_rejected_without_mutation(self):
        class StringSubclass(str):
            pass
        self.assert_rejected_without_mutation([
            True, False, 0, 1, 0.0, [], {}, b"unknown", StringSubclass("unknown")
        ])

    def test_noncanonical_strings_are_rejected_without_mutation(self):
        self.assert_rejected_without_mutation([
            "", "not-reviewed", "unknown ", " unknown", "UNKNOWN", "Not_reviewed"
        ])

    def test_unsupported_review_claims_are_rejected_without_mutation(self):
        self.assert_rejected_without_mutation(["reviewed", "no_corrections", "retracted"])


if __name__ == "__main__":
    unittest.main()
