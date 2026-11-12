import {readPopulationJson, sameModelNumbers, compactModelNumber} from './population-model-json.mjs';
/** Pure presentation contracts. No DOM, network, prediction, persistence or normalization. */
export const PH_GRID = Object.freeze({first: 0, last: 140, count: 141, initial: 70});
export const STATE_CLASSES = Object.freeze(['protonation', 'tautomer', 'stereoisomer', 'conformer']);
const acceptedResults = new WeakSet(), acceptedCapabilities = new WeakSet(), acceptedReferences = new WeakSet();
const GAP_LABEL = 'No verified numerical interval available';
const exactModels = new WeakMap();
const MAX_STATES = 4096, EPSILON = 1e-10;
const fail = message => { throw new TypeError(message); };
function requireThat(condition, message) { if (!condition) fail(message); }
function finite(value, name) { requireThat(typeof value === 'number' && Number.isFinite(value), `${name} must be finite.`); return value; }
function text(value, name, max = 8192) {
  requireThat(typeof value === 'string' && value.trim().length > 0 && value.length <= max, `${name} must be bounded nonempty text.`);
  return value;
}
function object(value, name) { requireThat(value !== null && typeof value === 'object' && !Array.isArray(value), `${name} must be an object.`); return value; }
function exactKeys(value, keys, name) {
  object(value, name);
  requireThat(Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key)), `${name} has an unsupported schema.`);
}
function list(value, name, max) { requireThat(Array.isArray(value) && value.length <= max, `${name} must be a bounded array.`); return value; }
function texts(value, name, max = 100) { return list(value, name, max).map(item => text(item, name)); }
function integer(value, name, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
  requireThat(Number.isSafeInteger(value) && value >= minimum && value <= maximum, `${name} must be an integer in range.`); return value;
}
function unique(values, name) { requireThat(new Set(values).size === values.length, `${name} must be unique.`); return values; }
function near(left, right) { return Math.abs(left - right) <= EPSILON; }
function sum(values) { // Compensated summation, never a rescaling operation.
  let total = 0, correction = 0;
  for (const value of values) { const next = value - correction, temporary = total + next; correction = temporary - total - next; total = temporary; }
  return total;
}
function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
  return JSON.stringify(value);
}
function immutableJson(input) {
  let nodes = 0, characters = 0;
  const ancestors = new Set();
  function copy(value, depth) {
    requireThat(++nodes <= 5000000 && depth <= 20, 'Input exceeds the presentation size/depth limit.');
    if (value === null || typeof value === 'boolean') return value;
    if (typeof value === 'number') return finite(value, 'JSON number');
    if (typeof value === 'string') {
      characters += value.length;
      requireThat(value.length <= 8192 && characters <= 16777216, 'Input exceeds the presentation text limit.');
      return value;
    }
    requireThat(value && typeof value === 'object' && !ancestors.has(value), 'Input must be acyclic plain JSON.');
    const array = Array.isArray(value);
    requireThat(array || Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null, 'Input must be plain JSON.');
    ancestors.add(value);
    const output = array ? [] : {};
    const descriptors = Object.getOwnPropertyDescriptors(value);
    for (const key of Reflect.ownKeys(descriptors)) {
      if (array && key === 'length') continue;
      requireThat(typeof key === 'string' && descriptors[key].enumerable && Object.hasOwn(descriptors[key], 'value'), 'JSON accessors and hidden properties are unsupported.');
      requireThat(!array || /^(0|[1-9]\d*)$/.test(key) && Number(key) < value.length, 'Array properties are unsupported.');
      Object.defineProperty(output, key, {value: copy(descriptors[key].value, depth + 1), enumerable: true});
    }
    if (array) requireThat(Object.keys(descriptors).length === value.length + 1, 'Sparse arrays are unsupported.');
    ancestors.delete(value);
    return Object.freeze(output);
  }
  return copy(input, 0);
}

export function indexToPh(index) { return integer(index, 'pH index', 0, 140) / 10; }

/** Decimal arithmetic on the entered text: do not round an off-grid decimal to a tenth. */
export function parseExactPhIndex(input) {
  requireThat(typeof input === 'string' || typeof input === 'number', 'Enter an exact decimal pH sample.');
  if (typeof input === 'number') finite(input, 'pH');
  const value = String(input).trim();
  requireThat(value.length > 0 && value.length <= 128, 'pH entry length is invalid.');
  const match = /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:e([+-]?\d+))?$/i.exec(value);
  requireThat(match, 'Enter an exact decimal pH sample.');
  const fraction = match[3] ?? match[4] ?? '';
  const coefficient = BigInt((match[2] ?? '0') + fraction);
  const exponent = Number(match[5] ?? 0);
  requireThat(Number.isSafeInteger(exponent) && Math.abs(exponent) <= 256, 'pH exponent exceeds the entry limit.');
  const shift = exponent - fraction.length + 1;
  let index;
  if (shift >= 0) index = coefficient * 10n ** BigInt(shift);
  else {
    const divisor = 10n ** BigInt(-shift);
    requireThat(coefficient % divisor === 0n, 'pH must be an exact 0.1 sample; it is not rounded.');
    index = coefficient / divisor;
  }
  if (match[1] === '-') index = -index;
  requireThat(index >= 0n && index <= 140n, 'pH must be between 0 and 14.');
  return Number(index);
}

/** Pointer-scale coordinates only: nearest sample, ties toward the higher index. */
export function snapPhIndex(ph) { return Math.max(0, Math.min(140, Math.round(finite(ph, 'Pointer pH') * 10))); }

export function createPhSelection(initial = 70) {
  let snapshot = Object.freeze({index: initial, ph: indexToPh(initial)});
  const subscribers = new Set();
  function selectIndex(index) {
    const ph = indexToPh(index);
    if (snapshot.index === index) return snapshot;
    snapshot = Object.freeze({index, ph});
    for (const notify of [...subscribers]) notify(snapshot);
    return snapshot;
  }
  return Object.freeze({getSnapshot: () => snapshot, selectIndex,
    enterPh: value => selectIndex(parseExactPhIndex(value)),
    pointerPh: value => selectIndex(snapPhIndex(value)),
    key(key) {
      const index = key === 'Home' ? 0 : key === 'End' ? 140 : key === 'ArrowLeft' ? Math.max(0, snapshot.index - 1)
        : key === 'ArrowRight' ? Math.min(140, snapshot.index + 1) : null;
      return index === null ? snapshot : selectIndex(index);
    },
    subscribe(listener) { requireThat(typeof listener === 'function', 'A selection listener is required.'); subscribers.add(listener); return () => subscribers.delete(listener); },
  });
}

function referenceOverlay(context) {
  const ph = context.ph;
  return {kind: context.display_kind, statistic: context.statistic, label: context.label,
    value: ph.value ?? null, lower: ph.lower ?? null, upper: ph.upper ?? null,
    confidenceLevel: ph.confidence_level ?? null, evidenceStatus: context.evidence_status,
    sourceIds: context.source_ids ?? [], joinTrajectory: false};
}

function validateContext(context, sources) {
  object(context, 'Reference context');
  for (const field of ['id', 'group', 'label', 'compartment', 'anatomical_site', 'species', 'population', 'preparation',
    'condition', 'temperature_note', 'measurement_method', 'specimen_handling', 'statistic', 'display_kind', 'evidence_status']) text(context[field], field);
  requireThat(context.join_trajectory === false, 'Reference contexts must not be joined as a trajectory.');
  const ids = unique(texts(context.source_ids, 'source_ids', 20), 'source_ids');
  requireThat(ids.every(id => sources.has(id)), 'Reference source is missing.');
  requireThat(texts(context.evidence_limitations, 'evidence_limitations', 50).length > 0, 'Reference limitations are required.');
  for (const key of ['temperature_c', 'reported_uncertainty', 'sample_size', 'nephron_class', 'ph', 'gap_label', 'gap_reason'])
    requireThat(Object.hasOwn(context, key), `Reference must preserve ${key}, including null.`);
  if (context.temperature_c !== null) finite(context.temperature_c, 'Reference temperature');
  if (context.ph === null) {
    requireThat(context.display_kind === 'gap' && context.statistic === 'unavailable' && context.evidence_status === 'unavailable'
      && context.gap_label === GAP_LABEL && context.reported_uncertainty === null, 'Missing evidence must remain an explicit gap.');
    text(context.gap_reason, 'gap_reason'); return;
  }
  requireThat(ids.length > 0 && context.evidence_status === 'source_qualified' && context.gap_label === null && context.gap_reason === null, 'Numerical reference provenance is invalid.');
  const ph = context.ph;
  exactKeys(ph, ['value', 'lower', 'upper', 'confidence_level'], 'Reference pH');
  for (const key of ['value', 'lower', 'upper']) if (ph[key] !== null) requireThat(Math.abs(finite(ph[key], key)) <= 100, 'Reference pH exceeds the source schema.');
  const displays = {reference_interval: 'reference_band', observed_range: 'observed_range', study_mean: 'study_marker', reported_value: 'study_marker',
    single_condition: 'study_marker', mean_confidence_interval: 'confidence_interval', mean_sd: 'sd_interval', mean_sem: 'sem_interval'};
  requireThat(Object.hasOwn(displays, context.statistic) && displays[context.statistic] === context.display_kind, 'Reference statistical meaning is inconsistent.');
  if (context.display_kind === 'study_marker') {
    requireThat(ph.value !== null && ph.lower === null && ph.upper === null && ph.confidence_level === null, 'A source point cannot acquire an interval.');
  } else {
    requireThat(ph.lower !== null && ph.upper !== null && ph.lower < ph.upper, 'Source interval bounds are invalid.');
    if (context.statistic.startsWith('mean_')) requireThat(ph.value !== null && ph.value >= ph.lower && ph.value <= ph.upper, 'Mean interval needs its reported point.');
    else requireThat(ph.value === null, 'A range cannot acquire an invented mean.');
    if (context.statistic === 'mean_confidence_interval') requireThat(finite(ph.confidence_level, 'Confidence level') > 0 && ph.confidence_level < 1, 'Confidence level is invalid.');
    else requireThat(ph.confidence_level === null, 'Non-confidence intervals cannot acquire a confidence level.');
  }
  const uncertainty = context.reported_uncertainty;
  if (uncertainty !== null) {
    exactKeys(uncertainty, ['value', 'kind', 'plotted'], 'Reported uncertainty');
    requireThat(finite(uncertainty.value, 'Uncertainty') >= 0 && ['sd', 'sem', 'unspecified_in_abstract'].includes(uncertainty.kind)
      && typeof uncertainty.plotted === 'boolean', 'Reported uncertainty is invalid.');
    requireThat(!uncertainty.plotted || ['mean_sd', 'mean_sem'].includes(context.statistic), 'Unspecified uncertainty cannot become a band.');
  }
  if (['mean_sd', 'mean_sem'].includes(context.statistic)) requireThat(uncertainty?.plotted === true
    && uncertainty.kind === context.statistic.slice(5) && near(ph.lower, ph.value - uncertainty.value)
    && near(ph.upper, ph.value + uncertainty.value), 'SD/SEM bounds must match the reported statistic.');
}

export function referenceView(input, contextId = null, proposedComparison = null) {
  const dataset = immutableJson(input);
  requireThat(dataset.schema_version === 1 && dataset.default_enabled === false && dataset.gap_label === GAP_LABEL, 'Unsupported reference dataset contract.');
  text(dataset.dataset_version, 'dataset_version'); text(dataset.purpose, 'purpose'); text(dataset.reviewed_date, 'reviewed_date');
  texts(dataset.interpretation_rules, 'interpretation_rules');
  const sources = new Map();
  for (const source of list(dataset.sources, 'sources', 100)) {
    for (const key of ['id', 'title', 'url', 'source_type', 'inspected_scope', 'accessed_date']) text(source[key], `source.${key}`);
    requireThat(!sources.has(source.id) && source.url.startsWith('https://'), 'Reference source identity is invalid.');
    sources.set(source.id, source);
  }
  const contexts = list(dataset.presets, 'presets', 100);
  unique(contexts.map(context => context.id), 'Reference IDs');
  for (const context of contexts) validateContext(context, sources);
  const context = contextId === null || contextId === '' ? null : contexts.find(item => item.id === contextId);
  requireThat(context !== undefined, 'Select an available reference context.');
  let comparison = null;
  const overlays = context?.ph ? [referenceOverlay(context)] : [];
  if (proposedComparison !== null) {
    comparison = immutableJson(proposedComparison);
    requireThat(context && comparison.context_id === context.id && comparison.source_kind === 'user_entered'
      && comparison.evidence_status === 'unverified' && comparison.statistic === 'user_defined_comparison', 'Custom comparison must retain its unverified context binding.');
    text(comparison.label, 'Comparison label', 80); text(comparison.context_label, 'Comparison context label');
    requireThat(comparison.context_label === context.label && !/[\x00-\x1f\x7f]/.test(comparison.label), 'Custom comparison must preserve its actual context label and single-line label.');
    const ph = object(comparison.ph, 'Comparison pH');
    if (comparison.display_kind === 'custom_marker') {
      exactKeys(ph, ['value', 'lower', 'upper'], 'Comparison marker');
      requireThat(finite(ph.value, 'Comparison pH') >= 0 && ph.value <= 14 && ph.lower === null && ph.upper === null, 'Custom marker is invalid.');
    } else {
      requireThat(comparison.display_kind === 'custom_band', 'Custom comparison kind is invalid.');
      exactKeys(ph, ['lower', 'upper'], 'Comparison band');
      requireThat(finite(ph.lower, 'Comparison lower') >= 0 && finite(ph.upper, 'Comparison upper') <= 14 && ph.lower < ph.upper, 'Custom band is invalid.');
    }
    overlays.push(referenceOverlay(comparison));
  }
  const result = immutableJson({schemaVersion: 1, datasetVersion: dataset.dataset_version, purpose: dataset.purpose,
    reviewedDate: dataset.reviewed_date, interpretationRules: dataset.interpretation_rules,
    context, comparison, sources: context ? context.source_ids.map(id => sources.get(id)) : [], overlays,
    gap: context?.ph === null ? {label: context.gap_label, reason: context.gap_reason} : null});
  acceptedReferences.add(result); return result;
}

/** Exact current installed contract; enabling a new predictor requires a reviewed adapter. */
export function validateCapabilities(input) {
  const value = immutableJson(input);
  exactKeys(value, ['module', 'molecular_predictor_available', 'mathematical_evaluator_available',
    'external_providers_enabled', 'state_classes', 'scope'], 'Capabilities');
  requireThat(value.module === 'maraPssiwS' && value.molecular_predictor_available === false
    && typeof value.mathematical_evaluator_available === 'boolean' && value.external_providers_enabled === false,
    'Unsupported capabilities: a predictor/provider needs a reviewed adapter.');
  text(value.scope, 'Capability scope'); exactKeys(value.state_classes, STATE_CLASSES, 'State classes');
  for (const name of STATE_CLASSES) {
    exactKeys(value.state_classes[name], ['status', 'reason'], name);
    requireThat(value.state_classes[name].status === 'unavailable', 'Current state classes must remain unavailable.');
    text(value.state_classes[name].reason, name + ' reason');
  }
  acceptedCapabilities.add(value); return value;
}

export function referencePresentation(capabilities, reference) {
  requireThat(acceptedCapabilities.has(capabilities), 'Use validated capabilities.');
  requireThat(acceptedReferences.has(reference), 'Use a validated reference view.');
  // The reference is already deeply immutable and validated. Keep its identity
  // so chart snapshots retain the same validation boundary as export callers.
  return Object.freeze({label: 'pH reference context', populationAxis: false, datasets: Object.freeze([]),
    unavailableClasses: immutableJson(STATE_CLASSES.map(id => ({id, reason: capabilities.state_classes[id].reason}))), reference});
}

export function stateStyle(id) {
  text(id, 'State ID'); let hash = 2166136261;
  for (let i = 0; i < id.length; i++) hash = Math.imul(hash ^ id.charCodeAt(i), 16777619) >>> 0;
  const patterns = [[], [6, 3], [2, 3], [8, 3, 2, 3]];
  return immutableJson({color: `hsl(${hash % 360} 58% 43%)`, dash: patterns[(hash >>> 9) % patterns.length], label: id});
}

// Result-envelope validation and selection are kept below the reference-only capability path.
// They expose supplied-model arithmetic, never an installed prediction or chemical state graph.

const conditionKeys = ['temperature_kelvin', 'solvent', 'ionic_strength_mol_l', 'activity_convention', 'reference_ph',
  'ph_min', 'ph_max', 'energy_unit', 'energy_basis', 'standard_state', 'solvation_model', 'thermal_entropy_treatment',
  'degeneracy_convention', 'sampling_method', 'truncation_treatment'];
const provenanceKeys = ['input_identity', 'engine', 'engine_version', 'model_name', 'model_version', 'source',
  'created_utc', 'applicability', 'validation', 'source_kind', 'transformations'];
const stateKeys = ['state_id', 'protonation_id', 'tautomer_id', 'stereoisomer_id', 'conformer_id', 'structure_id',
  'atom_ids', 'formal_charge', 'relative_protons', 'free_energy_at_reference', 'degeneracy'];
const resultKeys = ['ph', 'state_ids', 'fractions', 'log_fractions', 'model_signature', 'input_identity',
  'normalization_scope', 'omitted_fraction', 'omitted_mass_source', 'warnings', 'conditions', 'provenance',
  'percentages', 'log_fraction_base'];

function validateModel(model) {
  exactKeys(model, ['states', 'conditions', 'provenance', 'reference_state_id', 'supported_classes',
    'equilibrium_scope', 'equilibrium_justification', 'ensemble_complete'], 'Supplied model');
  const conditions = model.conditions, provenance = model.provenance;
  exactKeys(conditions, conditionKeys, 'Model conditions');
  requireThat(finite(conditions.temperature_kelvin, 'Temperature') > 0 && conditions.solvent === 'water', 'Actual aqueous model conditions are required.');
  if (conditions.ionic_strength_mol_l !== null) requireThat(finite(conditions.ionic_strength_mol_l, 'Ionic strength') >= 0, 'Ionic strength cannot be negative.');
  for (const key of ['ph_min', 'ph_max', 'reference_ph']) requireThat(Math.abs(finite(conditions[key], key)) <= 100, 'Declared model pH is out of bounds.');
  requireThat(conditions.ph_min <= conditions.reference_ph && conditions.reference_ph <= conditions.ph_max, 'Model pH domain is invalid.');
  for (const key of ['activity_convention', 'standard_state', 'solvation_model', 'thermal_entropy_treatment', 'sampling_method', 'truncation_treatment']) text(conditions[key], key);
  requireThat(['J/mol', 'kJ/mol', 'kcal/mol'].includes(conditions.energy_unit)
    && conditions.energy_basis === 'aqueous_transformed_gibbs_at_reference_ph'
    && ['explicit', 'included_in_free_energy'].includes(conditions.degeneracy_convention), 'Unsupported declared energy convention.');
  exactKeys(provenance, provenanceKeys, 'Model provenance');
  for (const key of provenanceKeys.filter(key => key !== 'transformations')) text(provenance[key], key);
  requireThat(['external_model', 'synthetic_analytical'].includes(provenance.source_kind), 'Source kind must remain explicit.');
  requireThat(/(?:Z|\+00:00)$/.test(provenance.created_utc) && Number.isFinite(Date.parse(provenance.created_utc)), 'Model provenance must retain its UTC timestamp.');
  texts(provenance.transformations, 'transformations');
  requireThat(typeof model.ensemble_complete === 'boolean', 'Ensemble completeness must be explicit.');
  requireThat(['conditional_configuration', 'joint_equilibrium'].includes(model.equilibrium_scope), 'Equilibrium scope is required.');
  text(model.equilibrium_justification, 'equilibrium_justification');
  const classes = unique(texts(model.supported_classes, 'supported_classes', 4), 'supported_classes');
  requireThat(classes.length > 0 && classes.every(name => STATE_CLASSES.includes(name)), 'Unsupported state classes.');
  const states = list(model.states, 'states', MAX_STATES);
  requireThat(states.length > 0, 'A supplied model needs states.');
  for (const state of states) {
    exactKeys(state, stateKeys, 'Joint state');
    for (const key of stateKeys.filter(key => key.endsWith('_id'))) text(state[key], key);
    requireThat(unique(texts(state.atom_ids, 'atom_ids', MAX_STATES), 'atom_ids').length > 0, 'Atomic core identity is required.');
    integer(state.formal_charge, 'formal_charge', -1000000000, 1000000000);
    integer(state.relative_protons, 'relative_protons', -1000000000, 1000000000);
    integer(state.degeneracy, 'degeneracy', 1, 1000000000);
    finite(state.free_energy_at_reference, 'Reference energy');
    requireThat(conditions.degeneracy_convention !== 'included_in_free_energy' || state.degeneracy === 1, 'Degeneracy cannot be counted twice.');
  }
  const ids = unique(states.map(state => state.state_id), 'state_ids');
  requireThat(ids.includes(model.reference_state_id), 'Reference state identity is missing.');
  unique(states.map(state => canonical(STATE_CLASSES.map(name => state[name + '_id']))), 'Joint basin identities');
  const protonCounts = new Map(), structures = new Map();
  for (const state of states) {
    requireThat(!protonCounts.has(state.protonation_id) || protonCounts.get(state.protonation_id) === state.relative_protons, 'A protonation ID cannot alias distinct proton counts.');
    protonCounts.set(state.protonation_id, state.relative_protons);
    const identity = canonical([state.protonation_id, state.tautomer_id, state.stereoisomer_id, state.formal_charge, state.relative_protons]);
    requireThat(!structures.has(state.structure_id) || structures.get(state.structure_id) === identity, 'A structure ID cannot alias different chemical state labels.');
    structures.set(state.structure_id, identity);
  }
  const core = canonical([...states[0].atom_ids].sort()), chargeOffset = states[0].formal_charge - states[0].relative_protons;
  requireThat(states.every(state => canonical([...state.atom_ids].sort()) === core
    && state.formal_charge - state.relative_protons === chargeOffset), 'Joint states must preserve atomic core and proton/charge relationships.');
  for (const name of STATE_CLASSES) requireThat(new Set(states.map(state => state[name + '_id'])).size === 1 || classes.includes(name), 'Varying state dimensions require a supported-class declaration.');
  if (model.equilibrium_scope === 'conditional_configuration') requireThat(!classes.includes('stereoisomer')
    && new Set(states.map(state => state.stereoisomer_id)).size === 1, 'Conditional configuration cannot establish stereoisomer populations.');
  return ids;
}

/** Checks a complete supplied model without interpreting its scientific claims. */
export function validateSuppliedPopulationModel(input) {
  const value = immutableJson(input);
  validateModel(value);
  return value;
}

/** Validates adapter binding/shape and arithmetic consistency, not chemical identity or model validity. */
export function validatePopulationEnvelope(input, expectedBinding, exactModel=null) {
  exactKeys(expectedBinding, ['documentRevision', 'graphHash', 'modelSignature'], 'Expected binding');
  integer(expectedBinding.documentRevision, 'Expected drawing revision');
  for (const field of ['graphHash', 'modelSignature']) requireThat(/^[0-9a-f]{64}$/.test(expectedBinding[field]), `Expected ${field} must be a SHA-256 identifier.`);
  const value = immutableJson(input);
  exactKeys(value, ['schemaVersion', 'documentRevision', 'graphHash', 'modelSignature', 'model', 'samples'], 'Population envelope');
  requireThat(value.schemaVersion === 1, 'Unsupported population envelope version.');
  for (const field of ['documentRevision', 'graphHash', 'modelSignature']) requireThat(value[field] === expectedBinding[field], `Population ${field} does not match the current binding.`);
  const ids = validateModel(value.model), conditions = value.model.conditions;
  const encodedConditions = canonical(conditions), encodedProvenance = canonical(value.model.provenance);
  requireThat(list(value.samples, 'samples', PH_GRID.count).length === PH_GRID.count, 'All 141 sample positions must be represented, including gaps.');
  value.samples.forEach((row, index) => {
    exactKeys(row, ['index', 'ph', 'status', 'result', 'reason'], 'Grid sample');
    requireThat(row.index === index && row.ph === indexToPh(index), 'Grid samples must use their exact ordered integer coordinates.');
    const inside = row.ph >= conditions.ph_min && row.ph <= conditions.ph_max;
    if (row.status !== 'computed') {
      requireThat(row.result === null && (row.status === 'outside_model_domain' && !inside
        || row.status === 'unavailable' && inside), 'Unsupported gap status or declared-domain mismatch.');
      text(row.reason, 'Gap reason'); return;
    }
    requireThat(inside && row.reason === null, 'Computed sample lies outside the model domain or has a gap reason.');
    const result = row.result;
    exactKeys(result, resultKeys, 'Population result');
    requireThat(result.ph === row.ph && result.model_signature === value.modelSignature
      && result.input_identity === value.model.provenance.input_identity && canonical(result.state_ids) === canonical(ids), 'Result model/state/input identity is inconsistent.');
    requireThat(canonical(result.conditions) === encodedConditions && canonical(result.provenance) === encodedProvenance, 'Actual result conditions/provenance differ from the supplied model.');
    requireThat(result.log_fraction_base === 'e', 'Unknown logarithm convention.');
    for (const field of ['fractions', 'percentages', 'log_fractions']) requireThat(list(result[field], field, MAX_STATES).length === ids.length, 'State-vector length mismatch.');
    result.fractions.forEach((fraction, position) => {
      requireThat(finite(fraction, 'Fraction') >= 0 && fraction <= 1, 'Fractions must be finite, nonnegative and no greater than one.');
      requireThat(near(finite(result.percentages[position], 'Percentage'), 100 * fraction), 'Percentage does not match its unchanged fraction.');
      const logarithm = finite(result.log_fractions[position], 'Log fraction');
      requireThat(logarithm <= EPSILON && near(Math.exp(logarithm), fraction), 'Log fractions disagree with fractions.');
    });
    requireThat(texts(result.warnings, 'warnings').length > 0, 'Supplied-model limitations must remain explicit.');
    if (value.model.ensemble_complete) {
      requireThat(result.normalization_scope === 'declared_complete_supplied_model' && result.omitted_fraction === 0, 'Complete ensemble mass declaration is inconsistent.');
      if (result.omitted_mass_source !== null) text(result.omitted_mass_source, 'Omitted mass source');
    } else if (result.omitted_fraction === null) {
      requireThat(result.normalization_scope === 'conditional_on_represented_states' && result.omitted_mass_source === null, 'Unknown omitted mass must remain conditional.');
    } else {
      requireThat(result.normalization_scope === 'supplied_total_mass' && finite(result.omitted_fraction, 'Omitted fraction') >= 0
        && result.omitted_fraction < 1, 'Supplied omitted mass is invalid.');
      text(result.omitted_mass_source, 'Omitted mass source');
    }
    requireThat(near(sum(result.fractions), 1 - (result.omitted_fraction ?? 0)), 'Supplied fractions do not conserve their declared mass; no renormalization is performed.');
  });
  if (exactModel !== null) {
    requireThat(typeof exactModel.modelInputHash === 'string' && /^[a-f0-9]{64}$/.test(exactModel.modelInputHash), 'The exact supplied model digest is required.');
    requireThat(typeof exactModel.modelText === 'string' && new TextEncoder().encode(exactModel.modelText).byteLength <= 2*1024*1024, 'The supplied model exceeds 2 MiB.');
    const original = readPopulationJson(exactModel.modelText), returned = readPopulationJson(exactModel.responseModelText);
    validateModel(original.value);
    requireThat(canonical(original.value) === canonical(value.model)
      && canonical(returned.value) === canonical(value.model)
      && sameModelNumbers(original.model, returned.model), 'The result changed the exact supplied model.');
    const conditions = {numbers:new Map([...original.model.numbers]
      .filter(([path])=>path.startsWith('/conditions/')).map(([path,raw])=>[path.slice('/conditions'.length),raw]))};
    for (const row of value.samples) if (row.status === 'computed') {
      const repeated = exactModel.responseConditions?.get(row.index);
      requireThat(sameModelNumbers(conditions, repeated), 'The result changed the exact supplied model conditions.');
    }
    exactModels.set(value, {numbers:new Map([...original.model.numbers].map(([path,raw])=>[path,compactModelNumber(raw)])),
      modelInputHash:exactModel.modelInputHash});
  }
  acceptedResults.add(value); return value;
}

/** Condition text from the exact accepted file binding, never caller labels.
 * Generic adapters retain their existing Number/String presentation.
 */
export function populationConditionText(envelope, key) {
  requireThat(acceptedResults.has(envelope), 'Use a validated population envelope.');
  requireThat(typeof key==='string' && conditionKeys.includes(key), 'Use a supported model condition field.');
  const value=envelope.model.conditions[key];
  return typeof value==='number' ? exactModels.get(envelope)?.numbers.get('/conditions/'+key) ?? String(value) : value;
}

function sampleAndVisibility(envelope, index, visibleStateIds) {
  requireThat(acceptedResults.has(envelope), 'Use a validated population envelope.'); indexToPh(index);
  const ids = envelope.model.states.map(state => state.state_id);
  const visible = visibleStateIds === null ? ids : unique(texts(visibleStateIds, 'Visible state IDs', MAX_STATES), 'Visible state IDs');
  requireThat(visible.every(id => ids.includes(id)), 'Visible IDs must belong to the full result.');
  return {sample: envelope.samples[index], visible: new Set(visible)};
}

export function populationTable(envelope, index, {visibleStateIds = null, aggregateBy = null} = {}) {
  const {sample, visible} = sampleAndVisibility(envelope, index, visibleStateIds);
  if (aggregateBy !== null) {
    requireThat(['formal_charge', 'relative_protons', ...STATE_CLASSES.map(name => name + '_id')].includes(aggregateBy), 'Unsupported aggregation dimension.');
    if (aggregateBy.endsWith('_id')) requireThat(envelope.model.supported_classes.includes(aggregateBy.slice(0, -3)), 'This state-class aggregation is unavailable.');
  }
  if (sample.result === null) return immutableJson({index, ph: sample.ph, status: sample.status, reason: sample.reason,
    rows: [], groups: [], representedMass: null, plottedMass: null, hiddenRepresentedMass: null,
    omittedMass: null, fullEnsembleMass: null, normalizationScope: null, warnings: []});
  const result = sample.result;
  const rows = envelope.model.states.map((state, position) => ({stateId: state.state_id,
    fraction: result.fractions[position], percentage: result.percentages[position], visible: visible.has(state.state_id), style: stateStyle(state.state_id)}));
  const groups = new Map();
  if (aggregateBy !== null) envelope.model.states.forEach((state, position) => {
    const key = String(state[aggregateBy]); if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(result.fractions[position]);
  });
  return immutableJson({index, ph: sample.ph, status: sample.status, reason: null, rows,
    groups: [...groups].map(([id, fractions]) => ({id, fraction: sum(fractions), percentage: 100 * sum(fractions)})),
    representedMass: sum(result.fractions), plottedMass: sum(rows.filter(row => row.visible).map(row => row.fraction)),
    hiddenRepresentedMass: sum(rows.filter(row => !row.visible).map(row => row.fraction)),
    omittedMass: result.omitted_fraction, omittedMassSource: result.omitted_mass_source,
    fullEnsembleMass: result.omitted_fraction === null ? null : sum(result.fractions) + result.omitted_fraction,
    normalizationScope: result.normalization_scope, ensembleComplete: envelope.model.ensemble_complete,
    equilibriumScope: envelope.model.equilibrium_scope, warnings: result.warnings,
    interpretation: result.omitted_fraction === null ? 'Conditional on represented states; full-ensemble and omitted mass are unknown.'
      : 'Supplied-model fractions; hidden curves remain part of the represented ensemble. No visibility renormalization.'});
}

export function populationSeries(envelope, {visibleStateIds = null} = {}) {
  const {visible} = sampleAndVisibility(envelope, 0, visibleStateIds);
  return immutableJson(envelope.model.states.map((state, position) => ({stateId: state.state_id,
    visible: visible.has(state.state_id), style: stateStyle(state.state_id), spanGaps: false,
    points: envelope.samples.map(row => ({index: row.index, x: row.ph, y: row.result?.fractions[position] ?? null,
      status: row.status, reason: row.reason}))})));
}

function referenceExport(reference, {index = 70} = {}) {
  requireThat(acceptedReferences.has(reference), 'Use a validated reference view.');
  return {schemaVersion: 1, exportKind: 'ph-reference-context', selectedSample: {index, ph: indexToPh(index)},
    reference, populationResults: null, limitation: 'Reference contexts and unverified comparisons only; not molecular population results.'};
}
export function serializeReferenceJson(reference, options) { return JSON.stringify(referenceExport(reference, options), null, 2) + '\n'; }

/** Typed scalar CSV; flagged leading apostrophe protects spreadsheet text without losing recovery information. */
export function referenceCsv(reference, options) {
  const rows = [['path', 'type', 'value', 'spreadsheet_escaped']];
  const escapePointer = value => value.replaceAll('~', '~0').replaceAll('/', '~1');
  function visit(value, path) {
    if (value !== null && typeof value === 'object') {
      if (Object.keys(value).length === 0) rows.push([path, Array.isArray(value) ? 'array' : 'object', Array.isArray(value) ? '[]' : '{}', 'false']);
      else for (const [key, child] of Object.entries(value)) visit(child, path + '/' + escapePointer(key));
      return;
    }
    const type = value === null ? 'null' : typeof value;
    const raw = value === null ? 'null' : String(value);
    const guarded = type === 'string' && (/^[\s\uFEFF]*[=+@-]/.test(raw) || /^[\t\r\n]/.test(raw));
    rows.push([path, type, guarded ? "'" + raw : raw, String(guarded)]);
  }
  visit(referenceExport(reference, options), '');
  return rows.map(row => row.map(value => '"' + value.replaceAll('"', '""') + '"').join(',')).join('\r\n') + '\r\n';
}

export const POPULATION_EXPORT_MAX_BYTES = 32 * 1024 * 1024;
export const POPULATION_EXPORT_LIMITATION = 'Supplied-model calculation; not a validated molecular prediction.';

function populationExport(envelope, {source, index=70, visibleStateIds=null, reference=null}={}) {
  const {visible} = sampleAndVisibility(envelope, index, visibleStateIds);
  const identity = immutableJson(source);
  exactKeys(identity, ['name', 'modelInputHash'], 'Export source');
  text(identity.name, 'Source filename', 256);
  requireThat(typeof identity.modelInputHash === 'string' && /^[a-f0-9]{64}$/.test(identity.modelInputHash), 'The exact supplied model digest is required.');
  const exact=exactModels.get(envelope);
  requireThat(!exact || identity.modelInputHash === exact.modelInputHash, 'The export source differs from the bound supplied model digest.');
  if (reference !== null) requireThat(acceptedReferences.has(reference), 'Use a validated reference view.');
  return {schemaVersion:1, exportKind:'supplied-population-results', limitation:POPULATION_EXPORT_LIMITATION,
    source:identity, selectedSample:{index, ph:indexToPh(index)}, visibleStateIds:[...visible],
    referenceContext:reference, populationResults:envelope};
}

// Only these paths are copies of original model numbers. Computed fractions,
// logarithms, percentages and grid coordinates are never replaced or repaired.
function originalNumber(exact, path) {
  const modelPath='/populationResults/model';
  if (path.startsWith(modelPath+'/')) return exact?.numbers.get(path.slice(modelPath.length));
  const condition=/^\/populationResults\/samples\/(0|[1-9]\d*)\/result\/conditions(\/[^/]+)$/.exec(path);
  return condition ? exact?.numbers.get('/conditions'+condition[2]) : undefined;
}

export function serializePopulationJson(envelope, options) {
  const exact = exactModels.get(envelope);
  function encode(value,path='') {
    if (value === null || typeof value !== 'object')
      return typeof value === 'number' ? originalNumber(exact,path) ?? JSON.stringify(value) : JSON.stringify(value);
    if (Array.isArray(value) && /^\/populationResults\/samples\/(?:0|[1-9]\d*)\/result\/(?:fractions|percentages|log_fractions|state_ids|warnings)$/.test(path))
      return JSON.stringify(value);
    if (Array.isArray(value)) return '['+value.map((item,index)=>encode(item,path+'/'+index)).join(',')+']';
    return '{'+Object.entries(value).map(([key,item])=>JSON.stringify(key)+':'+encode(item,
      path+'/'+key.replaceAll('~','~0').replaceAll('/','~1'))).join(',')+'}';
  }
  const document=populationExport(envelope,options);
  const encoded=(exact ? encode(document) : JSON.stringify(document))+'\n';
  requireThat(new TextEncoder().encode(encoded).byteLength <= POPULATION_EXPORT_MAX_BYTES, 'The complete population JSON exceeds 32 MiB; nothing was truncated.');
  return encoded;
}

/** Lossless typed scalar CSV, matching the existing reference-export convention. */
export function populationCsv(envelope, options) {
  const exact = exactModels.get(envelope);
  const lines = [], encoder = new TextEncoder();
  let bytes = 0;
  function append(row) {
    const line = row.map(value => '"' + value.replaceAll('"', '""') + '"').join(',') + '\r\n';
    bytes += encoder.encode(line).byteLength;
    requireThat(bytes <= POPULATION_EXPORT_MAX_BYTES, 'The complete population CSV exceeds 32 MiB. Use JSON for a more compact export; nothing was truncated.');
    lines.push(line);
  }
  append(['path', 'type', 'value', 'spreadsheet_escaped']);
  const escapePointer = value => value.replaceAll('~', '~0').replaceAll('/', '~1');
  function visit(value, path) {
    if (value !== null && typeof value === 'object') {
      if (Object.keys(value).length === 0) append([path, Array.isArray(value) ? 'array' : 'object', Array.isArray(value) ? '[]' : '{}', 'false']);
      else for (const [key, child] of Object.entries(value)) visit(child, path + '/' + escapePointer(key));
      return;
    }
    const type = value === null ? 'null' : typeof value;
    const raw = type === 'number' ? originalNumber(exact,path) ?? String(value) : value === null ? 'null' : String(value);
    const guarded = type === 'string' && (/^[\s\uFEFF]*[=+@-]/.test(raw) || /^[\t\r\n]/.test(raw));
    append([path, type, guarded ? "'" + raw : raw, String(guarded)]);
  }
  visit(populationExport(envelope, options), '');
  return lines.join('');
}
