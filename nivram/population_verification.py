"""Independent, stdlib-only verification of the supplied-model worker route.

No evaluator, model dataclasses, export saver, or molecular predictor is imported.
The original JSON is authoritative, including number types and signed float zero.
This route has no caller-supplied omitted masses. Generic envelope adapters may
support more result types; their contract is intentionally not changed here.
"""
from datetime import datetime, timedelta
import math


_FACTOR = {'kJ/mol': 1.0, 'J/mol': .001, 'kcal/mol': 4.184}
_CLASSES = ('protonation', 'tautomer', 'stereoisomer', 'conformer')
_MODEL = set('states conditions provenance reference_state_id supported_classes equilibrium_scope equilibrium_justification ensemble_complete'.split())
_STATE = set('state_id protonation_id tautomer_id stereoisomer_id conformer_id structure_id atom_ids formal_charge relative_protons free_energy_at_reference degeneracy'.split())
_CONDITIONS = set('temperature_kelvin solvent ionic_strength_mol_l activity_convention reference_ph ph_min ph_max energy_unit energy_basis standard_state solvation_model thermal_entropy_treatment degeneracy_convention sampling_method truncation_treatment'.split())
_PROVENANCE = set('input_identity engine engine_version model_name model_version source created_utc applicability validation source_kind transformations'.split())
_RESULT = set('ph state_ids fractions log_fractions model_signature input_identity normalization_scope omitted_fraction omitted_mass_source warnings conditions provenance percentages log_fraction_base'.split())


def _require(condition):
    if not condition:
        raise ValueError('Worker population result does not match the supplied model.')


def _record(value, keys):
    _require(type(value) is dict and value.keys() == keys)


def _text(value):
    _require(type(value) is str and bool(value.strip()) and len(value) <= 8192)


def _number(value):
    _require(type(value) in (int, float) and math.isfinite(value))
    return value


def _integer(value):
    _require(type(value) is int and abs(value) <= 1_000_000_000)
    return value


def _list(value, minimum, maximum):
    _require(type(value) is list and minimum <= len(value) <= maximum)
    return value


def _same(left, right, check):
    """Parsed JSON equality, without bool/int/float or signed-zero aliases."""
    _require(type(left) is type(right))
    if type(left) is dict:
        check()
        _require(left.keys() == right.keys())
        for key in left:
            _same(left[key], right[key], check)
    elif type(left) is list:
        _require(len(left) == len(right))
        for index, (a, b) in enumerate(zip(left, right)):
            if index % 64 == 0:
                check()
            _same(a, b, check)
    else:
        _require(left == right)
        if type(left) is float:
            _require(math.isfinite(left) and (left != 0 or math.copysign(1, left) == math.copysign(1, right)))


def _model(model, check):
    """Validate the complete producer schema without trusting worker success."""
    _record(model, _MODEL)
    c, p = model['conditions'], model['provenance']
    _record(c, _CONDITIONS); _record(p, _PROVENANCE)
    for key in _CONDITIONS - {'temperature_kelvin', 'ionic_strength_mol_l', 'reference_ph', 'ph_min', 'ph_max'}:
        _text(c[key])
    _require(_number(c['temperature_kelvin']) > 0 and .00831446261815324 * c['temperature_kelvin'] > 0)
    _require(c['solvent'] == 'water')
    if c['ionic_strength_mol_l'] is not None:
        _require(_number(c['ionic_strength_mol_l']) >= 0)
    lo, hi, ref = (_number(c[k]) for k in ('ph_min', 'ph_max', 'reference_ph'))
    _require(-100 <= lo <= ref <= hi <= 100)
    _require(c['energy_unit'] in _FACTOR and c['energy_basis'] == 'aqueous_transformed_gibbs_at_reference_ph')
    _require(c['degeneracy_convention'] in ('explicit', 'included_in_free_energy'))
    for key in _PROVENANCE - {'transformations'}:
        _text(p[key])
    _require(p['source_kind'] in ('external_model', 'synthetic_analytical'))
    when = datetime.fromisoformat(p['created_utc'].replace('Z', '+00:00'))
    _require(when.tzinfo is not None and when.utcoffset() == timedelta(0))
    for value in _list(p['transformations'], 0, 100):
        _text(value)
    classes = _list(model['supported_classes'], 1, 4)
    for value in classes:
        _text(value)
    _require(len(set(classes)) == len(classes) and all(k in _CLASSES for k in classes))
    _require(type(model['ensemble_complete']) is bool)
    _text(model['equilibrium_justification']); _text(model['reference_state_id'])
    _require(model['equilibrium_scope'] in ('joint_equilibrium', 'conditional_configuration'))
    states = _list(model['states'], 1, 4096)
    ids, basins, counts, structures = set(), set(), {}, {}
    variants = {k: set() for k in _CLASSES}
    core = offset = None
    for state in states:
        check()
        _record(state, _STATE)
        for key in ('state_id', 'structure_id', *(k + '_id' for k in _CLASSES)):
            _text(state[key])
        atoms = _list(state['atom_ids'], 1, 4096)
        for atom in atoms:
            _text(atom)
        _require(len(set(atoms)) == len(atoms))
        charge, protons, degeneracy = (_integer(state[k]) for k in ('formal_charge', 'relative_protons', 'degeneracy'))
        _require(degeneracy >= 1)
        _number(state['free_energy_at_reference'])
        _number(state['free_energy_at_reference'] * _FACTOR[c['energy_unit']])
        _require(c['degeneracy_convention'] == 'explicit' or degeneracy == 1)
        basin = tuple(state[k + '_id'] for k in _CLASSES)
        _require(state['state_id'] not in ids and basin not in basins)
        ids.add(state['state_id']); basins.add(basin)
        pid, sid = state['protonation_id'], state['structure_id']
        identity = (*basin[:3], charge, protons)
        _require(pid not in counts or counts[pid] == protons)
        _require(sid not in structures or structures[sid] == identity)
        counts[pid], structures[sid] = protons, identity
        if core is None:
            core, offset = set(atoms), charge - protons
        _require(set(atoms) == core and charge - protons == offset)
        for key in _CLASSES:
            variants[key].add(state[key + '_id'])
    _require(model['reference_state_id'] in ids)
    for key in _CLASSES:
        _require(len(variants[key]) == 1 or key in classes)
    if model['equilibrium_scope'] == 'conditional_configuration':
        _require(len(variants['stereoisomer']) == 1 and 'stereoisomer' not in classes)


def verify_population_response(model, envelope, signature, check):
    """Reject inconsistent successful worker output; never rewrite valid bytes.

    check is the supervisor's cooperative cancellation callback. It runs between
    states, rows and bounded array chunks. JSON parsing, canonical serialization,
    hashing, individual scalar operations and final encoding are not interruptible.
    Verification is outside the subprocess timeout, not a hard real-time bound.
    """
    _model(model, check)
    _same(model, envelope['model'], check)
    c, p, states = model['conditions'], model['provenance'], model['states']
    ids = [s['state_id'] for s in states]
    anchor = next(s for s in states if s['state_id'] == model['reference_state_id'])
    warnings = ['Mathematical evaluation of supplied thermodynamics; not independently validated molecular predictions.']
    if p['source_kind'] == 'synthetic_analytical':
        warnings.append('Synthetic analytical fixture only; not an analysis of the drawing.')
    if model['equilibrium_scope'] == 'conditional_configuration':
        warnings.append('Conditional on the supplied configuration; between-configuration fractions are unavailable.')
    complete = model['ensemble_complete']
    scope = 'declared_complete_supplied_model' if complete else 'conditional_on_represented_states'
    if not complete:
        warnings.append('Omitted population is unknown; percentages are conditional on represented states and do not establish full-ensemble fractions.')
    rt, factor = .00831446261815324 * c['temperature_kelvin'], _FACTOR[c['energy_unit']]
    terms = None
    _list(envelope['samples'], 141, 141)
    for index, row in enumerate(envelope['samples']):
        check()
        _record(row, {'index', 'ph', 'status', 'result', 'reason'})
        ph = index / 10.0
        _require(type(row['index']) is int and row['index'] == index)
        _same(ph, row['ph'], check)
        if not c['ph_min'] <= ph <= c['ph_max']:
            _require(row['status'] == 'outside_model_domain' and row['result'] is None and
                     row['reason'] == 'No extrapolation outside the supplied model domain.')
            continue
        _require(row['status'] == 'computed' and row['reason'] is None)
        # The producer never evaluates an all-gap grid. Individual valid
        # energies can have an unrepresentable difference; derive terms only
        # when a computed row actually requires arithmetic.
        if terms is None:
            terms = []
            for state in states:
                check()
                # Preserve adjacent large integer differences before float/unit conversion.
                dg = (state['free_energy_at_reference'] - anchor['free_energy_at_reference']) * factor
                dn = state['relative_protons'] - anchor['relative_protons']
                terms.append((-dg / rt, dn * math.log(10.0),
                              math.log(state['degeneracy']) if c['degeneracy_convention'] == 'explicit' else 0.0))
        result = row['result']
        _record(result, _RESULT)
        for key, expected in (('ph', ph), ('state_ids', ids), ('model_signature', signature),
                              ('input_identity', p['input_identity']), ('normalization_scope', scope),
                              ('omitted_fraction', 0.0 if complete else None), ('omitted_mass_source', None),
                              ('warnings', warnings), ('conditions', c), ('provenance', p), ('log_fraction_base', 'e')):
            _same(expected, result[key], check)
        for key in ('fractions', 'percentages', 'log_fractions'):
            _list(result[key], len(states), len(states))
        weights = []
        for i, (energy, proton, multiplicity) in enumerate(terms):
            if i % 64 == 0:
                check()
            weights.append(_number(energy - proton * (ph - c['reference_ph']) + multiplicity))
        largest = max(weights)
        shifted = []
        scaled = []
        for i, weight in enumerate(weights):
            if i % 64 == 0:
                check()
            delta = _number(weight - largest)
            shifted.append(delta); scaled.append(math.exp(delta))
        normalizer = math.fsum(scaled)
        log_sum = math.log(normalizer)
        for i, (delta, weight) in enumerate(zip(shifted, scaled)):
            if i % 64 == 0:
                check()
            fraction = weight / normalizer
            for key, expected in (('fractions', fraction), ('percentages', 100.0 * fraction),
                                  ('log_fractions', delta - log_sum)):
                actual = result[key][i]
                # Same-runtime producer arithmetic: no absolute tolerance that
                # could turn tiny nonzero probabilities into accepted zeros.
                _require(type(actual) is float and math.isfinite(actual) and actual == expected)
                if expected == 0:
                    _require(math.copysign(1, actual) == math.copysign(1, expected))
