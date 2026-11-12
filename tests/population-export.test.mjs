import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {validatePopulationEnvelope,serializePopulationJson,populationCsv,referenceView} from '../public/modules/population-view.mjs';
import {savePopulationExport} from '../public/modules/population-save.mjs';

const fixtures=JSON.parse(await readFile(new URL('./fixtures/population-analytical.json',import.meta.url),'utf8'));
const physiology=JSON.parse(await readFile(new URL('../nivram/data/physiology.json',import.meta.url),'utf8'));
const accepted=name=>validatePopulationEnvelope(fixtures[name].envelope,fixtures[name].binding);
const source={name:'=analytical-model.json',modelInputHash:'c'.repeat(64)};
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');

test('full JSON preserves every state and sample despite hidden curves and an exact selected index',()=>{
  const envelope=accepted('complete'),visibleStateIds=['A'];
  const context=physiology.presets.find(p=>p.ph?.lower===7.35&&p.ph?.upper===7.45);
  assert.ok(context);const reference=referenceView(physiology,context.id);
  const result=JSON.parse(serializePopulationJson(envelope,{source,index:80,visibleStateIds,reference}));
  assert.deepEqual(result.populationResults,envelope);assert.equal(result.populationResults.samples.length,141);
  assert.deepEqual(result.visibleStateIds,['A']);assert.deepEqual(result.selectedSample,{index:80,ph:8});
  assert.deepEqual(result.referenceContext,reference);assert.deepEqual(result.source,source);
  assert.equal(result.populationResults.samples[70].result.fractions[0],0.5);
  visibleStateIds.push('HA');assert.deepEqual(result.visibleStateIds,['A']);
});

test('domain gaps and incomplete mass declarations survive JSON export without fabricated values',()=>{
  for(const name of ['narrow','incomplete']){
    const envelope=accepted(name),result=JSON.parse(serializePopulationJson(envelope,{source,visibleStateIds:[]}));
    assert.deepEqual(result.populationResults,envelope);
    assert.deepEqual(result.visibleStateIds,[]);assert.equal(result.referenceContext,null);
  }
});

test('typed CSV keeps scalar precision, nulls and spreadsheet-escaped model text with recovery flags',()=>{
  const csv=populationCsv(accepted('narrow'),{source,index:74,visibleStateIds:[]});
  assert.ok(csv.startsWith('"path","type","value","spreadsheet_escaped"\r\n'));
  assert.ok(csv.includes('"/source/name","string","\'=analytical-model.json","true"'));
  assert.ok(csv.includes('"/visibleStateIds","array","[]","false"'));
  assert.ok(csv.includes('"/populationResults/samples/0/result","null","null","false"'));
  assert.ok(csv.includes('"/selectedSample/ph","number","7.4","false"'));
  assert.ok(csv.includes('"/populationResults/samples/140/index","number","140","false"'));
});

test('unvalidated envelopes, unknown state IDs and unvalidated references cannot be exported',()=>{
  assert.throws(()=>serializePopulationJson(fixtures.complete.envelope,{source}),/validated/);
  assert.throws(()=>populationCsv(accepted('complete'),{source,visibleStateIds:['missing']}),/Visible IDs/);
  assert.throws(()=>serializePopulationJson(accepted('complete'),{source,reference:{}}),/validated reference/);
  assert.throws(()=>serializePopulationJson(accepted('complete'),{source:{...source,modelInputHash:'bad'}}),/digest/);
});

test('native save verifies exact bytes and has no browser fallback for uncertainty or cancellation',async()=>{
  const text=serializePopulationJson(accepted('complete'),{source}),blob=new Blob([text],{type:'application/json'}),calls=[];
  const native={async save_population_export(...args){calls.push(args);return {status:'saved',bytes:blob.size,sha256:hash(text)};}};
  assert.deepEqual(await savePopulationExport({kind:'json',blob,native,token:'local-token',download(){assert.fail('Unexpected browser fallback');}}),{status:'saved'});
  assert.deepEqual(calls,[['json',text,'local-token']]);
  for(const response of [{status:'saved',bytes:blob.size,sha256:'0'.repeat(64)},{status:'cancelled'},null]){
    const outcome=await savePopulationExport({kind:'json',blob,token:'local-token',native:{async save_population_export(){return response;}},download(){assert.fail('Unexpected fallback');}});
    assert.equal(outcome.status,response?.status==='cancelled'?'cancelled':'unconfirmed');
  }
});

test('browser save dispatches within the initiating call and cancelled preparation cannot reach native saving',async()=>{
  const blob=new Blob(['{}'],{type:'application/json'});let dispatched=false;
  const pending=savePopulationExport({kind:'json',blob,download(value,name){assert.equal(value,blob);assert.equal(name,'nivram-supplied-populations.json');dispatched=true;}});
  assert.equal(dispatched,true);assert.deepEqual(await pending,{status:'prepared'});
  let release;const gate=new Promise(resolve=>{release=resolve;}),controller=new AbortController();
  blob.text=async()=>{await gate;return '{}';};
  const saving=savePopulationExport({kind:'json',blob,token:'local-token',signal:controller.signal,native:{save_population_export(){assert.fail('Cancelled payload dispatched');}}});
  controller.abort();release();await assert.rejects(saving,error=>error.name==='AbortError');
});
