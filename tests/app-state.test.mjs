import {boundImportFixture} from './bound-import-fixture.mjs';
import {boundRenderFixture} from './bound-render-fixture.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

// Real browser UI with fault injection only at its HTTP/file-input boundaries.
const {chromium}=createRequire(import.meta.url)('playwright');
const publicPath=fileURLToPath(new URL('../public/',import.meta.url));
const style={bondWidth:4.2,labelSize:36,bondSpacing:0.14,color:true};
function graphFor(symbols){
  const atoms=[...symbols].map((symbol,i)=>({id:`a${i}`,symbol,x:i*1.5,y:0,charge:0,isotope:0,explicitHs:0,noImplicit:false,aromatic:false,chiralTag:0,radicals:0,mapNum:0}));
  return {atoms,bonds:atoms.slice(1).map((atom,i)=>({id:`b${i}`,begin:`a${i}`,end:atom.id,order:1,direction:0,stereo:0,stereoAtoms:[]}))};
}
function resultFor(graph,appearance=style){
  return {graph,scene:{svg:`<svg xmlns="http://www.w3.org/2000/svg"><path d="M 10 10 L ${20+graph.atoms.length*50} 10" style="stroke:#000000;stroke-width:3;fill:none"/></svg>`,positions:graph.atoms.map(a=>({id:a.id,x:10+a.x*50,y:10})),transform:{x0:10,y0:10,xScale:50,yScale:-50},width:800,height:500,formula:'fixture',smiles:graph.atoms.map(a=>a.symbol).join(''),mass:42,style:appearance}};
}
const projectFor=graph=>({schema:'nivram/1',version:'0.1.0-alpha.1',graph,style,export:{width:2000,marginMode:'percent',margin:2}});
async function fixture(t){
  const fault={malformedNext:false,svgNext:null,deferNext:null,release:null};
  const server=createServer(async(req,res)=>{
    try{
      if(req.url==='/api/status'){res.setHeader('Content-Type','application/json');return res.end(JSON.stringify({version:'test',rdkit:'fixture'}));}
      if(req.url.startsWith('/api/')){
        if(!['/api/import','/api/render','/api/clean'].includes(req.url)){
          res.statusCode=503;res.setHeader('Content-Type','application/json');return res.end(JSON.stringify({error:'This focused fixture supplies only the editor chemistry boundary.'}));
        }
        const chunks=[];for await(const chunk of req)chunks.push(chunk);
        const data=JSON.parse(Buffer.concat(chunks).toString());
        const graph=req.url==='/api/import'?graphFor(data.source):data.graph;
        const result=resultFor(graph,data.style||style);
        if(fault.malformedNext){fault.malformedNext=false;result.scene.svg='<svg invalid';}
        if(fault.svgNext){result.scene.svg=fault.svgNext;fault.svgNext=null;}
        if(fault.deferNext){const started=fault.deferNext;fault.deferNext=null;await new Promise(resolve=>{fault.release=resolve;started();});}
        res.setHeader('Content-Type','application/json');return res.end(JSON.stringify(req.url==='/api/import'?boundImportFixture(data,result):boundRenderFixture(req.url.slice(5),data,result)));
      }
      const relative=req.url==='/'?'index.html':req.url.slice(1);
      const filename=path.resolve(publicPath,relative);
      if(!filename.startsWith(publicPath)){res.statusCode=404;return res.end();}
      res.setHeader('Content-Type',relative.endsWith('.mjs')?'text/javascript':relative.endsWith('.css')?'text/css':'text/html');
      res.end(await readFile(filename));
    }catch(error){res.statusCode=500;res.end(JSON.stringify({error:error.message}));}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  t.after(async()=>{fault.release?.();await browser?.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
  browser=await chromium.launch({headless:true,...(process.env.NIVRAM_BROWSER_EXECUTABLE?{executablePath:process.env.NIVRAM_BROWSER_EXECUTABLE}:{})});
  const page=await browser.newPage({acceptDownloads:true});
  page.on('dialog',dialog=>dialog.accept());
  const url=`http://127.0.0.1:${server.address().port}/`,external=new Set();
  await page.route('**/*',route=>{
    const target=route.request().url();
    if(target.startsWith(url)||target.startsWith('blob:')||target.startsWith('data:'))return route.continue();
    external.add(new URL(target).origin);return route.abort();
  });
  t.after(()=>{if(external.size)t.diagnostic(`Focused fixture blocked external browser injection: ${[...external].join(', ')}. This is not a privacy acceptance check.`);});
  await page.goto(url);
  await ready(page);
  return {page,fault};
}
const ready=page=>page.waitForFunction(()=>document.querySelector('#request-status').textContent==='Ready');
async function importText(page,text){
  await page.click('#import-button');await page.fill('#structure-input',text);await page.click('#load-structure');await ready(page);
  await page.waitForFunction(()=>!document.querySelector('#import-dialog').open);
}
async function downloadedProject(page){
  const downloadPromise=page.waitForEvent('download');await page.click('#save-button');const download=await downloadPromise;
  const chunks=[];for await(const chunk of await download.createReadStream())chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString());
}

test('malformed render response preserves the previous document, artwork, and history',async t=>{
  const {page,fault}=await fixture(t);await importText(page,'CO');
  const before=await downloadedProject(page),artwork=await page.locator('#drawing').innerHTML();
  fault.malformedNext=true;
  await page.click('#import-button');await page.fill('#structure-input','NNN');await page.click('#load-structure');
  await page.waitForFunction(()=>!document.querySelector('#toast').hidden&&document.querySelector('#toast').classList.contains('error'));
  await ready(page);await page.evaluate(()=>document.querySelector('#import-dialog').close());
  assert.deepEqual((await downloadedProject(page)).graph,before.graph);
  assert.equal(await page.locator('#drawing').innerHTML(),artwork);
  await page.click('#undo-button');await ready(page);
  assert.equal((await downloadedProject(page)).graph.atoms.length,0);
});

test('a delayed file read cannot replace a drawing edited after the file was selected',async t=>{
  const {page}=await fixture(t);await importText(page,'CC');
  await page.evaluate(contents=>{
    const file=new File([contents],'delayed.nivram',{type:'application/json'});
    file.text=()=>new Promise(resolve=>{window.releaseFileRead=()=>resolve(contents);});
    window.pendingFileOpen=document.querySelector('#file-input').onchange({target:{files:[file],value:'delayed.nivram'}});
  },JSON.stringify(projectFor(graphFor('NNN'))));
  await importText(page,'CO');const before=await downloadedProject(page);
  await page.evaluate(async()=>{window.releaseFileRead();await window.pendingFileOpen;});
  assert.deepEqual((await downloadedProject(page)).graph,before.graph);
});

test('a view installation error rolls back artwork, settings, document and history together',async t=>{
  const {page}=await fixture(t);await importText(page,'CO');
  const before=await downloadedProject(page),artwork=await page.locator('#drawing').innerHTML();
  await page.evaluate(()=>{
    const drawing=document.querySelector('#drawing'),replace=drawing.replaceChildren;
    drawing.replaceChildren=function(...nodes){this.replaceChildren=replace;replace.apply(this,nodes);throw new Error('Injected view failure');};
  });
  await page.click('#import-button');await page.fill('#structure-input','NNN');await page.click('#load-structure');
  await page.waitForFunction(()=>document.querySelector('#toast').textContent==='Injected view failure');await ready(page);
  await page.evaluate(()=>document.querySelector('#import-dialog').close());
  assert.deepEqual(await downloadedProject(page),before);assert.equal(await page.locator('#drawing').innerHTML(),artwork);
  assert.equal(await page.locator('#margin-mode option').count(),2);
  await page.click('#undo-button');await ready(page);assert.equal((await downloadedProject(page)).graph.atoms.length,0);
});

test('a failure after fitted view installation restores manual zoom through a later resize',async t=>{
  const {page}=await fixture(t);await importText(page,'CO');await page.click('#zoom-in');
  const before=await downloadedProject(page),view=await page.locator('#canvas').getAttribute('viewBox');
  await page.evaluate(()=>{
    const layer=document.querySelector('#selection'),replace=layer.replaceChildren;
    layer.replaceChildren=function(...nodes){this.replaceChildren=replace;replace.apply(this,nodes);throw new Error('Injected post-fit failure');};
  });
  await page.click('#import-button');await page.fill('#structure-input','NNN');await page.click('#load-structure');
  await page.waitForFunction(()=>document.querySelector('#toast').textContent==='Injected post-fit failure');await ready(page);
  await page.keyboard.press('Escape');assert.deepEqual(await downloadedProject(page),before);
  assert.equal(await page.locator('#canvas').getAttribute('viewBox'),view);
  await page.setViewportSize({width:1200,height:800});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await page.locator('#canvas').getAttribute('viewBox'),view);
});

test('unsafe SVG is rejected before it can enter the live drawing',async t=>{
  const {page,fault}=await fixture(t);await importText(page,'CO');
  const before=await downloadedProject(page),artwork=await page.locator('#drawing').innerHTML();
  for(const content of ['<script>window.unsafe=true</script>','<g onclick="window.unsafe=true"><path d="M0 0L1 1"/></g>','<image href="https://example.invalid/remote.png"/>','<path d="M0 0L1 1" style="fill:url(https://example.invalid/fill)"/>']){
    fault.svgNext=`<svg xmlns="http://www.w3.org/2000/svg">${content}</svg>`;
    await page.click('#import-button');await page.fill('#structure-input','NNN');await page.click('#load-structure');
    await ready(page);await page.waitForFunction(()=>!document.querySelector('#toast').hidden&&document.querySelector('#toast').classList.contains('error'));
    await page.evaluate(()=>document.querySelector('#import-dialog').close());
    assert.deepEqual(await downloadedProject(page),before);assert.equal(await page.locator('#drawing').innerHTML(),artwork);
  }
  assert.equal(await page.evaluate(()=>window.unsafe),undefined);
});

test('Escape cancels an outstanding render and a subsequent retry survives its late response',async t=>{
  const {page,fault}=await fixture(t);await importText(page,'CC');
  const started=new Promise(resolve=>{fault.deferNext=resolve;});
  await page.click('#import-button');await page.fill('#structure-input','NNN');await page.click('#load-structure');await started;
  await page.keyboard.press('Escape');await ready(page);
  await page.evaluate(()=>document.querySelector('#import-dialog').close());
  await importText(page,'CO');const before=await downloadedProject(page);
  fault.release();assert.deepEqual(await downloadedProject(page),before);
});

test('native project opening validates all settings, supports undo, and marks recovery unsaved',async t=>{
  const {page}=await fixture(t);await importText(page,'CO');
  const before=await page.evaluate(()=>window.nivramDocument.getSnapshot());
  const project=projectFor(graphFor('NN'));project.export.width=1234;project.analysis=[{kind:'fixture',status:'unavailable'}];
  assert.equal(await page.evaluate(project=>window.nivramDocument.openProject(project),project),true);
  assert.equal(await page.inputValue('#export-width'),'1234');
  let current=await page.evaluate(()=>window.nivramDocument.getSnapshot());assert.deepEqual(current.document.analysis,project.analysis);
  assert.equal(await page.evaluate(({revision,document:project})=>window.nivramDocument.markSaved(revision,project),current),true);
  await page.click('#undo-button');await ready(page);
  assert.deepEqual((await page.evaluate(()=>window.nivramDocument.getSnapshot())).document,before.document);
  await page.click('#redo-button');await ready(page);
  current=await page.evaluate(()=>window.nivramDocument.getSnapshot());assert.equal(current.dirty,false);
  assert.equal(await page.evaluate(project=>window.nivramDocument.openProject(project,{asUnsaved:true}),current.document),true);
  assert.equal((await page.evaluate(()=>window.nivramDocument.getSnapshot())).dirty,true);
  assert.equal(await page.evaluate(({document:project,revision})=>window.nivramDocument.openProject(project,{expectedRevision:revision-1}),current),false);
  assert.equal(await page.evaluate(project=>window.nivramDocument.openProject({...project,schema:'nivram/99'}),current.document),false);
  assert.deepEqual((await page.evaluate(()=>window.nivramDocument.getSnapshot())).document,current.document);
});

test('Escape cancels a PNG without a download or document edit, and the next export succeeds',async t=>{
  const {page}=await fixture(t);await importText(page,'CO');
  const before=await page.evaluate(()=>window.nivramDocument.getSnapshot());let downloads=0;
  page.on('download',()=>downloads++);
  await page.evaluate(()=>{
    window.originalImageDecode=HTMLImageElement.prototype.decode;
    HTMLImageElement.prototype.decode=function(){window.pngDecodeStarted=true;return new Promise(()=>{});};
  });
  await page.click('#export-button');await page.waitForFunction(()=>window.pngDecodeStarted);
  await page.keyboard.press('Escape');await ready(page);
  await page.evaluate(()=>{HTMLImageElement.prototype.decode=window.originalImageDecode;});
  assert.equal(downloads,0);assert.deepEqual(await page.evaluate(()=>window.nivramDocument.getSnapshot()),before);
  const downloaded=page.waitForEvent('download');await page.click('#export-button');const download=await downloaded;
  const chunks=[];for await(const chunk of await download.createReadStream())chunks.push(chunk);
  const png=Buffer.concat(chunks);assert.equal(png.readUInt32BE(16),2000);await ready(page);
  assert.deepEqual(await page.evaluate(()=>window.nivramDocument.getSnapshot()),before);assert.equal(downloads,1);
});
