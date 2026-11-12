import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,ready,state} from './editor-browser-fixture.mjs';

test('changing from Erase to Select cancels the active marquee before release', {timeout:90000}, async t=>{
  const page=await productionEditor(t);
  await load(page,'CCCC');
  const before=await state(page);
  const layout=await rendered(page);
  await page.click('[data-tool="erase"]');
  await beginMarquee(page,region(layout.positions));
  assert.deepEqual((await state(page)).document,before.document);
  await page.keyboard.press('v');
  assert.equal(await page.locator('[data-tool="select"]').getAttribute('aria-pressed'),'true');
  await page.mouse.up();
  await ready(page);
  assert.deepEqual(await state(page),before);
});

test('bond shortcut rechecks hover after Alt-pan without an extra pointer move', {timeout:90000}, async t=>{
  const page=await productionEditor(t);
  await load(page,'CC');
  await page.click('[data-tool="select"]');
  const before=await state(page);
  const layout=await rendered(page);
  const [a,b]=layout.positions;
  const midpoint={x:(a.screenX+b.screenX)/2,y:(a.screenY+b.screenY)/2};
  await page.keyboard.down('Alt');
  await page.mouse.move(midpoint.x-120,midpoint.y);
  await page.mouse.down();
  await page.mouse.move(midpoint.x,midpoint.y);
  await page.mouse.up();
  await page.keyboard.up('Alt');
  // The final move hit the old bond position before panning changed its location.
  // Keep the pointer stationary so the key must use the current view transform.
  await page.keyboard.press('2');
  await ready(page);
  assert.deepEqual(await state(page),before);
  assert.equal(await page.locator('[data-tool="bond"]').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('#bond-type').inputValue(),'2');
});

const close=(actual,expected,tolerance=1e-6)=>assert.ok(Math.abs(actual-expected)<=tolerance,`${actual} != ${expected}`);
async function load(page,text){
  await page.click('#import-button');await page.fill('#structure-input',text);await page.click('#load-structure');
  await page.waitForFunction(()=>!document.getElementById('import-dialog').open);await ready(page);
}
async function rendered(page){
  // Read-only projection through the production renderer; no document/UI mutation.
  return page.evaluate(async()=>{
    const {document:project}=window.nivramDocument.getSnapshot();
    const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:project.graph,style:project.style})});
    if(!response.ok)throw new Error('Read-only renderer inspection failed.');
    const result=await response.json(),canvas=document.getElementById('canvas'),matrix=canvas.getScreenCTM();
    return {positions:result.scene.positions.map(atom=>{
      const point=new DOMPoint(atom.x,atom.y).matrixTransform(matrix);return {...atom,screenX:point.x,screenY:point.y};
    }),transform:result.scene.transform,smiles:result.scene.smiles,view:canvas.getAttribute('viewBox'),rect:{width:canvas.getBoundingClientRect().width,height:canvas.getBoundingClientRect().height}};
  });
}
async function edit(page,selector){
  const before=(await state(page)).revision;await page.click(selector);
  await page.waitForFunction(revision=>window.nivramDocument.getSnapshot().revision>revision,before);await ready(page);
}
async function changed(page,before){await page.waitForFunction(revision=>window.nivramDocument.getSnapshot().revision>revision,before.revision);await ready(page);}
async function keyboardPanel(page){if(!await page.locator('#keyboard-editor').evaluate(node=>node.open))await page.click('#keyboard-summary');}
async function selectAtoms(page,ids){
  await keyboardPanel(page);
  for(const [index,id] of ids.entries()){
    await page.selectOption('#keyboard-target','atom:'+id);await page.click(index===0?'#keyboard-select':'#keyboard-toggle-select');
  }
}
function region(atoms,padding=20){
  return {x1:Math.min(...atoms.map(a=>a.screenX))-padding,y1:Math.min(...atoms.map(a=>a.screenY))-padding,
    x2:Math.max(...atoms.map(a=>a.screenX))+padding,y2:Math.max(...atoms.map(a=>a.screenY))+padding};
}
async function beginMarquee(page,area){
  await page.mouse.move(area.x1,area.y1);await page.mouse.down();await page.mouse.move(area.x2,area.y2,{steps:6});
}
function worldView(layout){
  const [x,y,w,h]=layout.view.split(/\s+/).map(Number),tr=layout.transform;
  return {x:(x-tr.x0)/tr.xScale,y:(y-tr.y0)/tr.yScale,w:w/tr.xScale,h:h/tr.yScale};
}
function medianDisplayedBond(layout,graph){
  const atoms=new Map(layout.positions.map(a=>[a.id,a]));
  const lengths=graph.bonds.map(b=>{const a=atoms.get(b.begin),c=atoms.get(b.end);return Math.hypot(a.screenX-c.screenX,a.screenY-c.screenY);}).sort((a,b)=>a-b);
  const i=Math.floor(lengths.length/2);return lengths.length%2?lengths[i]:(lengths[i-1]+lengths[i])/2;
}

test('thirty-degree toolbar rotations complete a full turn with production chemistry and undo',{timeout:90000},async t=>{
  const page=await productionEditor(t);await load(page,'N[C@@H](C)C(=O)O');
  const before=await state(page),identity=(await rendered(page)).smiles;
  for(let step=0;step<12;step++)await edit(page,'#rotate-cw');
  const after=await state(page);assert.equal(after.revision,before.revision+12);
  assert.deepEqual(after.document.graph.bonds,before.document.graph.bonds);
  for(const atom of after.document.graph.atoms){
    const old=before.document.graph.atoms.find(a=>a.id===atom.id);
    close(atom.x,old.x);close(atom.y,old.y);
    assert.deepEqual({...atom,x:0,y:0},{...old,x:0,y:0});
  }
  assert.equal((await rendered(page)).smiles,identity);
  await edit(page,'#rotate-ccw');const rotated=await state(page);
  assert.notDeepEqual(rotated.document.graph.atoms,before.document.graph.atoms);
  await edit(page,'#undo-button');
  assert.deepEqual((await state(page)).document,after.document);
});

test('rotation affects the exact selected atom subset and retains other atoms and fragments',{timeout:90000},async t=>{
  const page=await productionEditor(t);await load(page,'CCCC.CC');
  const before=await state(page),atoms=before.document.graph.atoms,ids=atoms.slice(0,2).map(a=>a.id);
  await selectAtoms(page,ids);await edit(page,'#rotate-ccw');const after=await state(page);
  for(const old of atoms.slice(2))assert.deepEqual(after.document.graph.atoms.find(a=>a.id===old.id),old);
  const pivot={x:(atoms[0].x+atoms[1].x)/2,y:(atoms[0].y+atoms[1].y)/2},angle=Math.PI/6;
  for(const old of atoms.slice(0,2)){
    const atom=after.document.graph.atoms.find(a=>a.id===old.id),dx=old.x-pivot.x,dy=old.y-pivot.y;
    close(atom.x,pivot.x+dx*Math.cos(angle)-dy*Math.sin(angle));
    close(atom.y,pivot.y+dx*Math.sin(angle)+dy*Math.cos(angle));
  }
  assert.deepEqual(after.document.graph.bonds,before.document.graph.bonds);
  await edit(page,'#undo-button');assert.deepEqual((await state(page)).document,before.document);
});

test('erase marquee previews, deletes only enclosed atoms on release, cancels with Escape, and undoes once',{timeout:90000},async t=>{
  const page=await productionEditor(t);await load(page,'CCCC');
  const before=await state(page),layout=await rendered(page),left=[...layout.positions].sort((a,b)=>a.screenX-b.screenX).slice(0,2),area=region(left);
  const chosen=new Set(layout.positions.filter(a=>a.screenX>=area.x1&&a.screenX<=area.x2&&a.screenY>=area.y1&&a.screenY<=area.y2).map(a=>a.id));
  assert.equal(chosen.size,2);
  await page.click('[data-tool="erase"]');await beginMarquee(page,area);
  assert.deepEqual((await state(page)).document,before.document,'Pointer move must not erase before release.');
  assert.equal(await page.locator('#gesture circle').count(),2);
  await page.mouse.up();await changed(page,before);const erased=await state(page);
  assert.equal(erased.revision,before.revision+1);
  assert.deepEqual(erased.document.graph.atoms,before.document.graph.atoms.filter(a=>!chosen.has(a.id)));
  assert.deepEqual(erased.document.graph.bonds,before.document.graph.bonds.filter(b=>!chosen.has(b.begin)&&!chosen.has(b.end)));
  await edit(page,'#undo-button');assert.deepEqual((await state(page)).document,before.document);
  const restored=await state(page),current=await rendered(page);await beginMarquee(page,region(current.positions));
  assert.ok(await page.locator('#gesture circle').count()>0);await page.keyboard.press('Escape');await page.mouse.up();await ready(page);
  assert.deepEqual(await state(page),restored);assert.equal(await page.locator('#gesture > *').count(),0);
  const canvas=await page.locator('#canvas').boundingBox();
  await beginMarquee(page,{x1:canvas.x+12,y1:canvas.y+12,x2:canvas.x+40,y2:canvas.y+40});await page.mouse.up();await ready(page);
  assert.deepEqual(await state(page),restored,'Empty marquee must have no history entry.');
});

test('ordinary erase removes a bond only, and one marquee can erase disconnected structures',{timeout:90000},async t=>{
  const page=await productionEditor(t);await load(page,'CC.CC');
  const before=await state(page),layout=await rendered(page),bond=before.document.graph.bonds[0],positions=new Map(layout.positions.map(a=>[a.id,a]));
  const a=positions.get(bond.begin),b=positions.get(bond.end);await page.click('[data-tool="erase"]');
  await page.mouse.click((a.screenX+b.screenX)/2,(a.screenY+b.screenY)/2);await changed(page,before);
  const erased=await state(page);assert.equal(erased.document.graph.atoms.length,4);assert.equal(erased.document.graph.bonds.length,1);
  assert.ok(!erased.document.graph.bonds.some(edge=>edge.id===bond.id));
  await edit(page,'#undo-button');assert.deepEqual((await state(page)).document,before.document);
  const restored=await state(page),current=await rendered(page);await beginMarquee(page,region(current.positions));await page.mouse.up();await changed(page,restored);
  assert.deepEqual((await state(page)).document.graph,{atoms:[],bonds:[]});
  await edit(page,'#undo-button');assert.deepEqual((await state(page)).document,before.document);
});

test('Delete and Backspace erase selected atoms while text and empty selections remain safe',{timeout:90000},async t=>{
  const page=await productionEditor(t);await load(page,'CCO');
  const before=await state(page);await selectAtoms(page,before.document.graph.atoms.map(a=>a.id));
  await page.fill('#filename','typed');await page.keyboard.press('Backspace');await page.keyboard.press('Delete');
  assert.deepEqual((await state(page)).document,before.document);assert.equal(await page.inputValue('#filename'),'type');
  await page.click('#import-button');await page.fill('#structure-input','NCC');await page.keyboard.press('Control+A');await page.keyboard.press('Backspace');
  assert.equal(await page.inputValue('#structure-input'),'');assert.deepEqual((await state(page)).document,before.document);
  await page.locator('#import-dialog').getByRole('button',{name:'Cancel',exact:true}).click();
  for(const key of ['Delete','Backspace']){
    await selectAtoms(page,before.document.graph.atoms.map(a=>a.id));const selected=await state(page);
    await page.keyboard.press(key);await changed(page,selected);assert.equal((await state(page)).document.graph.atoms.length,0);
    await edit(page,'#undo-button');assert.deepEqual((await state(page)).document,before.document);
  }
  await page.click('#keyboard-clear-selection');const empty=await state(page),url=page.url();
  await page.keyboard.press('Backspace');await page.keyboard.press('Delete');await ready(page);
  assert.deepEqual(await state(page),empty);assert.equal(page.url(),url);
});

test('hovered bond number shortcuts edit that bond without switching tools and respect numeric focus',{timeout:90000},async t=>{
  const page=await productionEditor(t);await load(page,'CC');await page.click('[data-tool="select"]');
  const original=await state(page),initial=await rendered(page),[first,last]=initial.positions;
  await page.mouse.move((first.screenX+last.screenX)/2,(first.screenY+last.screenY)/2);
  // Playwright's installed keyboard layout maps bare Numpad3 to PageDown and
  // Shift+Numpad3 to numeric "3". Exercise both meanings without changing OS keys.
  await page.keyboard.press('Numpad3');await ready(page);
  assert.deepEqual((await state(page)).document,original.document,'Navigation-mode keypad input must not change chemistry.');
  for(const [key,order] of [['2',2],['Shift+Numpad3',3],['1',1]]){
    const before=await state(page),layout=await rendered(page),[a,b]=layout.positions;
    await page.mouse.move((a.screenX+b.screenX)/2,(a.screenY+b.screenY)/2);await page.keyboard.press(key);await changed(page,before);
    assert.equal((await state(page)).document.graph.bonds[0].order,order);
    assert.equal(await page.getAttribute('[data-tool="select"]','aria-pressed'),'true');
  }
  const before=await state(page),canvas=await page.locator('#canvas').boundingBox();
  await page.mouse.move(canvas.x+10,canvas.y+10);await page.keyboard.press('2');await ready(page);
  assert.equal(await page.getAttribute('[data-tool="bond"]','aria-pressed'),'true');assert.equal(await page.inputValue('#bond-type'),'2');
  assert.deepEqual((await state(page)).document.graph,before.document.graph);
  await page.fill('#export-width','3000');await page.keyboard.press('Control+A');await page.keyboard.press('3');
  assert.equal(await page.inputValue('#export-width'),'3');assert.equal(await page.inputValue('#bond-type'),'2');
  assert.deepEqual((await state(page)).document.graph,before.document.graph);
});

test('new drawing scale is calibrated and manual world view survives adding another ring',{timeout:90000},async t=>{
  const page=await productionEditor(t),canvas=await page.locator('#canvas').boundingBox();
  await page.click('[data-tool="ring"]');const empty=await state(page);
  await page.mouse.click(canvas.x+canvas.width*.55,canvas.y+canvas.height*.5);await changed(page,empty);
  const first=await state(page),layout=await rendered(page),length=medianDisplayedBond(layout,first.document.graph);
  assert.equal(first.document.graph.atoms.length,6);close(length,70.47475694468609,70.47475694468609*.05);
  await page.click('#zoom-out');const manual=worldView(await rendered(page));
  await page.mouse.click(canvas.x+canvas.width*.2,canvas.y+canvas.height*.5);await changed(page,first);
  const second=await state(page),after=worldView(await rendered(page));assert.equal(second.document.graph.atoms.length,12);
  for(const key of ['x','y','w','h'])close(after[key],manual[key]);
  await edit(page,'#rotate-cw');const rotated=worldView(await rendered(page));
  for(const key of ['x','y','w','h'])close(rotated[key],manual[key]);
});

test('tiny imports cap enlargement and large multi-fragment imports fit full rendered ink',{timeout:90000},async t=>{
  const page=await productionEditor(t);await load(page,'CC');
  const small=await state(page),layout=await rendered(page);
  assert.ok(medianDisplayedBond(layout,small.document.graph)<=70.47475694468609*1.01);
  await load(page,'CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC.[NH4+].[Cl-]');
  const ink=await page.evaluate(()=>{
    const canvas=document.getElementById('canvas'),drawing=document.getElementById('drawing'),rect=canvas.getBoundingClientRect(),box=drawing.getBBox(),matrix=canvas.getScreenCTM();
    const a=new DOMPoint(box.x,box.y).matrixTransform(matrix),b=new DOMPoint(box.x+box.width,box.y+box.height).matrixTransform(matrix);
    return {left:a.x-rect.left,top:a.y-rect.top,right:rect.right-b.x,bottom:rect.bottom-b.y};
  });
  for(const [side,padding] of Object.entries(ink))assert.ok(padding>=23.5,`${side} padding ${padding}`);
  await page.click('#zoom-in');const manual=await page.getAttribute('#canvas','viewBox');
  await page.setViewportSize({width:1100,height:700});await page.waitForTimeout(100);
  assert.equal(await page.getAttribute('#canvas','viewBox'),manual,'Manual view must survive a viewport resize.');
});
