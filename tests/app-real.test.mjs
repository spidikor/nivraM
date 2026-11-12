import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor} from './editor-browser-fixture.mjs';

test('direct Windows browser route renders real RDKit stereo, isotope, ionic and radical scenes',async t=>{
  const external=[];
  const page=await productionEditor(t,{externalRequests:external});
  for(const smiles of ['CCO','[13CH3][C@@H](O)C(=O)[O-]','F/C=C/F','F/C=C\\F','[Na+].[Cl-]','Cn1c(=O)c2c(ncn2C)n(C)c1=O','[CH3]','c1cc[nH]c1']){
    await page.click('#import-button');await page.fill('#structure-input',smiles);await page.click('#load-structure');
    await page.waitForFunction(()=>document.querySelector('#request-status').textContent==='Ready'&&!document.querySelector('#import-dialog').open);
    const state=await page.evaluate(()=>window.nivramDocument.getSnapshot());
    assert.ok(state.document.graph.atoms.length>0,smiles);assert.ok(await page.locator('#drawing path, #drawing text').count(),smiles);
    assert.equal(await page.evaluate(project=>window.nivramDocument.openProject(project),state.document),true,smiles);
    assert.deepEqual((await page.evaluate(()=>window.nivramDocument.getSnapshot())).document,state.document);
  }
  const before=await page.evaluate(()=>window.nivramDocument.getSnapshot());
  await page.click('#import-button');await page.fill('#structure-input','CO(C)C');await page.click('#load-structure');
  await page.waitForFunction(()=>document.querySelector('#request-status').textContent==='Ready'&&document.querySelector('#toast').classList.contains('error'));
  assert.deepEqual(await page.evaluate(()=>window.nivramDocument.getSnapshot()),before);
  assert.deepEqual(external,[]);
});
