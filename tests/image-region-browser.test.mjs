import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {deflateSync} from 'node:zlib';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {testBrowser} from './browser-session.mjs';

// Real UI and browser decoding, synthetic native bridge. This deliberately does
// not establish native decoder correctness or actual large-source performance.
const publicPath=fileURLToPath(new URL('../public/',import.meta.url)),hash='a'.repeat(64);
const graph={atoms:[{id:'ca',symbol:'C',x:0,y:0},{id:'oa',symbol:'O',x:1.5,y:0}],bonds:[{id:'b',begin:'ca',end:'oa',order:1}]};
const style={bondWidth:4.2,labelSize:36,bondSpacing:.14,color:true};
function render(value,appearance=style){return {graph:value,scene:{svg:'<svg xmlns="http://www.w3.org/2000/svg"><path d="M80 110L155 110" fill="none" stroke="#000" stroke-width="3"/></svg>',positions:value.atoms.map(a=>({id:a.id,x:80+a.x*50,y:110-a.y*50})),transform:{x0:80,y0:110,xScale:50,yScale:-50},width:300,height:220,formula:'fixture',smiles:'CO',mass:32,style:appearance}};}
function png(width,height){
  const raw=Buffer.alloc((width*4+1)*height,255);for(let y=0;y<height;y++){raw[y*(width*4+1)]=0;for(let x=0;x<width;x++)if(x===y||x===width-1-y){const p=y*(width*4+1)+1+x*4;raw[p]=10;raw[p+1]=80;raw[p+2]=190;}}
  const crc=data=>{let v=0xffffffff;for(const b of data){v^=b;for(let i=0;i<8;i++)v=(v>>>1)^((v&1)?0xedb88320:0);}return (v^0xffffffff)>>>0;};
  const chunk=(kind,body)=>{const data=Buffer.concat([Buffer.from(kind),body]),size=Buffer.alloc(4),check=Buffer.alloc(4);size.writeUInt32BE(body.length);check.writeUInt32BE(crc(data));return Buffer.concat([size,data,check]);};
  const head=Buffer.alloc(13);head.writeUInt32BE(width);head.writeUInt32BE(height,4);head[8]=8;head[9]=6;
  return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',head),chunk('IDAT',deflateSync(raw)),chunk('IEND',Buffer.alloc(0))]);
}
async function fixture(t){
  const fault={small:false,badHash:false,hold:false,started:null,release:null},calls={regions:[],prepared:[]};let detail=null;
  const server=createServer(async(req,res)=>{
    try{
      if(req.url==='/api/status'){res.setHeader('Content-Type','application/json');return res.end(JSON.stringify({version:'fixture',rdkit:'test'}));}
      if(req.url.startsWith('/api/')||req.url.startsWith('/fixture/')){
        const chunks=[];for await(const value of req)chunks.push(value);const data=chunks.length?JSON.parse(Buffer.concat(chunks)):{};res.setHeader('Content-Type','application/json');
        if(req.url==='/api/render')return res.end(JSON.stringify(render(data.graph,data.style)));
        if(req.url==='/fixture/inspect')return res.end(JSON.stringify({status:'inspected',jobId:data.id,name:'synthetic-source.png',reviewAvailable:fault.small,regionPreviewAvailable:true,sourcePreview:fault.small?'data:image/png;base64,'+png(120,80).toString('base64'):null,inspection:{schemaVersion:1,originalSha256:hash,image:{width:fault.small?120:11559,height:fault.small?80:9999},engine:{name:'fixture',version:'1'},warnings:['Metadata coordinates are not image pixels.'],unsupportedSources:[{source:'metadata',features:['Annotations remain in the original PNG.']}],candidates:[{id:'c1',label:'Complete component',status:'supported',source:'metadata',formalCharge:0,graph,warnings:['Review every selected atom and bond.']}]}}));
        if(req.url==='/fixture/cancel'){detail=null;return res.end('{}');}
        if(req.url==='/fixture/cancel-region'){if(detail?.requestId===data.requestId)detail=null;return res.end('{}');}
        if(req.url==='/fixture/region'){
          calls.regions.push(data);detail=null;const sw=fault.small?120:11559,sh=fault.small?80:9999,rect=data.mode==='overview'?{x:0,y:0,width:sw,height:sh}:data.rect;
          const width=data.mode==='overview'?Math.max(1,Math.floor(sw*Math.min(960,Math.max(sw,sh))/Math.max(sw,sh))):rect.width,height=data.mode==='overview'?Math.max(1,Math.floor(sh*Math.min(960,Math.max(sw,sh))/Math.max(sw,sh))):rect.height,bytes=png(width,height);
          const receipt={schemaVersion:1,jobId:data.id,requestId:data.requestId,sourceSha256:hash,sourceWidth:sw,sourceHeight:sh,mode:data.mode,rect,width,height,resampling:data.mode==='overview'?'nearest-center':'none',navigationOnly:data.mode==='overview',pngBase64:bytes.toString('base64'),pngSha256:fault.badHash?'b'.repeat(64):createHash('sha256').update(bytes).digest('hex'),...(data.mode==='crop'&&data.ids.length?{reviewToken:'synthetic_review_token'}:{})};
          if(data.mode==='crop')detail=receipt;
          if(fault.hold){fault.hold=false;await new Promise(resolve=>{fault.release=resolve;fault.started();});}
          return res.end(JSON.stringify(receipt));
        }
        if(req.url==='/fixture/preview'||req.url==='/fixture/prepare'){
          const value={...render(graph,data.style),sourceSha256:hash,selectedComponents:data.ids,warnings:['Review every selected atom and bond.']};
          if(req.url==='/fixture/prepare'){
            calls.prepared.push(data);value.provenance={kind:'png-metadata-component-reconstruction',sourceSha256:hash,selectedComponents:data.ids,sourceStorage:'local-profile/import-sources'};
            if(data.token){assert.equal(data.token,detail?.reviewToken);const {requestId,sourceSha256,sourceWidth,sourceHeight,rect,width,height,pngSha256}=detail;value.provenance.reviewedRegion={kind:'crop',requestId,sourceSha256,sourceWidth,sourceHeight,rect,width,height,pngSha256,scale:'one-source-pixel-per-image-pixel',metadataCorrespondence:'not established; whole components retained'};}
          }
          return res.end(JSON.stringify(value));
        }
        res.statusCode=503;return res.end(JSON.stringify({error:'Unavailable in this focused UI fixture.'}));
      }
      const filename=path.resolve(publicPath,req.url==='/'?'index.html':req.url.slice(1));if(!filename.startsWith(publicPath)){res.statusCode=404;return res.end();}
      res.setHeader('Content-Type',filename.endsWith('.mjs')?'text/javascript':filename.endsWith('.css')?'text/css':'text/html');res.end(await readFile(filename));
    }catch(error){res.statusCode=500;res.end(JSON.stringify({error:error.message}));}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(async()=>{fault.release?.();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
  const browser=await testBrowser(t),page=await browser.newPage({viewport:{width:1366,height:900}}),url=`http://127.0.0.1:${server.address().port}/`,errors=[];
  await page.route('**/*',route=>route.request().url().startsWith(url)||route.request().url().startsWith('data:')||route.request().url().startsWith('blob:')?route.continue():route.abort());
  page.on('pageerror',error=>errors.push(error.message));await page.addInitScript(()=>{
    const call=(name,data)=>fetch('/fixture/'+name,{method:'POST',body:JSON.stringify(data)}).then(r=>r.json());window.nivramNative={resetDestination(){}};
    window.pywebview={api:{inspect_image:id=>call('inspect',{id}),cancel_image_import:id=>call('cancel',{id}),preview_image_components:(id,ids,style)=>call('preview',{id,ids,style}),prepare_image_import:(id,ids,style,token)=>call('prepare',{id,ids,style,token}),preview_image_region:(id,requestId,rect,mode,ids)=>call('region',{id,requestId,rect,mode,ids}),cancel_image_region:(id,requestId)=>call('cancel-region',{id,requestId})}};
  });
  await page.goto(url);await page.evaluate(()=>window.nivramDocument.ready);return {page,fault,calls,errors};
}
async function open(page){await page.click('#image-import-button');await page.waitForFunction(()=>document.querySelector('#image-import-components input')&&!document.querySelector('#image-import-components input').disabled);await page.check('#image-import-components input');await page.click('#image-import-preview');await page.waitForFunction(()=>document.querySelector('#image-import-candidate path'));}
async function detail(page){await page.click('#image-import-crop-button');await page.waitForFunction(()=>!document.querySelector('#image-import-acknowledge').disabled);}

test('large-source overview, 100% detail, crop binding, invalid hash, cancel and one undo',async t=>{
  const {page,fault,calls,errors}=await fixture(t),before=await page.evaluate(()=>window.nivramDocument.getSnapshot());await open(page);
  assert.equal(await page.locator('#image-import-source').getAttribute('src'),null);await page.click('#image-import-overview-button');await page.waitForFunction(()=>document.querySelector('#image-import-overview').naturalWidth===960);
  assert.equal(await page.locator('#image-import-acknowledge').isDisabled(),true);assert.equal(calls.prepared.length,0);
  await detail(page);const bounds=await page.locator('#image-import-source').evaluate(img=>({natural:[img.naturalWidth,img.naturalHeight],shown:[img.getBoundingClientRect().width,img.getBoundingClientRect().height],scroll:img.parentElement.scrollWidth,viewport:img.parentElement.clientWidth,background:getComputedStyle(img.parentElement).backgroundColor}));
  assert.deepEqual(bounds.natural,[1000,1000]);assert.deepEqual(bounds.shown,[1000,1000]);assert.ok(bounds.scroll>bounds.viewport);assert.equal(bounds.background,'rgb(255, 255, 255)');
  await page.check('#image-import-acknowledge');await page.fill('#image-import-crop-x','5');assert.equal(await page.locator('#image-import-acknowledge').isChecked(),false);assert.equal(await page.locator('#image-import-accept').isDisabled(),true);
  fault.badHash=true;await page.click('#image-import-crop-button');await page.waitForFunction(()=>document.querySelector('#image-import-status').textContent.includes('hash'));assert.equal(await page.locator('#image-import-acknowledge').isDisabled(),true);fault.badHash=false;
  await detail(page);await page.check('#image-import-acknowledge');await page.click('#image-import-accept');await page.waitForFunction(()=>!document.querySelector('#image-import-dialog').open);
  const imported=await page.evaluate(()=>window.nivramDocument.getSnapshot());assert.deepEqual(imported.document.graph,graph);assert.equal(imported.document.analysis.imageImport.reviewedRegion.rect.x,5);assert.equal(calls.prepared.at(-1).token,'synthetic_review_token');
  await page.click('#undo-button');await page.waitForFunction(()=>document.querySelector('#request-status').textContent==='Ready');assert.deepEqual((await page.evaluate(()=>window.nivramDocument.getSnapshot())).document,before.document);
  await open(page);fault.hold=true;const started=new Promise(resolve=>{fault.started=resolve;});await page.click('#image-import-crop-button');await started;await page.keyboard.press('Escape');fault.release();await page.waitForTimeout(50);assert.deepEqual((await page.evaluate(()=>window.nivramDocument.getSnapshot())).document,before.document);assert.deepEqual(errors,[]);
  t.diagnostic('Synthetic native bridge only; browser image hashes, exact intrinsic crop pixels, authority revocation, source provenance and one undo verified.');
});

test('small-original route remains available until optional crop controls are used',async t=>{
  const {page,fault,calls,errors}=await fixture(t);fault.small=true;await open(page);assert.equal(await page.locator('#image-import-acknowledge').isDisabled(),false);
  await page.fill('#image-import-crop-width','60');await page.fill('#image-import-crop-height','40');assert.equal(await page.locator('#image-import-acknowledge').isDisabled(),true);
  await page.click('#image-import-overview-button');await page.waitForFunction(()=>document.querySelector('#image-import-overview').naturalWidth===120);assert.equal(await page.locator('#image-import-acknowledge').isDisabled(),true);
  await detail(page);assert.deepEqual(await page.locator('#image-import-source').evaluate(img=>[img.naturalWidth,img.naturalHeight]),[60,40]);await page.check('#image-import-acknowledge');await page.click('#image-import-accept');await page.waitForFunction(()=>!document.querySelector('#image-import-dialog').open);assert.equal(calls.prepared.at(-1).token,'synthetic_review_token');assert.deepEqual(errors,[]);
});
