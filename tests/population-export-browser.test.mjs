import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {productionEditor,state} from './editor-browser-fixture.mjs';

const fixtures=JSON.parse(await readFile(new URL('./fixtures/population-analytical.json',import.meta.url),'utf8'));
async function bytes(download){const parts=[];for await(const chunk of await download.createReadStream())parts.push(chunk);return Buffer.concat(parts);}
test('the production population view downloads JSON, typed CSV and PNG with unchanged drawing and native-compatible bytes',async t=>{
  const page=await productionEditor(t),before=await state(page);
  await page.locator('#open-population-model').click();
  await page.locator('#population-model-file').setInputFiles({name:'analytical.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(fixtures.complete.envelope.model))});
  await page.waitForFunction(()=>document.querySelector('#population-model-status').textContent.startsWith('Supplied-model calculation ready.'));
  await page.locator('#population-model-chart [data-population-visible]').first().uncheck();
  const artifacts={};
  for(const kind of ['json','csv','png']){
    const waiting=page.waitForEvent('download');
    await page.locator(`#population-export-${kind}`).click();
    const download=await waiting;assert.equal(download.suggestedFilename(),`nivram-supplied-populations.${kind}`);
    artifacts[kind]=await bytes(download);
    assert.ok(artifacts[kind].length>0);
    const validation=spawnSync(process.env.NIVRAM_PYTHON,['-B','-c',
      "import sys,json;from nivram.population_export import validate_payload;kind=sys.argv[1];raw=sys.stdin.buffer.read().decode('utf-8');result=validate_payload(kind,raw);print(json.dumps({'bytes':len(result)}))",kind],
      {cwd:new URL('../',import.meta.url),encoding:'utf8',input:kind==='png'?artifacts[kind].toString('base64'):artifacts[kind].toString('utf8'),windowsHide:true,timeout:15000});
    assert.ifError(validation.error);assert.equal(validation.status,0,validation.stderr);
    assert.equal(JSON.parse(validation.stdout).bytes,artifacts[kind].length);
    assert.match(await page.locator('#population-export-status').textContent(),/prepared/);
  }
  const document=JSON.parse(artifacts.json.toString('utf8'));
  assert.equal(document.populationResults.samples.length,141);assert.deepEqual(document.visibleStateIds,['A']);
  assert.deepEqual(document.populationResults.samples[70].result.fractions,[0.5,0.5]);
  assert.ok(artifacts.csv.toString('utf8').includes('"/populationResults/samples/140/index","number","140","false"'));
  assert.equal(artifacts.png.readUInt32BE(16),1000);assert.equal(artifacts.png.readUInt32BE(20),720);
  assert.deepEqual((await state(page)).document,before.document);assert.equal((await state(page)).revision,before.revision);
  await page.locator('#close-population-model').click();
  for(const kind of ['json','csv','png'])assert.equal(await page.locator(`#population-export-${kind}`).isDisabled(),true);
});
