import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {productionEditor,ready,state} from './editor-browser-fixture.mjs';

const repository=fileURLToPath(new URL('../',import.meta.url));
const artifacts=path.join(repository,'../checks',process.env.NIVRAM_THEME_RUN??'label-clearance-browser','artifacts');
const snapshot=page=>page.evaluate(()=>({state:window.nivramDocument.getSnapshot(),svg:document.getElementById('drawing').innerHTML,
  undo:document.getElementById('undo-button').disabled,redo:document.getElementById('redo-button').disabled}));
async function imported(page,source){
  await page.click('#import-button');await page.fill('#structure-input',source);await page.click('#load-structure');
  await page.waitForFunction(()=>!document.getElementById('import-dialog').open);await ready(page);
}
async function clearance(page){
  return page.evaluate(()=>{
    const labels=[...document.querySelectorAll('#drawing [class~="atom-1"]')].filter(node=>!/(?:^|\s)bond-\d+(?:\s|$)/.test(node.getAttribute('class')??''));
    if(!labels.length)throw new Error('Expected visible oxygen glyph artwork.');
    const boxes=labels.map(node=>node.getBBox()),left=Math.min(...boxes.map(box=>box.x)),rightLabel=Math.max(...boxes.map(box=>box.x+box.width));
    const top=Math.min(...boxes.map(box=>box.y)),bottom=Math.max(...boxes.map(box=>box.y+box.height));
    const paths=[...document.querySelectorAll('#drawing path.bond-0')];
    const right=Math.max(...paths.flatMap(node=>[node.getPointAtLength(0).x,node.getPointAtLength(node.getTotalLength()).x]));
    const stroke=parseFloat(getComputedStyle(paths[0]).strokeWidth);
    const project=window.nivramDocument.getSnapshot().document;
    return {gap:left-right-stroke/2,label:project.graph.atoms[1].symbol,labelSize:project.style.labelSize,
      glyphs:labels.length,glyphWidth:rightLabel-left,glyphHeight:bottom-top,style:project.style};
  });
}
async function annotationGlyphs(page){
  return page.evaluate(()=>{
    const graph=window.nivramDocument.getSnapshot().document.graph;
    return graph.atoms.map((atom,index)=>({atom,index})).filter(({atom})=>atom.isotope||atom.charge).map(({atom,index})=>{
      const glyphs=[...document.querySelectorAll(`#drawing [class~="atom-${index}"]`)].filter(node=>!/(?:^|\s)bond-\d+(?:\s|$)/.test(node.getAttribute('class')??''));
      if(!glyphs.length)throw new Error('An annotated atom has no rendered glyphs.');
      const boxes=glyphs.map(node=>node.getBBox()),x=Math.min(...boxes.map(box=>box.x)),y=Math.min(...boxes.map(box=>box.y));
      return {symbol:atom.symbol,isotope:atom.isotope,charge:atom.charge,glyphs:glyphs.map((node,i)=>({kind:node.localName,text:node.textContent,
        style:node.getAttribute('style'),width:boxes[i].width,height:boxes[i].height,
        shape:typeof node.getTotalLength==='function'?Array.from({length:17},(_,step)=>{const point=node.getPointAtLength(node.getTotalLength()*step/16);return [point.x-x,point.y-y];}):[[boxes[i].x-x,boxes[i].y-y]]}))};
    });
  });
}
async function png(page,kind){
  if(kind==='preview'){
    const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Save this PNG',exact:true}).click()]);
    const destination=path.join(artifacts,'clearance-preview.png');await download.saveAs(destination);assert.equal(await download.failure(),null);
    return 'data:image/png;base64,'+(await readFile(destination)).toString('base64');
  }
  return page.evaluate(async kind=>{
    const project=window.nivramDocument.getSnapshot().document;
    let output;
    if(kind==='main'){
      const {exportPng}=await import('/modules/export.mjs');output=await exportPng(document.getElementById('drawing'),project.export,{graph:project.graph});
    }else{
      const {renderBatchPng}=await import('/modules/batch-export.mjs');
      const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:project.graph,style:project.style})});
      if(!response.ok)throw new Error('Actual batch clearance render failed.');
      output=await renderBatchPng({...await response.json(),project});
    }
    return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(output.blob);});
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
    const hash=await crypto.subtle.digest('SHA-256',pixels),result={width:canvas.width,height:canvas.height,nonOpaque,nonwhiteMargin,ink,
      rgbaSha256:[...new Uint8Array(hash)].map(value=>value.toString(16).padStart(2,'0')).join('')};
    image.removeAttribute('src');canvas.width=canvas.height=1;return result;
  },data);
}

test('label clearance controls actual bond-label distance and preserves legacy styles, chemistry, history and exact exports',{timeout:180000},async t=>{
  const page=await productionEditor(t);
  assert.equal(await page.locator('#label-padding').count(),1,'Drawing appearance needs the extra label clearance control.');
  assert.equal(await page.locator('#label-padding').getAttribute('min'),'0');
  assert.equal(await page.locator('#label-padding').getAttribute('max'),'20');
  assert.equal(await page.locator('#label-padding').getAttribute('step'),'1');
  assert.equal(await page.inputValue('#label-padding'),'4');
  await imported(page,'C=O');await page.fill('#export-width','640');
  const initial=(await state(page)).document,graph=initial.graph;
  await mkdir(artifacts,{recursive:true});
  async function apply(value){await page.fill('#label-padding',value);await page.click('#apply-style');await ready(page);assert.equal((await state(page)).document.style.labelPadding,Number(value)/100);assert.deepEqual((await state(page)).document.graph,graph);}
  const baseline=await clearance(page);
  await apply('0');const tight=await clearance(page),tightDocument=(await state(page)).document;
  await apply('20');const wide=await clearance(page),wideDocument=(await state(page)).document;
  assert.ok(wide.gap>tight.gap+3,JSON.stringify({tight,wide}));
  assert.equal(wide.labelSize,tight.labelSize);assert.equal(wide.label,tight.label);assert.equal(wide.glyphs,tight.glyphs);assert.ok(wide.glyphs>0);
  assert.ok(Math.abs(wide.glyphWidth-tight.glyphWidth)<0.25);assert.ok(Math.abs(wide.glyphHeight-tight.glyphHeight)<0.25);
  await page.click('#undo-button');await ready(page);assert.equal(await page.inputValue('#label-padding'),'0');assert.deepEqual((await state(page)).document,tightDocument);
  await page.click('#redo-button');await ready(page);assert.equal(await page.inputValue('#label-padding'),'20');assert.deepEqual((await state(page)).document,wideDocument);
  let requests=0;const track=request=>{if(new URL(request.url()).pathname==='/api/render')requests++;};page.on('request',track);
  for(const value of ['', '-1','21']){
    const before=await snapshot(page),count=requests;await page.fill('#label-padding',value);await page.click('#apply-style');await ready(page);
    assert.match(await page.locator('#toast').textContent(),/Extra label clearance must be a number from 0% to 20%/);assert.deepEqual(await snapshot(page),before);assert.equal(requests,count);
  }
  page.off('request',track);await page.fill('#label-padding','20');
  const beforeExport=await snapshot(page),exports={};
  await page.evaluate(async()=>{
    const {initializeProfiles}=await import('/modules/profiles.mjs'),project=window.nivramDocument.getSnapshot().document,legacyStyle={...project.style};delete legacyStyle.labelPadding;
    const profile={schema:'nivram-profile/1',id:'b'.repeat(32),revision:1,name:'Legacy spacing',style:legacyStyle,export:project.export};
    await initializeProfiles(window.nivramDocument,{list_profiles:async()=>({profiles:[profile,{...profile,id:'c'.repeat(32),name:'Fine clearance',style:{...project.style,labelPadding:0.075}}]})});
  });
  const main=await png(page,'main');exports.main=await metrics(page,main);await writeFile(path.join(artifacts,'clearance-20.png'),Buffer.from(main.split(',')[1],'base64'));
  const details=page.locator('.profile-controls');await details.locator('summary').click();await page.click('#preview-profile-button');
  await page.waitForFunction(()=>{const image=document.getElementById('png-preview-image');return image.complete&&image.naturalWidth===640&&[...document.querySelectorAll('#png-preview-dialog button')].some(button=>button.textContent==='Save this PNG'&&!button.disabled);});
  exports.preview=await metrics(page,await png(page,'preview'));
  await page.getByRole('button',{name:'Close / cancel preview',exact:true}).click();
  exports.batch=await metrics(page,await png(page,'batch'));
  for(const value of Object.values(exports)){assert.equal(value.width,640);assert.equal(value.nonOpaque,0);assert.equal(value.nonwhiteMargin,0);assert.ok(value.ink>0);assert.deepEqual(value,exports.main);}
  assert.deepEqual(await snapshot(page),beforeExport);

  const loaded=structuredClone(wideDocument);loaded.style.labelPadding=0.145;
  assert.equal(await page.evaluate(project=>window.nivramDocument.openProject(project),loaded),true);
  assert.equal(await page.inputValue('#label-padding'),'14.5');
  const loadedBefore=await state(page);await page.click('#apply-style');await ready(page);
  assert.deepEqual(await state(page),loadedBefore,'Applying an untouched loaded fraction must preserve its exact value and history.');
  await page.click('#undo-button');await ready(page);assert.equal(await page.inputValue('#label-padding'),'20');
  await page.click('#redo-button');await ready(page);assert.equal(await page.inputValue('#label-padding'),'14.5');
  await apply('5.5');assert.equal((await state(page)).document.style.labelPadding,0.055);

  const legacy=structuredClone(wideDocument);delete legacy.style.labelPadding;
  assert.equal(await page.evaluate(project=>window.nivramDocument.openProject(project),legacy),true);
  assert.equal(await page.inputValue('#label-padding'),'4');assert.equal((await state(page)).document.style.labelPadding,0.04);
  assert.deepEqual((await state(page)).document.graph,graph);
  await page.selectOption('#profile-choice',`${'c'.repeat(32)}/1`);await page.click('#apply-profile-button');await ready(page);
  assert.equal(await page.inputValue('#label-padding'),'7.5');assert.equal((await state(page)).document.style.labelPadding,0.075);assert.deepEqual((await state(page)).document.graph,graph);
  await page.click('#undo-button');await ready(page);assert.equal(await page.inputValue('#label-padding'),'4');
  await page.click('#redo-button');await ready(page);assert.equal(await page.inputValue('#label-padding'),'7.5');
  await page.selectOption('#profile-choice',`${'b'.repeat(32)}/1`);await page.click('#apply-profile-button');await ready(page);
  assert.equal(await page.inputValue('#label-padding'),'4');
  await imported(page,'[13CH3][NH2+]C');const annotations=(await state(page)).document.graph;
  const beforeLabels=await annotationGlyphs(page);assert.equal(beforeLabels.length,2);
  for(const label of beforeLabels){
    assert.ok(label.glyphs.every(glyph=>glyph.width>0&&glyph.height>0));
    if(label.glyphs.some(glyph=>glyph.kind==='path'))assert.ok(label.glyphs.length>=3,'The isotope and charged hydrogen labels need multiple visible character outlines.');
    else assert.match(label.glyphs.map(glyph=>glyph.text).join(''),label.isotope?/13/:/\+/);
  }
  await page.fill('#label-padding','20');await page.click('#apply-style');await ready(page);
  assert.deepEqual((await state(page)).document.graph,annotations);
  const afterLabels=await annotationGlyphs(page);assert.equal(afterLabels.length,beforeLabels.length);
  for(let i=0;i<beforeLabels.length;i++){
    const before=beforeLabels[i],after=afterLabels[i];
    assert.deepEqual([after.symbol,after.isotope,after.charge],[before.symbol,before.isotope,before.charge]);assert.equal(after.glyphs.length,before.glyphs.length);
    for(let j=0;j<before.glyphs.length;j++){
      const a=before.glyphs[j],b=after.glyphs[j];assert.deepEqual([b.kind,b.text,b.style],[a.kind,a.text,a.style]);
      assert.ok(Math.abs(a.width-b.width)<0.25&&Math.abs(a.height-b.height)<0.25);
      for(let k=0;k<a.shape.length;k++)assert.ok(Math.hypot(a.shape[k][0]-b.shape[k][0],a.shape[k][1]-b.shape[k][1])<0.35,'Clearance must preserve annotated glyph shapes, apart from SVG coordinate rounding.');
    }
  }
  assert.ok(annotations.atoms.some(atom=>atom.isotope===13));assert.ok(annotations.atoms.some(atom=>atom.charge===1));
  const evidence={baseline,tight,wide,exports,legacyDefault:0.04,loadedFraction:0.145,annotationGraphPreserved:true};
  await writeFile(path.join(artifacts,'label-clearance.json'),JSON.stringify(evidence,null,2)+'\n');t.diagnostic(JSON.stringify(evidence));
});
