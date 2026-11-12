import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {testBrowser} from './browser-session.mjs';
import {stateStyle} from '../public/modules/population-view.mjs';

// Synthetic analytical envelopes exercise presentation arithmetic only. These
// tests do not establish molecular predictions, input chemistry or native UI.
const fixtures = JSON.parse(await readFile(new URL('./fixtures/population-analytical.json', import.meta.url), 'utf8'));
const physiology = JSON.parse(await readFile(new URL('../nivram/data/physiology.json', import.meta.url), 'utf8'));
const chartBytes = await readFile(new URL('../public/vendor/chart.umd.min.js', import.meta.url));
const chartSha256 = '48444a82d4edcb5bec0f1965faacdde18d9c17db3063d042abada2f705c9f54a';
const html = `<!doctype html><html><head><meta charset="utf-8"><link rel="icon" href="data:,">
<title>Synthetic supplied-population chart fixture</title><style>body{font:16px sans-serif;margin:16px}#main,#peer{width:1000px;max-width:100%}</style>
</head><body><div id="main"></div><div id="peer"></div><script src="/vendor/chart.umd.min.js"></script><script type="module" src="/fixture.mjs"></script></body></html>`;
const harness = `
import {mountPopulationChart} from '/modules/supplied-population-chart.mjs';
import {mountReferenceChart} from '/modules/population-chart.mjs';
import {createPhSelection,validatePopulationEnvelope,validateCapabilities} from '/modules/population-view.mjs';
const {fixtures,dataset}=await (await fetch('/fixture.json')).json();
const selection=createPhSelection();
const envelopes=Object.fromEntries(['complete','incomplete','narrow'].map(name=>[name,validatePopulationEnvelope(fixtures[name].envelope,fixtures[name].binding)]));
const missing=structuredClone(fixtures.complete.envelope);
missing.samples[70]={index:70,ph:7,status:'unavailable',result:null,reason:'Synthetic explicit missing sample'};
envelopes.gap=validatePopulationEnvelope(missing,fixtures.complete.binding);
const many=structuredClone(fixtures.complete.envelope);
many.modelSignature='c'.repeat(64);
many.model.states=Array.from({length:16},(_,index)=>({...many.model.states[1],state_id:'state-'+index,tautomer_id:'t'+index,structure_id:'synthetic-'+index}));
many.model.reference_state_id='state-0';many.model.supported_classes=['tautomer'];
for(const row of many.samples){row.result.model_signature=many.modelSignature;row.result.state_ids=many.model.states.map(state=>state.state_id);row.result.fractions=Array(16).fill(1/16);row.result.percentages=Array(16).fill(6.25);row.result.log_fractions=Array(16).fill(Math.log(1/16));}
envelopes.many=validatePopulationEnvelope(many,{...fixtures.complete.binding,modelSignature:many.modelSignature});
const host=document.querySelector('#main');
const stateSelections=[];
let active=mountPopulationChart(host,{Chart,envelope:envelopes[new URL(location.href).searchParams.get('case')??'complete'],selection,dataset,onStateSelect:stateId=>stateSelections.push(stateId)});
const peer=mountReferenceChart(document.querySelector('#peer'),{Chart,dataset,capabilities:validateCapabilities(fixtures.capabilities),selection});
window.fixture={selection,peer,stateSelections,get active(){return active},
  mount(name,noReference=false){active.destroy();active=mountPopulationChart(host,{Chart,envelope:envelopes[name],selection,dataset:noReference?null:dataset,onStateSelect:stateId=>stateSelections.push(stateId)});},
  sameEnvelope(name){return active.getSnapshot().envelope===envelopes[name]},
  destroy(){active.destroy();peer.destroy();}};
window.fixtureReady=true;
`;

async function fixture(t, name = 'complete') {
  assert.equal(createHash('sha256').update(chartBytes).digest('hex'), chartSha256, 'Use the pinned local Chart.js asset.');
  const routes = new Map([
    ['/', ['text/html', html]], ['/fixture.mjs', ['text/javascript', harness]],
    ['/fixture.json', ['application/json', JSON.stringify({fixtures, dataset: physiology})]],
    ['/vendor/chart.umd.min.js', ['text/javascript', chartBytes]],
  ]);
  for (const name of ['supplied-population-chart', 'population-view', 'population-chart'])
    routes.set(`/modules/${name}.mjs`, ['text/javascript', await readFile(new URL(`../public/modules/${name}.mjs`, import.meta.url))]);
  routes.set('/modules/png-metadata.mjs',['text/javascript',await readFile(new URL('../public/modules/png-metadata.mjs',import.meta.url))]);
  routes.set('/api/status',['application/json',JSON.stringify({version:'0.0.0-fixture'})]);
  const server = createServer((request, response) => {
    const entry = routes.get(new URL(request.url, 'http://localhost').pathname);
    response.writeHead(entry ? 200 : 404, {'Content-Type': entry?.[0] ?? 'text/plain', 'Cache-Control': 'no-store'});
    response.end(entry?.[1] ?? 'Not found');
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(() => new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve())));
  const browser = await testBrowser(t), page = await browser.newPage({viewport: {width: 1280, height: 1000}});
  page.setDefaultTimeout(10000);
  const errors = [], external = [], requests = [], origin = `http://127.0.0.1:${server.address().port}`;
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => requests.push(request.url()));
  await page.route('**/*', route => {
    const url = route.request().url();
    if (url.startsWith(origin + '/') || /^(data|blob):/.test(url)) return route.continue();
    external.push(url); return route.abort();
  });
  const response = await page.goto(`${origin}/?case=${name}`); await page.waitForFunction(() => window.fixtureReady);
  const receivedDocument = await response.text();
  t.after(() => {
    assert.deepEqual(errors, []);
    // AdGuard can insert its scripts into loopback HTML before it reaches the
    // browser. Verify that provenance from this response, keep the requests
    // blocked, and never mistake the fixture for an operating-system privacy test.
    const adguard = external.filter(url => new URL(url).hostname === 'injections.adguard.org');
    if (adguard.length) {
      assert.equal(html.includes('injections.adguard.org'), false);
      assert.ok(receivedDocument.includes('injections.adguard.org'), 'AdGuard requests must be explained by independently observed response injection.');
      t.diagnostic(`${adguard.length} AdGuard requests from injected response markup were blocked by the fixture; this is not a system-wide network test.`);
    }
    assert.deepEqual(external.filter(url => new URL(url).hostname !== 'injections.adguard.org'), [], 'Unexpected external requests are not permitted.');
  });
  return {page, requests};
}

const main = page => page.locator('#main');
const canvas = page => main(page).locator('canvas[data-population-canvas]');
const input = page => main(page).locator('input[data-population-ph]');
async function snapshot(page) {
  return page.evaluate(() => {
    const {reference, selection, table, visibleStateIds, theme, disposed, envelope} = window.fixture.active.getSnapshot();
    return {reference, selection, table, visibleStateIds, theme, disposed, modelSignature: envelope.modelSignature};
  });
}
async function chartInfo(page) {
  return canvas(page).evaluate(element => {
    const chart = window.Chart.getChart(element);
    return {version: Chart.version, id: chart.id, min: chart.scales.x.min, max: chart.scales.x.max,
      yMax: chart.scales.y.max, title: chart.options.scales.y.title.text,
      background: [...element.getContext('2d').getImageData(0, 0, 1, 1).data],
      datasets: chart.data.datasets.map((value, index) => ({label: value.label, data: structuredClone(value.data),
        color: value.borderColor, dash: Array.from(value.borderDash ?? []), spanGaps: value.spanGaps,
        pointRadius: Array.isArray(value.pointRadius) ? Array.from(value.pointRadius) : value.pointRadius,
        visible: chart.isDatasetVisible(index)}))};
  });
}
async function enter(page, value) {
  await input(page).click(); await page.keyboard.press('Control+A'); await page.keyboard.type(value);
  await main(page).locator('form[data-population-form] button[type=submit]').click();
}
async function selected(page, ph) {
  await page.waitForFunction(value => document.querySelector('#main canvas[data-population-canvas]')?.getAttribute('aria-valuenow') === String(value), ph);
  assert.equal(await input(page).inputValue(), ph.toFixed(1));
  assert.equal(await page.locator('#peer canvas').getAttribute('aria-valuenow'), String(ph), 'The reference chart uses the same selected sample.');
  assert.deepEqual((await snapshot(page)).selection, {index: Math.round(ph * 10), ph});
}
async function pointer(page, ph) {
  const point = await canvas(page).evaluate((element, value) => {
    const chart = Chart.getChart(element), rect = element.getBoundingClientRect();
    return {x: rect.left + chart.scales.x.getPixelForValue(value) * rect.width / chart.width,
      y: rect.top + (chart.chartArea.top + chart.chartArea.bottom) / 2 * rect.height / chart.height};
  }, ph);
  await page.mouse.move(point.x, point.y);
}
const closeTo = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-12, `${actual} must equal ${expected} within arithmetic tolerance.`);

test('state inspection uses independent accessible selection without changing plot data, visibility or pH', {timeout:60000}, async t=>{
  const {page,requests}=await fixture(t);
  const inspected=()=>page.evaluate(()=>({id:window.fixture.active.getSnapshot().selectedStateId,calls:[...window.fixture.stateSelections]}));
  assert.deepEqual(await inspected(),{id:fixtures.complete.envelope.model.reference_state_id,calls:[]});
  assert.equal(await main(page).getByRole('columnheader',{name:'Inspect',exact:true}).count(),1);
  assert.equal(await main(page).locator('[data-population-inspect]:checked').count(),1);
  await main(page).locator('[data-population-visible="A"]').uncheck();
  const before=await snapshot(page),beforeChart=await chartInfo(page),beforeRequests=requests.length;
  await main(page).getByRole('radio',{name:'Inspect HA state',exact:true}).focus();
  await page.keyboard.press('ArrowDown');
  assert.deepEqual(await inspected(),{id:'A',calls:['A']});
  assert.equal(await main(page).getByRole('radio',{name:'Inspect A state',exact:true}).isChecked(),true);
  await main(page).getByRole('radio',{name:'Inspect A state',exact:true}).click();
  assert.deepEqual(await inspected(),{id:'A',calls:['A']},'Selecting the already-inspected state must not emit another callback.');
  await page.evaluate(()=>{window.fixture.active.selectState('HA');window.fixture.active.selectState('HA');});
  assert.deepEqual(await inspected(),{id:'HA',calls:['A','HA']});
  assert.equal(await main(page).getByRole('radio',{name:'Inspect HA state',exact:true}).isChecked(),true);
  assert.equal(await main(page).locator('[data-population-inspect]:checked').count(),1);
  await assert.rejects(page.evaluate(()=>window.fixture.active.selectState('not-a-supplied-state')),/state from the supplied model/);
  await assert.rejects(page.evaluate(()=>window.fixture.active.selectState(null)),/state from the supplied model/);
  assert.deepEqual(await inspected(),{id:'HA',calls:['A','HA']});
  assert.deepEqual(await snapshot(page),before,'Inspecting a hidden state must not change visibility, percentages or the shared pH selection.');
  assert.deepEqual(await chartInfo(page),beforeChart,'Inspection must leave the actual curve datasets and rendered chart unchanged.');
  assert.equal(requests.length,beforeRequests,'Inspection does not request chemistry or population calculations.');
  await page.evaluate(()=>window.fixture.destroy());
  await assert.rejects(page.evaluate(()=>window.fixture.active.selectState('A')),/disposed/);
  assert.deepEqual(await inspected(),{id:'HA',calls:['A','HA']});
});

test('supplied chart uses unchanged full-ensemble fractions, stable styles, shared pH controls and continuous references', {timeout: 60000}, async t => {
  const {page, requests} = await fixture(t), initial = await snapshot(page), chart = await chartInfo(page);
  assert.equal(chart.version, '4.5.1'); assert.deepEqual([chart.min, chart.max], [0, 14]);
  assert.equal(chart.yMax, 100); assert.match(chart.title, /%|percent/i);
  assert.equal(chart.datasets.length, 2); assert.equal(await page.evaluate(() => window.fixture.sameEnvelope('complete')), true);
  assert.deepEqual(initial.table.rows.map(row => row.fraction), [.5, .5]);
  assert.deepEqual(initial.reference.overlays, []); await selected(page, 7);
  for (const [index, id] of ['HA', 'A'].entries()) {
    const curve = chart.datasets[index], expected = stateStyle(id);
    assert.equal(curve.data.length, 141); assert.equal(curve.spanGaps, false);
    assert.equal(curve.color, expected.color); assert.deepEqual(curve.dash, expected.dash);
    assert.equal(curve.data[70].x, 7); assert.equal(curve.data[70].y, 50);
    assert.match(await main(page).locator(`tr[data-state-id="${id}"]`).innerText(), /50(?:\.0+)?\s*%/);
  }
  const beforeInteraction = requests.length;
  await enter(page, '8'); await selected(page, 8);
  let value = await snapshot(page);
  closeTo(value.table.rows[0].fraction, 1 / 11); closeTo(value.table.rows[1].fraction, 10 / 11);
  closeTo((await chartInfo(page)).datasets[1].data[80].y, 1000 / 11);
  assert.match(await main(page).locator('tr[data-state-id="A"]').innerText(), /90\.9/);
  await main(page).locator('input[data-population-visible="A"]').uncheck();
  value = await snapshot(page);
  assert.deepEqual(value.table.rows.map(row => row.fraction), fixtures.complete.envelope.samples[80].result.fractions);
  closeTo(value.table.plottedMass, 1 / 11); closeTo(value.table.hiddenRepresentedMass, 10 / 11);
  closeTo(value.table.representedMass, 1); closeTo(value.table.fullEnsembleMass, 1);
  assert.deepEqual(value.visibleStateIds, ['HA']);
  const hidden = await chartInfo(page);
  assert.equal(hidden.datasets[1].visible, false);
  assert.deepEqual(hidden.datasets.map(row => row.data), chart.datasets.map(row => row.data), 'Hiding a curve must not rescale or replace its data.');
  assert.deepEqual(hidden.datasets.map(({color, dash}) => ({color, dash})), chart.datasets.map(({color, dash}) => ({color, dash})));
  assert.match(await main(page).locator('[data-population-mass]').innerText(), /hidden/i);
  await pointer(page, 7.36); await selected(page, 7.4);
  await canvas(page).click();
  for (const [key, ph] of [['Home', 0], ['End', 14], ['ArrowLeft', 13.9], ['ArrowRight', 14]]) {
    await page.keyboard.press(key); await selected(page, ph);
  }
  await enter(page, '7.35'); assert.match(await main(page).locator('[role=alert]').innerText(), /0\.1/);
  assert.equal(await canvas(page).getAttribute('aria-valuenow'), '14');
  await enter(page, '7.3000'); await selected(page, 7.3);
  await page.evaluate(() => window.fixture.active.updateContext({contextId: 'arterial-blood-uvm', comparison: null}));
  value = await snapshot(page);
  assert.deepEqual([value.reference.overlays[0].lower, value.reference.overlays[0].upper], [7.35, 7.45]);
  assert.match(await main(page).innerText(), /7\.35.*7\.45/s);
  assert.equal((await chartInfo(page)).datasets.length, 2, 'Reference intervals must not become molecular curves.');
  assert.deepEqual(value.selection, {index: 73, ph: 7.3});
  await page.evaluate(() => window.fixture.active.setTheme('dark'));
  assert.equal((await snapshot(page)).theme, 'dark');
  assert.notDeepEqual((await chartInfo(page)).background, chart.background);
  await page.evaluate(() => window.fixture.active.setTheme('light'));
  assert.deepEqual((await chartInfo(page)).background, chart.background);
  assert.match(await main(page).locator('[data-population-warnings]').innerText(), /Synthetic|not independently validated/i);
  const provenance = main(page).locator('details[data-population-provenance]');
  await provenance.locator('summary').click();
  assert.match(await provenance.innerText(), /synthetic-test/); assert.match(await provenance.innerText(), /298\.15/);
  assert.equal(requests.length, beforeInteraction, 'pH movement, visibility, context, theme and provenance must not request new data.');
  await page.evaluate(() => window.fixture.destroy());
  assert.equal(await main(page).locator('[data-population-chart]').count(), 0);
  assert.equal((await snapshot(page)).disposed, true);
  assert.equal(await page.evaluate(() => Object.keys(Chart.instances).length), 0);
  await page.evaluate(() => window.fixture.mount('complete'));
  assert.equal(await page.evaluate(() => Object.keys(Chart.instances).length), 1, 'Destroyed hosts can mount a fresh chart without a leaked instance.');
  await page.evaluate(() => window.fixture.destroy());
});

test('supplied chart preserves known omissions, conditional unknown mass and unjoined missing/domain samples', {timeout: 60000}, async t => {
  const {page, requests} = await fixture(t, 'incomplete');
  let value = await snapshot(page);
  assert.deepEqual(value.table.rows.map(row => row.fraction), [.4, .4]);
  assert.equal(value.table.omittedMass, .2); assert.equal(value.table.representedMass, .8); assert.equal(value.table.fullEnsembleMass, 1);
  await main(page).locator('input[data-population-visible="A"]').uncheck();
  value = await snapshot(page);
  assert.equal(value.table.plottedMass, .4); assert.equal(value.table.hiddenRepresentedMass, .4);
  assert.equal(value.table.omittedMass, .2); assert.equal(value.table.fullEnsembleMass, 1);
  const beforeInteraction = requests.length;
  await enter(page, '6.9'); await selected(page, 6.9); value = await snapshot(page);
  assert.equal(value.table.normalizationScope, 'conditional_on_represented_states');
  assert.equal(value.table.omittedMass, null); assert.equal(value.table.fullEnsembleMass, null);
  assert.match(await main(page).locator('[data-population-mass]').innerText(), /unknown/i);
  assert.match(await main(page).innerText(), /conditional/i);
  await page.evaluate(() => window.fixture.mount('narrow'));
  await enter(page, '7.3'); await selected(page, 7.3); value = await snapshot(page);
  assert.equal(value.table.status, 'outside_model_domain'); assert.equal(value.table.representedMass, null);
  assert.deepEqual(value.table.rows, []);
  assert.match(await main(page).locator('[data-population-readout]').innerText(), /outside|domain|unavailable/i);
  for (const curve of (await chartInfo(page)).datasets) {
    assert.equal(curve.data.length, 141); assert.equal(curve.spanGaps, false);
    assert.equal(curve.data.filter(point => point.y !== null).length, 1);
    assert.equal(curve.data[73].y, null); assert.equal(curve.data[75].y, null);
    assert.deepEqual([curve.data[74].x, curve.data[74].y], [7.4, 50]);
    assert.ok((Array.isArray(curve.pointRadius) ? curve.pointRadius[74] : curve.pointRadius) > 0, 'An isolated valid sample must remain visibly plotted between null gaps.');
  }
  await enter(page, '7.4'); await selected(page, 7.4);
  assert.deepEqual((await snapshot(page)).table.rows.map(row => row.fraction), [.5, .5]);
  await page.evaluate(() => window.fixture.mount('gap'));
  await enter(page, '7'); await selected(page, 7); value = await snapshot(page);
  assert.equal(value.table.status, 'unavailable'); assert.equal(value.table.reason, 'Synthetic explicit missing sample');
  for (const curve of (await chartInfo(page)).datasets) {
    assert.equal(curve.spanGaps, false); assert.equal(curve.data[70].y, null);
    assert.equal(typeof curve.data[69].y, 'number'); assert.equal(typeof curve.data[71].y, 'number');
  }
  assert.match(await main(page).locator('[data-population-readout]').innerText(), /missing|unavailable/i);
  await page.evaluate(() => window.fixture.mount('complete', true));
  value = await snapshot(page);
  assert.deepEqual(value.table.rows.map(row => row.fraction), [.5, .5]);
  assert.deepEqual(value.reference.overlays, []); assert.equal(value.reference.context, null);
  assert.match(value.reference.gap.label, /reference data is unavailable/i);
  assert.match(await main(page).innerText(), /reference data is unavailable/i);
  const withoutReference = await chartInfo(page);
  assert.equal(withoutReference.datasets.length, 2);
  await assert.rejects(page.evaluate(() => window.fixture.active.updateContext({contextId: 'arterial-blood-uvm'})), /reference data is unavailable/i);
  assert.equal((await chartInfo(page)).id, withoutReference.id, 'A rejected reference request must preserve the existing population chart.');
  assert.deepEqual((await snapshot(page)).table.rows.map(row => row.fraction), [.5, .5]);
  assert.equal(requests.length, beforeInteraction, 'Changing samples and remounting supplied envelopes must remain local.');
  await page.evaluate(() => window.fixture.destroy());
  assert.equal(await page.evaluate(() => Object.keys(Chart.instances).length), 0);
});

test('population PNG encodes opaque fixed-size theme-independent plots with unchanged samples, visibility, labels and exact references', {timeout:60000}, async t=>{
  const {page,requests}=await fixture(t);
  await enter(page,'7.3');
  await main(page).locator('input[data-population-visible="A"]').uncheck();
  await page.evaluate(()=>window.fixture.active.updateContext({contextId:'arterial-blood-uvm'}));
  const before=await snapshot(page),initialChart=await chartInfo(page),beforeRequests=requests.length;
  const receipt=await page.evaluate(async()=>{
    const originalToBlob=HTMLCanvasElement.prototype.toBlob,originalText=CanvasRenderingContext2D.prototype.fillText,originalRect=CanvasRenderingContext2D.prototype.strokeRect;
    const texts=new WeakMap(),rects=new WeakMap(),captures=[];
    CanvasRenderingContext2D.prototype.fillText=function(text,...args){if(!this.canvas.isConnected){const lines=texts.get(this.canvas)??[];lines.push(text);texts.set(this.canvas,lines);}return originalText.call(this,text,...args);};
    CanvasRenderingContext2D.prototype.strokeRect=function(...args){if(!this.canvas.isConnected){const rows=rects.get(this.canvas)??[];rows.push(args);rects.set(this.canvas,rows);}return originalRect.apply(this,args);};
    HTMLCanvasElement.prototype.toBlob=function(...args){
      const chart=Chart.getChart(this);
      captures.push({texts:texts.get(this)??[],rects:rects.get(this)??[],min:chart.scales.x.min,max:chart.scales.x.max,yMin:chart.scales.y.min,yMax:chart.scales.y.max,
        expectedBand:[chart.scales.x.getPixelForValue(7.35),chart.scales.x.getPixelForValue(7.45)-chart.scales.x.getPixelForValue(7.35)],
        curves:chart.data.datasets.map((state,index)=>({label:state.label,visible:chart.isDatasetVisible(index),data:structuredClone(state.data),spanGaps:state.spanGaps,color:state.borderColor,dash:[...state.borderDash]}))});
      return originalToBlob.apply(this,args);
    };
    try{
      const first=await window.fixture.active.exportPng();
      window.fixture.active.setTheme('dark');const darkState=JSON.stringify(window.fixture.active.getSnapshot());
      const second=await window.fixture.active.exportPng();
      const sameState=JSON.stringify(window.fixture.active.getSnapshot())===darkState;
      const digest=async blob=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await blob.arrayBuffer())),byte=>byte.toString(16).padStart(2,'0')).join('');
      const bitmap=await createImageBitmap(second),image=document.createElement('canvas');image.width=bitmap.width;image.height=bitmap.height;const ctx=image.getContext('2d');ctx.drawImage(bitmap,0,0);bitmap.close();
      const pixels=ctx.getImageData(0,0,image.width,image.height).data;let allOpaque=true;for(let index=3;index<pixels.length;index+=4)if(pixels[index]!==255){allOpaque=false;break;}
      const result={width:image.width,height:image.height,type:second.type,size:second.size,pixel:[...pixels.slice(0,4)],allOpaque,hashes:[await digest(first),await digest(second)],sameState,captures,instances:Object.keys(Chart.instances).length};
      window.fixture.active.setTheme('light');return result;
    }finally{HTMLCanvasElement.prototype.toBlob=originalToBlob;CanvasRenderingContext2D.prototype.fillText=originalText;CanvasRenderingContext2D.prototype.strokeRect=originalRect;}
  });
  assert.deepEqual([receipt.width,receipt.height],[1000,720]);assert.equal(receipt.type,'image/png');assert.ok(receipt.size>0&&receipt.size<=8388608);
  assert.deepEqual(receipt.pixel,[255,255,255,255]);assert.equal(receipt.allOpaque,true);assert.equal(receipt.hashes[0],receipt.hashes[1]);assert.equal(receipt.sameState,true);assert.equal(receipt.instances,2);
  assert.deepEqual(await snapshot(page),before);assert.deepEqual(await chartInfo(page),initialChart);
  for(const captured of receipt.captures){
    assert.deepEqual([captured.min,captured.max,captured.yMin,captured.yMax],[0,14,0,100]);
    assert.deepEqual(captured.curves.map(curve=>curve.data),initialChart.datasets.map(curve=>curve.data));
    assert.deepEqual(captured.curves.map(curve=>curve.visible),[true,false]);assert.ok(captured.curves.every(curve=>curve.spanGaps===false));
    const text=captured.texts.join('\n');assert.match(text,/Synthetic analytical population example/);assert.match(text,/Supplied-model calculation; not a validated molecular prediction\./);
    assert.match(text,/Selected pH 7\.3/);assert.match(text,/synthetic-not-a-drawing/);assert.ok(text.includes(before.modelSignature));assert.match(text,/298\.15 K/);
    assert.match(text,/7\.35[-\u2013]7\.45/);assert.match(text,/Curves shown: 1 of 2; hidden: 1\. Legend: 1 of 1 visible states/);
    assert.ok(captured.rects.some(([left,,width])=>Math.abs(left-captured.expectedBand[0])<1e-10&&Math.abs(width-captured.expectedBand[1])<1e-10),'The continuous interval is painted at its original unsnapped coordinates.');
  }
  assert.equal(requests.length,beforeRequests,'PNG export is local and needs no new data.');
  await page.evaluate(()=>window.fixture.destroy());
});

test('population PNG snapshots survive live changes, retain gaps and all curves beyond the bounded legend', {timeout:60000}, async t=>{
  const {page}=await fixture(t);
  const receipt=await page.evaluate(async()=>{
    window.fixture.mount('gap');window.fixture.selection.enterPh('7');
    const before=await window.fixture.active.exportPng();
    const digest=async blob=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await blob.arrayBuffer())),byte=>byte.toString(16).padStart(2,'0')).join('');
    const original=HTMLCanvasElement.prototype.toBlob;let pendingCanvas,pendingCallback;
    HTMLCanvasElement.prototype.toBlob=function(callback){pendingCanvas=this;pendingCallback=callback;};
    try{
      const exporting=window.fixture.active.exportPng(),capturedChart=Chart.getChart(pendingCanvas),captured=structuredClone(capturedChart.data.datasets.map(value=>value.data));
      const concurrent=await window.fixture.active.exportPng().then(()=>null,error=>error.message);
      window.fixture.selection.enterPh('8');window.fixture.active.setTheme('dark');window.fixture.active.updateContext({contextId:'arterial-blood-uvm'});
      capturedChart.draw();
      const stillCaptured=JSON.stringify(captured)===JSON.stringify(capturedChart.data.datasets.map(value=>value.data));
      original.call(pendingCanvas,pendingCallback,'image/png');const blob=await exporting;
      return {concurrent,stillCaptured,hashes:[await digest(before),await digest(blob)],curves:captured,ph:window.fixture.selection.getSnapshot().ph,theme:window.fixture.active.getSnapshot().theme,type:blob.type,instances:Object.keys(Chart.instances).length};
    }finally{HTMLCanvasElement.prototype.toBlob=original;}
  });
  assert.match(receipt.concurrent,/already in progress/);assert.equal(receipt.stillCaptured,true);assert.equal(receipt.type,'image/png');assert.equal(receipt.ph,8);assert.equal(receipt.theme,'dark');assert.equal(receipt.instances,2);
  assert.equal(receipt.hashes[0],receipt.hashes[1],'Even a redraw during encoding uses the captured selection and reference, not subsequent live settings.');
  for(const curve of receipt.curves){assert.equal(curve.length,141);assert.equal(curve[70].y,null);assert.equal(typeof curve[69].y,'number');assert.equal(typeof curve[71].y,'number');}
  const many=await page.evaluate(async()=>{
    window.fixture.mount('many');const originalToBlob=HTMLCanvasElement.prototype.toBlob,originalText=CanvasRenderingContext2D.prototype.fillText,lines=[];let captured;
    CanvasRenderingContext2D.prototype.fillText=function(text,...args){if(!this.canvas.isConnected)lines.push(text);return originalText.call(this,text,...args);};
    HTMLCanvasElement.prototype.toBlob=function(...args){const chart=Chart.getChart(this);captured=chart.data.datasets.map((state,index)=>({visible:chart.isDatasetVisible(index),count:state.data.length}));return originalToBlob.apply(this,args);};
    try{await window.fixture.active.exportPng();return {lines,curves:captured};}finally{HTMLCanvasElement.prototype.toBlob=originalToBlob;CanvasRenderingContext2D.prototype.fillText=originalText;}
  });
  assert.equal(many.curves.length,16);assert.ok(many.curves.every(curve=>curve.visible&&curve.count===141));assert.match(many.lines.join('\n'),/Curves shown: 16 of 16; hidden: 0\. Legend: 12 of 16 visible states/);assert.match(many.lines.join('\n'),/Export JSON for every state/);
  await page.evaluate(()=>window.fixture.destroy());
});

test('population PNG cancellation and encoder failures reject and release only their own canvases', {timeout:60000}, async t=>{
  const {page}=await fixture(t);
  const receipt=await page.evaluate(async()=>{
    const original=HTMLCanvasElement.prototype.toBlob;let callback;
    try{
      HTMLCanvasElement.prototype.toBlob=function(done){callback=done;};
      const pending=window.fixture.active.exportPng().then(()=>({success:true}),error=>({name:error.name,message:error.message}));
      window.fixture.active.destroy();const cancelled=await pending;callback(new Blob(['late'],{type:'image/png'}));
      const disposed=await window.fixture.active.exportPng().then(()=>null,error=>error.message),afterDispose=Object.keys(Chart.instances).length;
      window.fixture.mount('complete');
      HTMLCanvasElement.prototype.toBlob=function(done){done(null);};const failed=await window.fixture.active.exportPng().then(()=>null,error=>error.message);
      HTMLCanvasElement.prototype.toBlob=function(done){done(new Blob([new Uint8Array(8388609)],{type:'image/png'}));};const oversized=await window.fixture.active.exportPng().then(()=>null,error=>error.message);
      HTMLCanvasElement.prototype.toBlob=original;const valid=await window.fixture.active.exportPng();
      return {cancelled,disposed,afterDispose,failed,oversized,valid:valid.type,instances:Object.keys(Chart.instances).length};
    }finally{HTMLCanvasElement.prototype.toBlob=original;}
  });
  assert.equal(receipt.cancelled.name,'AbortError');assert.match(receipt.cancelled.message,/disposed during export/);assert.match(receipt.disposed,/disposed/);assert.equal(receipt.afterDispose,1);
  assert.match(receipt.failed,/encoding failed/);assert.match(receipt.oversized,/exceeds its bound/);assert.equal(receipt.valid,'image/png');assert.equal(receipt.instances,2);
  await page.evaluate(()=>window.fixture.destroy());
});
