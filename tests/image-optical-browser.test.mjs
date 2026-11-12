import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {deflateSync} from 'node:zlib';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {testBrowser} from './browser-session.mjs';

// Real browser UI, correction controller and PNG decoding. The native/model
// bridge and chemistry renderer are synthetic; this is not inference evidence.
const publicPath=fileURLToPath(new URL('../public/',import.meta.url)),sourceHash='a'.repeat(64),modelHash='32821fa4ba8d17f9a92e374601438c5e757947dbf1141e9ad0ef51cccfa1c501';
const graph={atoms:[{id:'c1',symbol:'C',x:0,y:0},{id:'o1',symbol:'O',x:1.5,y:0}],bonds:[{id:'b1',begin:'c1',end:'o1',order:1}]};
const style={bondWidth:4.2,labelSize:36,bondSpacing:.14,color:true};
const render=(value,appearance=style)=>({graph:value,scene:{svg:'<svg xmlns="http://www.w3.org/2000/svg"><path d="M80 110L155 110" fill="none" stroke="#000" stroke-width="3"/></svg>',positions:value.atoms.map(a=>({id:a.id,x:80+a.x*50,y:110-a.y*50})),transform:{x0:80,y0:110,xScale:50,yScale:-50},width:300,height:220,formula:'fixture',smiles:'CO',mass:32,style:appearance}});
function png(width,height){
  const pixels=Buffer.alloc((width*4+1)*height,255);for(let y=0;y<height;y++)pixels[y*(width*4+1)]=0;
  const crc=data=>{let value=0xffffffff;for(const byte of data){value^=byte;for(let bit=0;bit<8;bit++)value=(value>>>1)^((value&1)?0xedb88320:0);}return(value^0xffffffff)>>>0;};
  const chunk=(kind,body)=>{const value=Buffer.concat([Buffer.from(kind),body]),length=Buffer.alloc(4),check=Buffer.alloc(4);length.writeUInt32BE(body.length);check.writeUInt32BE(crc(value));return Buffer.concat([length,value,check]);};
  const header=Buffer.alloc(13);header.writeUInt32BE(width);header.writeUInt32BE(height,4);header[8]=8;header[9]=6;
  return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',header),chunk('IDAT',deflateSync(pixels)),chunk('IEND',Buffer.alloc(0))]);
}

test('optical UI requires a configured model and explicit crop action, then displays a correctable proposal with one undo',async t=>{
  const state={configured:false,recognitions:[],prepared:[],errors:[]};let receipt,recognition;
  const inspection=()=>({schemaVersion:1,originalSha256:sourceHash,image:{width:120,height:80},engine:{name:'fixture',version:'1'},candidates:[],warnings:[]});
  const server=createServer(async(req,res)=>{
    try{
      if(req.url.startsWith('/api/')||req.url.startsWith('/fixture/')){
        const chunks=[];for await(const chunk of req)chunks.push(chunk);const data=chunks.length?JSON.parse(Buffer.concat(chunks)):{};res.setHeader('Content-Type','application/json');
        let result;
        if(req.url==='/api/status')result={version:'fixture',rdkit:'fixture'};
        else if(req.url==='/api/render')result=render(data.graph,data.style);
        else if(req.url==='/fixture/inspect')result={status:'inspected',jobId:data.id,name:'ordinary.png',reviewAvailable:false,regionPreviewAvailable:true,inspection:inspection(),opticalRecognition:{status:state.configured?'configured':'missing-model',message:'Local recognition model is not installed.',modelSha256:modelHash}};
        else if(req.url==='/fixture/recognize'){
          state.recognitions.push(data);const bytes=png(data.rect.width,data.rect.height),pngSha256=createHash('sha256').update(bytes).digest('hex');
          recognition={method:'MolScribe',modelSha256:modelHash,inputSha256:pngSha256,confidence:0.91,confidenceMeaning:'uncalibrated model score',coordinates:'preserved-from-recognized-molfile'};
          receipt={schemaVersion:1,jobId:data.id,requestId:data.requestId,sourceSha256:sourceHash,sourceWidth:120,sourceHeight:80,mode:'crop',rect:data.rect,width:data.rect.width,height:data.rect.height,resampling:'none',navigationOnly:false,pngBase64:bytes.toString('base64'),pngSha256,reviewToken:'optical_fixture_token'};
          result={status:'recognized',jobId:data.id,requestId:data.requestId,inspection:{...inspection(),candidates:[{id:'optical-1',source:'optical-recognition',status:'supported',label:'Recognized proposal',graph,recognition}]},region:receipt,selectedComponents:['optical-1']};
        }else if(req.url==='/fixture/preview'||req.url==='/fixture/prepare'){
          result={...render(graph,data.style),sourceSha256:sourceHash,selectedComponents:data.ids,warnings:['Optical proposal is unverified.']};
          if(req.url==='/fixture/prepare'){
            state.prepared.push(data);assert.equal(data.token,receipt.reviewToken);
            const {requestId,sourceSha256,sourceWidth,sourceHeight,rect,width,height,pngSha256}=receipt;
            result.provenance={kind:'png-optical-reconstruction',sourceSha256,selectedComponents:data.ids,recognition,reviewedRegion:{kind:'crop',requestId,sourceSha256,sourceWidth,sourceHeight,rect,width,height,pngSha256,scale:'one-source-pixel-per-image-pixel',metadataCorrespondence:'unverified optical recognition of this crop'}};
          }
        }else if(req.url.startsWith('/fixture/cancel'))result={status:'cancelled'};
        else{res.statusCode=503;result={error:'Unavailable in focused fixture.'};}
        return res.end(JSON.stringify(result));
      }
      const filename=path.resolve(publicPath,req.url==='/'?'index.html':req.url.slice(1));if(!filename.startsWith(publicPath)){res.statusCode=404;return res.end();}
      res.setHeader('Content-Type',filename.endsWith('.mjs')?'text/javascript':filename.endsWith('.css')?'text/css':'text/html');res.end(await readFile(filename));
    }catch(error){res.statusCode=500;res.end(JSON.stringify({error:error.message}));}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(async()=>{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
  const browser=await testBrowser(t),page=await browser.newPage({viewport:{width:1366,height:950}}),url=`http://127.0.0.1:${server.address().port}/`;
  await page.route('**/*',route=>[url,'data:','blob:'].some(prefix=>route.request().url().startsWith(prefix))?route.continue():route.abort());
  page.on('pageerror',error=>state.errors.push(error.message));await page.addInitScript(()=>{
    const call=(name,data)=>fetch('/fixture/'+name,{method:'POST',body:JSON.stringify(data)}).then(r=>r.json());window.nivramNative={resetDestination(){}};
    window.pywebview={api:{inspect_image:id=>call('inspect',{id}),cancel_image_import:id=>call('cancel',{id}),cancel_image_region:(id,requestId)=>call('cancel-region',{id,requestId}),recognize_image:(id,requestId,rect,token)=>call('recognize',{id,requestId,rect,token}),preview_image_components:(id,ids,style)=>call('preview',{id,ids,style}),prepare_image_import:(id,ids,style,token)=>call('prepare',{id,ids,style,token})}};
  });
  await page.goto(url);await page.evaluate(()=>window.nivramDocument.ready);const before=await page.evaluate(()=>window.nivramDocument.getSnapshot());
  await page.click('#image-import-button');await page.waitForFunction(()=>document.querySelector('#image-import-optical-status').textContent.includes('not installed'));
  assert.equal(await page.locator('#image-import-recognize').isDisabled(),true);assert.equal(state.recognitions.length,0);await page.click('#image-import-cancel');
  state.configured=true;await page.click('#image-import-button');await page.waitForFunction(()=>!document.querySelector('#image-import-recognize').disabled);
  assert.equal(state.recognitions.length,0);await page.fill('#image-import-crop-x','10');await page.fill('#image-import-crop-width','100');
  await page.click('#image-import-recognize');await page.waitForFunction(()=>!document.querySelector('#image-import-acknowledge').disabled);
  assert.equal(state.recognitions.length,1);assert.equal(state.recognitions[0].token,'__TOKEN__');assert.deepEqual(state.recognitions[0].rect,{x:10,y:0,width:100,height:80});
  assert.deepEqual(await page.locator('#image-import-source').evaluate(img=>[img.naturalWidth,img.naturalHeight]),[100,80]);
  assert.equal(await page.locator('#image-import-crop-x').inputValue(),'10');assert.equal(await page.locator('#image-import-components input:checked').inputValue(),'optical-1');
  assert.match(await page.locator('#image-import-warnings').textContent(),/uncalibrated/);assert.equal(await page.locator('#image-import-accept').isDisabled(),true);
  assert.deepEqual(await page.evaluate(()=>window.nivramDocument.getSnapshot()),before);
  await page.check('#image-import-acknowledge');await page.locator('#image-import-correction details summary').click();
  await page.selectOption('[data-correction="target"]','atom:o1');await page.fill('[data-correction="element"]','N');await page.fill('[data-correction="charge"]','1');await page.click('[data-correction="apply-atom"]');
  await page.waitForFunction(()=>!document.querySelector('[data-correction="reset"]').disabled);assert.equal(await page.locator('#image-import-acknowledge').isChecked(),false);
  await page.check('#image-import-acknowledge');await page.fill('#image-import-crop-x','11');assert.equal(await page.locator('#image-import-accept').isDisabled(),true);assert.equal(await page.locator('#image-import-acknowledge').isChecked(),false);
  await page.click('#image-import-recognize');await page.waitForFunction(()=>!document.querySelector('#image-import-acknowledge').disabled);await page.check('#image-import-acknowledge');await page.click('#image-import-accept');
  await page.waitForFunction(()=>!document.querySelector('#image-import-dialog').open);const imported=await page.evaluate(()=>window.nivramDocument.getSnapshot());
  assert.deepEqual(imported.document.graph,graph);assert.equal(imported.document.analysis.imageImport.kind,'png-optical-reconstruction');assert.equal(imported.document.analysis.imageImport.reviewedRegion.rect.x,11);assert.equal(state.prepared.length,1);
  await page.click('#undo-button');await page.waitForFunction(()=>document.querySelector('#request-status').textContent==='Ready');assert.deepEqual((await page.evaluate(()=>window.nivramDocument.getSnapshot())).document,before.document);assert.deepEqual(state.errors,[]);
});
