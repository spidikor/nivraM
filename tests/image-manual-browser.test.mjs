import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,ready,state,tabTo} from './editor-browser-fixture.mjs';

// The real protected import worker parses every supplied structure; production
// rendering, review controls and document history are exercised in the browser.
// Native PNG inspection, cropped-image receipts and preservation are fixtures.
// This fixture does not recognize an image or validate the native storage layer.
async function ordinaryPng(page){
  const fixture=await page.evaluate(async()=>{
    const token=document.querySelector('meta[name="nivram-token"]').content;
    const style=window.nivramDocument.getSnapshot().document.style;
    const call=async(route,body)=>{
      const response=await fetch('/api/'+route,{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':token},body:JSON.stringify(body)});
      const result=await response.json();if(!response.ok)throw new Error(result.error||'Fixture parser failed.');return result;
    };
    const preceding=await call('import',{source:'CC',style});
    const replacement=await call('import',{source:'[13CH3]C=O',style});
    const mol=await call('export-mol',{graph:replacement.graph});
    await window.nivramDocument.openProject({...window.nivramDocument.getSnapshot().document,graph:preceding.graph});
    const canvas=document.createElement('canvas');canvas.width=120;canvas.height=80;
    const context=canvas.getContext('2d');context.fillStyle='white';context.fillRect(0,0,120,80);
    context.strokeStyle='black';context.lineWidth=2;context.beginPath();context.moveTo(25,40);context.lineTo(80,40);context.stroke();
    context.fillStyle='red';context.font='22px sans-serif';context.fillText('O',84,48);
    const png=canvas.toDataURL('image/png');
    const digest=async bytes=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');
    const pngBytes=data=>Uint8Array.from(atob(data.split(',')[1]),c=>c.charCodeAt(0));
    const hash=await digest(pngBytes(png)),jobs=new Map();
    const inspection={schemaVersion:1,originalSha256:hash,image:{width:120,height:80},engine:{name:'RDKit',version:'native inspection fixture'},candidates:[],warnings:[]};
    const selected=['manual-1'];
    const calls=window.manualPngFixture={drafts:[],prepared:[],cancelled:[],completed:0,resets:0};
    window.nivramNative={resetDestination(){calls.resets++;}};
    window.pywebview={api:{
      async inspect_image(jobId){
        jobs.set(jobId,{});
        return {status:'inspected',jobId,name:'ordinary.png',reviewAvailable:true,regionPreviewAvailable:true,sourcePreview:png,
          opticalRecognition:{status:'missing-model',message:'The local recognition model is unavailable.'},inspection:structuredClone(inspection)};
      },
      async preview_image_draft(jobId,requestId,rect,source,requestedStyle,sessionToken){
        calls.drafts.push({jobId,requestId,rect,source,sessionToken});
        if(sessionToken!==token)throw new Error('Missing application session token.');
        try{
          const parsed=await call('import',{source,style:requestedStyle});
          const crop=document.createElement('canvas');crop.width=rect.width;crop.height=rect.height;
          crop.getContext('2d').drawImage(canvas,rect.x,rect.y,rect.width,rect.height,0,0,rect.width,rect.height);
          const cropPng=crop.toDataURL('image/png');
          const suppliedStructure={textSha256:await digest(new TextEncoder().encode(source)),coordinates:'from structure text; not traced from image'};
          const region={schemaVersion:1,jobId,requestId,sourceSha256:hash,sourceWidth:120,sourceHeight:80,mode:'crop',rect:{...rect},
            width:rect.width,height:rect.height,resampling:'none',pngBase64:cropPng.split(',')[1],pngSha256:await digest(pngBytes(cropPng)),navigationOnly:false,reviewToken:'fixture_manual_'+requestId};
          const result={...parsed,sourceSha256:hash,selectedComponents:selected,warnings:['Structure supplied by you; image correspondence has not been independently verified.']};
          // Retain even a deliberately late result so the real controller, not
          // this fixture, must stop a closed review from publishing it.
          jobs.set(jobId,{result,region,suppliedStructure});
          return {status:'drafted',jobId,requestId,selectedComponents:selected,region,
            inspection:{...structuredClone(inspection),candidates:[{id:'manual-1',source:'user-supplied-structure',status:'supported',label:'Your supplied structure',graph:parsed.graph,suppliedStructure}]}};
        }finally{calls.completed++;}
      },
      async preview_image_components(jobId,ids){
        if(JSON.stringify(ids)!==JSON.stringify(selected)||!jobs.get(jobId)?.result)throw new Error('No supplied draft is available.');
        return structuredClone(jobs.get(jobId).result);
      },
      async prepare_image_import(jobId,ids,_style,reviewToken){
        const cached=jobs.get(jobId);calls.prepared.push({jobId,ids,reviewToken});
        if(reviewToken!==cached?.region.reviewToken||JSON.stringify(ids)!==JSON.stringify(selected))throw new Error('The preserved crop is not the reviewed crop.');
        const {requestId,sourceSha256,sourceWidth,sourceHeight,rect,width,height,pngSha256}=cached.region;
        return {...structuredClone(cached.result),provenance:{kind:'png-user-supplied-structure',sourceSha256,selectedComponents:selected,sourceStorage:'synthetic fixture',
          suppliedStructure:cached.suppliedStructure,suppliedGraph:cached.result.graph,
          reviewedRegion:{kind:'crop',requestId,sourceSha256,sourceWidth,sourceHeight,rect,width,height,pngSha256,scale:'one-source-pixel-per-image-pixel',metadataCorrespondence:'user-supplied structure; image correspondence not independently verified'}}};
      },
      async cancel_image_region(){return {status:'cancelled'};},
      async cancel_image_import(jobId){calls.cancelled.push(jobId);return {status:'cancelled'};}
    }};
    window.dispatchEvent(new Event('pywebviewready'));window.dispatchEvent(new Event('nivram-native-ready'));
    return {hash,mol:mol.text,textHash:await digest(new TextEncoder().encode('CO'))};
  });
  return {...fixture,before:await state(page)};
}

const source='#image-import-manual-source',start='#image-import-manual-start';
const replacement='[data-correction="structure-source"]',replace='[data-correction="replace-source"]';
const atomCount=page=>page.locator('[data-correction="target"] option').evaluateAll(options=>options.filter(o=>o.value.startsWith('atom:')).length);
const settled=page=>page.waitForFunction(()=>!document.querySelector('[data-correction="target"]').disabled);
const mainView=page=>page.locator('#canvas').evaluate(canvas=>{
  const box=canvas.viewBox.baseVal;return {x:box.x,y:box.y,width:box.width,height:box.height};
});
function assertView(actual,expected,message){
  for(const key of ['x','y','width','height'])assert.ok(Math.abs(actual[key]-expected[key])<.0001,
    `${message}: ${key} expected ${expected[key]}, received ${actual[key]}`);
}
async function manualMainView(page,{zoom='in',dx=61,dy=-37}={}){
  const previous=await mainView(page),box=await page.locator('#canvas').boundingBox(),x=box.x+box.width*.5,y=box.y+box.height*.5;
  // The import notification can overlap the lower zoom buttons. A real wheel
  // gesture on the unobstructed drawing exercises the same public zoom action.
  await page.mouse.move(x,y);await page.mouse.wheel(0,zoom==='in'?-180:180);
  await page.waitForFunction(width=>document.querySelector('#canvas').viewBox.baseVal.width!==width,previous.width);
  await page.mouse.move(x,y);await page.keyboard.down('Alt');
  try{await page.mouse.down();await page.mouse.move(x+dx,y+dy,{steps:5});await page.mouse.up();}
  finally{await page.keyboard.up('Alt');}
  return mainView(page);
}

test('ordinary PNG without a recognition model accepts supplied SMILES and MOL only after correction review',{timeout:90000},async t=>{
  const page=await productionEditor(t),fixture=await ordinaryPng(page);
  const precedingFit=await mainView(page),precedingView=await manualMainView(page);
  assert.ok(precedingView.width<precedingFit.width,'The original drawing must have a real manual zoom.');
  assert.notEqual(precedingView.x,precedingFit.x,'The original drawing must have a real manual pan.');
  assert.deepEqual(await state(page),fixture.before);
  await page.click('#image-import-button');await page.waitForSelector(source,{state:'visible'});
  assert.equal(await page.locator('#image-import-components input').count(),0);
  assert.equal(await page.isDisabled('#image-import-recognize'),true);
  assert.equal(await page.isDisabled(start),true);assert.equal(await page.isDisabled('#image-import-accept'),true);
  await page.fill(source,'[CH5]');await page.click(start);
  await page.waitForFunction(()=>!document.querySelector('#image-import-manual-source').disabled);
  assert.equal(await page.inputValue(source),'[CH5]');
  assert.equal(await page.locator('#image-import-status').evaluate(el=>el.classList.contains('image-import-error')),true);
  assert.equal(await page.isDisabled('#image-import-acknowledge'),true);
  assert.deepEqual(await state(page),fixture.before);

  await page.fill(source,'CO');await page.click(start);await settled(page);
  assert.equal(await atomCount(page),2);assert.equal(await page.inputValue(source),'');
  assert.equal(await page.isVisible('#image-import-manual'),false);
  assert.match(await page.textContent('#image-import-engine'),/supplied by you; not recognized from the image/);
  assert.equal(await page.isChecked('#image-import-acknowledge'),false);
  assert.equal(await page.isDisabled('#image-import-accept'),true);
  assert.deepEqual(await state(page),fixture.before);

  if(process.env.NIVRAM_MANUAL_SCREENSHOT){
    await page.locator('#image-import-source-title').scrollIntoViewIfNeeded();
    await page.screenshot({path:process.env.NIVRAM_MANUAL_SCREENSHOT});
  }
  await page.click('#image-import-correction summary');
  await page.check('#image-import-acknowledge');
  await page.fill(replacement,fixture.mol);await page.click(replace);await settled(page);
  assert.equal(await atomCount(page),3);assert.equal(await page.inputValue(replacement),'');
  assert.equal(await page.isChecked('#image-import-acknowledge'),false);
  await page.click('[data-correction="undo"]');await settled(page);assert.equal(await atomCount(page),2);
  await page.click('[data-correction="redo"]');await settled(page);assert.equal(await atomCount(page),3);
  assert.deepEqual(await state(page),fixture.before);
  await page.check('#image-import-acknowledge');await page.click('#image-import-accept');
  await page.waitForFunction(()=>!document.querySelector('#image-import-dialog').open);
  const after=await state(page),provenance=after.document.analysis.imageImport;
  assert.ok(after.revision>fixture.before.revision);assert.equal(after.dirty,true);
  assert.equal(after.document.graph.atoms.length,3);
  assert.equal(after.document.graph.atoms.filter(atom=>atom.isotope===13).length,1);
  assert.equal(provenance.kind,'png-user-supplied-structure');assert.equal(provenance.sourceSha256,fixture.hash);
  assert.equal(provenance.recognition,undefined);
  assert.deepEqual(provenance.suppliedStructure,{textSha256:fixture.textHash,coordinates:'from structure text; not traced from image'});
  assert.equal(provenance.suppliedGraph.atoms.length,2);
  assert.deepEqual(provenance.reviewedRegion.rect,{x:0,y:0,width:120,height:80});
  assert.equal(provenance.reviewedRegion.metadataCorrespondence,'user-supplied structure; image correspondence not independently verified');
  assert.deepEqual(provenance.correction.baselineGraph,provenance.suppliedGraph);
  assert.deepEqual(provenance.correction.reviewedGraph,after.document.graph);
  assert.deepEqual(after.document.style,fixture.before.document.style);
  const calls=await page.evaluate(()=>window.manualPngFixture);
  assert.equal(calls.prepared.length,1);assert.equal(calls.resets,1);
  const initialImportedView=await manualMainView(page,{dx:-44,dy:29});
  // The public project-open seam must not Fit or add a molecular history entry
  // when the requested document is already the current imported document.
  assert.equal(await page.evaluate(document=>window.nivramDocument.openProject(document,{rememberView:true}),after.document),true);
  assertView(await mainView(page),initialImportedView,'Identical project open preserves the manual imported view');
  assert.deepEqual(await state(page),after);
  // An ordinary appearance edit is also real document history, but crossing
  // that entry must not rewind navigation performed since the edit.
  await page.fill('#bond-width','3');await page.click('#apply-style');await ready(page);
  assert.equal((await state(page)).document.style.bondWidth,3);
  const importedView=await manualMainView(page,{zoom:'out',dx:27,dy:-19});
  await page.click('#undo-button');await ready(page);
  assert.deepEqual((await state(page)).document,after.document);
  assertView(await mainView(page),importedView,'Undo ordinary edit retains the current imported view');
  await page.click('#undo-button');await ready(page);
  assert.deepEqual((await state(page)).document,fixture.before.document);
  assertView(await mainView(page),precedingView,'Undo PNG import restores the preceding manual view');
  // Restored manual mode must not silently turn back into automatic Fit.
  await page.setViewportSize({width:1426,height:768});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assertView(await mainView(page),precedingView,'Restored manual view survives viewport resize without Fit');
  await page.setViewportSize({width:1366,height:768});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await page.click('#redo-button');await ready(page);
  assert.deepEqual((await state(page)).document,after.document);
  assertView(await mainView(page),importedView,'Redo PNG import restores its latest manual view');
  await page.click('#undo-button');await ready(page);
  const revisedOriginalView=await manualMainView(page,{zoom:'out',dx:-23,dy:17});
  await page.click('#redo-button');await ready(page);
  assert.deepEqual((await state(page)).document,after.document);
  assertView(await mainView(page),importedView,'Repeated Redo retains the latest imported view');
  await page.click('#undo-button');await ready(page);
  assert.deepEqual((await state(page)).document,fixture.before.document);
  assertView(await mainView(page),revisedOriginalView,'Repeated Undo captures the latest outgoing original view');
});

test('closing an ordinary PNG review rejects a late supplied draft and a new review can start from MOL',{timeout:90000},async t=>{
  const page=await productionEditor(t),fixture=await ordinaryPng(page);
  let arrivedResolve,releaseResolve;
  const arrived=new Promise(resolve=>{arrivedResolve=resolve;}),release=new Promise(resolve=>{releaseResolve=resolve;});
  await page.route('**/api/import',async route=>{
    if(route.request().postDataJSON().source!=='CCCC')return route.continue();
    const response=await route.fetch();arrivedResolve();await release;
    try{await route.fulfill({response});}catch{ /* The page can close after cancellation. */ }
  });
  t.after(()=>releaseResolve());
  await page.click('#image-import-button');await page.waitForSelector(source,{state:'visible'});
  await page.fill(source,'CCCC');await page.click(start);await arrived;
  assert.equal(await page.isDisabled(source),true);assert.deepEqual(await state(page),fixture.before);
  await page.click('#image-import-cancel');assert.equal(await page.inputValue(source),'');
  await page.click('#image-import-button');await page.waitForSelector(source,{state:'visible'});
  releaseResolve();await page.waitForFunction(()=>window.manualPngFixture.completed===1);
  assert.equal(await page.inputValue(source),'');assert.equal(await page.locator('#image-import-components input').count(),0);
  assert.equal(await atomCount(page),0);assert.equal(await page.isDisabled('#image-import-accept'),true);
  assert.deepEqual(await state(page),fixture.before);
  await page.fill(source,fixture.mol);await page.click(start);await settled(page);
  assert.equal(await atomCount(page),3);assert.equal(await page.inputValue(source),'');
  assert.equal(await page.isDisabled('#image-import-accept'),true);
  assert.deepEqual(await state(page),fixture.before);
  await page.click('#image-import-cancel');assert.equal(await page.inputValue(source),'');
});

test('Undo PNG import restores automatic Fit in the current viewport and on subsequent resize',{timeout:90000},async t=>{
  const page=await productionEditor(t),fixture=await ordinaryPng(page),originalFit=await mainView(page);
  await page.click('#image-import-button');await page.waitForSelector(source,{state:'visible'});
  await page.fill(source,'CO');await page.click(start);await settled(page);
  await page.check('#image-import-acknowledge');await page.click('#image-import-accept');
  await page.waitForFunction(()=>!document.querySelector('#image-import-dialog').open);
  await manualMainView(page,{dx:48,dy:-26});
  await page.setViewportSize({width:1486,height:828});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await page.click('#undo-button');await ready(page);
  assert.deepEqual((await state(page)).document,fixture.before.document);
  const restoredFit=await mainView(page);
  assert.notEqual(restoredFit.width,originalFit.width,'Undo must Fit the original in the resized viewport.');
  // Resize before pressing Fit: otherwise the button itself could conceal a
  // failure to restore automatic mode at the PNG history boundary.
  await page.setViewportSize({width:1546,height:878});
  await page.waitForFunction(width=>document.querySelector('#canvas').viewBox.baseVal.width!==width,restoredFit.width);
  const resizedFit=await mainView(page);
  await tabTo(page,'fit-button');await page.keyboard.press('Enter');
  assertView(await mainView(page),resizedFit,'Restored automatic framing matches explicit Fit');
  assert.deepEqual((await state(page)).document,fixture.before.document);
});
