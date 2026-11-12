/** Real evaluator bytes, production controllers/serializers and native validation.
 * No browser, chemistry installation, native GUI or source edits are required.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {createDocumentStore} from '../public/modules/document.mjs';
import {createPopulationAnalysis} from '../public/modules/population-analysis.mjs';
import {serializePopulationJson,populationCsv,populationTable,validatePopulationEnvelope} from '../public/modules/population-view.mjs';
import {readPopulationJson,readPopulationJsonAsync,sameModelNumbers} from '../public/modules/population-model-json.mjs';
const fixture=JSON.parse(await readFile(new URL('./fixtures/population-analytical.json',import.meta.url),'utf8')).complete;
const base=JSON.stringify(fixture.envelope.model);
function numbers(text,field,values){let index=0;const out=text.replace(new RegExp('("'+field+'":)-?(?:0|[1-9]\\d*)(?:\\.\\d+)?(?:[eE][+-]?\\d+)?','g'),(all,key)=>index<values.length?key+values[index++]:all);assert.equal(index,values.length);return out;}
const energy=(...values)=>numbers(base,'free_energy_at_reference',values);
const field=(name,value)=>numbers(base,name,[value]);
function python(mode,value){const child=spawnSync(process.env.PYTHON??'python',[new URL('./population-exact-model-helper.py',import.meta.url).pathname,mode],{input:JSON.stringify(value),encoding:'utf8',maxBuffer:40*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});assert.equal(child.status,0,child.stderr);return child.stdout;}
function harness(transform=wire=>wire){
  const store=createDocumentStore({schema:'nivram/1',graph:{atoms:[],bonds:[]}}),before=store.getSnapshot(),history=store.historyState(),calls=[];
  const analysis=createPopulationAnalysis({editor:store,fetcher:async(url,options)=>{
    const request=JSON.parse(options.body);calls.push(url);if(url.endsWith('/cancel'))return new Response('{}');
    const wire=python('evaluate',request);return new Response(transform(wire),{headers:{'content-type':'application/json'}});
  }});
  return{analysis,calls,store,before,history};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const cases=[
  ['small integers',energy('0','1')],
  ['adjacent large integers',energy('9007199254740992','9007199254740993')],
  ['negative large integers',energy('-9007199254740993','-9007199254740992')],
  ['large integer temperature',field('temperature_kelvin','9007199254740993')],
  ['large integer ionic strength',base.replace('"ionic_strength_mol_l":null','"ionic_strength_mol_l":9007199254740993')],
  ['float equivalent spellings',energy('0.00','1e0')],
  ['float negative zero and integer negative zero',energy('-0.0','-0')],
  ['mixed integer and float',energy('1','1.0')],
  ['subnormal and underflow floats',energy('5e-324','1e-999')],
  ['long finite integer',energy('1'+'0'.repeat(307),'1'+'0'.repeat(307))],
  ['long repeated float spelling',base.replace('"ionic_strength_mol_l":null','"ionic_strength_mol_l":1e-'+'0'.repeat(150000)+'999')],
  ['escaped data and pointer-like text',JSON.stringify({...fixture.envelope.model,provenance:{...fixture.envelope.model.provenance,source:'= /populationResults/model/states/0/free_energy_at_reference ~ " 9007199254740993 α 😀'}})],
  ['unknown mass and narrow domain',field('ph_min','7.35').replace('"reference_ph":7','"reference_ph":7.4').replace('"ph_max":14','"ph_max":7.45').replace('"ensemble_complete":true','"ensemble_complete":false')],
];

test('real evaluator JSON/CSV retain exact source numeric values and Python types, including repeated conditions',async()=>{
  for(const [name,text] of cases){const f=harness();try{
    const result=await f.analysis.loadText(text,{name}),options={source:{name,modelInputHash:result.modelInputHash}};
    assert.equal(f.analysis.status,'ready',name);assert.equal(result.modelText,text);
    const json=serializePopulationJson(result.envelope,options),csv=populationCsv(result.envelope,options);
    assert.deepEqual(JSON.parse(python('validate',{modelText:text,modelInputHash:result.modelInputHash,json,csv})),{validated:['json','csv'],nativeStatus:'cancelled',exactModelTypes:true},name);
    if(name==='adjacent large integers'){
      assert.deepEqual(populationTable(result.envelope,70).rows.map(row=>row.fraction),[0.5995031807152958,0.4004968192847041]);
      assert.match(json,/"free_energy_at_reference":9007199254740993/);assert.match(csv,/"9007199254740993","false"/);
    }
    if(name==='long repeated float spelling'){assert.ok(json.length<500000);assert.ok(csv.length<1000000);}
    assert.deepEqual(f.store.getSnapshot(),f.before);assert.deepEqual(f.store.historyState(),f.history);
    assert.throws(()=>serializePopulationJson(result.envelope,{source:{name,modelInputHash:'0'.repeat(64)}}),/bound supplied model digest/);
    assert.throws(()=>populationCsv(result.envelope,{source:{name,modelInputHash:'0'.repeat(64)}}),/bound supplied model digest/);
  }finally{f.analysis.dispose();}}
});

test('exact numeric comparison preserves typed Python equivalence without BigInt conversion',()=>{
  const equal=(a,b)=>sameModelNumbers(readPopulationJson(a).model,readPopulationJson(b).model);
  for(const [a,b] of [['-0','0'],['1.0','1e0'],['-0.0','-0e99'],['1e-999','0.0'],['9007199254740993','9007199254740993']])assert.equal(equal(a,b),true);
  for(const [a,b] of [['1','1.0'],['-0.0','0.0'],['9007199254740992','9007199254740993'],['9007199254740993','9007199254740992.0']])assert.equal(equal(a,b),false);
});

test('raw model/condition corruption cannot publish or be hidden by lossless export substitution',async()=>{
  const ionic=cases.find(([name])=>name==='large integer ionic strength')[1];
  const changes=[
    [energy('9007199254740992','9007199254740993'),wire=>wire.replace('"free_energy_at_reference":9007199254740993','"free_energy_at_reference":9007199254740992')],
    [energy('0','1'),wire=>wire.replace('"free_energy_at_reference":1','"free_energy_at_reference":1.0')],
    [energy('-0.0','1'),wire=>wire.replace('"free_energy_at_reference":-0.0','"free_energy_at_reference":0.0')],
    [ionic,wire=>{const index=wire.indexOf('"samples":');return wire.slice(0,index)+wire.slice(index).replace('"ionic_strength_mol_l":9007199254740993','"ionic_strength_mol_l":9007199254740992');}],
    [ionic,wire=>{const index=wire.indexOf('"samples":');return wire.slice(0,index)+wire.slice(index).replace('"ionic_strength_mol_l":9007199254740993','"ionic_strength_mol_l":9007199254740993.0');}],
    [base,wire=>wire.replace('"temperature_kelvin":298.15','"temperature_kelvin":298.15,"temperature_\\u006belvin":298.15')],
    [base,wire=>wire+' {}'],
  ];
  for(const [text,change] of changes){const f=harness(change);try{
    await assert.rejects(f.analysis.loadText(text));await flush();assert.equal(f.analysis.status,'idle');assert.equal(f.analysis.result,null);
    assert.equal(f.calls.filter(url=>url.endsWith('/cancel')).length,1);assert.deepEqual(f.store.getSnapshot(),f.before);assert.deepEqual(f.store.historyState(),f.history);
  }finally{f.analysis.dispose();}}
});

test('strict source parsing rejects decoded duplicate keys and malformed syntax before evaluation',async()=>{
  const bad=[base.replace('"temperature_kelvin":298.15','"temperature_kelvin":298.15,"temperature_\\u006belvin":298.15'),base+' {}','{"x":1,}', '[1,]', '{"x":"\\x00"}', '['.repeat(22)+'0'+']'.repeat(22)];
  for(const text of bad){const f=harness();try{await assert.rejects(f.analysis.loadText(text));assert.equal(f.calls.length,0);assert.equal(f.analysis.result,null);}finally{f.analysis.dispose();}}
  const parsed=readPopulationJson('{"__proto__":{"x":1},"constructor":2,"slash/~":3}').value;
  assert.equal(Object.getPrototypeOf(parsed),null);assert.equal(parsed.__proto__.x,1);assert.equal({}.x,undefined);
});

test('generic envelope exports remain unbound to a source digest and keep their existing schema',()=>{
  const envelope=validatePopulationEnvelope(fixture.envelope,fixture.binding);
  for(const modelInputHash of ['a'.repeat(64),'b'.repeat(64)]){
    const document=JSON.parse(serializePopulationJson(envelope,{source:{name:'generic',modelInputHash}}));
    assert.deepEqual(document.populationResults,envelope);assert.equal(document.source.modelInputHash,modelInputHash);
  }
});


function largeModelText(){
  const model=structuredClone(fixture.envelope.model);
  for(const name of ['input_identity','engine','engine_version','model_name','model_version','source','applicability','validation'])model.provenance[name]='S'.repeat(8192);
  for(const name of ['activity_convention','standard_state','solvation_model','thermal_entropy_treatment','sampling_method'])model.conditions[name]='C'.repeat(8192);
  return JSON.stringify(model);
}
let largeResponse;
function responseFixture(){
  if(!largeResponse){
    const modelText=largeModelText(),modelInputHash=createHash('sha256').update(modelText).digest('hex');
    const wire=python('evaluate',{schemaVersion:1,requestId:'large-parse',documentRevision:0,graphHash:'a'.repeat(64),modelText,modelInputHash});
    assert.ok(Buffer.byteLength(wire)>16*1000*1000&&Buffer.byteLength(wire)<=16*1024*1024);
    largeResponse={modelText,wire};
  }
  return largeResponse;
}

test('cooperative near-limit reader services cancellation and absolute deadline before completion',async()=>{
  const {wire}=responseFixture();
  const controller=new AbortController();let acted=false;
  const timer=setTimeout(()=>{acted=true;controller.abort(new DOMException('Stopped during parsing','AbortError'));},1);
  try{await assert.rejects(readPopulationJsonAsync(wire,['envelope','model'],true,{signal:controller.signal}),error=>error.name==='AbortError');assert.equal(acted,true);}
  finally{clearTimeout(timer);}
  await assert.rejects(readPopulationJsonAsync(wire,['envelope','model'],true,{deadline:performance.now()+1}),error=>error.name==='TimeoutError');
  const invalid='{"envelope":{"samples":[{"result":{"fractions":['+'0,'.repeat(4096)+'0]}}]}}';
  await assert.rejects(readPopulationJsonAsync(invalid,['envelope','model'],true),/size\/depth/);
  await assert.rejects(readPopulationJsonAsync(' '.repeat(16*1024*1024+1)),/transport limit/);
});

test('cancel, timeout, revision and disposal during owned response parsing publish nothing and cancel once',async()=>{
  const {wire,modelText}=responseFixture();
  for(const mode of ['cancel','timeout','revision','dispose']){
    const store=createDocumentStore({schema:'nivram/1',graph:{atoms:[],bonds:[]}}),before=store.getSnapshot(),calls=[];
    let analysis,timer,expected=before;
    analysis=createPopulationAnalysis({editor:store,timeoutMs:mode==='timeout'?30:60000,fetcher:async(url,options)=>{
      calls.push(url);if(url.endsWith('/cancel'))return new Response('{}');
      const q=JSON.parse(options.body),bound=wire.replace('"requestId":"large-parse"','"requestId":'+JSON.stringify(q.requestId)).replace('"graphHash":"'+'a'.repeat(64)+'"','"graphHash":'+JSON.stringify(q.graphHash));
      const response=new Response(bound);
      if(mode!=='timeout')timer=setTimeout(()=>{
        assert.equal(analysis.status,'calculating');
        if(mode==='cancel')analysis.cancel();
        if(mode==='dispose')analysis.dispose();
        if(mode==='revision'){const document=structuredClone(before.document);document.graph.atoms.push({id:'a0',symbol:'C',x:0,y:0});store.commit(document);expected=store.getSnapshot();}
      },1);
      return response;
    }});
    try{
      await assert.rejects(analysis.loadText(modelText),error=>error.name===(mode==='timeout'?'TimeoutError':'AbortError'));await flush();
      assert.equal(analysis.status,'idle');assert.equal(analysis.result,null);assert.equal(calls.filter(url=>url.endsWith('/cancel')).length,1);
      assert.deepEqual(store.getSnapshot(),expected);assert.equal(store.historyState().canUndo,mode==='revision');
    }finally{clearTimeout(timer);analysis.dispose();}
  }
});

test('the full 2 MiB original input is owned by cancel, timeout, revision and disposal before any request',async()=>{
  const modelText=base+' '.repeat(2*1024*1024-Buffer.byteLength(base));
  for(const mode of ['cancel','timeout','revision','dispose']){
    const store=createDocumentStore({schema:'nivram/1',graph:{atoms:[],bonds:[]}}),before=store.getSnapshot();let calls=0,timer;
    const analysis=createPopulationAnalysis({editor:store,timeoutMs:mode==='timeout'?1:60000,fetcher:()=>{calls++;assert.fail('Cancelled original parsing cannot request evaluation');}});
    if(mode!=='timeout')timer=setTimeout(()=>{
      assert.equal(analysis.status,'calculating');
      if(mode==='cancel')analysis.cancel();
      if(mode==='dispose')analysis.dispose();
      if(mode==='revision'){const document=structuredClone(before.document);document.graph.atoms.push({id:'a0',symbol:'C',x:0,y:0});store.commit(document);}
    },1);
    try{await assert.rejects(analysis.loadText(modelText),error=>error.name===(mode==='timeout'?'TimeoutError':'AbortError'));await flush();assert.equal(calls,0);assert.equal(analysis.result,null);assert.equal(analysis.status,'idle');}
    finally{clearTimeout(timer);analysis.dispose();}
  }
  const f=harness();try{const result=await f.analysis.loadText(modelText);assert.equal(result.modelText,modelText);assert.equal(f.analysis.status,'ready');}finally{f.analysis.dispose();}
});
