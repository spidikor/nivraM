import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,ready,state} from './editor-browser-fixture.mjs';

// Real editor/chemistry fixture; only response delivery is held or rejected.
// Geometry reads use the rendered drawing and actual CSS canvas, not viewport
// dimensions as a substitute for canvas size. No document or DOM edits injected.
const largeStructure='CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC.[NH4+].[Cl-]';
const resizedViewport={width:800,height:600};
async function frames(page){
  // The first frame's ResizeObserver delivery precedes the second callback.
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));
}
async function load(page,text=largeStructure){
  await page.click('#import-button');await page.fill('#structure-input',text);await page.click('#load-structure');
  await page.locator('#import-dialog').waitFor({state:'hidden'});await ready(page);await frames(page);
}
async function ink(page){
  return page.evaluate(()=>{
    const canvas=document.getElementById('canvas'),drawing=document.getElementById('drawing');
    const rect=canvas.getBoundingClientRect(),box=drawing.getBBox(),matrix=drawing.getScreenCTM();
    if(!matrix)throw new Error('Rendered drawing has no screen transform.');
    const corners=[[box.x,box.y],[box.x+box.width,box.y],[box.x,box.y+box.height],[box.x+box.width,box.y+box.height]]
      .map(([x,y])=>new DOMPoint(x,y).matrixTransform(matrix));
    const left=Math.min(...corners.map(p=>p.x)),right=Math.max(...corners.map(p=>p.x));
    const top=Math.min(...corners.map(p=>p.y)),bottom=Math.max(...corners.map(p=>p.y));
    return {canvas:{width:rect.width,height:rect.height},ink:{width:right-left,height:bottom-top},
      padding:{left:left-rect.left,right:rect.right-right,top:top-rect.top,bottom:rect.bottom-bottom},
      view:canvas.getAttribute('viewBox')};
  });
}
function assertFitted(measured,context='Rendered drawing'){
  for(const group of [measured.canvas,measured.ink])for(const value of Object.values(group))
    assert.ok(Number.isFinite(value)&&value>0,'Fit assertions require visible, nonempty rendered geometry.');
  for(const [side,value] of Object.entries(measured.padding))
    assert.ok(Number.isFinite(value)&&value>=23.5,`${context}: ${side} rendered ink padding is ${value} CSS px; expected at least 24 (0.5 tolerance).`);
}
async function worldView(page){
  // As in editor-refinement.test.mjs, obtain the real renderer's coordinate
  // transform without publishing its read-only result into the document.
  return page.evaluate(async()=>{
    const project=window.nivramDocument.getSnapshot().document;
    const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json',
      'X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:project.graph,style:project.style})});
    if(!response.ok)throw new Error('Read-only world-view inspection failed.');
    const {scene}=await response.json(),tr=scene.transform;
    const [x,y,w,h]=document.getElementById('canvas').getAttribute('viewBox').split(/\s+/).map(Number);
    return {x:(x-tr.x0)/tr.xScale,y:(y-tr.y0)/tr.yScale,w:w/tr.xScale,h:h/tr.yScale};
  });
}
function assertWorldView(actual,expected){
  assert.deepEqual(Object.keys(actual),['x','y','w','h']);
  for(const key of Object.keys(expected))assert.ok(Number.isFinite(actual[key])&&Number.isFinite(expected[key])
    &&Math.abs(actual[key]-expected[key])<=1e-7,`${key} changed from ${expected[key]} to ${actual[key]}`);
}
async function bounded(promise,label){
  let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(label)),15000);})]);}
  finally{clearTimeout(timer);}
}
async function holdRender(page,{reject=false}={}){
  let release,received,receiveError,completed,first=true,allowCancellation=false,failure=null;
  const barrier=new Promise(resolve=>release=resolve),arrived=new Promise((resolve,reject)=>{received=resolve;receiveError=reject;});
  const done=new Promise(resolve=>completed=resolve);
  // Keep early route errors handled until the test reaches wait().
  void arrived.catch(()=>{});
  const handler=async route=>{
    if(!first){await route.continue();return;}first=false;
    try{
      const response=await route.fetch({timeout:10000});
      assert.equal(response.status(),200,'The delayed edit must have a real successful chemistry response.');
      received();await barrier;
      if(reject)await route.fulfill({status:422,contentType:'application/json',body:JSON.stringify({error:'Synthetic held render rejection'})});
      else await route.fulfill({response});
    }catch(error){if(!allowCancellation){failure=error;receiveError(error);}}
    finally{completed();}
  };
  await page.route('**/api/render',handler);
  return {wait:()=>bounded(arrived,'The editor did not reach the held render.'),release:()=>release(),
    cancel:()=>{allowCancellation=true;},
    async finish(){release();try{if(!first)await bounded(done,'The held route did not settle.');if(failure)throw failure;}
      finally{await page.unroute('**/api/render',handler);}}};
}
async function startEdit(page,held){
  await page.fill('#bond-width','4');await page.click('#apply-style');await held.wait();
  assert.notEqual(await page.textContent('#request-status'),'Ready','The edit must still be pending at resize.');
}
async function resizeWhileHeld(page,before){
  await page.setViewportSize(resizedViewport);await frames(page);
  const resized=await ink(page);
  assert.ok(resized.canvas.width<before.canvas.width,'The actual canvas must shrink; viewport size alone is not evidence.');
  assert.deepEqual(resized.view,before.view,'A busy edit defers the resize fit until it settles.');
  // SVG letterboxing can keep sufficient padding after a viewport shrinks.
  // The contract is to fit the current viewport once ready, including its
  // comfortable-zoom cap; compare with the user's explicit Fit below.
  return resized;
}

test('a render held across resize refits current rendered ink when the edit succeeds',{timeout:90000},async t=>{
  const page=await productionEditor(t);await load(page);
  const before=await state(page),initial=await ink(page);assertFitted(initial);
  const held=await holdRender(page);
  try{
    await startEdit(page,held);const during=await resizeWhileHeld(page,initial);
    t.diagnostic(JSON.stringify({phase:'resize-held',initial,during}));
    assert.deepEqual(await state(page),before,'Pending rendering must not commit early.');
    held.release();await held.finish();await ready(page);await frames(page);
    const after=await state(page),fitted=await ink(page);
    t.diagnostic(JSON.stringify({phase:'resize-settled',measured:fitted,revisionDelta:after.revision-before.revision}));
    assert.equal(after.revision,before.revision+1);assert.equal(after.document.style.bondWidth,4);
    assert.deepEqual(after.document.graph,before.document.graph);assertFitted(fitted,'Resize completion');
    assert.notEqual(fitted.view,initial.view);
    await page.click('#fit-button');await frames(page);
    t.diagnostic(JSON.stringify({phase:'automatic-versus-explicit-fit',automatic:fitted.view,explicit:(await ink(page)).view}));
    assert.equal((await ink(page)).view,fitted.view,'Deferred fit must match explicit Fit for the current drawing and canvas.');
    assert.deepEqual(await state(page),after,'Fitting must not add a molecular edit or history entry.');
  }finally{await held.finish();}
});

test('a rejected held edit refits the retained drawing after resize without a history entry',{timeout:90000},async t=>{
  const page=await productionEditor(t);await load(page);
  const before=await state(page),initial=await ink(page);assertFitted(initial);
  const held=await holdRender(page,{reject:true});
  try{
    await startEdit(page,held);await resizeWhileHeld(page,initial);
    held.release();await held.finish();await ready(page);await frames(page);
    assert.match(await page.textContent('#toast'),/Synthetic held render rejection/);
    assert.deepEqual(await state(page),before);assertFitted(await ink(page));
    const fitted=(await ink(page)).view;
    await page.click('#fit-button');await frames(page);assert.equal((await ink(page)).view,fitted);
  }finally{await held.finish();}
});

test('cancel refits retained ink; a late response cannot refit a replacement or override subsequent zoom and pan',{timeout:90000},async t=>{
  const page=await productionEditor(t);await load(page);
  const before=await state(page),initial=await ink(page);assertFitted(initial);
  const held=await holdRender(page);
  try{
    await startEdit(page,held);await resizeWhileHeld(page,initial);
    held.cancel();await page.locator('#canvas').focus();await page.keyboard.press('Escape');await ready(page);await frames(page);
    assert.deepEqual(await state(page),before);assertFitted(await ink(page));
    const cancelledView=(await ink(page)).view;
    await page.click('#fit-button');await frames(page);
    assert.equal((await ink(page)).view,cancelledView,'Cancellation must consume the pending fit before later document replacement.');
    await load(page,'CCO.[Cl-]');const replacement=await state(page);
    assert.equal(replacement.document.graph.atoms.length,4);assertFitted(await ink(page));
    const automatic=(await ink(page)).view;
    await page.click('#zoom-out');const zoomed=(await ink(page)).view;assert.notEqual(zoomed,automatic);
    const rect=await page.locator('#canvas').boundingBox();assert.ok(rect&&rect.width>0&&rect.height>0);
    await page.mouse.move(rect.x+rect.width*.5,rect.y+rect.height*.5);await page.keyboard.down('Alt');
    try{await page.mouse.down();await page.mouse.move(rect.x+rect.width*.6,rect.y+rect.height*.6);await page.mouse.up();}
    finally{await page.keyboard.up('Alt');}
    const manual=(await ink(page)).view;assert.notEqual(manual,zoomed,'The real pan must change the view.');
    held.release();await held.finish();await ready(page);await frames(page);
    assert.deepEqual(await state(page),replacement);assert.equal((await ink(page)).view,manual);
    // A later busy-to-ready transition must not revive an older resize request.
    const chosen=await worldView(page);
    await page.fill('#bond-width','3.8');await page.click('#apply-style');await ready(page);await frames(page);
    assertWorldView(await worldView(page),chosen);
    const changed=await state(page);assert.equal(changed.revision,replacement.revision+1);
    assert.deepEqual(changed.document.graph,replacement.document.graph);assert.equal(changed.document.style.bondWidth,3.8);
  }finally{await held.finish();}
});

test('manual zoom during a resize-delayed edit wins over the pending automatic fit',{timeout:90000},async t=>{
  const page=await productionEditor(t);await load(page);
  const before=await state(page),initial=await ink(page);assertFitted(initial);
  const held=await holdRender(page);
  try{
    await startEdit(page,held);await resizeWhileHeld(page,initial);
    await page.click('#zoom-out');const manual=await worldView(page);
    assert.notEqual((await ink(page)).view,initial.view,'The manual zoom must take effect during the held edit.');
    held.release();await held.finish();await ready(page);await frames(page);
    const after=await state(page);assert.equal(after.revision,before.revision+1);
    assert.deepEqual(after.document.graph,before.document.graph);assert.equal(after.document.style.bondWidth,4);
    assertWorldView(await worldView(page),manual);
    const retained=await ink(page);
    await page.setViewportSize({width:1100,height:700});await frames(page);
    const resized=await ink(page);assert.ok(resized.canvas.width>retained.canvas.width);
    assert.equal(resized.view,retained.view,'Manual view remains manual on subsequent resize.');
    assert.deepEqual(await state(page),after);
  }finally{await held.finish();}
});
