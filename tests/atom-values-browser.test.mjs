import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,ready,state} from './editor-browser-fixture.mjs';

async function imported(page,smiles){
  await page.click('#import-button');await page.fill('#structure-input',smiles);await page.click('#load-structure');
  await page.waitForFunction(()=>!document.getElementById('import-dialog').open);await ready(page);
  await page.locator('#keyboard-summary').click();
}
async function selected(page,id){
  await page.selectOption('#keyboard-target','atom:'+id);await page.click('#keyboard-select');
  assert.equal(await page.locator('#apply-atom').isDisabled(),false);
}
async function completeState(page){
  return page.evaluate(()=>({snapshot:window.nivramDocument.getSnapshot(),drawing:document.getElementById('drawing').outerHTML,
    selection:document.getElementById('selection').innerHTML,viewBox:document.getElementById('canvas').getAttribute('viewBox'),
    undoDisabled:document.getElementById('undo-button').disabled,redoDisabled:document.getElementById('redo-button').disabled}));
}
async function apply(page){await page.click('#apply-atom');await ready(page);}
async function identity(page,smiles){await page.waitForFunction(expected=>document.getElementById('property-smiles').value===expected,smiles);}
function changedDocument(document,id,fields){
  const expected=structuredClone(document);Object.assign(expected.graph.atoms.find(atom=>atom.id===id),fields);return expected;
}

test('charge/isotope incomplete entries reject atomically without neutralizing a valid ion or removing its isotope; explicit zeros remain undoable',{timeout:90000},async t=>{
  const page=await productionEditor(t);await imported(page,'N');
  const id=(await state(page)).document.graph.atoms[0].id;await selected(page,id);
  await page.fill('#atom-charge','1');await page.fill('#atom-isotope','15');await apply(page);await identity(page,'[15NH4+]');
  const baseline=await completeState(page),atom=baseline.snapshot.document.graph.atoms[0];
  assert.equal(atom.charge,1);assert.equal(atom.isotope,15);assert.equal(atom.explicitHs,0);assert.equal(atom.noImplicit,false);assert.equal(atom.radicals,0);
  let renderRequests=0;page.on('request',request=>{if(new URL(request.url()).pathname==='/api/render')renderRequests++;});
  let refused=0;
  for(const field of ['charge','isotope']){
    const other=field==='charge'?'isotope':'charge';
    for(const invalid of ['empty','incomplete','1e309',field==='charge'?'9':'401','0.5']){
      await page.fill('#atom-charge','1');await page.fill('#atom-isotope','15');
      // Give the other field a valid different value. Neither field may commit
      // when the current entry is missing, malformed or outside the contract.
      await page.fill('#atom-'+other,other==='charge'?'0':'16');
      const input=page.locator('#atom-'+field);
      if(invalid==='empty')await input.fill('');
      else if(invalid==='incomplete'){
        await input.fill('');await input.press(field==='charge'?'-':'e');
        assert.equal(await input.evaluate(node=>node.validity.badInput),true);
      }else await input.fill(invalid);
      const beforeRequests=renderRequests;await apply(page);
      assert.equal(await page.locator('#toast').evaluate(node=>node.classList.contains('error')),true,field+' '+invalid);
      assert.match(await page.locator('#toast').textContent(),/finite number|Invalid atom (charge|isotope)/);
      assert.deepEqual(await completeState(page),baseline,field+' '+invalid+' must preserve document, history, selection and artwork.');
      assert.equal(renderRequests,beforeRequests,'Invalid atom input must reject before requesting a chemical render.');refused++;
    }
  }
  await page.fill('#atom-charge','1');await page.fill('#atom-isotope','15');
  assert.deepEqual(await completeState(page),baseline,'Restoring the displayed values adds no history.');

  await page.fill('#atom-isotope','0');await apply(page);await identity(page,'[NH4+]');
  let after=await state(page);
  assert.equal(after.revision,baseline.snapshot.revision+1);
  assert.deepEqual(after.document,changedDocument(baseline.snapshot.document,id,{isotope:0}));
  await page.click('#undo-button');await ready(page);await identity(page,'[15NH4+]');
  assert.deepEqual((await state(page)).document,baseline.snapshot.document,'One Undo restores the intentionally removed isotope.');
  await page.click('#redo-button');await ready(page);await identity(page,'[NH4+]');
  assert.deepEqual((await state(page)).document,after.document);
  await page.click('#undo-button');await ready(page);await identity(page,'[15NH4+]');await selected(page,id);

  const beforeCharge=await state(page);
  await page.fill('#atom-charge','0');await apply(page);await identity(page,'[15NH3]');
  after=await state(page);assert.equal(after.revision,beforeCharge.revision+1);
  assert.deepEqual(after.document,changedDocument(baseline.snapshot.document,id,{charge:0}));
  await page.click('#undo-button');await ready(page);await identity(page,'[15NH4+]');
  assert.deepEqual((await state(page)).document,baseline.snapshot.document,'One Undo restores the intentionally neutralized atom.');
  await selected(page,id);const beforeBoth=await state(page);
  await page.fill('#atom-charge','0');await page.fill('#atom-isotope','0');await apply(page);await identity(page,'N');
  after=await state(page);assert.equal(after.revision,beforeBoth.revision+1);
  assert.deepEqual(after.document,changedDocument(baseline.snapshot.document,id,{charge:0,isotope:0}));
  await page.click('#undo-button');await ready(page);await identity(page,'[15NH4+]');
  assert.deepEqual((await state(page)).document,baseline.snapshot.document,'Clearing both annotations explicitly remains one atomic Undo.');
  t.diagnostic(JSON.stringify({refusedEntries:refused,baseline:'[15NH4+]',explicitChargeZero:'[15NH3]',explicitIsotopeZero:'[NH4+]',explicitBothZero:'N'}));
});

test('clearing an imported carbon isotope requires explicit zero; an empty entry preserves its complete molecular graph',{timeout:60000},async t=>{
  const page=await productionEditor(t);await imported(page,'[13CH3]CO');
  const graph=(await state(page)).document.graph,id=graph.atoms.find(atom=>atom.isotope===13).id;
  await selected(page,id);const baseline=await completeState(page);
  await page.fill('#atom-isotope','');await apply(page);
  assert.equal(await page.locator('#toast').evaluate(node=>node.classList.contains('error')),true);
  assert.deepEqual(await completeState(page),baseline);
  await page.fill('#atom-isotope','0');await apply(page);await identity(page,'CCO');
  const after=await state(page);assert.equal(after.revision,baseline.snapshot.revision+1);
  assert.deepEqual(after.document,changedDocument(baseline.snapshot.document,id,{isotope:0}));
  await page.click('#undo-button');await ready(page);await identity(page,'[13CH3]CO');
  assert.deepEqual((await state(page)).document,baseline.snapshot.document);
});
