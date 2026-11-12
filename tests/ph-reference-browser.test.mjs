import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {productionEditor,ready,state,choose} from './editor-browser-fixture.mjs';

const CHART_SHA256='48444a82d4edcb5bec0f1965faacdde18d9c17db3063d042abada2f705c9f54a';
const dataset=JSON.parse(await readFile(new URL('../nivram/data/physiology.json',import.meta.url),'utf8'));
const slider=page=>page.getByRole('slider',{name:'Selected reference pH',exact:true});
const input=page=>page.getByLabel('Selected pH (0\u201314, exact 0.1 samples)',{exact:true});
// Application changes use real user input. Evaluations only inspect production
// state/public Chart.js objects, or decode downloaded bytes in an isolated canvas.
async function typeInto(page,locator,text){await locator.click();await page.keyboard.press('Control+A');await page.keyboard.type(text);}
async function syntheticDrawing(page){
  await page.locator('#import-button').click();await typeInto(page,page.locator('#structure-input'),'[13CH3]CO');
  await page.locator('#load-structure').click();await ready(page);
  assert.equal((await state(page)).document.graph.atoms.length,3);
}
async function context(page,id){
  // The editor becomes ready before its independent physiology request resolves.
  // Wait for the actual requested option instead of racing the async data mount.
  await page.waitForFunction(value=>[...document.querySelectorAll('#physiology-context option')].some(option=>option.value===value),id);
  const details=page.locator('#physiology-context').locator('xpath=ancestor::details');
  if(!await details.evaluate(el=>el.open))await details.locator('summary').click();
  await choose(page,'physiology-context',id);
}
async function open(page){
  assert.equal(await page.locator('#open-ph-chart').count(),1,'The production page must offer the pH reference chart.');
  await page.locator('#open-ph-chart').click();await page.locator('#ph-reference-dialog[open] canvas').waitFor();
}
async function selected(page,ph){
  await page.waitForFunction(value=>document.querySelector('#ph-chart-host canvas')?.getAttribute('aria-valuenow')===String(value),ph);
  assert.equal(await input(page).inputValue(),ph.toFixed(1));
  assert.match(await page.locator('[data-ph-readout]').innerText(),new RegExp(`sample ${Math.round(ph*10)} of 0\u2013140`));
}
async function enter(page,text){await typeInto(page,input(page),text);await page.getByRole('button',{name:'Select pH',exact:true}).click();}
async function download(page,id){
  const awaited=page.waitForEvent('download');await page.locator('#'+id).click();const item=await awaited;
  const stream=await item.createReadStream();assert.ok(stream,'The browser must provide an actual download stream.');
  const chunks=[];let length=0;try{for await(const chunk of stream){length+=chunk.length;assert.ok(length<=8*1024*1024,'Reference download exceeded its bound.');chunks.push(chunk);}}
  finally{stream.destroy();}
  assert.equal(await item.failure(),null);return {name:item.suggestedFilename(),bytes:Buffer.concat(chunks)};
}
async function jsonDownload(page){const file=await download(page,'ph-export-json');assert.match(file.name,/\.json$/);return JSON.parse(file.bytes.toString('utf8'));}
async function chartInfo(page){return page.evaluate(()=>{
  const canvas=document.querySelector('#ph-chart-host canvas'),chart=window.Chart.getChart(canvas);
  return {version:window.Chart.version,datasets:structuredClone(chart.data.datasets),yDisplayed:chart.options.scales.y.display,
    xMin:chart.scales.x.min,xMax:chart.scales.x.max,id:chart.id,instances:Object.keys(window.Chart.instances).length,
    background:[...canvas.getContext('2d').getImageData(0,0,1,1).data]};
});}
async function pngPixels(page,bytes){
  assert.deepEqual([...bytes.subarray(0,8)],[137,80,78,71,13,10,26,10]);
  return page.evaluate(async encoded=>{
    const binary=atob(encoded),data=Uint8Array.from(binary,c=>c.charCodeAt(0));
    const bitmap=await createImageBitmap(new Blob([data],{type:'image/png'})),canvas=document.createElement('canvas');
    try{
      canvas.width=bitmap.width;canvas.height=bitmap.height;const ctx=canvas.getContext('2d');ctx.drawImage(bitmap,0,0);
      const rgba=ctx.getImageData(0,0,canvas.width,canvas.height).data;let transparent=0,nonwhiteBorder=0;
      for(let i=0;i<rgba.length;i+=4){if(rgba[i+3]!==255)transparent++;const pixel=i/4,x=pixel%canvas.width,y=Math.floor(pixel/canvas.width);
        if((x===0||y===0||x===canvas.width-1||y===canvas.height-1)&&!(rgba[i]===255&&rgba[i+1]===255&&rgba[i+2]===255&&rgba[i+3]===255))nonwhiteBorder++;}
      const digest=await crypto.subtle.digest('SHA-256',rgba);
      return {width:canvas.width,height:canvas.height,transparent,nonwhiteBorder,sha256:[...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('')};
    }finally{bitmap.close();canvas.width=0;canvas.height=0;}
  },bytes.toString('base64'));
}

function parseCsv(text){
  const rows=[];let row=[],field='',quoted=false;
  for(let i=0;i<text.length;i++){const c=text[i];if(quoted){if(c==='"'&&text[i+1]==='"'){field+='"';i++;}else if(c==='"')quoted=false;else field+=c;}
    else if(c==='"')quoted=true;else if(c===','){row.push(field);field='';}else if(c==='\n'){row.push(field.endsWith('\r')?field.slice(0,-1):field);rows.push(row);row=[];field='';}else field+=c;}
  assert.equal(quoted,false);assert.equal(field,'');return rows;
}

function documentSummary(bytes,base){
  // Never print document bodies, injected source text, or the session token.
  const text=bytes.toString('utf8').replace(/(<meta name="nivram-token" content=")[^"]*(")/,'$1__TOKEN__$2');
  return {text,bytes:Buffer.byteLength(text),sha256:createHash('sha256').update(text).digest('hex'),
    scripts:[...text.matchAll(/<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']/gi)].map(match=>{
      const url=new URL(match[1],base);return url.origin+url.pathname;
    })};
}
function documentDifference(expected,received){
  let prefix=0,suffix=0;
  while(prefix<expected.length&&prefix<received.length&&expected[prefix]===received[prefix])prefix++;
  while(suffix<expected.length-prefix&&suffix<received.length-prefix&&expected[expected.length-suffix-1]===received[received.length-suffix-1])suffix++;
  return {equal:expected===received,firstDifferentCharacter:prefix,expectedDifferenceLength:expected.length-prefix-suffix,receivedDifferenceLength:received.length-prefix-suffix};
}

test('pH selection preserves reference DOM nodes while updating selected values and accessibility text', {timeout:90000}, async t=>{
  const page=await productionEditor(t);const before=await state(page);
  await context(page,'neuronal-cytosol-bonnet-2019');await open(page);
  const capture=()=>page.locator('#ph-chart-host').evaluateHandle(host=>{
    const readout=host.querySelector('[data-ph-readout]');
    const roots=[...host.querySelectorAll('tbody, section > ul, details > ul'),readout.nextElementSibling,readout.nextElementSibling.nextElementSibling];
    return roots.flatMap(node=>[node,...node.querySelectorAll('*')]).map(node=>({node,text:node.textContent}));
  });
  async function unchanged(nodes){
    const changed=await nodes.evaluate(values=>values.filter(({node,text})=>!node.isConnected||node.textContent!==text)
      .map(({node})=>({tag:node.nodeName,connected:node.isConnected})));
    assert.deepEqual(changed,[],'Selection must preserve existing reference table, evidence and interpretation nodes.');
  }
  await t.test('keyboard selection',async()=>{
    const nodes=await capture();
    try{
      // Opening the real dialog focuses the slider without a synthetic DOM action.
      assert.equal(await slider(page).evaluate(canvas=>canvas===document.activeElement),true);
      for(const [key,ph] of [['Home',0],['End',14],['ArrowLeft',13.9]]){
        await page.keyboard.press(key);await selected(page,ph);await unchanged(nodes);
        assert.equal(await slider(page).getAttribute('aria-valuetext'),`pH ${ph.toFixed(1)}; molecular predictions unavailable`);
      }
    }finally{await nodes.dispose();}
  });
  await t.test('pointer selection',async()=>{
    const nodes=await capture();
    try{
      const point=await slider(page).evaluate(canvas=>{const chart=window.Chart.getChart(canvas),rect=canvas.getBoundingClientRect();return {x:rect.left+chart.scales.x.getPixelForValue(7.36)*rect.width/chart.width,y:rect.top+(chart.chartArea.top+chart.chartArea.bottom)/2*rect.height/chart.height};});
      await page.mouse.move(point.x,point.y);await selected(page,7.4);await unchanged(nodes);
    }finally{await nodes.dispose();}
  });
  await t.test('numeric validation and same-sample normalization',async()=>{
    const nodes=await capture();
    try{
      await enter(page,'7.3');await selected(page,7.3);await unchanged(nodes);
      await enter(page,'7.35');assert.match(await page.locator('#ph-chart-host [role=alert]').innerText(),/0\.1/);
      assert.equal(await input(page).inputValue(),'7.35');assert.equal(await slider(page).getAttribute('aria-valuenow'),'7.3');await unchanged(nodes);
      await enter(page,'7.3000');await selected(page,7.3);await unchanged(nodes);
      assert.equal(await page.locator('#ph-chart-host [role=alert]').innerText(),'');
    }finally{await nodes.dispose();}
  });
  const exported=await jsonDownload(page);
  assert.equal(exported.reference.context.id,'neuronal-cytosol-bonnet-2019');assert.deepEqual(exported.selectedSample,{index:73,ph:7.3});
  await page.locator('#ph-change-context').click();await choose(page,'physiology-context','arterial-blood-uvm');await open(page);
  await selected(page,7.3);assert.match(await page.locator('#ph-chart-host table').innerText(),/7\.35.*7\.45/);
  assert.equal((await jsonDownload(page)).reference.context.id,'arterial-blood-uvm');
  await page.locator('#close-ph-chart').click();assert.equal(await page.evaluate(()=>Object.keys(window.Chart.instances).length),0);
  assert.deepEqual(await state(page),before);
});

test('production pH reference chart loads only pinned local assets and keeps continuous references distinct from selected samples', {timeout:90000}, async t=>{
  const page=await productionEditor(t);
  const network=await page.context().newCDPSession(page),networkRequests=new Map();
  network.on('Network.requestWillBeSent',event=>networkRequests.set(event.requestId,{
    url:event.request.url,type:event.type,documentURL:event.documentURL,initiator:event.initiator
  }));
  network.on('Network.loadingFailed',event=>{const request=networkRequests.get(event.requestId);if(request)Object.assign(request,{errorText:event.errorText,blockedReason:event.blockedReason,canceled:event.canceled});});
  await network.send('Network.enable');
  const requests=[],chartAssets=[];page.on('request',request=>requests.push(request.url()));
  page.on('response',response=>{if(/\/chart\.umd\.min\.js(?:\?|$)/.test(response.url()))chartAssets.push(response.body());});
  const response=await page.reload();await ready(page);
  const receivedDocument=await response.body();
  const csp=response.headers()['content-security-policy'];assert.ok(csp,'Production responses must retain their CSP.');
  assert.match(csp,/script-src[^;]*'self'/);assert.doesNotMatch(csp,/script-src[^;]*(?:https?:|\*|'unsafe-inline'|'unsafe-eval')/);
  await syntheticDrawing(page);const before=await state(page);
  await context(page,'arterial-blood-uvm');await open(page);
  assert.match(await page.locator('#ph-chart-host').innerText(),/Molecular predictions unavailable/);
  assert.match(await page.locator('#ph-chart-host').innerText(),/Reference values are not molecular populations/);
  const info=await chartInfo(page);assert.equal(info.version,'4.5.1');assert.deepEqual(info.datasets,[]);assert.equal(info.yDisplayed,false);
  assert.equal(info.xMin,0);assert.equal(info.xMax,14);assert.match(await page.locator('#ph-chart-host table').innerText(),/7\.35.*7\.45/);
  const exported=await jsonDownload(page),arterial=dataset.presets.find(value=>value.id==='arterial-blood-uvm');
  assert.deepEqual(exported.reference.context,arterial);assert.equal(exported.populationResults,null);
  assert.deepEqual(exported.reference.sources,dataset.sources.filter(source=>arterial.source_ids.includes(source.id)));
  assert.equal(exported.reference.overlays[0].lower,7.35);assert.equal(exported.reference.overlays[0].upper,7.45);
  await selected(page,7);await slider(page).click();await page.keyboard.press('Home');await selected(page,0);
  await page.keyboard.press('End');await selected(page,14);await page.keyboard.press('ArrowLeft');await selected(page,13.9);
  await enter(page,'7.3');await selected(page,7.3);
  const point=await slider(page).evaluate(canvas=>{const chart=window.Chart.getChart(canvas),rect=canvas.getBoundingClientRect();return {x:rect.left+chart.scales.x.getPixelForValue(7.36)*rect.width/chart.width,y:rect.top+(chart.chartArea.top+chart.chartArea.bottom)/2*rect.height/chart.height};});
  await page.mouse.move(point.x,point.y);await selected(page,7.4);
  await enter(page,'7.35');assert.match(await page.locator('#ph-chart-host [role=alert]').innerText(),/0\.1/);assert.equal(await slider(page).getAttribute('aria-valuenow'),'7.4');
  await enter(page,'7.4000000000000001');assert.match(await page.locator('#ph-chart-host [role=alert]').innerText(),/0\.1/);assert.equal(await slider(page).getAttribute('aria-valuenow'),'7.4');
  await enter(page,'7.4');await selected(page,7.4);
  if(process.env.NIVRAM_PH_REFERENCE_SCREENSHOT)await page.locator('#ph-reference-dialog').screenshot({path:process.env.NIVRAM_PH_REFERENCE_SCREENSHOT});
  assert.deepEqual(await state(page),before,'References and pH navigation must not revise, dirty or change the molecular document/history.');
  assert.equal(chartAssets.length,1);assert.equal(createHash('sha256').update(await chartAssets[0]).digest('hex'),CHART_SHA256);
  const origin=new URL(page.url()).origin;assert.ok(requests.length>0);
  const external=requests.filter(url=>!url.startsWith(origin+'/')&&!/^(data|blob):/.test(url));
  if(external.length){
    const directResponse=await page.request.get(page.url());
    let directDocument;try{assert.equal(directResponse.status(),200);directDocument=await directResponse.body();}finally{await directResponse.dispose();}
    const disk=documentSummary(await readFile(new URL('../public/index.html',import.meta.url)),page.url());
    const received=documentSummary(receivedDocument,page.url()),direct=documentSummary(directDocument,page.url());
    const safe=({text,...summary})=>summary;
    t.diagnostic(JSON.stringify({externalRequests:external,initiators:[...networkRequests.values()].filter(request=>external.includes(request.url)),
      documents:{normalization:'Only the local session-token content is replaced with the source placeholder.',disk:safe(disk),browser:safe(received),directLocalRequest:safe(direct),
        diskVersusBrowser:documentDifference(disk.text,received.text),diskVersusDirect:documentDifference(disk.text,direct.text)}}));
  }
  assert.deepEqual(external,[],'No external page requests are permitted, including blocked attempts. Initiator diagnostics do not exempt a request.');
});

test('production reference downloads retain an unknown nephron gap and exact unverified custom comparison provenance', {timeout:90000}, async t=>{
  const page=await productionEditor(t);await syntheticDrawing(page);const before=await state(page);
  await context(page,'bowmans-space-gap');
  for(const [id,value] of [['physiology-custom-lower','6.75'],['physiology-custom-upper','7.25'],['physiology-custom-label','=SYNTHETIC(1)']])await typeInto(page,page.locator('#'+id),value);
  await page.getByRole('button',{name:'Apply comparison',exact:true}).click();await open(page);
  const text=await page.locator('#ph-chart-host').innerText();assert.match(text,/No verified|not established|gap|unavailable/i);assert.match(text,/6\.75.*7\.25/);assert.match(text,/unverified/);
  assert.deepEqual((await chartInfo(page)).datasets,[]);
  await enter(page,'6.8');await selected(page,6.8);
  const value=await jsonDownload(page),gap=dataset.presets.find(item=>item.id==='bowmans-space-gap');
  assert.equal(value.exportKind,'ph-reference-context');assert.equal(value.reference.datasetVersion,dataset.dataset_version);
  assert.deepEqual(value.reference.context,gap);assert.equal(value.populationResults,null);assert.deepEqual(value.selectedSample,{index:68,ph:6.8});
  assert.ok(value.reference.gap.reason);assert.equal(value.reference.overlays.length,1);assert.equal(value.reference.overlays[0].kind,'custom_band');
  assert.equal(value.reference.comparison.evidence_status,'unverified');assert.equal(value.reference.comparison.source_kind,'user_entered');
  assert.deepEqual(value.reference.comparison.ph,{lower:6.75,upper:7.25});assert.equal(value.reference.comparison.label,'=SYNTHETIC(1)');
  const csv=await download(page,'ph-export-csv');assert.match(csv.name,/\.csv$/);
  const rows=parseCsv(csv.bytes.toString('utf8'));assert.deepEqual(rows[0],['path','type','value','spreadsheet_escaped']);
  const row=path=>rows.find(value=>value[0]===path);
  assert.deepEqual(row('/reference/comparison/label'),['/reference/comparison/label','string',"'=SYNTHETIC(1)",'true']);
  assert.deepEqual(row('/reference/comparison/ph/lower'),['/reference/comparison/ph/lower','number','6.75','false']);
  assert.deepEqual(row('/populationResults'),['/populationResults','null','null','false']);
  assert.equal(row('/reference/gap/reason')[2],value.reference.gap.reason);assert.equal(row('/reference/datasetVersion')[2],value.reference.datasetVersion);
  assert.match(await page.locator('#ph-export-status').innerText(),/prepared/i);assert.doesNotMatch(await page.locator('#ph-export-status').innerText(),/saved/i);
  await page.locator('#ph-change-context').click();assert.equal(await page.evaluate(()=>document.activeElement.id),'physiology-context');
  await choose(page,'physiology-context','arterial-blood-uvm');await open(page);assert.doesNotMatch(await page.locator('#ph-chart-host table').innerText(),/SYNTHETIC/);
  await page.locator('#ph-change-context').click();await choose(page,'physiology-context','bowmans-space-gap');await open(page);assert.match(await page.locator('#ph-chart-host table').innerText(),/SYNTHETIC/);
  assert.deepEqual(await state(page),before);
});

test('production chart theme stays display-only, real PNG downloads stay opaque white, and closing destroys chart instances', {timeout:90000}, async t=>{
  const page=await productionEditor(t);await syntheticDrawing(page);const before=await state(page);
  await context(page,'arterial-blood-uvm');await open(page);await enter(page,'7.4');
  const light=await chartInfo(page);assert.deepEqual(light.background,[255,255,255,255]);assert.equal(light.instances,1);
  const lightFile=await download(page,'ph-export-png');assert.match(lightFile.name,/\.png$/);const lightPixels=await pngPixels(page,lightFile.bytes);
  assert.equal(lightPixels.transparent,0);assert.equal(lightPixels.nonwhiteBorder,0);assert.equal(lightPixels.width,1000);assert.equal(lightPixels.height,560);
  await page.locator('#close-ph-chart').click();
  assert.equal(await page.locator('#ph-reference-dialog').evaluate(el=>el.open),false);
  assert.equal(await page.evaluate(()=>Object.keys(window.Chart.instances).length),0);assert.equal(await page.locator('#ph-chart-host canvas').count(),0);
  await page.locator('#theme-toggle').click();await open(page);await selected(page,7.4);
  const dark=await chartInfo(page);assert.deepEqual(dark.background,[25,28,35,255]);assert.notEqual(dark.id,light.id);assert.equal(dark.instances,1);
  const darkPixels=await pngPixels(page,(await download(page,'ph-export-png')).bytes);
  assert.deepEqual(darkPixels,lightPixels,'Decoded exported pixels must remain identical across display themes.');
  await slider(page).click();await page.keyboard.press('Escape');
  assert.equal(await page.locator('#ph-reference-dialog').evaluate(el=>el.open),false);assert.equal(await page.evaluate(()=>Object.keys(window.Chart.instances).length),0);
  assert.deepEqual(await state(page),before);
});

test('malformed science capability response fails closed while physiology stays usable and manual chart retry recovers', {timeout:90000}, async t=>{
  const page=await productionEditor(t),pattern='**/api/science-capabilities';let injected=0;
  await page.route(pattern,route=>{injected++;return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({module:'maraPssiwS',molecularPredictorAvailable:true})});});
  await page.reload();await ready(page);await context(page,'arterial-blood-uvm');
  await page.locator('#open-ph-chart').click();
  await page.waitForFunction(()=>document.getElementById('open-ph-chart').textContent==='Retry pH reference chart'&&!document.getElementById('open-ph-chart').disabled);
  assert.match(await page.locator('#ph-chart-status').innerText(),/textual physiology references remain available/);
  assert.ok(injected>0);assert.equal(await page.locator('#ph-reference-dialog').evaluate(el=>el.open),false);assert.equal(await page.locator('#ph-chart-host canvas').count(),0);
  assert.match(await page.locator('#physiology-detail').innerText(),/7\.35\u20137\.45/);
  await page.unroute(pattern);await open(page);assert.deepEqual((await chartInfo(page)).datasets,[]);
});
