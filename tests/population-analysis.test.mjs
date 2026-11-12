import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {createDocumentStore} from '../public/modules/document.mjs';
import {createPopulationAnalysis,MAX_MODEL_BYTES} from '../public/modules/population-analysis.mjs';

// The response data is a Python-produced synthetic analytical fixture. These
// tests cover the controller and its stream/freshness boundary, not prediction.
const fixtureData=JSON.parse(await readFile(new URL('./fixtures/population-analytical.json',import.meta.url),'utf8')).complete;
const modelText=JSON.stringify(fixtureData.envelope.model),hash=value=>createHash('sha256').update(value).digest('hex');
const deferred=()=>{let resolve;return{promise:new Promise(value=>{resolve=value;}),resolve:value=>resolve(value)};};
const turn=()=>new Promise(resolve=>setImmediate(resolve));
function fixture({timeoutMs=10000}={}){
  const store=createDocumentStore({schema:'nivram/1',graph:{atoms:[{id:'a0',symbol:'C',x:0,y:0},{id:'a1',symbol:'O',x:1.5,y:0}],bonds:[{id:'b0',begin:'a0',end:'a1',order:1}]}});
  const before=store.getSnapshot(),calls=[],fault={},invalidations=[];let sequence=0;
  const payload=request=>{
    const envelope=structuredClone(fixtureData.envelope);envelope.documentRevision=request.documentRevision;envelope.graphHash=request.graphHash;
    return{schemaVersion:1,requestId:request.requestId,modelInputHash:request.modelInputHash,envelope};
  };
  const fetcher=async(url,options)=>{
    const request=JSON.parse(options.body);calls.push({url,options,request});
    if(url==='/api/populations/cancel')return new Response('{}',{status:200});
    assert.equal(url,'/api/populations/evaluate');
    return fault.evaluate?fault.evaluate(request,options):Response.json(payload(request));
  };
  const analysis=createPopulationAnalysis({editor:store,fetcher,token:'session-token',timeoutMs,newId:()=>`population-request-${++sequence}`,onInvalidate:message=>invalidations.push(message)});
  return{store,before,calls,fault,payload,analysis,invalidations,evaluations:()=>calls.filter(call=>call.url.endsWith('/evaluate')),cancellations:()=>calls.filter(call=>call.url.endsWith('/cancel'))};
}

test('a supplied model is bound to exact request bytes and the unchanged drawing without committing a project edit',async()=>{
  const f=fixture();try{
    const result=await f.analysis.loadText(modelText,{name:'model.json'}),request=f.evaluations()[0];
    assert.equal(f.analysis.status,'ready');assert.equal(f.analysis.result,result);assert.equal(result.name,'model.json');
    assert.equal(request.options.method,'POST');assert.equal(request.options.headers['X-Nivram-Token'],'session-token');
    assert.equal(request.request.requestId,'population-request-1');assert.equal(request.request.schemaVersion,1);
    assert.equal(request.request.documentRevision,f.before.revision);assert.equal(request.request.graphHash,hash(JSON.stringify(f.before.document.graph)));
    assert.equal(request.request.modelText,modelText);assert.equal(request.request.modelInputHash,hash(modelText));assert.equal(result.modelInputHash,hash(modelText));
    assert.deepEqual(result.envelope.model,fixtureData.envelope.model);assert.deepEqual(result.envelope.samples[70].result.fractions,[.5,.5]);
    assert.deepEqual(f.store.getSnapshot(),f.before);assert.equal(f.store.historyState().canUndo,false);assert.deepEqual(f.invalidations,[]);assert.equal(f.cancellations().length,0);
    assert.ok(Object.isFrozen(result));assert.ok(Object.isFrozen(result.envelope.model));assert.throws(()=>{result.envelope.model.provenance.source='changed';},TypeError);
  }finally{f.analysis.dispose();}
});

test('malformed, invalid and oversized input is rejected before any evaluator request',async()=>{
  const invalidModel=structuredClone(fixtureData.envelope.model);invalidModel.conditions.temperature_kelvin=0;
  for(const text of [null,'{','{}','[]',JSON.stringify(invalidModel),' '.repeat(MAX_MODEL_BYTES+1),'é'.repeat(Math.floor(MAX_MODEL_BYTES/2)+1)]){
    const f=fixture();try{await assert.rejects(f.analysis.loadText(text));assert.equal(f.calls.length,0);assert.equal(f.analysis.status,'idle');assert.equal(f.analysis.result,null);assert.deepEqual(f.store.getSnapshot(),f.before);}finally{f.analysis.dispose();}
  }
});

test('response request, byte hash, drawing binding and model inconsistencies are rejected without publishing a result',async()=>{
  const mutations=[
    payload=>payload.requestId='another-request',payload=>payload.modelInputHash='b'.repeat(64),payload=>payload.schemaVersion=2,payload=>payload.extra='unexpected',
    payload=>payload.envelope.graphHash='b'.repeat(64),payload=>payload.envelope.documentRevision++,
    payload=>payload.envelope.modelSignature='b'.repeat(64),payload=>payload.envelope.samples[70].result.model_signature='b'.repeat(64),
    payload=>payload.envelope.samples[70].result.input_identity='another-input',
    payload=>{payload.envelope.model.provenance.source='Different model source';for(const row of payload.envelope.samples)row.result.provenance.source='Different model source';}
  ];
  for(const mutate of mutations){
    const f=fixture();try{
      f.fault.evaluate=request=>{const value=f.payload(request);mutate(value);return Response.json(value);};
      await assert.rejects(f.analysis.loadText(modelText));await turn();assert.equal(f.analysis.status,'idle');assert.equal(f.analysis.result,null);assert.deepEqual(f.store.getSnapshot(),f.before);
      assert.equal(f.cancellations().length,1);assert.equal(f.cancellations()[0].request.requestId,f.evaluations()[0].request.requestId);
    }finally{f.analysis.dispose();}
  }
});

test('server and stream decoding failures retain the drawing and cancel the owned calculation',async()=>{
  const responses=[()=>Response.json({error:'Supplied model domain is unsupported.'},{status:422}),()=>new Response('{broken'),()=>new Response(new Uint8Array([0xc3,0x28])),()=>new Response(null,{status:204})];
  for(const response of responses){const f=fixture();try{f.fault.evaluate=response;await assert.rejects(f.analysis.loadText(modelText));await turn();assert.equal(f.analysis.result,null);assert.equal(f.analysis.status,'idle');assert.equal(f.cancellations().length,1);assert.deepEqual(f.store.getSnapshot(),f.before);}finally{f.analysis.dispose();}}
});

test('cancel sends a separate authenticated cancellation and a late successful response cannot publish',async()=>{
  const f=fixture(),entered=deferred(),late=deferred();let request,signal;
  try{
    f.fault.evaluate=(value,options)=>{request=value;signal=options.signal;entered.resolve();return late.promise;};
    const pending=f.analysis.loadText(modelText),rejected=assert.rejects(pending,error=>error.name==='AbortError');
    await entered.promise;f.analysis.cancel();await rejected;await turn();assert.equal(signal.aborted,true);assert.equal(f.analysis.result,null);assert.equal(f.analysis.status,'idle');
    const cancellation=f.cancellations()[0];assert.equal(f.cancellations().length,1);assert.equal(cancellation.options.headers['X-Nivram-Token'],'session-token');assert.equal(cancellation.options.keepalive,true);assert.notEqual(cancellation.options.signal,signal);assert.equal(cancellation.options.signal.aborted,false);
    assert.deepEqual(cancellation.request,{schemaVersion:1,requestId:request.requestId});
    late.resolve(Response.json(f.payload(request)));await turn();assert.equal(f.analysis.result,null);assert.deepEqual(f.store.getSnapshot(),f.before);
  }finally{late.resolve(new Response('{}'));f.analysis.dispose();}
});

test('a replaced request cannot overwrite a newer result when its response arrives late',async()=>{
  const f=fixture(),entered=deferred(),late=deferred();let oldRequest;
  try{
    f.fault.evaluate=request=>{if(!oldRequest){oldRequest=request;entered.resolve();return late.promise;}return Response.json(f.payload(request));};
    const pending=f.analysis.loadText(modelText,{name:'old.json'}),rejected=assert.rejects(pending,/cancelled/i);await entered.promise;
    const current=await f.analysis.loadText(modelText,{name:'new.json'});await rejected;
    assert.equal(current.name,'new.json');assert.equal(f.analysis.status,'ready');late.resolve(Response.json(f.payload(oldRequest)));await turn();
    assert.equal(f.analysis.result,current);assert.equal(f.analysis.status,'ready');assert.deepEqual(f.store.getSnapshot(),f.before);
    assert.equal(f.cancellations().length,1);assert.equal(f.cancellations()[0].request.requestId,oldRequest.requestId);
  }finally{late.resolve(new Response('{}'));f.analysis.dispose();}
});

test('drawing changes discard pending and already displayed results without changing the new drawing',async()=>{
  const f=fixture(),entered=deferred(),late=deferred();let request;
  try{
    f.fault.evaluate=value=>{request=value;entered.resolve();return late.promise;};const pending=f.analysis.loadText(modelText),rejected=assert.rejects(pending,/drawing changed/i);
    await entered.promise;const changed=structuredClone(f.before.document);changed.graph.atoms[1].symbol='N';f.store.commit(changed);const afterEdit=f.store.getSnapshot();await rejected;
    late.resolve(Response.json(f.payload(request)));await turn();assert.equal(f.analysis.result,null);assert.equal(f.analysis.status,'idle');assert.deepEqual(f.store.getSnapshot(),afterEdit);assert.equal(f.invalidations.length,1);
    delete f.fault.evaluate;await f.analysis.loadText(modelText);assert.equal(f.analysis.status,'ready');
    const moved=structuredClone(afterEdit.document);moved.graph.atoms[1].x=2;f.store.commit(moved);assert.equal(f.analysis.status,'idle');assert.equal(f.analysis.result,null);assert.equal(f.invalidations.length,2);
  }finally{late.resolve(new Response('{}'));f.analysis.dispose();}
});

test('timeout aborts the calculation, sends cancellation once and discards late data',async()=>{
  const f=fixture({timeoutMs:500}),entered=deferred(),late=deferred();let request,signal;
  try{
    f.fault.evaluate=(value,options)=>{request=value;signal=options.signal;entered.resolve();return late.promise;};
    const pending=f.analysis.loadText(modelText),rejected=assert.rejects(pending,error=>error.name==='TimeoutError');await entered.promise;await rejected;await turn();
    assert.equal(signal.aborted,true);assert.equal(f.analysis.status,'idle');assert.equal(f.analysis.result,null);assert.equal(f.cancellations().length,1);
    late.resolve(Response.json(f.payload(request)));await turn();assert.equal(f.analysis.result,null);assert.deepEqual(f.store.getSnapshot(),f.before);
  }finally{late.resolve(new Response('{}'));f.analysis.dispose();}
});

test('bounded streamed responses reject oversized bytes or declared length and cancel their readers',async()=>{
  for(const advertised of [true,false]){
    const f=fixture();let cancelled=0,pulls=0;
    try{
      const block=new Uint8Array(1024*1024),stream=new ReadableStream({pull(controller){pulls++;controller.enqueue(block);},cancel(){cancelled++;}});
      f.fault.evaluate=()=>new Response(stream,advertised?{headers:{'Content-Length':String(16*1024*1024+1)}}:{});
      await assert.rejects(f.analysis.loadText(modelText),/size limit/);assert.equal(cancelled,1);assert.ok(pulls<=18,`The reader must stop at the bounded response size, read ${pulls} chunks.`);
      await turn();assert.equal(f.cancellations().length,1);assert.equal(f.analysis.result,null);assert.deepEqual(f.store.getSnapshot(),f.before);
    }finally{f.analysis.dispose();}
  }
});

test('chunked UTF-8 success uses the same validation and disposal removes its revision subscription',async()=>{
  const f=fixture();try{
    f.fault.evaluate=request=>{
      const bytes=new TextEncoder().encode(JSON.stringify(f.payload(request)));let position=0;
      return new Response(new ReadableStream({pull(controller){if(position===bytes.length){controller.close();return;}const end=Math.min(position+97,bytes.length);controller.enqueue(bytes.subarray(position,end));position=end;}}));
    };
    const result=await f.analysis.loadText(modelText);assert.equal(f.analysis.result,result);assert.equal(f.analysis.status,'ready');
    f.analysis.dispose();assert.equal(f.analysis.status,'idle');assert.equal(f.analysis.result,null);await assert.rejects(f.analysis.loadText(modelText),/closed/);
    const changed=structuredClone(f.before.document);changed.graph.atoms[0].x=1;f.store.commit(changed);assert.deepEqual(f.invalidations,[]);
  }finally{f.analysis.dispose();}
});
