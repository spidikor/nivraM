import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,ready,state} from './editor-browser-fixture.mjs';
async function importText(page,text){await page.locator('#import-button').click();await page.locator('#structure-input').fill(text);await page.locator('#load-structure').click();await page.locator('#import-dialog').waitFor({state:'hidden'});await ready(page);}
async function hoverAtom(page,index=0){
  // Atom labels are path outlines; glyph bounds need not contain the atom's
  // hit-test center (and carbons may have no label). Use the renderer's actual
  // atom coordinate, as in editor-refinement.test.mjs, without installing it.
  const point=await page.evaluate(async index=>{
    const project=window.nivramDocument.getSnapshot().document,atom=project.graph.atoms[index];
    if(!atom)throw new Error('The hover fixture has no requested atom.');
    const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json',
      'X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:project.graph,style:project.style})});
    if(!response.ok)throw new Error('Read-only hover coordinate inspection failed.');
    const {scene}=await response.json(),position=scene.positions.find(p=>p.id===atom.id);
    const matrix=document.getElementById('canvas').getScreenCTM();
    if(!position||!matrix)throw new Error('The rendered atom has no screen coordinate.');
    const screen=new DOMPoint(position.x,position.y).matrixTransform(matrix);
    return {x:screen.x,y:screen.y};
  },index);
  assert.ok(Number.isFinite(point.x)&&Number.isFinite(point.y));
  await page.locator('#canvas').focus();await page.mouse.move(point.x,point.y);
}
test('real editor immediately previews C, accepts queued Cl, and groups authoritative updates into one Undo',async t=>{
  const page=await productionEditor(t);await importText(page,'N');await hoverAtom(page);
  let release,intercepted;const held=new Promise(r=>release=r),seen=new Promise(r=>intercepted=r);let first=true;
  await page.route('**/api/render',async route=>{if(first){first=false;intercepted();await held;}await route.continue();});
  await page.keyboard.press('c');await seen;
  assert.equal(await page.locator('#element-entry-preview text').textContent(),'C');assert.equal((await state(page)).document.graph.atoms[0].symbol,'N');
  await page.keyboard.press('Shift+L');assert.equal(await page.locator('#element-entry-preview text').textContent(),'Cl');release();
  await page.waitForFunction(()=>window.nivramDocument.getSnapshot().document.graph.atoms[0].symbol==='Cl');await ready(page);
  await page.keyboard.press('Control+z');await ready(page);assert.equal((await state(page)).document.graph.atoms[0].symbol,'N');
  await page.keyboard.press('Control+y');await ready(page);assert.equal((await state(page)).document.graph.atoms[0].symbol,'Cl');
});
test('expiry, all-element input, pointer movement, and focused text respect element entry boundaries',async t=>{
  const page=await productionEditor(t);await importText(page,'N');await hoverAtom(page);
  await page.keyboard.press('c');await ready(page);await new Promise(r=>setTimeout(r,510));await page.keyboard.press('l');await page.keyboard.press('i');await ready(page);
  assert.equal((await state(page)).document.graph.atoms[0].symbol,'Li');
  await hoverAtom(page);await page.keyboard.press('x');await page.keyboard.press('e');await ready(page);assert.equal((await state(page)).document.graph.atoms[0].symbol,'Xe');
  await hoverAtom(page);await page.keyboard.press('c');await ready(page);const canvas=await page.locator('#canvas').boundingBox();await page.mouse.move(canvas.x+15,canvas.y+15);await page.keyboard.press('l');await ready(page);assert.equal((await state(page)).document.graph.atoms[0].symbol,'C');
  const before=await state(page);await page.locator('#filename').fill('CL123');await page.keyboard.press('Backspace');await page.keyboard.press('n');await page.keyboard.press('3');
  assert.deepEqual((await state(page)).document,before.document);assert.equal(await page.locator('#filename').inputValue(),'CL12n3');
});
test('Undo during the first pending symbol cancels it without undoing an earlier document',async t=>{
  const page=await productionEditor(t);await importText(page,'N');await hoverAtom(page);
  let release,seen;const held=new Promise(r=>release=r),received=new Promise(r=>seen=r);
  await page.route('**/api/render',async route=>{seen();await held;await route.continue().catch(()=>{});});
  await page.keyboard.press('c');await received;await page.keyboard.press('Control+z');release();await ready(page);
  assert.equal((await state(page)).document.graph.atoms[0].symbol,'N');assert.equal(await page.locator('#element-entry-preview text').count(),0);
});
