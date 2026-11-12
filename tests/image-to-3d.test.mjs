import assert from 'node:assert/strict';
import {mountImageTo3d} from '../public/modules/image-to-3d.mjs';
import {sourceBinding} from '../public/modules/conformer-source.mjs';
class Node{constructor(){this.handlers={};}addEventListener(t,f){(this.handlers[t]??=[]).push(f);}removeEventListener(t,f){this.handlers[t]=this.handlers[t].filter(x=>x!==f);}async fire(t,e={}){for(const f of this.handlers[t]||[])await f(e);}}
const events=new Node(),button=new Node();let revision=1,graph={atoms:[{id:'a'}],bonds:[]},context=null,listeners=new Set(),opened=0,generated=0,allowed=true;
const doc={getSnapshot:()=>({revision,document:{graph,analysis:context?{imageImport:context}:{}}}),subscribe:fn=>{listeners.add(fn);return ()=>listeners.delete(fn);}};
let current=doc;const ctl=mountImageTo3d({events,button,getDocument:()=>current,open:()=>opened++,generate:()=>{generated++;return true;},canOpen:()=>allowed});
const offer=()=>events.fire('nivram-image-accepted',{detail:sourceBinding(doc.getSnapshot())});
assert(button.hidden);await events.fire('nivram-image-accepted');assert(button.hidden);assert.equal(generated,0);
await offer();assert(!button.hidden);assert.equal(generated,0);allowed=false;await button.fire('click');assert(!button.hidden);assert.equal(generated,0);allowed=true;await button.fire('click');assert.equal(generated,1);assert.equal(opened,1);assert(button.hidden);
const old=sourceBinding(doc.getSnapshot());revision++;await events.fire('nivram-image-accepted',{detail:old});assert(button.hidden); // acceptance-to-UI race
await offer();revision++;for(const fn of [...listeners])fn(doc.getSnapshot());assert(button.hidden);
await offer();graph={atoms:[{id:'other'}],bonds:[]};await button.fire('click');assert(button.hidden);assert.equal(generated,1); // missed event still checked
await offer();context={kind:'png-metadata-component-reconstruction',sourceSha256:'a'.repeat(64)};await button.fire('click');assert.equal(generated,1);
await offer();await events.fire('nivram-close-review');assert(button.hidden);await offer();assert(button.hidden);await events.fire('nivram-close-review-ended');await offer();assert(!button.hidden);await events.fire('pagehide');assert(button.hidden);
await offer();current={...doc};await button.fire('click');assert(button.hidden);assert.equal(generated,1);ctl.destroy();assert.equal(listeners.size,0);
console.log('PASS optional continuation: no automatic generation, exact receipt binding, revision/graph/context/document changes, modal/close/pagehide guards and cleanup. Controlled events only.');
