import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {testBrowser} from './browser-session.mjs';

const repository=fileURLToPath(new URL('../',import.meta.url));
const publicPath=path.join(repository,'public');
const python=process.env.NIVRAM_PYTHON??path.resolve(repository,'../venv/Scripts/python.exe');

function acceptanceProbe(){
  const result=spawnSync(python,['-c','import json; from nivram.acceptance_preview import PREVIEW_SNAPSHOT; print(json.dumps(PREVIEW_SNAPSHOT))'],
    {cwd:repository,encoding:'utf8',windowsHide:true,timeout:10000,maxBuffer:65536});
  assert.ifError(result.error);
  assert.equal(result.status,0,'The production Python acceptance probe must be available: '+result.stderr);
  const probe=JSON.parse(result.stdout);
  assert.equal(typeof probe,'string');
  return probe;
}

// Preserve the previous acceptance predicate to demonstrate its actual defect.
// The corrected probe below is imported from Python, not reimplemented here.
const previousProbe=`(() => {
  const image=document.getElementById('png-preview-image');
  if(!image?.complete || image.naturalWidth!==2000)return false;
  const canvas=document.createElement('canvas');canvas.width=1;canvas.height=1;
  const context=canvas.getContext('2d');context.drawImage(image,0,0);
  return {width:image.naturalWidth,height:image.naturalHeight,
    corner:Array.from(context.getImageData(0,0,1,1).data),
    displayedWidth:parseFloat(image.style.width),devicePixelRatio:window.devicePixelRatio};
})()`;

test('production profile PNG preview waits for decoded-image sizing before native pixel inspection',
  {timeout:60000},async t=>{
  const probe=acceptanceProbe(),pageErrors=[],externalRequests=[];
  const svg='<svg xmlns="http://www.w3.org/2000/svg" width="260" height="100"><path d="M40 65L120 25L200 65" fill="none" stroke="#000" stroke-width="4.2"/></svg>';
  const server=createServer(async(req,res)=>{
    try{
      if(req.url==='/api/render'){
        for await(const _ of req){};
        res.setHeader('Content-Type','application/json');
        return res.end(JSON.stringify({scene:{svg}}));
      }
      if(req.url==='/'){
        res.setHeader('Content-Type','text/html');
        return res.end('<!doctype html><meta name="nivram-token" content="synthetic-fixture-token"><div class="appearance-panel"></div>');
      }
      const filename=path.resolve(publicPath,req.url.slice(1));
      const relative=path.relative(publicPath,filename);
      if(relative.startsWith('..')||path.isAbsolute(relative))throw new Error('Fixture route escaped public files.');
      res.setHeader('Content-Type',filename.endsWith('.mjs')?'text/javascript':'text/plain');
      res.end(await readFile(filename));
    }catch(error){res.statusCode=500;res.end(String(error.message));}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
  const browser=await testBrowser(t);
  const page=await browser.newPage({viewport:{width:1366,height:900},deviceScaleFactor:1.25});
  page.setDefaultTimeout(10000);
  const origin=`http://127.0.0.1:${server.address().port}/`;
  await page.route('**/*',route=>{
    const url=route.request().url();
    if(url.startsWith(origin)||url.startsWith('blob:')||url.startsWith('data:'))return route.continue();
    externalRequests.push(new URL(url).origin);return route.abort();
  });
  page.on('pageerror',error=>pageErrors.push(error.message));
  await page.goto(origin);
  await page.evaluate(async()=>{
    const {initializeProfiles}=await import('/modules/profiles.mjs');
    const {DEFAULT_STYLE,DEFAULT_EXPORT}=await import('/modules/document.mjs');
    const documentState={schema:'nivram/1',graph:{atoms:[
      {id:'c1',symbol:'C',x:0,y:0},{id:'c2',symbol:'C',x:1.5,y:1},
      {id:'o1',symbol:'O',x:3,y:0}],bonds:[
      {id:'b1',begin:'c1',end:'c2',order:1},{id:'b2',begin:'c2',end:'o1',order:1}]},
      style:{...DEFAULT_STYLE},export:{...DEFAULT_EXPORT,width:2000}};
    const snapshot={revision:1,document:documentState};
    const editor={getSnapshot:()=>structuredClone(snapshot),subscribe:()=>{}};
    window.previewReadinessFixture={snapshot:()=>JSON.stringify(editor.getSnapshot()),decoded:false};
    await initializeProfiles(editor,{list_profiles:async()=>({profiles:[]})});
    document.querySelector('.profile-controls').open=true;
    const image=document.getElementById('png-preview-image');
    const decode=image.decode;
    const pause=new Promise(resolve=>{previewReadinessFixture.release=resolve;});
    image.decode=async function(){
      // Complete the browser's real PNG decode, but hold the production await
      // continuation that sets pixel-view width and enables Save this PNG.
      await decode.call(this);
      previewReadinessFixture.decoded=true;
      await pause;
    };
    previewReadinessFixture.restore=()=>{image.decode=decode;};
  });
  const before=await page.evaluate(()=>previewReadinessFixture.snapshot());
  try{
    await page.click('#preview-profile-button');
    await page.waitForFunction(()=>previewReadinessFixture.decoded);
    const paused=await page.evaluate(()=>{
      const image=document.getElementById('png-preview-image');
      const save=[...document.querySelectorAll('#png-preview-dialog button')].find(button=>button.textContent==='Save this PNG');
      return {complete:image.complete,width:image.naturalWidth,inlineWidth:image.style.width,saveDisabled:save.disabled};
    });
    assert.deepEqual(paused,{complete:true,width:2000,inlineWidth:'',saveDisabled:true});
    const oldResult=await page.evaluate(previousProbe);
    assert.equal(oldResult.width,2000);
    assert.ok(Number.isNaN(oldResult.displayedWidth),'The old probe reads NaN before production sizing completes.');
    assert.equal(JSON.parse(JSON.stringify(oldResult)).displayedWidth,null,'The JSON transport converts that NaN into Python None.');
    t.diagnostic('Reproduced: real PNG complete/naturalWidth=2000 while decode continuation is paused; previous probe produces NaN, serialized as null.');

    assert.equal(await page.evaluate(probe),false,'The actual fixed acceptance probe must keep waiting at that same state.');
    assert.equal(await page.evaluate(()=>previewReadinessFixture.snapshot()),before);
    t.diagnostic('Fixed probe waits while Save this PNG is disabled and pixel-view width is unset.');

    await page.evaluate(()=>previewReadinessFixture.release());
    await page.waitForFunction(probe);
    const ready=await page.evaluate(probe);
    assert.equal(ready.width,2000);
    assert.ok(ready.height>0);
    assert.deepEqual(ready.corner,[255,255,255,255]);
    assert.ok(Number.isFinite(ready.displayedWidth)&&ready.displayedWidth>0);
    assert.ok(Number.isFinite(ready.devicePixelRatio)&&ready.devicePixelRatio>0);
    assert.ok(Math.abs(ready.displayedWidth*ready.devicePixelRatio-2000)<=.01);
    assert.equal(await page.getByRole('button',{name:'Save this PNG',exact:true}).isDisabled(),false);
    assert.equal(await page.evaluate(()=>previewReadinessFixture.snapshot()),before);
    t.diagnostic(`Fixed probe passes after production continuation: ${ready.width} pixels, ${ready.displayedWidth} CSS pixels, DPR ${ready.devicePixelRatio}, opaque white corner.`);
  }finally{
    await page.evaluate(()=>{previewReadinessFixture.release();previewReadinessFixture.restore();document.getElementById('png-preview-dialog').close();});
  }
  // dialog.close() queues its close event; wait for the production listener.
  await page.waitForFunction(()=>!document.getElementById('png-preview-image').hasAttribute('src'));
  assert.deepEqual(pageErrors,[]);
  assert.deepEqual(externalRequests,[]);
  assert.equal(await page.locator('#png-preview-image').getAttribute('src'),null);
});
