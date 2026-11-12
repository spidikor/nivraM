import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createDocumentStore} from '../public/modules/document.mjs';
import {createPopulationAnalysis} from '../public/modules/population-analysis.mjs';
import {validatePopulationEnvelope} from '../public/modules/population-view.mjs';
const fixtures=JSON.parse(await readFile(new URL('./fixtures/population-analytical.json',import.meta.url),'utf8'));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function incompleteWithoutOmission(){
  const envelope=structuredClone(fixtures.incomplete.envelope),r=envelope.samples[70].result;
  r.fractions=r.fractions.map(x=>x/.8);r.percentages=r.fractions.map(x=>100*x);r.log_fractions=r.fractions.map(Math.log);
  r.omitted_fraction=null;r.omitted_mass_source=null;r.normalization_scope='conditional_on_represented_states';return envelope;
}
function unavailable(e,index){e.samples[index]={index,ph:index/10,status:'unavailable',result:null,reason:'Fabricated unavailable sample'};}
function harness(envelope){
  const editor=createDocumentStore({schema:'nivram/1',graph:{atoms:[],bonds:[]}}),before=editor.getSnapshot(),history=editor.historyState(),calls=[];let transform=()=>{};
  const analysis=createPopulationAnalysis({editor,fetcher:async(url,options)=>{
    const request=JSON.parse(options.body);calls.push({url,request});if(url.endsWith('/cancel'))return Response.json({});
    const value=structuredClone(envelope);value.documentRevision=request.documentRevision;value.graphHash=request.graphHash;transform(value);
    return Response.json({schemaVersion:1,requestId:request.requestId,modelInputHash:request.modelInputHash,envelope:value});
  }});
  return{editor,before,history,calls,analysis,modelText:JSON.stringify(envelope.model),setTransform(fn){transform=fn;}};
}

test('model-only route accepts complete, incomplete and correctly bounded domain gaps',async()=>{
  for(const input of [fixtures.complete.envelope,incompleteWithoutOmission(),fixtures.narrow.envelope]){
    const f=harness(input);try{const result=await f.analysis.loadText(f.modelText);assert.equal(f.analysis.status,'ready');assert.equal(result.envelope.samples.length,141);assert.deepEqual(f.editor.getSnapshot(),f.before);assert.deepEqual(f.editor.historyState(),f.history);}
    finally{f.analysis.dispose();}
  }
});

test('model-only route rejects an unavailable sample inside the supplied domain',async()=>{
  for(const [input,index] of [[fixtures.complete.envelope,0],[fixtures.complete.envelope,70],[fixtures.complete.envelope,140],[incompleteWithoutOmission(),70],[fixtures.narrow.envelope,74]]){
    const f=harness(input);try{
      f.setTransform(e=>unavailable(e,index));await assert.rejects(f.analysis.loadText(f.modelText),/in-domain sample.*unavailable/i);await tick();
      assert.equal(f.analysis.status,'idle');assert.equal(f.analysis.result,null);assert.equal(f.calls.filter(x=>x.url.endsWith('/cancel')).length,1);
      assert.deepEqual(f.editor.getSnapshot(),f.before);assert.deepEqual(f.editor.historyState(),f.history);
      f.setTransform(()=>{});const clean=await f.analysis.loadText(f.modelText);assert.equal(clean.envelope.samples[index].status,'computed');
    }finally{f.analysis.dispose();}
  }
});

test('generic envelope contract retains explicitly unavailable samples for other adapters',()=>{
  const e=structuredClone(fixtures.complete.envelope);unavailable(e,70);
  const accepted=validatePopulationEnvelope(e,{documentRevision:e.documentRevision,graphHash:e.graphHash,modelSignature:e.modelSignature});
  assert.equal(accepted.samples[70].status,'unavailable');assert.equal(accepted.samples[70].result,null);
});
