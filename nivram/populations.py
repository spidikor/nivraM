"""maraPssiwS population arithmetic and evidence-qualified pH context.

This module does not predict a molecular pKa, free energy, tautomer, stereo ratio,
or conformer ensemble. It evaluates an explicitly supplied, compatible model.
Declarations of comparability, identity and applicability remain caller assertions.
There are no default molecule models, network operations, or third-party imports.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, DecimalException, localcontext
import hashlib
import json
import math
from pathlib import Path
from typing import Mapping


GAS_CONSTANT_KJ_MOL_K = 0.00831446261815324
LN10 = math.log(10.0)
STATE_CLASSES = ("protonation", "tautomer", "stereoisomer", "conformer")
ENERGY_TO_KJ_MOL = {"kJ/mol": 1.0, "J/mol": 0.001, "kcal/mol": 4.184}
MAX_STATES = 4096
MAX_GRID_SAMPLES = 10001
MAX_GRID_STATE_EVALUATIONS = 1_000_000
GAP_LABEL = "No verified numerical interval available"
PHYSIOLOGY_SCHEMA_VERSION = 1


class PopulationError(ValueError):
    """An input does not meet the mathematical or evidence-data contract."""


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 8192:
        raise PopulationError(f"{name} must be a nonempty bounded string.")
    return value


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PopulationError(f"{name} must be a finite number.")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise PopulationError(f"{name} must be a finite number.") from exc
    if not math.isfinite(result):
        raise PopulationError(f"{name} must be a finite number.")
    return result


def _integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise PopulationError(f"{name} must be an integer.")
    if abs(value) > 1_000_000_000:
        raise PopulationError(f"{name} exceeds the supported integer bound.")
    return value


def _items(value, name, maximum=MAX_STATES):
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise PopulationError(f"{name} must be a bounded list or tuple.")
    return tuple(value)


@dataclass(frozen=True)
class JointState:
    """One unique basin in a caller-defined joint chemical ensemble.

    atom_ids identifies the same non-exchanged atomic core across all states.
    structure_id must come from an upstream stereo-preserving chemical identity
    system; this arithmetic layer cannot determine chemical equivalence.
    None energy is permitted only while building a microscopic equilibrium model.
    """
    state_id: str
    protonation_id: str
    tautomer_id: str
    stereoisomer_id: str
    conformer_id: str
    structure_id: str
    atom_ids: tuple[str, ...]
    formal_charge: int
    relative_protons: int
    free_energy_at_reference: float | None
    degeneracy: int = 1

    def __post_init__(self):
        for name in ("state_id", "protonation_id", "tautomer_id", "stereoisomer_id", "conformer_id", "structure_id"):
            _text(getattr(self, name), name)
        ids = _items(self.atom_ids, "atom_ids")
        if not ids or len(set(_text(x, "atom_id") for x in ids)) != len(ids):
            raise PopulationError("atom_ids must be nonempty and unique.")
        object.__setattr__(self, "atom_ids", ids)
        _integer(self.formal_charge, "formal_charge")
        _integer(self.relative_protons, "relative_protons")
        if _integer(self.degeneracy, "degeneracy") < 1:
            raise PopulationError("degeneracy must be a positive integer.")
        if self.free_energy_at_reference is not None:
            _number(self.free_energy_at_reference, "free_energy_at_reference")

    @property
    def basin_identity(self):
        return (self.protonation_id, self.tautomer_id, self.stereoisomer_id, self.conformer_id)


@dataclass(frozen=True)
class ModelConditions:
    temperature_kelvin: float
    solvent: str
    ionic_strength_mol_l: float | None
    activity_convention: str
    reference_ph: float
    ph_min: float
    ph_max: float
    energy_unit: str
    energy_basis: str
    standard_state: str
    solvation_model: str
    thermal_entropy_treatment: str
    degeneracy_convention: str
    sampling_method: str
    truncation_treatment: str

    def __post_init__(self):
        if _number(self.temperature_kelvin, "temperature_kelvin") <= 0:
            raise PopulationError("temperature_kelvin must be positive.")
        if GAS_CONSTANT_KJ_MOL_K * self.temperature_kelvin == 0:
            raise PopulationError("temperature_kelvin is below the numerical support limit.")
        if self.solvent != "water":
            raise PopulationError("This model contract currently supports aqueous solvent 'water' only.")
        if self.ionic_strength_mol_l is not None and _number(self.ionic_strength_mol_l, "ionic_strength_mol_l") < 0:
            raise PopulationError("ionic_strength_mol_l cannot be negative.")
        for name in ("activity_convention", "standard_state", "solvation_model", "thermal_entropy_treatment", "sampling_method", "truncation_treatment"):
            _text(getattr(self, name), name)
        lo, hi, ref = (_number(getattr(self, name), name) for name in ("ph_min", "ph_max", "reference_ph"))
        if not -100 <= lo <= ref <= hi <= 100:
            raise PopulationError("Declare an ordered model pH domain within -100..100 containing reference_ph.")
        _text(self.energy_unit, "energy_unit")
        if self.energy_unit not in ENERGY_TO_KJ_MOL:
            raise PopulationError("energy_unit must be J/mol, kJ/mol, or kcal/mol.")
        if self.energy_basis != "aqueous_transformed_gibbs_at_reference_ph":
            raise PopulationError("Supply compatible aqueous transformed Gibbs energies at reference_ph, not raw potential/electronic energies.")
        if self.degeneracy_convention not in ("explicit", "included_in_free_energy"):
            raise PopulationError("Declare whether degeneracy is explicit or included_in_free_energy.")


@dataclass(frozen=True)
class ModelProvenance:
    input_identity: str
    engine: str
    engine_version: str
    model_name: str
    model_version: str
    source: str
    created_utc: str
    applicability: str
    validation: str
    source_kind: str
    transformations: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ("input_identity", "engine", "engine_version", "model_name", "model_version", "source", "created_utc", "applicability", "validation"):
            _text(getattr(self, name), name)
        if self.source_kind not in ("external_model", "synthetic_analytical"):
            raise PopulationError("source_kind must identify an external_model or synthetic_analytical input.")
        try:
            when = datetime.fromisoformat(self.created_utc.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PopulationError("created_utc must be an ISO UTC timestamp.") from exc
        if when.tzinfo is None or when.utcoffset() != timedelta(0):
            raise PopulationError("created_utc must be an ISO UTC timestamp.")
        transformations = _items(self.transformations, "transformations", 100)
        for text in transformations:
            _text(text, "transformation")
        object.__setattr__(self, "transformations", transformations)


@dataclass(frozen=True)
class PopulationModel:
    states: tuple[JointState, ...]
    conditions: ModelConditions
    provenance: ModelProvenance
    reference_state_id: str
    supported_classes: tuple[str, ...]
    equilibrium_scope: str
    equilibrium_justification: str
    ensemble_complete: bool
    _signature: str = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        states = _items(self.states, "states")
        if not states or not all(isinstance(s, JointState) for s in states):
            raise PopulationError("Supply at least one JointState.")
        if not isinstance(self.conditions, ModelConditions) or not isinstance(self.provenance, ModelProvenance):
            raise PopulationError("Conditions and provenance are required typed records.")
        classes = _items(self.supported_classes, "supported_classes", len(STATE_CLASSES))
        for name in classes:
            _text(name, "supported state class")
        if not classes or len(set(classes)) != len(classes) or any(c not in STATE_CLASSES for c in classes):
            raise PopulationError("supported_classes must list distinct known state classes.")
        if type(self.ensemble_complete) is not bool:
            raise PopulationError("ensemble_complete must explicitly be true or false.")
        _text(self.equilibrium_justification, "equilibrium_justification")
        if self.equilibrium_scope not in ("joint_equilibrium", "conditional_configuration"):
            raise PopulationError("Declare joint_equilibrium or conditional_configuration scope.")
        ids, identities = set(), set()
        protonation_counts, structure_identities = {}, {}
        core_ids = set(states[0].atom_ids)
        charge_offset = states[0].formal_charge - states[0].relative_protons
        for s in states:
            if s.state_id in ids:
                raise PopulationError("Duplicate state_id.")
            if s.basin_identity in identities:
                raise PopulationError("Duplicate joint-state basin identity; embeddings are not multiplicity.")
            ids.add(s.state_id)
            identities.add(s.basin_identity)
            if s.protonation_id in protonation_counts and protonation_counts[s.protonation_id] != s.relative_protons:
                raise PopulationError("A protonation_id cannot alias states with different proton counts.")
            protonation_counts[s.protonation_id] = s.relative_protons
            chemical_identity = (s.protonation_id, s.tautomer_id, s.stereoisomer_id, s.formal_charge, s.relative_protons)
            if s.structure_id in structure_identities and structure_identities[s.structure_id] != chemical_identity:
                raise PopulationError("A structure_id cannot alias different chemical or stereo identities.")
            structure_identities[s.structure_id] = chemical_identity
            if set(s.atom_ids) != core_ids:
                raise PopulationError("All states must preserve the same mapped atomic core.")
            if s.formal_charge - s.relative_protons != charge_offset:
                raise PopulationError("Formal charge differences must match proton stoichiometry; this is not a redox/ion-binding model.")
            if s.free_energy_at_reference is None:
                raise PopulationError("Every evaluated state needs a compatible reference free energy.")
            energy = s.free_energy_at_reference * ENERGY_TO_KJ_MOL[self.conditions.energy_unit]
            _number(energy, "converted reference free energy")
            if self.conditions.degeneracy_convention == "included_in_free_energy" and s.degeneracy != 1:
                raise PopulationError("degeneracy must be 1 when already included in the free energy.")
        if self.reference_state_id not in ids:
            raise PopulationError("reference_state_id must identify a supplied state.")
        if self.equilibrium_scope == "conditional_configuration":
            if len({s.stereoisomer_id for s in states}) != 1 or "stereoisomer" in classes:
                raise PopulationError("A conditional configuration model cannot establish between-configuration populations.")
        for name, attr in (("protonation", "protonation_id"), ("tautomer", "tautomer_id"), ("stereoisomer", "stereoisomer_id"), ("conformer", "conformer_id")):
            if len({getattr(s, attr) for s in states}) > 1 and name not in classes:
                raise PopulationError(f"Varying {name} states require an explicit supplied-model capability declaration.")
        object.__setattr__(self, "states", states)
        object.__setattr__(self, "supported_classes", classes)
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        object.__setattr__(self, "_signature", hashlib.sha256(payload.encode("utf-8")).hexdigest())

    @property
    def signature(self):
        return self._signature

    def to_dict(self):
        return {
            "states": [asdict(s) for s in self.states], "conditions": asdict(self.conditions),
            "provenance": asdict(self.provenance), "reference_state_id": self.reference_state_id,
            "supported_classes": list(self.supported_classes), "equilibrium_scope": self.equilibrium_scope,
            "equilibrium_justification": self.equilibrium_justification, "ensemble_complete": self.ensemble_complete,
        }

    def capabilities(self):
        return {
            name: {"status": "supplied_model_only" if name in self.supported_classes else "unavailable",
                   "reason": "Caller-supplied thermodynamics; scientific validity not verified by this evaluator."
                   if name in self.supported_classes else "No compatible quantitative model supplied for this state class."}
            for name in STATE_CLASSES
        }


def model_from_dict(payload: Mapping) -> PopulationModel:
    """Strict deserialization; no missing energies, inferred units, or defaults."""
    if not isinstance(payload, dict):
        raise PopulationError("Model must be a JSON object.")
    try:
        data = dict(payload)
        data["states"] = tuple(JointState(**s) for s in _items(data["states"], "states"))
        data["conditions"] = ModelConditions(**data["conditions"])
        data["provenance"] = ModelProvenance(**data["provenance"])
        return PopulationModel(**data)
    except (KeyError, TypeError) as exc:
        raise PopulationError("Model fields do not match the documented strict schema.") from exc


@dataclass(frozen=True)
class MicroscopicPKaEdge:
    """One-proton dissociation: target/source = 10**(pH - pKa).

    The supplied equilibrium ratio includes any multiplicity of these states.
    It is not an intrinsic site pKa to which an unknown statistical factor is added.
    """
    protonated_state_id: str
    deprotonated_state_id: str
    pka: float

    def __post_init__(self):
        _text(self.protonated_state_id, "protonated_state_id")
        _text(self.deprotonated_state_id, "deprotonated_state_id")
        _number(self.pka, "pka")


@dataclass(frozen=True)
class EquilibriumRatioEdge:
    """log10(P_target / P_source) at the model's reference pH, including multiplicity."""
    source_state_id: str
    target_state_id: str
    log10_ratio_at_reference: float

    def __post_init__(self):
        _text(self.source_state_id, "source_state_id")
        _text(self.target_state_id, "target_state_id")
        _number(self.log10_ratio_at_reference, "log10_ratio_at_reference")


def model_from_microscopic_equilibria(*, states, edges, conditions, provenance,
                                     reference_state_id, supported_classes,
                                     equilibrium_scope, equilibrium_justification,
                                     ensemble_complete, cycle_tolerance_log10=1e-8):
    """Resolve a connected, thermodynamically consistent supplied microscopic graph.

    State energies must initially be None; no supplied energy is silently replaced.
    Disconnected state families have no shared normalization and are rejected.
    """
    states = _items(states, "states")
    edges = _items(edges, "edges", MAX_STATES * 8)
    if not states or not all(isinstance(s, JointState) for s in states):
        raise PopulationError("Supply JointState records.")
    if not isinstance(conditions, ModelConditions):
        raise PopulationError("Supply ModelConditions.")
    if any(s.free_energy_at_reference is not None for s in states):
        raise PopulationError("Microscopic graph input energies must be None; do not mix energy sources implicitly.")
    tolerance = _number(cycle_tolerance_log10, "cycle_tolerance_log10")
    if not 0 < tolerance <= 1e-4:
        raise PopulationError("cycle_tolerance_log10 must be positive and no greater than 1e-4.")
    by_id = {s.state_id: s for s in states}
    if len(by_id) != len(states) or reference_state_id not in by_id:
        raise PopulationError("State IDs must be unique and include the reference state.")
    graph = {sid: [] for sid in by_id}
    for edge in edges:
        if isinstance(edge, MicroscopicPKaEdge):
            source, target = edge.protonated_state_id, edge.deprotonated_state_id
            if source not in by_id or target not in by_id:
                raise PopulationError("Equilibrium edge references an unknown state.")
            if by_id[source].relative_protons - by_id[target].relative_protons != 1:
                raise PopulationError("A microscopic pKa edge must release exactly one proton.")
            delta = conditions.reference_ph - edge.pka
        elif isinstance(edge, EquilibriumRatioEdge):
            source, target = edge.source_state_id, edge.target_state_id
            delta = edge.log10_ratio_at_reference
        else:
            raise PopulationError("Unknown microscopic equilibrium edge type.")
        if source not in by_id or target not in by_id or source == target:
            raise PopulationError("Equilibrium edges need two distinct known states.")
        graph[source].append((target, delta))
        graph[target].append((source, -delta))
    log_weights = {reference_state_id: 0.0}
    queue = deque((reference_state_id,))
    while queue:
        source = queue.popleft()
        for target, delta in graph[source]:
            candidate = _number(log_weights[source] + delta, "microscopic log ratio")
            if target in log_weights:
                if not math.isclose(candidate, log_weights[target], rel_tol=0, abs_tol=tolerance):
                    raise PopulationError("Inconsistent thermodynamic cycle in the supplied microscopic model.")
            else:
                log_weights[target] = candidate
                queue.append(target)
    if len(log_weights) != len(states):
        raise PopulationError("Disconnected state families lack compatible relative free energies.")
    explicit = conditions.degeneracy_convention == "explicit"
    ref_log_g = math.log10(by_id[reference_state_id].degeneracy) if explicit else 0.0
    rt = GAS_CONSTANT_KJ_MOL_K * conditions.temperature_kelvin
    factor = ENERGY_TO_KJ_MOL[conditions.energy_unit]
    resolved = []
    for s in states:
        log_g = math.log10(s.degeneracy) if explicit else 0.0
        energy = -rt * LN10 * (log_weights[s.state_id] - log_g + ref_log_g) / factor
        resolved.append(replace(s, free_energy_at_reference=energy))
    return PopulationModel(tuple(resolved), conditions, provenance, reference_state_id,
                           supported_classes, equilibrium_scope, equilibrium_justification, ensemble_complete)


@dataclass(frozen=True)
class PopulationResult:
    ph: float
    state_ids: tuple[str, ...]
    fractions: tuple[float, ...]
    log_fractions: tuple[float, ...]
    model_signature: str
    input_identity: str
    normalization_scope: str
    omitted_fraction: float | None
    omitted_mass_source: str | None
    warnings: tuple[str, ...]
    conditions: ModelConditions
    provenance: ModelProvenance

    def to_dict(self):
        result = asdict(self)
        result["percentages"] = [100.0 * p for p in self.fractions]
        result["log_fraction_base"] = "e"
        return result


def evaluate_populations(model: PopulationModel, ph, *, omitted_fraction=None, omitted_mass_source=None):
    """Calculate stable joint-state weights only inside the declared model domain.

    G_i(pH) = G_i(ref) + n_H,i RT ln(10) (pH - ref).
    Explicit multiplicity contributes ln(g_i) once. A common energy/proton anchor
    is subtracted before normalization; it has no effect on relative populations.
    Unknown omitted mass produces conditional fractions, not total-molecule ones.
    """
    if not isinstance(model, PopulationModel):
        raise PopulationError("An explicit PopulationModel is required; no predictor is configured.")
    ph = _number(ph, "pH")
    c = model.conditions
    if not c.ph_min <= ph <= c.ph_max:
        raise PopulationError("pH is outside the supplied model's declared domain; extrapolation is disabled.")
    warnings = ["Mathematical evaluation of supplied thermodynamics; not independently validated molecular predictions."]
    if model.provenance.source_kind == "synthetic_analytical":
        warnings.append("Synthetic analytical fixture only; not an analysis of the drawing.")
    if model.equilibrium_scope == "conditional_configuration":
        warnings.append("Conditional on the supplied configuration; between-configuration fractions are unavailable.")
    if omitted_fraction is not None:
        omitted_fraction = _number(omitted_fraction, "omitted_fraction")
        if not 0 <= omitted_fraction < 1:
            raise PopulationError("Known omitted_fraction must be at least 0 and less than 1.")
        _text(omitted_mass_source, "omitted_mass_source")
        if model.ensemble_complete and omitted_fraction != 0:
            raise PopulationError("A declared complete ensemble cannot have nonzero omitted mass.")
        scope = "declared_complete_supplied_model" if model.ensemble_complete else "supplied_total_mass"
    elif omitted_mass_source is not None:
        raise PopulationError("omitted_mass_source requires an omitted_fraction at this pH.")
    elif model.ensemble_complete:
        omitted_fraction = 0.0
        scope = "declared_complete_supplied_model"
    else:
        scope = "conditional_on_represented_states"
        warnings.append("Omitted population is unknown; percentages are conditional on represented states and do not establish full-ensemble fractions.")
    anchor = next(s for s in model.states if s.state_id == model.reference_state_id)
    rt = GAS_CONSTANT_KJ_MOL_K * c.temperature_kelvin
    factor = ENERGY_TO_KJ_MOL[c.energy_unit]
    weights = []
    for s in model.states:
        delta_g = (s.free_energy_at_reference - anchor.free_energy_at_reference) * factor
        delta_n = s.relative_protons - anchor.relative_protons
        value = -delta_g / rt - delta_n * LN10 * (ph - c.reference_ph)
        if c.degeneracy_convention == "explicit":
            value += math.log(s.degeneracy)
        weights.append(_number(value, "log weight"))
    largest = max(weights)
    shifted = [_number(w - largest, "shifted log weight") for w in weights]
    scaled_weights = tuple(math.exp(w) for w in shifted)
    normalizer = math.fsum(scaled_weights)
    log_sum = math.log(normalizer)
    log_scale = math.log1p(-omitted_fraction) if omitted_fraction is not None else 0.0
    log_fractions = tuple(w - log_sum + log_scale for w in shifted)
    represented_mass = 1.0 - omitted_fraction if omitted_fraction is not None else 1.0
    fractions = tuple((w / normalizer) * represented_mass for w in scaled_weights)
    return PopulationResult(ph, tuple(s.state_id for s in model.states), fractions, log_fractions,
                            model.signature, model.provenance.input_identity, scope,
                            omitted_fraction, omitted_mass_source, tuple(warnings), c, model.provenance)


def aggregate_populations(model, result, by="formal_charge"):
    """Sum the same joint ensemble; never recompute or normalize a marginal."""
    if not isinstance(model, PopulationModel) or not isinstance(result, PopulationResult):
        raise PopulationError("Supply a model and its evaluated result.")
    if result.model_signature != model.signature or result.state_ids != tuple(s.state_id for s in model.states):
        raise PopulationError("This result belongs to a different model.")
    if by not in ("formal_charge", "relative_protons", "protonation_id", "tautomer_id", "stereoisomer_id", "conformer_id"):
        raise PopulationError("Unsupported aggregation dimension.")
    if by.endswith("_id") and by[:-3] not in model.supported_classes:
        raise PopulationError("This quantitative state-class aggregation is unavailable in the supplied model.")
    groups = {}
    for s, fraction in zip(model.states, result.fractions):
        groups.setdefault(str(getattr(s, by)), []).append(fraction)
    return {key: math.fsum(values) for key, values in groups.items()}


def visible_populations(result, state_ids):
    """Select already computed fractions without rescaling hidden states away."""
    if not isinstance(result, PopulationResult):
        raise PopulationError("Supply an evaluated PopulationResult.")
    chosen = _items(state_ids, "visible state_ids")
    if len(set(chosen)) != len(chosen) or any(s not in result.state_ids for s in chosen):
        raise PopulationError("Visible state IDs must be distinct members of the full result.")
    values = dict(zip(result.state_ids, result.fractions))
    return {sid: values[sid] for sid in chosen}


def ph_from_index(index):
    """Integer tenths are the canonical sample coordinate; no repeated addition."""
    return _integer(index, "pH index") / 10.0


def ph_to_index(ph):
    """Accept exactly a tenth; physiology bounds intentionally bypass this function."""
    if isinstance(ph, bool) or not isinstance(ph, (str, int, float, Decimal)):
        raise PopulationError("pH must identify an exact 0.1 sample.")
    try:
        text = str(ph)
        if len(text) > 128:
            raise PopulationError("pH input is too long.")
        with localcontext() as ctx:
            ctx.prec = 132
            value = Decimal(text)
            if not value.is_finite() or abs(value) > 100_000_000:
                raise PopulationError("pH is outside the bounded grid index domain.")
            value *= 10
            if value != value.to_integral_value():
                raise PopulationError("pH must identify an exact 0.1 sample; values are not rounded.")
            return int(value)
    except (DecimalException, ValueError, OverflowError) as exc:
        raise PopulationError("pH must identify an exact 0.1 sample.") from exc


@dataclass(frozen=True)
class GridSample:
    index: int
    ph: float
    status: str
    result: PopulationResult | None
    reason: str | None

    def to_dict(self):
        return {"index": self.index, "ph": self.ph, "status": self.status,
                "result": self.result.to_dict() if self.result is not None else None, "reason": self.reason}


def sample_grid(model, start_index=0, end_index=140, *, omitted_fractions=None, omitted_mass_source=None):
    """Cacheable 0.1-pH samples, retaining unsupported portions as explicit gaps."""
    if not isinstance(model, PopulationModel):
        raise PopulationError("An explicit model is required.")
    start_index, end_index = _integer(start_index, "start_index"), _integer(end_index, "end_index")
    count = end_index - start_index + 1
    if not 1 <= count <= MAX_GRID_SAMPLES or count * len(model.states) > MAX_GRID_STATE_EVALUATIONS:
        raise PopulationError("Requested population grid exceeds the bounded workload.")
    if omitted_fractions is not None:
        if not isinstance(omitted_fractions, dict):
            raise PopulationError("omitted_fractions must map integer grid indices to externally supplied masses.")
        _text(omitted_mass_source, "omitted_mass_source")
        for index in omitted_fractions:
            _integer(index, "omitted mass index")
            if not start_index <= index <= end_index or not model.conditions.ph_min <= ph_from_index(index) <= model.conditions.ph_max:
                raise PopulationError("Omitted masses must belong to requested supported grid samples.")
    elif omitted_mass_source is not None:
        raise PopulationError("omitted_mass_source requires per-sample omitted_fractions.")
    rows = []
    for index in range(start_index, end_index + 1):
        ph = ph_from_index(index)
        if not model.conditions.ph_min <= ph <= model.conditions.ph_max:
            rows.append(GridSample(index, ph, "outside_model_domain", None, "No extrapolation outside the supplied model domain."))
        else:
            has_mass = omitted_fractions is not None and index in omitted_fractions
            result = evaluate_populations(model, ph, omitted_fraction=omitted_fractions[index] if has_mass else None,
                                          omitted_mass_source=omitted_mass_source if has_mass else None)
            rows.append(GridSample(index, ph, "computed", result, None))
    return tuple(rows)


def population_capabilities():
    """Installed capability, independent of caller-supplied analytical fixtures."""
    return {
        "module": "maraPssiwS", "molecular_predictor_available": False,
        "mathematical_evaluator_available": True, "external_providers_enabled": False,
        "state_classes": {name: {"status": "unavailable", "reason": "No integrated, independently validated molecular population predictor."}
                          for name in STATE_CLASSES},
        "scope": "Arithmetic for explicit compatible models; no scientific model is fabricated from the drawing.",
    }


def validate_physiology_dataset(data):
    """Validate schema v1 and statistical display semantics without reinterpreting evidence."""
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != PHYSIOLOGY_SCHEMA_VERSION:
        raise PopulationError("Unsupported physiology schema version.")
    for name in ("dataset_version", "purpose", "gap_label", "reviewed_date"):
        _text(data.get(name), name)
    if data["gap_label"] != GAP_LABEL or data.get("default_enabled") is not False:
        raise PopulationError("Physiology overlays must be explicitly selected and retain the evidence-gap label.")
    try:
        date.fromisoformat(data["reviewed_date"])
    except ValueError as exc:
        raise PopulationError("reviewed_date must be an ISO date.") from exc
    rules = _items(data.get("interpretation_rules"), "interpretation_rules", 100)
    if not rules:
        raise PopulationError("Physiology interpretation rules are required.")
    for rule in rules:
        _text(rule, "interpretation_rule")
    sources = {}
    for source in _items(data.get("sources"), "sources", 100):
        if not isinstance(source, dict):
            raise PopulationError("Every physiology source must be an object.")
        for key in ("id", "title", "url", "source_type", "inspected_scope", "accessed_date"):
            _text(source.get(key), "source." + key)
        if source["id"] in sources or not source["url"].startswith("https://"):
            raise PopulationError("Source IDs must be unique with HTTPS provenance URLs.")
        if source["source_type"] not in ("primary_research", "primary_clinical_catalog"):
            raise PopulationError("Numerical physiology evidence requires a primary source.")
        if "publication_date" not in source or "limitations" not in source:
            raise PopulationError("Sources must preserve publication-date availability and limitations.")
        # Legacy schema-v1 absence means unknown; do not normalize or mutate input.
        # These states record review provenance, never correction clearance.
        correction_review = source.get("correction_review_status", "unknown")
        if type(correction_review) is not str or correction_review not in ("not_reviewed", "unknown"):
            raise PopulationError("Correction-review status must be not_reviewed or unknown.")
        sources[source["id"]] = source
    required_text = ("id", "group", "label", "compartment", "anatomical_site", "species", "population", "preparation",
                     "condition", "temperature_note", "measurement_method", "specimen_handling", "statistic", "display_kind", "evidence_status")
    groups = {"plasma_blood", "neuronal_cytosol", "synaptic_cleft", "csf", "nephron_bowmans",
              "nephron_proximal", "nephron_loop", "nephron_distal", "nephron_collecting"}
    displays = {"reference_interval": "reference_band", "observed_range": "observed_range",
                "study_mean": "study_marker", "reported_value": "study_marker", "single_condition": "study_marker",
                "mean_confidence_interval": "confidence_interval", "mean_sd": "sd_interval", "mean_sem": "sem_interval"}
    ids = set()
    presets = _items(data.get("presets"), "presets", 100)
    if not presets:
        raise PopulationError("The physiology dataset must contain explicit contexts.")
    for preset in presets:
        if not isinstance(preset, dict):
            raise PopulationError("Every physiology preset must be an object.")
        for key in required_text:
            _text(preset.get(key), "preset." + key)
        if preset["id"] in ids or preset["group"] not in groups:
            raise PopulationError("Preset IDs must be unique and groups must be recognized.")
        ids.add(preset["id"])
        for key in ("ph", "temperature_c", "reported_uncertainty", "sample_size", "nephron_class", "gap_label", "gap_reason"):
            if key not in preset:
                raise PopulationError(f"Preset must preserve {key}, including explicit null when unknown.")
        if preset.get("join_trajectory") is not False:
            raise PopulationError("Presets cannot be joined into a presumed measured trajectory.")
        if preset["temperature_c"] is not None:
            _number(preset["temperature_c"], "temperature_c")
        limitations = _items(preset.get("evidence_limitations"), "evidence_limitations", 50)
        if not limitations:
            raise PopulationError("Evidence limitations must remain explicit.")
        for limitation in limitations:
            _text(limitation, "evidence limitation")
        sids = _items(preset.get("source_ids"), "source_ids", 20)
        for sid in sids:
            _text(sid, "source_id")
        if len(set(sids)) != len(sids) or any(sid not in sources for sid in sids):
            raise PopulationError("Preset references an unknown or duplicate source.")
        if preset["group"].startswith("nephron_") and ("luminal" not in preset["compartment"].lower() or "final urine" in preset["compartment"].lower()):
            raise PopulationError("Renal presets describe nephron luminal fluid, not final urine or renal-cell pH.")
        if preset["group"] == "synaptic_cleft" and "vesicle" in preset["compartment"].lower():
            raise PopulationError("Synaptic cleft and vesicle lumen are distinct compartments.")
        if preset["ph"] is None:
            if preset["statistic"] != "unavailable" or preset["display_kind"] != "gap" or preset["evidence_status"] != "unavailable" or preset["gap_label"] != GAP_LABEL:
                raise PopulationError("Missing numerical evidence must remain an explicitly labeled gap.")
            _text(preset["gap_reason"], "gap_reason")
            if preset["reported_uncertainty"] is not None:
                raise PopulationError("An evidence gap cannot contain numerical uncertainty.")
            continue
        if not sids or preset["evidence_status"] != "source_qualified" or preset["gap_label"] is not None or preset["gap_reason"] is not None:
            raise PopulationError("Numerical presets require qualified source evidence, not gap metadata.")
        stat, ph = preset["statistic"], preset["ph"]
        if stat not in displays or preset["display_kind"] != displays[stat]:
            raise PopulationError("Display kind must preserve the reported statistical meaning.")
        if not isinstance(ph, dict) or set(ph) != {"value", "lower", "upper", "confidence_level"}:
            raise PopulationError("pH statistics must use the exact v1 numerical schema.")
        for name in ("value", "lower", "upper"):
            if ph[name] is not None and not -100 <= _number(ph[name], "pH." + name) <= 100:
                raise PopulationError("pH statistic is outside the bounded numerical range.")
        if stat in ("study_mean", "reported_value", "single_condition"):
            if ph["value"] is None or any(ph[name] is not None for name in ("lower", "upper", "confidence_level")):
                raise PopulationError("A point estimate cannot be turned into a manufactured interval.")
        else:
            if ph["lower"] is None or ph["upper"] is None or ph["lower"] >= ph["upper"]:
                raise PopulationError("Interval boundaries must be finite and strictly ordered.")
            if stat.startswith("mean_") and (ph["value"] is None or not ph["lower"] <= ph["value"] <= ph["upper"]):
                raise PopulationError("A mean interval must retain its point estimate inside the interval.")
            if stat in ("reference_interval", "observed_range") and ph["value"] is not None:
                raise PopulationError("Do not invent a mean for a range.")
            if stat == "mean_confidence_interval":
                if ph["confidence_level"] is None or not 0 < _number(ph["confidence_level"], "confidence_level") < 1:
                    raise PopulationError("Confidence intervals require their stated confidence level.")
            elif ph["confidence_level"] is not None:
                raise PopulationError("A non-confidence interval cannot acquire a confidence level.")
        uncertainty = preset["reported_uncertainty"]
        if uncertainty is not None:
            if not isinstance(uncertainty, dict) or set(uncertainty) != {"value", "kind", "plotted"}:
                raise PopulationError("reported_uncertainty must preserve its value, kind, and plotting status.")
            if _number(uncertainty["value"], "reported_uncertainty.value") < 0:
                raise PopulationError("Uncertainty cannot be negative.")
            if uncertainty["kind"] not in ("sd", "sem", "unspecified_in_abstract") or type(uncertainty["plotted"]) is not bool:
                raise PopulationError("Unknown uncertainty semantics.")
            if uncertainty["kind"] == "unspecified_in_abstract" and uncertainty["plotted"]:
                raise PopulationError("Unspecified uncertainty cannot be plotted as a band.")
            if uncertainty["plotted"] and stat not in ("mean_sd", "mean_sem"):
                raise PopulationError("Plotted SD/SEM requires its matching statistical display type.")
        if stat in ("mean_sd", "mean_sem"):
            if uncertainty is None or uncertainty["kind"] != stat[5:] or not uncertainty["plotted"]:
                raise PopulationError("An SD/SEM interval requires that explicitly identified uncertainty statistic.")
            if not (math.isclose(ph["lower"], ph["value"] - uncertainty["value"], abs_tol=1e-12, rel_tol=0)
                    and math.isclose(ph["upper"], ph["value"] + uncertainty["value"], abs_tol=1e-12, rel_tol=0)):
                raise PopulationError("SD/SEM interval boundaries must agree with the stated mean and uncertainty.")
    return data


def load_physiology_presets():
    """Read only the bundled dataset and return a fresh validated JSON object."""
    path = Path(__file__).resolve().parent / "data" / "physiology.json"
    with path.open("r", encoding="utf-8") as stream:
        data = json.load(stream, parse_constant=lambda value: (_ for _ in ()).throw(PopulationError(f"Non-finite JSON value: {value}")))
    return validate_physiology_dataset(data)
