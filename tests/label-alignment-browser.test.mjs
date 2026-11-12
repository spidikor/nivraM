import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {productionEditor,ready,state} from './editor-browser-fixture.mjs';

const repository=fileURLToPath(new URL('../',import.meta.url));
const artifacts=path.join(repository,'../checks',process.env.NIVRAM_THEME_RUN??'label-alignment-browser','artifacts');

async function glyphInk(page){
  return page.evaluate(async()=>{
    const project=window.nivramDocument.getSnapshot().document;
    const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:project.graph,style:project.style})});
    if(!response.ok)throw new Error('The actual renderer could not provide its atom coordinate.');
    const {scene}=await response.json(),atom=scene.positions.find(position=>position.id===project.graph.atoms[1].id);
    const selected=[...document.querySelectorAll('#drawing [class~="atom-1"]')].filter(node=>!/(?:^|\s)bond-\d+(?:\s|$)/.test(node.getAttribute('class')??''));
    const glyphs=selected.filter(node=>!selected.some(parent=>parent!==node&&parent.contains(node)));
    if(!glyphs.length)throw new Error('The element must have actual rendered glyph artwork.');
    const bounds=glyphs.map(node=>node.getBBox()),left=Math.min(...bounds.map(box=>box.x))-2,top=Math.min(...bounds.map(box=>box.y))-2;
    const right=Math.max(...bounds.map(box=>box.x+box.width))+2,bottom=Math.max(...bounds.map(box=>box.y+box.height))+2;
    const scale=8,width=Math.ceil((right-left)*scale),height=Math.ceil((bottom-top)*scale);
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('width',String(width));svg.setAttribute('height',String(height));svg.setAttribute('viewBox',`${left} ${top} ${width/scale} ${height/scale}`);
    for(const glyph of glyphs)svg.append(glyph.cloneNode(true));
    const url=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)],{type:'image/svg+xml'}));
    const image=new Image(),canvas=document.createElement('canvas');
    try{
      await document.fonts.ready;image.src=url;await image.decode();canvas.width=width;canvas.height=height;
      const context=canvas.getContext('2d');context.drawImage(image,0,0);const pixels=context.getImageData(0,0,width,height).data;
      let x0=width,x1=-1,y0=height,y1=-1,inkPixels=0;
      for(let y=0;y<height;y++)for(let x=0;x<width;x++)if(pixels[(y*width+x)*4+3]>=128){x0=Math.min(x0,x);x1=Math.max(x1,x);y0=Math.min(y0,y);y1=Math.max(y1,y);inkPixels++;}
      if(!inkPixels)throw new Error('The rendered element glyph has no visible ink.');
      const inkCenterX=left+(x0+x1+1)/(2*scale);
      return {symbol:project.graph.atoms[1].symbol,labelSize:project.style.labelSize,glyphCount:glyphs.length,glyphKinds:glyphs.map(node=>node.localName),
        inkPixels,atomX:atom.x,inkCenterX,horizontalError:inkCenterX-atom.x,inkBounds:{x:left+x0/scale,y:top+y0/scale,width:(x1-x0+1)/scale,height:(y1-y0+1)/scale},
        rasterScale:scale,png:canvas.toDataURL('image/png')};
    }finally{URL.revokeObjectURL(url);image.removeAttribute('src');canvas.width=canvas.height=1;}
  });
}

async function exportPixels(page,data){
  return page.evaluate(async data=>{
    const image=new Image();image.src=data;await image.decode();
    const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
    const context=canvas.getContext('2d');context.drawImage(image,0,0);const pixels=context.getImageData(0,0,canvas.width,canvas.height).data;
    let ink=0,nonOpaque=0,nonwhiteMargin=0;
    for(let y=0;y<canvas.height;y++)for(let x=0;x<canvas.width;x++){
      const i=(y*canvas.width+x)*4,white=pixels[i]===255&&pixels[i+1]===255&&pixels[i+2]===255;
      if(pixels[i+3]!==255)nonOpaque++;
      if(!white){ink++;if(x<13||x>=canvas.width-13||y<13||y>=canvas.height-13)nonwhiteMargin++;}
    }
    const hash=await crypto.subtle.digest('SHA-256',pixels),result={width:canvas.width,height:canvas.height,ink,nonOpaque,nonwhiteMargin,
      rgbaSha256:[...new Uint8Array(hash)].map(value=>value.toString(16).padStart(2,'0')).join('')};
    image.removeAttribute('src');canvas.width=canvas.height=1;return result;
  },data);
}
async function png(page,kind){
  if(kind==='preview'){
    const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Save this PNG',exact:true}).click()]);
    const filename=path.join(artifacts,'aligned-labels-preview.png');await download.saveAs(filename);assert.equal(await download.failure(),null);
    return 'data:image/png;base64,'+(await readFile(filename)).toString('base64');
  }
  return page.evaluate(async kind=>{
    const project=window.nivramDocument.getSnapshot().document;let output;
    if(kind==='main'){
      const {exportPng}=await import('/modules/export.mjs');output=await exportPng(document.getElementById('drawing'),project.export,{graph:project.graph});
    }else{
      const {renderBatchPng}=await import('/modules/batch-export.mjs');
      const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:project.graph,style:project.style})});
      if(!response.ok)throw new Error('Actual annotated-label batch preparation failed.');
      output=await renderBatchPng({...await response.json(),project});
    }
    return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(output.blob);});
  },kind);
}

test('standalone element glyph ink is horizontally centered on its vertical bond atom coordinate',{timeout:120000},async t=>{
  const page=await productionEditor(t);await mkdir(artifacts,{recursive:true});
  const reports=[];
  for(const [smiles,sizes] of [['C=O',[36,24]],['C#N',[36,24]],['CF',[36]]]){
    await page.click('#import-button');await page.fill('#structure-input',smiles);await page.click('#load-structure');
    await page.waitForFunction(()=>!document.getElementById('import-dialog').open);await ready(page);
    const original=(await state(page)).document;
    assert.equal(original.graph.atoms.length,2);
    // Construct a vertical depiction of the imported graph. Subsequent style
    // changes must retain those exact molecular coordinates and annotations.
    original.graph.atoms[0].x=0;original.graph.atoms[0].y=0;
    original.graph.atoms[1].x=0;original.graph.atoms[1].y=1.5;
    for(const size of sizes){
      const project=structuredClone(original);project.style.labelSize=size;
      assert.equal(await page.evaluate(candidate=>window.nivramDocument.openProject(candidate),project),true);
      assert.deepEqual((await state(page)).document.graph,project.graph);
      const before=await state(page),result=await glyphInk(page),{png,...row}=result;
      assert.deepEqual(await state(page),before,'Observing glyph ink must not change the document or history.');
      assert.ok(row.glyphCount>0&&row.inkPixels>0);
      await writeFile(path.join(artifacts,`glyph-${row.symbol}-${size}.png`),Buffer.from(png.split(',')[1],'base64'));
      reports.push(row);
    }
  }
  await writeFile(path.join(artifacts,'label-alignment.json'),JSON.stringify(reports,null,2)+'\n');
  t.diagnostic(JSON.stringify(reports));
  // Eight raster samples per SVG unit bound pixel quantization to 0.125 units;
  // 0.5 allows that rounding while catching the observed multi-unit label drift.
  for(const row of reports)assert.ok(Math.abs(row.horizontalError)<=0.5,`${row.symbol} at ${row.labelSize}: glyph center differs from its atom by ${row.horizontalError} SVG units.`);

  await page.click('#import-button');await page.fill('#structure-input','[13CH3][NH2+]CO');await page.click('#load-structure');
  await page.waitForFunction(()=>!document.getElementById('import-dialog').open);await ready(page);await page.fill('#export-width','640');
  if(await page.locator('#theme-toggle').getAttribute('aria-pressed')==='true')await page.click('#theme-toggle');
  const capture=()=>page.evaluate(()=>({state:window.nivramDocument.getSnapshot(),svg:document.getElementById('drawing').innerHTML,
    undo:document.getElementById('undo-button').disabled,redo:document.getElementById('redo-button').disabled}));
  const before=await capture();
  const glyphPaint=()=>page.evaluate(()=>[...document.querySelectorAll('#drawing [class*="atom-"]')].filter(node=>!/(?:^|\s)bond-\d+(?:\s|$)/.test(node.getAttribute('class')??'')).map(node=>{
    const box=node.getBBox();return {classes:node.getAttribute('class'),kind:node.localName,text:node.textContent,fill:getComputedStyle(node).fill,width:box.width,height:box.height};
  }));
  const labels=await glyphPaint();assert.ok(labels.length>0);assert.ok(labels.every(label=>label.width>0&&label.height>0));
  for(const [index,property,value] of [[0,'isotope',13],[1,'charge',1]]){
    assert.equal(before.state.document.graph.atoms[index][property],value);
    const subset=labels.filter(label=>label.classes.split(/\s+/).includes(`atom-${index}`));assert.ok(subset.length>0);
    if(subset.some(label=>label.kind==='path'))assert.ok(subset.length>=3,'Annotated carbon and nitrogen must include nonempty character outlines beyond the element glyph.');
    else assert.match(subset.map(label=>label.text).join(''),property==='isotope'?/13/:/\+/);
  }
  await page.evaluate(async()=>{const {initializeProfiles}=await import('/modules/profiles.mjs');await initializeProfiles(window.nivramDocument,{list_profiles:async()=>({profiles:[]})});});
  const exports={},main=await png(page,'main');exports.lightMain=await exportPixels(page,main);
  await writeFile(path.join(artifacts,'aligned-labels-light.png'),Buffer.from(main.split(',')[1],'base64'));
  await page.click('#theme-toggle');assert.equal(await page.locator('#theme-toggle').getAttribute('aria-pressed'),'true');
  assert.deepEqual(await glyphPaint(),labels,'Dark mode must preserve glyph shapes and canonical chemical colors.');
  assert.deepEqual(await capture(),before);
  exports.darkMain=await exportPixels(page,await png(page,'main'));
  exports.darkBatch=await exportPixels(page,await png(page,'batch'));
  await page.locator('.profile-controls summary').click();await page.click('#preview-profile-button');
  await page.waitForFunction(()=>{const image=document.getElementById('png-preview-image');return image.complete&&image.naturalWidth===640&&[...document.querySelectorAll('#png-preview-dialog button')].some(button=>button.textContent==='Save this PNG'&&!button.disabled);});
  exports.darkPreview=await exportPixels(page,await png(page,'preview'));
  await page.getByRole('button',{name:'Close / cancel preview',exact:true}).click();
  for(const result of Object.values(exports)){assert.equal(result.width,640);assert.equal(result.nonOpaque,0);assert.equal(result.nonwhiteMargin,0);assert.ok(result.ink>0);assert.deepEqual(result,exports.lightMain);}
  assert.deepEqual(await capture(),before);
  await writeFile(path.join(artifacts,'aligned-export-comparison.json'),JSON.stringify({labels,exports},null,2)+'\n');t.diagnostic(JSON.stringify({annotationGlyphs:labels.length,exports}));
});
