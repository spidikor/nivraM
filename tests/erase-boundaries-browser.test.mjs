import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,ready,state} from './editor-browser-fixture.mjs';

// Normal import/erase/release/Undo/Redo paths use real mouse and keyboard input.
// Only the two explicitly named cancellation events below are synthetic.
async function frames(page){await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));}
async function load(page,source='CC'){
  await page.locator('#import-button').click();await page.locator('#structure-input').click();
  await page.keyboard.press('Control+A');await page.keyboard.type(source);
  const [response]=await Promise.all([
    page.waitForResponse(r=>new URL(r.url()).pathname==='/api/import'&&r.request().method()==='POST'),
    page.locator('#load-structure').click(),
  ]);
  assert.equal(response.ok(),true,`Import failed: HTTP ${response.status()}`);
  const result=await response.json();assert.ok(result.scene?.positions?.length>0);
  await page.locator('#import-dialog').waitFor({state:'hidden'});await ready(page);await frames(page);
  return result.scene;
}
async function layout(page,scene){
  return page.evaluate(scene=>{
    const canvas=document.getElementById('canvas'),m=canvas.getScreenCTM(),r=canvas.getBoundingClientRect();
    if(!m)throw new Error('Missing real canvas transform.');
    return {canvas:{left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height},dpr:devicePixelRatio,
      atoms:scene.positions.map(a=>{const p=new DOMPoint(a.x,a.y).matrixTransform(m);return {id:a.id,x:p.x,y:p.y};})};
  },scene);
}
async function observePointer(page){
  // Passive event observation records actual CSS coordinates and pointer identity.
  // It neither prevents events nor mutates application state.
  await page.evaluate(()=>{
    window.__eraseBoundaryEvents=[];
    const canvas=document.getElementById('canvas');
    for(const type of ['pointerdown','pointermove','pointerup'])canvas.addEventListener(type,e=>{
      window.__eraseBoundaryEvents.push({type:e.type,x:e.clientX,y:e.clientY,pointerId:e.pointerId,pointerType:e.pointerType,trusted:e.isTrusted});
    },{passive:true});
  });
}
async function historyButtons(page){return {undo:await page.isDisabled('#undo-button'),redo:await page.isDisabled('#redo-button')};}
async function unchanged(page,before,buttons,artwork){
  await ready(page);await frames(page);
  assert.deepEqual(await state(page),before,'No document, revision, or save-state change is allowed.');
  assert.deepEqual(await historyButtons(page),buttons,'Undo/Redo availability must remain unchanged.');
  assert.equal(await page.locator('#drawing').innerHTML(),artwork,'Artwork must remain unchanged.');
}
async function changeByClick(page,id){
  const revision=(await state(page)).revision;await page.locator(id).click();
  await page.waitForFunction(r=>window.nivramDocument.getSnapshot().revision>r,revision);await ready(page);
}
async function historyRoundTrip(page,document){
  await changeByClick(page,'#undo-button');
  assert.deepEqual((await state(page)).document.graph,{atoms:[],bonds:[]},'One Undo must reach the pre-import empty document, with no extra erase transaction.');
  await changeByClick(page,'#redo-button');
  assert.deepEqual((await state(page)).document,document,'Redo must restore the exact imported document.');
}
function containingRegion(atoms){
  return {start:{x:Math.min(...atoms.map(a=>a.x))-20,y:Math.min(...atoms.map(a=>a.y))-20},
    end:{x:Math.max(...atoms.map(a=>a.x))+20,y:Math.max(...atoms.map(a=>a.y))+20}};
}
function within(point,canvas){return point.x>canvas.left&&point.x<canvas.right&&point.y>canvas.top&&point.y<canvas.bottom;}
function enclosed(p,start,end){return p.x>=Math.min(start.x,end.x)&&p.x<=Math.max(start.x,end.x)&&p.y>=Math.min(start.y,end.y)&&p.y<=Math.max(start.y,end.y);}
async function begin(page,start,end){
  await page.mouse.move(start.x,start.y);await page.mouse.down();await page.mouse.move(end.x,end.y,{steps:5});await frames(page);
}
async function noGesture(page){assert.equal(await page.locator('#gesture > *').count(),0,'Canceled/released gesture must leave no preview.');}

for(const deviceScaleFactor of [1,2])test(`erase threshold is strictly beyond four CSS pixels at DPR ${deviceScaleFactor}`,{timeout:120000},async t=>{
  const page=await productionEditor(t,{deviceScaleFactor}),scene=await load(page);
  await page.locator('[data-tool="erase"]').click();const measured=await layout(page,scene);
  assert.equal(measured.dpr,deviceScaleFactor);assert.equal(measured.atoms.length,2);
  const start={x:Math.ceil(measured.canvas.left)+10,y:Math.ceil(measured.canvas.top)+10};
  assert.ok(within(start,measured.canvas));
  assert.ok(measured.atoms.every(a=>Math.hypot(a.x-start.x,a.y-start.y)>30),'Boundary drag must start on blank canvas.');
  const before=await state(page),buttons=await historyButtons(page),artwork=await page.locator('#drawing').innerHTML();
  await observePointer(page);
  // Integer horizontal displacements avoid floating-point ambiguity at exactly 4.
  // An empty horizontal marquee cannot contain atoms; its activation is observed
  // separately, followed below by a nonempty deletion as a positive control.
  for(const distance of [3,4,5]){
    await page.mouse.move(start.x,start.y);await page.mouse.down();await page.mouse.move(start.x+distance,start.y);await frames(page);
    const events=await page.evaluate(()=>window.__eraseBoundaryEvents);
    const down=events.findLast(e=>e.type==='pointerdown'),move=events.findLast(e=>e.type==='pointermove');
    assert.ok(down?.trusted&&move?.trusted,'Threshold must use actual browser pointer events.');
    const actual=Math.hypot(move.x-down.x,move.y-down.y);
    assert.equal(actual,distance,'Do not substitute device pixels or assume the input was delivered at the requested boundary.');
    assert.equal(await page.locator('#gesture rect').count(),distance>4?1:0,`Erase activation at ${actual} CSS px`);
    assert.equal(await page.locator('#gesture circle').count(),0);
    await unchanged(page,before,buttons,artwork);await page.mouse.up();await noGesture(page);await unchanged(page,before,buttons,artwork);
    t.diagnostic(JSON.stringify({phase:'threshold',dpr:measured.dpr,requestedCss:distance,actualCss:actual,activated:distance>4,canvas:measured.canvas}));
  }
  const area=containingRegion(measured.atoms);
  assert.ok(within(area.start,measured.canvas)&&within(area.end,measured.canvas));
  await begin(page,area.start,area.end);
  assert.equal(await page.locator('#gesture circle').count(),2,'Positive control must actually enclose both atoms.');
  assert.equal(await page.locator('#gesture line').count(),1,'Positive control must preview the incident bond.');
  await unchanged(page,before,buttons,artwork);await page.mouse.up();
  await page.waitForFunction(r=>window.nivramDocument.getSnapshot().revision>r,before.revision);await ready(page);
  const erased=await state(page);assert.equal(erased.revision,before.revision+1);
  assert.deepEqual(erased.document.graph,{atoms:[],bonds:[]});await noGesture(page);
  await changeByClick(page,'#undo-button');assert.deepEqual((await state(page)).document,before.document,'One Undo must restore the entire erase transaction.');
});

test('erase rectangle crossing a bond with both endpoints outside preserves that bond and history',{timeout:120000},async t=>{
  const page=await productionEditor(t),scene=await load(page);await page.locator('[data-tool="erase"]').click();
  const measured=await layout(page,scene),before=await state(page),buttons=await historyButtons(page),artwork=await page.locator('#drawing').innerHTML();
  assert.equal(before.document.graph.bonds.length,1);assert.equal(measured.atoms.length,2);
  const [a,b]=measured.atoms,mid={x:(a.x+b.x)/2,y:(a.y+b.y)/2},length=Math.hypot(b.x-a.x,b.y-a.y),half=length*.2;
  assert.ok(Number.isFinite(length)&&length>60,'Crossing fixture needs a comfortably sized real bond.');
  const corners=[[-1,-1],[-1,1],[1,-1],[1,1]].map(([x,y])=>({x:mid.x+x*half,y:mid.y+y*half}));
  const lineDistance=p=>Math.abs((b.x-a.x)*(a.y-p.y)-(a.x-p.x)*(b.y-a.y))/length;
  const start=corners.sort((p,q)=>lineDistance(q)-lineDistance(p))[0],end={x:2*mid.x-start.x,y:2*mid.y-start.y};
  assert.ok(within(start,measured.canvas)&&within(end,measured.canvas));
  assert.ok(lineDistance(start)>10&&measured.atoms.every(p=>Math.hypot(p.x-start.x,p.y-start.y)>16),'Start must be blank, beyond bond/atom hit targets.');
  assert.ok(enclosed(mid,start,end),'The real bond midpoint must be inside the rectangle.');
  assert.ok(measured.atoms.every(p=>!enclosed(p,start,end)),'Neither real bond endpoint may be inside.');
  await begin(page,start,end);
  assert.equal(await page.locator('#gesture rect').count(),1);assert.equal(await page.locator('#gesture circle').count(),0);
  assert.equal(await page.locator('#gesture line').count(),0,'A crossing-only bond must not be previewed for erasure.');
  await unchanged(page,before,buttons,artwork);await page.mouse.up();await noGesture(page);await unchanged(page,before,buttons,artwork);
  t.diagnostic(JSON.stringify({phase:'crossing-only',canvas:measured.canvas,atoms:measured.atoms,start,end,midpoint:mid}));
  await historyRoundTrip(page,before.document);
});

for(const type of ['pointercancel','lostpointercapture'])test(`synthetic ${type} cancels an active erase; real late pointerup leaves document and history unchanged`,{timeout:120000},async t=>{
  const page=await productionEditor(t),scene=await load(page);await page.locator('[data-tool="erase"]').click();
  const measured=await layout(page,scene),area=containingRegion(measured.atoms);
  assert.ok(within(area.start,measured.canvas)&&within(area.end,measured.canvas));
  const before=await state(page),buttons=await historyButtons(page),artwork=await page.locator('#drawing').innerHTML();
  await observePointer(page);await begin(page,area.start,area.end);
  assert.equal(await page.locator('#gesture circle').count(),2);assert.equal(await page.locator('#gesture line').count(),1);
  await unchanged(page,before,buttons,artwork);
  const pointer=await page.evaluate(()=>window.__eraseBoundaryEvents.findLast(e=>e.type==='pointerdown'));
  assert.ok(pointer?.trusted&&Number.isInteger(pointer.pointerId),'Cancellation must follow a real held pointer.');
  const synthetic=await page.evaluate(({type,pointer})=>{
    const canvas=document.getElementById('canvas');
    const captured=canvas.hasPointerCapture(pointer.pointerId);
    // Explicit synthetic browser-boundary injection. No edit/undo API is called.
    const event=new PointerEvent(type,{bubbles:true,pointerId:pointer.pointerId,pointerType:pointer.pointerType,clientX:pointer.x,clientY:pointer.y});
    canvas.dispatchEvent(event);return {captured,trusted:event.isTrusted};
  },{type,pointer});
  assert.equal(synthetic.captured,true,'The actual drag must own pointer capture before cancellation.');
  assert.equal(synthetic.trusted,false);await noGesture(page);await unchanged(page,before,buttons,artwork);
  await page.mouse.up();await noGesture(page);await unchanged(page,before,buttons,artwork);
  const up=await page.evaluate(()=>window.__eraseBoundaryEvents.findLast(e=>e.type==='pointerup'));
  assert.ok(up?.trusted&&up.pointerId===pointer.pointerId,'Deliver an actual late release of that pointer.');
  t.diagnostic(`${type}: synthetic cancellation of a real captured erase drag; real late release preserved state.`);
  await historyRoundTrip(page,before.document);
});
