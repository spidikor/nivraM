import test from 'node:test';
import assert from 'node:assert/strict';
import {captureRenderRequest,validateRenderResponse,admitRenderResponse,requestRender} from '../public/modules/render-contract.mjs';
import {DEFAULT_STYLE} from '../public/modules/document.mjs';
import {propertyReport} from './property-response-fixtures.mjs';
const graph={atoms:[],bonds:[]},payload={graph,style:{...DEFAULT_STYLE}};
const capture=()=>captureRenderRequest('render',payload,3,'a'.repeat(32));
function envelope(c=capture()){
 const properties=propertyReport('empty');
 return {schema:'nivram-render/1',requestId:c.requestId,documentRevision:c.documentRevision,action:c.action,sourceRequest:structuredClone(c.sourceRequest),sourceIdentity:structuredClone(properties.inputIdentity),outputIdentity:structuredClone(properties.inputIdentity),outputGraphJson:'{"atoms":[],"bonds":[]}',graph:structuredClone(graph),scene:{properties,smiles:'',style:{...DEFAULT_STYLE}}};
}
test('captures immutable exact source and permits independent normalized output identity',()=>{
 const c=capture();assert.ok(Object.isFrozen(c.sourceRequest.graph));assert.deepEqual(validateRenderResponse(envelope(c),c).graph,graph);
 const r=envelope(c);r.outputIdentity.graphHash='b'.repeat(64);r.scene.properties.inputIdentity.graphHash=r.outputIdentity.graphHash;
 assert.doesNotThrow(()=>validateRenderResponse(r,c)); // exact input/output hashes need not match
});
test('rejects unbound, missing, extra and mismatched transport envelope',()=>{
 const c=capture();assert.throws(()=>validateRenderResponse({graph,scene:{}},c));
 for(const key of Object.keys(envelope(c))){const r=envelope(c);delete r[key];assert.throws(()=>validateRenderResponse(r,c),key);}
 for(const [key,value] of Object.entries({requestId:'b'.repeat(32),documentRevision:4,action:'clean',schema:'nivram-render/2',extra:true})){const r=envelope(c);r[key]=value;assert.throws(()=>validateRenderResponse(r,c),key);}
});
test('rejects stale source, appearance and identity reports; key order is irrelevant',()=>{
 const c=capture();const good=envelope(c);good.sourceRequest={style:good.sourceRequest.style,graph:good.sourceRequest.graph};assert.doesNotThrow(()=>validateRenderResponse(good,c));
 for(const change of [r=>r.sourceRequest.graph.atoms.push({id:'foreign'}),r=>r.sourceRequest.style.color=false,r=>r.scene.style.color=false,r=>r.scene.properties.inputIdentity.graphHash='c'.repeat(64),r=>r.scene.properties.inputIdentity.documentRevision=3,r=>r.outputIdentity.smiles='C',r=>r.sourceIdentity.structureHash='d'.repeat(64)]){const r=envelope(c);change(r);assert.throws(()=>validateRenderResponse(r,c));}
});
test('rejects unsupported actions, request fields, IDs and revisions',()=>{
 for(const args of [['import',payload,3,'a'.repeat(32)],['render',{...payload,selected:[]},3,'a'.repeat(32)],['render',payload,-1,'a'.repeat(32)],['render',payload,3,'bad']])assert.throws(()=>captureRenderRequest(...args));
});
test('transport captures source before async fetch and checks currency after body read',async()=>{
 let current=true,body,mutate=false;
 const source=structuredClone(payload),job={revision:3,signal:new AbortController().signal,assertCurrent(){if(!current)throw Error('stale');},wait:async p=>p};
 const fetchImpl=async(_url,options)=>{body=JSON.parse(options.body);source.style.color=false;const c={action:'render',requestId:body.requestId,documentRevision:3,sourceRequest:{graph:body.graph,style:body.style}};return {ok:true,text:async()=>{if(mutate)current=false;return JSON.stringify(envelope(c));}};};
 await requestRender('render',source,{job,fetchImpl,requestId:'a'.repeat(32)});assert.equal(body.style.color,true);
 mutate=true;await assert.rejects(requestRender('render',payload,{job,fetchImpl,requestId:'a'.repeat(32)}),/stale/);
});

import {createJobController} from '../public/modules/jobs.mjs';
import {prepareRender,createDocumentStore} from '../public/modules/document.mjs';
import fs from 'node:fs';
const dom={defaultView:{DOMParser:class{parseFromString(){return {querySelector:()=>null,documentElement:{localName:'svg',namespaceURI:'http://www.w3.org/2000/svg',children:[],attributes:[],querySelectorAll:()=>[]}};}}},createDocumentFragment(){return {children:[],ownerDocument:dom,querySelectorAll:()=>[],append(){}};}};
test('full scene admission remains mandatory after transport and legacy admission remains available',()=>{
 const c=capture(),r=envelope(c);Object.assign(r.scene,{svg:'<svg/>',positions:[],transform:{x0:0,y0:0,xScale:1,yScale:-1},width:800,height:500,formula:'',mass:0});
 assert.doesNotThrow(()=>prepareRender(validateRenderResponse(r,c),{schema:'nivram/1',graph},dom));
 const legacy={graph,scene:structuredClone(r.scene)};delete legacy.scene.properties;
 assert.doesNotThrow(()=>prepareRender(legacy,{schema:'nivram/1',graph},dom));
 assert.throws(()=>validateRenderResponse(legacy,c));
 r.scene.positions=[{id:'foreign',x:0,y:0}];assert.throws(()=>prepareRender(validateRenderResponse(r,c),{schema:'nivram/1',graph},dom));
});
test('exact request preserves atom order, IDs, coordinates, annotations and omitted defaults',()=>{
 const original={graph:{atoms:[{id:'a',symbol:'C',x:0,y:1,mapNum:7},{id:'b',symbol:'O',x:1,y:0}],bonds:[{id:'bond',begin:'a',end:'b',order:1}]},style:{...DEFAULT_STYLE}};
 const c=captureRenderRequest('render',original,3,'a'.repeat(32));
 for(const change of [g=>g.atoms.reverse(),g=>g.atoms[0].id='changed',g=>g.atoms[0].x=2,g=>g.atoms[0].mapNum=8,g=>g.atoms[0].charge=0]){const r=envelope(c);change(r.sourceRequest.graph);assert.throws(()=>validateRenderResponse(r,c));}
});
test('transport rejects aborted, replaced, timed-out and edit-undo ABA jobs without admitting late bodies',async()=>{
 for(const mode of ['cancel','replace','timeout','ABA']){
  const store=createDocumentStore({schema:'nivram/1',graph}),jobs=createJobController(()=>store.getSnapshot().revision,{timeoutMs:mode==='timeout'?10:1000}),job=jobs.begin('render');
  let release;const fetchImpl=()=>new Promise(resolve=>{release=resolve;});
  const pending=requestRender('render',payload,{job,fetchImpl,requestId:'a'.repeat(32)});let replacement;
  if(mode==='cancel')job.cancel();
  if(mode==='replace')replacement=jobs.begin('render');
  if(mode==='ABA'){store.commit({...store.getSnapshot().document,export:{...store.getSnapshot().document.export,width:1000}});store.commit(store.historyTarget(),{mode:'undo'});release({ok:true,text:async()=>JSON.stringify(envelope({...capture(),documentRevision:0}))});}
  await assert.rejects(pending,/cancelled|newer operation|timed out|document changed/);
  if(mode!=='ABA')release({ok:true,text:async()=>{throw Error('late body must not read');}});
  job.finish();replacement?.finish();
 }
});
test('all modern callers use strict helper and profile uses fully admitted output metadata',()=>{
 const read=p=>fs.readFileSync(new URL('../public/'+p,import.meta.url),'utf8');
 const app=read('app.mjs'),image=read('modules/image-import.mjs'),profile=read('modules/profiles.mjs');
 assert.match(app,/\['render','clean','reflect','rotate'\].includes\(action\).*requestRender/);
 assert.match(app,/expectedRevision:startupJob.revision/);assert.match(app,/expectedRevision:task.revision/);
 assert.match(image,/render:\(graph,style,job\)=>requestRender/);
 assert.match(profile,/prepareRender\(result,candidate,document\)/);assert.match(profile,/group.append\(prepared.fragment\)/);assert.match(profile,/graph:prepared.project.graph,removeChemistryMetadata/);
 assert.doesNotMatch(profile,/sanitizeSvg/);
});
import {createHash,webcrypto} from 'node:crypto';
const sha=s=>createHash('sha256').update(s,'utf8').digest('hex');
test('async witness admission rejects altered output graph, false graph hash and false SMILES hash',async()=>{
 const c=capture();await admitRenderResponse(envelope(c),c,{cryptoImpl:webcrypto});
 for(const mutate of [r=>r.graph.atoms.push({id:'a',symbol:'N',x:0,y:0}),r=>{r.outputIdentity.graphHash='b'.repeat(64);r.scene.properties.inputIdentity.graphHash='b'.repeat(64);},r=>{r.sourceIdentity.structureHash=r.outputIdentity.structureHash=r.scene.properties.inputIdentity.structureHash='b'.repeat(64);}]){const r=envelope(c);mutate(r);await assert.rejects(admitRenderResponse(r,c,{cryptoImpl:webcrypto}));}
 await assert.rejects(admitRenderResponse(envelope(c),c,{cryptoImpl:{}}));
});
test('witness preserves original Unicode escape and number spellings without JS serialization',async()=>{
 const graph={atoms:[{id:'µ',symbol:'C',x:1,y:-0}],bonds:[]},source={graph,style:{...DEFAULT_STYLE}},c=captureRenderRequest('render',source,3,'a'.repeat(32));
 const properties=propertyReport('methane'),witness='{"atoms":[{"id":"\\u00b5","symbol":"C","x":1.0,"y":-0.0}],"bonds":[]}';
 properties.inputIdentity.graphHash=sha(witness);
 const r={schema:'nivram-render/1',requestId:c.requestId,documentRevision:3,action:'render',sourceRequest:structuredClone(c.sourceRequest),sourceIdentity:structuredClone(properties.inputIdentity),outputIdentity:structuredClone(properties.inputIdentity),outputGraphJson:witness,graph,scene:{properties,smiles:'C',style:{...DEFAULT_STYLE}}};
 await admitRenderResponse(r,c,{cryptoImpl:webcrypto});
 for(const change of [g=>g.atoms[0].x=17,g=>g.atoms[0].symbol='N']){const changed=structuredClone(r);change(changed.graph);await assert.rejects(admitRenderResponse(changed,c,{cryptoImpl:webcrypto}));}
});
test('asynchronous digest completion checks revision and cancellation',async()=>{
 const c=capture();let current=true;
 const cryptoImpl={subtle:{digest:async(...args)=>{const result=await webcrypto.subtle.digest(...args);current=false;return result;}}};
 await assert.rejects(admitRenderResponse(envelope(c),c,{cryptoImpl,assertCurrent:()=>{if(!current)throw Error('stale digest');}}),/stale digest/);
});
test('maximum supported graph and escaped IDs fit transport budget with witness',async()=>{
 const atoms=Array.from({length:512},(_,i)=>({id:String(i)+'µ'.repeat(76),symbol:'C',x:10000,y:-10000,charge:0,isotope:0,explicitHs:0,noImplicit:false,aromatic:false,chiralTag:0,radicals:0,mapNum:999999}));
 const bonds=Array.from({length:1024},(_,i)=>({id:'b'+i,begin:atoms[i%512].id,end:atoms[(i%512+(i<512?1:2))%512].id,order:1,direction:0,stereo:0,stereoAtoms:[]}));
 const graph={atoms,bonds},source={graph,style:{...DEFAULT_STYLE}},c=captureRenderRequest('render',source,3,'a'.repeat(32));
 const witness=JSON.stringify(graph).replace(/µ/g,'\\u00b5'),properties=propertyReport('methane'); // controlled semantic schema, not a chemistry producer
 properties.inputIdentity.graphHash=sha(witness);
 const r={schema:'nivram-render/1',requestId:c.requestId,documentRevision:3,action:'render',sourceRequest:structuredClone(source),sourceIdentity:structuredClone(properties.inputIdentity),outputIdentity:structuredClone(properties.inputIdentity),outputGraphJson:witness,graph,scene:{properties,smiles:'C',style:{...DEFAULT_STYLE}}};
 assert.ok(new TextEncoder().encode(JSON.stringify(r)).length<4_000_000);
 await admitRenderResponse(r,c,{cryptoImpl:webcrypto});
});
