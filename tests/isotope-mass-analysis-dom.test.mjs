import {propertyEnvelope} from './property-response-fixtures.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {MASS_MESSAGE} from '../public/modules/mass-contract.mjs';
// Controlled DOM integration of the actual analysis startup/render path, not a real browser.
class Node extends EventTarget{constructor(){super();this.textContent='';this.value='';this.children=[];this.open=false;}replaceChildren(...c){this.children=c;}setAttribute(){}removeAttribute(){}close(){this.open=false;} }
const tick=()=>new Promise(r=>setTimeout(r,0));
test('analysis DOM displays both unavailable masses, keeps LogP and ignores obsolete reply',async()=>{
  const old={window:globalThis.window,document:globalThis.document,fetch:globalThis.fetch};
  const nodes=new Map(),find=k=>{if(!nodes.has(k))nodes.set(k,new Node());return nodes.get(k);};
  const view=new EventTarget();view.MutationObserver=class{observe(){}disconnect(){}};
  let revision=1;const listeners=new Set(),pending=[],graph={atoms:[{id:'a',symbol:'C',isotope:400,x:0,y:0}],bonds:[]};
  view.nivramDocument={ready:Promise.resolve(),getSnapshot:()=>({revision,document:{graph}}),subscribe:f=>{listeners.add(f);return()=>listeners.delete(f);}};
  globalThis.window=view;globalThis.document={getElementById:find,defaultView:view,documentElement:{dataset:{}},querySelector:()=>({content:'test'})};
  globalThis.fetch=(url,options)=>url==='/api/properties'?new Promise(resolve=>pending.push({resolve,body:JSON.parse(options.body)})):Promise.resolve({ok:false});
  const reply=(i,unknown)=>pending[i].resolve({ok:true,json:async()=>propertyEnvelope(pending[i].body,unknown?'missing':'methane')});
  try{
    await import('../public/modules/analysis.mjs?isotope-dom');await tick();reply(0,true);await tick();
    assert.equal(find('property-mass').textContent,'Unavailable g/mol');assert.equal(find('property-exact-mass').textContent,'Unavailable Da');assert.equal(find('property-logp').textContent,'0.636');assert.equal(find('property-smiles').value,'[400CH4]');assert.match(find('property-provenance').textContent,/isotope mass table/);
    view.dispatchEvent(new Event('nivram-close-review-ended'));await tick();
    revision=2;for(const f of listeners)f({revision,document:{graph}});
    assert.equal(find('property-mass').textContent,'Updating…');reply(1,true);await tick();assert.equal(find('property-mass').textContent,'Updating…');
    await new Promise(r=>setTimeout(r,370));reply(2,false);await tick();assert.equal(find('property-mass').textContent,'16.0430 g/mol');assert.doesNotMatch(find('property-provenance').textContent,/isotope mass table/);
    view.dispatchEvent(new Event('nivram-close-review'));view.dispatchEvent(new Event('pagehide'));
  }finally{Object.assign(globalThis,old);}
});
