import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {createDocumentStore} from '../public/modules/document.mjs';
import {validatePopulationEnvelope} from '../public/modules/population-view.mjs';
import {createPopulationStateReview,MAX_STRUCTURES_BYTES} from '../public/modules/population-state-review.mjs';

const fixtureData=JSON.parse(await readFile(new URL('./fixtures/population-analytical.json',import.meta.url),'utf8')).complete;
const hash=value=>createHash('sha256').update(value).digest('hex');
const turn=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;return{promise:new Promise(done=>{resolve=done;}),resolve:value=>resolve(value)};};
const limitations=[
  'Explicit whole-drawing replacement with a supplied 2D depiction; model applicability to the current drawing remains unverified.',
  'Mapped core, charge, hydrogen stoichiometry and supported chemical identity were checked; assignment of energies to structures was not independently validated.',
  'This operation does not adopt or validate a 3D conformer geometry.'
];
// A deliberately tiny DOM for the empty valid SVG fixture. Production browser
// checks exercise the real DOM parser and actual chemistry/depiction pipeline.
const svg='<svg xmlns="http://www.w3.org/2000/svg"></svg>';
const fragment=()=>({
  nodes:[],
  append(node){this.nodes.push(node);},
  // This fixture covers only an empty SVG. Fail loudly if a future fixture
  // needs real element traversal instead of silently hiding nonempty content.
  get children(){assert.equal(this.nodes.length,0,'Use a real DOM for nonempty SVG content.');return [];},
  querySelectorAll(selector){assert.equal(selector,'[id]');assert.equal(this.nodes.length,0,'Use a real DOM for nonempty SVG content.');return [];},
  cloneNode(){const copy=fragment();copy.nodes=structuredClone(this.nodes);return copy;}
});
const dom={defaultView:{DOMParser:class{parseFromString(source){assert.equal(source,svg);return{querySelector:()=>null,documentElement:{localName:'svg',namespaceURI:'http://www.w3.org/2000/svg',attributes:[],children:[],querySelectorAll:()=>[]}};}}},createDocumentFragment:fragment,importNode:node=>structuredClone(node)};

function fixture({analysis={notes:['Keep this note'],imageImport:{kind:'original'}},timeoutMs=10000}={}){
  const store=createDocumentStore({schema:'nivram/1',graph:{atoms:[{id:'old',symbol:'N',x:3,y:4}],bonds:[]},style:{bondWidth:2,labelSize:24,bondSpacing:.2,color:false},export:{width:4321,marginMode:'pixels',margin:12},analysis});
  const before=store.getSnapshot(),raw=structuredClone(fixtureData.envelope);
  raw.documentRevision=before.revision;raw.graphHash=hash(JSON.stringify(before.document.graph));
  const envelope=validatePopulationEnvelope(raw,{documentRevision:before.revision,graphHash:raw.graphHash,modelSignature:raw.modelSignature});
  const modelText=JSON.stringify(envelope.model),modelInputHash=hash(modelText),result={name:'model.json',modelText,modelInputHash,envelope};
  const sidecar={schemaVersion:1,fileKind:'nivram-population-state-structures',modelInputHash,structures:envelope.model.states.map(state=>({stateId:state.state_id,structureId:state.structure_id,format:'smiles',source:state.state_id==='HA'?' [CH3:1][OH:2] ':'[CH3:1][O-:2]',coreAtomMaps:[{atomId:'a0',mapNum:1},{atomId:'a1',mapNum:2}]}))};
  const calls=[],opens=[],fault={};
  function payload(request){
    const file=JSON.parse(request.structuresText),entry=file.structures.find(row=>row.stateId===request.stateId),reference=file.structures.find(row=>row.stateId==='HA'),state=envelope.model.states.find(row=>row.state_id===request.stateId);
    const graph={atoms:[{id:'a0',symbol:'C',x:0,y:0,explicitHs:3,mapNum:1},{id:'a1',symbol:'O',x:1.5,y:0,explicitHs:request.stateId==='HA'?1:0,charge:state.formal_charge,mapNum:2}],bonds:[{id:'b0',begin:'a0',end:'a1',order:1}]};
    const canonicalIsomericSmiles=request.stateId==='HA'?'CO':'C[O-]';
    return{graph,scene:{style:request.style,positions:graph.atoms.map(({id,x,y})=>({id,x,y})),transform:{x0:0,y0:0,xScale:1,yScale:-1},width:100,height:100,formula:'test fixture',smiles:canonicalIsomericSmiles,mass:31,svg},
      populationState:{schemaVersion:1,requestId:request.requestId,documentRevision:request.documentRevision,graphHash:request.graphHash,modelInputHash:request.modelInputHash,structuresInputHash:request.structuresInputHash,modelSignature:envelope.modelSignature,stateId:request.stateId,structureId:state.structure_id,referenceStateId:'HA',source:{format:entry.format,sha256:hash(entry.source)},referenceSource:{format:reference.format,sha256:hash(reference.source)},coreAtomMaps:entry.coreAtomMaps.map((row,index)=>({...row,graphAtomId:`a${index}`,element:graph.atoms[index].symbol,isotope:0})),formalCharge:state.formal_charge,relativeProtons:state.relative_protons,observedHydrogenDifference:state.relative_protons-1,identity:{canonicalIsomericSmiles,structureHash:hash(canonicalIsomericSmiles)},referenceIdentity:{canonicalIsomericSmiles:'CO',structureHash:hash('CO')},depiction:'2d',modelApplicability:'unverified',thermodynamics:'supplied; not independently validated',conformerGeometry:'not adopted',limitations}};
  }
  const fetcher=async(url,options)=>{
    const request=JSON.parse(options.body);calls.push({url,options,request});
    if(url==='/api/populations/cancel-state')return new Response('{}');
    assert.equal(url,'/api/populations/prepare-state');
    return fault.prepare?fault.prepare(request,options):Response.json(payload(request));
  };
  const editor={getSnapshot:store.getSnapshot,subscribe:store.subscribe,async openProject(candidate,options){
    opens.push({candidate:structuredClone(candidate),options});if(fault.open)await fault.open(options);
    if(options.signal.aborted||fault.rejectOpen)return false;
    try{store.commit(candidate,{expectedRevision:options.expectedRevision});if(options.asUnsaved)store.markUnsaved();return true;}catch{return false;}
  }};
  const review=createPopulationStateReview({editor,result,fetcher,dom,token:'test-session-token',timeoutMs});
  const load=()=>review.loadText(JSON.stringify(sidecar),{name:'states.json'});
  return{review,store,before,result,sidecar,calls,opens,fault,payload,load,preparations:()=>calls.filter(row=>row.url.endsWith('/prepare-state')),cancellations:()=>calls.filter(row=>row.url.endsWith('/cancel-state'))};
}

test('exact sidecar/model bytes bind an isolated explicit 2D preview with source and graph receipts',async()=>{
  const f=fixture();try{
    const text=JSON.stringify(f.sidecar,null,2)+'\n',loaded=await f.review.loadText(text,{name:'structures α.json'});
    assert.equal(loaded.structuresInputHash,hash(text));assert.deepEqual(loaded.stateIds,['HA','A']);assert.equal(f.review.structuresName,'structures α.json');
    assert.equal(f.review.hasState('CO'),false);assert.equal(f.review.hasState('A'),true);
    const preview=await f.review.previewState('A'),call=f.preparations()[0];
    assert.equal(call.request.structuresText,text);assert.equal(call.request.modelText,f.result.modelText);assert.equal(call.request.graphHash,f.result.envelope.graphHash);assert.equal(call.request.documentRevision,f.before.revision);
    assert.equal(call.options.headers['X-Nivram-Token'],'test-session-token');assert.equal(call.options.method,'POST');
    assert.deepEqual(Object.keys(call.request).sort(),['schemaVersion','requestId','documentRevision','graphHash','modelText','modelInputHash','structuresText','structuresInputHash','stateId','style'].sort());
    assert.equal(preview.populationState.source.sha256,hash('[CH3:1][O-:2]'));assert.equal(preview.populationState.referenceSource.sha256,hash(' [CH3:1][OH:2] '));
    assert.equal(f.review.status,'ready');assert.deepEqual(f.store.getSnapshot(),f.before);assert.equal(f.store.historyState().canUndo,false);
    preview.graph.atoms[0].symbol='N';preview.populationState.stateId='changed';preview.fragment.nodes.push('changed');
    assert.equal(f.review.preview.graph.atoms[0].symbol,'C');assert.equal(f.review.preview.populationState.stateId,'A');assert.deepEqual(f.review.preview.fragment.nodes,[]);
  }finally{f.review.dispose();}
});

test('sidecar schema, state identities, exact byte binding, bounded core labels and maps fail before requests',async()=>{
  const mutations=[s=>s.modelInputHash='b'.repeat(64),s=>s.extra=true,s=>s.schemaVersion=2,s=>s.structures.push(structuredClone(s.structures[0])),s=>s.structures[0].stateId='CO',s=>s.structures[0].structureId='CO',s=>s.structures[0].source='C\0O',s=>s.structures[0].format='auto',s=>s.structures[0].coreAtomMaps[1].mapNum=1,s=>s.structures[0].coreAtomMaps[1].atomId='a0',s=>s.structures[0].coreAtomMaps[1].atomId='missing',s=>s.structures[0].coreAtomMaps[0].mapNum=0,s=>s.structures[0].coreAtomMaps[0].extra=true];
  for(const mutate of mutations){const f=fixture();try{mutate(f.sidecar);await assert.rejects(f.load());assert.equal(f.calls.length,0);assert.equal(f.review.preview,null);assert.deepEqual(f.store.getSnapshot(),f.before);}finally{f.review.dispose();}}
  const f=fixture();try{
    for(const value of [null,'{',' '.repeat(MAX_STRUCTURES_BYTES+1)])await assert.rejects(f.review.loadText(value));
    assert.equal(f.calls.length,0);
    f.sidecar.structures=f.sidecar.structures.filter(row=>row.stateId==='A');await f.load();assert.equal(f.review.hasState('A'),false);await assert.rejects(f.review.previewState('A'),/reference/);assert.equal(f.calls.length,0);
  }finally{f.review.dispose();}
});

test('tampered receipt identities, source hashes, stoichiometry, core mapping, flags and scenes cannot publish',async()=>{
  const mutations=[
    [p=>p.populationState.requestId='foreign-request',/requestId does not match/],
    [p=>p.populationState.modelSignature='b'.repeat(64),/changed its model or state identities/],
    [p=>p.populationState.structuresInputHash='b'.repeat(64),/structuresInputHash does not match/],
    [p=>p.populationState.source.sha256='b'.repeat(64),/changed its selected or reference structure source/],
    [p=>p.populationState.referenceSource.sha256='b'.repeat(64),/changed its selected or reference structure source/],
    [p=>p.populationState.identity.structureHash='b'.repeat(64),/invalid chemical identity digest/],
    [p=>p.populationState.observedHydrogenDifference=0,/charge and proton relationships/],
    [p=>p.populationState.formalCharge=0,/charge and proton relationships/],
    [p=>p.populationState.coreAtomMaps[0].graphAtomId='missing',/core does not correspond/],
    [p=>p.populationState.coreAtomMaps[0].element='N',/core does not correspond/],
    [p=>p.populationState.conformerGeometry='adopted',/omitted its scientific limitations/],
    [p=>p.populationState.limitations=[],/retain its exact scientific limitations/],
    [p=>p.scene.positions.pop(),/scene does not match its molecular graph/],
    [p=>p.scene.style.color=true,/changed the current drawing style/],
    [p=>p.scene.svg='<!DOCTYPE svg><svg/>',/returned unsupported SVG/],
    [p=>p.extra=true,/response has an unsupported schema/],
  ];
  for(const [mutate,reason] of mutations){const f=fixture();try{
    await f.load();f.fault.prepare=request=>{const value=f.payload(request);mutate(value);return Response.json(value);};
    await assert.rejects(f.review.previewState('A'),reason);await turn();assert.equal(f.review.preview,null);assert.equal(f.review.status,'loaded');assert.equal(f.cancellations().length,1);assert.deepEqual(f.store.getSnapshot(),f.before);assert.equal(f.opens.length,0);
  }finally{f.review.dispose();}}
});

test('explicit adoption preserves style, exports and both legacy analysis representations in exactly one history entry',async()=>{
  for(const analysis of [{notes:['Preserved'],imageImport:{kind:'old'}},[{kind:'legacy',note:'Preserved'}]]){
    const f=fixture({analysis});try{
      await f.load();await f.review.previewState('A');await assert.rejects(f.review.adopt({index:70}),/Confirm/);assert.equal(f.opens.length,0);
      const exposed=f.review.preview;exposed.graph.atoms[0].symbol='N';exposed.scene.style.color=true;exposed.populationState.stateId='changed';
      const adoption=await f.review.adopt({index:70,confirmed:true}),after=f.store.getSnapshot();
      assert.equal(f.opens.length,1);assert.equal(f.opens[0].options.expectedRevision,f.before.revision);assert.ok(f.opens[0].options.signal instanceof AbortSignal);assert.equal(f.opens[0].options.asUnsaved,undefined);
      assert.equal(after.revision,f.before.revision+1);assert.equal(after.document.graph.atoms[0].symbol,'C');assert.deepEqual(after.document.style,f.before.document.style);assert.deepEqual(after.document.export,f.before.document.export);
      if(Array.isArray(analysis))assert.deepEqual(after.document.analysis.previousAnalysis,analysis);else for(const key of Object.keys(analysis))assert.deepEqual(after.document.analysis[key],analysis[key]);
      assert.deepEqual(after.document.analysis.populationAdoption,adoption);assert.equal(adoption.selectedSample.fraction,.5);assert.equal(adoption.selectedSample.normalizationScope,'declared_complete_supplied_model');assert.equal(adoption.receipt.stateId,'A');assert.equal(adoption.model.sha256,f.result.modelInputHash);
      assert.equal('samples' in adoption,false);assert.equal('states' in adoption,false);assert.equal(f.review.status,'adopted');assert.equal(f.review.preview,null);
      assert.deepEqual(f.store.historyTarget(),f.before.document);f.store.commit(f.store.historyTarget(),{mode:'undo'});assert.deepEqual(f.store.getSnapshot().document,f.before.document);assert.equal(f.store.historyState().canUndo,false);
    }finally{f.review.dispose();}
  }
});

test('cancellation retains the sidecar, uses an independent authenticated request and rejects late responses',async()=>{
  const f=fixture(),entered=deferred(),late=deferred();let request,signal;
  try{
    await f.load();f.fault.prepare=(value,options)=>{request=value;signal=options.signal;entered.resolve();return late.promise;};
    const pending=f.review.previewState('A'),rejected=assert.rejects(pending,error=>error.name==='AbortError');await entered.promise;f.review.cancel();await rejected;await turn();
    assert.equal(f.review.hasState('HA'),true);assert.equal(f.review.status,'loaded');assert.equal(signal.aborted,true);
    const cancel=f.cancellations()[0];assert.equal(f.cancellations().length,1);assert.deepEqual(cancel.request,{schemaVersion:1,requestId:request.requestId});assert.notEqual(cancel.options.signal,signal);assert.equal(cancel.options.signal.aborted,false);assert.equal(cancel.options.keepalive,true);assert.equal(cancel.options.headers['X-Nivram-Token'],'test-session-token');
    delete f.fault.prepare;await f.review.previewState('HA');late.resolve(Response.json(f.payload(request)));await turn();assert.equal(f.review.preview.populationState.stateId,'HA');assert.deepEqual(f.store.getSnapshot(),f.before);
  }finally{late.resolve(new Response('{}'));f.review.dispose();}
});

test('revision changes and disposal revoke pending or reviewed states before they can be adopted',async()=>{
  for(const pendingMode of [true,false]){
    const f=fixture(),entered=deferred(),late=deferred();let request;
    try{
      await f.load();let rejected;
      if(pendingMode){f.fault.prepare=value=>{request=value;entered.resolve();return late.promise;};rejected=assert.rejects(f.review.previewState('A'),/drawing changed/i);await entered.promise;}
      else await f.review.previewState('A');
      f.store.commit({...f.before.document,export:{...f.before.document.export,width:1234}});const changed=f.store.getSnapshot();if(rejected)await rejected;
      assert.equal(f.review.preview,null);assert.equal(f.review.hasState('A'),false);await assert.rejects(f.review.adopt({index:70,confirmed:true}),/drawing changed/i);
      if(request)late.resolve(Response.json(f.payload(request)));await turn();assert.deepEqual(f.store.getSnapshot(),changed);assert.equal(f.opens.length,0);
    }finally{late.resolve(new Response('{}'));f.review.dispose();}
  }
  const f=fixture();await f.load();await f.review.previewState('A');f.review.dispose();assert.equal(f.review.preview,null);assert.equal(f.review.structuresName,null);await assert.rejects(f.review.adopt({index:70,confirmed:true}),/closed/);assert.deepEqual(f.store.getSnapshot(),f.before);
});

test('cancellation while adoption awaits editor rendering prevents a later commit',async()=>{
  const f=fixture(),entered=deferred(),late=deferred();
  try{
    await f.load();await f.review.previewState('A');f.fault.open=()=>{entered.resolve();return late.promise;};
    const pending=f.review.adopt({index:70,confirmed:true}),rejected=assert.rejects(pending,/not committed/);await entered.promise;f.review.cancel();assert.equal(f.opens[0].options.signal.aborted,true);late.resolve();await rejected;
    assert.deepEqual(f.store.getSnapshot(),f.before);assert.equal(f.store.historyState().canUndo,false);assert.equal(f.review.preview,null);
  }finally{late.resolve();f.review.dispose();}
});

test('render failures retain the preceding drawing and require a fresh preview',async()=>{
  const f=fixture();try{await f.load();await f.review.previewState('A');f.fault.rejectOpen=true;await assert.rejects(f.review.adopt({index:70,confirmed:true}),/not committed/);assert.deepEqual(f.store.getSnapshot(),f.before);assert.equal(f.review.preview,null);assert.equal(f.review.status,'loaded');assert.equal(f.review.hasState('A'),true);assert.equal(f.store.historyState().canUndo,false);}finally{f.review.dispose();}
});

test('timeout and bounded response failure cancel owned work without publishing',async()=>{
  const f=fixture({timeoutMs:20}),late=deferred();try{
    await f.load();f.fault.prepare=()=>late.promise;await assert.rejects(f.review.previewState('A'),error=>error.name==='TimeoutError');await turn();assert.equal(f.cancellations().length,1);assert.equal(f.review.preview,null);assert.deepEqual(f.store.getSnapshot(),f.before);
  }finally{late.resolve(new Response('{}'));f.review.dispose();}
  const bounded=fixture();let cancelled=false;try{
    await bounded.load();bounded.fault.prepare=()=>new Response(new ReadableStream({cancel(){cancelled=true;}}),{headers:{'Content-Length':String(8*1024*1024+1)}});
    await assert.rejects(bounded.review.previewState('A'),/size limit/);assert.equal(cancelled,true);assert.equal(bounded.review.preview,null);assert.deepEqual(bounded.store.getSnapshot(),bounded.before);
  }finally{bounded.review.dispose();}
});

