import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor, state} from './editor-browser-fixture.mjs';

test('physiology explorer publishes immutable accepted states and disposes its owned DOM', {timeout:45000}, async t => {
  const page = await productionEditor(t);
  const before = await state(page);
  const result = await page.evaluate(async () => {
    'use strict';
    const {mountPhysiologyExplorer} = await import('/modules/physiology.mjs');
    const dataset = await (await fetch('/api/physiology')).json();
    const original = JSON.stringify(dataset);
    // An isolated, real browser document exercises component lifecycle without
    // injecting edits or replacing the production editor's existing controls.
    const frame = document.createElement('iframe');
    document.body.append(frame);
    let component;
    try {
      const doc = frame.contentDocument;
      const select = doc.createElement('select'), detail = doc.createElement('p');
      const option = doc.createElement('option'); option.value = ''; option.textContent = 'Choose';
      select.append(option); detail.textContent = 'Initial reference text'; doc.body.append(select, detail);
      const changes = [];
      component = mountPhysiologyExplorer({dataset, select, detail});
      if (!component?.getSnapshot || !component?.subscribe || !component?.destroy) {
        throw new Error('Observable/disposable physiology explorer contract missing');
      }
      const unsubscribe = component.subscribe(value => changes.push(value));
      const choose = value => {select.value = value; select.dispatchEvent(new Event('change'));};
      const initial = component.getSnapshot();
      choose('arterial-blood-uvm');
      const arterial = component.getSnapshot();
      const noOp = component.getSnapshot(); choose('arterial-blood-uvm');
      const stableNoOp = noOp === component.getSnapshot();
      let mutationRejected = false;
      try {arterial.context.ph.lower = 1;} catch {mutationRejected = true;}
      choose('bowmans-space-gap');
      const form = doc.querySelector('form');
      const lower = doc.getElementById('physiology-custom-lower');
      const upper = doc.getElementById('physiology-custom-upper');
      lower.value = '7.35'; upper.value = '7.45';
      form.dispatchEvent(new Event('submit', {cancelable:true}));
      const accepted = component.getSnapshot(), acceptedCount = changes.length;
      lower.value = '8'; upper.value = '7';
      form.dispatchEvent(new Event('submit', {cancelable:true}));
      const invalidPreserved = component.getSnapshot() === accepted && changes.length === acceptedCount;
      const visibleError = Boolean(doc.getElementById('physiology-custom-error').textContent);
      const clear = [...form.querySelectorAll('button')].find(b => b.textContent === 'Remove comparison');
      clear.click();
      const cleared = component.getSnapshot();
      unsubscribe(); choose('arterial-blood-uvm');
      const countAfterUnsubscribe = changes.length;
      const detachedSnapshot = component.getSnapshot();
      component.destroy(); component.destroy();
      select.dispatchEvent(new Event('change'));
      const disposed = !doc.querySelector('form') && select.options.length === 1
        && detail.textContent === 'Initial reference text'
        && component.getSnapshot() === detachedSnapshot && changes.length === countAfterUnsubscribe;
      let disposedSubscriptionRejected = false;
      try {component.subscribe(() => {});} catch {disposedSubscriptionRejected = true;}
      const remounted = mountPhysiologyExplorer({dataset, select, detail});
      const remountClean = remounted.getSnapshot().context === null
        && doc.querySelectorAll('form').length === 1 && select.options.length === dataset.presets.length + 1;
      remounted.destroy();
      return {initial, arterial, accepted, cleared, stableNoOp, mutationRejected, invalidPreserved,
        visibleError, disposed, disposedSubscriptionRejected, remountClean,
        changes: changes.length, sourceUnchanged: JSON.stringify(dataset) === original};
    } finally {component?.destroy(); frame.remove();}
  });
  assert.equal(result.initial.context, null);
  assert.equal(result.initial.comparison, null);
  assert.equal(result.arterial.context.ph.lower, 7.35);
  assert.equal(result.arterial.context.ph.upper, 7.45);
  assert.equal(result.accepted.context.display_kind, 'gap');
  assert.deepEqual(result.accepted.comparison.ph, {lower:7.35, upper:7.45});
  assert.equal(result.cleared.comparison, null);
  for (const key of ['stableNoOp','mutationRejected','invalidPreserved','visibleError','disposed',
    'disposedSubscriptionRejected','remountClean','sourceUnchanged']) assert.equal(result[key], true, key);
  assert.equal(result.changes, 5);
  assert.deepEqual(await state(page), before, 'Reference selection cannot mutate the molecular document');
});
