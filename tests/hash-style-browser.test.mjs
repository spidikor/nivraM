import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {productionEditor,ready,state} from './editor-browser-fixture.mjs';

// Synthetic molecules and actual local renderer output only. No reference
// images, user profile writes, installed-app checks, or corpus claims.
const repository=fileURLToPath(new URL('../',import.meta.url));
const run=process.env.NIVRAM_THEME_RUN??'hash-style-browser';
if(!/^[a-z0-9-]+$/.test(run))throw new Error('Use an owned hash-style test run name.');
const artifacts=path.join(repository,'../checks',run,'artifacts');

async function imported(page,smiles){
  await page.click('#import-button');await page.fill('#structure-input',smiles);await page.click('#load-structure');
  await page.waitForFunction(()=>!document.getElementById('import-dialog').open);await ready(page);
}
async function snapshot(page){
  return page.evaluate(()=>({document:window.nivramDocument.getSnapshot(),drawing:document.getElementById('drawing').innerHTML,
    undo:document.getElementById('undo-button').disabled,redo:document.getElementById('redo-button').disabled}));
}
async function apply(page,style,width){
  await page.selectOption('#hash-bond-style',style);
  if(width!==undefined)await page.fill('#bond-width',String(width));
  await page.click('#apply-style');await ready(page);
  assert.equal((await state(page)).document.style.hashBondStyle,style);
}
async function hashes(page){
  return page.evaluate(async()=>{
    const project=window.nivramDocument.getSnapshot().document;
    const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:project.graph,style:project.style})});
    if(!response.ok)throw new Error('Actual hash renderer failed.');
    const result=await response.json(),root=new DOMParser().parseFromString(result.scene.svg,'image/svg+xml');
    const number='[+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?';
    const line=new RegExp(`^M\\s*(${number})[ ,]+(${number})\\s*L\\s*(${number})[ ,]+(${number})\\s*$`);
    const groups=new Map();
    for(const p of root.querySelectorAll('path')){
      const cls=p.getAttribute('class')??'';
      if(!/^bond-\d+ atom-\d+ atom-\d+$/.test(cls))continue;
      if(!groups.has(cls))groups.set(cls,[]);groups.get(cls).push(p);
    }
    const hashes=[...groups].filter(([_cls,paths])=>paths.length>=3&&paths.every(p=>p.style.fill==='none'&&line.test(p.getAttribute('d')??'')));
    const drawing=document.getElementById('drawing');
    return {style:project.style,graph:project.graph,positions:result.scene.positions,transform:result.scene.transform,
      groups:hashes.map(([cls,paths])=>{
        const painted=[...drawing.querySelectorAll('path')].filter(p=>p.getAttribute('class')===cls);
        return {cls,paths:paths.map(p=>({d:p.getAttribute('d'),style:p.getAttribute('style'),width:parseFloat(p.style.strokeWidth),stroke:p.style.stroke,points:line.exec(p.getAttribute('d')).slice(1).map(Number)})),
          sanitizerPreserved:paths.length===painted.length&&paths.every((p,i)=>p.getAttribute('d')===painted[i].getAttribute('d')&&p.getAttribute('style')===painted[i].getAttribute('style')),
          generatedHashPaint:painted.some(p=>/url\s*\(/i.test(p.getAttribute('style')??''))};
      })};
  });
}
function checkSpaced(before,after,width,label){
  assert.equal(before.groups.length,1,label);assert.equal(after.groups.length,1,label);
  const original=before.groups[0],adjusted=after.groups[0],paths=adjusted.paths;
  assert.equal(original.cls,adjusted.cls,label);assert.ok(paths.length>=3,label);
  assert.equal(paths[0].d,original.paths[0].d,label);assert.equal(paths.at(-1).d,original.paths.at(-1).d,label);
  assert.equal(adjusted.sanitizerPreserved,true,label);assert.equal(adjusted.generatedHashPaint,false,label);
  assert.deepEqual(after.graph,before.graph,label);assert.deepEqual(after.positions,before.positions,label);assert.deepEqual(after.transform,before.transform,label);
  const center=p=>[(p.points[0]+p.points[2])/2,(p.points[1]+p.points[3])/2];
  const start=center(paths[0]),end=center(paths.at(-1)),span=Math.hypot(end[0]-start[0],end[1]-start[1]);
  const ux=(end[0]-start[0])/span,uy=(end[1]-start[1])/span,step=span/(paths.length-1),lengths=[];
  assert.ok(step>width+.2,label);
  for(let i=0;i<paths.length;i++){
    const p=paths[i],c=center(p);assert.equal(p.width,width,label);
    assert.ok(Math.hypot(c[0]-start[0]-ux*step*i,c[1]-start[1]-uy*step*i)<=.15,label);
    assert.ok(Math.abs((p.points[2]-p.points[0])*ux+(p.points[3]-p.points[1])*uy)<=.2,label);
    lengths.push(Math.hypot(p.points[2]-p.points[0],p.points[3]-p.points[1]));
  }
  assert.ok(lengths.every((length,i)=>length>0&&(!i||length>lengths[i-1])),label);
  const colors=paths.map(p=>p.stroke),runs=colors.filter((color,i)=>!i||color!==colors[i-1]);
  assert.equal(runs.length,new Set(runs).size,label);
  assert.equal(colors[0],original.paths[0].stroke,label);assert.equal(colors.at(-1),original.paths.at(-1).stroke,label);
}

test('optional hashed-stereobond appearance defaults to engine, validates exact values, and follows Apply and history',{timeout:120000},async t=>{
  await mkdir(artifacts,{recursive:true});
  const page=await productionEditor(t);
  assert.equal(await page.locator('#hash-bond-style').count(),1);
  assert.deepEqual(await page.locator('#hash-bond-style option').evaluateAll(options=>options.map(option=>option.value)),['engine','spaced']);
  assert.equal(await page.inputValue('#hash-bond-style'),'engine');
  assert.equal((await state(page)).document.style.hashBondStyle,'engine');
  const validation=await page.evaluate(async()=>{
    const {DEFAULT_STYLE,validateProject}=await import('/modules/document.mjs');
    const base=window.nivramDocument.getSnapshot().document,legacy=structuredClone(base);delete legacy.style.hashBondStyle;
    const before=JSON.stringify(legacy),checked=validateProject(legacy);
    const invalid=['',null,true,0,'Spaced','spaced ','unrecognized',[],{}].map(hashBondStyle=>{
      try{validateProject({...base,style:{...base.style,hashBondStyle}});return false;}catch{return true;}
    });
    let unknownRejected=false;try{validateProject({...base,style:{...base.style,unknownStyle:'engine'}});}catch{unknownRejected=true;}
    return {defaultValue:DEFAULT_STYLE.hashBondStyle,legacyValue:checked.style.hashBondStyle,legacyUnchanged:JSON.stringify(legacy)===before,invalid,unknownRejected};
  });
  assert.equal(validation.defaultValue,'engine');assert.equal(validation.legacyValue,'engine');assert.equal(validation.legacyUnchanged,true);
  assert.ok(validation.invalid.every(Boolean));assert.equal(validation.unknownRejected,true);
  await imported(page,'N[C@H](C)O');await apply(page,'engine',2.4);
  const baseline=await state(page),engine=await hashes(page);
  assert.equal(engine.groups.length,1);assert.ok(engine.groups[0].paths.every(p=>p.width===1.2));
  await apply(page,'spaced');const adjusted=await state(page),spaced=await hashes(page);
  checkSpaced(engine,spaced,2.4,'black synthetic hash');
  assert.deepEqual(adjusted.document.graph,baseline.document.graph);
  assert.deepEqual(adjusted.document.export,baseline.document.export);
  assert.deepEqual(adjusted.document.style,{...baseline.document.style,hashBondStyle:'spaced'});
  assert.equal(adjusted.revision,baseline.revision+1);
  await page.click('#undo-button');await ready(page);assert.equal(await page.inputValue('#hash-bond-style'),'engine');
  assert.deepEqual((await state(page)).document,baseline.document);
  assert.deepEqual((await hashes(page)).groups[0].paths,engine.groups[0].paths);
  await page.click('#redo-button');await ready(page);assert.equal(await page.inputValue('#hash-bond-style'),'spaced');
  assert.deepEqual((await state(page)).document,adjusted.document);
  assert.deepEqual((await hashes(page)).groups[0].paths,spaced.groups[0].paths);
  const noOp=await state(page);await apply(page,'spaced');assert.deepEqual(await state(page),noOp,'Unchanged appearance must add no document history.');
  const legacy=structuredClone(adjusted.document);delete legacy.style.hashBondStyle;
  assert.equal(await page.evaluate(project=>window.nivramDocument.openProject(project),legacy),true);
  assert.equal(await page.inputValue('#hash-bond-style'),'engine');assert.deepEqual((await state(page)).document.graph,baseline.document.graph);
  await page.evaluate(async project=>{
    const {initializeProfiles}=await import('/modules/profiles.mjs');
    const profile={schema:'nivram-profile/1',id:'a'.repeat(32),revision:1,name:'Synthetic spaced hashes',style:project.style,export:project.export};
    await initializeProfiles(window.nivramDocument,{list_profiles:async()=>({profiles:[profile]})});
  },adjusted.document);
  await page.locator('.profile-controls summary').click();
  await page.selectOption('#profile-choice','a'.repeat(32)+'/1');await page.click('#apply-profile-button');await ready(page);
  assert.equal(await page.inputValue('#hash-bond-style'),'spaced');assert.deepEqual((await state(page)).document.graph,baseline.document.graph);
  assert.deepEqual((await state(page)).document.style,adjusted.document.style);
  const beforeSave=await snapshot(page);
  const [download]=await Promise.all([page.waitForEvent('download'),page.click('#save-button')]);
  const filename=path.join(artifacts,'hash-style-project.nivram');await download.saveAs(filename);assert.equal(await download.failure(),null);
  const saved=JSON.parse(await readFile(filename,'utf8'));
  assert.deepEqual(saved,beforeSave.document.document,'Project bytes must retain the normalized enum, graph and export settings.');
  assert.equal(saved.style.hashBondStyle,'spaced');assert.deepEqual(await snapshot(page),beforeSave);
  assert.equal(await page.evaluate(project=>window.nivramDocument.openProject(project),saved),true);
  assert.equal(await page.inputValue('#hash-bond-style'),'spaced');assert.deepEqual((await state(page)).document,saved);
});

test('malformed render or unsupported project style retains document, artwork and history',{timeout:120000},async t=>{
  const page=await productionEditor(t);await imported(page,'N[C@@H](O)S');await apply(page,'spaced');
  for(const fault of ['unsafe-svg','invalid-style','hash-style-422']){
    const before=await snapshot(page);
    const route=async route=>{
      if(fault==='hash-style-422'){
        return route.fulfill({status:422,contentType:'application/json',body:JSON.stringify({error:'Thicker, spaced bars could not preserve this hashed stereobond. Choose Engine spacing for Hashed stereobonds and apply appearance again; the drawing is unchanged.'})});
      }
      const response=await route.fetch(),result=await response.json();
      if(fault==='unsafe-svg')result.scene.svg=result.scene.svg.replace('</svg>','<defs><linearGradient id="injected"/></defs></svg>');
      else result.scene.style.hashBondStyle='unsupported';
      await route.fulfill({response,json:result});
    };
    await page.route('**/api/render',route);
    try{
      await page.selectOption('#hash-bond-style','engine');await page.click('#apply-style');
      await page.waitForFunction(()=>document.getElementById('toast').classList.contains('error'));await ready(page);
      assert.deepEqual(await snapshot(page),before,fault);
      if(fault==='hash-style-422')assert.match(await page.locator('#toast').textContent(),/Engine spacing.*unchanged/);
    }finally{await page.unroute('**/api/render',route);}
    await page.selectOption('#hash-bond-style','spaced');
  }
  const before=await snapshot(page),project=structuredClone(before.document.document);project.style.hashBondStyle='unknown';
  assert.equal(await page.evaluate(project=>window.nivramDocument.openProject(project),project),false);
  assert.deepEqual(await snapshot(page),before);
});

async function png(page,kind,label){
  if(kind==='preview'){
    const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Save this PNG',exact:true}).click()]);
    const filename=path.join(artifacts,label+'.png');await download.saveAs(filename);assert.equal(await download.failure(),null);
    return 'data:image/png;base64,'+(await readFile(filename)).toString('base64');
  }
  return page.evaluate(async kind=>{
    const data=blob=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(blob);});
    const project=window.nivramDocument.getSnapshot().document;
    if(kind==='main'){const {exportPng}=await import('/modules/export.mjs');return data((await exportPng(document.getElementById('drawing'),project.export,{graph:project.graph})).blob);}
    const {renderBatchPng}=await import('/modules/batch-export.mjs');
    const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:project.graph,style:project.style})});
    if(!response.ok)throw new Error('Actual spaced-hash batch renderer failed.');
    return data((await renderBatchPng({...await response.json(),project})).blob);
  },kind);
}
async function metrics(page,data){
  return page.evaluate(async data=>{
    const image=new Image();image.src=data;await image.decode();
    const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
    const context=canvas.getContext('2d');context.drawImage(image,0,0);const pixels=context.getImageData(0,0,canvas.width,canvas.height).data;
    let nonOpaque=0,nonwhiteMargin=0,ink=0;
    for(let y=0;y<canvas.height;y++)for(let x=0;x<canvas.width;x++){
      const i=(y*canvas.width+x)*4,white=pixels[i]===255&&pixels[i+1]===255&&pixels[i+2]===255;
      if(pixels[i+3]!==255)nonOpaque++;
      if(!white){ink++;if(x<13||x>=canvas.width-13||y<13||y>=canvas.height-13)nonwhiteMargin++;}
    }
    const digest=await crypto.subtle.digest('SHA-256',pixels);
    const result={width:canvas.width,height:canvas.height,nonOpaque,nonwhiteMargin,ink,rgbaSha256:[...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('')};
    image.removeAttribute('src');canvas.width=canvas.height=1;return result;
  },data);
}

test('actual spaced hashes keep the same PNG in main, exact profile preview and batch across both editor themes',{timeout:180000},async t=>{
  await mkdir(artifacts,{recursive:true});const page=await productionEditor(t),report={scope:'synthetic fixtures; colored-hash calibration unverified',exports:{},geometry:{}};
  await page.evaluate(async()=>{const {initializeProfiles}=await import('/modules/profiles.mjs');await initializeProfiles(window.nivramDocument,{list_profiles:async()=>({profiles:[]})});});
  for(const [label,smiles] of [['black','N[C@H](C)O'],['black-blue','N[C@@H](O)S']]){
    await imported(page,smiles);await apply(page,'engine',4.2);const engine=await hashes(page);
    await apply(page,'spaced');const spaced=await hashes(page);checkSpaced(engine,spaced,4.2,label);report.geometry[label]={engine,spaced};
    await page.fill('#export-width','640');
    if(await page.locator('#theme-toggle').getAttribute('aria-pressed')==='true')await page.click('#theme-toggle');
    const before=await snapshot(page);let baseline=null;
    for(const theme of ['light','dark']){
      if(await page.locator('#theme-toggle').getAttribute('aria-pressed')!==(theme==='dark'?'true':'false'))await page.click('#theme-toggle');
      for(const kind of ['main','preview','batch']){
        const name=`${label}-${theme}-${kind}`;
        if(kind==='preview'){
          const details=page.locator('.profile-controls');if(await details.getAttribute('open')===null)await details.locator('summary').click();
          await page.click('#preview-profile-button');
          await page.waitForFunction(()=>{const image=document.getElementById('png-preview-image'),button=[...document.querySelectorAll('#png-preview-dialog button')].find(node=>node.textContent==='Save this PNG');return image.complete&&image.naturalWidth===640&&button&&!button.disabled;});
        }
        const data=await png(page,kind,name),value=await metrics(page,data);
        await writeFile(path.join(artifacts,name+'.png'),Buffer.from(data.split(',')[1],'base64'));
        if(kind==='preview'){await page.getByRole('button',{name:'Close / cancel preview',exact:true}).click();await page.waitForFunction(()=>!document.getElementById('png-preview-image').hasAttribute('src'));}
        assert.equal(value.width,640);assert.equal(value.nonOpaque,0);assert.equal(value.nonwhiteMargin,0);assert.ok(value.ink>0);
        if(baseline===null)baseline=value;else assert.deepEqual(value,baseline,`${name} must match the actual light-main RGBA.`);
        assert.deepEqual(await snapshot(page),before,'Theme/export must retain the full document, coordinates, styles, revision, history and original drawing.');
        report.exports[name]=value;
      }
    }
  }
  assert.equal(await page.locator('#png-preview-image').getAttribute('src'),null);
  await writeFile(path.join(artifacts,'hash-style.json'),JSON.stringify(report,null,2)+'\n');t.diagnostic(JSON.stringify(report));
});
