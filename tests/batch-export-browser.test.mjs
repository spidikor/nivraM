import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import path from 'node:path';

const {chromium}=createRequire(import.meta.url)('playwright');
const repository=fileURLToPath(new URL('../',import.meta.url));
const publicPath=fileURLToPath(new URL('../public/',import.meta.url));
const python=process.env.NIVRAM_PYTHON??path.resolve(repository,'../venv/Scripts/python.exe');
function backend(code,input={}){
  const child=spawnSync(python,['-c',code],{cwd:repository,input:JSON.stringify(input),encoding:'utf-8',windowsHide:true,timeout:20000,maxBuffer:4*1024*1024});
  if(child.status!==0)throw new Error(child.error?.message??child.stderr);
  return JSON.parse(child.stdout);
}
function prepared(){
  return backend("import json; from nivram.chemistry import import_structure; from nivram.scene import render_scene,DEFAULT_STYLE; g=import_structure('CCO'); p={'schema':'nivram/1','graph':g,'style':DEFAULT_STYLE,'export':{'width':640,'marginMode':'pixels','margin':8}}; print(json.dumps({'status':'ready','project':p,'graph':g,'scene':render_scene(g,p['style']),'item':{'id':'one','name':'synthetic.mol','status':'rendering'}}))");
}
async function fixture(t){
  const server=createServer(async(req,res)=>{
    try{
      if(req.url==='/'){
        res.setHeader('Content-Type','text/html');
        return res.end('<!doctype html><div class="appearance-panel"></div><svg xmlns="http://www.w3.org/2000/svg"><g id="active-drawing"><path d="M 0 0 L 50 10"/></g></svg>');
      }
      const filename=path.resolve(publicPath,req.url.slice(1));
      if(!filename.startsWith(path.resolve(publicPath)+path.sep)){res.statusCode=404;return res.end();}
      res.setHeader('Content-Type','text/javascript');res.end(await readFile(filename));
    }catch(error){res.statusCode=500;res.end(error.message);}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  t.after(async()=>{await browser?.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
  browser=await chromium.launch({headless:true,...(process.env.NIVRAM_BROWSER_EXECUTABLE?{executablePath:process.env.NIVRAM_BROWSER_EXECUTABLE}:{})});
  const page=await browser.newPage();await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.evaluate(async()=>{window.batchModule=await import('/modules/batch-export.mjs');});
  return page;
}

test('actual RDKit scene uses production exportPng and the bounded native validator without changing the active drawing',async t=>{
  const page=await fixture(t),source=prepared();
  const result=await page.evaluate(async prepared=>{
    const active=document.querySelector('#active-drawing').outerHTML,before=JSON.stringify(prepared);
    const output=await window.batchModule.renderBatchPng(prepared);
    const encoded=await window.batchModule.pngBase64(output.blob);
    const decoded=await createImageBitmap(output.blob),canvas=document.createElement('canvas');canvas.width=decoded.width;canvas.height=decoded.height;
    const context=canvas.getContext('2d');context.drawImage(decoded,0,0);decoded.close();
    const pixels=context.getImageData(0,0,canvas.width,canvas.height).data;
    let opaque=true,whiteMargins=true,ink=0;
    for(let y=0;y<canvas.height;y++)for(let x=0;x<canvas.width;x++){
      const i=(y*canvas.width+x)*4,white=pixels[i]===255&&pixels[i+1]===255&&pixels[i+2]===255&&pixels[i+3]===255;
      if(pixels[i+3]!==255)opaque=false;
      if(x<8||x>=canvas.width-8||y<8||y>=canvas.height-8){if(!white)whiteMargins=false;}
      if(!white)ink++;
    }
    return {encoded,width:output.width,height:output.height,opaque,whiteMargins,ink,
      same:before===JSON.stringify(prepared)&&active===document.querySelector('#active-drawing').outerHTML,
      stages:document.querySelectorAll('svg[aria-hidden="true"]').length};
  },source);
  assert.equal(result.width,640);assert.ok(result.height>16);assert.ok(result.opaque);assert.ok(result.whiteMargins);assert.ok(result.ink>0);assert.ok(result.same);assert.equal(result.stages,0);
  const verified=backend("import json,sys,threading; from nivram.batch_export import _decode_png; p=json.load(sys.stdin); data,dims=_decode_png(p['png'],p['options'],threading.Event()); print(json.dumps({'bytes':len(data),'dimensions':dims}))",{png:result.encoded,options:source.project.export});
  assert.equal(verified.dimensions.width,640);assert.equal(verified.dimensions.height,result.height);assert.ok(verified.bytes>100);
});

test('cancelling the real renderer during SVG decoding removes its stage and never calls native write',async t=>{
  const page=await fixture(t),source=prepared();
  const result=await page.evaluate(async prepared=>{
    const decode=Image.prototype.decode;let started,release,writeCount=0;
    const startedPromise=new Promise(resolve=>{started=resolve;}),gate=new Promise(resolve=>{release=resolve;});
    Image.prototype.decode=function(){return decode.call(this).then(()=>{started();return gate;});};
    const items=[{id:'one',name:'synthetic.mol',status:'pending'}];
    const native={begin_batch_export:async()=>({status:'running',sessionId:'batch',items}),prepare_batch_item:async()=>prepared,
      write_batch_png:async()=>{writeCount++;return {status:'exported',item:{...items[0],status:'exported'}};},
      fail_batch_item:async()=>{throw new Error('Cancellation should not be a failure.');},
      cancel_batch_export:async()=>{items[0].status='cancelled';return {status:'cancelled',items};},
      finish_batch_export:async()=>({status:'cancelled',items,manifestName:'synthetic.json'})};
    const runner=window.batchModule.createBatchExport({native});
    const task=runner.run({style:prepared.project.style,export:prepared.project.export});
    await startedPromise;runner.cancel();
    const receipt=await task;Image.prototype.decode=decode;release();
    return {writeCount,status:receipt.status,stages:document.querySelectorAll('svg[aria-hidden="true"]').length};
  },source);
  assert.deepEqual(result,{writeCount:0,status:'cancelled',stages:0});
});

test('close-review events gate batch UI and cancel a session returned after native file selection',async t=>{
  const page=await fixture(t),source=prepared();
  const result=await page.evaluate(async prepared=>{
    let choose,prepareCalls=0,cancelCalls=0;
    const choice=new Promise(resolve=>{choose=resolve;});
    window.nivramDocument={ready:Promise.resolve(),getSnapshot:()=>({document:prepared.project,revision:0,dirty:false}),subscribe:()=>{}};
    window.pywebview={api:{begin_batch_export:()=>choice,list_profiles:async()=>({profiles:[]}),
      prepare_batch_item:async()=>{prepareCalls++;return prepared;},
      cancel_batch_export:async()=>{cancelCalls++;return {status:'cancelled'};},
      finish_batch_export:async()=>({status:'cancelled',items:[{id:'one',name:'test.mol',status:'cancelled'}],manifestName:'test.json'})}};
    await window.batchModule.initializeBatchExport();
    document.querySelector('#batch-export-button').click();window.dispatchEvent(new Event('nivram-close-review'));
    choose({status:'running',sessionId:'late',items:[{id:'one',name:'test.mol',status:'pending'}]});
    for(let i=0;i<30&&!document.querySelector('#batch-export-status').textContent.includes('Batch cancelled');i++)await new Promise(resolve=>setTimeout(resolve,10));
    const during=document.querySelector('#batch-export-button').disabled;
    window.dispatchEvent(new Event('nivram-close-review-ended'));
    return {during,after:document.querySelector('#batch-export-button').disabled,prepareCalls,cancelCalls};
  },source);
  assert.deepEqual(result,{during:true,after:false,prepareCalls:0,cancelCalls:1});
});

for(const failure of ['native-unverified','lost-acknowledgement','manifest-uncertain']){
  test(`batch UI shows terminal native item states after ${failure}`,async t=>{
    const page=await fixture(t),source=prepared();
    await page.evaluate(async({prepared,failure})=>{
      const snapshot={document:prepared.project,revision:7,dirty:true};
      const baseline=JSON.stringify(snapshot),drawing=document.querySelector('#active-drawing').outerHTML;
      const items=[{id:'one',name:'first.nivram',status:'pending'},{id:'two',name:'second.nivram',status:'pending'}];
      const facts={prepare:0,write:0,cancel:0,pngBytes:0};
      window.nivramDocument={ready:Promise.resolve(),getSnapshot:()=>snapshot,subscribe:()=>{}};
      window.pywebview={api:{
        list_profiles:async()=>({profiles:[]}),
        begin_batch_export:async()=>({status:'running',sessionId:'synthetic',items:structuredClone(items)}),
        prepare_batch_item:async()=>{
          facts.prepare++;items[0].status='rendering';
          return {...prepared,item:structuredClone(items[0])};
        },
        write_batch_png:async(session,id,encoded)=>{
          facts.write++;facts.pngBytes=atob(encoded).length;
          items[0].status=failure==='native-unverified'?'failed':'exported';
          items[0].outputName=failure==='native-unverified'?'unverified.png':'retained.png';
          if(failure==='native-unverified')items[0].error={code:'write_unverified',message:'PNG publication could not be verified.'};
          if(failure==='lost-acknowledgement')throw new Error('Synthetic acknowledgement lost after publication');
          return {status:failure==='native-unverified'?'stopped':'exported',item:structuredClone(items[0]),
            manifestError:failure==='manifest-uncertain'?'The latest manifest could not be verified.':null};
        },
        cancel_batch_export:async()=>{
          facts.cancel++;items[1].status='cancelled';
          return {status:failure==='lost-acknowledgement'?'cancelled':'stopped',items:structuredClone(items)};
        },
        fail_batch_item:async()=>{throw new Error('Publication uncertainty must not be reclassified as rendering failure');},
        finish_batch_export:async()=>{throw new Error('Uncertain publication must stop before finish');}
      }};
      window.publicationFixture={facts,same:()=>JSON.stringify(snapshot)===baseline&&document.querySelector('#active-drawing').outerHTML===drawing};
      await window.batchModule.initializeBatchExport();
      document.querySelector('#batch-export-button').click();
    },{prepared:source,failure});
    await page.waitForFunction(()=>document.querySelector('#batch-export-status').textContent.includes('The batch stopped before completion.'),{},{timeout:10000});
    const result=await page.evaluate(()=>({
      ...window.publicationFixture.facts,same:window.publicationFixture.same(),
      rows:[...document.querySelector('#batch-export-results').children].map(row=>row.textContent),
      processed:document.querySelector('#batch-export-controls progress').value,
      startDisabled:document.querySelector('#batch-export-button').disabled,
      cancelDisabled:document.querySelector('#batch-export-cancel').disabled,
      stages:document.querySelectorAll('svg[aria-hidden="true"]').length
    }));
    assert.equal(result.prepare,1);assert.equal(result.write,1);assert.equal(result.cancel,1);
    assert.ok(result.pngBytes>100);assert.equal(result.same,true);
    assert.match(result.rows[0],failure==='native-unverified'?/failed.*unverified\.png.*publication could not be verified/:/exported.*retained\.png/);
    assert.match(result.rows[1],/second\.nivram: cancelled/);
    assert.equal(result.processed,2);assert.equal(result.startDisabled,false);assert.equal(result.cancelDisabled,true);assert.equal(result.stages,0);
  });
}
