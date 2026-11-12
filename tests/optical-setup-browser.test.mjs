import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,state} from './editor-browser-fixture.mjs';

// Real editor, parser, correction UI and dialogs. Native choosers, model
// verification and image storage are synthetic; this does not run inference.
test('local recognition setup saves or cancels without rebuilding an acknowledged corrected PNG draft',{timeout:90000},async t=>{
  const page=await productionEditor(t);
  await page.evaluate(async()=>{
    const token=document.querySelector('meta[name="nivram-token"]').content,style=window.nivramDocument.getSnapshot().document.style;
    const response=await fetch('/api/import',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':token},body:JSON.stringify({source:'CO',style})});
    if(!response.ok)throw new Error('Fixture chemistry could not prepare its candidate.');
    const candidate=await response.json(),canvas=document.createElement('canvas');canvas.width=120;canvas.height=80;
    const ctx=canvas.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,120,80);ctx.fillStyle='black';ctx.fillText('C—O',25,40);
    const png=canvas.toDataURL('image/png'),bytes=Uint8Array.from(atob(png.split(',')[1]),c=>c.charCodeAt(0));
    const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');
    const modelSha256='32821fa4ba8d17f9a92e374601438c5e757947dbf1141e9ad0ef51cccfa1c501';
    const f=window.opticalFixture={configured:false,inspections:0,recognitions:0,cancellations:0,saves:0,failSave:false,sequence:0};let selection;
    const available=()=>f.configured?{status:'configured',message:'Runtime checked on recognition.',modelSha256}:{status:'not-configured',message:'Local recognition has not been set up.'};
    const selectionResult=status=>({status,...selection,opticalRecognition:available()});
    const check=(id,providedToken)=>{if(id!==selection.setupId||providedToken!==token)throw new Error('Invalid setup request.');};
    window.nivramNative={resetDestination(){}};
    window.pywebview={api:{
      inspect_image:async id=>{f.inspections++;return {status:'inspected',jobId:id,name:'review.png',reviewAvailable:true,regionPreviewAvailable:true,sourcePreview:png,opticalRecognition:available(),inspection:{schemaVersion:1,originalSha256:hash,image:{width:120,height:80},engine:{name:'RDKit',version:'fixture'},candidates:[{id:'molecule',status:'supported',label:'Methanol',graph:candidate.graph}],warnings:[]}};},
      preview_image_components:async()=>({...candidate,sourceSha256:hash,selectedComponents:['molecule'],warnings:['Compare the draft against the original image.']}),
      cancel_image_import:async()=>{f.cancellations++;return {status:'cancelled'};},
      recognize_image:async()=>{f.recognitions++;throw new Error('Recognition must remain explicit.');},
      begin_optical_setup:async providedToken=>{if(providedToken!==token)throw new Error('Invalid session.');selection={setupId:`setup_fixture_${++f.sequence}`,runtimeName:f.configured?'python.exe':'',modelName:f.configured?'model.pth':''};return selectionResult('editing');},
      choose_optical_setup_file:async(id,kind,providedToken)=>{check(id,providedToken);if(!['python','model'].includes(kind))throw new Error('Invalid chooser.');selection[kind==='python'?'runtimeName':'modelName']=kind==='python'?'python.exe':'model.pth';return selectionResult('selected');},
      save_optical_setup:async(id,providedToken)=>{check(id,providedToken);f.saves++;if(f.failSave)throw new Error('The selected model failed its integrity check.');f.configured=true;return {status:'saved',setupId:id,opticalRecognition:available(),modelVerified:true,runtimeCompatibility:'checked-on-recognition'};},
      cancel_optical_setup:async(id,providedToken)=>{check(id,providedToken);return {status:'cancelled'};}
    }};
    window.dispatchEvent(new Event('pywebviewready'));window.dispatchEvent(new Event('nivram-native-ready'));
  });
  const before=await state(page);
  await page.click('#optical-setup-button');await page.waitForFunction(()=>!document.querySelector('#optical-setup-runtime-choose').disabled);
  await page.click('#optical-setup-cancel');assert.deepEqual(await state(page),before);
  await page.click('#image-import-button');await page.check('#image-import-components input');await page.click('#image-import-preview');
  await page.waitForFunction(()=>!document.querySelector('[data-correction="target"]').disabled);
  await page.click('#image-import-correction summary');await page.fill('[data-correction="structure-source"]','CCN');await page.click('[data-correction="replace-source"]');
  await page.waitForFunction(()=>!document.querySelector('[data-correction="structure-source"]').disabled);
  await page.check('#image-import-acknowledge');
  const reviewState=()=>page.evaluate(()=>({
    source:document.getElementById('image-import-source').src,candidate:document.getElementById('image-import-candidate').innerHTML,
    crop:['x','y','width','height'].map(key=>document.getElementById('image-import-crop-'+key).value),
    selected:[...document.querySelectorAll('#image-import-components input:checked')].map(input=>input.value),
    acknowledged:document.getElementById('image-import-acknowledge').checked,
    canUndo:!document.querySelector('[data-correction="undo"]').disabled,canRedo:!document.querySelector('[data-correction="redo"]').disabled,
    canAccept:!document.getElementById('image-import-accept').disabled
  }));
  const draft=await reviewState();assert.equal(draft.acknowledged,true);assert.equal(draft.canUndo,true);
  assert.equal(await page.isDisabled('#image-import-recognize'),true);
  const chooseFiles=async()=>{await page.click('#optical-setup-runtime-choose');await page.waitForFunction(()=>!document.querySelector('#optical-setup-model-choose').disabled);await page.click('#optical-setup-model-choose');await page.waitForFunction(()=>!document.querySelector('#optical-setup-save').disabled);};
  await page.click('#image-import-setup');await page.waitForFunction(()=>!document.querySelector('#optical-setup-runtime-choose').disabled);await chooseFiles();
  await page.evaluate(()=>{window.opticalFixture.failSave=true;});await page.click('#optical-setup-save');
  await page.waitForFunction(()=>document.querySelector('#optical-setup-status').textContent.includes('integrity check'));
  assert.equal(await page.locator('#optical-setup-dialog').evaluate(dialog=>dialog.open),true);
  await page.click('#optical-setup-cancel');assert.deepEqual(await reviewState(),draft);assert.deepEqual(await state(page),before);
  assert.equal(await page.isDisabled('#image-import-recognize'),true);
  await page.click('#image-import-setup');await page.waitForFunction(()=>!document.querySelector('#optical-setup-runtime-choose').disabled);await chooseFiles();
  await page.evaluate(()=>{window.opticalFixture.failSave=false;});await page.click('#optical-setup-save');
  await page.waitForFunction(()=>!document.querySelector('#optical-setup-dialog').open);
  assert.equal(await page.isDisabled('#image-import-recognize'),false);assert.deepEqual(await reviewState(),draft);assert.deepEqual(await state(page),before);
  await page.click('#image-import-setup');await page.waitForFunction(()=>!document.querySelector('#optical-setup-runtime-choose').disabled);await page.keyboard.press('Escape');
  assert.equal(await page.locator('#optical-setup-dialog').evaluate(dialog=>dialog.open),false);assert.deepEqual(await reviewState(),draft);
  const atoms=()=>page.locator('[data-correction="target"] option').evaluateAll(options=>options.filter(o=>o.value.startsWith('atom:')).length);
  assert.equal(await atoms(),3);await page.click('[data-correction="undo"]');await page.waitForFunction(()=>!document.querySelector('[data-correction="redo"]').disabled);assert.equal(await atoms(),2);
  await page.click('[data-correction="redo"]');await page.waitForFunction(()=>!document.querySelector('[data-correction="undo"]').disabled);assert.equal(await atoms(),3);
  assert.deepEqual(await page.evaluate(()=>({inspections:window.opticalFixture.inspections,recognitions:window.opticalFixture.recognitions,cancellations:window.opticalFixture.cancellations,saves:window.opticalFixture.saves})),{inspections:1,recognitions:0,cancellations:0,saves:2});
  assert.deepEqual(await state(page),before);await page.click('#image-import-cancel');
});
