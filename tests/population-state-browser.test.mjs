import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdir} from 'node:fs/promises';
import {productionEditor,state,ready} from './editor-browser-fixture.mjs';

const model=await readFile(new URL('../examples/population-analytical-pka7.json',import.meta.url));
const structures=await readFile(new URL('../examples/population-analytical-pka7-structures.json',import.meta.url));
const upload=(page,id,buffer,name)=>page.locator('#'+id).setInputFiles({name,mimeType:'application/json',buffer});
async function loadModel(page){
  await page.locator('#open-population-model').click();
  await upload(page,'population-model-file',model,'synthetic-pka7.json');
  await page.waitForFunction(()=>document.querySelector('#population-model-status').textContent.startsWith('Supplied-model calculation ready.'));
}
async function reviewed(page){
  await page.waitForFunction(()=>document.querySelector('#population-state-status').textContent.startsWith('Preview ready.'),{},{timeout:25000});
  assert.equal(await page.locator('#population-state-adopt').isEnabled(),true);
}

test('actual model, mapped chemistry preview and explicit state adoption preserve the drawing until one undoable commit',async t=>{
  const page=await productionEditor(t),before=await state(page),requests=[];
  page.on('request',request=>{if(request.url().includes('/api/populations/prepare-state'))requests.push(request.url());});
  await loadModel(page);
  assert.equal(await page.locator('#population-state-adopt').isDisabled(),true);
  await upload(page,'population-structures-file',structures,'synthetic-state-structures.json');await reviewed(page);
  assert.deepEqual((await state(page)).document,before.document);
  await page.locator('[data-population-inspect="A"]').check();await reviewed(page);
  assert.match(await page.locator('#population-state-summary').textContent(),/State A;/);
  const preparedCount=requests.length;
  await page.locator('[data-population-ph]').fill('8.0');await page.locator('[data-population-form] button').click();
  await page.locator('[data-population-visible="A"]').uncheck();
  assert.match(await page.locator('#population-state-summary').textContent(),/pH 8\.0: 90\.9091%/);
  assert.equal(requests.length,preparedCount,'Cached pH/visibility changes do not rerun structure chemistry.');
  assert.equal(await page.locator('#population-state-preview').isVisible(),true);
  assert.deepEqual((await state(page)).document,before.document);
  assert.equal((await state(page)).revision,before.revision);
  await mkdir(new URL('../../checks/population-state-feature/',import.meta.url),{recursive:true});
  await page.locator('#population-state-panel').screenshot({path:new URL('../../checks/population-state-feature/preview.png',import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1')});
  await page.locator('#population-state-adopt').click();
  await page.waitForFunction(()=>!document.querySelector('#population-dialog').open);await ready(page);
  const after=await state(page);
  assert.equal(after.revision,before.revision+1);
  assert.equal(after.document.graph.atoms.length,2);
  assert.equal(after.document.graph.atoms.reduce((sum,atom)=>sum+atom.charge,0),-1);
  assert.deepEqual(after.document.style,before.document.style);assert.deepEqual(after.document.export,before.document.export);
  const receipt=after.document.analysis.populationAdoption;
  assert.equal(receipt.selectedSample.stateId,'A');assert.equal(receipt.selectedSample.index,80);
  assert.ok(Math.abs(receipt.selectedSample.fraction-10/11)<1e-12);
  assert.equal(receipt.receipt.depiction,'2d');assert.equal(receipt.receipt.conformerGeometry,'not adopted');
  await page.locator('#undo-button').click();await ready(page);
  assert.deepEqual((await state(page)).document,before.document,'One Undo restores the whole prior drawing and metadata.');
});

test('mismatched files and cancelled real-worker previews cannot adopt or modify the document',async t=>{
  const page=await productionEditor(t),before=await state(page);
  await loadModel(page);
  const mismatch=JSON.parse(structures);mismatch.modelInputHash='0'.repeat(64);
  await upload(page,'population-structures-file',Buffer.from(JSON.stringify(mismatch)),'wrong-model.json');
  await page.waitForFunction(()=>!document.querySelector('#choose-population-structures').disabled);
  assert.equal(await page.locator('#population-state-adopt').isDisabled(),true);
  assert.match(await page.locator('#population-state-status').textContent(),/model|match/i);
  let release,received,finished;
  const held=new Promise(resolve=>{release=resolve;}),responseReceived=new Promise(resolve=>{received=resolve;}),responseFinished=new Promise(resolve=>{finished=resolve;});
  await page.route('**/api/populations/prepare-state',async route=>{
    try{const response=await route.fetch();received();await held;await route.fulfill({response});}
    catch{/* The deliberate cancellation can close the intercepted request. */}
    finally{finished();}
  });
  await upload(page,'population-structures-file',structures,'synthetic-state-structures.json');
  await responseReceived;
  await page.locator('#population-state-cancel').click();release();await responseFinished;
  assert.equal(await page.locator('#population-state-adopt').isDisabled(),true);
  assert.equal(await page.locator('#population-state-preview').isVisible(),false);
  assert.match(await page.locator('#population-state-status').textContent(),/cancelled/i);
  assert.deepEqual((await state(page)).document,before.document);assert.equal((await state(page)).revision,before.revision);
  await page.unroute('**/api/populations/prepare-state');
  assert.equal(await page.locator('#population-state-preview-button').isEnabled(),true,'Cancelled previews can retry without reopening the file.');
  await page.locator('#population-state-preview-button').click();await reviewed(page);
  let releaseRender,renderReceived,renderFinished;
  const renderHeld=new Promise(resolve=>{releaseRender=resolve;}),receivedRender=new Promise(resolve=>{renderReceived=resolve;}),finishedRender=new Promise(resolve=>{renderFinished=resolve;});
  await page.route('**/api/render',async route=>{
    try{const response=await route.fetch();renderReceived();await renderHeld;await route.fulfill({response});}
    catch{/* Changing state deliberately aborts the pending document render. */}
    finally{renderFinished();}
  });
  await page.locator('#population-state-adopt').click();await receivedRender;
  await page.locator('[data-population-inspect="A"]').check();releaseRender();await finishedRender;
  await page.waitForFunction(()=>document.querySelector('#population-state-status').textContent!=='Applying the reviewed 2D state.');
  assert.deepEqual((await state(page)).document,before.document,'Changing state during adoption must cancel its awaited render.');
  assert.equal((await state(page)).revision,before.revision);
  assert.equal(await page.locator('#population-state-adopt').isDisabled(),true);
  await page.locator('#close-population-model').click();
  assert.equal(await page.locator('#population-state-adopt').isDisabled(),true);
});
