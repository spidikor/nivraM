import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,ready,state} from './editor-browser-fixture.mjs';

async function imported(page){
  await page.click('#import-button');await page.fill('#structure-input','CCO');await page.click('#load-structure');
  await page.waitForFunction(()=>!document.getElementById('import-dialog').open);await ready(page);
  await page.locator('#margin-details > summary').click();
}
async function completeState(page){
  return page.evaluate(()=>({snapshot:window.nivramDocument.getSnapshot(),drawing:document.getElementById('drawing').outerHTML,
    undoDisabled:document.getElementById('undo-button').disabled,redoDisabled:document.getElementById('redo-button').disabled}));
}
async function decodedDownload(page,download){
  assert.equal(await download.failure(),null,'The real Export PNG action must complete its browser download.');
  const stream=await download.createReadStream();assert.ok(stream);
  const chunks=[];for await(const chunk of stream)chunks.push(chunk);
  const bytes=Buffer.concat(chunks);assert.deepEqual([...bytes.subarray(0,8)],[137,80,78,71,13,10,26,10]);
  return page.evaluate(async data=>{
    const image=new Image();image.src='data:image/png;base64,'+data;await image.decode();
    const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
    const context=canvas.getContext('2d');context.drawImage(image,0,0);
    const pixels=context.getImageData(0,0,canvas.width,canvas.height).data;
    let left=canvas.width,top=canvas.height,right=-1,bottom=-1,nonOpaque=0;
    for(let y=0;y<canvas.height;y++)for(let x=0;x<canvas.width;x++){
      const i=(y*canvas.width+x)*4;if(pixels[i+3]!==255)nonOpaque++;
      if(pixels[i]!==255||pixels[i+1]!==255||pixels[i+2]!==255){left=Math.min(left,x);right=Math.max(right,x);top=Math.min(top,y);bottom=Math.max(bottom,y);}
    }
    const result={width:canvas.width,height:canvas.height,nonOpaque,ink:right>=left,
      left,right:canvas.width-right-1,top,bottom:canvas.height-bottom-1};
    image.removeAttribute('src');canvas.width=canvas.height=1;return result;
  },bytes.toString('base64'));
}

test('incomplete and invalid margin entries preserve the complete document and refuse PNG/project downloads; explicit zero remains supported',{timeout:90000},async t=>{
  const page=await productionEditor(t);await imported(page);
  await page.fill('#export-width','320');await page.selectOption('#margin-mode','pixels');await page.fill('#margin-value','7.25');
  const baseline=await completeState(page),downloads=[];page.on('download',download=>downloads.push(download));
  assert.deepEqual(baseline.snapshot.document.export,{width:320,marginMode:'pixels',margin:7.25,transparentBackground:false});
  async function rejected(label){
    assert.deepEqual(await completeState(page),baseline,label+' must not change the document, history or drawing.');
    assert.match(await page.locator('#margin-summary').textContent(),/Margin must be a nonnegative finite number/);
    const count=downloads.length;
    await page.click('#export-button');await ready(page);
    assert.equal(await page.locator('#export-result').textContent(),'No PNG created.');
    assert.match(await page.locator('#toast').textContent(),/Margin must be a nonnegative finite number/);
    assert.deepEqual(await completeState(page),baseline,label+' export refusal must preserve the complete state.');
    await page.click('#save-button');await ready(page);
    assert.equal(await page.locator('#toast').evaluate(node=>node.classList.contains('error')),true);
    assert.deepEqual(await completeState(page),baseline,label+' project refusal must preserve the complete state.');
    assert.equal(downloads.length,count,label+' must not dispatch either download.');
  }

  await page.fill('#margin-value','');await rejected('Empty margin');
  await page.selectOption('#margin-mode','percent');await rejected('Changing units while margin is empty');
  await page.selectOption('#margin-mode','pixels');assert.deepEqual(await completeState(page),baseline);
  for(const value of ['-1','1e309']){
    await page.fill('#margin-value',value);await rejected(value==='-1'?'Negative margin':'Nonfinite margin');
  }
  // A single exponent key exercises the browser's incomplete-number state,
  // without a valid intermediate keystroke committing a different value.
  await page.fill('#margin-value','');await page.locator('#margin-value').press('e');
  assert.equal(await page.locator('#margin-value').evaluate(node=>node.validity.badInput),true);
  await rejected('Incomplete exponent');
  await page.fill('#margin-value','7.25');assert.deepEqual(await completeState(page),baseline,'Restoring the previous entry adds no history.');

  await page.fill('#margin-value','0');
  const zero=await completeState(page);
  assert.deepEqual(zero.snapshot.document.export,{width:320,marginMode:'pixels',margin:0,transparentBackground:false});
  assert.equal(zero.snapshot.revision,baseline.snapshot.revision+1);
  assert.deepEqual(zero.snapshot.document.graph,baseline.snapshot.document.graph);
  assert.deepEqual(zero.snapshot.document.style,baseline.snapshot.document.style);assert.equal(zero.drawing,baseline.drawing);
  const [download]=await Promise.all([page.waitForEvent('download'),page.click('#export-button')]);
  const metrics=await decodedDownload(page,download);await ready(page);
  assert.equal(metrics.width,320);assert.equal(metrics.nonOpaque,0);assert.equal(metrics.ink,true);
  assert.equal(metrics.top,0);assert.equal(metrics.bottom,0);
  assert.ok(metrics.left<=1&&metrics.right<=1,JSON.stringify(metrics));
  assert.match(await page.locator('#export-result').textContent(),/0 px margins/);
  assert.deepEqual(await completeState(page),zero,'A successful zero-margin download must not mutate the document or history.');
  assert.equal(downloads.length,1);t.diagnostic(JSON.stringify({zeroMargin:metrics,refusedEntries:5}));
});

test('a loaded valid fractional margin keeps its exact setting and exports rounded pixel padding without rewriting the project',{timeout:90000},async t=>{
  const page=await productionEditor(t);await imported(page);
  const project=(await state(page)).document;project.export={width:333,marginMode:'pixels',margin:7.25};
  await page.locator('#file-input').setInputFiles({name:'fractional-margin.nivram',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(project))});
  await page.waitForFunction(()=>document.getElementById('filename').value==='fractional-margin');await ready(page);
  const before=await completeState(page);
  assert.deepEqual(before.snapshot.document,project);assert.equal(before.snapshot.dirty,false);
  assert.equal(await page.inputValue('#margin-value'),'7.25');
  assert.equal(await page.locator('#margin-value').evaluate(node=>node.validity.stepMismatch),true,'This saved value is valid even though it lies between the control increments.');
  assert.match(await page.locator('#margin-summary').textContent(),/^8 px white margin on every side$/);
  const [download]=await Promise.all([page.waitForEvent('download'),page.click('#export-button')]);
  const metrics=await decodedDownload(page,download);await ready(page);
  assert.equal(metrics.width,333);assert.equal(metrics.nonOpaque,0);assert.equal(metrics.ink,true);
  assert.equal(metrics.top,8);assert.equal(metrics.bottom,8);
  assert.ok(metrics.left>=8&&metrics.left<=9&&metrics.right>=8&&metrics.right<=9,JSON.stringify(metrics));
  assert.deepEqual(await completeState(page),before);
  assert.equal(await page.inputValue('#margin-value'),'7.25');t.diagnostic(JSON.stringify({loadedFractionalMargin:metrics,retainedMargin:7.25}));
});
