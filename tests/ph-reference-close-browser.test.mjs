import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor, ready, state} from './editor-browser-fixture.mjs';

async function bounded(promise, milliseconds, label) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(label)), milliseconds);
    })]);
  } finally { clearTimeout(timer); }
}

test('a physiology response arriving during native close review cannot enable chart actions, and cancellation restores them', {timeout: 90000}, async t => {
  const page = await productionEditor(t);
  const pattern = '**/api/physiology';
  let releaseResponse, observeRequest;
  const held = new Promise(resolve => { observeRequest = resolve; });
  const gate = new Promise(resolve => { releaseResponse = resolve; });
  const handler = async route => {
    observeRequest();
    await gate;
    await route.continue();
  };
  await page.route(pattern, handler);
  let reviewing = false;
  try {
    await page.reload();
    await ready(page);
    await bounded(held, 10000, 'The reloaded production physiology request was not observed.');
    assert.equal(await page.locator('#physiology-context option').count(), 1,
      'The reference response must still be held when close review begins.');
    const before = await state(page);
    const locked = await page.evaluate(() => window.nivramDocument.beginCloseReview());
    reviewing = true;
    const {appearance,...lockedDocument}=locked;
    assert.deepEqual(lockedDocument, before);
    assert.deepEqual(appearance,{theme:'light',revision:0,generation:null});
    assert.equal(await page.locator('#theme-toggle').isDisabled(),true);
    assert.match(await page.locator('#request-status').innerText(), /Closing review/);

    releaseResponse();
    await page.waitForFunction(() => document.querySelector('#physiology-context')?.options.length > 1);
    await page.waitForFunction(() => /Reference chart available/.test(document.querySelector('#ph-chart-status')?.textContent ?? ''));
    assert.equal(await page.locator('#open-ph-chart').isDisabled(), true,
      'Completing physiology/capability loading must not enable the chart during an existing native close review.');
    assert.equal(await page.locator('#ph-reference-dialog').evaluate(dialog => dialog.open), false);
    assert.equal(await page.locator('#ph-chart-host canvas').count(), 0);
    assert.deepEqual(await state(page), before);

    await page.evaluate(() => window.nivramDocument.endCloseReview());
    reviewing = false;
    await ready(page);
    assert.equal(await page.locator('#theme-toggle').isDisabled(),false);
    await page.locator('#open-ph-chart').click();
    await page.locator('#ph-reference-dialog[open] canvas').waitFor();
    assert.equal(await page.evaluate(() => Object.keys(window.Chart.instances).length), 1);

    // The same real application methods must close an already mounted chart.
    await page.evaluate(() => window.nivramDocument.beginCloseReview());
    reviewing = true;
    assert.equal(await page.locator('#ph-reference-dialog').evaluate(dialog => dialog.open), false);
    assert.equal(await page.locator('#ph-chart-host canvas').count(), 0);
    assert.equal(await page.evaluate(() => Object.keys(window.Chart.instances).length), 0);
    assert.equal(await page.locator('#open-ph-chart').isDisabled(), true);
    for (const kind of ['png', 'csv', 'json']) {
      assert.equal(await page.locator(`#ph-export-${kind}`).isDisabled(), true);
    }
    assert.deepEqual(await state(page), before);
    await page.evaluate(() => window.nivramDocument.endCloseReview());
    reviewing = false;
    await ready(page);
    await page.locator('#open-ph-chart').click();
    await page.locator('#ph-reference-dialog[open] canvas').waitFor();
    await page.locator('#close-ph-chart').click();
    assert.equal(await page.evaluate(() => Object.keys(window.Chart.instances).length), 0);
    assert.deepEqual(await state(page), before);
  } finally {
    releaseResponse();
    if (reviewing && !page.isClosed()) {
      await page.evaluate(() => window.nivramDocument.endCloseReview());
    }
    await page.unroute(pattern, handler);
  }
});
