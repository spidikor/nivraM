import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {productionEditor,ready} from './editor-browser-fixture.mjs';

const repository=fileURLToPath(new URL('../',import.meta.url));
const run=process.env.NIVRAM_THEME_RUN??'bond-gradient-export-browser';
if(!/^[a-z0-9-]+$/.test(run))throw new Error('Use an owned gradient test run name.');
const artifacts=path.join(repository,'../checks',run,'artifacts');
async function imported(page,smiles){
  await page.click('#import-button');await page.fill('#structure-input',smiles);await page.click('#load-structure');
  await page.waitForFunction(()=>!document.getElementById('import-dialog').open);await ready(page);
}
async function snapshot(page){
  return page.evaluate(()=>({document:window.nivramDocument.getSnapshot(),drawing:document.getElementById('drawing').innerHTML,
    undo:document.getElementById('undo-button').disabled,redo:document.getElementById('redo-button').disabled}));
}
async function png(page,kind,artifactLabel){
  if(kind==='preview'){
    // Observe the real save action. The page's CSP intentionally disallows
    // fetching blob URLs even though its decoded preview image can show them.
    const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Save this PNG',exact:true}).click()]);
    const filename=path.join(artifacts,artifactLabel+'.png');await download.saveAs(filename);
    assert.equal(await download.failure(),null);
    return 'data:image/png;base64,'+(await readFile(filename)).toString('base64');
  }
  return page.evaluate(async kind=>{
    const asData=blob=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(blob);});
    const project=window.nivramDocument.getSnapshot().document;
    if(kind==='main'){
      const {exportPng}=await import('/modules/export.mjs');return asData((await exportPng(document.getElementById('drawing'),project.export,{graph:project.graph})).blob);
    }
    const {renderBatchPng}=await import('/modules/batch-export.mjs');
    const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:project.graph,style:project.style})});
    if(!response.ok)throw new Error('Production batch preparation failed.');
    return asData((await renderBatchPng({...await response.json(),project})).blob);
  },kind);
}
async function metrics(page,data,{scanRamp=false}={}){
  return page.evaluate(async({data,scanRamp})=>{
    const image=new Image();image.src=data;await image.decode();
    const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
    const context=canvas.getContext('2d');context.drawImage(image,0,0);
    const width=canvas.width,height=canvas.height,p=context.getImageData(0,0,width,height).data;
    let nonOpaque=0,nonwhiteMargin=0,black=0,red=0;
    for(let y=0;y<height;y++)for(let x=0;x<width;x++){
      const i=(y*width+x)*4,white=p[i]===255&&p[i+1]===255&&p[i+2]===255;
      if(p[i+3]!==255)nonOpaque++;
      if(!white&&(x<13||x>=width-13||y<13||y>=height-13))nonwhiteMargin++;
      if(p[i]===0&&p[i+1]===0&&p[i+2]===0)black++;
      if(p[i]===255&&p[i+1]===0&&p[i+2]===0)red++;
    }
    let ramp=null;
    if(scanRamp){
      // CO is horizontal. Search the solid interior, excluding antialiased
      // edges by requiring exactly zero green and blue channels. A hard
      // rounded join can have one mixed boundary pixel, not this sustained ramp.
      const is=(x,y,kind)=>{if(x<0||x>=width||y<0||y>=height)return false;const i=(y*width+x)*4;return p[i+1]===0&&p[i+2]===0&&p[i+3]===255&&(kind==='black'?p[i]===0:kind==='red'?p[i]===255:p[i]>0&&p[i]<255);};
      for(let y=0;y<height;y++)for(let x=1;x<width-1;x++){
        if(!is(x,y,'mix')||!is(x-1,y,'black'))continue;
        let end=x;while(is(end,y,'mix'))end++;
        if(!is(end,y,'red'))continue;
        let left=x-1,right=end;while(is(left,y,'black'))left--;while(is(right,y,'red'))right++;
        if(x-left-1<20||right-end<20)continue;
        const reds=[];for(let v=x;v<end;v++)reds.push(p[(y*width+v)*4]);
        const probe=x-10;let top=y,bottom=y;while(is(probe,top-1,'black'))top--;while(is(probe,bottom+1,'black'))bottom++;
        const candidate={x,y,length:end-x,blackRun:x-left-1,redRun:right-end,strokePixels:bottom-top+1,reds};
        if(!ramp||candidate.length>ramp.length)ramp=candidate;
      }
    }
    const hash=await crypto.subtle.digest('SHA-256',p);
    const result={width,height,nonOpaque,nonwhiteMargin,black,red,rgbaSha256:[...new Uint8Array(hash)].map(v=>v.toString(16).padStart(2,'0')).join(''),ramp};
    image.removeAttribute('src');canvas.width=canvas.height=1;return result;
  },{data,scanRamp});
}
async function savePng(page,label,kind,scanRamp){
  const data=await png(page,kind,label);await writeFile(path.join(artifacts,label+'.png'),Buffer.from(data.split(',')[1],'base64'));
  return metrics(page,data,{scanRamp});
}
function validPng(value){
  assert.equal(value.width,640);assert.equal(value.nonOpaque,0);assert.equal(value.nonwhiteMargin,0);
  assert.ok(value.black>0);assert.ok(value.red>0);
}

test('real editor, exact preview and batch preserve a short smooth bond join through light and dark PNG export',{timeout:180000},async t=>{
  await mkdir(artifacts,{recursive:true});
  const page=await productionEditor(t),report={exports:{},references:{}};
  await page.evaluate(async()=>{const {initializeProfiles}=await import('/modules/profiles.mjs');await initializeProfiles(window.nivramDocument,{list_profiles:async()=>({profiles:[]})});});
  for(const [label,smiles] of [['co','CO'],['ncco','NCCO']]){
    await imported(page,smiles);await page.fill('#export-width','640');
    if(await page.locator('#theme-toggle').getAttribute('aria-pressed')==='true')await page.click('#theme-toggle');
    const before=await snapshot(page);
    // Keep baseline PNG even when the expected pre-implementation red fails.
    const initial=await savePng(page,label+'-light-main','main',label==='co');validPng(initial);
    report.exports[label+'-light-main']=initial;
    if(label==='co'){
      await writeFile(path.join(artifacts,'initial-pixel-metrics.json'),JSON.stringify(initial,null,2)+'\n');
      assert.ok(initial.ramp&&initial.ramp.length>=3,'The actual CO PNG must contain multiple intermediate interior pixels across the bond centre, not a hard rounded colour join.');
      assert.ok(Math.abs(initial.ramp.length-initial.ramp.strokePixels)<=2,JSON.stringify(initial.ramp));
      assert.ok(initial.ramp.reds.every((value,index,array)=>!index||value>=array[index-1]),'The transition must progress monotonically from black to red.');
    }
    report.references[label]=await page.evaluate(async()=>{
      const project=window.nivramDocument.getSnapshot().document;
      const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:project.graph,style:project.style})});
      if(!response.ok)throw new Error('Production reference preparation failed.');
      const {sanitizeSvg}=await import('/modules/document.mjs'),{scene}=await response.json(),groups=[document.getElementById('drawing')];
      window.gradientQaStages=[];
      for(let i=0;i<2;i++){
        const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),g=document.createElementNS(svg.namespaceURI,'g');
        svg.setAttribute('width','1000');svg.setAttribute('height','1000');svg.style.cssText='position:fixed;left:-100000px;visibility:hidden';
        g.append(sanitizeSvg(scene.svg));svg.append(g);document.body.append(svg);window.gradientQaStages.push(svg);groups.push(g);
      }
      const allIds=groups.flatMap(g=>[...g.querySelectorAll('linearGradient')].map(e=>e.id));
      return {counts:groups.map(g=>g.querySelectorAll('linearGradient').length),unique:allIds.length===new Set(allIds).size,
        local:groups.every(g=>[...g.querySelectorAll('path')].every(p=>{const match=p.getAttribute('style')?.match(/stroke:\s*url\(#([^)]+)\)/);return !match||[...g.querySelectorAll('linearGradient')].some(e=>e.id===match[1]);}))};
    });
    assert.deepEqual(report.references[label].counts,[label==='co'?1:2,label==='co'?1:2,label==='co'?1:2]);
    assert.equal(report.references[label].unique,true);assert.equal(report.references[label].local,true);
    for(const theme of ['light','dark']){
      if(await page.locator('#theme-toggle').getAttribute('aria-pressed')!==(theme==='dark'?'true':'false'))await page.click('#theme-toggle');
      const stops=await page.evaluate(()=>[...document.querySelectorAll('#drawing linearGradient stop')].filter(e=>e.getAttribute('stop-color')==='#000000'||/stop-color:\s*#000000/.test(e.getAttribute('style')??'')).map(e=>({inline:e.getAttribute('stop-color')??e.style.stopColor,computed:getComputedStyle(e).stopColor})));
      assert.ok(stops.length>0);for(const stop of stops){assert.equal(stop.inline,'#000000');assert.equal(stop.computed,theme==='dark'?'rgb(214, 214, 214)':'rgb(0, 0, 0)');}
      const main=theme==='light'?initial:await savePng(page,label+'-'+theme+'-main','main',label==='co');
      const batch=await savePng(page,label+'-'+theme+'-batch','batch',label==='co');
      const details=page.locator('.profile-controls');if(await details.getAttribute('open')===null)await details.locator('summary').click();
      await page.click('#preview-profile-button');
      await page.waitForFunction(()=>{const image=document.getElementById('png-preview-image'),button=[...document.querySelectorAll('#png-preview-dialog button')].find(e=>e.textContent==='Save this PNG');return image.complete&&image.naturalWidth===640&&button&&!button.disabled;});
      const preview=await savePng(page,label+'-'+theme+'-preview','preview',label==='co');
      await page.getByRole('button',{name:'Close / cancel preview',exact:true}).click();
      await page.waitForFunction(()=>!document.getElementById('png-preview-image').hasAttribute('src'));
      for(const [kind,value] of Object.entries({main,batch,preview})){validPng(value);assert.deepEqual(value,initial,`${label} ${theme} ${kind} must have exactly the light main RGBA pixels.`);report.exports[label+'-'+theme+'-'+kind]=value;}
      assert.deepEqual(await snapshot(page),before,'Theme and all export paths must preserve graph coordinates, document revision, history controls and source artwork.');
    }
    if(label==='co'){
      // Maximum width does not override the existing aspect-ratio-dependent
      // memory bound. Rotate an independent, actually rendered CO graph to
      // make this case taller without hitting the separate raster-size limit.
      await assert.rejects(page.evaluate(async()=>{
        const {exportPng,exportGeometry,visibleVectorBounds,MAX_MEMORY_BYTES}=await import('/modules/export.mjs');
        const {sanitizeSvg}=await import('/modules/document.mjs'),project=window.nivramDocument.getSnapshot().document;
        const graph=structuredClone(project.graph),angle=Math.PI/4;
        for(const atom of graph.atoms){const {x,y}=atom;atom.x=x*Math.cos(angle)-y*Math.sin(angle);atom.y=x*Math.sin(angle)+y*Math.cos(angle);}
        const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph,style:project.style})});
        if(!response.ok)throw new Error('Production tall export fixture preparation failed.');
        const {scene}=await response.json(),stage=document.createElementNS('http://www.w3.org/2000/svg','svg'),group=document.createElementNS(stage.namespaceURI,'g');
        stage.setAttribute('width','1000');stage.setAttribute('height','1000');stage.setAttribute('aria-hidden','true');
        stage.style.cssText='position:fixed;left:-100000px;visibility:hidden';group.append(sanitizeSvg(scene.svg));stage.append(group);
        try{
          document.body.append(stage);
          const options={...project.export,width:9999},geometry=exportGeometry(visibleVectorBounds(group),options);
          if(geometry.estimatedPeakBytes<=MAX_MEMORY_BYTES)throw new Error('The tall rendered fixture must exceed the existing export memory budget.');
          await exportPng(group,options,{graph});
        }finally{stage.remove();}
      }),/Estimated export memory exceeds the 512 MiB budget\. Reduce the width; no lower-resolution file was created\./);
      assert.deepEqual(await snapshot(page),before);
      report.exports['rotated-co-9999']={status:'rejected-at-existing-memory-preflight',documentAndArtworkUnchanged:true};
    }
    await page.evaluate(()=>{for(const stage of window.gradientQaStages)stage.remove();delete window.gradientQaStages;});
    await writeFile(path.join(artifacts,label+'-drawing.svg'),await page.locator('#canvas').evaluate(e=>e.outerHTML));
  }
  // A naturally wide molecule can satisfy both the 9999px width contract and
  // the unchanged memory budget. It still contains an eligible C-O gradient.
  await imported(page,'CCCCCCCCCCCCCCCCO');
  assert.equal(await page.locator('#drawing linearGradient').count(),1);
  const wideBefore=await snapshot(page);
  const large=await page.evaluate(async()=>{
    const {exportPng}=await import('/modules/export.mjs'),project=window.nivramDocument.getSnapshot().document;
    const output=await exportPng(document.getElementById('drawing'),{...project.export,width:9999},{graph:project.graph});
    const bitmap=await createImageBitmap(output.blob),canvas=document.createElement('canvas');
    canvas.width=bitmap.width;canvas.height=bitmap.height;const context=canvas.getContext('2d',{willReadFrequently:true});context.drawImage(bitmap,0,0);bitmap.close();
    let nonOpaque=0,nonwhiteMargin=0;
    // Read short strips, without a second full RGBA image or redundant hash.
    for(let top=0;top<canvas.height;top+=64){
      const height=Math.min(64,canvas.height-top),pixels=context.getImageData(0,top,canvas.width,height).data;
      for(let y=0;y<height;y++)for(let x=0;x<canvas.width;x++){
        const i=(y*canvas.width+x)*4;if(pixels[i+3]!==255)nonOpaque++;
        if((x<output.padding||x>=canvas.width-output.padding||top+y<output.padding||top+y>=canvas.height-output.padding)&&
          (pixels[i]!==255||pixels[i+1]!==255||pixels[i+2]!==255))nonwhiteMargin++;
      }
    }
    const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(output.blob);});
    const result={width:canvas.width,height:canvas.height,padding:output.padding,nonOpaque,nonwhiteMargin,estimatedPeakBytes:output.estimatedPeakBytes,memoryBudgetBytes:output.memoryBudgetBytes,data};
    canvas.width=canvas.height=1;return result;
  });
  await writeFile(path.join(artifacts,'wide-chain-9999.png'),Buffer.from(large.data.split(',')[1],'base64'));delete large.data;
  assert.equal(large.width,9999);assert.equal(large.padding,200);assert.equal(large.nonOpaque,0);assert.equal(large.nonwhiteMargin,0);
  assert.ok(large.estimatedPeakBytes<=large.memoryBudgetBytes);assert.deepEqual(await snapshot(page),wideBefore);
  report.exports['wide-chain-9999']=large;
  await writeFile(path.join(artifacts,'comparison.json'),JSON.stringify(report,null,2)+'\n');t.diagnostic(JSON.stringify(report));
});

test('actual single, double, triple and stereochemical imports preserve all RDKit coordinates while painting only eligible pairs',{timeout:120000},async t=>{
  const page=await productionEditor(t),reports=[];
  for(const [smiles,expected] of [['CO',1],['NCCO',2],['C=O',2],['C#N',3],['N[C@H](C)O',2],['N[C@@H](C)O',2]]){
    await imported(page,smiles);
    const result=await page.evaluate(async()=>{
      const project=window.nivramDocument.getSnapshot().document;
      const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:project.graph,style:project.style})});
      if(!response.ok)throw new Error('Production geometry comparison failed.');
      const {scene}=await response.json(),original=new DOMParser().parseFromString(scene.svg,'image/svg+xml'),drawing=document.getElementById('drawing');
      const plain=[...original.querySelectorAll('path')],painted=[...drawing.querySelectorAll('path')];
      // Hashes may now have the full ordinary width. Identify their repeated
      // transverse line geometry against the actual atom-position axis as well.
      // The prior filled/reduced-width predicate remains in place.
      const groups=new Map();
      for(const p of plain){const cls=p.getAttribute('class')??'';if(!groups.has(cls))groups.set(cls,[]);groups.get(cls).push(p);}
      const hashClasses=new Set([...groups].filter(([cls,paths])=>{
        const match=/^bond-\d+ atom-(\d+) atom-(\d+)$/.exec(cls);
        if(!match||paths.length<3)return false;
        const a=scene.positions[Number(match[1])],b=scene.positions[Number(match[2])];if(!a||!b)return false;
        const dx=b.x-a.x,dy=b.y-a.y,length=Math.hypot(dx,dy);if(!length)return false;
        return paths.every(p=>{
          const points=/^M\s*([+-]?[\d.]+)[ ,]+([+-]?[\d.]+)\s*L\s*([+-]?[\d.]+)[ ,]+([+-]?[\d.]+)\s*$/.exec(p.getAttribute('d')??'');
          return p.style.fill==='none'&&points&&Math.abs((Number(points[3])-Number(points[1]))*dx+(Number(points[4])-Number(points[2]))*dy)/length<=.2;
        });
      }).map(([cls])=>cls));
      const stereo=plain.map((p,i)=>({p,i})).filter(({p})=>p.getAttribute('class')?.includes('bond-')&&(p.style.fill!=='none'||hashClasses.has(p.getAttribute('class'))||parseFloat(p.style.strokeWidth)!==project.style.bondWidth));
      return {gradients:drawing.querySelectorAll('linearGradient').length,geometrySame:plain.length===painted.length&&plain.every((p,i)=>p.getAttribute('d')===painted[i].getAttribute('d')&&p.getAttribute('class')===painted[i].getAttribute('class')),
        stereoCount:stereo.length,stereoUnchanged:stereo.every(({p,i})=>p.getAttribute('style')===painted[i].getAttribute('style')),
        pathsPerGradient:[...drawing.querySelectorAll('linearGradient')].map(g=>painted.filter(p=>(p.getAttribute('style')??'').includes(`url(#${g.id})`)).length)};
    });
    reports.push({smiles,...result});assert.equal(result.gradients,expected,smiles);assert.equal(result.geometrySame,true,smiles);assert.equal(result.stereoUnchanged,true,smiles);assert.ok(result.pathsPerGradient.every(n=>n===2));
    if(smiles.includes('@'))assert.ok(result.stereoCount>0,'The stereochemical fixture must actually contain a wedge or hashed wedge.');
  }
  await mkdir(artifacts,{recursive:true});await writeFile(path.join(artifacts,'geometry.json'),JSON.stringify(reports,null,2)+'\n');t.diagnostic(JSON.stringify(reports));
});
