import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,ready,state} from './editor-browser-fixture.mjs';

// Real parser, protected import worker, renderer, correction UI, history and
// editor. Only native image inspection/storage is supplied by this fixture.
async function startReview(page){
  const fixture=await page.evaluate(async()=>{
    const token=document.querySelector('meta[name="nivram-token"]').content;
    const style=window.nivramDocument.getSnapshot().document.style;
    const call=async(route,body)=>{
      const r=await fetch('/api/'+route,{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':token},body:JSON.stringify(body)});
      if(!r.ok)throw new Error(await r.text());return r.json();
    };
    const preceding=await call('import',{source:'CC',style});
    const candidate=await call('import',{source:'CO',style});
    const replacement=await call('import',{source:'[13CH3]C=O',style});
    const mol=await call('export-mol',{graph:replacement.graph});
    await window.nivramDocument.openProject({...window.nivramDocument.getSnapshot().document,graph:preceding.graph});
    const canvas=document.createElement('canvas');canvas.width=120;canvas.height=80;
    const ctx=canvas.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,120,80);
    ctx.strokeStyle='black';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(25,40);ctx.lineTo(80,40);ctx.stroke();
    ctx.fillStyle='red';ctx.font='22px sans-serif';ctx.fillText('O',84,48);
    const png=canvas.toDataURL('image/png');
    const bytes=Uint8Array.from(atob(png.split(',')[1]),c=>c.charCodeAt(0));
    const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');
    const result={...candidate,sourceSha256:hash,selectedComponents:['molecule'],warnings:['Compare the entire draft to the original image.']};
    window.nivramNative={resetDestination(){}};
    window.pywebview={api:{
      inspect_image:async id=>({status:'inspected',jobId:id,name:'methanol.png',reviewAvailable:true,sourcePreview:png,
        inspection:{schemaVersion:1,originalSha256:hash,image:{width:120,height:80},engine:{name:'RDKit',version:'real chemistry'},
          candidates:[{id:'molecule',status:'supported',label:'Methanol',graph:candidate.graph}],warnings:[]}}),
      preview_image_components:async()=>result,
      prepare_image_import:async()=>({...result,provenance:{kind:'png-metadata-component-reconstruction',sourceSha256:hash,selectedComponents:['molecule'],sourceStorage:'synthetic fixture'}}),
      cancel_image_import:async()=>({status:'cancelled'})
    }};
    window.dispatchEvent(new Event('pywebviewready'));window.dispatchEvent(new Event('nivram-native-ready'));
    return {hash,mol:mol.text};
  });
  const before=await state(page);
  await page.click('#image-import-button');await page.check('#image-import-components input');await page.click('#image-import-preview');
  await page.waitForFunction(()=>!document.querySelector('[data-correction="target"]').disabled);
  await page.click('#image-import-correction summary');
  return {...fixture,before};
}
const source='[data-correction="structure-source"]';
const replace='[data-correction="replace-source"]';
const atomCount=page=>page.locator('[data-correction="target"] option').evaluateAll(options=>options.filter(o=>o.value.startsWith('atom:')).length);
const settled=page=>page.waitForFunction(()=>!document.querySelector('[data-correction="structure-source"]').disabled);

test('pasted SMILES and 2D MOL replace only the PNG draft with draft history and one editor Undo',{timeout:90000},async t=>{
  const page=await productionEditor(t),fixture=await startReview(page);
  assert.equal(await page.isDisabled(replace),true);
  await page.check('#image-import-acknowledge');
  await page.fill(source,'C[C@H](N)C(=O)O');await page.click(replace);await settled(page);
  assert.equal(await atomCount(page),6);
  assert.equal(await page.inputValue(source),'');
  assert.equal(await page.isChecked('#image-import-acknowledge'),false);
  assert.deepEqual(await state(page),fixture.before);
  await page.click('[data-correction="undo"]');await settled(page);
  assert.equal(await atomCount(page),2);assert.equal(await page.isDisabled('[data-correction="undo"]'),true);
  await page.click('[data-correction="redo"]');await settled(page);assert.equal(await atomCount(page),6);
  await page.fill(source,fixture.mol);await page.click(replace);await settled(page);
  assert.equal(await atomCount(page),3);
  if(process.env.NIVRAM_FEATURE_SCREENSHOT){
    await page.click('#image-import-correction summary');await page.locator(source).scrollIntoViewIfNeeded();
    await page.screenshot({path:process.env.NIVRAM_FEATURE_SCREENSHOT});
    await page.click('#image-import-correction summary');
  }
  await page.check('#image-import-acknowledge');
  await page.fill(source,'[CH5]');await page.click(replace);await settled(page);
  assert.equal(await atomCount(page),3);assert.equal(await page.inputValue(source),'[CH5]');
  assert.equal(await page.isChecked('#image-import-acknowledge'),false);
  assert.equal(await page.locator('#image-import-status').evaluate(el=>el.classList.contains('image-import-error')),true);
  assert.deepEqual(await state(page),fixture.before);
  await page.check('#image-import-acknowledge');await page.click('#image-import-accept');
  await page.waitForFunction(()=>!document.querySelector('#image-import-dialog').open);
  const after=await state(page),provenance=after.document.analysis.imageImport;
  // Import records one drawing change; marking its destination unsaved also
  // advances the revision fence without adding another Undo history entry.
  assert.ok(after.revision>fixture.before.revision);assert.equal(after.dirty,true);
  assert.equal(after.document.graph.atoms.length,3);
  assert.equal(after.document.graph.atoms.filter(a=>a.isotope===13).length,1);
  assert.equal(provenance.sourceSha256,fixture.hash);
  assert.equal(provenance.correction.kind,'user-edited-graph');
  assert.equal(provenance.correction.baselineGraph.atoms.length,2);
  assert.deepEqual(provenance.correction.reviewedGraph,after.document.graph);
  assert.deepEqual(after.document.style,fixture.before.document.style);
  await page.click('#undo-button');await ready(page);
  assert.deepEqual((await state(page)).document,fixture.before.document);
});

test('PNG acceptance storage failure retains the corrected draft, pasted text and history until a fresh acknowledgement',{timeout:90000},async t=>{
  const page=await productionEditor(t),fixture=await startReview(page);
  await page.fill(source,'CCCC');await page.click(replace);await settled(page);
  await page.fill(source,'[CH5]');await page.click(replace);await settled(page);
  assert.equal(await atomCount(page),4);assert.equal(await page.inputValue(source),'[CH5]');
  await page.evaluate(()=>{
    const prepare=window.pywebview.api.prepare_image_import;let fail=true;
    window.pywebview.api.prepare_image_import=(...args)=>{
      if(fail){fail=false;throw new Error('The local source-image store is full. Existing source images were retained.');}
      return prepare(...args);
    };
  });
  await page.check('#image-import-acknowledge');await page.click('#image-import-accept');
  await page.waitForFunction(()=>document.querySelector('#image-import-status').textContent.includes('store is full'));
  assert.equal(await atomCount(page),4);assert.equal(await page.inputValue(source),'[CH5]');
  assert.equal(await page.isVisible('#image-import-correction .image-correction-editor'),true);
  assert.equal(await page.isChecked('#image-import-acknowledge'),false);
  assert.equal(await page.isDisabled('#image-import-accept'),true);
  assert.equal(await page.isDisabled('[data-correction="undo"]'),false);
  assert.deepEqual(await state(page),fixture.before);
  if(process.env.NIVRAM_PNG_RETRY_SCREENSHOT){
    const viewport=page.viewportSize();
    await page.setViewportSize({width:1366,height:1800});
    await page.locator('#image-import-name').scrollIntoViewIfNeeded();
    await page.screenshot({path:process.env.NIVRAM_PNG_RETRY_SCREENSHOT});
    await page.setViewportSize(viewport);
  }
  await page.click('[data-correction="undo"]');await settled(page);assert.equal(await atomCount(page),2);
  await page.click('[data-correction="redo"]');await settled(page);assert.equal(await atomCount(page),4);
  await page.check('#image-import-acknowledge');await page.click('#image-import-accept');
  await page.waitForFunction(()=>!document.querySelector('#image-import-dialog').open);
  assert.equal((await state(page)).document.graph.atoms.length,4);
  await page.click('#undo-button');await ready(page);assert.deepEqual((await state(page)).document,fixture.before.document);
});

test('closing PNG review while pasted structure preparation is pending discards the late result',{timeout:90000},async t=>{
  const page=await productionEditor(t),fixture=await startReview(page);
  let arrivedResolve,releaseResolve;
  const arrived=new Promise(resolve=>{arrivedResolve=resolve;}),release=new Promise(resolve=>{releaseResolve=resolve;});
  await page.route('**/api/import',async route=>{
    if(route.request().postDataJSON().source!=='CCCC')return route.continue();
    const response=await route.fetch();arrivedResolve();await release;
    try{await route.fulfill({response});}catch{ /* Dialog cancellation aborts the waiting fetch. */ }
  });
  t.after(()=>releaseResolve());
  await page.fill(source,'CCCC');await page.click(replace);await arrived;
  assert.equal(await page.isDisabled(source),true);assert.deepEqual(await state(page),fixture.before);
  await page.click('#image-import-cancel');
  releaseResolve();
  await page.click('#image-import-button');await page.check('#image-import-components input');await page.click('#image-import-preview');
  await page.waitForFunction(()=>!document.querySelector('[data-correction="target"]').disabled);
  assert.equal(await atomCount(page),2);assert.equal(await page.inputValue(source),'');
  assert.deepEqual(await state(page),fixture.before);
  await page.click('#image-import-cancel');
});
