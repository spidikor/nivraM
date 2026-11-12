import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,ready,state} from './editor-browser-fixture.mjs';

// Real editor and local chemistry server; only the native PNG inspection/source
// storage boundary is synthetic. This is not a native clipboard or installer test.
test('PNG draft corrections use real chemistry validation and import as one undoable change', {timeout:90000}, async t=>{
  const page=await productionEditor(t);
  const source=await page.evaluate(async()=>{
    const token=document.querySelector('meta[name="nivram-token"]').content;
    const chemistry=async source=>{
      const response=await fetch('/api/import',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':token},body:JSON.stringify({source,style:window.nivramDocument.getSnapshot().document.style})});
      if(!response.ok)throw new Error(await response.text());return response.json();
    };
    const preceding=await chemistry('CC'),candidate=await chemistry('CO');
    await window.nivramDocument.openProject({...window.nivramDocument.getSnapshot().document,graph:preceding.graph});
    const canvas=document.createElement('canvas');canvas.width=120;canvas.height=80;
    const context=canvas.getContext('2d');context.fillStyle='white';context.fillRect(0,0,120,80);context.strokeStyle='black';context.lineWidth=2;
    context.beginPath();context.moveTo(25,40);context.lineTo(80,40);context.stroke();context.fillStyle='red';context.font='22px sans-serif';context.fillText('O',84,48);
    const png=canvas.toDataURL('image/png'),bytes=Uint8Array.from(atob(png.split(',')[1]),c=>c.charCodeAt(0));
    const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');
    const result={...candidate,sourceSha256:hash,selectedComponents:['molecule'],warnings:['Review charges and stereochemistry against the PNG.']};
    window.nivramNative={resetDestination(){}};
    window.pywebview={api:{
      inspect_image:async id=>({status:'inspected',jobId:id,name:'methanol-fixture.png',reviewAvailable:true,sourcePreview:png,inspection:{schemaVersion:1,originalSha256:hash,image:{width:120,height:80},engine:{name:'RDKit',version:'real local render'},candidates:[{id:'molecule',status:'supported',label:'Methanol',graph:candidate.graph}],warnings:[]}}),
      preview_image_components:async()=>result,
      prepare_image_import:async()=>({...result,provenance:{kind:'png-metadata-component-reconstruction',sourceSha256:hash,selectedComponents:['molecule'],sourceStorage:'synthetic fixture'}}),
      cancel_image_import:async()=>({status:'cancelled'})
    }};
    window.dispatchEvent(new Event('pywebviewready'));window.dispatchEvent(new Event('nivram-native-ready'));
    return {hash,oxygen:candidate.graph.atoms.find(a=>a.symbol==='O').id};
  });
  const before=await state(page);
  await page.click('#image-import-button');await page.check('#image-import-components input');await page.click('#image-import-preview');
  await page.waitForFunction(()=>!document.querySelector('[data-correction="target"]').disabled);
  await page.click('#image-import-correction summary');
  const oxygenOption=await page.locator('[data-correction="target"] option').evaluateAll((options,id)=>options.find(o=>o.value.includes(id))?.value,source.oxygen);
  assert.ok(oxygenOption);await page.selectOption('[data-correction="target"]',oxygenOption);
  await page.fill('[data-correction="element"]','N');await page.fill('[data-correction="charge"]','1');
  await page.click('[data-correction="apply-atom"]');
  await page.waitForFunction(()=>document.querySelector('#image-import-status').textContent.startsWith('Draft updated.'));
  assert.deepEqual(await state(page),before);
  // Exercise the pointer bond tool through the same production render path.
  await page.selectOption('[data-correction="tool"]','bond');await page.selectOption('[data-correction="bond-kind"]','2');
  await page.locator('#image-import-candidate').scrollIntoViewIfNeeded();
  const bondPoint=await page.locator('#image-import-candidate path.bond-0').first().evaluate(path=>{
    const p=path.getPointAtLength(path.getTotalLength()/2),point=new DOMPoint(p.x,p.y).matrixTransform(path.getScreenCTM());return {x:point.x,y:point.y};
  });
  await page.mouse.click(bondPoint.x,bondPoint.y);
  await page.waitForFunction(()=>!document.querySelector('[data-correction="undo"]').disabled);
  const bondOption=await page.locator('[data-correction="target"] option').evaluateAll(options=>options.find(o=>o.value.startsWith('bond:')).value);
  await page.selectOption('[data-correction="target"]',bondOption);assert.equal(await page.inputValue('[data-correction="bond-kind"]'),'2');
  await page.click('[data-correction="undo"]');
  await page.waitForFunction(()=>!document.querySelector('[data-correction="redo"]').disabled);
  assert.equal(await page.inputValue('[data-correction="bond-kind"]'),'1');
  await page.selectOption('[data-correction="target"]',oxygenOption);
  if(process.env.NIVRAM_FEATURE_SCREENSHOT){
    await page.click('#image-import-correction summary');
    await page.locator('#image-import-dialog').evaluate(dialog=>{dialog.scrollTop=0;});
    await page.screenshot({path:process.env.NIVRAM_FEATURE_SCREENSHOT});
    await page.click('#image-import-correction summary');
  }
  await page.check('#image-import-acknowledge');
  await page.fill('[data-correction="explicit-hs"]','8');await page.click('[data-correction="apply-atom"]');
  await page.waitForFunction(()=>document.querySelector('#image-import-status').classList.contains('image-import-error'));
  assert.match(await page.textContent('#image-import-status'),/valence|invalid|hydrogen/i);
  assert.equal(await page.isChecked('#image-import-acknowledge'),false);assert.deepEqual(await state(page),before);
  await page.check('#image-import-acknowledge');await page.click('#image-import-accept');
  await page.waitForFunction(()=>!document.querySelector('#image-import-dialog').open);
  const accepted=await state(page),nitrogen=accepted.document.graph.atoms.find(a=>a.id===source.oxygen);
  assert.equal(nitrogen.symbol,'N');assert.equal(nitrogen.charge,1);assert.notEqual(nitrogen.explicitHs,8);
  assert.equal(accepted.document.analysis.imageImport.sourceSha256,source.hash);
  assert.equal(accepted.document.analysis.imageImport.correction.baselineGraph.atoms.find(a=>a.id===source.oxygen).symbol,'O');
  await page.click('#undo-button');await ready(page);assert.deepEqual((await state(page)).document,before.document);
});
