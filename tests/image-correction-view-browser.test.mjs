import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,state} from './editor-browser-fixture.mjs';

// Real production editor and chemistry service. Only the native PNG selection
// and preserved-source receipt are synthetic, as in the chemistry browser test.
const canvasSelector='#image-import-candidate';
const control=(page,key)=>page.locator(`[data-correction="${key}"]`);
const settled=page=>page.waitForFunction(()=>
  document.querySelector('#image-import-candidate').getAttribute('aria-busy')==='false'
  &&!document.querySelector('[data-correction="fit"]').disabled);
const apiResponse=(page,name)=>page.waitForResponse(response=>
  new URL(response.url()).pathname===`/api/${name}`&&response.request().method()==='POST');

async function fixture(t){
  const page=await productionEditor(t);
  const candidate=await page.evaluate(async()=>{
    const token=document.querySelector('meta[name="nivram-token"]').content;
    async function chemistry(source){
      const response=await fetch('/api/import',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':token},
        body:JSON.stringify({source,style:window.nivramDocument.getSnapshot().document.style})});
      if(!response.ok)throw new Error(await response.text());return response.json();
    }
    const preceding=await chemistry('CC'),candidate=await chemistry('CO');
    await window.nivramDocument.openProject({...window.nivramDocument.getSnapshot().document,graph:preceding.graph});
    const source=document.createElement('canvas');source.width=120;source.height=80;
    const context=source.getContext('2d');context.fillStyle='white';context.fillRect(0,0,120,80);
    context.strokeStyle='black';context.lineWidth=2;context.beginPath();context.moveTo(25,40);context.lineTo(80,40);context.stroke();
    context.fillStyle='red';context.font='22px sans-serif';context.fillText('O',84,48);
    const png=source.toDataURL('image/png'),bytes=Uint8Array.from(atob(png.split(',')[1]),c=>c.charCodeAt(0));
    const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');
    const result={...candidate,sourceSha256:hash,selectedComponents:['molecule'],warnings:['Review the source before import.']};
    window.nivramNative={resetDestination(){}};
    window.pywebview={api:{
      inspect_image:async id=>({status:'inspected',jobId:id,name:'view-fixture.png',reviewAvailable:true,sourcePreview:png,
        inspection:{schemaVersion:1,originalSha256:hash,image:{width:120,height:80},engine:{name:'RDKit',version:'real local render'},
          candidates:[{id:'molecule',status:'supported',label:'Methanol',graph:candidate.graph}],warnings:[]}}),
      preview_image_components:async()=>result,
      prepare_image_import:async()=>({...result,provenance:{kind:'png-metadata-component-reconstruction',sourceSha256:hash,
        selectedComponents:['molecule'],sourceStorage:'synthetic fixture'}}),
      cancel_image_import:async()=>({status:'cancelled'})
    }};
    window.dispatchEvent(new Event('pywebviewready'));window.dispatchEvent(new Event('nivram-native-ready'));
    return candidate;
  });
  const renders=[];
  page.on('request',request=>{if(new URL(request.url()).pathname==='/api/render'&&request.method()==='POST')renders.push(request.postDataJSON());});
  return {page,candidate,renders,before:await state(page)};
}

async function openPreview(page){
  await page.click('#image-import-button');
  await page.waitForFunction(()=>document.querySelector('#image-import-components input')&&!document.querySelector('#image-import-components input').disabled);
  await page.check('#image-import-components input');await page.click('#image-import-preview');await settled(page);
}

async function fields(page,open){
  const details=page.locator('#image-import-correction details');
  if(await details.evaluate(node=>node.open)!==open)await details.locator('summary').click();
}

async function view(page){
  return page.locator(canvasSelector).evaluate(node=>{
    const box=node.viewBox.baseVal;return {x:box.x,y:box.y,w:box.width,h:box.height};
  });
}

function closeNumber(actual,expected,label,tolerance=.3){
  assert.ok(Math.abs(actual-expected)<=tolerance,`${label}: expected ${expected}, received ${actual}`);
}
function closeView(actual,expected,label){for(const key of ['x','y','w','h'])closeNumber(actual[key],expected[key],`${label} ${key}`,.01);}

async function selectedPoint(page){
  return page.locator(`${canvasSelector}>g`).nth(1).locator('circle').evaluate(circle=>{
    const point=new DOMPoint(Number(circle.getAttribute('cx')),Number(circle.getAttribute('cy'))).matrixTransform(circle.getScreenCTM());
    const canvas=circle.ownerSVGElement,rect=canvas.getBoundingClientRect();
    return {x:point.x-rect.left,y:point.y-rect.top,clientX:point.x,clientY:point.y};
  });
}

async function dragPan(page,dx=54,dy=31){
  await control(page,'tool').selectOption('pan');await page.locator(canvasSelector).scrollIntoViewIfNeeded();
  const box=await page.locator(canvasSelector).boundingBox();
  const x=box.x+box.width*.4,y=box.y+box.height*.4;
  await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+dx,y+dy,{steps:5});await page.mouse.up();
}

test('PNG draft zoom, wheel anchor, pan and Fit only change the review view', {timeout:90000},async t=>{
  const {page,before,renders}=await fixture(t);await openPreview(page);
  const fitted=await view(page),artwork=await page.locator(`${canvasSelector}>g`).first().innerHTML();
  await page.check('#image-import-acknowledge');const renderCount=renders.length;
  assert.equal(await control(page,'undo').isDisabled(),true);assert.equal(await control(page,'redo').isDisabled(),true);
  await control(page,'zoom-in').click();const zoomed=await view(page);
  assert.ok(zoomed.w<fitted.w&&zoomed.h<fitted.h,'Zoom in must increase the drawing scale.');
  await control(page,'zoom-out').click();assert.ok((await view(page)).w>zoomed.w,'Zoom out must decrease the drawing scale.');
  await page.locator(canvasSelector).scrollIntoViewIfNeeded();
  const box=await page.locator(canvasSelector).boundingBox(),anchor={x:box.x+box.width*.29,y:box.y+box.height*.61};
  const scenePoint=anchor=>page.locator(canvasSelector).evaluate((node,anchor)=>{
    const p=new DOMPoint(anchor.x,anchor.y).matrixTransform(node.getScreenCTM().inverse());return {x:p.x,y:p.y};
  },anchor);
  // WheelEvent client coordinates may be rounded by Chromium even when the
  // automation pointer was placed at fractional CSS coordinates. Bind the
  // expected point to the delivered event, before the app's bubble handler.
  await page.locator(canvasSelector).evaluate(node=>node.addEventListener('wheel',event=>{
    const p=new DOMPoint(event.clientX,event.clientY).matrixTransform(node.getScreenCTM().inverse());
    window.__draftWheel={client:{x:event.clientX,y:event.clientY},before:{x:p.x,y:p.y}};
  },{once:true,capture:true}));
  const wheelBefore=await view(page);
  await page.mouse.move(anchor.x,anchor.y);await page.mouse.wheel(0,-180);
  await page.waitForFunction(previous=>document.querySelector('#image-import-candidate').viewBox.baseVal.width<previous,wheelBefore.w);
  const delivered=await page.evaluate(()=>window.__draftWheel),anchorAfter=await scenePoint(delivered.client);
  closeNumber(anchorAfter.x,delivered.before.x,'Wheel anchor x',.02);closeNumber(anchorAfter.y,delivered.before.y,'Wheel anchor y',.02);
  const pointBefore=await page.locator(`${canvasSelector} path`).first().evaluate(path=>{
    const p=path.getPointAtLength(0).matrixTransform(path.getScreenCTM()),r=path.ownerSVGElement.getBoundingClientRect();return {x:p.x-r.left,y:p.y-r.top};
  });
  await dragPan(page,54,31);
  assert.equal(await control(page,'apply-tool').isDisabled(),true,'Pan must not offer the atom-moving Apply tool action');
  const pointAfter=await page.locator(`${canvasSelector} path`).first().evaluate(path=>{
    const p=path.getPointAtLength(0).matrixTransform(path.getScreenCTM()),r=path.ownerSVGElement.getBoundingClientRect();return {x:p.x-r.left,y:p.y-r.top};
  });
  closeNumber(pointAfter.x-pointBefore.x,54,'Pan screen displacement x');closeNumber(pointAfter.y-pointBefore.y,31,'Pan screen displacement y');
  // The pointer gesture leaves the real SVG focused; keyboard navigation must
  // remain a view operation even though the Pan tool is selected.
  const keyboardBefore=await view(page);
  await page.keyboard.press('Shift+Equal');const keyboardZoom=await view(page);
  assert.ok(keyboardZoom.w<keyboardBefore.w&&keyboardZoom.h<keyboardBefore.h,'Keyboard + zooms in.');
  await page.keyboard.press('Minus');assert.ok((await view(page)).w>keyboardZoom.w,'Keyboard - zooms out.');
  const arrowsBefore=await view(page);
  await page.keyboard.press('ArrowRight');const right=await view(page);
  assert.ok(right.x>arrowsBefore.x,'Pan ArrowRight advances the view origin.');
  closeNumber(right.y,arrowsBefore.y,'Horizontal keyboard pan preserves y',.01);
  await page.keyboard.press('ArrowUp');const up=await view(page);
  assert.ok(up.y<right.y,'Pan ArrowUp moves the view origin upward.');
  closeNumber(up.x,right.x,'Vertical keyboard pan preserves x',.01);
  closeNumber(up.w,arrowsBefore.w,'Keyboard pan preserves view width',.01);
  closeNumber(up.h,arrowsBefore.h,'Keyboard pan preserves view height',.01);
  await control(page,'fit').click();closeView(await view(page),fitted,'Fit restores original framing');
  assert.equal(await page.locator(`${canvasSelector}>g`).first().innerHTML(),artwork);
  assert.equal(renders.length,renderCount,'Navigation must not request a graph correction.');
  assert.equal(await control(page,'undo').isDisabled(),true);assert.equal(await control(page,'redo').isDisabled(),true);
  assert.equal(await page.isChecked('#image-import-acknowledge'),true);
  assert.deepEqual(await state(page),before);
  if(process.env.NIVRAM_NAV_SCREENSHOT){
    await page.locator(canvasSelector).scrollIntoViewIfNeeded();
    await control(page,'fit').scrollIntoViewIfNeeded();
    await page.screenshot({path:process.env.NIVRAM_NAV_SCREENSHOT});
  }
});

test('PNG manual view preserves molecular coordinates through real label edits, Undo/Redo and select/move', {timeout:90000},async t=>{
  const {page,candidate,before,renders}=await fixture(t);await openPreview(page);
  const oxygen=candidate.graph.atoms.find(atom=>atom.symbol==='O').id;
  await fields(page,true);await control(page,'target').selectOption(`atom:${oxygen}`);await fields(page,false);
  await control(page,'zoom-in').click();await control(page,'zoom-in').click();await dragPan(page,39,-24);
  const manual=await selectedPoint(page);
  await fields(page,true);await control(page,'element').fill('Cl');await control(page,'isotope').fill('37');
  const correctionResponse=apiResponse(page,'render');await control(page,'apply-atom').click();
  const corrected=await (await correctionResponse).json();await settled(page);await fields(page,false);
  assert.notDeepEqual(corrected.scene.transform,candidate.scene.transform,'The fixture must actually change RDKit SVG framing.');
  let point=await selectedPoint(page);closeNumber(point.x,manual.x,'Label edit atom x');closeNumber(point.y,manual.y,'Label edit atom y');
  assert.equal(corrected.graph.atoms.find(atom=>atom.id===oxygen).symbol,'Cl');
  for(const key of ['undo','redo']){
    const response=apiResponse(page,'render');await control(page,key).click();await (await response).finished();await settled(page);
    point=await selectedPoint(page);closeNumber(point.x,manual.x,`${key} atom x`);closeNumber(point.y,manual.y,`${key} atom y`);
  }
  // The original select/move gesture remains an actual graph edit after pan.
  await control(page,'tool').selectOption('select');await page.locator(canvasSelector).scrollIntoViewIfNeeded();
  const start=await selectedPoint(page),response=apiResponse(page,'render'),count=renders.length;
  await page.mouse.move(start.clientX,start.clientY);await page.mouse.down();
  await page.mouse.move(start.clientX+22,start.clientY-16,{steps:5});await page.mouse.up();
  const moved=await (await response).json();await settled(page);
  assert.equal(renders.length,count+1);
  const oldAtom=corrected.graph.atoms.find(atom=>atom.id===oxygen),newAtom=moved.graph.atoms.find(atom=>atom.id===oxygen);
  assert.ok(Math.abs(newAtom.x-oldAtom.x)+Math.abs(newAtom.y-oldAtom.y)>.01,'Select/move must still move the selected atom.');
  point=await selectedPoint(page);closeNumber(point.x,start.x+22,'Moved atom x');closeNumber(point.y,start.y-16,'Moved atom y');
  assert.deepEqual(await state(page),before);
});

test('PNG replacement and a new review Fit; pending and closed reviews disable navigation', {timeout:90000},async t=>{
  const {page,before}=await fixture(t);await openPreview(page);const originalFit=await view(page);
  await control(page,'zoom-in').click();await control(page,'zoom-in').click();await dragPan(page,92,42);
  await control(page,'structure-source').fill('c1ccccc1CCO');
  const replacementResponse=apiResponse(page,'import');await control(page,'replace-source').click();
  await (await replacementResponse).finished();await settled(page);
  const replacementFit=await view(page);
  await control(page,'fit').click();closeView(await view(page),replacementFit,'Replacement automatically Fits');
  const ink=await page.locator(`${canvasSelector}>g`).first().evaluate(node=>{
    const b=node.getBBox();return {x:b.x,y:b.y,w:b.width,h:b.height};
  });
  assert.ok(ink.x>=replacementFit.x&&ink.y>=replacementFit.y&&ink.x+ink.w<=replacementFit.x+replacementFit.w&&ink.y+ink.h<=replacementFit.y+replacementFit.h,
    'The fitted replacement must contain its complete rendered ink.');
  await control(page,'zoom-in').click();
  let release,announce;
  const hold=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{announce=resolve;});
  let held=false;
  await page.route('**/api/render',async route=>{
    if(!held){held=true;announce();await hold;}await route.continue();
  });
  t.after(()=>release());
  await fields(page,true);
  const target=await control(page,'target').locator('option').evaluateAll(options=>options.find(option=>option.value.startsWith('atom:')).value);
  await control(page,'target').selectOption(target);await control(page,'isotope').fill('13');
  const correctionResponse=apiResponse(page,'render');await control(page,'apply-atom').click();await started;
  for(const key of ['zoom-in','zoom-out','fit','tool'])assert.equal(await control(page,key).isDisabled(),true,`${key} disabled while chemistry is pending`);
  const pendingView=await view(page);await page.locator(canvasSelector).scrollIntoViewIfNeeded();
  const box=await page.locator(canvasSelector).boundingBox();
  await page.mouse.move(box.x+box.width*.5,box.y+box.height*.5);await page.mouse.wheel(0,-160);
  await page.mouse.down();await page.mouse.move(box.x+box.width*.5+35,box.y+box.height*.5+20,{steps:3});await page.mouse.up();
  closeView(await view(page),pendingView,'Pending navigation is inert');
  release();await (await correctionResponse).finished();await settled(page);
  await page.click('#image-import-cancel');
  assert.equal(await page.locator('#image-import-dialog').evaluate(node=>node.open),false);
  for(const key of ['zoom-in','zoom-out','fit','tool'])assert.equal(await control(page,key).isDisabled(),true,`${key} disabled after close`);
  assert.equal(await page.locator(`${canvasSelector}>g`).count(),0);
  await openPreview(page);closeView(await view(page),originalFit,'New review resets to original Fit');
  assert.equal(await control(page,'undo').isDisabled(),true);assert.equal(await page.isChecked('#image-import-acknowledge'),false);
  assert.deepEqual(await state(page),before);
});
