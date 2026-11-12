import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {validateStereoInspection,requestStereoInspection,mountStereoInspector} from '../public/modules/stereo-inspector.mjs';
const graph={atoms:[{id:'a',symbol:'C'},{id:'b',symbol:'C'}],bonds:[{id:'x',begin:'a',end:'b',order:2}]};
const request={graph,documentRevision:1,requestId:'a'.repeat(32)};
const result=()=>({schema:'nivram-stereo-inspector/1',status:'inspected',requestId:request.requestId,inputIdentity:{graphHash:'0'.repeat(64),structureHash:'1'.repeat(64),smiles:'CC',documentRevision:1},engine:{name:'RDKit',version:'2025.09.4',method:'FindPotentialStereo + rdCIPLabeler.AssignCIPLabels'},sites:[{kind:'atom',id:'a',index:0,status:'assigned',label:'R',reason:'specified'},{kind:'bond',id:'x',index:0,status:'unresolved',label:null,reason:'unknown'}],unsupportedCount:0,transformations:[],warnings:['Engine-qualified.']});
test('valid assigned and unresolved typed results are copied',()=>{const r=result();assert.deepEqual(validateStereoInspection(r,request),r);assert.notEqual(validateStereoInspection(r,request),r);});
test('rejects stale, fabricated, wrongly mapped or misleading labels',()=>{
  for(const mutate of [r=>r.requestId='b'.repeat(32),r=>r.inputIdentity.documentRevision=2,r=>r.sites[0].id='unknown',r=>r.sites[0].index=1,r=>r.sites[0].label='r',r=>r.sites[1].label='E',r=>r.sites[1].reason='specified',r=>r.sites.push({...r.sites[0]}),r=>r.unsupportedCount=1.1,r=>r.transformations.push('cleanup'),r=>r.warnings.push({html:'x'}),r=>r.extra=true]){const r=result();mutate(r);assert.throws(()=>validateStereoInspection(r,request));}
});
test('unsupported cumulene and pseudoasymmetric labels stay unassigned',()=>{for(const reason of ['unsupported_type','unsupported_label']){const r=result();Object.assign(r.sites[0],{status:'unsupported',reason,label:null});assert.doesNotThrow(()=>validateStereoInspection(r,request));}});
test('request uses local authenticated endpoint and exact captured graph',async()=>{
  let call;const response=await requestStereoInspection(request,{token:'test',signal:new AbortController().signal,fetcher:async(...args)=>{call=args;return {ok:true,text:async()=>JSON.stringify(result())};}});
  assert.equal(call[0],'/api/stereo-inspector');assert.equal(call[1].headers['X-Nivram-Token'],'test');assert.deepEqual(JSON.parse(call[1].body),request);assert.deepEqual(response,result());
});
test('failed and oversized transport cannot create result',async()=>{for(const response of [{ok:false,text:async()=>'{"error":"reject"}'},{ok:true,text:async()=>'x'.repeat(1000001)}])await assert.rejects(()=>requestStereoInspection(request,{token:'test',fetcher:async()=>response}));});
class Node extends EventTarget{constructor(){super();this.children=[];this.textContent='';this.hidden=false;this.disabled=false;}append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;}setAttribute(){} }
function harness(fetcher){
  const nodes=new Map(['inspect-stereo','cancel-stereo-inspector','stereo-inspector-status','stereo-inspector-list','stereo-inspector-details'].map(k=>[k,new Node()]));
  let revision=1,listener,selected=[];const doc={graph},before=JSON.stringify(doc);
  const editor={getSnapshot:()=>({revision,document:doc}),subscribe:fn=>{listener=fn;return()=>{listener=null;};}};
  const events=new EventTarget();const instance=mountStereoInspector({dom:{getElementById:k=>nodes.get(k),createElement:()=>new Node()},events,editor,token:'test',makeId:()=>request.requestId,fetcher,timeoutMs:100});
  return {instance,events,nodes,doc,before,change(){revision++;listener?.(editor.getSnapshot());}};
}
const ok=async()=>({ok:true,text:async()=>JSON.stringify(result())});
test('mount renders inert text, clears on edits and never mutates document',async()=>{const h=harness(ok);try{await h.instance.run();assert.equal(h.nodes.get('stereo-inspector-list').children.length,2);assert.match(h.nodes.get('stereo-inspector-status').textContent,/1 labeled; 1 unresolved/);h.change();assert.equal(h.nodes.get('stereo-inspector-list').children.length,0);assert.equal(JSON.stringify(h.doc),h.before);}finally{h.instance.destroy();}});
test('late completion after edit is discarded',async()=>{let resolve;const h=harness(()=>new Promise(r=>resolve=r));const running=h.instance.run();h.change();resolve(await ok());await running;assert.equal(h.nodes.get('stereo-inspector-list').children.length,0);h.instance.destroy();});
test('cancel and closing review discard pending work and clear old reports',async()=>{for(const event of ['cancel','nivram-close-review','pagehide']){let resolve;const h=harness(()=>new Promise(r=>resolve=r));const running=h.instance.run();if(event==='cancel')h.nodes.get('cancel-stereo-inspector').dispatchEvent(new Event('click'));else h.events.dispatchEvent(new Event(event));resolve(await ok());await running;assert.equal(h.nodes.get('stereo-inspector-list').children.length,0);if(event==='nivram-close-review')assert.equal(h.nodes.get('inspect-stereo').disabled,true);h.instance.destroy();}});
test('timeout contains an unresponsive fetch without applying anything',async()=>{const h=harness(()=>new Promise(()=>{}));await h.instance.run();assert.match(h.nodes.get('stereo-inspector-status').textContent,/timed out/);assert.equal(h.nodes.get('stereo-inspector-list').children.length,0);h.instance.destroy();});
test('UI integration remains read-only and uses actual source IDs',()=>{const app=fs.readFileSync(new URL('../public/app.mjs',import.meta.url),'utf8'),html=fs.readFileSync(new URL('../public/index.html',import.meta.url),'utf8');assert.match(app,/mountStereoInspector\(\{dom:document,events:window,editor:documentStore/);assert.match(app,/busy\|\|documentStore.getSnapshot\(\).revision!==revision/);for(const id of ['inspect-stereo','stereo-inspector-list','stereo-inspector-status'])assert.ok(html.includes(`id="${id}"`));});
