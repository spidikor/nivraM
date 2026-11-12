import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,ready,state} from './editor-browser-fixture.mjs';

async function load(page,text){
  await page.click('#import-button');await page.fill('#structure-input',text);await page.click('#load-structure');
  await page.waitForFunction(()=>!document.getElementById('import-dialog').open);await ready(page);
}
async function menu(page){
  await page.locator('#canvas').click({button:'right',position:{x:8,y:8}});
  await page.waitForSelector('#canvas-context-menu:not([hidden])',{timeout:3000});
}
const smiles=page=>page.evaluate(async()=>{
  const s=window.nivramDocument.getSnapshot();
  const r=await fetch('/api/export-smiles',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:s.document.graph})});
  if(!r.ok)throw new Error('Identity readback failed');return (await r.json()).text;
});
test('right-click flip and mirror change real coordinates with distinct stereo semantics and undo', {timeout:90000},async t=>{
  const page=await productionEditor(t);await load(page,'N[C@@H](C)C(=O)O');
  const before=await state(page),identity=await smiles(page);
  await menu(page);assert.deepEqual(await page.locator('#canvas-context-menu [role=menuitem]').evaluateAll(items=>items.map(item=>item.id)),['canvas-flip-horizontal','canvas-flip-vertical','canvas-mirror-horizontal','canvas-mirror-vertical','canvas-rotate-ccw','canvas-rotate-cw']);
  await page.click('#canvas-flip-horizontal');await ready(page);
  const flipped=await state(page);assert.equal(flipped.revision,before.revision+1);
  assert.equal(await smiles(page),identity);assert.notDeepEqual(flipped.document.graph,before.document.graph);
  const xs=before.document.graph.atoms.map(a=>a.x),pivot=Math.min(...xs)+Math.max(...xs);
  for(const atom of flipped.document.graph.atoms){const old=before.document.graph.atoms.find(a=>a.id===atom.id);assert.ok(Math.abs(atom.x-(pivot-old.x))<1e-6);assert.equal(atom.y,old.y);}
  await page.click('#undo-button');await ready(page);assert.deepEqual((await state(page)).document,before.document);
  await menu(page);await page.click('#canvas-mirror-vertical');await ready(page);
  const mirrored=await state(page);assert.equal(mirrored.revision,before.revision+3);assert.notEqual(await smiles(page),identity);
  const ys=before.document.graph.atoms.map(a=>a.y),yp=Math.min(...ys)+Math.max(...ys);
  for(const atom of mirrored.document.graph.atoms){const old=before.document.graph.atoms.find(a=>a.id===atom.id);assert.ok(Math.abs(atom.y-(yp-old.y))<1e-6);assert.equal(atom.x,old.x);}
  await page.click('#undo-button');await ready(page);assert.deepEqual((await state(page)).document,before.document);
  await menu(page);await page.click('#canvas-rotate-ccw');await ready(page);
  assert.equal(await smiles(page),identity);assert.notDeepEqual((await state(page)).document.graph,before.document.graph);
  await page.click('#undo-button');await ready(page);assert.deepEqual((await state(page)).document,before.document);
});
test('keyboard context menu, Escape, empty drawing and failed reflection preserve state',{timeout:90000},async t=>{
  const page=await productionEditor(t);const empty=await state(page);await menu(page);
  assert.equal(await page.locator('#canvas-context-menu button:disabled').count(),6);
  await page.keyboard.press('Escape');assert.deepEqual(await state(page),empty);
  await load(page,'CCO');await page.locator('#canvas').focus();await page.keyboard.press('Shift+F10');
  await page.waitForSelector('#canvas-context-menu:not([hidden])');assert.equal(await page.evaluate(()=>document.activeElement.id),'canvas-flip-horizontal');
  await page.keyboard.press('ArrowDown');assert.equal(await page.evaluate(()=>document.activeElement.id),'canvas-flip-vertical');
  await page.keyboard.press('End');assert.equal(await page.evaluate(()=>document.activeElement.id),'canvas-rotate-cw');
  await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.id),'canvas');
  const before=await state(page);
  await page.route('**/api/reflect',route=>route.fulfill({status:422,contentType:'application/json',body:JSON.stringify({error:'Synthetic rejected reflection'})}));
  await menu(page);await page.click('#canvas-mirror-horizontal');await ready(page);
  await page.waitForFunction(()=>document.getElementById('toast').textContent.includes('Synthetic rejected reflection'));
  assert.deepEqual(await state(page),before);
});

test('all four menu actions work on a selected molecule and retain the other fragment',{timeout:90000},async t=>{
  const page=await productionEditor(t);await load(page,'N[C@@H](C)C(=O)O.CCO');
  await page.click('#keyboard-summary');
  const before=await state(page),atoms=before.document.graph.atoms;
  await page.selectOption('#keyboard-target','atom:'+atoms[0].id);await page.click('#keyboard-select');
  // A real right-click on an unselected bond targets its connected molecule.
  const bondPoint=await page.locator('#drawing .bond-2').first().evaluate(path=>{
    const midpoint=path.getPointAtLength(path.getTotalLength()/2);
    const point=new DOMPoint(midpoint.x,midpoint.y).matrixTransform(path.getScreenCTM());
    return {x:point.x,y:point.y};
  });
  await page.mouse.click(bondPoint.x,bondPoint.y,{button:'right'});
  await page.waitForSelector('#canvas-context-menu:not([hidden])');
  assert.match(await page.textContent('#canvas-context-scope'),/^6 selected atoms$/);
  await page.keyboard.press('Escape');
  // Keyboard selection can select every atom of the first component explicitly.
  for(const atom of atoms.slice(1,6)){
    await page.selectOption('#keyboard-target','atom:'+atom.id);await page.click('#keyboard-toggle-select');
  }
  const identity=await smiles(page);
  for(const mode of ['flip','mirror'])for(const axis of ['horizontal','vertical']){
    await menu(page);assert.match(await page.textContent('#canvas-context-scope'),/^6 selected atoms$/);
    await page.click(`#canvas-${mode}-${axis}`);await ready(page);
    const after=await state(page);
    for(const atom of atoms.slice(6))assert.deepEqual(after.document.graph.atoms.find(a=>a.id===atom.id),atom);
    assert.equal((await smiles(page))===identity,mode==='flip');
    await page.click('#undo-button');await ready(page);assert.deepEqual((await state(page)).document,before.document);
    // Undo intentionally clears selection; restore the tested explicit scope.
    for(const [index,atom] of atoms.slice(0,6).entries()){
      await page.selectOption('#keyboard-target','atom:'+atom.id);await page.click(index===0?'#keyboard-select':'#keyboard-toggle-select');
    }
  }
});
