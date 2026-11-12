import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,webcrypto} from 'node:crypto';
import fs from 'node:fs';
import {captureImportRequest,admitImportResponse,requestImport} from '../public/modules/import-contract.mjs';
import {DEFAULT_STYLE,prepareRender} from '../public/modules/document.mjs';
import {propertyReport} from './property-response-fixtures.mjs';
const sha=s=>createHash('sha256').update(s,'utf8').digest('hex');
const graph={atoms:[],bonds:[]};
const capture=(source=' CCO\r\nµ ',style={})=>captureImportRequest({source,style},3,'a'.repeat(32));
function envelope(c=capture()){
 const properties=propertyReport('empty');
 return {schema:'nivram-import/1',action:'import',requestId:c.requestId,documentRevision:c.documentRevision,source:{sha256:sha(c.source),bytes:new TextEncoder().encode(c.source).length},style:{...c.style},outputIdentity:structuredClone(properties.inputIdentity),outputGraphJson:'{"atoms":[],"bonds":[]}',graph:structuredClone(graph),scene:{properties,smiles:'',style:{...c.style}}};
}
test('captures exact source and normalized immutable style; parser output need not equal source',async()=>{
 const input={source:' CCO\r\nµ ',style:{color:false}},c=captureImportRequest(input,3,'a'.repeat(32));input.style.color=true;
 assert.equal(c.source,' CCO\r\nµ ');assert.equal(c.style.color,false);assert.ok(Object.isFrozen(c.style));
 await admitImportResponse(envelope(c),c,{cryptoImpl:webcrypto});
});
test('rejects unbound or altered closed receipts, style and output identities',async()=>{
 const c=capture();await assert.rejects(admitImportResponse({graph,scene:{}},c));
 for(const key of Object.keys(envelope(c))){const r=envelope(c);delete r[key];await assert.rejects(admitImportResponse(r,c),key);}
 for(const change of [r=>r.extra=true,r=>r.source.extra=true,r=>r.source.bytes++,r=>r.source.sha256=sha(c.source.trim()),r=>r.source.sha256=sha(c.source.replace(/\r\n/g,'\n')),r=>r.requestId='b'.repeat(32),r=>r.documentRevision++,r=>r.schema='nivram-import/2',r=>r.action='render',r=>r.style.color=false,r=>r.scene.style.color=false,r=>r.graph.atoms.push({id:'a',symbol:'C',x:0,y:0}),r=>r.outputIdentity.graphHash='b'.repeat(64),r=>{r.outputIdentity.structureHash=r.scene.properties.inputIdentity.structureHash='b'.repeat(64);}]){const r=envelope(c);change(r);await assert.rejects(admitImportResponse(r,c),String(change));}
 await assert.rejects(admitImportResponse(envelope(c),c,{cryptoImpl:{}}));
});
test('rejects invalid source, transport metadata, and style without dispatch',()=>{
 for(const args of [[{source:7},3,'a'.repeat(32)],[{source:'C',extra:true},3,'a'.repeat(32)],[{source:'C',style:{color:0}},3,'a'.repeat(32)],[{source:'C'},-1,'a'.repeat(32)],[{source:'C'},3,'bad']])assert.throws(()=>captureImportRequest(...args));
});
test('request opts in, preserves exact source, reads body before admission and rejects stale bodies',async()=>{
 let current=true,stale=false,body,reads=0;const c=capture();
 const job={revision:3,signal:new AbortController().signal,assertCurrent(){if(!current)throw Error('stale');},wait:async p=>p};
 const fetchImpl=async(url,options)=>{assert.equal(url,'/api/import');body=JSON.parse(options.body);return {ok:true,text:async()=>{reads++;if(stale)current=false;return JSON.stringify(envelope(c));}};};
 await requestImport({source:c.source},{job,fetchImpl,requestId:c.requestId});assert.equal(reads,1);assert.equal(body.source,c.source);assert.equal(body.sourceBinding,'nivram-import/1');assert.deepEqual(body.style,DEFAULT_STYLE);
 stale=true;await assert.rejects(requestImport({source:c.source},{job,fetchImpl,requestId:c.requestId}),/stale/);
});
test('every asynchronous digest checks currency, including exact UTF8 source digest',async()=>{
 for(const stopAt of [1,2,3]){let current=true,count=0;const c=capture();const cryptoImpl={subtle:{digest:async(...args)=>{const result=await webcrypto.subtle.digest(...args);if(++count===stopAt)current=false;return result;}}};await assert.rejects(admitImportResponse(envelope(c),c,{cryptoImpl,assertCurrent(){if(!current)throw Error('stale digest');}}),/stale digest/);}
 const controller=new AbortController();controller.abort(Error('cancelled'));let calls=0;await assert.rejects(requestImport({source:'C'},{revision:3,signal:controller.signal,fetchImpl:()=>{calls++;}}),/cancelled/);assert.equal(calls,0);
});
test('both modern callers use strict import helper and full scene admission stays mandatory',async()=>{
 const read=p=>fs.readFileSync(new URL('../public/'+p,import.meta.url),'utf8');assert.match(read('app.mjs'),/action==='import'.*requestImport/);assert.match(read('modules/image-import.mjs'),/importSource:\(source,style,job\)=>requestImport/);
 const c=capture(),r=await admitImportResponse(envelope(c),c);assert.throws(()=>prepareRender(r,{schema:'nivram/1',graph}));
});
test('malformed Unicode and oversized exact UTF8 source fail before dispatch',async()=>{
 for(const source of ['\ud800','C\udfff','\ud800C','µ'.repeat(2_000_001),'']){let calls=0;await assert.rejects(requestImport({source},{revision:0,fetchImpl:()=>{calls++;}}));assert.equal(calls,0);}
 assert.doesNotThrow(()=>capture('😀'));assert.doesNotThrow(()=>capture('C'.repeat(4_000_000)));
});
test('typed HTTP errors never fall back or admit a graph',async()=>{
 let calls=0;await assert.rejects(requestImport({source:'C'},{revision:0,fetchImpl:async()=>{calls++;return {ok:false,text:async()=>JSON.stringify({error:'controlled import failure',diagnostic:{code:'test'}})};}}),/controlled import failure/);assert.equal(calls,1);
});
import {createJobController} from '../public/modules/jobs.mjs';
import {createDocumentStore} from '../public/modules/document.mjs';
test('cancel, replacement, timeout and edit-undo ABA cannot admit late import responses',async()=>{
 for(const mode of ['cancel','replace','timeout','ABA']){
  const store=createDocumentStore({schema:'nivram/1',graph}),jobs=createJobController(()=>store.getSnapshot().revision,{timeoutMs:mode==='timeout'?10:1000}),job=jobs.begin('import');let release,replacement;
  const pending=requestImport({source:'C'},{job,requestId:'a'.repeat(32),fetchImpl:()=>new Promise(resolve=>{release=resolve;})});
  if(mode==='cancel')job.cancel();if(mode==='replace')replacement=jobs.begin('import');
  if(mode==='ABA'){store.commit({...store.getSnapshot().document,export:{...store.getSnapshot().document.export,width:1000}});store.commit(store.historyTarget(),{mode:'undo'});release({ok:true,text:async()=>{throw Error('stale body must not read');}});}
  await assert.rejects(pending,/cancelled|newer operation|timed out|document changed/);
  if(mode!=='ABA')release({ok:true,text:async()=>{throw Error('late body must not read');}});job.finish();replacement?.finish();
 }
});
test('synthetic UI fixture supports modern receipt without changing malformed scene payloads',async()=>{
 const {boundImportFixture}=await import('./bound-import-fixture.mjs');const c=capture('C'),response=boundImportFixture({...c,sourceBinding:'nivram-import/1'},{graph,scene:{svg:'<svg invalid'}});
 const admitted=await admitImportResponse(response,c);assert.equal(admitted.scene.svg,'<svg invalid');assert.throws(()=>prepareRender(admitted,{schema:'nivram/1',graph}));
});
test('witness hashing preserves producer Unicode escapes and numeric spellings',async()=>{
 const c=capture(' methane '),g={atoms:[{id:'µ',symbol:'C',x:1,y:0}],bonds:[]},properties=propertyReport('methane');
 const witness='{"atoms":[{"id":"\\u00b5","symbol":"C","x":1.0,"y":0.0}],"bonds":[]}';properties.inputIdentity.graphHash=sha(witness);
 const r={...envelope(c),graph:g,outputGraphJson:witness,outputIdentity:structuredClone(properties.inputIdentity),scene:{properties,smiles:'C',style:{...c.style}}};await admitImportResponse(r,c);
 const reordered={...r,source:{bytes:r.source.bytes,sha256:r.source.sha256},style:Object.fromEntries(Object.entries(r.style).reverse())};await admitImportResponse(reordered,c);
 r.outputGraphJson=JSON.stringify(g);await assert.rejects(admitImportResponse(r,c));
});
