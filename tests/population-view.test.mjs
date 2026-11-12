import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {
  indexToPh, parseExactPhIndex, snapPhIndex, createPhSelection, referenceView,
  validateCapabilities, referencePresentation, validatePopulationEnvelope,
  populationTable, populationSeries, stateStyle,
  serializeReferenceJson, referenceCsv,
} from '../public/modules/population-view.mjs';

const dataset = JSON.parse(await readFile(new URL('../nivram/data/physiology.json', import.meta.url), 'utf8'));
const fixtures = JSON.parse(await readFile(new URL('./fixtures/population-analytical.json', import.meta.url), 'utf8'));
const clone = value => structuredClone(value);
const accept = name => validatePopulationEnvelope(fixtures[name].envelope, fixtures[name].binding);

test('the chart presentation preserves the validated reference identity required by both exporters', () => {
  const reference=referenceView(dataset,'arterial-blood-uvm');
  const presentation=referencePresentation(validateCapabilities(fixtures.capabilities),reference);
  assert.equal(presentation.reference,reference);
  assert.ok(Object.isFrozen(presentation));
  assert.ok(Object.isFrozen(presentation.unavailableClasses));
  const exported=JSON.parse(serializeReferenceJson(presentation.reference,{index:74}));
  assert.equal(exported.reference.context.id,'arterial-blood-uvm');
  assert.equal(exported.populationResults,null);
  assert.match(referenceCsv(presentation.reference,{index:74}),/arterial-blood-uvm/);
  assert.throws(()=>serializeReferenceJson(structuredClone(reference)),/validated reference/);
});

test('all 141 samples use integer coordinates with exact direct entry and pointer agreement', () => {
  for (let i = 0; i <= 140; i++) {
    assert.equal(indexToPh(i), i / 10);
    assert.equal(parseExactPhIndex(String(i / 10)), i);
    assert.equal(parseExactPhIndex(i / 10), i);
    assert.equal(snapPhIndex(i / 10), i);
  }
  for (const value of ['7.35', '7.300000000000000000000001', 'NaN', '', '0x7', true, null, [], 14.1, -0.1]) {
    assert.throws(() => parseExactPhIndex(value));
  }
  assert.equal(parseExactPhIndex('73e-1'), 73);
  assert.equal(parseExactPhIndex('7.3000'), 73);
  assert.equal(snapPhIndex(7.35), 74);
});

test('selection is stable, shared, immutable and invalid proposals preserve the accepted index', () => {
  const selection = createPhSelection();
  const initial = selection.getSnapshot();
  assert.deepEqual(initial, {index: 70, ph: 7});
  let calls = 0;
  const stop = selection.subscribe(() => calls++);
  assert.equal(selection.selectIndex(70), initial);
  assert.equal(calls, 0);
  assert.throws(() => selection.enterPh('7.35'));
  assert.equal(selection.getSnapshot(), initial);
  selection.key('Home'); selection.key('ArrowLeft');
  assert.equal(selection.getSnapshot().index, 0);
  selection.key('End'); selection.key('ArrowRight');
  assert.equal(selection.getSnapshot().index, 140);
  selection.key('ArrowLeft');
  assert.equal(selection.getSnapshot().index, 139);
  assert.throws(() => { selection.getSnapshot().index = 7; }, TypeError);
  stop(); const before = calls; selection.selectIndex(70); assert.equal(calls, before);
});

test('reference overlays retain exact off-grid boundaries and remain off until selected', () => {
  const before = JSON.stringify(dataset);
  assert.deepEqual(referenceView(dataset).overlays, []);
  const arterial = referenceView(dataset, 'arterial-blood-uvm');
  assert.equal(arterial.overlays[0].lower, 7.35);
  assert.equal(arterial.overlays[0].upper, 7.45);
  assert.equal(arterial.overlays[0].value, null);
  assert.equal(arterial.overlays[0].kind, 'reference_band');
  assert.equal(JSON.stringify(dataset), before);
});

test('study points and confidence intervals retain distinct meanings without invented uncertainty', () => {
  const point = referenceView(dataset, 'neuronal-cytosol-bonnet-2019').overlays[0];
  assert.equal(point.kind, 'study_marker'); assert.equal(point.value, 7.09);
  assert.equal(point.lower, null); assert.equal(point.upper, null);
  const ci = referenceView(dataset, 'csf-merril-1961').overlays[0];
  assert.deepEqual([ci.value, ci.lower, ci.upper, ci.confidenceLevel], [7.31, 7.29, 7.33, 0.99]);
});

test('custom comparison never replaces a source gap and cannot mutate accepted metadata', () => {
  const comparison = {context_id: 'bowmans-space-gap', context_label: dataset.presets.find(item => item.id === 'bowmans-space-gap').label, label: 'My comparison',
    ph: {lower: 7.35, upper: 7.45}, display_kind: 'custom_band', statistic: 'user_defined_comparison',
    source_kind: 'user_entered', evidence_status: 'unverified'};
  const view = referenceView(dataset, 'bowmans-space-gap', comparison);
  assert.equal(view.gap.label, 'No verified numerical interval available');
  assert.equal(view.context.ph, null);
  assert.equal(view.overlays.length, 1);
  assert.equal(view.overlays[0].kind, 'custom_band');
  assert.equal(view.overlays[0].evidenceStatus, 'unverified');
  comparison.ph.lower = 1;
  assert.equal(view.overlays[0].lower, 7.35);
  assert.throws(() => { view.context.ph = {}; }, TypeError);
});

test('current installed capability cannot yield any molecular datasets or percentage axis', () => {
  const caps = {module: 'maraPssiwS', molecular_predictor_available: false, mathematical_evaluator_available: true,
    external_providers_enabled: false, scope: 'Arithmetic only', state_classes: Object.fromEntries(
      ['protonation', 'tautomer', 'stereoisomer', 'conformer'].map(id => [id, {status: 'unavailable', reason: 'No model'}]))};
  const value = validateCapabilities(caps);
  const view = referencePresentation(value, referenceView(dataset));
  assert.equal(view.label, 'pH reference context');
  assert.equal(view.populationAxis, false);
  assert.deepEqual(view.datasets, []);
  assert.equal(view.unavailableClasses.length, 4);
  assert.throws(() => validateCapabilities({...caps, molecular_predictor_available: true}));
});

test('production Python serializer rows validate and remain unchanged across table and visibility selection', () => {
  const before = JSON.stringify(fixtures.complete.envelope);
  const value = accept('complete');
  assert.equal(value.model.conditions.temperature_kelvin, 298.15, 'Must not relabel the actual temperature as 37 C');
  assert.equal(value.model.conditions.ionic_strength_mol_l, null);
  for (let index = 0; index <= 140; index++) {
    const all = populationTable(value, index);
    const subset = populationTable(value, index, {visibleStateIds: ['HA']});
    assert.equal(all.ph, index / 10);
    assert.deepEqual(subset.rows.map(row => row.fraction), all.rows.map(row => row.fraction));
    assert.equal(subset.rows[0].visible, true); assert.equal(subset.rows[1].visible, false);
    assert.equal(subset.plottedMass, all.rows[0].fraction);
    assert.equal(subset.hiddenRepresentedMass, all.rows[1].fraction);
  }
  assert.deepEqual(populationTable(value, 70).rows.map(row => row.fraction), [0.5, 0.5]);
  assert.equal(JSON.stringify(fixtures.complete.envelope), before);
  assert.throws(() => { value.samples[70].result.fractions[0] = 1; }, TypeError);
  assert.throws(() => populationTable(clone(value), 70), /validated/);
});

test('known omitted mass and unknown conditional mass stay distinct at each sample', () => {
  const value = accept('incomplete');
  const known = populationTable(value, 70, {visibleStateIds: ['HA']});
  assert.equal(known.normalizationScope, 'supplied_total_mass');
  assert.equal(known.omittedMass, 0.2);
  assert.equal(known.representedMass, 0.8);
  assert.equal(known.plottedMass, 0.4);
  assert.equal(known.hiddenRepresentedMass, 0.4);
  assert.equal(known.fullEnsembleMass, 1);
  assert.match(known.omittedMassSource, /pH 7/);
  const unknown = populationTable(value, 69);
  assert.equal(unknown.normalizationScope, 'conditional_on_represented_states');
  assert.equal(unknown.omittedMass, null);
  assert.equal(unknown.fullEnsembleMass, null);
  assert.match(unknown.interpretation, /unknown/);
  assert.deepEqual(unknown.warnings, value.samples[69].result.warnings);
  assert.equal(known.equilibriumScope, 'conditional_configuration');
});

test('outside-domain and explicit unknown samples are null gaps with no interpolation or extrapolation', () => {
  const narrow = accept('narrow');
  const series = populationSeries(narrow);
  for (const curve of series) {
    assert.equal(curve.spanGaps, false);
    assert.equal(curve.points.filter(point => point.y !== null).length, 1);
    assert.equal(curve.points[73].y, null); assert.equal(curve.points[75].y, null);
    assert.equal(curve.points[74].x, 7.4);
  }
  const gap = populationTable(narrow, 73);
  assert.equal(gap.status, 'outside_model_domain');
  assert.equal(gap.representedMass, null); assert.deepEqual(gap.rows, []);
  const input = clone(fixtures.complete.envelope);
  input.samples[70] = {index: 70, ph: 7, status: 'unavailable', result: null, reason: 'Explicit missing sample'};
  const value = validatePopulationEnvelope(input, fixtures.complete.binding);
  assert.equal(populationSeries(value)[0].points[70].y, null);
  assert.equal(populationTable(value, 70).reason, 'Explicit missing sample');
});

test('aggregation sums the same full ensemble even when some curves are hidden', () => {
  const value = accept('complete');
  const full = populationTable(value, 70, {aggregateBy: 'formal_charge'});
  const subset = populationTable(value, 70, {aggregateBy: 'formal_charge', visibleStateIds: ['HA']});
  assert.deepEqual(subset.groups, full.groups);
  assert.equal(full.groups.reduce((sum, row) => sum + row.fraction, 0), 1);
  assert.throws(() => populationTable(value, 70, {aggregateBy: 'tautomer_id'}), /unavailable/);
  for (const visibleStateIds of [['missing'], ['HA', 'HA']]) assert.throws(() => populationTable(value, 70, {visibleStateIds}));
  assert.equal(populationTable(value, 70, {visibleStateIds: []}).plottedMass, 0);
});

test('state colors and redundant dash/label identity do not depend on order or visibility', () => {
  const ids = ['HA', 'A', '__proto__', 'stereo:R'];
  const original = new Map(ids.map(id => [id, stateStyle(id)]));
  for (const id of ids.reverse()) assert.deepEqual(stateStyle(id), original.get(id));
  const value = accept('complete');
  const all = populationSeries(value), hidden = populationSeries(value, {visibleStateIds: []});
  assert.deepEqual(hidden.map(row => row.style), all.map(row => row.style));
  assert.deepEqual(hidden.map(row => row.points), all.map(row => row.points));
  assert.equal(all[0].style.label, 'HA');
});

test('stale revision, graph, model signature and inconsistent row identities are rejected', () => {
  for (const field of ['documentRevision', 'graphHash', 'modelSignature']) {
    const input = clone(fixtures.complete.envelope);
    input[field] = field === 'documentRevision' ? 8 : 'b'.repeat(64);
    assert.throws(() => validatePopulationEnvelope(input, fixtures.complete.binding), /binding/);
  }
  for (const mutate of [
    value => { value.samples[70].result.model_signature = 'b'.repeat(64); },
    value => { value.samples[70].result.state_ids.reverse(); },
    value => { value.samples[70].result.input_identity = 'different'; },
    value => { value.samples[70].result.conditions.temperature_kelvin = 310.15; },
    value => { value.samples[70].result.provenance.source = 'different source'; },
  ]) {
    const input = clone(fixtures.complete.envelope); mutate(input);
    assert.throws(() => validatePopulationEnvelope(input, fixtures.complete.binding));
  }
});

test('malformed, nonfinite, negative, unnormalized and missing grid data fail without repair', () => {
  for (const mutate of [
    value => { value.samples.pop(); },
    value => { value.samples[70].index = 71; },
    value => { value.samples[70].ph = 7.35; },
    value => { value.samples[70].result.fractions[0] = -0.1; },
    value => { value.samples[70].result.fractions[0] = NaN; },
    value => { value.samples[70].result.fractions[0] = Infinity; },
    value => { value.samples[70].result.percentages[0] = 100; },
    value => { value.samples[70].result.log_fractions[0] = -Infinity; },
    value => { value.samples[70].result.omitted_fraction = 0.5; },
    value => { value.samples[70].result.normalization_scope = 'silently_normalized'; },
    value => { value.samples[70].result.fractions = [0.4, 0.4]; value.samples[70].result.percentages = [40, 40]; value.samples[70].result.log_fractions = [Math.log(0.4), Math.log(0.4)]; },
    value => { value.model.states[1].atom_ids = ['changed-core']; },
    value => { value.model.states[0].extra_state_graph = {}; },
  ]) {
    const input = clone(fixtures.complete.envelope); mutate(input);
    assert.throws(() => validatePopulationEnvelope(input, fixtures.complete.binding));
  }
  const input = clone(fixtures.incomplete.envelope);
  input.samples[69].result.omitted_mass_source = 'Cannot turn unknown mass into known mass';
  assert.throws(() => validatePopulationEnvelope(input, fixtures.incomplete.binding));
});

test('capability errors cannot fabricate availability and malicious object accessors are not invoked', () => {
  validateCapabilities(fixtures.capabilities);
  for (const mutation of [
    value => { delete value.state_classes.conformer; },
    value => { value.state_classes.protonation.status = 'available'; },
    value => { value.external_providers_enabled = true; },
    value => { value.mathematical_evaluator_available = 'true'; },
  ]) {
    const value = clone(fixtures.capabilities); mutation(value); assert.throws(() => validateCapabilities(value));
  }
  let called = false;
  const payload = {...fixtures.capabilities};
  Object.defineProperty(payload, 'scope', {enumerable: true, get() { called = true; return 'hidden'; }});
  assert.throws(() => validateCapabilities(payload), /accessors/);
  assert.equal(called, false);
});

test('reference metadata and comparison validation do not turn uncertainty or gaps into predictions', () => {
  for (const mutate of [
    context => { context.ph.lower = 7; },
    context => { context.reported_uncertainty.plotted = true; },
    context => { context.join_trajectory = true; },
  ]) {
    const data = clone(dataset);
    mutate(data.presets.find(item => item.id === 'neuronal-cytosol-bonnet-2019'));
    assert.throws(() => referenceView(data, 'neuronal-cytosol-bonnet-2019'));
  }
  assert.throws(() => referenceView(dataset, 'not-a-context'));
  const comparison = {context_id: 'bowmans-space-gap', context_label: dataset.presets.find(item => item.id === 'bowmans-space-gap').label, label: 'Custom',
    ph: {lower: 7.35, upper: 7.45}, display_kind: 'custom_band', statistic: 'user_defined_comparison',
    source_kind: 'user_entered', evidence_status: 'verified'};
  assert.throws(() => referenceView(dataset, 'bowmans-space-gap', comparison));
});

function parseCsv(text) {
  const rows = []; let row = [], field = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === '"') { if (quoted && text[i + 1] === '"') { field += '"'; i++; } else quoted = !quoted; }
    else if (!quoted && ch === ',') { row.push(field); field = ''; }
    else if (!quoted && ch === '\r' && text[i + 1] === '\n') { row.push(field); rows.push(row); row = []; field = ''; i++; }
    else field += ch;
  }
  return rows;
}

test('reference exports retain exact values, provenance and gap status with safe recoverable CSV text', () => {
  const comparison = {context_id: 'bowmans-space-gap', context_label: dataset.presets.find(item => item.id === 'bowmans-space-gap').label, label: '=HYPERLINK("https://example.invalid")',
    ph: {lower: 7.35, upper: 7.45}, display_kind: 'custom_band', statistic: 'user_defined_comparison',
    source_kind: 'user_entered', evidence_status: 'unverified'};
  const view = referenceView(dataset, 'bowmans-space-gap', comparison);
  const exported = JSON.parse(serializeReferenceJson(view, {index: 73}));
  assert.equal(exported.exportKind, 'ph-reference-context');
  assert.deepEqual(exported.selectedSample, {index: 73, ph: 7.3});
  assert.equal(exported.populationResults, null);
  assert.deepEqual(exported.reference, view);
  assert.equal(exported.reference.gap.label, 'No verified numerical interval available');
  const rows = parseCsv(referenceCsv(view, {index: 73}));
  assert.deepEqual(rows[0], ['path', 'type', 'value', 'spreadsheet_escaped']);
  assert.deepEqual(rows.find(row => row[0] === '/reference/comparison/label'),
    ['/reference/comparison/label', 'string', "'" + comparison.label, 'true']);
  assert.deepEqual(rows.find(row => row[0] === '/reference/comparison/ph/lower'),
    ['/reference/comparison/ph/lower', 'number', '7.35', 'false']);
  assert.deepEqual(rows.find(row => row[0] === '/reference/context/ph'),
    ['/reference/context/ph', 'null', 'null', 'false']);
  assert.throws(() => serializeReferenceJson(clone(view)), /validated/);
});
