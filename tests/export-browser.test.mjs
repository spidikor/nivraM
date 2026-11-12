import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const {chromium}=createRequire(import.meta.url)('playwright');
const publicPath=fileURLToPath(new URL('../public/',import.meta.url));
async function bounded(operation,milliseconds,label){
  let timer;
  try{return await Promise.race([operation,new Promise((_resolve,reject)=>{timer=setTimeout(()=>reject(new Error(`${label} exceeded ${milliseconds} ms`)),milliseconds);})]);}
  finally{clearTimeout(timer);}
}
async function browserFixture(t){
  let server,browser,context;
  // A timeout does not cancel Playwright work. The external owned Job bounds
  // residual processes; these independent attempts preserve cleanup failures.
  t.after(async()=>{
    const failures=[];
    async function close(label,operation){
      try{await bounded(operation(),5000,label);}
      catch(error){failures.push(error);t.diagnostic(`${label}: ${error.message}`);}
    }
    try{
      if(context)await close('browser context close',()=>context.close());
      if(browser)await close('browser close',()=>browser.close());
    }finally{
      if(server){
        try{server.closeAllConnections();}catch(error){failures.push(error);}
        await close('HTTP server close',()=>new Promise((resolve,reject)=>server.close(error=>{
          if(error&&error.code!=='ERR_SERVER_NOT_RUNNING')reject(error);else resolve();
        })));
      }
    }
    if(failures.length)throw new AggregateError(failures,'Browser fixture cleanup failed');
    t.diagnostic('PNG fixture cleanup completed: context, browser and HTTP server.');
  },{timeout:20000});
  server=createServer(async(req,res)=>{
    try{
      if(req.url==='/api/status'){res.setHeader('Content-Type','application/json');return res.end(JSON.stringify({version:'0.0.0-fixture'}));}
      if(req.url==='/'){
        res.setHeader('Content-Type','text/html');
        return res.end('<!doctype html><svg xmlns="http://www.w3.org/2000/svg" width="500" height="300"><g id="drawing"><path d="M 10 10 L 210 10" style="stroke:#000;stroke-width:4;stroke-linecap:round;fill:none"/></g></svg>');
      }
      const filename=path.resolve(publicPath,req.url.slice(1));if(!filename.startsWith(publicPath)){res.statusCode=404;return res.end();}
      res.setHeader('Content-Type','text/javascript');res.end(await readFile(filename));
    }catch(error){res.statusCode=500;res.end(error.message);}
  });
  await bounded(new Promise((resolve,reject)=>{
    const onError=error=>reject(error);server.once('error',onError);
    server.listen(0,'127.0.0.1',()=>{server.removeListener('error',onError);resolve();});
  }),5000,'HTTP server listen');
  browser=await bounded(chromium.launch({headless:true,timeout:15000,...(process.env.NIVRAM_BROWSER_EXECUTABLE?{executablePath:process.env.NIVRAM_BROWSER_EXECUTABLE}:{})}).then(value=>{browser=value;return value;}),16000,'browser launch');
  context=await bounded(browser.newContext().then(value=>{context=value;return value;}),5000,'browser context creation');
  const page=await bounded(context.newPage(),5000,'browser page creation');
  page.setDefaultTimeout(10000);page.setDefaultNavigationTimeout(10000);
  await bounded(page.goto(`http://127.0.0.1:${server.address().port}/`,{timeout:10000}),11000,'browser navigation');
  await bounded(page.evaluate(async()=>{
    const module=await import('/modules/export.mjs');
    const fixtureGraph={atoms:[{id:'a',symbol:'C',x:10,y:10},{id:'b',symbol:'C',x:210,y:10}],bonds:[{id:'ab',begin:'a',end:'b',order:1}]};
    // Synthetic raster stress artwork is not a chemical-identity fixture.
    window.exporter={...module,exportPng:(group,options,context={})=>module.exportPng(group,options,{graph:fixtureGraph,...context})};window.exportCanvases=[];window.exportUrls=new Set();
    const create=document.createElement.bind(document);
    document.createElement=(name,...args)=>{const element=create(name,...args);if(name==='canvas')window.exportCanvases.push(element);return element;};
    const makeUrl=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL);
    URL.createObjectURL=blob=>{const url=makeUrl(blob);window.exportUrls.add(url);return url;};
    URL.revokeObjectURL=url=>{window.exportUrls.delete(url);revoke(url);};
  }),10000,'export fixture setup');
  return page;
}

for(const failure of ['readback','encoding'])test(`all export canvases are released after ${failure} failure`,async t=>{
  const page=await browserFixture(t);
  const result=await page.evaluate(async failure=>{
    if(failure==='readback')CanvasRenderingContext2D.prototype.getImageData=function(){throw new Error('Injected readback failure');};
    if(failure==='encoding')HTMLCanvasElement.prototype.toBlob=function(callback){callback(null);};
    let error='';try{await window.exporter.exportPng(document.querySelector('#drawing'),{width:1000,marginMode:'percent',margin:2});}catch(caught){error=caught.message;}
    return {error,canvases:window.exportCanvases.map(canvas=>({width:canvas.width,height:canvas.height}))};
  },failure);
  assert.match(result.error,failure==='readback'?/readback failure/:/encoding failed/);
  assert.ok(result.canvases.length>0);
  assert.ok(result.canvases.every(canvas=>canvas.width===1&&canvas.height===1),JSON.stringify(result.canvases));
});

test('an already cancelled export allocates no raster surfaces',async t=>{
  const page=await browserFixture(t);
  const result=await page.evaluate(async()=>{
    const controller=new AbortController();controller.abort();let error='';
    try{await window.exporter.exportPng(document.querySelector('#drawing'),{width:1000,marginMode:'percent',margin:2},{signal:controller.signal});}catch(caught){error=caught.name;}
    return {error,count:window.exportCanvases.length};
  });
  assert.equal(result.error,'AbortError');assert.equal(result.count,0);
});

test('unsafe memory demand is rejected before the first canvas is allocated',async t=>{
  const page=await browserFixture(t);
  const result=await page.evaluate(async()=>{
    const drawing=document.querySelector('#drawing');drawing.innerHTML='<rect x="0" y="0" width="100" height="100" fill="#000"/>';
    let error='';try{await window.exporter.exportPng(drawing,{width:9999,marginMode:'percent',margin:2});}catch(caught){error=caught.message;}
    return {error,count:window.exportCanvases.length};
  });
  assert.match(result.error,/memory.*budget/i);assert.equal(result.count,0);
});

for(const failure of ['context','decode','clipping'])test(`raster ${failure} failure releases canvases and object URLs`,async t=>{
  const page=await browserFixture(t);
  const result=await page.evaluate(async failure=>{
    if(failure==='context')HTMLCanvasElement.prototype.getContext=()=>null;
    if(failure==='decode')HTMLImageElement.prototype.decode=()=>Promise.reject(new Error('Injected decode failure'));
    if(failure==='clipping'){
      const read=CanvasRenderingContext2D.prototype.getImageData;let calls=0;
      CanvasRenderingContext2D.prototype.getImageData=function(...args){const data=read.apply(this,args);if(++calls===2)data.data[3]=255;return data;};
    }
    let error='';try{await window.exporter.exportPng(document.querySelector('#drawing'),{width:1000,marginMode:'percent',margin:2});}catch(caught){error=caught.message;}
    return {error,urls:window.exportUrls.size,canvases:window.exportCanvases.map(canvas=>({width:canvas.width,height:canvas.height}))};
  },failure);
  assert.match(result.error,failure==='context'?/unavailable/:failure==='decode'?/decode failure/:/safety boundary/);
  assert.equal(result.urls,0);assert.ok(result.canvases.length>0);
  assert.ok(result.canvases.every(canvas=>canvas.width===1&&canvas.height===1),JSON.stringify(result.canvases));
});

test('cancellation during decode releases resources and permits a successful retry',async t=>{
  const page=await browserFixture(t);
  const result=await page.evaluate(async()=>{
    const original=HTMLImageElement.prototype.decode;let started;
    const decoding=new Promise(resolve=>{started=resolve;});
    HTMLImageElement.prototype.decode=function(){started();return new Promise(()=>{});};
    const controller=new AbortController(),drawing=document.querySelector('#drawing');
    const pending=window.exporter.exportPng(drawing,{width:1000,margin:2},{signal:controller.signal});
    await decoding;controller.abort();let error='';try{await pending;}catch(caught){error=caught.name;}
    HTMLImageElement.prototype.decode=original;
    const retry=await window.exporter.exportPng(drawing,{width:333,marginMode:'pixels',margin:7});
    return {error,retry:retry.width,urls:window.exportUrls.size,canvases:window.exportCanvases.map(canvas=>({width:canvas.width,height:canvas.height}))};
  });
  assert.equal(result.error,'AbortError');assert.equal(result.retry,333);assert.equal(result.urls,0);
  assert.ok(result.canvases.every(canvas=>canvas.width===1&&canvas.height===1));
});

test('cancellation during encoding retains budget ownership until the encoder completes',async t=>{
  const page=await browserFixture(t);
  const result=await page.evaluate(async()=>{
    const encode=HTMLCanvasElement.prototype.toBlob;let encodingStarted,finish;
    const encoding=new Promise(resolve=>{encodingStarted=resolve;});
    HTMLCanvasElement.prototype.toBlob=function(callback,...args){encode.call(this,blob=>{finish=()=>callback(blob);encodingStarted();},...args);};
    const drawing=document.querySelector('#drawing'),options={width:333,marginMode:'pixels',margin:7},controller=new AbortController();
    const pending=window.exporter.exportPng(drawing,options,{signal:controller.signal});await encoding;controller.abort();
    let overlap='',cancel='';try{await window.exporter.exportPng(drawing,options);}catch(error){overlap=error.message;}
    finish();try{await pending;}catch(error){cancel=error.name;}
    HTMLCanvasElement.prototype.toBlob=encode;
    const retry=await window.exporter.exportPng(drawing,options);
    return {overlap,cancel,width:retry.width,urls:window.exportUrls.size,canvases:window.exportCanvases.map(canvas=>({width:canvas.width,height:canvas.height}))};
  });
  assert.match(result.overlap,/Another PNG export/);assert.equal(result.cancel,'AbortError');assert.equal(result.width,333);
  assert.equal(result.urls,0);assert.ok(result.canvases.every(canvas=>canvas.width===1&&canvas.height===1));
});

test('decoded PNGs preserve exact width, opaque white margins, artwork and vector state',async t=>{
  const page=await browserFixture(t);
  const reports=await page.evaluate(async()=>{
    const drawing=document.querySelector('#drawing'),line='<path d="M 10 10 L 210 10" style="stroke:#000;stroke-width:4;stroke-linecap:round;fill:none"/>';
    const cases=[
      {name:'default',art:line,options:{width:1000,marginMode:'percent',margin:2}},
      {name:'maximum-width',art:line,options:{width:9999,marginMode:'percent',margin:2}},
      {name:'fixed-fractional-margin',art:line,options:{width:333,marginMode:'pixels',margin:7.2}},
      {name:'tall',art:'<path d="M 10 10 L 10 530" style="stroke:#000;stroke-width:4;stroke-linecap:round;fill:none"/>',options:{width:100,marginMode:'percent',margin:2}},
      {name:'one-pixel',art:'<rect x="0" y="0" width="100" height="100" fill="#000"/>',options:{width:1,marginMode:'pixels',margin:0}},
      {name:'labels',art:line+'<text x="80" y="8" style="font-family:Arial;font-size:18px;fill:#000">13CH<tspan dy="-5" style="font-size:12px">3+</tspan></text>',options:{width:777,marginMode:'percent',margin:2}}
    ];
    const reports=[];
    for(const item of cases){
      drawing.innerHTML=item.art;const before=drawing.outerHTML;
      const result=await window.exporter.exportPng(drawing,item.options);
      const bytes=new Uint8Array(await result.blob.arrayBuffer()),bitmap=await createImageBitmap(result.blob);
      const decoded=document.createElement('canvas');decoded.width=bitmap.width;decoded.height=bitmap.height;
      const context=decoded.getContext('2d',{willReadFrequently:true});context.drawImage(bitmap,0,0);
      const data=context.getImageData(0,0,decoded.width,decoded.height).data;
      let left=decoded.width,top=decoded.height,right=-1,bottom=-1,opaque=true;
      for(let y=0;y<decoded.height;y++)for(let x=0;x<decoded.width;x++){
        const offset=(y*decoded.width+x)*4;
        if(data[offset+3]!==255)opaque=false;
        if(data[offset]!==255||data[offset+1]!==255||data[offset+2]!==255){left=Math.min(left,x);right=Math.max(right,x);top=Math.min(top,y);bottom=Math.max(bottom,y);}
      }
      reports.push({name:item.name,width:decoded.width,height:decoded.height,padding:result.padding,left,right:decoded.width-right-1,top,bottom:decoded.height-bottom-1,opaque,signature:[...bytes.slice(0,8)],unchanged:drawing.outerHTML===before,withinBudget:result.estimatedPeakBytes<=result.memoryBudgetBytes,ink:right>=left});
      bitmap.close();decoded.width=1;decoded.height=1;
    }
    return {reports,released:window.exportCanvases.every(canvas=>canvas.width===1&&canvas.height===1),urls:window.exportUrls.size};
  });
  const expectedWidths=[1000,9999,333,100,1,777],expectedPadding=[20,200,8,2,0,16];
  reports.reports.forEach((report,index)=>{
    assert.equal(report.width,expectedWidths[index],report.name);assert.equal(report.padding,expectedPadding[index],report.name);
    assert.deepEqual(report.signature,[137,80,78,71,13,10,26,10]);assert.ok(report.opaque&&report.ink&&report.unchanged&&report.withinBudget,report.name);
    assert.equal(report.top,report.padding,report.name);assert.equal(report.bottom,report.padding,report.name);
    assert.ok(report.left>=report.padding&&report.left<=report.padding+1,report.name);
    assert.ok(report.right>=report.padding&&report.right<=report.padding+1,report.name);
  });
  assert.ok(reports.reports.find(report=>report.name==='tall').height>9999);
  assert.equal(reports.released,true);assert.equal(reports.urls,0);
  t.diagnostic(JSON.stringify(reports.reports));
});

test('PNG acceptance: 300x150 rectangle decodes to 333x174 with default margins',{timeout:60000},async t=>{
  const page=await browserFixture(t);
  const result=await bounded(page.evaluate(async()=>{
    const drawing=document.querySelector('#drawing');
    drawing.innerHTML='<rect x="0" y="0" width="300" height="150" fill="#000000" stroke="none"/>';
    const before=drawing.outerHTML;
    const exported=await window.exporter.exportPng(drawing,{width:333});
    const png=[...new Uint8Array(await exported.blob.arrayBuffer())];
    let bitmap,decoded;
    try{
      bitmap=await createImageBitmap(exported.blob);
      decoded=document.createElement('canvas');decoded.width=bitmap.width;decoded.height=bitmap.height;
      const context=decoded.getContext('2d',{willReadFrequently:true});context.drawImage(bitmap,0,0);
      const data=context.getImageData(0,0,decoded.width,decoded.height).data;
      let left=decoded.width,top=decoded.height,right=-1,bottom=-1,opaque=true,whiteMargins=true;
      for(let y=0;y<decoded.height;y++)for(let x=0;x<decoded.width;x++){
        const offset=(y*decoded.width+x)*4;
        const white=data[offset]===255&&data[offset+1]===255&&data[offset+2]===255;
        if(data[offset+3]!==255)opaque=false;
        if((x<7||x>=decoded.width-7||y<7||y>=decoded.height-7)&&(!white||data[offset+3]!==255))whiteMargins=false;
        if(!white){left=Math.min(left,x);right=Math.max(right,x);top=Math.min(top,y);bottom=Math.max(bottom,y);}
      }
      return {width:decoded.width,height:decoded.height,left,right:decoded.width-right-1,top,bottom:decoded.height-bottom-1,opaque,whiteMargins,ink:right>=left,unchanged:drawing.outerHTML===before,png};
    }finally{bitmap?.close();if(decoded){decoded.width=1;decoded.height=1;}}
  }),20000,'PNG acceptance export and decode');
  // Keep the actual PNG and observations in captured output even if an assertion fails.
  t.diagnostic('PNG_ACCEPTANCE_RESULT '+JSON.stringify({...result,png:undefined}));
  t.diagnostic('PNG_ACCEPTANCE_PNG_BASE64 '+Buffer.from(result.png).toString('base64'));
  assert.equal(result.width,333,'decoded total width');
  assert.equal(result.height,174,'decoded height: ceil(319 * 150 / 300) + 14');
  assert.deepEqual([result.left,result.right,result.top,result.bottom],[7,7,7,7],'exact visible-ink margins');
  assert.equal(result.opaque,true,'every decoded pixel is opaque');
  assert.equal(result.whiteMargins,true,'all four seven-pixel margins are white');
  assert.equal(result.ink,true,'rectangle is visible');
  assert.equal(result.unchanged,true,'source SVG is unchanged');
});
