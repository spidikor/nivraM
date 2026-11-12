"""Analytical checks of machinery, not molecular-prediction benchmarks."""
import dataclasses
import json
import math
import unittest

from nivram.populations import (
    EquilibriumRatioEdge, JointState, MicroscopicPKaEdge, ModelConditions,
    ModelProvenance, PopulationError, PopulationModel, aggregate_populations,
    evaluate_populations, model_from_dict, model_from_microscopic_equilibria,
    ph_from_index, ph_to_index, population_capabilities, sample_grid,
    visible_populations,
)


def conditions(**changes):
    values = dict(
        temperature_kelvin=310.15, solvent="water", ionic_strength_mol_l=0.1,
        activity_convention="Synthetic fixed-ionic-strength apparent pKa model",
        reference_ph=7.0, ph_min=0.0, ph_max=14.0, energy_unit="kJ/mol",
        energy_basis="aqueous_transformed_gibbs_at_reference_ph",
        standard_state="Synthetic common molar standard state; fixed apparent activities",
        solvation_model="Synthetic analytical input; no molecule or solvent calculation",
        thermal_entropy_treatment="Included by assertion in synthetic state energies",
        degeneracy_convention="explicit", sampling_method="Exactly listed synthetic states",
        truncation_treatment="All states of the synthetic model listed",
    )
    values.update(changes)
    return ModelConditions(**values)


def provenance(**changes):
    values = dict(
        input_identity="synthetic-fixture-not-a-drawing", engine="analytical-fixture",
        engine_version="1", model_name="two-state acid", model_version="1",
        source="Synthetic mathematical acceptance fixture, not experimental data",
        created_utc="2026-09-19T00:00:00Z", applicability="Synthetic states only",
        validation="Analytical identities only; no molecular validation",
        source_kind="synthetic_analytical", transformations=(),
    )
    values.update(changes)
    return ModelProvenance(**values)


def state(sid, protons, *, energy=None, **changes):
    values = dict(
        state_id=sid, protonation_id="p" + str(protons), tautomer_id="t0",
        stereoisomer_id="fixed-unspecified", conformer_id="basin0",
        structure_id="synthetic-" + sid, atom_ids=("a0", "a1"),
        formal_charge=protons - 1, relative_protons=protons,
        free_energy_at_reference=energy, degeneracy=1,
    )
    values.update(changes)
    return JointState(**values)


def model(states, **changes):
    values = dict(
        states=states, conditions=conditions(), provenance=provenance(),
        reference_state_id=states[0].state_id, supported_classes=("protonation",),
        equilibrium_scope="conditional_configuration",
        equilibrium_justification="Synthetic proton-exchange equilibrium in one configuration",
        ensemble_complete=True,
    )
    values.update(changes)
    return PopulationModel(**values)


def acid_model(pka=7.0, **changes):
    states = (state("HA", 1), state("A", 0))
    values = dict(
        states=states, edges=(MicroscopicPKaEdge("HA", "A", pka),),
        conditions=conditions(), provenance=provenance(), reference_state_id="HA",
        supported_classes=("protonation",),
        equilibrium_scope="conditional_configuration",
        equilibrium_justification="Synthetic one-proton exchange",
        ensemble_complete=True,
    )
    values.update(changes)
    return model_from_microscopic_equilibria(**values)


class PopulationTests(unittest.TestCase):
    def test_monoprotic_analytical_identity_and_direction(self):
        m = acid_model(7.0)
        for ph in (0, 6, 7, 8, 14):
            r = evaluate_populations(m, ph)
            self.assertAlmostEqual(r.fractions[0], 1 / (1 + 10 ** (ph - 7)), places=14)
            self.assertAlmostEqual(math.fsum(r.fractions), 1.0, places=15)
        self.assertEqual(evaluate_populations(m, 7).fractions, (0.5, 0.5))
        r6, r7 = evaluate_populations(m, 6), evaluate_populations(m, 7)
        self.assertAlmostEqual((r7.fractions[1] / r7.fractions[0]) /
                               (r6.fractions[1] / r6.fractions[0]), 10)

    def test_two_proton_coupled_network_and_charge_aggregation(self):
        states = (state("H2", 2), state("HA", 1),
                  state("HB", 1, protonation_id="p1_other_site"), state("A", 0))
        edges = (MicroscopicPKaEdge("H2", "HA", 7), MicroscopicPKaEdge("H2", "HB", 9),
                 MicroscopicPKaEdge("HA", "A", 9), MicroscopicPKaEdge("HB", "A", 7))
        m = acid_model(states=states, edges=edges, reference_state_id="H2")
        r = evaluate_populations(m, 8)
        expected = (1 / 12.1, 10 / 12.1, 0.1 / 12.1, 1 / 12.1)
        for actual, wanted in zip(r.fractions, expected):
            self.assertAlmostEqual(actual, wanted, places=14)
        grouped = aggregate_populations(m, r, "formal_charge")
        self.assertAlmostEqual(grouped["0"], expected[1] + expected[2])
        self.assertAlmostEqual(math.fsum(grouped.values()), 1)

    def test_same_proton_count_tautomer_ratio_is_ph_independent(self):
        states = (state("t1", 1, tautomer_id="t1"), state("t2", 1, tautomer_id="t2"))
        m = acid_model(states=states, edges=(EquilibriumRatioEdge("t1", "t2", math.log10(4)),),
                       reference_state_id="t1", supported_classes=("tautomer",))
        for ph in (0, 7, 14):
            self.assertAlmostEqual(evaluate_populations(m, ph).fractions[1], 0.8)

    def test_inconsistent_cycle_is_rejected(self):
        states = (state("a", 1, tautomer_id="a"), state("b", 1, tautomer_id="b"),
                  state("c", 1, tautomer_id="c"))
        edges = (EquilibriumRatioEdge("a", "b", 1), EquilibriumRatioEdge("b", "c", 1),
                 EquilibriumRatioEdge("a", "c", 2.01))
        with self.assertRaisesRegex(PopulationError, "cycle"):
            acid_model(states=states, edges=edges, reference_state_id="a", supported_classes=("tautomer",))

    def test_disconnected_configurations_cannot_be_normalized_together(self):
        states = (state("r", 1, stereoisomer_id="R"), state("s", 1, stereoisomer_id="S"))
        with self.assertRaisesRegex(PopulationError, "Disconnected"):
            acid_model(states=states, edges=(), reference_state_id="r",
                       supported_classes=("stereoisomer",), equilibrium_scope="joint_equilibrium")

    def test_extreme_energies_are_stable_and_log_fractions_retained(self):
        m = model((state("HA", 1, energy=0), state("A", 0, energy=1e6)))
        r = evaluate_populations(m, 7)
        self.assertEqual(r.fractions, (1.0, 0.0))
        self.assertTrue(all(math.isfinite(x) for x in r.log_fractions))
        self.assertLess(r.log_fractions[1], -100000)
        json.dumps(r.to_dict(), allow_nan=False)

    def test_energy_units_and_common_offset(self):
        base = model((state("HA", 1, energy=0), state("A", 0, energy=10)))
        expected = evaluate_populations(base, 7).fractions
        for unit, scale in (("J/mol", 1000), ("kcal/mol", 1 / 4.184)):
            converted = model((state("HA", 1, energy=0), state("A", 0, energy=10 * scale)),
                              conditions=conditions(energy_unit=unit))
            for x, y in zip(expected, evaluate_populations(converted, 7).fractions):
                self.assertAlmostEqual(x, y, places=14)
        shifted = model((state("HA", 1, energy=1e8), state("A", 0, energy=1e8 + 10)))
        self.assertEqual(evaluate_populations(shifted, 7).fractions, expected)

    def test_degeneracy_counted_once_and_not_from_duplicates(self):
        states = (state("a", 1, energy=0, conformer_id="a"),
                  state("b", 1, energy=0, conformer_id="b", degeneracy=3))
        m = model(states, supported_classes=("conformer",))
        self.assertAlmostEqual(evaluate_populations(m, 7).fractions[1], 0.75)
        with self.assertRaisesRegex(PopulationError, "degeneracy"):
            model(states, supported_classes=("conformer",),
                  conditions=conditions(degeneracy_convention="included_in_free_energy"))
        duplicate = dataclasses.replace(states[0], state_id="duplicate-embedding")
        with self.assertRaisesRegex(PopulationError, "Duplicate.*identity"):
            model((states[0], duplicate), supported_classes=("conformer",))

    def test_microscopic_ratio_already_includes_degeneracy(self):
        states = (state("a", 1, conformer_id="a"), state("b", 1, conformer_id="b", degeneracy=3))
        m = acid_model(states=states, edges=(EquilibriumRatioEdge("a", "b", math.log10(3)),),
                       reference_state_id="a", supported_classes=("conformer",))
        self.assertAlmostEqual(evaluate_populations(m, 7).fractions[1], 0.75)
        self.assertAlmostEqual(m.states[1].free_energy_at_reference, 0, places=12)

    def test_preserves_stereoisomers_and_rejects_conditional_mixing(self):
        states = (state("r", 1, energy=0, stereoisomer_id="R"),
                  state("s", 1, energy=2, stereoisomer_id="S"))
        with self.assertRaisesRegex(PopulationError, "configuration"):
            model(states, supported_classes=("stereoisomer",))
        m = model(states, supported_classes=("stereoisomer",), equilibrium_scope="joint_equilibrium")
        groups = aggregate_populations(m, evaluate_populations(m, 7), "stereoisomer_id")
        self.assertEqual(set(groups), {"R", "S"})
        self.assertGreater(groups["R"], groups["S"])

    def test_visible_subset_is_never_renormalized(self):
        r = evaluate_populations(acid_model(), 7)
        self.assertEqual(visible_populations(r, ("HA",)), {"HA": 0.5})
        with self.assertRaises(PopulationError):
            visible_populations(r, ("HA", "HA"))

    def test_unknown_omitted_mass_is_explicitly_conditional(self):
        m = acid_model(ensemble_complete=False)
        r = evaluate_populations(m, 7)
        self.assertIsNone(r.omitted_fraction)
        self.assertEqual(r.normalization_scope, "conditional_on_represented_states")
        self.assertTrue(any("unknown" in text.lower() for text in r.warnings))
        with self.assertRaises(PopulationError):
            evaluate_populations(m, 7, omitted_fraction=0.2)
        r = evaluate_populations(m, 7, omitted_fraction=0.2, omitted_mass_source="External value at this pH")
        # Fractions are binary floats; test the analytical value with explicit
        # numerical tolerance, independently checking conservation of total mass.
        for fraction in r.fractions:
            self.assertAlmostEqual(fraction, 0.4, delta=1e-14)
        self.assertAlmostEqual(math.fsum(r.fractions) + r.omitted_fraction, 1.0, delta=1e-14)
        self.assertEqual(r.omitted_fraction, 0.2)
        self.assertAlmostEqual(visible_populations(r, ("HA",))["HA"], 0.4, delta=1e-14)

    def test_complete_model_rejects_nonzero_omitted_mass(self):
        with self.assertRaises(PopulationError):
            evaluate_populations(acid_model(), 7, omitted_fraction=0.2, omitted_mass_source="contradiction")

    def test_integer_grid_and_exact_domain_limits(self):
        grid = sample_grid(acid_model())
        self.assertEqual(len(grid), 141)
        self.assertEqual([row.index for row in grid], list(range(141)))
        self.assertEqual(grid[73].ph, 7.3)
        self.assertEqual(grid[140].ph, 14.0)
        self.assertEqual(grid[70].result.fractions, evaluate_populations(acid_model(), 7).fractions)
        narrowed = sample_grid(acid_model(conditions=conditions(reference_ph=7.4, ph_min=7.35, ph_max=7.45)))
        self.assertEqual([row.index for row in narrowed if row.status == "computed"], [74])
        self.assertIsNone(narrowed[73].result)
        self.assertEqual(narrowed[73].status, "outside_model_domain")

    def test_grid_conversions_do_not_round_overlay_boundaries(self):
        self.assertEqual(ph_to_index("7.3"), 73)
        self.assertEqual(ph_from_index(73), 7.3)
        with self.assertRaises(PopulationError):
            ph_to_index(7.35)
        for invalid in ("7.30000000000000000000000000001", "1e1000000000", "NaN"):
            with self.subTest(invalid=invalid), self.assertRaises(PopulationError):
                ph_to_index(invalid)
        with self.assertRaises(PopulationError):
            ph_from_index(True)
        with self.assertRaises(PopulationError):
            sample_grid(acid_model(), start_index=0, end_index=10001)

    def test_units_temperature_and_energy_basis_are_required(self):
        for changes in ({"energy_unit": "eV"}, {"temperature_kelvin": 0},
                        {"temperature_kelvin": float("nan")}, {"ionic_strength_mol_l": -1},
                        {"energy_basis": "MMFF_potential_energy"}, {"solvent": "hexane"}):
            with self.subTest(changes=changes), self.assertRaises(PopulationError):
                conditions(**changes)

    def test_finite_numbers_integer_stoichiometry_and_identity_guards(self):
        for changes in ({"free_energy_at_reference": float("inf")}, {"relative_protons": 1.5},
                        {"relative_protons": True}, {"degeneracy": 0}, {"atom_ids": ("a0", "a0")}):
            with self.subTest(changes=changes), self.assertRaises(PopulationError):
                state("x", 1, **changes)
        with self.assertRaisesRegex(PopulationError, "charge.*proton"):
            model((state("HA", 1, energy=0), state("A", 0, energy=0, formal_charge=0)))
        with self.assertRaisesRegex(PopulationError, "atom"):
            model((state("HA", 1, energy=0), state("A", 0, energy=0, atom_ids=("a0", "changed"))))
        with self.assertRaises(PopulationError):
            evaluate_populations(acid_model(), float("nan"))

    def test_model_roundtrip_and_result_identity(self):
        m = acid_model()
        restored = model_from_dict(json.loads(json.dumps(m.to_dict(), allow_nan=False)))
        self.assertEqual(m.signature, restored.signature)
        r = evaluate_populations(m, 7)
        self.assertEqual(r.input_identity, "synthetic-fixture-not-a-drawing")
        with self.assertRaisesRegex(PopulationError, "different model"):
            aggregate_populations(acid_model(8), r, "formal_charge")

    def test_labels_cannot_hide_proton_exchange_or_stereo_changes(self):
        states = (state("HA", 1, energy=0, protonation_id="shared", tautomer_id="t1"),
                  state("A", 0, energy=0, protonation_id="shared", tautomer_id="t2"))
        with self.assertRaisesRegex(PopulationError, "protonation_id"):
            model(states, supported_classes=("tautomer",))
        states = (state("r", 1, energy=0, stereoisomer_id="R", structure_id="same-graph"),
                  state("s", 1, energy=0, stereoisomer_id="S", structure_id="same-graph"))
        with self.assertRaisesRegex(PopulationError, "structure_id"):
            model(states, supported_classes=("stereoisomer",), equilibrium_scope="joint_equilibrium")

    def test_known_omitted_mass_is_not_reused_at_other_grid_samples(self):
        rows = sample_grid(acid_model(ensemble_complete=False), start_index=69, end_index=71,
                           omitted_fractions={70: 0.2}, omitted_mass_source="External pH 7 measurement")
        self.assertIsNone(rows[0].result.omitted_fraction)
        self.assertIsNone(rows[2].result.omitted_fraction)
        self.assertAlmostEqual(math.fsum(rows[1].result.fractions), 0.8, delta=1e-14)

    def test_microscopic_reference_state_degeneracy_is_removed_once(self):
        states = (state("a", 1, conformer_id="a", degeneracy=3), state("b", 1, conformer_id="b"))
        m = acid_model(states=states, edges=(EquilibriumRatioEdge("a", "b", -math.log10(3)),),
                       reference_state_id="a", supported_classes=("conformer",))
        self.assertAlmostEqual(evaluate_populations(m, 7).fractions[0], 0.75)
        self.assertAlmostEqual(m.states[1].free_energy_at_reference, 0, places=12)

    def test_no_default_scientific_predictor_or_supported_population_claims(self):
        caps = population_capabilities()
        self.assertFalse(caps["molecular_predictor_available"])
        self.assertTrue(all(value["status"] == "unavailable" for value in caps["state_classes"].values()))
        m = acid_model()
        self.assertEqual(m.capabilities()["tautomer"]["status"], "unavailable")
        self.assertEqual(m.capabilities()["protonation"]["status"], "supplied_model_only")
        self.assertIn("synthetic", evaluate_populations(m, 7).to_dict()["provenance"]["source_kind"])
        with self.assertRaisesRegex(PopulationError, "unavailable"):
            aggregate_populations(m, evaluate_populations(m, 7), "stereoisomer_id")


if __name__ == "__main__":
    unittest.main()
