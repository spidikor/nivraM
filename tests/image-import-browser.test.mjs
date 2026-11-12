import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {deflateSync} from 'node:zlib';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {testBrowser} from './browser-session.mjs';

// Browser integration with a synthetic native bridge. Real PNG parser, source
// preservation and native-dialog behavior are covered by the Python tests.
const publicPath=fileURLToPath(new URL('../public/',import.meta.url));
const style={bondWidth:4.2,labelSize:36,bondSpacing:.14,color:true},hash=createHash('sha256').update(Buffer.from(pngSource().split(',')[1],'base64')).digest('hex');
const graph=(prefix,symbol='O')=>({atoms:[{id:prefix+'a',symbol:'C',x:0,y:0},{id:prefix+'b',symbol,x:1.5,y:0}],bonds:[{id:prefix+'bond',begin:prefix+'a',end:prefix+'b',order:1}]});
const project={schema:'nivram/1',graph:graph('old','N'),style,export:{width:1234,marginMode:'pixels',margin:17},analysis:[{kind:'old-project'}]};
function render(graph,appearance=style){
  const positions=graph.atoms.map(a=>({id:a.id,x:80+a.x*50,y:110-a.y*50})),byId=new Map(positions.map(p=>[p.id,p]));
  const paths=graph.bonds.map(b=>{const a=byId.get(b.begin),c=byId.get(b.end);return `<path d="M${a.x} ${a.y}L${c.x} ${c.y}" fill="none" stroke="#000" stroke-width="3"/>`;}).join('');
  return {graph,scene:{svg:`<svg xmlns="http://www.w3.org/2000/svg">${paths}</svg>`,positions,transform:{x0:80,y0:110,xScale:50,yScale:-50},width:300,height:220,formula:'fixture',smiles:graph.atoms.length?'CO':'',mass:32,style:appearance}};
}
function pngSource(){
  const width=120,height=80,raw=Buffer.alloc((width*4+1)*height,255);
  for(let y=0;y<height;y++){
    raw[y*(width*4+1)]=0;
    for(let x=0;x<width;x++)if((x>=25&&x<=80&&y>=38&&y<=41)||Math.abs(Math.hypot(x-91,y-40)-8)<1.5){const offset=y*(width*4+1)+1+x*4;raw[offset]=raw[offset+1]=raw[offset+2]=0;}
  }
  const crc=data=>{let value=0xffffffff;for(const byte of data){value^=byte;for(let i=0;i<8;i++)value=(value>>>1)^((value&1)?0xedb88320:0);}return (value^0xffffffff)>>>0;};
  const chunk=(type,content)=>{const data=Buffer.concat([Buffer.from(type),content]),length=Buffer.alloc(4),check=Buffer.alloc(4);length.writeUInt32BE(content.length);check.writeUInt32BE(crc(data));return Buffer.concat([length,data,check]);};
  const header=Buffer.alloc(13);header.writeUInt32BE(width);header.writeUInt32BE(height,4);header[8]=8;header[9]=6;
  return 'data:image/png;base64,'+Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',header),chunk('IDAT',deflateSync(raw)),chunk('IEND',Buffer.alloc(0))]).toString('base64');
}
async function fixture(t){
  const fault={large:false,badImage:false,badIdentity:false,holdPreview:false,holdRender:false,started:null,release:null,intakes:[],renders:[]},component=graph('image');
  const server=createServer(async(req,res)=>{
    try{
      if(req.url==='/api/status'){res.setHeader('Content-Type','application/json');return res.end(JSON.stringify({version:'fixture',rdkit:'test'}));}
      if(req.url.startsWith('/api/')||req.url.startsWith('/fixture/')){
        const chunks=[];for await(const chunk of req)chunks.push(chunk);const data=chunks.length?JSON.parse(Buffer.concat(chunks).toString()):{};
        if(req.url==='/fixture/inspect'&&data.encoded!==undefined)fault.intakes.push(data);
        res.setHeader('Content-Type','application/json');
        if(req.url==='/fixture/inspect')return res.end(JSON.stringify({status:'inspected',jobId:data.id,name:'synthetic-source.png',reviewAvailable:!fault.large,sourcePreview:fault.large?null:fault.badImage?'data:image/png;base64,bm90LXB uZw==':pngSource(),previewLimitation:fault.large?'Crop review is not yet available; importing is disabled.':null,inspection:{schemaVersion:1,originalSha256:hash,image:{width:fault.large?14000:120,height:fault.large?14000:80},engine:{name:'RDKit',version:'fixture'},preservation:'Original bytes remain in local source storage.',warnings:['Disconnected components are not verified compounds.'],unsupportedSources:[{source:'metadata-2',features:['Unsupported annotation remains only in the original.']}],candidates:[{id:'component-1',label:'Component 1 (2 atoms)',source:'metadata-1',status:'supported',formalCharge:0,graph:component,warnings:['Review charges and stereochemistry.'],unsupportedFeatures:[]}]}}));
        if(req.url==='/fixture/cancel')return res.end(JSON.stringify({status:'cancelled'}));
        if(req.url==='/fixture/preview'||req.url==='/fixture/prepare'){
          if(fault.holdPreview&&req.url==='/fixture/preview'){fault.holdPreview=false;await new Promise(resolve=>{fault.release=resolve;fault.started();});}
          const result={...render(component,data.style),sourceSha256:hash,selectedComponents:data.ids,warnings:['Review charges and stereochemistry.']};
          if(req.url==='/fixture/prepare')result.provenance={kind:'png-metadata-component-reconstruction',sourceSha256:fault.badIdentity?'b'.repeat(64):hash,selectedComponents:data.ids,sourceStorage:'local-profile/import-sources',imageCorrespondence:'user-reviewed; not independently verified'};
          return res.end(JSON.stringify(result));
        }
        if(req.url==='/api/import'&&data.source==='invalid'){res.statusCode=400;return res.end(JSON.stringify({error:'Injected invalid source'}));}
        if(req.url==='/api/render'||req.url==='/api/import'){
          if(req.url==='/api/render')fault.renders.push(structuredClone(data.graph));
          if(fault.holdRender){fault.holdRender=false;await new Promise(resolve=>{fault.release=resolve;fault.started();});}
          return res.end(JSON.stringify(render(req.url==='/api/import'?graph('pasted'):data.graph,data.style)));
        }
        res.statusCode=503;return res.end(JSON.stringify({error:'Unavailable in this focused UI fixture.'}));
      }
      const relative=req.url==='/'?'index.html':req.url.slice(1),filename=path.resolve(publicPath,relative);
      if(!filename.startsWith(publicPath)){res.statusCode=404;return res.end();}
      res.setHeader('Content-Type',relative.endsWith('.mjs')?'text/javascript':relative.endsWith('.css')?'text/css':'text/html');res.end(await readFile(filename));
    }catch(error){res.statusCode=500;res.end(JSON.stringify({error:error.message}));}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{fault.release?.();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
  const browser=await testBrowser(t),page=await browser.newPage({viewport:{width:1366,height:768}}),url=`http://127.0.0.1:${server.address().port}/`,external=new Set();
  await page.route('**/*',route=>{const target=route.request().url();if(target.startsWith(url)||target.startsWith('blob:')||target.startsWith('data:'))return route.continue();external.add(new URL(target).origin);return route.abort();});
  t.after(()=>{if(external.size)t.diagnostic(`Focused fixture blocked external browser injection: ${[...external].join(', ')}. This is not privacy acceptance.`);});
  await page.addInitScript(()=>{
    const call=(name,payload)=>fetch('/fixture/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(r=>r.json());
    window.resetDestinationCount=0;window.nivramNative={resetDestination(){window.resetDestinationCount++;}};
    window.pywebview={api:{inspect_image:id=>call('inspect',{id}),inspect_image_bytes:(encoded,name,id,token)=>call('inspect',{encoded,name,id,token}).then(result=>({...result,name})),cancel_image_import:id=>call('cancel',{id}),preview_image_components:(id,ids,style)=>call('preview',{id,ids,style}),prepare_image_import:(id,ids,style)=>call('prepare',{id,ids,style})}};
  });
  page.on('dialog',dialog=>dialog.accept());await page.goto(url);await page.evaluate(()=>window.nivramDocument.ready);
  assert.equal(await page.evaluate(p=>window.nivramDocument.openProject(p),project),true);
  return {page,fault};
}
const ready=page=>page.waitForFunction(()=>document.querySelector('#request-status').textContent==='Ready');
const live=page=>page.evaluate(()=>({snapshot:window.nivramDocument.getSnapshot(),artwork:document.querySelector('#drawing').innerHTML,selection:document.querySelector('#selection').innerHTML,view:document.querySelector('#canvas').getAttribute('viewBox'),resets:window.resetDestinationCount}));
async function open(page){await page.click('#image-import-button');await page.waitForFunction(()=>document.querySelector('#image-import-components input')&&!document.querySelector('#image-import-components input').disabled);}
async function preview(page){await page.check('#image-import-components input');await page.click('#image-import-preview');await page.waitForFunction(()=>document.querySelector('#image-import-candidate path'));}

async function intakeEvent(page,kind,{count=1,selector='#canvas',mime='image/png',name='drop-source.png'}={}){
  return page.evaluate(({kind,count,selector,mime,name,encoded})=>{
    const data=new DataTransfer(),bytes=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));
    for(let i=0;i<count;i++)data.items.add(new File([bytes],name,{type:mime}));
    const event=kind==='paste'?new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}):new DragEvent('drop',{dataTransfer:data,bubbles:true,cancelable:true});
    document.querySelector(selector).dispatchEvent(event);return event.defaultPrevented;
  },{kind,count,selector,mime,name,encoded:pngSource().split(',')[1]});
}

test('PNG drop and clipboard paste use exact bytes and the ordinary explicit-review transaction',async t=>{
  const {page,fault}=await fixture(t),before=await live(page);
  assert.equal(await intakeEvent(page,'drop'),true);
  await page.waitForFunction(()=>document.querySelector('#image-import-components input'));
  assert.equal(fault.intakes.length,1);assert.equal(fault.intakes[0].encoded,pngSource().split(',')[1]);
  assert.equal(fault.intakes[0].name,'drop-source.png');assert.equal(fault.intakes[0].token,'__TOKEN__');
  assert.equal(await page.locator('#image-import-components input:checked').count(),0);
  assert.equal(await page.locator('#image-import-accept').isDisabled(),true);assert.deepEqual(await live(page),before);
  await page.click('#image-import-cancel');assert.deepEqual(await live(page),before);
  assert.equal(await intakeEvent(page,'paste',{name:'clipboard.png'}),true);
  await page.waitForFunction(()=>document.querySelector('#image-import-components input'));
  assert.equal(fault.intakes.length,2);assert.equal(fault.intakes[1].encoded,pngSource().split(',')[1]);
  await preview(page);assert.deepEqual(await live(page),before);
  await page.check('#image-import-acknowledge');await page.click('#image-import-accept');
  await page.waitForFunction(()=>!document.querySelector('#image-import-dialog').open);
  const accepted=await live(page);assert.equal(accepted.resets,1);assert.equal(accepted.snapshot.dirty,true);
  assert.equal(accepted.snapshot.document.analysis.imageImport.sourceSha256,hash);
  await page.click('#undo-button');await ready(page);assert.deepEqual((await live(page)).snapshot.document,before.snapshot.document);
});

test('PNG intake rejects multiple files and stays inactive in text fields, dialogs and close review',async t=>{
  const {page,fault}=await fixture(t),before=await live(page);
  assert.equal(await intakeEvent(page,'drop',{count:2}),true);
  await page.waitForFunction(()=>document.querySelector('#toast').textContent==='Import one PNG image at a time.');
  assert.equal(fault.intakes.length,0);assert.equal(await page.locator('#image-import-dialog').evaluate(e=>e.open),false);
  assert.equal(await intakeEvent(page,'paste',{selector:'#filename'}),false);assert.equal(fault.intakes.length,0);
  await page.evaluate(()=>window.nivramDocument.beginCloseReview());
  assert.equal(await intakeEvent(page,'drop'),true);assert.equal(fault.intakes.length,0);
  await page.evaluate(()=>window.nivramDocument.endCloseReview());
  await open(page);assert.equal(await intakeEvent(page,'paste'),true);assert.equal(fault.intakes.length,0);
  await page.click('#image-import-cancel');assert.deepEqual(await live(page),before);
});

test('PNG corrections remain private through draft history and cancel, then import with provenance and one main Undo',async t=>{
  const {page,fault}=await fixture(t),before=await live(page),baseline=graph('image');
  const control=key=>page.locator(`[data-correction="${key}"]`);
  const settled=()=>page.waitForFunction(()=>document.querySelector('#image-import-candidate').getAttribute('aria-busy')==='false'&&!document.querySelector('[data-correction="reset"]').disabled);
  async function atomFields(){
    const details=page.locator('#image-import-correction details');
    if(!await details.evaluate(node=>node.open))await details.locator('summary').click();
    await control('target').selectOption('atom:imageb');
  }
  async function correctAtom(){
    await atomFields();await control('element').fill('N');await control('charge').fill('1');
    await control('apply-atom').click();await settled();
  }
  await open(page);await preview(page);
  assert.equal(await control('undo').isDisabled(),true);assert.equal(await control('redo').isDisabled(),true);
  await page.check('#image-import-acknowledge');await correctAtom();
  const corrected=structuredClone(fault.renders.at(-1));
  assert.equal(corrected.atoms[1].symbol,'N');assert.equal(corrected.atoms[1].charge,1);
  assert.deepEqual(corrected.atoms[0],baseline.atoms[0]);assert.deepEqual(corrected.bonds,baseline.bonds);
  assert.equal(await page.locator('#image-import-acknowledge').isChecked(),false);
  assert.equal(await page.locator('#image-import-accept').isDisabled(),true);
  assert.deepEqual(await live(page),before);
  await control('undo').click();await settled();
  assert.deepEqual(fault.renders.at(-1),baseline);assert.equal(await control('element').inputValue(),'O');assert.equal(await control('charge').inputValue(),'0');
  assert.equal(await control('undo').isDisabled(),true);assert.equal(await control('redo').isDisabled(),false);
  await control('redo').click();await settled();assert.deepEqual(fault.renders.at(-1),corrected);
  assert.equal(await control('element').inputValue(),'N');assert.equal(await control('charge').inputValue(),'1');
  await control('reset').click();await settled();assert.deepEqual(fault.renders.at(-1),baseline);
  await control('undo').click();await settled();assert.deepEqual(fault.renders.at(-1),corrected);
  assert.deepEqual(await live(page),before);
  await page.click('#image-import-cancel');assert.deepEqual(await live(page),before);

  await open(page);await preview(page);await atomFields();
  assert.equal(await control('element').inputValue(),'O');assert.equal(await control('charge').inputValue(),'0');
  await correctAtom();await page.check('#image-import-acknowledge');await page.click('#image-import-accept');
  await page.waitForFunction(()=>!document.querySelector('#image-import-dialog').open);
  const accepted=await live(page),provenance=accepted.snapshot.document.analysis.imageImport;
  assert.equal(accepted.resets,before.resets+1);assert.equal(accepted.snapshot.dirty,true);
  assert.deepEqual(accepted.snapshot.document.graph,corrected);
  assert.deepEqual(accepted.snapshot.document.style,before.snapshot.document.style);
  assert.deepEqual(accepted.snapshot.document.export,before.snapshot.document.export);
  assert.equal(provenance.sourceSha256,hash);assert.deepEqual(provenance.selectedComponents,['component-1']);
  assert.equal(provenance.correction.kind,'user-edited-graph');
  assert.deepEqual(provenance.correction.baselineGraph,baseline);assert.deepEqual(provenance.correction.reviewedGraph,corrected);
  assert.equal(provenance.correction.imageCorrespondence,'user-reviewed; not independently verified');
  await page.click('#undo-button');await ready(page);
  assert.deepEqual((await live(page)).snapshot.document,before.snapshot.document);
});

test('PNG UI requires explicit reviewed components, preserves cancellation and guards destination and stale commits',async t=>{
  const {page,fault}=await fixture(t);let before=await live(page);
  await open(page);assert.equal(await page.locator('#image-import-components input:checked').count(),0);
  assert.equal(await page.locator('#image-import-accept').isDisabled(),true);assert.equal(await page.locator('#image-import-source').evaluate(img=>img.naturalWidth),120);
  await preview(page);assert.deepEqual(await live(page),before);assert.equal(await page.locator('#image-import-accept').isDisabled(),true);
  await page.check('#image-import-acknowledge');await page.click('#image-import-cancel');assert.deepEqual(await live(page),before);
  await open(page);await preview(page);await page.check('#image-import-acknowledge');await page.click('#image-import-accept');
  await page.waitForFunction(()=>!document.querySelector('#image-import-dialog').open);const accepted=await live(page);
  assert.equal(accepted.resets,1);assert.equal(accepted.snapshot.dirty,true);assert.equal(accepted.snapshot.document.analysis.imageImport.sourceSha256,hash);
  assert.deepEqual(accepted.snapshot.document.graph.atoms.map(a=>a.id),['imagea','imageb']);
  await page.click('#undo-button');await ready(page);assert.deepEqual((await live(page)).snapshot.document,before.snapshot.document);
  for(const type of ['large','badImage']){
    fault[type]=true;before=await live(page);await open(page);await preview(page);
    assert.equal(await page.locator('#image-import-accept').isDisabled(),true);assert.equal(await page.locator('#image-import-acknowledge').isDisabled(),true);
    assert.equal(await page.locator('#image-import-source').getAttribute('src'),null);await page.keyboard.press('Escape');assert.deepEqual(await live(page),before);fault[type]=false;
  }
  fault.badIdentity=true;before=await live(page);await open(page);await preview(page);await page.check('#image-import-acknowledge');await page.click('#image-import-accept');
  await page.waitForFunction(()=>document.querySelector('#image-import-status').classList.contains('image-import-error'));
  assert.deepEqual(await live(page),before);await page.click('#image-import-cancel');fault.badIdentity=false;
  fault.holdPreview=true;let started=new Promise(resolve=>{fault.started=resolve;});before=await live(page);
  await open(page);await page.check('#image-import-components input');await page.click('#image-import-preview');await started;
  await page.keyboard.press('Escape');fault.release();assert.deepEqual(await live(page),before);
  await open(page);await preview(page);await page.check('#image-import-acknowledge');fault.holdRender=true;started=new Promise(resolve=>{fault.started=resolve;});
  await page.click('#image-import-accept');await started;await page.keyboard.press('Escape');await ready(page);fault.release();assert.deepEqual(await live(page),before);
  const locked=await page.evaluate(()=>window.nivramDocument.beginCloseReview());
  const {appearance,...lockedDocument}=locked;
  assert.deepEqual(appearance,{theme:'light',revision:0,generation:null});
  assert.equal(await page.evaluate(p=>window.nivramDocument.openProject(p),project),false);assert.deepEqual((await live(page)).snapshot,lockedDocument);
  await page.evaluate(()=>window.nivramDocument.endCloseReview());
  const resets=(await live(page)).resets;await page.click('#new-button');await ready(page);assert.equal((await live(page)).resets,resets+1);
  await page.click('#import-button');await page.fill('#structure-input','CO');await page.click('#load-structure');await ready(page);
  await page.waitForFunction(()=>!document.querySelector('#import-dialog').open);assert.equal((await live(page)).resets,resets+2);
  before=await live(page);await page.click('#import-button');await page.fill('#structure-input','invalid');await page.click('#load-structure');await ready(page);
  await page.waitForFunction(()=>document.querySelector('#toast').textContent==='Injected invalid source');await page.keyboard.press('Escape');assert.deepEqual(await live(page),before);
  await page.setInputFiles('#file-input',{name:'copied.nivram',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(project))});await ready(page);
  await page.waitForFunction(()=>window.nivramDocument.getSnapshot().document.graph.atoms[0]?.id==='olda');assert.equal((await live(page)).resets,resets+3);
  assert.equal(await page.locator('.wordmark').getAttribute('aria-label'),'nivraM');assert.equal(await page.locator('.wordmark').innerText(),'Marvin');
  assert.equal(await page.locator('.wordmark').evaluate(node=>getComputedStyle(node).transform),'matrix(-1, 0, 0, 1, 0, 0)');
  t.diagnostic('PNG source decoding, explicit selection/acceptance, provenance, crop/decode rejection, cancellation, stale render and destination guards completed.');
});
