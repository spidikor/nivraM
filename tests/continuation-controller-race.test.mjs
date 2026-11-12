import assert from 'node:assert/strict';
import {webcrypto,createHash} from 'node:crypto';
import {mountImageTo3d} from '../public/modules/image-to-3d.mjs';
import {sourceBinding} from '../public/modules/conformer-source.mjs';
import {createConformerController} from '../public/modules/conformer.mjs';
class Node{handlers={};addEventListener(t,f){(this.handlers[t]??=[]).push(f);}removeEventListener(t,f){this.handlers[t]=this.handlers[t].filter(x=>x!==f);}fire(t,e={}){return Promise.all((this.handlers[t]??[]).map(f=>f(e)));}}
const hash=s=>createHash('sha256').update(s).digest('hex');
for(const stage of ['ready','graph-hash','context-hash','output-hash','none'])for(const kind of ['graph','context','style']){
 let snapshot={revision:1,document:{graph:{atoms:[{id:'reviewed',symbol:'C',x:0,y:0}],bonds:[]}}};
 const listeners=new Set(),sent=[],results=[];let digests=0;
 const doc={ready:Promise.resolve(),getSnapshot:()=>snapshot,subscribe:f=>{listeners.add(f);return()=>listeners.delete(f);}};
 const edit=()=>{snapshot={revision:2,document:{...snapshot.document,...(kind==='graph'?{graph:{atoms:[{id:'later',symbol:'O',x:0,y:0}],bonds:[]}}:kind==='context'?{analysis:{imageImport:{kind:'png-metadata-component-reconstruction',sourceSha256:'a'.repeat(64)}}}:{style:{color:false}})}};for(const f of listeners)f(snapshot);};
 const cryptoApi={getRandomValues:b=>webcrypto.getRandomValues(b),subtle:{digest:async(...args)=>{digests++;const result=await webcrypto.subtle.digest(...args);if(digests===({'graph-hash':1,'context-hash':2,'output-hash':3}[stage]))edit();return result;}}};
 const controller=createConformerController({getDocument:()=>doc,cryptoApi,onResult:r=>results.push(r),request:async(route,p)=>{
  if(route!=='conformer')return {};
  sent.push(p);const sourceText='synthetic\nM  END\n';
  return {sourceHash:p.sourceHash,sourceRevision:p.sourceRevision,sourceContextHash:p.sourceContextHash,scene:{kind:'generated_coordinates',atoms:[{id:1,element:'C',x:0,y:0,z:0}],bonds:[],sourceText},provenance:{generated:true,origin:'generated_coordinates',sourceRevision:p.sourceRevision,sourceSha256:p.sourceHash,sourceContextSha256:p.sourceContextHash,sourceContext:JSON.parse(p.sourceContextText),outputSha256:hash(sourceText)}};
 }});
 const events=new Node(),button=new Node();const bridge=mountImageTo3d({events,button,getDocument:()=>doc,open(){},generate:options=>controller.generate(options)});
 await events.fire('nivram-image-accepted',{detail:sourceBinding(snapshot)});
 if(stage==='ready')queueMicrotask(edit);
 await button.fire('click');
 assert.equal(sent.length,['none','output-hash'].includes(stage)?1:0,`${stage}/${kind} request fence`);
 assert.equal(results.length,stage==='none'?1:0,`${stage}/${kind} adoption fence`);
 for(const p of sent){assert.equal(p.sourceRevision,1);assert.equal(JSON.parse(p.graphText).atoms[0].id,'reviewed');}
 if(stage==='ready'){await controller.generate();assert.equal(sent.at(-1).sourceRevision,2,'ordinary Generate still uses current drawing');}
 bridge.destroy();controller.destroy();assert.equal(listeners.size,0);
}
console.log('PASS actual bridge/controller continuation fences at readiness, graph hash, context hash and output hash for graph/context/style revisions; unchanged receipt generates, ordinary generation captures current drawing. Synthetic transport only.');
