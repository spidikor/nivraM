import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,state} from './editor-browser-fixture.mjs';

async function fixture(t){
  const page=await productionEditor(t),before=await state(page);let downloads=0;
  page.on('download',()=>downloads++);
  // Only the native boundary is substituted. Production chart, encoding,
  // click handlers and result feedback remain real; actual disk I/O is separate.
  await page.evaluate(()=>{
    window.__referenceSaves=[];
    window.pywebview={api:{async save_reference_export(kind,payload,token){
      const bytes=kind==='png'?Uint8Array.from(atob(payload),value=>value.charCodeAt(0)):new TextEncoder().encode(payload);
      const digest=await crypto.subtle.digest('SHA-256',bytes);
      window.__referenceSaves.push({kind,payload,tokenMatches:token===document.querySelector('meta[name="nivram-token"]').content});
      return {status:'saved',bytes:bytes.length,sha256:[...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('')};
    }}};
  });
  await page.locator('#open-ph-chart').click();await page.locator('#ph-reference-dialog[open] canvas').waitFor();
  return {page,before,downloads:()=>downloads};
}

test('native reference UI saves exact JSON, CSV and PNG through the scoped bridge without downloads',{timeout:60000},async t=>{
  const {page,before,downloads}=await fixture(t);
  for(const kind of ['json','csv','png']){
    await page.locator('#ph-export-'+kind).click();
    await page.waitForFunction(kind=>document.getElementById('ph-export-status').textContent.includes(`Reference ${kind.toUpperCase()} saved and verified`),kind);
  }
  const calls=await page.evaluate(()=>window.__referenceSaves);
  assert.deepEqual(calls.map(row=>row.kind),['json','csv','png']);assert.ok(calls.every(row=>row.tokenMatches));
  const exported=JSON.parse(calls[0].payload);assert.equal(exported.exportKind,'ph-reference-context');assert.equal(exported.populationResults,null);
  assert.match(calls[1].payload,/^"path","type","value","spreadsheet_escaped"\r\n/);
  assert.ok(calls[1].payload.includes('ph-reference-context'));
  const png=Buffer.from(calls[2].payload,'base64');
  assert.deepEqual([...png.subarray(0,8)],[137,80,78,71,13,10,26,10]);
  assert.equal(png.readUInt32BE(16),1000);assert.equal(png.readUInt32BE(20),560);
  assert.equal(downloads(),0);assert.deepEqual((await state(page)).document,before.document);
});

test('native reference cancellation and unconfirmed writes keep accurate feedback without a browser fallback',{timeout:60000},async t=>{
  const {page,before,downloads}=await fixture(t);
  await page.evaluate(()=>{window.__referenceOutcomes=['cancelled','unconfirmed'];window.pywebview.api.save_reference_export=async()=>({status:window.__referenceOutcomes.shift(),message:'PRIVATE NATIVE ERROR'});});
  for(const word of ['cancelled','confirmed']){
    await page.locator('#ph-export-json').click();
    await page.waitForFunction(word=>document.getElementById('ph-export-status').textContent.toLowerCase().includes(word),word);
    const message=await page.locator('#ph-export-status').innerText();
    assert.doesNotMatch(message,/PRIVATE|saved and verified/);
  }
  assert.equal(downloads(),0);assert.deepEqual((await state(page)).document,before.document);
});

test('native reference save completion cannot update a reopened chart or dispatch another save',{timeout:60000},async t=>{
  const {page,before,downloads}=await fixture(t);
  for(const outcome of ['saved','unconfirmed']){
    const initialCalls=await page.evaluate(()=>window.__referenceSaves.length);
    // Hold only the native response. The actual production export preparation,
    // dialog close/reopen controls, and post-response ownership checks remain real.
    await page.evaluate(outcome=>{
      const original=window.pywebview.api.save_reference_export;
      let release;const held=new Promise(resolve=>{release=resolve;});
      const gate=window.__referenceSaveGate={original,release,entered:false};
      window.pywebview.api.save_reference_export=async(...args)=>{
        const receipt=await original(...args);
        gate.entered=true;await held;
        return outcome==='saved'?receipt:{status:'unconfirmed'};
      };
    },outcome);
    try{
      await page.locator('#ph-export-json').click();
      await page.waitForFunction(()=>window.__referenceSaveGate.entered);
      assert.equal(await page.evaluate(()=>window.__referenceSaves.length),initialCalls+1);
      await page.locator('#close-ph-chart').click();
      await page.locator('#open-ph-chart').click();
      await page.locator('#ph-reference-dialog[open] canvas').waitFor();
      const freshMessage=await page.locator('#ph-export-status').innerText();
      const freshControls=await page.locator('#ph-export-json, #ph-export-csv, #ph-export-png').evaluateAll(nodes=>nodes.map(node=>node.disabled));
      assert.deepEqual(freshControls,[false,false,false]);
      assert.doesNotMatch(freshMessage,/saved and verified|could not be confirmed/);
      assert.equal(await page.evaluate(()=>window.__referenceSaves.length),initialCalls+1);
      await page.evaluate(async()=>{
        window.__referenceSaveGate.release();
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      });
      assert.equal(await page.locator('#ph-export-status').innerText(),freshMessage,`${outcome} response must not replace the reopened chart's feedback.`);
      assert.deepEqual(await page.locator('#ph-export-json, #ph-export-csv, #ph-export-png').evaluateAll(nodes=>nodes.map(node=>node.disabled)),freshControls);
      assert.equal(await page.evaluate(()=>window.__referenceSaves.length),initialCalls+1,`${outcome} response must not dispatch another native save.`);
      assert.equal(downloads(),0);assert.deepEqual((await state(page)).document,before.document);
    }finally{
      if(!page.isClosed())await page.evaluate(()=>{
        const gate=window.__referenceSaveGate;
        gate.release?.();window.pywebview.api.save_reference_export=gate.original;
        delete window.__referenceSaveGate;
      });
    }
  }
  await page.locator('#close-ph-chart').click();
});
