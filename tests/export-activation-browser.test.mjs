import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,state} from './editor-browser-fixture.mjs';

test('production reference exports retain trusted activation metadata without changing the molecule', {timeout:60000},async t=>{
  const page=await productionEditor(t),before=await state(page);
  const snapshot=()=>page.evaluate(async()=> (await import('/modules/export-activation.mjs')).referenceExportDiagnostics());
  assert.deepEqual(await snapshot(),{schema:1,seen:0,dropped:0,events:[]});
  await page.locator('#open-ph-chart').click();await page.locator('#ph-reference-dialog[open] canvas').waitFor();
  for(const kind of ['json','csv']){
    const pending=page.waitForEvent('download');await page.locator('#ph-export-'+kind).click();
    const download=await pending;assert.equal(await download.failure(),null);
    const stream=await download.createReadStream();assert.ok(stream);let bytes=0;
    try{for await(const chunk of stream){bytes+=chunk.length;assert.ok(bytes<65536);}}finally{stream.destroy();}
    assert.ok(bytes>0);
  }
  const result=await snapshot();
  assert.deepEqual(result,{schema:1,seen:2,dropped:0,events:['json','csv'].map((format,index)=>({
    sequence:index+1,format,trusted:true,handler_active:true,dispatch_active:true,dispatch_observed:true
  }))});
  await page.locator('#close-ph-chart').click();
  assert.deepEqual(await snapshot(),result,'Closing the chart must preserve local troubleshooting evidence.');
  assert.deepEqual((await state(page)).document,before.document);
});

test('asynchronous PNG export samples the dispatch boundary after encoding', {timeout:60000},async t=>{
  const page=await productionEditor(t),before=await state(page);
  await page.locator('#open-ph-chart').click();await page.locator('#ph-reference-dialog[open] canvas').waitFor();
  const pending=page.waitForEvent('download');await page.locator('#ph-export-png').click();
  const download=await pending;assert.equal(await download.failure(),null);
  const stream=await download.createReadStream();assert.ok(stream);const chunks=[];
  try{for await(const chunk of stream)chunks.push(chunk);}finally{stream.destroy();}
  assert.deepEqual([...Buffer.concat(chunks).subarray(0,8)],[137,80,78,71,13,10,26,10]);
  const result=await page.evaluate(async()=> (await import('/modules/export-activation.mjs')).referenceExportDiagnostics());
  assert.equal(result.events.length,1);
  const row=result.events[0];
  assert.equal(row.format,'png');assert.equal(row.trusted,true);assert.equal(row.handler_active,true);
  assert.equal(row.dispatch_observed,true);assert.equal(typeof row.dispatch_active,'boolean');
  assert.deepEqual((await state(page)).document,before.document);
});

test('closing during held PNG completion does not dispatch a stale export or invent activation', {timeout:60000},async t=>{
  const page=await productionEditor(t),before=await state(page);let downloads=0;
  page.on('download',()=>downloads++);
  await page.locator('#open-ph-chart').click();await page.locator('#ph-reference-dialog[open] canvas').waitFor();
  // Fault injection at the encoding callback only; production UI initiates and cancels the operation.
  await page.evaluate(()=>{
    const original=HTMLCanvasElement.prototype.toBlob;
    const gate=window.__activationEncodingGate={original,held:false,release:null};
    HTMLCanvasElement.prototype.toBlob=function(callback,...args){
      return original.call(this,blob=>{gate.held=true;gate.release=()=>{gate.release=null;callback(blob);};},...args);
    };
  });
  try{
    await page.locator('#ph-export-png').click();
    await page.waitForFunction(()=>window.__activationEncodingGate.held);
    await page.locator('#close-ph-chart').click();
    await page.evaluate(async()=>{
      window.__activationEncodingGate.release();
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    });
    const result=await page.evaluate(async()=> (await import('/modules/export-activation.mjs')).referenceExportDiagnostics());
    assert.deepEqual(result.events,[{sequence:1,format:'png',trusted:true,handler_active:true,dispatch_active:null,dispatch_observed:false}]);
    assert.equal(downloads,0);assert.equal(await page.evaluate(()=>Object.keys(window.Chart.instances).length),0);
    assert.deepEqual((await state(page)).document,before.document);
  }finally{
    if(!page.isClosed())await page.evaluate(()=>{
      const gate=window.__activationEncodingGate;HTMLCanvasElement.prototype.toBlob=gate.original;
      gate.release?.();delete window.__activationEncodingGate;
    });
  }
});
