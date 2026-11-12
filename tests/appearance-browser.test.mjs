import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {productionEditor,ready,state} from './editor-browser-fixture.mjs';

const repository=fileURLToPath(new URL('../',import.meta.url));

async function appearanceState(page){
  return page.evaluate(()=>({snapshot:window.nivramDocument.getSnapshot(),svg:document.getElementById('drawing').innerHTML,
    undoDisabled:document.getElementById('undo-button').disabled,redoDisabled:document.getElementById('redo-button').disabled}));
}

async function measureSeparation(page){
  return page.evaluate(async()=>{
    const project=window.nivramDocument.getSnapshot().document;
    const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:project.graph,style:project.style})});
    if(!response.ok)throw new Error('The actual renderer could not provide its coordinate frame.');
    const {scene}=await response.json(),[a,b]=scene.positions,dx=b.x-a.x,dy=b.y-a.y,length=Math.hypot(dx,dy);
    const paths=[...document.querySelectorAll('#drawing path.bond-0')].filter(node=>getComputedStyle(node).fill==='none');
    const projections=paths.map(node=>{const point=node.getPointAtLength(node.getTotalLength()/2);return (-dy*point.x+dx*point.y)/length;});
    return {paths:paths.length,bondLength:length,normalizedGap:(Math.max(...projections)-Math.min(...projections))/length,style:project.style};
  });
}

async function pngMetrics(page,source){
  return page.evaluate(async source=>{
    const image=new Image();image.src=source;await image.decode();
    const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
    const context=canvas.getContext('2d');context.drawImage(image,0,0);
    const pixels=context.getImageData(0,0,canvas.width,canvas.height).data,padding=13;
    let nonOpaque=0,nonwhiteMargin=0,ink=0;
    for(let y=0;y<canvas.height;y++)for(let x=0;x<canvas.width;x++){
      const i=(y*canvas.width+x)*4,white=pixels[i]===255&&pixels[i+1]===255&&pixels[i+2]===255;
      if(pixels[i+3]!==255)nonOpaque++;
      if(!white){ink++;if(x<padding||x>=canvas.width-padding||y<padding||y>=canvas.height-padding)nonwhiteMargin++;}
    }
    const digest=await crypto.subtle.digest('SHA-256',pixels);
    const result={width:canvas.width,height:canvas.height,nonOpaque,nonwhiteMargin,ink,rgbaSha256:[...new Uint8Array(digest)].map(v=>v.toString(16).padStart(2,'0')).join('')};
    image.removeAttribute('src');canvas.width=canvas.height=1;return result;
  },source);
}

async function mainPng(page){
  return page.evaluate(async()=>{
    const {exportPng}=await import('/modules/export.mjs');
    const result=await exportPng(document.getElementById('drawing'),window.nivramDocument.getSnapshot().document.export,{graph:window.nivramDocument.getSnapshot().document.graph});
    const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(result.blob);});
    return {data,width:result.width,height:result.height,padding:result.padding};
  });
}

test('multiple-bond spacing changes rendered separation without changing chemistry and remains consistent through history, profiles and PNG export',{timeout:180000},async t=>{
  const page=await productionEditor(t);
  assert.equal(await page.locator('#bond-spacing').count(),1,'The appearance panel needs the multiple-bond spacing control.');
  assert.equal(await page.locator('#bond-spacing').getAttribute('min'),'0.06');
  assert.equal(await page.locator('#bond-spacing').getAttribute('max'),'0.28');
  assert.equal(await page.locator('#bond-spacing').getAttribute('step'),'0.01');
  assert.equal(await page.inputValue('#bond-spacing'),'0.14');
  await page.click('#import-button');await page.fill('#structure-input','C=C');await page.click('#load-structure');
  await page.waitForFunction(()=>!document.getElementById('import-dialog').open);await ready(page);
  await page.fill('#export-width','640');
  const initial=await state(page),graph=initial.document.graph;
  assert.equal(graph.atoms.length,2);assert.equal(graph.bonds.length,1);assert.equal(graph.bonds[0].order,2);
  const artifacts=path.join(repository,'../checks',process.env.NIVRAM_THEME_RUN??'appearance-browser','artifacts');
  await mkdir(artifacts,{recursive:true});
  const reports={};
  async function preservePng(label){
    const before=await appearanceState(page),output=await mainPng(page),metrics=await pngMetrics(page,output.data);
    assert.equal(output.width,640);assert.equal(output.padding,13);assert.equal(metrics.width,640);
    assert.equal(metrics.nonOpaque,0);assert.equal(metrics.nonwhiteMargin,0);assert.ok(metrics.ink>0);
    assert.deepEqual(await appearanceState(page),before);
    await writeFile(path.join(artifacts,`spacing-${label}.png`),Buffer.from(output.data.split(',')[1],'base64'));
    await writeFile(path.join(artifacts,`spacing-${label}.svg`),await page.locator('#canvas').evaluate(node=>node.outerHTML));
    reports[label]={...metrics,spacing:before.snapshot.document.style.bondSpacing};return metrics;
  }
  async function apply(value){
    await page.fill('#bond-spacing',value);await page.click('#apply-style');await ready(page);
    assert.equal((await state(page)).document.style.bondSpacing,Number(value));
    assert.deepEqual((await state(page)).document.graph,graph);
    assert.deepEqual((await state(page)).document.export,initial.document.export);
    for(const key of ['bondWidth','labelSize','color'])assert.equal((await state(page)).document.style[key],initial.document.style[key]);
  }
  await preservePng('0-14');
  await apply('0.06');const narrow=await measureSeparation(page),narrowState=await appearanceState(page);
  await apply('0.28');const wide=await measureSeparation(page),wideState=await appearanceState(page);
  assert.equal(narrow.paths,2);assert.equal(wide.paths,2);assert.ok(narrow.normalizedGap>0);
  assert.ok(wide.normalizedGap>narrow.normalizedGap*2,JSON.stringify({narrow,wide}));
  const widePng=await preservePng('0-28');
  await page.click('#undo-button');await ready(page);assert.equal(await page.inputValue('#bond-spacing'),'0.06');
  assert.deepEqual((await state(page)).document,narrowState.snapshot.document);assert.equal(await page.locator('#drawing').innerHTML(),narrowState.svg);
  await page.click('#redo-button');await ready(page);assert.equal(await page.inputValue('#bond-spacing'),'0.28');
  assert.deepEqual((await state(page)).document,wideState.snapshot.document);assert.equal(await page.locator('#drawing').innerHTML(),wideState.svg);

  let renderRequests=0;const track=request=>{if(new URL(request.url()).pathname==='/api/render')renderRequests++;};page.on('request',track);
  for(const invalid of ['', '0.05', '0.29']){
    const before=await appearanceState(page),requestsBefore=renderRequests;
    await page.fill('#bond-spacing',invalid);await page.click('#apply-style');await ready(page);
    assert.match(await page.locator('#toast').textContent(),/bondSpacing/);
    assert.deepEqual(await appearanceState(page),before);assert.equal(renderRequests,requestsBefore,'Invalid spacing must reject before a render request.');
    assert.equal(await page.inputValue('#bond-spacing'),invalid);
  }
  page.off('request',track);
  // Restore the displayed entry only; the committed style remains 0.28.
  await page.fill('#bond-spacing','0.28');
  const beforeExports=await appearanceState(page);
  await page.evaluate(async()=>{
    const {initializeProfiles}=await import('/modules/profiles.mjs'),documentState=window.nivramDocument.getSnapshot().document;
    const profile={schema:'nivram-profile/1',id:'a'.repeat(32),revision:1,name:'Spacing fixture',style:{...documentState.style,bondSpacing:0.10},export:documentState.export};
    await initializeProfiles(window.nivramDocument,{list_profiles:async()=>({profiles:[profile]})});
  });
  await page.locator('.profile-controls summary').click();await page.click('#preview-profile-button');
  await page.waitForFunction(()=>{
    const image=document.getElementById('png-preview-image'),save=[...document.querySelectorAll('#png-preview-dialog button')].find(button=>button.textContent==='Save this PNG');
    return image?.complete&&image.naturalWidth===640&&Boolean(image.style.width)&&save&&!save.disabled;
  });
  const previewPng=await pngMetrics(page,await page.locator('#png-preview-image').getAttribute('src'));
  assert.deepEqual(previewPng,widePng);
  await page.getByRole('button',{name:'Close / cancel preview',exact:true}).click();
  await page.waitForFunction(()=>!document.getElementById('png-preview-image').hasAttribute('src'));
  const batchData=await page.evaluate(async()=>{
    const {renderBatchPng}=await import('/modules/batch-export.mjs'),project=window.nivramDocument.getSnapshot().document;
    const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:project.graph,style:project.style})});
    if(!response.ok)throw new Error('Actual batch preparation render failed.');
    const result=await renderBatchPng({...await response.json(),project});
    return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(result.blob);});
  });
  const batchPng=await pngMetrics(page,batchData);assert.deepEqual(batchPng,widePng);assert.deepEqual(await appearanceState(page),beforeExports);
  await apply('0.18');await preservePng('0-18');
  await page.selectOption('#profile-choice',`${'a'.repeat(32)}/1`);await page.click('#apply-profile-button');await ready(page);
  assert.equal(await page.inputValue('#bond-spacing'),'0.1');assert.equal((await state(page)).document.style.bondSpacing,0.10);assert.deepEqual((await state(page)).document.graph,graph);
  await page.click('#undo-button');await ready(page);assert.equal(await page.inputValue('#bond-spacing'),'0.18');
  await page.click('#redo-button');await ready(page);assert.equal(await page.inputValue('#bond-spacing'),'0.1');
  const loaded=await state(page);loaded.document.style.bondSpacing=0.145;
  assert.equal(await page.evaluate(project=>window.nivramDocument.openProject(project),loaded.document),true);
  assert.equal(await page.inputValue('#bond-spacing'),'0.145');assert.deepEqual((await state(page)).document.graph,graph);
  await page.click('#apply-style');await ready(page);
  assert.equal((await state(page)).document.style.bondSpacing,0.145,'An in-range loaded value must not be rounded to the input step.');
  await page.click('#undo-button');await ready(page);assert.equal(await page.inputValue('#bond-spacing'),'0.1');
  await page.click('#redo-button');await ready(page);assert.equal(await page.inputValue('#bond-spacing'),'0.145');
  assert.deepEqual((await state(page)).document.graph,graph);
  await page.click('#import-button');await page.fill('#structure-input','c1ccccc1');await page.click('#load-structure');
  await page.waitForFunction(()=>!document.getElementById('import-dialog').open);await ready(page);
  const aromaticGraph=(await state(page)).document.graph;
  assert.equal(aromaticGraph.atoms.length,6);assert.equal(aromaticGraph.bonds.length,6);
  for(const value of ['0.06','0.28']){
    await page.fill('#bond-spacing',value);await page.click('#apply-style');await ready(page);
    assert.equal((await state(page)).document.style.bondSpacing,Number(value));
    assert.deepEqual((await state(page)).document.graph,aromaticGraph,'Changing spacing preserves a real imported aromatic graph.');
  }
  const evidence={fixture:'Real C=C import; no layout operation',narrow,wide,exports:reports,comparison:{main:widePng,preview:previewPng,batch:batchPng},graph};
  await writeFile(path.join(artifacts,'comparison.json'),JSON.stringify(evidence,null,2)+'\n');t.diagnostic(JSON.stringify(evidence));
});
