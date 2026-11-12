import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor as fixture,ready,state,tabTo,press,number,choose} from './editor-browser-fixture.mjs';

test('keyboard-only chemistry uses production transactions, undo, selection and ring placement', {timeout:150000},async t=>{
  const page=await fixture(t);
  const step=text=>t.diagnostic(text);
  assert.equal(await page.locator('#keyboard-editor').count(),1,'A complete keyboard drawing route must exist.');
  await press(page,'keyboard-summary');
  await press(page,'keyboard-atom');
  let s=await state(page);assert.equal(s.document.graph.atoms.length,1);
  const first=s.document.graph.atoms[0].id;
  await press(page,'keyboard-start-bond');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'keyboard-target');
  await choose(page,'keyboard-element','O');
  await press(page,'keyboard-finish-bond');
  s=await state(page);assert.equal(s.document.graph.atoms.length,2);assert.equal(s.document.graph.bonds.length,1);
  assert.equal(s.document.graph.atoms[1].symbol,'O');
  const beforePending=s;
  step('Atom and C-O bond created through keyboard controls.');
  await press(page,'keyboard-start-bond');await press(page,'keyboard-cancel-bond');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'keyboard-target');
  assert.deepEqual(await state(page),beforePending,'Cancelling a pending bond must not dirty or revise the project.');
  await choose(page,'keyboard-target','atom:'+first);await press(page,'keyboard-select');
  await number(page,'keyboard-dx',1);await number(page,'keyboard-dy',-1);
  const beforeMove=await state(page);await press(page,'keyboard-move');
  s=await state(page);assert.equal(s.document.graph.atoms[0].x,beforeMove.document.graph.atoms[0].x+1);
  assert.equal(s.document.graph.atoms[0].y,beforeMove.document.graph.atoms[0].y-1);
  await press(page,'undo-button');assert.deepEqual((await state(page)).document,beforeMove.document);
  await press(page,'redo-button');assert.deepEqual((await state(page)).document,s.document);
  step('Selection movement, undo and redo completed.');
  await choose(page,'keyboard-target','empty');await number(page,'keyboard-x',6);await number(page,'keyboard-y',0);
  await choose(page,'keyboard-ring-type','6');await press(page,'keyboard-ring');
  const ring=await state(page);assert.equal(ring.document.graph.atoms.length,8);assert.equal(ring.document.graph.bonds.length,7);
  step('Free six-membered ring created; next action is keyboard traversal to Undo.');
  await press(page,'undo-button');assert.deepEqual((await state(page)).document,s.document);
  await choose(page,'keyboard-target','atom:'+first);await press(page,'keyboard-select');
  await press(page,'keyboard-delete');assert.equal((await state(page)).document.graph.atoms.length,1);
  await press(page,'undo-button');assert.deepEqual((await state(page)).document,s.document);
  assert.ok(await page.evaluate(()=>document.activeElement.id==='undo-button'),'Document rendering preserves actual keyboard focus.');
});

test('keyboard branches, existing endpoints, attached/fused rings and atom/bond edits reach the real chemistry engine', {timeout:150000},async t=>{
  const page=await fixture(t);await press(page,'keyboard-summary');
  await press(page,'keyboard-atom');const a=(await state(page)).document.graph.atoms[0].id;
  await press(page,'keyboard-start-bond');await press(page,'keyboard-finish-bond');const b=(await state(page)).document.graph.atoms[1].id;
  await choose(page,'keyboard-target','atom:'+a);await press(page,'keyboard-start-bond');await number(page,'keyboard-x',-1);await number(page,'keyboard-y',1);
  await press(page,'keyboard-finish-bond');const c=(await state(page)).document.graph.atoms[2].id;
  assert.equal((await state(page)).document.graph.bonds.length,2);
  await press(page,'keyboard-start-bond');await choose(page,'keyboard-target','atom:'+b);await press(page,'keyboard-finish-bond');
  let s=await state(page);assert.equal(s.document.graph.atoms.length,3);assert.equal(s.document.graph.bonds.length,3);
  // An existing edge must not produce a new revision or partial endpoint.
  await press(page,'keyboard-start-bond');await choose(page,'keyboard-target','atom:'+c);await press(page,'keyboard-finish-bond');
  assert.deepEqual(await state(page),s);assert.match(await page.textContent('#keyboard-status'),/already bonded/);await press(page,'keyboard-cancel-bond');
  await choose(page,'keyboard-target','atom:'+a);await choose(page,'keyboard-ring-type','5');await press(page,'keyboard-ring');
  s=await state(page);assert.equal(s.document.graph.atoms.length,7);assert.equal(s.document.graph.bonds.length,8);
  await press(page,'undo-button');const triangle=await state(page),edge=triangle.document.graph.bonds[0].id;
  await choose(page,'keyboard-target','bond:'+edge);await choose(page,'keyboard-ring-type','6');await choose(page,'keyboard-ring-side','right');await press(page,'keyboard-ring');
  s=await state(page);assert.equal(s.document.graph.atoms.length,7);assert.equal(s.document.graph.bonds.length,8);
  await press(page,'undo-button');assert.deepEqual((await state(page)).document,triangle.document);
  await choose(page,'keyboard-target','bond:'+edge);await choose(page,'keyboard-bond-type','2');await press(page,'keyboard-edit-bond');
  assert.equal((await state(page)).document.graph.bonds.find(x=>x.id===edge).order,2);await press(page,'undo-button');
  await choose(page,'keyboard-target','atom:'+c);await choose(page,'keyboard-element','N');await press(page,'keyboard-atom');
  assert.equal((await state(page)).document.graph.atoms.find(x=>x.id===c).symbol,'N');await press(page,'keyboard-select');
  await number(page,'atom-charge',1);await number(page,'atom-isotope',15);await press(page,'apply-atom');
  const nitrogen=(await state(page)).document.graph.atoms.find(x=>x.id===c);assert.equal(nitrogen.charge,1);assert.equal(nitrogen.isotope,15);
  await choose(page,'keyboard-target','bond:'+edge);await press(page,'keyboard-endpoints');await number(page,'keyboard-dx',1);
  const before=await state(page);await press(page,'keyboard-move');const after=await state(page);
  assert.equal(after.document.graph.atoms.find(x=>x.id===a).x,before.document.graph.atoms.find(x=>x.id===a).x+1);
  assert.equal(after.document.graph.atoms.find(x=>x.id===b).x,before.document.graph.atoms.find(x=>x.id===b).x+1);
  assert.equal(after.document.graph.atoms.find(x=>x.id===c).x,before.document.graph.atoms.find(x=>x.id===c).x);
});

test('pending keyboard edits respect modal Escape, revisions and failed rendering', {timeout:150000},async t=>{
  const page=await fixture(t);await press(page,'keyboard-summary');await press(page,'keyboard-atom');
  await press(page,'keyboard-start-bond');const before=await state(page);
  await press(page,'help-button');await page.keyboard.press('Escape');
  assert.equal(await page.locator('#about-dialog').evaluate(el=>el.open),false,'First Escape closes the active modal.');
  assert.deepEqual(await state(page),before);
  // Changing document settings invalidates the captured start revision.
  await press(page,'keyboard-start-bond');await number(page,'export-width',1600);
  assert.equal(await page.isDisabled('#keyboard-finish-bond'),true);
  const original=await state(page),artwork=await page.locator('#drawing').innerHTML();
  await choose(page,'keyboard-element','N');
  // Fault only the next render response; real production state must roll back.
  await page.route('**/api/render',async route=>{await page.unroute('**/api/render');await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({graph:original.document.graph,scene:{svg:'<svg invalid'}})});},{times:1});
  await choose(page,'keyboard-target','empty');await press(page,'keyboard-atom');
  assert.deepEqual(await state(page),original);assert.equal(await page.locator('#drawing').innerHTML(),artwork);
  assert.match(await page.textContent('#keyboard-status'),/not applied/);
  assert.equal(await page.evaluate(()=>document.activeElement?.id),'keyboard-atom');
  await press(page,'keyboard-atom');assert.equal((await state(page)).document.graph.atoms.length,2);
});

test('keyboard render cancellation rejects late results and target overlays stay out of white PNGs', {timeout:150000},async t=>{
  const page=await fixture(t);await press(page,'keyboard-summary');await press(page,'keyboard-atom');
  const before=await state(page);let release,started;
  const held=new Promise(resolve=>{release=resolve;}),observed=new Promise(resolve=>{started=resolve;});
  t.after(()=>release());
  await page.route('**/api/render',async route=>{started();await held;await route.continue().catch(()=>{});},{times:1});
  await choose(page,'keyboard-target','empty');await tabTo(page,'keyboard-atom');await page.keyboard.press('Enter');await observed;
  await page.keyboard.press('Escape');await ready(page);
  assert.deepEqual(await state(page),before,'Cancelled rendering cannot commit a partial keyboard edit.');
  release();await page.unroute('**/api/render');
  await press(page,'keyboard-atom');assert.equal((await state(page)).document.graph.atoms.length,2);
  await number(page,'export-width',640);
  const original=await state(page);
  const inspectExport=()=>page.evaluate(async()=>{
    const {exportPng}=await import('/modules/export.mjs');
    const result=await exportPng(document.getElementById('drawing'),{width:640,marginMode:'percent',margin:2},{graph:window.nivramDocument.getSnapshot().document.graph});
    const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await result.blob.arrayBuffer()))).map(n=>n.toString(16).padStart(2,'0')).join('');
    const bitmap=await createImageBitmap(result.blob),canvas=document.createElement('canvas');canvas.width=bitmap.width;canvas.height=bitmap.height;
    const context=canvas.getContext('2d');context.drawImage(bitmap,0,0);const corner=[...context.getImageData(0,0,1,1).data];bitmap.close();canvas.width=canvas.height=1;
    return {hash,corner,width:result.width,height:result.height};
  });
  const light=await inspectExport();
  await choose(page,'keyboard-target','atom:'+original.document.graph.atoms[0].id);
  assert.equal(await page.locator('#keyboard-target-overlay circle').count(),1);
  assert.equal(await page.locator('#drawing #keyboard-target-overlay').count(),0);
  await press(page,'theme-toggle');const dark=await inspectExport();
  assert.deepEqual(dark,light);assert.deepEqual(light.corner,[255,255,255,255]);assert.equal(light.width,640);
  assert.deepEqual(await state(page),original,'Navigation, target overlays, theme and export never change the project.');
});
