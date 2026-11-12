const DEFAULT_DETAIL = 'Select a reference context. No interval is assumed for your physiology.';
const CUSTOM_LIMIT = 'User-entered comparison; applicability to this context has not been verified. This is not a verified physiological interval or a molecular-population prediction.';
const mountedExplorers = new WeakMap();

function freezeReference(value) {
  if (value && typeof value === 'object') {
    for (const child of Object.values(value)) freezeReference(child);
    Object.freeze(value);
  }
  return value;
}

function parsePh(value) {
  if (typeof value !== 'number' && (typeof value !== 'string' ||
      !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim()))) {
    throw new Error('Enter a finite decimal pH for both bounds.');
  }
  const result = Number(value);
  if (!Number.isFinite(result)) throw new Error('Enter a finite decimal pH for both bounds.');
  if (result < 0 || result > 14) throw new Error('Comparison bounds must be from 0 to 14, the current display domain.');
  return result;
}

/** Session-only annotations. Source presets and molecular documents are never modified. */
export function createComparisonStore(contexts) {
  const available = new Map(contexts.map(context => [context.id, context.label]));
  const ranges = new Map();
  return Object.freeze({
    get: contextId => ranges.get(contextId) ?? null,
    clear: contextId => ranges.delete(contextId),
    set(contextId, input) {
      if (!available.has(contextId)) throw new Error('Select an available context before adding a comparison.');
      const lower = parsePh(input.lower), upper = parsePh(input.upper);
      if (lower > upper) throw new Error('The lower pH must be less than or equal to the upper pH.');
      const label = input.label ?? 'Custom comparison';
      if (typeof label !== 'string' || label.length > 80 || /[\x00-\x1f\x7f]/.test(label)) {
        throw new Error('Use a single-line comparison label of at most 80 characters.');
      }
      const comparison = Object.freeze({
        context_id: contextId,
        context_label: available.get(contextId),
        label: label.trim() || 'Custom comparison',
        ph: Object.freeze(lower === upper ? {value: lower, lower: null, upper: null} : {lower, upper}),
        display_kind: lower === upper ? 'custom_marker' : 'custom_band',
        statistic: 'user_defined_comparison',
        source_kind: 'user_entered',
        evidence_status: 'unverified',
      });
      ranges.set(contextId, comparison);
      return comparison;
    },
  });
}

export function describeCustomComparison(comparison) {
  if (!comparison) return 'No custom comparison applied.';
  const quantity = comparison.display_kind === 'custom_marker' ? `${comparison.ph.value} (single pH value)` : `${comparison.ph.lower}–${comparison.ph.upper}`;
  return `${comparison.label}: pH ${quantity}. ${CUSTOM_LIMIT}`;
}

export function describePhysiologyContext(dataset, context) {
  if (!context) return DEFAULT_DETAIL;
  let quantity = context.gap_label;
  if (context.display_kind !== 'gap') {
    const statistic = context.statistic === 'reported_value' ? 'reported value; central statistic unverified' : context.statistic.replaceAll('_', ' ');
    const ph = context.ph;
    if (ph.lower != null) {
      const estimate = ph.value != null ? `reported estimate ${ph.value}; ` : '';
      const confidence = ph.confidence_level != null ? `; ${Number((ph.confidence_level * 100).toFixed(6))}% confidence` : '';
      quantity = `${estimate}${ph.lower}–${ph.upper} (${statistic}${confidence})`;
    } else quantity = `${ph.value} (${statistic})`;
  }
  const sources = dataset.sources.filter(source => context.source_ids.includes(source.id));
  return `pH: ${quantity}\n${context.compartment}; ${context.anatomical_site}.\n${context.species}; ${context.population}.\n${context.preparation}; ${context.condition}.\n${context.temperature_note}\nMethod: ${context.measurement_method}. Handling: ${context.specimen_handling}.\n${context.evidence_limitations.join(' ')}\n${context.gap_reason || ''}\nSources: ${sources.map(source => source.title + ' — ' + source.url).join('\n')}`;
}

/** The only DOM adapter; no document-model, persistence, renderer or network access. */
export function mountPhysiologyExplorer({dataset: suppliedDataset, select, detail, onChange}) {
  if (mountedExplorers.has(select)) throw new Error('This physiology selector already has an active explorer.');
  if (onChange !== undefined && typeof onChange !== 'function') throw new TypeError('onChange must be a function.');
  const dataset = freezeReference(structuredClone(suppliedDataset));
  const dom = select.ownerDocument;
  const store = createComparisonStore(dataset.presets);
  const originalValue = select.value, originalDetail = detail.textContent;
  const options = [], listeners = new Set(onChange ? [onChange] : []);
  let snapshot = null, destroyed = false;
  for (const context of dataset.presets) {
    const option = dom.createElement('option');
    option.value = context.id; option.textContent = context.label; select.append(option);
    options.push(option);
  }
  const form = dom.createElement('form');
  const fields = dom.createElement('fieldset');
  const legend = dom.createElement('legend'); legend.textContent = 'Custom pH comparison';
  const note = dom.createElement('p'); note.className = 'muted'; note.id = 'physiology-custom-note';
  note.textContent = `${CUSTOM_LIMIT} Stored only in this open window. Bounds use the current 0–14 display domain; they are not rounded to 0.1 pH. Equal bounds specify a single pH value.`;
  const row = dom.createElement('div'); row.className = 'two-fields';
  const makeInput = (id, title, type) => {
    const wrapper = dom.createElement('div');
    const label = dom.createElement('label'); label.htmlFor = id; label.textContent = title;
    const input = dom.createElement('input'); input.id = id; input.type = type;
    input.setAttribute('aria-describedby', 'physiology-custom-note physiology-custom-error');
    wrapper.append(label, input);
    return {wrapper, input};
  };
  const lower = makeInput('physiology-custom-lower', 'Lower pH', 'number');
  const upper = makeInput('physiology-custom-upper', 'Upper pH', 'number');
  for (const input of [lower.input, upper.input]) {
    input.required = true; input.min = '0'; input.max = '14'; input.step = 'any';
  }
  const label = makeInput('physiology-custom-label', 'Comparison label (optional)', 'text');
  label.input.maxLength = 80;
  const apply = dom.createElement('button'); apply.type = 'submit'; apply.textContent = 'Apply comparison';
  const clear = dom.createElement('button'); clear.type = 'button'; clear.textContent = 'Remove comparison';
  const buttons = dom.createElement('div'); buttons.className = 'button-group'; buttons.append(apply, clear);
  const error = dom.createElement('p'); error.id = 'physiology-custom-error'; error.setAttribute('role', 'alert');
  const result = dom.createElement('p'); result.id = 'physiology-custom-result'; result.className = 'evidence-detail';
  result.setAttribute('role', 'status'); result.setAttribute('aria-live', 'polite');
  row.append(lower.wrapper, upper.wrapper);
  fields.append(legend, note, row, label.wrapper, buttons);
  form.append(fields, error, result); detail.after(form);
  function refresh() {
    if (destroyed) return;
    const context = dataset.presets.find(item => item.id === select.value) ?? null;
    detail.textContent = describePhysiologyContext(dataset, context);
    fields.disabled = !context;
    const comparison = store.get(select.value);
    lower.input.value = comparison ? String(comparison.ph.lower ?? comparison.ph.value) : '';
    upper.input.value = comparison ? String(comparison.ph.upper ?? comparison.ph.value) : '';
    label.input.value = comparison?.label || '';
    clear.disabled = !comparison;
    result.textContent = describeCustomComparison(comparison);
    error.textContent = '';
    if (snapshot?.context === context && snapshot.comparison === comparison) return;
    snapshot = Object.freeze({contextId: context?.id ?? null, context, comparison});
    const failures = [];
    for (const listener of [...listeners]) {
      try { listener(snapshot); } catch (failure) { failures.push(failure); }
    }
    // The accepted selection is committed. A broken observer cannot prevent
    // other views from seeing it; report failures only after all are notified.
    if (failures.length) throw new AggregateError(failures, 'Reference selection changed, but a subscribed view could not be refreshed.');
  }
  select.addEventListener('change', refresh);
  function submit(event) {
    event.preventDefault();
    if (destroyed) return;
    try {
      store.set(select.value, {lower: lower.input.value, upper: upper.input.value, label: label.input.value});
    } catch (failure) {
      error.textContent = failure.message;
      // An invalid proposal leaves the preceding accepted comparison visible.
      return;
    }
    refresh();
  }
  function removeComparison() {
    if (destroyed) return;
    store.clear(select.value); refresh();
  }
  form.addEventListener('submit', submit);
  clear.addEventListener('click', removeComparison);
  const explorer = Object.freeze({
    getSnapshot: () => snapshot,
    selectContext(contextId) {
      if (destroyed) throw new Error('This physiology explorer has been destroyed.');
      if (contextId !== null && contextId !== '' && !dataset.presets.some(context => context.id === contextId)) {
        throw new Error('Select an available reference context.');
      }
      // Use the same accepted per-context comparisons as the editor controls.
      // Invalid IDs leave both the selection and any uncommitted input intact.
      select.value = contextId ?? '';
      refresh();
      return snapshot;
    },
    subscribe(listener) {
      if (destroyed) throw new Error('This physiology explorer has been destroyed.');
      if (typeof listener !== 'function') throw new TypeError('The subscriber must be a function.');
      listeners.add(listener);
      try { listener(snapshot); } catch (failure) { listeners.delete(listener); throw failure; }
      return () => listeners.delete(listener);
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      select.removeEventListener('change', refresh);
      form.removeEventListener('submit', submit);
      clear.removeEventListener('click', removeComparison);
      listeners.clear();
      for (const option of options) option.remove();
      form.remove(); select.value = originalValue; detail.textContent = originalDetail;
      mountedExplorers.delete(select);
    },
  });
  mountedExplorers.set(select, explorer);
  try { refresh(); } catch (failure) { explorer.destroy(); throw failure; }
  return explorer;
}
