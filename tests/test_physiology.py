import copy
import json
from pathlib import Path
import unittest

from nivram.populations import PopulationError, load_physiology_presets, validate_physiology_dataset


class PhysiologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_physiology_presets()
        cls.presets = {p["id"]: p for p in cls.data["presets"]}

    def test_dataset_passes_schema_and_all_numeric_entries_have_primary_sources(self):
        validate_physiology_dataset(self.data)
        sources = {s["id"]: s for s in self.data["sources"]}
        for preset in self.data["presets"]:
            if preset["ph"] is not None:
                self.assertTrue(preset["source_ids"])
                for sid in preset["source_ids"]:
                    self.assertIn(sources[sid]["source_type"], ("primary_research", "primary_clinical_catalog"))

    def test_arterial_interval_retains_exact_boundaries_and_specimen(self):
        p = self.presets["arterial-blood-uvm"]
        self.assertEqual((p["ph"]["lower"], p["ph"]["upper"]), (7.35, 7.45))
        self.assertIn("whole blood", p["compartment"].lower())
        self.assertEqual(p["statistic"], "reference_interval")
        self.assertEqual(p["display_kind"], "reference_band")

    def test_csf_confidence_interval_is_not_a_reference_interval(self):
        p = self.presets["csf-merril-1961"]
        self.assertEqual(p["statistic"], "mean_confidence_interval")
        self.assertEqual(p["ph"], {"value": 7.31, "lower": 7.29, "upper": 7.33, "confidence_level": 0.99})
        self.assertEqual(p["display_kind"], "confidence_interval")

    def test_neuronal_reported_value_preserves_patient_preparation_and_unknown_uncertainty(self):
        p = self.presets["neuronal-cytosol-bonnet-2019"]
        self.assertIn("epilepsy", p["population"].lower())
        self.assertIn("slice", p["preparation"].lower())
        self.assertEqual(p["ph"]["value"], 7.09)
        self.assertIsNone(p["ph"]["lower"])
        self.assertFalse(p["reported_uncertainty"]["plotted"])

    def test_nephron_is_luminal_and_never_final_urine(self):
        renal = [p for p in self.data["presets"] if p["group"].startswith("nephron_")]
        self.assertGreaterEqual(len(renal), 12)
        for p in renal:
            self.assertIn("luminal", p["compartment"].lower())
            self.assertFalse(p["join_trajectory"])
            self.assertNotIn("final urine", p["compartment"].lower())
            if p["ph"] is not None:
                self.assertEqual(p["species"], "Rat (species not specified in inspected abstract)")
                self.assertEqual(p["statistic"], "reported_value")
                self.assertEqual(p["display_kind"], "study_marker")

    def test_loop_bend_is_not_forced_into_monotonic_trajectory(self):
        proximal = self.presets["rat-late-proximal-dubose-1983"]
        loop = self.presets["rat-deep-loop-bend-dubose-1983"]
        self.assertEqual(proximal["ph"]["value"], 6.78)
        self.assertEqual(loop["ph"]["value"], 7.39)
        self.assertNotEqual(proximal["nephron_class"], loop["nephron_class"])

    def test_unsupported_segments_and_cleft_have_null_values(self):
        for sid in ("bowmans-space-gap", "early-proximal-gap", "thin-descending-gap",
                    "thin-ascending-gap", "thick-ascending-gap", "early-distal-gap",
                    "cortical-collecting-gap", "outer-medullary-collecting-gap", "synaptic-cleft-gap"):
            p = self.presets[sid]
            self.assertIsNone(p["ph"])
            self.assertEqual(p["display_kind"], "gap")
            self.assertEqual(p["gap_label"], "No verified numerical interval available")
        self.assertFalse(any("vesicle" in p["compartment"].lower() for p in self.data["presets"]))

    def test_missing_or_broken_provenance_is_rejected(self):
        broken = copy.deepcopy(self.data)
        broken["presets"][0]["source_ids"] = ["invented-source"]
        with self.assertRaises(PopulationError):
            validate_physiology_dataset(broken)
        broken = copy.deepcopy(self.data)
        del broken["presets"][0]["measurement_method"]
        with self.assertRaises(PopulationError):
            validate_physiology_dataset(broken)

    def test_manufactured_bands_and_gap_numbers_are_rejected(self):
        for sid, mutate in (
            ("neuronal-cytosol-bonnet-2019", lambda p: p["ph"].update(lower=6.95, upper=7.23)),
            ("bowmans-space-gap", lambda p: p.update(ph={"value": 7.4, "lower": None, "upper": None, "confidence_level": None})),
        ):
            data = copy.deepcopy(self.data)
            p = next(p for p in data["presets"] if p["id"] == sid)
            mutate(p)
            with self.assertRaises(PopulationError):
                validate_physiology_dataset(data)

    def test_duplicate_ids_and_nonfinite_data_are_rejected(self):
        duplicate = copy.deepcopy(self.data)
        duplicate["presets"].append(copy.deepcopy(duplicate["presets"][0]))
        with self.assertRaises(PopulationError):
            validate_physiology_dataset(duplicate)
        nonfinite = copy.deepcopy(self.data)
        nonfinite["presets"][0]["ph"]["lower"] = float("nan")
        with self.assertRaises(PopulationError):
            validate_physiology_dataset(nonfinite)

    def test_loading_does_not_share_mutable_dataset_state(self):
        changed = load_physiology_presets()
        changed["presets"].clear()
        self.assertTrue(load_physiology_presets()["presets"])
        json.dumps(self.data, allow_nan=False)

    def test_sd_band_cannot_be_inferred_from_unspecified_plus_minus(self):
        data = copy.deepcopy(self.data)
        p = next(p for p in data["presets"] if p["id"] == "neuronal-cytosol-bonnet-2019")
        p.update(statistic="mean_sd", display_kind="sd_interval")
        p["ph"].update(lower=6.95, upper=7.23)
        with self.assertRaises(PopulationError):
            validate_physiology_dataset(data)

    def test_correction_review_metadata_is_explicit_and_backward_compatible(self):
        expected_ids = {"uvm-arterial-catalog", "bonnet-2019", "merril-1961",
                        "dubose-1983", "dubose-1982-handling", "cleft-2023"}
        self.assertEqual({source["id"] for source in self.data["sources"]}, expected_ids)
        for source in self.data["sources"]:
            self.assertEqual(source["correction_review_status"], "not_reviewed")
        for metadata in ({}, {"correction_review_status": "unknown"},
                         {"correction_review_status": "not_reviewed"}):
            with self.subTest(metadata=metadata):
                data = copy.deepcopy(self.data)
                for source in data["sources"]:
                    source.pop("correction_review_status")
                    source.update(metadata)
                before = copy.deepcopy(data)
                self.assertIs(validate_physiology_dataset(data), data)
                self.assertEqual(data, before)

    def test_invalid_correction_review_is_rejected_without_mutation(self):
        for status in (None, True, False, 0, 1, [], {}, "", "not-reviewed",
                       "unknown ", "reviewed", "no_corrections", "retracted"):
            with self.subTest(status=status):
                data = copy.deepcopy(self.data)
                data["sources"][0]["correction_review_status"] = status
                before = copy.deepcopy(data)
                with self.assertRaisesRegex(PopulationError, "Correction-review status"):
                    validate_physiology_dataset(data)
                self.assertEqual(data, before)


if __name__ == "__main__":
    unittest.main()
