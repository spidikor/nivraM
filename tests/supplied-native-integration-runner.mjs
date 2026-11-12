// Controlled DOM plus actual local authenticated HTTP/native worker and PNG bytes.
// No browser, installation, or native UI is launched.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import {mountSupplied3d} from '../public/modules/supplied-3d-viewer.mjs';
import {parseSuppliedMol} from '../public/modules/supplied-3d.mjs';
import {validateSuppliedResult} from '../public/modules/supplied-validation.mjs';
import {createThreeDRecord,validateThreeDRecord} from '../public/modules/three-d-record.mjs';
import {exportThreeDPng,sha256} from '../public/modules/three-d-png.mjs';
import {stampPng,extractThreeDRecord} from '../public/modules/png-metadata.mjs';
const data=JSON.parse(readFileSync(0,'utf8')),enc=new TextEncoder();
class Node {constructor(){this.handlers={};this.children=[];this.attrs={};this.disabled=true;this.classList={toggle(){}};this.dataset={};this.files=[];this.textContent='';}addEventListener(t,f){(this.handlers[t]??=[]).push(f);}removeEventListener(t,f){this.handlers[t]=this.handlers[t].filter(x=>x!==f);}async fire(t,e={}){for(const f of this.handlers[t]||[])await f(e);}append(...v){this.children.push(...v);}replaceChildren(...v){this.children=v;}setAttribute(k,v){this.attrs[k]=v;}querySelectorAll(){return [];}focus(){}showModal(){this.open=true;}close(){this.open=false;this.fire('close');}setPointerCapture(){}}
const ids=['dialog','open','close','file','scene','status','atoms','bonds','provenance','export','export-provenance','export-png','png-file','cancel'];
const nodes=Object.fromEntries(ids.map(id=>['supplied-3d-'+id,new Node()]));
const dom={getElementById:id=>nodes[id],createElement:()=>new Node(),createElementNS:()=>new Node()};
const original2d={unchanged:true},downloads=[],calls=[],records=[],pngs=[],invalidRecords=[];
const defer=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};};
let gate=null,lastResult=null,lastRequest=null,corrupt=false;
const originalPng=()=>new Blob([Buffer.from(data.png,'base64')],{type:'image/png'});
async function request(action,payload){
 calls.push({action,payload});const held=action==='supplied-validation'?gate:null;if(held)gate=null;
 const response=await fetch(data.origin+'/api/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':data.token},body:JSON.stringify(payload)});
 const result=await response.json();if(!response.ok)throw Error(result.error||'Request rejected');
 if(action==='supplied-validation'){lastResult=result;lastRequest=payload;if(held){held.started.resolve();await held.release.promise;}if(corrupt){result.scene.atoms[0].x+=1;corrupt=false;}}
 return result;
}
const ctl=mountSupplied3d({dom,cryptoApi:webcrypto,getDocument:()=>original2d,request,download:(...v)=>downloads.push(v),sceneSnapshot:()=>'<svg/>',pngExport:s=>exportThreeDPng(s,{rasterize:async()=>originalPng(),version:async()=>data.version})});
ctl.open();
const counts=()=>calls.filter(c=>c.action==='supplied-validation').length;
for(const source of data.sources){
 const before=counts();assert(await ctl.loadBytes(enc.encode(source)));assert.equal(counts(),before+1);
 assert.equal(ctl.getState().model.sourceText,source);assert.equal(ctl.getState().provenance.schema,'nivram-supplied-3d-view/2');
 assert.equal(ctl.getState().provenance.generated,false);
 for(const [i,a] of ctl.getState().model.atoms.entries()){assert.equal(nodes['supplied-3d-atoms'].children[i].children[9].textContent,String(a.declarations?.CFG??'not declared'));assert.equal(nodes['supplied-3d-atoms'].children[i].children[10].textContent,String(a.declarations?.VAL??'not declared'));}
 assert.match(nodes['supplied-3d-status'].textContent,/parse-consistency/);
 await nodes['supplied-3d-export'].fire('click');assert.deepEqual(downloads.at(-1)[0],enc.encode(source));
 assert(await ctl.exportPng());const blob=downloads.at(-1)[0],record=await extractThreeDRecord(blob);
 assert.equal(record.schema,'nivram-png-coordinates/4');assert.equal(record.molText,source);records.push(record);pngs.push(Buffer.from(await blob.arrayBuffer()).toString('base64'));
 const old=ctl.getState().model;
 assert(await ctl.loadPng(new Uint8Array(await blob.arrayBuffer())));assert.equal(counts(),before+2);assert.notEqual(ctl.getState().model,old);
 assert.equal(ctl.getState().provenance.validation.schema,'nivram-supplied-3d-validation/1');assert.equal(ctl.getState().model.kind,'recovered_coordinates');
 assert(await ctl.loadPng(new Uint8Array(await blob.arrayBuffer())));assert.equal(counts(),before+3,'every reopen calls native validation');
 await nodes['supplied-3d-export'].fire('click');assert.deepEqual(downloads.at(-1)[0],enc.encode(source));
 assert(await ctl.exportPng());const repeated=await extractThreeDRecord(downloads.at(-1)[0]);assert.deepEqual(repeated,record);
}
// Full front-end schema/coordinate/request binding rejects altered server responses.
const preflight=parseSuppliedMol(enc.encode(lastRequest.molText),{allowNativeDeclarations:true});
for(const change of [r=>r.clientId='0'.repeat(32),r=>r.scene.sourceText+='\n',r=>r.scene.atoms[0].x+=1,r=>r.scene.atoms[0].id=true,r=>r.validation.generationClaimsVerified=true,r=>r.unexpected=1]){const bad=structuredClone(lastResult);change(bad);assert.throws(()=>validateSuppliedResult(bad,{model:preflight,...lastRequest}));}
assert.throws(()=>validateSuppliedResult(lastResult,{model:preflight,...lastRequest,expiresAt:1}));
const accepted=ctl.getState().model;
assert.equal(await ctl.loadBytes(enc.encode(data.contradiction)),false);assert.equal(ctl.getState().model,accepted);
corrupt=true;assert.equal(await ctl.loadBytes(enc.encode(data.sources[0])),false);assert.equal(ctl.getState().model,accepted);
// Forge a syntactically valid historical receipt with a contradictory exact MOL.
async function changedRecord(record,source,identity){const r=structuredClone(record),p=JSON.parse(r.provenanceJson);r.molText=source;r.molBytes=enc.encode(source).length;r.molSha256=await sha256(source);p.sourceSha256=r.molSha256;p.sourceBytes=r.molBytes;if(identity)p.validation.engineDerivedIsomericSmiles=identity;r.provenanceJson=JSON.stringify(p);r.provenanceSha256=await sha256(r.provenanceJson);return r;}
const forged=await changedRecord(records[0],data.contradiction);
await validateThreeDRecord(forged); // structural acceptance must never become current trust
const forgedPng=await stampPng(originalPng(),{coordinateRecord:forged,version:data.version});
const n=counts();assert.equal(await ctl.loadPng(new Uint8Array(await forgedPng.arrayBuffer())),false);assert.equal(counts(),n+1);assert.equal(ctl.getState().model,accepted);
const oldIdentity=await changedRecord(records[0],records[0].molText,'C');
const oldIdentityPng=await stampPng(originalPng(),{coordinateRecord:oldIdentity,version:data.version});
assert(await ctl.loadPng(new Uint8Array(await oldIdentityPng.arrayBuffer())));
assert.equal(ctl.getState().provenance.historicalProvenance.validation.engineDerivedIsomericSmiles,'C');assert.notEqual(ctl.getState().provenance.validation.engineDerivedIsomericSmiles,'C');
for(const schema of ['nivram-png-coordinates/2','nivram-png-coordinates/3']){
 const bad=structuredClone(records[0]);bad.schema=schema;const p=JSON.parse(bad.provenanceJson);delete p.validation;p.schema='nivram-supplied-3d-view/1';bad.provenanceJson=JSON.stringify(p);bad.provenanceSha256=await sha256(bad.provenanceJson);await assert.rejects(validateThreeDRecord(bad));invalidRecords.push(bad);
}
for(const change of [p=>p.validation.generationClaimsVerified=true,p=>p.validation.declarationsPreserved=1,p=>p.validation.engineDerivedIsomericSmiles='',p=>p.extra=true]){
 const bad=structuredClone(records[0]),p=JSON.parse(bad.provenanceJson);change(p);bad.provenanceJson=JSON.stringify(p);bad.provenanceSha256=await sha256(bad.provenanceJson);await assert.rejects(validateThreeDRecord(bad));invalidRecords.push(bad);
}
// Race tests deliberately ignore AbortSignal in adapter: late successes must not adopt.
function block(){gate={started:defer(),release:defer()};return gate;}
let held=block(),pending=ctl.loadBytes(enc.encode(data.sources[0]));await held.started.promise;
const previous=ctl.getState().model;assert(await ctl.exportPng());assert.equal((await extractThreeDRecord(downloads.at(-1)[0])).molText,previous.sourceText,'pending load exports only accepted scene');
assert(await ctl.loadBytes(enc.encode(data.plain)));const next=ctl.getState().model;held.release.resolve();assert.equal(await pending,false);assert.equal(ctl.getState().model,next);
held=block();pending=ctl.loadBytes(enc.encode(data.sources[0]));await held.started.promise;await nodes['supplied-3d-cancel'].fire('click');held.release.resolve();assert.equal(await pending,false);assert.equal(ctl.getState().model,next);
held=block();pending=ctl.loadBytes(enc.encode(data.sources[0]));await held.started.promise;
assert.equal(await ctl.loadBytes(new Uint8Array([1,2,3])),false);held.release.resolve();assert.equal(await pending,false);assert.equal(ctl.getState().model,next);
held=block();pending=ctl.loadBytes(enc.encode(data.sources[0]));await held.started.promise;
const realNow=Date.now;Date.now=()=>realNow()+30000;try{held.release.resolve();assert.equal(await pending,false);}finally{Date.now=realNow;}assert.equal(ctl.getState().model,next);
const realTimeout=globalThis.setTimeout;let expire;
globalThis.setTimeout=(fn,delay,...args)=>{if(delay===25000){expire=fn;return realTimeout(()=>{},25000);}return realTimeout(fn,delay,...args);};
held=block();pending=ctl.loadBytes(enc.encode(data.sources[0]));globalThis.setTimeout=realTimeout;await held.started.promise;expire();held.release.resolve();assert.equal(await pending,false);assert.equal(ctl.getState().model,next);
held=block();pending=ctl.loadBytes(enc.encode(data.sources[0]));await held.started.promise;ctl.close();held.release.resolve();assert.equal(await pending,false);assert.equal(ctl.getState().model,next);ctl.open();
held=block();pending=ctl.loadPng(new Uint8Array(Buffer.from(pngs[0],'base64')));await held.started.promise;ctl.destroy();held.release.resolve();assert.equal(await pending,false);assert.equal(ctl.getState().model,next);
assert(calls.some(c=>c.action==='supplied-validation/cancel'));assert.deepEqual(original2d,{unchanged:true});
console.log(JSON.stringify({records,pngs,forged,invalidRecords,requests:counts(),cancellations:calls.filter(c=>c.action.endsWith('/cancel')).length}));
