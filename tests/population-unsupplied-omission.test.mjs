import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createDocumentStore} from '../public/modules/document.mjs';
import {createPopulationAnalysis} from '../public/modules/population-analysis.mjs';
import {validatePopulationEnvelope} from '../public/modules/population-view.mjs';

const fixtures=JSON.parse(await readFile(new URL('./fixtures/population-analytical.json',import.meta.url),'utf8'));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function unknownIncomplete(){
  const envelope=structuredClone(fixtures.incomplete.envelope), r=envelope.samples[70].result;
  r.fractions=r.fractions.map(value=>value/.8);r.percentages=r.fractions.map(value=>100*value);
  r.log_fractions=r.fractions.map(Math.log);r.omitted_fraction=null;r.omitted_mass_source=null;r.normalization_scope='conditional_on_represented_states';
  return envelope;
}
function injectClaim(envelope,index,fraction,source){
  const r=envelope.samples[index].result;
  if(fraction!==0){r.fractions=r.fractions.map(value=>value*(1-fraction));r.percentages=r.fractions.map(value=>100*value);r.log_fractions=r.log_fractions.map(value=>value+Math.log1p(-fraction));}
  r.omitted_fraction=fraction;r.omitted_mass_source=source;
  r.normalization_scope=envelope.model.ensemble_complete?'declared_complete_supplied_model':'supplied_total_mass';
}
function harness(envelope){
  const editor=createDocumentStore({schema:'nivram/1',graph:{atoms:[],bonds:[]}}),before=editor.getSnapshot(),history=editor.historyState(),calls=[];
  let transform=()=>{};
  const analysis=createPopulationAnalysis({editor,fetcher:async(url,options)=>{
    const request=JSON.parse(options.body);calls.push({url,request});if(url.endsWith('/cancel'))return Response.json({});
    const value=structuredClone(envelope);value.documentRevision=request.documentRevision;value.graphHash=request.graphHash;transform(value);
    return Response.json({schemaVersion:1,requestId:request.requestId,modelInputHash:request.modelInputHash,envelope:value});
  }});
  return{editor,before,history,calls,analysis,modelText:JSON.stringify(envelope.model),setTransform(fn){transform=fn;}};
}

test('model-only route retains legitimate complete zero, incomplete unknown and narrow gaps',async()=>{
  for(const input of [fixtures.complete.envelope,unknownIncomplete(),fixtures.narrow.envelope]){
    const f=harness(input);try{const result=await f.analysis.loadText(f.modelText);assert.equal(f.analysis.status,'ready');assert.equal(result.envelope.samples.length,141);assert.deepEqual(f.editor.getSnapshot(),f.before);assert.deepEqual(f.editor.historyState(),f.history);}
    finally{f.analysis.dispose();}
  }
});

test('model-only route rejects unrequested omission claims at first, interior and last samples',async()=>{
  for(const [index,fraction] of [[0,.2],[70,.2],[140,.2],[70,0]]){
    const f=harness(unknownIncomplete());try{
      f.setTransform(e=>injectClaim(e,index,fraction,'unrequested source'));
      await assert.rejects(f.analysis.loadText(f.modelText),/omitted mass.*not supplied/i);await tick();
      assert.equal(f.analysis.status,'idle');assert.equal(f.analysis.result,null);assert.equal(f.calls.filter(x=>x.url.endsWith('/cancel')).length,1);
      assert.deepEqual(f.editor.getSnapshot(),f.before);assert.deepEqual(f.editor.historyState(),f.history);
      f.setTransform(()=>{});const subsequent=await f.analysis.loadText(f.modelText);assert.equal(subsequent.envelope.samples[70].result.omitted_fraction,null);
    }finally{f.analysis.dispose();}
  }
});

test('model-only route rejects an invented source even for the implicit complete-model zero',async()=>{
  const f=harness(fixtures.complete.envelope);try{
    f.setTransform(e=>injectClaim(e,70,0,'unrequested source'));
    await assert.rejects(f.analysis.loadText(f.modelText),/omitted mass.*not supplied/i);await tick();
    assert.equal(f.analysis.result,null);assert.equal(f.analysis.status,'idle');assert.equal(f.calls.filter(x=>x.url.endsWith('/cancel')).length,1);
    assert.deepEqual(f.editor.getSnapshot(),f.before);assert.deepEqual(f.editor.historyState(),f.history);
  }finally{f.analysis.dispose();}
});

test('generic envelope contract still accepts independently supplied omission evidence',()=>{
  const e=fixtures.incomplete.envelope;
  const accepted=validatePopulationEnvelope(e,{documentRevision:e.documentRevision,graphHash:e.graphHash,modelSignature:e.modelSignature});
  assert.equal(accepted.samples[70].result.omitted_fraction,.2);
  assert.equal(accepted.samples[70].result.omitted_mass_source,'Synthetic pH 7 mass assertion only');
});
