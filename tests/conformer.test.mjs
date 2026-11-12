import assert from 'node:assert/strict';
import {webcrypto,createHash} from 'node:crypto';
import {createConformerController,validateGeneratedResult} from '../public/modules/conformer.mjs';
let revision=3;const graph={atoms:[{id:'c1',symbol:'C',x:0,y:0}],bonds:[]},before=JSON.stringify(graph),listeners=new Set();
const doc={ready:Promise.resolve(),getSnapshot:()=>({revision,document:{graph}}),subscribe:f=>{listeners.add(f);return ()=>listeners.delete(f)}};
const response=p=>({sourceHash:p.sourceHash,sourceRevision:p.sourceRevision,sourceContextHash:p.sourceContextHash,scene:{kind:'generated_coordinates',atoms:[{id:1,element:'C',x:0,y:0,z:0,sourceId:'c1'}],bonds:[],sourceText:'generated\nM  END\n'},provenance:{sourceContext:JSON.parse(p.sourceContextText),sourceContextSha256:p.sourceContextHash,generated:true,origin:'generated_coordinates',sourceRevision:p.sourceRevision,sourceSha256:p.sourceHash,outputSha256:createHash('sha256').update('generated\nM  END\n').digest('hex')}});
let calls=[],results=[],states=[],invalidations=[],pendingResolve=null,hold=false;
const ctl=createConformerController({getDocument:()=>doc,cryptoApi:webcrypto,onState:(...a)=>states.push(a),onResult:r=>results.push(r),onInvalidate:r=>invalidations.push(r),request:async(path,payload)=>{calls.push({path,payload});if(path.endsWith('/cancel'))return {};if(hold)return new Promise(r=>{pendingResolve=()=>r(response(payload))});return response(payload);}});
assert.equal(await ctl.generate(),true);assert.equal(results.length,1);assert.equal(JSON.stringify(graph),before);
const payload=calls[0].payload;assert.equal(payload.graphText,before);assert.equal(payload.sourceHash,createHash('sha256').update(before).digest('hex'));assert.equal(payload.sourceRevision,3);assert.match(payload.clientId,/^[a-f0-9]{32}$/);assert(!('graph' in payload));
revision++;for(const f of listeners)f(doc.getSnapshot());assert.equal(invalidations.length,1);
const tick=()=>new Promise(r=>setTimeout(r,0));async function untilPending(){for(let i=0;i<100&&!pendingResolve;i++)await tick();assert(pendingResolve);}
hold=true;let pending=ctl.generate();await untilPending();const staleResolve=pendingResolve;pendingResolve=null;revision++;for(const f of listeners)f(doc.getSnapshot());staleResolve();assert.equal(await pending,false);assert.equal(results.length,1);assert(calls.some(c=>c.path==='conformer/cancel'));
pending=ctl.generate();await untilPending();const closeResolve=pendingResolve;pendingResolve=null;ctl.invalidate('closed');closeResolve();assert.equal(await pending,false);assert.equal(results.length,1);
hold=false;assert.equal(await ctl.generate(),true);assert.equal(results.length,2);
for(const alter of [r=>r.sourceRevision++,r=>r.sourceHash='0'.repeat(64),r=>r.scene.kind='supplied_coordinates',r=>r.scene.atoms[0].z=NaN,r=>r.provenance.generated=false,r=>r.provenance.origin='supplied_coordinates',r=>r.provenance.sourceRevision++,r=>r.scene.bonds.push({id:1,a:1,b:2,order:1})]){const r=response(payload);alter(r);assert.throws(()=>validateGeneratedResult(r,payload));}
ctl.destroy();assert.equal(listeners.size,0);
const badStates=[],badOutputs=[];const bad=createConformerController({getDocument:()=>doc,cryptoApi:webcrypto,onState:(...a)=>badStates.push(a),onResult:r=>badOutputs.push(r),request:async(_path,p)=>{const r=response(p);r.provenance.outputSha256='0'.repeat(64);return r;}});assert.equal(await bad.generate(),false);assert.equal(badOutputs.length,0);assert.match(badStates.at(-1)[1],/output provenance/);bad.destroy();
console.log('PASS generated controller: exact graph hash, nonmutation, generation transport, response binding/validation, edit/close cancellation, late response fencing and unsubscribe. Synthetic scene only; not chemistry or visual qualification.');
