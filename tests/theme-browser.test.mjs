import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {once} from 'node:events';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const repository=fileURLToPath(new URL('../',import.meta.url));
const runName=process.env.NIVRAM_THEME_RUN??'latest';
if(!/^[a-z0-9-]+$/.test(runName))throw new Error('Use a simple owned theme test run name.');
const fixture=process.env.NIVRAM_THEME_FIXTURE??'radical';
if(!['original','radical'].includes(fixture))throw new Error('Choose the original or radical public fixture.');
const artifacts=path.join(repository,'verification','dark-mode-browser',runName);
const {chromium}=createRequire(import.meta.url)('playwright');
const python=process.env.NIVRAM_PYTHON??path.resolve(repository,'../venv/Scripts/python.exe');
const bounded=async(promise,milliseconds,label)=>{
  let timer;
  try{return await Promise.race([promise,new Promise((_resolve,reject)=>{
    timer=setTimeout(()=>reject(new Error(`${label} exceeded ${milliseconds} ms.`)),milliseconds);
  })]);}finally{clearTimeout(timer);}
};

test('real local routes preserve chemical colors and PNG pixels across a display-only theme',
  {timeout:210000},async t=>{
  await mkdir(artifacts,{recursive:true});
  const receipt={started:new Date().toISOString(),checks:[],externalBlocked:[],pageErrors:[],cleanup:{}};
  const save=()=>writeFile(path.join(artifacts,'receipt.json'),JSON.stringify(receipt,null,2));
  const check=async(name,body)=>{
    try{const details=await body();receipt.checks.push({name,status:'passed',details});}
    catch(error){receipt.checks.push({name,status:'failed',error:error.stack});}
    await save();
  };
  let browserServer,browser,serverChild,page;
  t.after(async()=>{
    if(browserServer){
      const owned=browserServer.process();receipt.cleanup.browserPid=owned.pid;
      const exited=owned.exitCode!==null||owned.signalCode!==null?Promise.resolve():once(owned,'exit');
      try{
        const session=await bounded(browser.newBrowserCDPSession(),4000,'Owned browser shutdown connection');
        await bounded(session.send('Browser.close').catch(error=>{
          if(!/closed|disconnected/i.test(error.message))throw error;
        }),5000,'Owned browser close request');
        await bounded(exited,12000,'Owned browser exit');
        receipt.cleanup.browser={status:'exited',exitCode:owned.exitCode,signalCode:owned.signalCode,method:'CDP Browser.close'};
      }catch(error){
        receipt.cleanup.browser={status:'failed',error:error.message};
        try{await bounded(browserServer.kill(),10000,'Owned browser fallback termination');
          receipt.cleanup.browser.fallback={exitCode:owned.exitCode,signalCode:owned.signalCode};
        }catch(failure){receipt.cleanup.browser.fallbackError=failure.message;}
      }
    }
    if(serverChild){
      receipt.cleanup.serverPid=serverChild.pid;
      const exited=serverChild.exitCode!==null?Promise.resolve():once(serverChild,'exit');
      try{
        if(serverChild.exitCode===null)serverChild.stdin.end('stop\n');
        await bounded(exited,15000,'Owned local server shutdown');
        assert.equal(serverChild.exitCode,0);
        receipt.cleanup.server={status:'exited',exitCode:serverChild.exitCode,method:'shutdown/server_close'};
      }catch(error){
        receipt.cleanup.server={status:'failed',error:error.message};
        if(serverChild.exitCode===null){serverChild.kill();await bounded(exited,5000,'Owned server fallback termination').catch(()=>{});}
      }
    }
    receipt.finished=new Date().toISOString();await save();
    if(browserServer)assert.equal(receipt.cleanup.browser?.status,'exited','Owned browser must exit cleanly; fallback is retained as a failed lifecycle check.');
    assert.equal(receipt.cleanup.server?.status,'exited','Owned server must close its production resources cleanly.');
  });

  // Serve actual production routes; stdin provides deterministic owned cleanup.
  const launch=`import sys,threading\nfrom nivram.server import make_server\nserver=make_server(0)\nthread=threading.Thread(target=server.serve_forever)\nthread.start()\nprint(f'http://127.0.0.1:{server.server_port}/',flush=True)\ntry: sys.stdin.readline()\nfinally:\n server.shutdown()\n server.server_close()\n thread.join(5)\n`;
  serverChild=spawn(python,['-u','-c',launch],{cwd:repository,windowsHide:true,stdio:['pipe','pipe','pipe']});
  let output='',stderr='';serverChild.stderr.on('data',data=>{stderr=(stderr+data).slice(-4000);});
  const origin=await bounded(new Promise((resolve,reject)=>{
    serverChild.once('error',reject);
    serverChild.once('exit',code=>reject(new Error(`Production server exited ${code}: ${stderr}`)));
    serverChild.stdout.on('data',data=>{output+=data;const match=output.match(/http:\/\/127\.0\.0\.1:\d+\//);if(match)resolve(match[0]);});
  }),20000,'Production server startup');
  try{
    browserServer=await chromium.launchServer({headless:true,timeout:20000,
      ...(process.env.NIVRAM_BROWSER_EXECUTABLE?{executablePath:process.env.NIVRAM_BROWSER_EXECUTABLE}:{})});
  }catch(error){receipt.startupFailure={stage:'browser launch',error:error.message};await save();throw error;}
  browser=await chromium.connect(browserServer.wsEndpoint());
  page=await browser.newPage({viewport:{width:1366,height:768},deviceScaleFactor:1});
  page.setDefaultTimeout(15000);
  page.on('pageerror',error=>receipt.pageErrors.push(error.message));
  await page.route('**/*',route=>{
    const url=route.request().url();
    if(url.startsWith(origin)||url.startsWith('blob:')||url.startsWith('data:'))return route.continue();
    const parsed=new URL(url);receipt.externalBlocked.push(parsed.origin+parsed.pathname);return route.abort();
  });
  await page.goto(origin);
  await page.waitForFunction(()=>window.nivramDocument&&document.querySelector('#request-status').textContent==='Ready');
  receipt.environment={browser:browser.version(),viewport:{width:1366,height:768},devicePixelRatio:await page.evaluate(()=>devicePixelRatio),route:'actual nivram.server',nativeHost:false};
  const originalSynthetic='NCCO.CCCl.CCBr.CCI.CCF.CCS.CCP(C)C.[13CH3][C@@H](O)C(=O)[O-].N[C@H](C)C(=O)O.N[C@@H](C)C(=O)O';
  const synthetic=originalSynthetic+(fixture==='radical'?'.[CH3]':'');
  receipt.fixture={name:fixture,smiles:synthetic};
  await page.click('#import-button');await page.fill('#structure-input',synthetic);await page.click('#load-structure');
  await page.waitForFunction(()=>document.querySelector('#request-status').textContent==='Ready'&&!document.querySelector('#import-dialog').open);
  await page.fill('#export-width','640');
  await page.evaluate(async()=>{
    const {initializeProfiles}=await import('/modules/profiles.mjs');
    await initializeProfiles(window.nivramDocument,{list_profiles:async()=>({profiles:[]})});
    const {exportPng}=await import('/modules/export.mjs');
    const {renderBatchPng}=await import('/modules/batch-export.mjs');
    const paint=()=>[...document.querySelectorAll('#drawing path,#drawing line,#drawing polygon,#drawing polyline,#drawing text,#drawing tspan,#drawing circle,#drawing ellipse')].map(node=>{
      const computed=getComputedStyle(node);
      return {tag:node.localName,classes:node.getAttribute('class')??'',d:node.getAttribute('d')??'',inline:node.getAttribute('style')??'',stroke:computed.stroke,fill:computed.fill,strokeWidth:computed.strokeWidth};
    });
    const snapshot=()=>({state:window.nivramDocument.getSnapshot(),svg:document.querySelector('#drawing').outerHTML,
      undo:document.querySelector('#undo-button').disabled,redo:document.querySelector('#redo-button').disabled});
    const readImage=image=>{
      const canvas=document.createElement('canvas');
      canvas.width=image.naturalWidth||image.width;canvas.height=image.naturalHeight||image.height;
      const context=canvas.getContext('2d');context.drawImage(image,0,0);
      const pixels=context.getImageData(0,0,canvas.width,canvas.height).data;
      const result={pixels,width:canvas.width,height:canvas.height};canvas.width=canvas.height=1;
      return result;
    };
    const decode=async blob=>{const bitmap=await createImageBitmap(blob);try{return readImage(bitmap);}finally{bitmap.close();}};
    const metrics=group=>{
      const box=group.getBBox(),ctm=group.getCTM();
      return {box:{x:box.x,y:box.y,width:box.width,height:box.height},ctm:{a:ctm.a,b:ctm.b,c:ctm.c,d:ctm.d,e:ctm.e,f:ctm.f},body:group.innerHTML,
        textStyles:[...group.querySelectorAll('text,tspan')].map(node=>{const style=getComputedStyle(node);return {text:node.textContent,fontFamily:style.fontFamily,fontSize:style.fontSize,fontWeight:style.fontWeight};})};
    };
    const unclassified=[...document.querySelectorAll('#drawing path,#drawing line,#drawing circle,#drawing polygon,#drawing polyline')].filter(node=>!/(?:^|\s)bond-\d+(?:\s|$)/.test(node.getAttribute('class')??'')&&/\b(?:stroke|fill):#000000(?:;|$)/.test(node.getAttribute('style')??'')).map(node=>({svg:node.outerHTML,fill:getComputedStyle(node).fill,stroke:getComputedStyle(node).stroke}));
    window.themeQa={paint,snapshot,readImage,decode,metrics,exportPng,renderBatchPng,initial:snapshot(),light:paint(),images:{},stages:{},unclassified};
    new MutationObserver(records=>{for(const record of records)for(const node of record.addedNodes){if(node.isConnected&&node.localName==='svg'&&node.querySelector('g')&&themeQa.activePath)themeQa.stages[themeQa.activePath]=metrics(node.querySelector('g'));}}).observe(document.body,{childList:true});
  });
  receipt.propertiesSettled=await page.waitForFunction(()=>document.querySelector('#property-smiles').value.length>0,null,{timeout:5000}).then(()=>true,()=>false);
  await check('integrated curated palette and compact rotation toolbar remain accessible',async()=>{
    assert.equal(await page.locator('#elements [data-element]').count(),32);
    assert.equal(await page.locator('#elements [data-element]:visible').count(),10);
    await page.click('#more-elements-button');
    assert.equal(await page.locator('#more-elements-button').getAttribute('aria-expanded'),'true');
    assert.equal(await page.evaluate(()=>document.activeElement.dataset.element),'Li');
    await page.click('#more-elements-options [data-element="Se"]');
    assert.equal(await page.locator('#more-elements-button').getAttribute('aria-expanded'),'false');
    assert.equal(await page.locator('#elements [data-element="Se"]').getAttribute('aria-pressed'),'true');
    assert.equal(await page.locator('[data-tool="atom"]').getAttribute('aria-pressed'),'true');
    await page.click('#elements [data-element="C"]');await page.click('[data-tool="select"]');
    const bounds=[];
    for(const viewport of [{width:1366,height:768},{width:800,height:600}]){
      await page.setViewportSize(viewport);
      const row=await page.evaluate(()=>{
        const toolbar=document.querySelector('.drawing-transform-toolbar'),box=toolbar.getBoundingClientRect();
        return {toolbar:{x:box.x,y:box.y,width:box.width,height:box.height},
          overflow:toolbar.scrollWidth>toolbar.clientWidth,
          buttons:[...toolbar.querySelectorAll('button')].map(button=>{
            const r=button.getBoundingClientRect();return {id:button.id,label:button.getAttribute('aria-label'),
              inside:r.left>=box.left&&r.right<=box.right&&r.top>=box.top&&r.bottom<=box.bottom};
          })};
      });
      assert.equal(row.overflow,false);assert.equal(row.buttons.length,2);
      assert.ok(row.buttons.every(button=>button.inside&&button.label.includes('30°')));
      bounds.push({viewport,...row});
    }
    await page.setViewportSize({width:1366,height:768});
    assert.equal(await page.evaluate(()=>JSON.stringify(themeQa.initial.state)===JSON.stringify(themeQa.snapshot().state)),true);
    return {elements:32,commonVisible:10,selectionAndToolUpdated:true,documentUnchanged:true,bounds};
  });
  if(fixture==='original')await check('original fixture exactly reproduces the preserved failing SVG drawing',async()=>{
    const preservedPath=path.join(repository,'verification/dark-mode-browser/contract-corrected/original-drawing.svg');
    const preserved=await readFile(preservedPath,'utf8');
    const originalDrawing=preserved.match(/<g id="drawing">[\s\S]*?<\/g>/)?.[0];
    assert.ok(originalDrawing,'The preserved failing fixture must contain the molecular drawing.');
    const currentDrawing=await page.evaluate(()=>document.querySelector('#drawing').outerHTML);
    assert.equal(currentDrawing,originalDrawing,'Run the exact original drawing, not a differently laid-out substitute.');
    return {source:'verification/dark-mode-browser/contract-corrected/original-drawing.svg',
      sourceSha256:createHash('sha256').update(preserved).digest('hex'),
      drawingSha256:createHash('sha256').update(currentDrawing).digest('hex'),
      exactDrawingMatch:true};
  });
  await page.screenshot({path:path.join(artifacts,'light-editor.png')});
  await check('keyboard button activation, pressed state, and exact editor paint',async()=>{
    await page.locator('.skip-link').first().focus();
    // The page has three skip destinations, including keyboard drawing.
    // Traverse each real stop before asserting the first header control.
    const skipLinks=await page.locator('.skip-link').count();
    for(let index=0;index<skipLinks;index++)await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'theme-toggle');
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#theme-toggle').getAttribute('aria-pressed'),'true');
    const result=await page.evaluate(()=>{
      const before=themeQa.light,after=themeQa.paint(),errors=[];let blackStrokes=0,blackFills=0,colored=0,labels=0,joins=0,radicalDots=0;
      const counts={};
      for(let i=0;i<before.length;i++){
        const a=before[i],b=after[i],bond=/(?:^|\s)bond-\d+(?:\s|$)/.test(a.classes),primitive=['path','line','polygon','polyline'].includes(a.tag);
        const join=a.tag==='path'&&!a.classes&&/^M [-.\d]+,[-.\d]+ L [-.\d]+,[-.\d]+ L [-.\d]+,[-.\d]+$/.test(a.d)&&a.fill==='none'&&a.stroke==='rgb(0, 0, 0)';
        if(join)joins++;if(['circle','ellipse'].includes(a.tag)&&a.fill==='rgb(0, 0, 0)')radicalDots++;
        const token=a.classes.match(/(?:^|\s)(bond-\d+)(?:\s|$)/)?.[1];if(token)counts[token]=(counts[token]??0)+1;
        for(const property of ['stroke','fill']){
          const canonical=new RegExp(`(?:^|;)${property}:#000000(?:;|$)`).test(a.inline);
          const expected=(bond&&primitive&&canonical)||(join&&property==='stroke')?'rgb(214, 214, 214)':a[property];
          if(expected!==b[property])errors.push({i,property,expected,actual:b[property],classes:a.classes});
          if(bond&&primitive&&canonical){if(property==='stroke')blackStrokes++;else blackFills++;}
          if(!['none','rgb(0, 0, 0)'].includes(a[property]))colored++;
        }
        if(a.strokeWidth!==b.strokeWidth)errors.push({i,property:'strokeWidth'});
        if(!bond&&/(?:^|\s)atom-\d+(?:\s|$)/.test(a.classes)&&['path','text','tspan'].includes(a.tag))labels++;
      }
      return {errors,blackStrokes,blackFills,colored,labels,joins,radicalDots,radicalAtoms:themeQa.initial.state.document.graph.atoms.filter(atom=>atom.radicals>0).length,hashedBondCandidates:Object.values(counts).filter(count=>count>=5).length,
        colors:[...new Set(before.flatMap(value=>[value.fill,value.stroke]))],same:JSON.stringify(themeQa.initial)===JSON.stringify(themeQa.snapshot()),focused:document.activeElement.id};
    });
    assert.deepEqual(result.errors,[]);assert.ok(result.blackStrokes>0);assert.ok(result.blackFills>0);assert.ok(result.hashedBondCandidates>0);
    assert.ok(result.colored>0);assert.ok(result.labels>0);assert.ok(result.joins>0);assert.equal(result.radicalAtoms,fixture==='radical'?1:0);assert.ok(result.same);assert.equal(result.focused,'theme-toggle');
    for(const color of ['rgb(51, 51, 153)','rgb(255, 0, 0)','rgb(0, 153, 0)'])assert.ok(result.colors.includes(color),color);
    await page.keyboard.press('Space');assert.equal(await page.locator('#theme-toggle').getAttribute('aria-pressed'),'false');
    await page.keyboard.press('Space');assert.equal(await page.locator('#theme-toggle').getAttribute('aria-pressed'),'true');
    return result;
  });
  await page.screenshot({path:path.join(artifacts,'dark-editor.png')});
  await writeFile(path.join(artifacts,'original-drawing.svg'),await page.evaluate(()=>document.querySelector('#canvas').outerHTML));
  await writeFile(path.join(artifacts,'unclassified-black-primitives.json'),JSON.stringify(await page.evaluate(()=>themeQa.unclassified),null,2));
  for(const mode of ['light','dark']){
    if(await page.locator('#theme-toggle').getAttribute('aria-pressed')!==(mode==='dark'?'true':'false'))await page.click('#theme-toggle');
    await check(`${mode} main export and production batch renderer`,async()=>{
      const result=await page.evaluate(async mode=>{
        const qa=themeQa,project=qa.initial.state.document;
        qa.stages[mode+'Main']=qa.metrics(document.querySelector('#drawing'));
        const main=await qa.exportPng(document.querySelector('#drawing'),project.export,{graph:project.graph});
        qa.images[mode+'Main']=await qa.decode(main.blob);
        const png=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(new Error('PNG fixture read failed.'));reader.onload=()=>resolve(reader.result.split(',')[1]);reader.readAsDataURL(main.blob);});
        const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({graph:project.graph,style:project.style})});
        if(!response.ok)throw new Error('Batch preparation route failed.');
        const prepared={...await response.json(),project};
        qa.activePath=mode+'Batch';const batch=await qa.renderBatchPng(prepared);qa.images[mode+'Batch']=await qa.decode(batch.blob);qa.activePath=null;
        return {main:{width:main.width,height:main.height,padding:main.padding},batch:{width:batch.width,height:batch.height,padding:batch.padding},stages:document.querySelectorAll('svg[aria-hidden="true"]').length,png};
      },mode);
      await writeFile(path.join(artifacts,`${mode}-export.png`),Buffer.from(result.png,'base64'));delete result.png;
      assert.equal(result.main.width,640);assert.equal(result.batch.width,640);assert.equal(result.main.padding,13);assert.equal(result.batch.padding,13);assert.equal(result.stages,0);return result;
    });
    await check(`${mode} exact-PNG preview UI`,async()=>{
      const details=page.locator('.profile-controls');if(await details.getAttribute('open')===null)await details.locator('summary').click();
      try{
        await page.evaluate(mode=>{themeQa.activePath=mode+'Preview';},mode);
        await page.click('#preview-profile-button');
        await page.waitForFunction(()=>document.querySelector('#png-preview-image').complete&&document.querySelector('#png-preview-image').naturalWidth===640);
        const result=await page.evaluate(mode=>{
          const image=document.querySelector('#png-preview-image');themeQa.images[mode+'Preview']=themeQa.readImage(image);
          return {width:image.naturalWidth,height:image.naturalHeight,background:getComputedStyle(document.querySelector('#png-preview-viewport')).backgroundColor};
        },mode);
        assert.equal(result.width,640);assert.equal(result.background,'rgb(255, 255, 255)');return result;
      }finally{
        await page.evaluate(()=>{themeQa.activePath=null;});
        if(await page.locator('#png-preview-dialog').getAttribute('open')!==null)await page.getByRole('button',{name:'Close / cancel preview',exact:true}).click();
      }
    });
  }
  await writeFile(path.join(artifacts,'render-metrics.json'),JSON.stringify(await page.evaluate(()=>themeQa.stages),null,2));
  await check('light and dark decoded pixels match within each export path with opaque white margins',async()=>{
    const result=await page.evaluate(async()=>{
      const results={};
      for(const key of ['lightMain','darkMain','lightBatch','darkBatch','lightPreview','darkPreview']){
        const value=themeQa.images[key];if(!value)throw new Error(`Missing ${key}`);
        const base=themeQa.images[key.replace(/^dark/,'light')];
        let differences=0,transparent=0,nonwhiteMargin=0,black=0,colored=0;
        for(let i=0;i<value.pixels.length;i++)if(value.pixels[i]!==base.pixels[i])differences++;
        for(let y=0;y<value.height;y++)for(let x=0;x<value.width;x++){
          const i=(y*value.width+x)*4,p=value.pixels;
          if(p[i+3]!==255)transparent++;
          const white=p[i]===255&&p[i+1]===255&&p[i+2]===255&&p[i+3]===255;
          if((x<13||x>=value.width-13||y<13||y>=value.height-13)&&!white)nonwhiteMargin++;
          if(p[i]===0&&p[i+1]===0&&p[i+2]===0&&p[i+3]===255)black++;
          if(p[i]!==p[i+1]||p[i+1]!==p[i+2])colored++;
        }
        const digest=await crypto.subtle.digest('SHA-256',value.pixels);
        results[key]={width:value.width,height:value.height,referenceWidth:base.width,referenceHeight:base.height,pixelCount:value.width*value.height,differences,transparent,nonwhiteMargin,black,colored,
          rgbaSha256:[...new Uint8Array(digest)].map(byte=>byte.toString(16).padStart(2,'0')).join('')};
      }
      return results;
    });
    await writeFile(path.join(artifacts,'pixel-comparison.json'),JSON.stringify(result,null,2));
    for(const value of Object.values(result)){assert.equal(value.width,640);assert.equal(value.height,value.referenceHeight);assert.equal(value.differences,0);assert.equal(value.transparent,0);assert.equal(value.nonwhiteMargin,0);assert.ok(value.black>0);assert.ok(value.colored>0);}
    return result;
  });
  for(const key of ['lightMain','darkMain','lightBatch','darkBatch','lightPreview','darkPreview']){
    const encoded=await page.evaluate(async key=>{const value=themeQa.images[key];if(!value)return null;return await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=reject;reader.onload=()=>resolve(reader.result.split(',')[1]);reader.readAsDataURL(new Blob([value.pixels]));});},key);
    if(encoded)await writeFile(path.join(artifacts,key+'.rgba'),Buffer.from(encoded,'base64'));
  }
  await check('main, preview, and batch export paths produce the same pixels',async()=>{
    const result=await page.evaluate(()=>{
      const base=themeQa.images.lightMain;
      return Object.fromEntries(['lightBatch','lightPreview','darkBatch','darkPreview'].map(key=>{const value=themeQa.images[key];if(!value)return [key,{missing:true}];let differences=0;for(let i=0;i<Math.max(base.pixels.length,value.pixels.length);i++)if(base.pixels[i]!==value.pixels[i])differences++;return [key,{width:value.width,height:value.height,referenceHeight:base.height,differences}];}));
    });
    await writeFile(path.join(artifacts,'cross-path-comparison.json'),JSON.stringify(result,null,2));
    for(const value of Object.values(result)){assert.equal(value.height,value.referenceHeight);assert.equal(value.differences,0);}return result;
  });
  await check('review artwork stays white and SVG/document/history are unchanged',async()=>{
    const result=await page.evaluate(()=>({same:JSON.stringify(themeQa.initial)===JSON.stringify(themeQa.snapshot()),
      panels:Object.fromEntries(['cleanup-current','cleanup-proposed','image-import-candidate'].map(id=>[id,getComputedStyle(document.getElementById(id)).backgroundColor])),
      source:getComputedStyle(document.querySelector('.image-import-source-wrap')).backgroundColor}));
    assert.ok(result.same);for(const color of Object.values(result.panels))assert.equal(color,'rgb(255, 255, 255)');assert.equal(result.source,'rgb(255, 255, 255)');return result;
  });
  await check('a bounds failure removes temporary SVG stages and permits a new export',async()=>{
    const result=await page.evaluate(async()=>{
      const before=document.querySelectorAll('body>svg').length,original=SVGGraphicsElement.prototype.getBBox;
      let error='';
      try{
        SVGGraphicsElement.prototype.getBBox=()=>{throw new Error('Injected vector-bounds failure');};
        await themeQa.exportPng(document.querySelector('#drawing'),themeQa.initial.state.document.export,{graph:themeQa.initial.state.document.graph});
      }catch(failure){error=failure.message;}
      finally{SVGGraphicsElement.prototype.getBBox=original;}
      const after=document.querySelectorAll('body>svg').length;
      const retry=await themeQa.exportPng(document.querySelector('#drawing'),themeQa.initial.state.document.export,{graph:themeQa.initial.state.document.graph});
      return {error,before,after,retryWidth:retry.width,unchanged:JSON.stringify(themeQa.initial)===JSON.stringify(themeQa.snapshot())};
    });
    assert.match(result.error,/Injected vector-bounds failure/);assert.equal(result.after,result.before);assert.equal(result.retryWidth,640);assert.ok(result.unchanged);return result;
  });
  await check('physiology custom comparison preserves exact decimals, context gaps, and prior valid values',async()=>{
    await page.locator('.science-panel summary').click();
    await page.waitForSelector('#physiology-custom-lower');
    const contexts=await page.evaluate(async()=>{const dataset=await(await fetch('/api/physiology')).json();return dataset.presets.map(value=>({id:value.id,kind:value.display_kind}));});
    const gap=contexts.find(value=>value.id==='bowmans-space-gap'),other=contexts.find(value=>value.id!==gap.id);assert.ok(gap&&other);
    await page.selectOption('#physiology-context',gap.id);
    const detail=await page.locator('#physiology-detail').textContent();assert.match(detail,/gap|unavailable|not established|no |insufficient|not verified/i);
    const apply=page.getByRole('button',{name:'Apply comparison',exact:true});
    await page.fill('#physiology-custom-lower','7.35');await page.fill('#physiology-custom-upper','7.45');await page.fill('#physiology-custom-label','Synthetic comparison');await apply.click();
    const band=await page.locator('#physiology-custom-result').textContent();assert.match(band,/7\.35–7\.45/);assert.match(band,/not a verified physiological interval/);assert.equal(await page.locator('#physiology-detail').textContent(),detail);
    for(const [lower,upper] of [['8','7'],['','7.45'],['-1','7.45'],['7.35','15']]){
      await page.fill('#physiology-custom-lower',lower);await page.fill('#physiology-custom-upper',upper);await apply.click();assert.equal(await page.locator('#physiology-custom-result').textContent(),band);
    }
    await page.fill('#physiology-custom-lower','7.35');await page.fill('#physiology-custom-upper','7.35');await apply.click();
    const point=await page.locator('#physiology-custom-result').textContent();assert.match(point,/7\.35 \(single pH value\)/);
    await page.selectOption('#physiology-context',other.id);assert.equal(await page.locator('#physiology-custom-result').textContent(),'No custom comparison applied.');
    await page.selectOption('#physiology-context',gap.id);assert.equal(await page.locator('#physiology-custom-result').textContent(),point);assert.equal(await page.locator('#physiology-custom-lower').inputValue(),'7.35');assert.equal(await page.locator('#physiology-detail').textContent(),detail);
    await page.getByRole('button',{name:'Remove comparison',exact:true}).click();assert.equal(await page.locator('#physiology-custom-result').textContent(),'No custom comparison applied.');assert.equal(await page.locator('#physiology-detail').textContent(),detail);
    assert.equal(await page.evaluate(()=>JSON.stringify(themeQa.initial)===JSON.stringify(themeQa.snapshot())),true);
    return {gapContext:gap.id,otherContext:other.id,band,point,invalidProposals:4,sourceGapPreserved:true,documentUnchanged:true};
  });
  await check('no application page errors',async()=>{assert.deepEqual(receipt.pageErrors,[]);return {externalBlocked:receipt.externalBlocked};});
  assert.deepEqual(receipt.checks.filter(value=>value.status==='failed').map(value=>({name:value.name,error:value.error})),[]);
});
