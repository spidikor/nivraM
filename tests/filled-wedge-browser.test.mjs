import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {productionEditor,ready} from './editor-browser-fixture.mjs';

// Synthetic geometry and actual production output; no reference image or
// held-out data is used here.
const repository=fileURLToPath(new URL('../',import.meta.url));
const run=process.env.NIVRAM_THEME_RUN??'filled-wedge-browser';
if(!/^[a-z0-9-]+$/.test(run))throw new Error('Use an owned filled-wedge test run name.');
const artifacts=path.join(repository,'../checks',run,'artifacts');
const triangle='M 0,0 L 20,-2 L 20,2 Z';
const quadrilateral='M 40,-4 L 40,4 L 20,2 L 20,-2 Z';
const paint=color=>`fill:${color};fill-rule:evenodd;stroke:${color};stroke-width:1px;stroke-linecap:butt;stroke-linejoin:miter;stroke-opacity:1;fill-opacity:1`;
const wedgePath=(d,color,extra='')=>`<path class="bond-0 atom-0 atom-1" d="${d}" style="${paint(color)}${extra}"/>`;
const svg=body=>`<svg xmlns="http://www.w3.org/2000/svg">${body}</svg>`;
const pair=svg(wedgePath(triangle,'#000000')+wedgePath(quadrilateral,'#333399'));

async function imported(page,smiles){
  await page.click('#import-button');await page.fill('#structure-input',smiles);await page.click('#load-structure');
  await page.waitForFunction(()=>!document.getElementById('import-dialog').open);await ready(page);
}
async function snapshot(page){
  return page.evaluate(()=>({document:window.nivramDocument.getSnapshot(),drawing:document.getElementById('drawing').innerHTML,
    undo:document.getElementById('undo-button').disabled,redo:document.getElementById('redo-button').disabled}));
}
function fullWidth(paint,width,label){
  assert.ok(paint,'The fixture needs a generated filled-wedge gradient.');
  assert.equal(paint.units,'userSpaceOnUse',label);
  assert.equal(paint.stops.length,4,label);
  assert.equal(paint.stops[0].offset,0,label);assert.equal(paint.stops[3].offset,1,label);
  const axisLength=Math.hypot(paint.x2-paint.x1,paint.y2-paint.y1);
  assert.ok(axisLength>width,label);
  assert.ok(Math.abs((paint.stops[2].offset-paint.stops[1].offset)*axisLength-width)<1e-8,
    `${label}: transition span must equal the full validated ordinary-bond width, not the quarter-width outline.`);
  assert.ok(paint.stops.every((stop,i,a)=>stop.offset>=0&&stop.offset<=1&&(!i||stop.offset>=a[i-1].offset)),label);
  assert.equal(paint.stops[0].color,paint.stops[1].color,label);
  assert.equal(paint.stops[2].color,paint.stops[3].color,label);
  assert.notEqual(paint.stops[0].color,paint.stops[3].color,label);
}

test('only explicit valid full bond width paints an unambiguous triangle and quadrilateral without changing geometry',{timeout:120000},async t=>{
  const page=await productionEditor(t);
  const cases=[
    {name:'triangle then quadrilateral',source:pair},
    {name:'reversed child order',source:svg(wedgePath(quadrilateral,'#333399')+wedgePath(triangle,'#000000'))},
    {name:'reversed winding and starting vertices',source:svg(wedgePath('M 20,2 L 20,-2 L 0,0 Z','#000000')+wedgePath('M 20,2 L 40,4 L 40,-4 L 20,-2 Z','#333399'))},
  ];
  for(const item of cases)for(const width of [.5,4,5]){
    const result=await page.evaluate(async({source,width})=>{
      const {sanitizeSvg}=await import('/modules/document.mjs');
      const original=new DOMParser().parseFromString(source,'image/svg+xml'),fragment=sanitizeSvg(source,document,{bondWidth:width});
      const gradients=[...fragment.querySelectorAll('linearGradient')],paths=[...fragment.querySelectorAll('path')];
      const gradient=gradients[0],reference=/^url\(["']?#([^"')]+)["']?\)$/;
      return {count:gradients.length,paths:paths.map(p=>p.getAttribute('d')),originalPaths:[...original.querySelectorAll('path')].map(p=>p.getAttribute('d')),
        classesSame:paths.every((p,i)=>p.getAttribute('class')===original.querySelectorAll('path')[i].getAttribute('class')),
        localFillAndStroke:paths.every(p=>reference.exec(p.style.fill)?.[1]===gradient?.id&&reference.exec(p.style.stroke)?.[1]===gradient?.id),
        outlineWidths:paths.map(p=>p.style.strokeWidth),paint:gradient?{units:gradient.getAttribute('gradientUnits'),
          x1:Number(gradient.getAttribute('x1')),y1:Number(gradient.getAttribute('y1')),x2:Number(gradient.getAttribute('x2')),y2:Number(gradient.getAttribute('y2')),
          stops:[...gradient.children].map(s=>({offset:Number(s.getAttribute('offset')),color:s.getAttribute('stop-color')}))}:null};
    },{source:item.source,width});
    assert.equal(result.count,1,item.name);assert.deepEqual(result.paths,result.originalPaths,item.name);
    assert.equal(result.classesSame,true,item.name);assert.equal(result.localFillAndStroke,true,item.name);
    assert.deepEqual(result.outlineWidths,['1px','1px'],item.name);fullWidth(result.paint,width,item.name);
    const startColor=result.paint.x1===0?'#000000':'#333399',endColor=result.paint.x2===40?'#333399':'#000000';
    assert.equal(result.paint.stops[0].color,startColor,item.name);assert.equal(result.paint.stops[3].color,endColor,item.name);
  }
  for(const paintStyle of [{},{bondWidth:undefined},{bondWidth:0},{bondWidth:.49},{bondWidth:5.01},{bondWidth:'4'},{bondWidth:null},{bondWidth:NaN},{bondWidth:Infinity}]){
    const result=await page.evaluate(async({source,paintStyle})=>{
      const {sanitizeSvg}=await import('/modules/document.mjs');
      try{const fragment=sanitizeSvg(source,document,paintStyle);return {rejected:false,count:fragment.querySelectorAll('linearGradient').length,paths:[...fragment.querySelectorAll('path')].map(p=>p.getAttribute('d'))};}
      catch{return {rejected:true};}
    },{source:pair,paintStyle});
    if(!result.rejected){assert.equal(result.count,0,'Absent or invalid full width cannot paint a filled wedge.');assert.deepEqual(result.paths,[triangle,quadrilateral]);}
  }
  await imported(page,'N[C@H](O)S');
  const prepared=await page.evaluate(async()=>{
    const {prepareRender}=await import('/modules/document.mjs'),base=window.nivramDocument.getSnapshot().document;
    const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:base.graph,style:{...base.style,bondWidth:2.4}})});
    if(!response.ok)throw new Error('Actual renderer failed the validated-style fixture.');
    const result=await response.json(),prepared=prepareRender(result,base,document);
    const filled=[...prepared.fragment.querySelectorAll('path')].filter(p=>p.style.fill.startsWith('url(')),id=/^url\(["']?#([^"')]+)["']?\)$/.exec(filled[0]?.style.fill??'')?.[1];
    const gradient=[...prepared.fragment.querySelectorAll('linearGradient')].find(g=>g.id===id);
    let invalidRejected=false;try{prepareRender({...result,scene:{...result.scene,style:{...result.scene.style,bondWidth:5.1}}},base,document);}catch{invalidRejected=true;}
    return {style:prepared.project.style.bondWidth,filledCount:filled.length,invalidRejected,paint:gradient?{units:gradient.getAttribute('gradientUnits'),
      x1:Number(gradient.getAttribute('x1')),y1:Number(gradient.getAttribute('y1')),x2:Number(gradient.getAttribute('x2')),y2:Number(gradient.getAttribute('y2')),
      stops:[...gradient.children].map(s=>({offset:Number(s.getAttribute('offset')),color:s.getAttribute('stop-color')}))}:null};
  });
  assert.equal(prepared.style,2.4);assert.equal(prepared.filledCount,2);assert.equal(prepared.invalidRejected,true);
  fullWidth(prepared.paint,2.4,'prepareRender must pass its validated scene style to the sanitizer');
});

test('filled-wedge paint stays local across connected clones and refuses ambiguous or untrusted SVG resources',{timeout:120000},async t=>{
  const page=await productionEditor(t);
  const noPaint=[
    {name:'same color',source:pair.replaceAll('#333399','#000000')},
    {name:'non-adjacent pieces',source:svg(wedgePath(triangle,'#000000')+'<path d="M 0,10 L 40,10" style="fill:none;stroke:#000000;stroke-width:4px"/>'+wedgePath(quadrilateral,'#333399'))},
    {name:'unclosed triangle',source:pair.replace(triangle,triangle.replace(' Z',''))},
    {name:'curved triangle',source:pair.replace(triangle,'M 0,0 Q 10,0 20,-2 L 20,2 Z')},
    {name:'missing exact common edge',source:pair.replace(quadrilateral,'M 40,-4 L 40,4 L 20,2.1 L 20,-2 Z')},
    {name:'duplicate vertex',source:pair.replace(triangle,'M 0,0 L 20,-2 L 20,-2 Z')},
    {name:'zero area',source:pair.replace(triangle,'M 0,0 L 20,0 L 40,0 Z')},
    {name:'crossed quadrilateral',source:pair.replace(quadrilateral,'M 40,-4 L 40,4 L 20,-2 L 20,2 Z')},
    {name:'concave quadrilateral',source:pair.replace(quadrilateral,'M 40,-4 L 22,0 L 20,2 L 20,-2 Z')},
    {name:'bent taper axis',source:pair.replace(quadrilateral,'M 40,8 L 40,16 L 20,2 L 20,-2 Z')},
    {name:'different bond classes',source:pair.replace('class="bond-0 atom-0 atom-1" d="M 40','class="bond-1 atom-0 atom-1" d="M 40')},
    {name:'different outline styles',source:pair.replace('stroke-width:1px;stroke-linecap:butt','stroke-width:2px;stroke-linecap:butt')},
    {name:'fill does not match its stroke',source:pair.replace('fill:#333399','fill:#FF0000')},
    {name:'transparent fill',source:pair.replaceAll('fill-opacity:1','fill-opacity:0.5')},
    {name:'transparent outline',source:pair.replaceAll('stroke-opacity:1','stroke-opacity:0.5')},
    {name:'duplicate color declaration',source:svg(wedgePath(triangle,'#000000',';fill:#000000')+wedgePath(quadrilateral,'#333399'))},
    {name:'inherited transform',source:svg(`<g transform="translate(5 5)">${wedgePath(triangle,'#000000')+wedgePath(quadrilateral,'#333399')}</g>`)},
    {name:'inherited opacity',source:svg(`<g opacity="0.5">${wedgePath(triangle,'#000000')+wedgePath(quadrilateral,'#333399')}</g>`)},
    {name:'ambiguous duplicate triangle',source:svg(wedgePath(triangle,'#000000')+wedgePath(triangle,'#000000')+wedgePath(quadrilateral,'#333399'))},
  ];
  for(const item of noPaint){
    const result=await page.evaluate(async source=>{
      const {sanitizeSvg}=await import('/modules/document.mjs'),original=new DOMParser().parseFromString(source,'image/svg+xml');
      try{const fragment=sanitizeSvg(source,document,{bondWidth:4});return {rejected:false,count:fragment.querySelectorAll('linearGradient').length,
        unchanged:[...fragment.querySelectorAll('path')].every((p,i)=>p.getAttribute('d')===original.querySelectorAll('path')[i].getAttribute('d')&&p.getAttribute('style')===original.querySelectorAll('path')[i].getAttribute('style'))};}
      catch{return {rejected:true};}
    },item.source);
    if(!result.rejected){assert.equal(result.count,0,item.name);assert.equal(result.unchanged,true,item.name);}
  }
  const isolated=await page.evaluate(async source=>{
    const {sanitizeSvg}=await import('/modules/document.mjs'),{rebaseBondPaint}=await import('/modules/bond-paint.mjs');
    const make=()=>{const root=document.createElementNS('http://www.w3.org/2000/svg','svg');root.append(sanitizeSvg(source,document,{bondWidth:4}));document.body.append(root);return root;};
    const first=make(),second=make(),before=first.outerHTML,clone=first.cloneNode(true);
    rebaseBondPaint(clone);document.body.append(clone);
    try{
      const nodes=[first,second,clone],ids=nodes.map(root=>root.querySelector('linearGradient')?.id),reference=/^url\(["']?#([^"')]+)["']?\)$/;
      const local=nodes.every(root=>[...root.querySelectorAll('path')].every(p=>reference.exec(p.style.fill)?.[1]===root.querySelector('linearGradient')?.id&&reference.exec(p.style.stroke)?.[1]===root.querySelector('linearGradient')?.id));
      const reserved=sanitizeSvg(source.replace('class="bond-0',`id="${ids[0]}" class="bond-0`),document,{bondWidth:4});
      return {ids,local,originalUnchanged:first.outerHTML===before,reservedIdsAvoided:![...reserved.querySelectorAll('linearGradient')].some(g=>ids.includes(g.id))};
    }finally{first.remove();second.remove();clone.remove();}
  },pair);
  assert.equal(isolated.ids.filter(Boolean).length,3);assert.equal(new Set(isolated.ids).size,3);
  assert.equal(isolated.local,true);assert.equal(isolated.originalUnchanged,true);assert.equal(isolated.reservedIdsAvoided,true);
  const rejected=await page.evaluate(async()=>{
    const {sanitizeSvg}=await import('/modules/document.mjs');
    const bodies=['<defs><linearGradient id="x"><stop offset="0" stop-color="#000000"/></linearGradient></defs>',
      '<linearGradient id="x"/>','<path d="M0 0L1 1" style="fill:url(#x);stroke:#000000"/>',
      '<path d="M0 0L1 1" fill="url(https://example.invalid/paint.svg#x)"/>',
      '<path d="M0 0L1 1" style="fill:#000000;stroke:url(#x)"/>','<path href="#x" d="M0 0L1 1"/>'];
    return bodies.map(body=>{try{sanitizeSvg(`<svg xmlns="http://www.w3.org/2000/svg">${body}</svg>`,document,{bondWidth:4});return false;}catch{return true;}});
  });
  assert.ok(rejected.every(Boolean),'Incoming fill/stroke resource references and paint servers remain rejected before generated paint.');
});

async function png(page,kind,label){
  if(kind==='preview'){
    const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Save this PNG',exact:true}).click()]);
    const filename=path.join(artifacts,label+'.png');await download.saveAs(filename);assert.equal(await download.failure(),null);
    return 'data:image/png;base64,'+(await readFile(filename)).toString('base64');
  }
  return page.evaluate(async kind=>{
    const asData=blob=>new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(blob);});
    const project=window.nivramDocument.getSnapshot().document;
    if(kind==='main'){const {exportPng}=await import('/modules/export.mjs');return asData((await exportPng(document.getElementById('drawing'),project.export,{graph:project.graph})).blob);}
    const {renderBatchPng}=await import('/modules/batch-export.mjs');
    const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:project.graph,style:project.style})});
    if(!response.ok)throw new Error('Actual filled-wedge batch preparation failed.');
    return asData((await renderBatchPng({...await response.json(),project})).blob);
  },kind);
}
async function pngMetrics(page,data){
  return page.evaluate(async data=>{
    const image=new Image();image.src=data;await image.decode();
    const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
    const context=canvas.getContext('2d');context.drawImage(image,0,0);const p=context.getImageData(0,0,canvas.width,canvas.height).data;
    let nonOpaque=0,nonwhiteMargin=0,black=0,blue=0;
    for(let y=0;y<canvas.height;y++)for(let x=0;x<canvas.width;x++){
      const i=(y*canvas.width+x)*4,white=p[i]===255&&p[i+1]===255&&p[i+2]===255;
      if(p[i+3]!==255)nonOpaque++;
      if(!white&&(x<13||x>=canvas.width-13||y<13||y>=canvas.height-13))nonwhiteMargin++;
      if(p[i]===0&&p[i+1]===0&&p[i+2]===0)black++;
      if(p[i]===51&&p[i+1]===51&&p[i+2]===153)blue++;
    }
    const digest=await crypto.subtle.digest('SHA-256',p);
    const result={width:canvas.width,height:canvas.height,nonOpaque,nonwhiteMargin,black,blue,rgbaSha256:[...new Uint8Array(digest)].map(v=>v.toString(16).padStart(2,'0')).join('')};
    image.removeAttribute('src');canvas.width=canvas.height=1;return result;
  },data);
}
async function actualPaint(page){
  return page.evaluate(async()=>{
    const project=window.nivramDocument.getSnapshot().document;
    const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:project.graph,style:project.style})});
    if(!response.ok)throw new Error('Actual filled-wedge geometry comparison failed.');
    const {scene}=await response.json(),original=new DOMParser().parseFromString(scene.svg,'image/svg+xml'),plain=[...original.querySelectorAll('path')],drawing=document.getElementById('drawing'),paths=[...drawing.querySelectorAll('path')];
    const filled=paths.filter(p=>p.style.fill.startsWith('url(')),reference=/^url\(["']?#([^"')]+)["']?\)$/;
    const id=reference.exec(filled[0]?.style.fill??'')?.[1],gradient=[...drawing.querySelectorAll('linearGradient')].find(g=>g.id===id);
    if(!gradient)return {filledCount:filled.length,paint:null};
    const x1=Number(gradient.getAttribute('x1')),y1=Number(gradient.getAttribute('y1')),x2=Number(gradient.getAttribute('x2')),y2=Number(gradient.getAttribute('y2'));
    const stops=[...gradient.children].map(s=>({offset:Number(s.getAttribute('offset')),color:s.getAttribute('stop-color')}));
    // Render only actual generated wedge paths at known SVG coordinates. This
    // isolates opaque interior samples from labels, other bonds and edge AA.
    const coordinates=filled.flatMap(p=>[...p.getAttribute('d').matchAll(/[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?/g)].map(v=>Number(v[0]))),xs=coordinates.filter((_,i)=>i%2===0),ys=coordinates.filter((_,i)=>i%2===1);
    const left=Math.min(...xs)-3,top=Math.min(...ys)-3,width=Math.max(...xs)-left+3,height=Math.max(...ys)-top+3,scale=8;
    const source=`<svg xmlns="http://www.w3.org/2000/svg" width="${Math.ceil(width*scale)}" height="${Math.ceil(height*scale)}" viewBox="${left} ${top} ${Math.ceil(width*scale)/scale} ${Math.ceil(height*scale)/scale}"><defs>${gradient.outerHTML}</defs>${filled.map(p=>p.outerHTML).join('')}</svg>`;
    const image=new Image();image.src='data:image/svg+xml;base64,'+btoa(source);await image.decode();
    const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;
    const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);const pixels=ctx.getImageData(0,0,canvas.width,canvas.height).data;
    const samples=[],seen=new Set(),low=stops[1].offset,high=stops[2].offset;
    for(let i=0;i<=32;i++){
      const fraction=low+(high-low)*(.1+.8*i/32),x=Math.floor((x1+(x2-x1)*fraction-left)*scale),y=Math.floor((y1+(y2-y1)*fraction-top)*scale),key=x+','+y;
      if(seen.has(key))continue;seen.add(key);const offset=(y*canvas.width+x)*4;samples.push({x,y,rgba:[...pixels.slice(offset,offset+4)]});
    }
    image.removeAttribute('src');canvas.width=canvas.height=1;
    return {filledCount:filled.length,geometrySame:plain.length===paths.length&&plain.every((p,i)=>p.getAttribute('d')===paths[i].getAttribute('d')&&p.getAttribute('class')===paths[i].getAttribute('class')),
      localFillAndStroke:filled.every(p=>reference.exec(p.style.fill)?.[1]===id&&reference.exec(p.style.stroke)?.[1]===id),
      paint:{units:gradient.getAttribute('gradientUnits'),x1,y1,x2,y2,stops},samples};
  });
}

test('actual stereochemical import preserves geometry and smooth filled paint through both themes and all PNG paths',{timeout:180000},async t=>{
  await mkdir(artifacts,{recursive:true});const page=await productionEditor(t),report={exports:{},paint:{}};
  await page.evaluate(async()=>{const {initializeProfiles}=await import('/modules/profiles.mjs');await initializeProfiles(window.nivramDocument,{list_profiles:async()=>({profiles:[]})});});
  await imported(page,'N[C@H](O)S');await page.fill('#export-width','640');
  const initialGraph=(await snapshot(page)).document.document.graph;
  const initialWidth=(await snapshot(page)).document.document.style.bondWidth;
  for(const width of [initialWidth,2.4]){
    if(width!==initialWidth){await page.fill('#bond-width',String(width));await page.click('#apply-style');await ready(page);}
    if(await page.locator('#theme-toggle').getAttribute('aria-pressed')==='true')await page.click('#theme-toggle');
    const before=await snapshot(page);assert.deepEqual(before.document.document.graph,initialGraph);assert.equal(before.document.document.style.bondWidth,width);
    const paint=await actualPaint(page);assert.equal(paint.filledCount,2);assert.equal(paint.geometrySame,true);assert.equal(paint.localFillAndStroke,true);fullWidth(paint.paint,width,'actual renderer');
    assert.ok(paint.samples.length>=8,'The wedge interior must span several independent opaque samples.');
    assert.ok(paint.samples.every(s=>s.rgba[3]===255),'Sample the solid wedge interior, not antialiased boundaries.');
    const start=paint.paint.stops[0].color==='#000000'?0:153,end=paint.paint.stops[3].color==='#000000'?0:153,direction=Math.sign(end-start);
    const values=paint.samples.map(s=>s.rgba[2]);
    assert.ok(values.every(v=>v>Math.min(start,end)&&v<Math.max(start,end)),'Interior samples must include mixed endpoint colours rather than a hard split.');
    assert.ok(new Set(values).size>=6,'A one-pixel boundary cannot satisfy a substantive vector ramp.');
    assert.ok(values.every((v,i)=>!i||direction*(v-values[i-1])>=-1),'The interior transition must progress monotonically along its gradient axis.');
    report.paint[String(width)]=paint;
    let baseline=null;
    for(const theme of ['light','dark']){
      if(await page.locator('#theme-toggle').getAttribute('aria-pressed')!==(theme==='dark'?'true':'false'))await page.click('#theme-toggle');
      const canonical=await page.evaluate(()=>[...document.querySelectorAll('#drawing linearGradient stop')].filter(s=>s.getAttribute('stop-color')==='#000000').map(s=>({inline:s.getAttribute('stop-color'),computed:getComputedStyle(s).stopColor})));
      assert.ok(canonical.length>0);for(const stop of canonical){assert.equal(stop.inline,'#000000');assert.equal(stop.computed,theme==='dark'?'rgb(214, 214, 214)':'rgb(0, 0, 0)');}
      for(const kind of ['main','batch','preview']){
        const label=`wedge-${width}-${theme}-${kind}`;
        if(kind==='preview'){
          const details=page.locator('.profile-controls');if(await details.getAttribute('open')===null)await details.locator('summary').click();
          await page.click('#preview-profile-button');
          await page.waitForFunction(()=>{const image=document.getElementById('png-preview-image'),button=[...document.querySelectorAll('#png-preview-dialog button')].find(e=>e.textContent==='Save this PNG');return image.complete&&image.naturalWidth===640&&button&&!button.disabled;});
        }
        const data=await png(page,kind,label),metrics=await pngMetrics(page,data);
        await writeFile(path.join(artifacts,label+'.png'),Buffer.from(data.split(',')[1],'base64'));
        if(kind==='preview'){await page.getByRole('button',{name:'Close / cancel preview',exact:true}).click();await page.waitForFunction(()=>!document.getElementById('png-preview-image').hasAttribute('src'));}
        assert.equal(metrics.width,640);assert.equal(metrics.nonOpaque,0);assert.equal(metrics.nonwhiteMargin,0);assert.ok(metrics.black>0);assert.ok(metrics.blue>0);
        if(baseline===null)baseline=metrics;else assert.deepEqual(metrics,baseline,`${label} must match the light main RGBA exactly.`);
        assert.deepEqual(await snapshot(page),before,'Theme/export must preserve graph, coordinates, style, revision, history controls and source artwork.');
        report.exports[label]=metrics;
      }
    }
  }
  assert.equal(await page.locator('#png-preview-image').getAttribute('src'),null);
  await writeFile(path.join(artifacts,'filled-wedge.json'),JSON.stringify(report,null,2)+'\n');t.diagnostic(JSON.stringify(report));
});
