import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createComparisonStore, describeCustomComparison, describePhysiologyContext} from '../public/modules/physiology.mjs';

const dataset = JSON.parse(await readFile(new URL('../nivram/data/physiology.json', import.meta.url), 'utf8'));
const gap = dataset.presets.find(item => item.id === 'bowmans-space-gap');
const input = {lower: '7.35', upper: '7.45', label: 'My comparison'};

test('custom comparison retains exact off-grid bounds and explicit non-evidence identity', () => {
  const store = createComparisonStore(dataset.presets);
  const result = store.set(gap.id, input);
  assert.deepEqual(result.ph, {lower: 7.35, upper: 7.45});
  assert.equal(result.context_id, gap.id);
  assert.equal(result.display_kind, 'custom_band');
  assert.equal(result.statistic, 'user_defined_comparison');
  assert.equal(result.source_kind, 'user_entered');
  assert.equal(result.evidence_status, 'unverified');
  for (const field of ['species', 'temperature', 'preparation', 'measurement_method', 'source_ids']) {
    assert.equal(field in result, false);
  }
  assert.match(describeCustomComparison(result), /7\.35–7\.45/);
  assert.match(describeCustomComparison(result), /not a verified physiological interval/i);
  assert.match(describeCustomComparison(result), /applicability to this context has not been verified/i);
});

test('applying a custom range cannot replace or mutate the source gap or dataset', () => {
  const before = JSON.stringify(dataset);
  const store = createComparisonStore(dataset.presets);
  store.set(gap.id, input);
  assert.equal(JSON.stringify(dataset), before);
  assert.equal(gap.display_kind, 'gap');
  assert.equal(gap.ph, null);
  assert.match(describePhysiologyContext(dataset, gap), /No verified numerical interval available/);
});

test('unselected and invented contexts cannot receive a comparison', () => {
  const store = createComparisonStore(dataset.presets);
  for (const id of ['', 'missing', null]) assert.throws(() => store.set(id, input), /Select an available context/);
});

test('empty, nonnumeric, nonfinite and nondecimal inputs are rejected without replacing a valid range', () => {
  const store = createComparisonStore(dataset.presets);
  const accepted = store.set(gap.id, input);
  for (const value of ['', ' ', null, undefined, NaN, Infinity, true, [], {}, '0x7', '7.2 pH']) {
    for (const bound of ['lower', 'upper']) {
      assert.throws(() => store.set(gap.id, {...input, [bound]: value}), /finite decimal pH/);
      assert.equal(store.get(gap.id), accepted);
    }
  }
});

test('range bounds must not reverse or exceed the requested 0–14 comparison display domain', () => {
  const store = createComparisonStore(dataset.presets);
  const accepted = store.set(gap.id, input);
  for (const [lower, upper] of [[-0.1, 7], [7, 14.1], [8, 7]]) {
    assert.throws(() => store.set(gap.id, {...input, lower, upper}), /0 to 14|lower.*less/i);
    assert.equal(store.get(gap.id), accepted);
  }
  assert.deepEqual(store.set(gap.id, {lower: 0, upper: 14}).ph, {lower: 0, upper: 14});
});

test('equal custom bounds are a single-value marker, never a manufactured interval', () => {
  const store = createComparisonStore(dataset.presets);
  const result = store.set(gap.id, {lower: 7.35, upper: 7.35});
  assert.equal(result.display_kind, 'custom_marker');
  assert.deepEqual(result.ph, {value: 7.35, lower: null, upper: null});
  assert.match(describeCustomComparison(result), /7\.35 \(single pH value\)/);
  assert.doesNotMatch(describeCustomComparison(result), /7\.35–7\.35/);
});

test('contexts and independent sessions keep separate explicit comparison values', () => {
  const store = createComparisonStore(dataset.presets);
  const other = createComparisonStore(dataset.presets);
  const otherId = 'synaptic-cleft-gap';
  const first = store.set(gap.id, input);
  const second = store.set(otherId, {lower: 6.8, upper: 7.1});
  assert.equal(store.get(gap.id), first);
  assert.equal(store.get(otherId), second);
  assert.equal(other.get(gap.id), null);
  assert.equal(store.clear(gap.id), true);
  assert.equal(store.get(gap.id), null);
  assert.equal(store.get(otherId), second);
});

test('accepted ranges are immutable and caller mutation cannot alter stored results', () => {
  const store = createComparisonStore(dataset.presets);
  const supplied = {...input};
  const result = store.set(gap.id, supplied);
  supplied.lower = '1';
  assert.throws(() => { result.ph.lower = 1; }, TypeError);
  assert.throws(() => { result.evidence_status = 'verified'; }, TypeError);
  assert.equal(store.get(gap.id).ph.lower, 7.35);
});

test('labels are bounded plain text, with an explicit custom label when omitted', () => {
  const store = createComparisonStore(dataset.presets);
  assert.equal(store.set(gap.id, {lower: 7, upper: 8}).label, 'Custom comparison');
  assert.equal(store.set(gap.id, {...input, label: '  trial <b>one</b>  '}).label, 'trial <b>one</b>');
  for (const label of ['x'.repeat(81), 'line\nline', 23]) {
    assert.throws(() => store.set(gap.id, {...input, label}), /label/i);
  }
});

test('study points stay points; confidence intervals retain their reported central value', () => {
  const point = dataset.presets.find(item => item.id === 'neuronal-cytosol-bonnet-2019');
  const ci = dataset.presets.find(item => item.id === 'csf-merril-1961');
  const pointText = describePhysiologyContext(dataset, point);
  const ciText = describePhysiologyContext(dataset, ci);
  assert.match(pointText, /7\.09 \(reported value; central statistic unverified\)/);
  assert.doesNotMatch(pointText.split('\n')[0], /–/);
  assert.match(ciText, /7\.31/);
  assert.match(ciText, /7\.29–7\.33/);
  assert.match(ciText, /99%/);
  assert.match(ciText, /mean confidence interval/);
});
