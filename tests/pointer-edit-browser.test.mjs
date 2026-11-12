import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,ready,state,tabTo,press,choose} from './editor-browser-fixture.mjs';

// All changes below use actual mouse/keyboard input. Evaluation only reads
// production state, target/selection overlays, geometry, and focus.
async function importStructure(page,source){
  await page.locator('#import-button').click();
  await page.locator('#structure-input').click();
  await page.keyboard.press('Control+A');await page.keyboard.type(source);
  await page.locator('#load-structure').click();await ready(page);
  assert.equal(await page.locator('#import-dialog').evaluate(el=>el.open),false);
  if(!await page.locator('#keyboard-editor').evaluate(el=>el.open))await press(page,'keyboard-summary');
}
async function atomPoint(page,id){
  await choose(page,'keyboard-target','atom:'+id);
  return page.evaluate(()=>{
    const circle=document.querySelector('#keyboard-target-overlay circle');
    if(!circle)throw new Error('The production target overlay has no atom.');
    const svg=document.getElementById('canvas'),matrix=svg.getScreenCTM();
    const x=Number(circle.getAttribute('cx')),y=Number(circle.getAttribute('cy'));
    return {x:matrix.a*x+matrix.c*y+matrix.e,y:matrix.b*x+matrix.d*y+matrix.f};
  });
}
async function tool(page,name){await page.locator(`[data-tool="${name}"]`).click();}
async function drag(page,start,end){
  await page.mouse.move(start.x,start.y);await page.mouse.down();
  await page.mouse.move(end.x,end.y,{steps:5});await page.mouse.up();await ready(page);
}
async function selection(page){return page.locator('#selection').innerHTML();}
async function assertEmptyGesture(page){assert.equal(await page.locator('#gesture > *').count(),0);}
function nonCoordinates(graph){return {...graph,atoms:graph.atoms.map(({x,y,...atom})=>atom)};}
function near(actual,expected,label){assert.ok(Math.abs(actual-expected)<1e-9,`${label}: ${actual} versus ${expected}`);}

test('pointer valence rejection preserves the document, cancels a pending keyboard bond, and permits the next valid edit',{timeout:150000},async t=>{
  const page=await productionEditor(t);await importStructure(page,'C(C)(C)(C)C');
  const initial=(await state(page)).document.graph;
  const degree=id=>initial.bonds.filter(b=>b.begin===id||b.end===id).length;
  const center=initial.atoms.find(a=>degree(a.id)===4),terminal=initial.atoms.find(a=>degree(a.id)===1);
  assert.ok(center&&terminal);
  const centerPoint=await atomPoint(page,center.id);
  await press(page,'keyboard-select');await press(page,'keyboard-start-bond');
  assert.equal(await page.isDisabled('#keyboard-finish-bond'),false);
  await tool(page,'bond');
  assert.equal(await page.isDisabled('#keyboard-finish-bond'),false,'Choosing a tool alone must leave the keyboard bond pending.');
  const before=await state(page),artwork=await page.locator('#drawing').innerHTML(),selected=await selection(page);
  await drag(page,centerPoint,{x:centerPoint.x,y:centerPoint.y-90});
  assert.match(await page.textContent('#toast'),/invalid valence/i);
  assert.deepEqual(await state(page),before);assert.equal(await page.locator('#drawing').innerHTML(),artwork);
  assert.equal(await selection(page),selected);await assertEmptyGesture(page);
  assert.equal(await page.isDisabled('#keyboard-finish-bond'),true);
  assert.equal(await page.locator('#valence-warning line').count(),1);
  assert.equal(await page.locator('#valence-warning line').getAttribute('stroke'),'#dc2626');
  assert.equal(await page.locator('#valence-warning').getAttribute('aria-label'),'Rejected bond: invalid valence');
  t.diagnostic('Real fifth-bond valence rejection retained document, history, artwork and selection; pending keyboard bond cancelled.');
  const endpoint=await atomPoint(page,terminal.id),origin=await atomPoint(page,center.id);
  const length=Math.hypot(endpoint.x-origin.x,endpoint.y-origin.y);
  await drag(page,endpoint,{x:endpoint.x+65*(endpoint.x-origin.x)/length,y:endpoint.y+65*(endpoint.y-origin.y)/length});
  assert.equal(await page.locator('#valence-warning > *').count(),0);
  const after=await state(page);assert.equal(after.document.graph.atoms.length,6);assert.equal(after.document.graph.bonds.length,5);
  await page.locator('#undo-button').click();await ready(page);assert.deepEqual((await state(page)).document,before.document);
});

test('pointer no-op bond editing and whole-fragment movement preserve stereo, isotope and charge annotations',{timeout:150000},async t=>{
  const page=await productionEditor(t);await importStructure(page,'[13CH3][C@@H](O)/C=C/[C@H](F)C(=O)[O-]');
  const before=await state(page),graph=before.document.graph;
  assert.ok(graph.atoms.some(a=>a.isotope===13));assert.ok(graph.atoms.some(a=>a.charge===-1));
  assert.ok(graph.atoms.some(a=>a.chiralTag));assert.ok(graph.bonds.some(b=>b.stereo));
  const edge=graph.bonds.find(b=>b.order===2&&b.stereo),a=await atomPoint(page,edge.begin),b=await atomPoint(page,edge.end);
  await choose(page,'bond-type','2');await tool(page,'bond');
  await page.mouse.click((a.x+b.x)/2,(a.y+b.y)/2);await ready(page);
  assert.deepEqual(await state(page),before,'Applying the existing bond kind must not clear E/Z or add history.');
  await press(page,'keyboard-select-all');await tool(page,'select');
  const start=await atomPoint(page,graph.atoms[0].id),selected=await selection(page);
  assert.equal(await page.locator('#selection circle').count(),graph.atoms.length);
  await drag(page,start,{x:start.x+20,y:start.y+12});
  const after=await state(page);assert.notEqual(after.revision,before.revision);
  assert.deepEqual(nonCoordinates(after.document.graph),nonCoordinates(graph));
  const dx=after.document.graph.atoms[0].x-graph.atoms[0].x,dy=after.document.graph.atoms[0].y-graph.atoms[0].y;
  assert.ok(Math.abs(dx)>0&&Math.abs(dy)>0);
  for(let i=0;i<graph.atoms.length;i++){
    near(after.document.graph.atoms[i].x-graph.atoms[i].x,dx,'Uniform x movement');
    near(after.document.graph.atoms[i].y-graph.atoms[i].y,dy,'Uniform y movement');
  }
  assert.equal(await page.locator('#selection circle').count(),graph.atoms.length);
  assert.notEqual(await selection(page),selected);
  await page.locator('#undo-button').click();await ready(page);assert.deepEqual((await state(page)).document,before.document);
  await page.locator('#redo-button').click();await ready(page);assert.deepEqual((await state(page)).document,after.document);
  t.diagnostic('No-op retained E/Z; pointer translation retained all non-coordinate graph fields and exact undo/redo documents.');
});

test('pointer movement uses keyboard selection rather than inspected target and mixed deletion is undoable',{timeout:150000},async t=>{
  const page=await productionEditor(t);await importStructure(page,'CCCO');
  const imported=await state(page),[a,b,c,d]=imported.document.graph.atoms;
  const aPoint=await atomPoint(page,a.id);await press(page,'keyboard-select');
  await choose(page,'keyboard-target','atom:'+b.id);await press(page,'keyboard-toggle-select');
  await choose(page,'keyboard-target','atom:'+c.id);await tool(page,'select');
  assert.equal(await page.locator('#selection circle').count(),2);
  await drag(page,aPoint,{x:aPoint.x+18,y:aPoint.y+16});
  const moved=await state(page),byId=id=>moved.document.graph.atoms.find(a=>a.id===id);
  assert.equal(await page.inputValue('#keyboard-target'),'atom:'+c.id);
  const dx=byId(a.id).x-a.x,dy=byId(a.id).y-a.y;assert.ok(Math.abs(dx)>0&&Math.abs(dy)>0);
  near(byId(b.id).x-b.x,dx,'Selected B x');near(byId(b.id).y-b.y,dy,'Selected B y');
  assert.deepEqual(byId(c.id),c);assert.deepEqual(byId(d.id),d);
  const remote=moved.document.graph.bonds.find(edge=>[edge.begin,edge.end].includes(c.id)&&[edge.begin,edge.end].includes(d.id));
  await choose(page,'keyboard-target','bond:'+remote.id);await press(page,'keyboard-toggle-select');
  assert.equal(await page.locator('#selection circle').count(),2);assert.equal(await page.locator('#selection line').count(),1);
  await tabTo(page,'canvas');await page.keyboard.press('Delete');await ready(page);
  const deleted=await state(page);assert.deepEqual(deleted.document.graph.atoms.map(x=>x.id),[c.id,d.id]);
  assert.deepEqual(deleted.document.graph.bonds,[]);await assertEmptyGesture(page);
  await page.locator('#undo-button').click();await ready(page);assert.deepEqual((await state(page)).document,moved.document);
  t.diagnostic('Inspected C stayed fixed while selected A/B moved; mixed atom/bond deletion and undo preserved unrelated atoms.');
});

test('Undo and Escape during a pointer drag prevent stale pointer-up commits',{timeout:150000},async t=>{
  const page=await productionEditor(t);await importStructure(page,'CCO');
  const original=await state(page),first=original.document.graph.atoms[0].id;
  let start=await atomPoint(page,first);await tool(page,'select');
  await drag(page,start,{x:start.x+18,y:start.y+14});assert.notDeepEqual((await state(page)).document,original.document);
  start=await atomPoint(page,first);await tool(page,'bond');
  await page.mouse.move(start.x,start.y);await page.mouse.down();await page.mouse.move(start.x,start.y-70,{steps:4});
  assert.equal(await page.locator('#gesture line').count(),1);
  await page.keyboard.press('Control+z');await ready(page);
  const undone=await state(page);assert.deepEqual(undone.document,original.document);await assertEmptyGesture(page);
  await page.mouse.up();await ready(page);assert.deepEqual(await state(page),undone,'Late pointer-up must not restore its old base graph.');
  start=await atomPoint(page,first);await tool(page,'select');
  await page.mouse.move(start.x,start.y);await page.mouse.down();await page.mouse.move(start.x+28,start.y+20,{steps:4});
  assert.ok(await page.locator('#gesture circle').count()>0);
  await page.keyboard.press('Escape');await assertEmptyGesture(page);await page.mouse.up();await ready(page);
  assert.deepEqual(await state(page),undone,'Escape followed by pointer-up must not commit movement.');
  t.diagnostic('Undo during bond drag and Escape during movement both discarded late pointer-up work.');
});

test('canvas bare-tool shortcuts ignore Alt and preserve editable and modal key handling',{timeout:150000},async t=>{
  const page=await productionEditor(t);await importStructure(page,'CCO');await tool(page,'select');
  await tabTo(page,'canvas');assert.equal(await page.locator('[data-tool="select"]').getAttribute('aria-pressed'),'true');
  await page.keyboard.press('Alt+e');
  assert.equal(await page.locator('[data-tool="select"]').getAttribute('aria-pressed'),'true','Alt+E is not the bare Erase shortcut.');
  await page.keyboard.press('e');assert.equal(await page.locator('[data-tool="erase"]').getAttribute('aria-pressed'),'true');
  await page.keyboard.press('v');assert.equal(await page.locator('[data-tool="select"]').getAttribute('aria-pressed'),'true');
  await tabTo(page,'filename');await page.keyboard.press('e');assert.equal(await page.locator('[data-tool="select"]').getAttribute('aria-pressed'),'true');
  await page.locator('#help-button').click();await page.keyboard.press('e');
  assert.equal(await page.locator('[data-tool="select"]').getAttribute('aria-pressed'),'true');
  await page.keyboard.press('Escape');assert.equal(await page.locator('#about-dialog').evaluate(el=>el.open),false);
});

test('keyboard changed-bond valence warning names only attempted bond and Escape dismisses it',{timeout:150000},async t=>{
  const page=await productionEditor(t);await importStructure(page,'C(C)(C)(C)C');
  const before=await state(page),g=before.document.graph,bond=g.bonds[0];
  await choose(page,'keyboard-target','bond:'+bond.id);await choose(page,'keyboard-bond-type','2');
  const artwork=await page.locator('#drawing').innerHTML();
  await press(page,'keyboard-edit-bond');await ready(page);
  assert.deepEqual(await state(page),before);assert.equal(await page.locator('#drawing').innerHTML(),artwork);
  assert.equal(await page.locator('#valence-warning line').count(),1);
  assert.equal(await page.locator('#valence-warning line').getAttribute('data-proposed-bond'),bond.id);
  assert.match(await page.textContent('#toast'),/invalid valence/);await assertEmptyGesture(page);
  await page.keyboard.press('Escape');assert.equal(await page.locator('#valence-warning > *').count(),0);
});

test('neutral oxygen third-bond keyboard rejection retains document and next valid terminal edit succeeds',{timeout:150000},async t=>{
  const page=await productionEditor(t);await importStructure(page,'COC');
  const before=await state(page),oxygen=before.document.graph.atoms.find(a=>a.symbol==='O');
  await choose(page,'keyboard-target','atom:'+oxygen.id);await press(page,'keyboard-start-bond');
  await press(page,'keyboard-finish-bond');await ready(page);
  assert.deepEqual(await state(page),before);assert.equal(await page.locator('#valence-warning line').count(),1);
  assert.match(await page.textContent('#toast'),/invalid valence at O/);
  const terminal=before.document.graph.atoms.find(a=>a.symbol==='C');
  await choose(page,'keyboard-target','atom:'+terminal.id);await press(page,'keyboard-start-bond');
  assert.equal(await page.locator('#valence-warning > *').count(),0);
  await press(page,'keyboard-finish-bond');await ready(page);
  assert.equal((await state(page)).document.graph.atoms.length,4);
});
