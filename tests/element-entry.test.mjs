import test from 'node:test';
import assert from 'node:assert/strict';
import {createElementEntry} from '../public/modules/element-entry.mjs';
function fixture({slow=false}={}){
  let time=0,revision=0,target={id:'a',x:10,y:20},busy=false,timer,resolve;
  const applied=[],shown=[],ended=[];
  const entry=createElementEntry({getTarget:()=>target,getRevision:()=>revision,isBusy:()=>busy,beginGroup:()=>({}),endGroup:g=>ended.push(g),preview:(id,s)=>shown.push([id,s]),clearPreview:()=>{},now:()=>time,schedule:f=>(timer=f,1),unschedule:()=>{},apply:async(s,g,id)=>{busy=true;if(slow)await new Promise(r=>resolve=r);applied.push([id,s,g]);revision++;busy=false;return true;}});
  return {entry,applied,shown,ended,tick:v=>time=v,move:v=>target=v,expire:()=>timer(),release:()=>resolve(),idle:()=>new Promise(r=>setImmediate(r))};
}
test('first character is displayed synchronously; eligible continuation survives slow rendering',async()=>{
  const f=fixture({slow:true});assert.equal(f.entry.input('c'),true);assert.deepEqual(f.shown,[['a','C']]);assert.equal(f.applied.length,0);
  f.tick(499);assert.equal(f.entry.input('L'),true);assert.deepEqual(f.shown.at(-1),['a','Cl']);f.release();await f.idle();f.release();await f.idle();
  assert.deepEqual(f.applied.map(v=>v.slice(0,2)),[['a','C'],['a','Cl']]);assert.equal(f.applied[0][2],f.applied[1][2]);
});
test('500ms is outside the old window and prefix-only letters do not mutate',async()=>{
  const f=fixture();f.entry.input('C');await f.idle();f.tick(500);f.entry.input('L');assert.equal(f.shown.length,1);f.tick(501);f.entry.input('i');await f.idle();assert.deepEqual(f.shown,[['a','C'],['a','Li']]);assert.notEqual(f.applied[0][2],f.applied[1][2]);
});
test('invalid continuation retains last valid edit; expired prefix does nothing',async()=>{
  const f=fixture();f.entry.input('C');await f.idle();f.entry.input('z');await f.idle();assert.equal(f.applied.length,1);f.entry.input('L');f.expire();await f.idle();assert.equal(f.applied.length,1);
});
test('moving pointer or changing atom ends extension and cannot retarget queued edit',async()=>{
  const f=fixture();f.entry.input('C');await f.idle();f.move({id:'b',x:11,y:20});f.entry.input('L');await f.idle();assert.equal(f.applied.length,1);f.entry.input('i');await f.idle();assert.deepEqual(f.applied.at(-1).slice(0,2),['b','Li']);
});
test('validation failure drops queued continuation',async()=>{
  let release;const seen=[];const e=createElementEntry({getTarget:()=>({id:'a',x:0,y:0}),getRevision:()=>0,isBusy:()=>false,beginGroup:()=>({}),endGroup:()=>{},preview:()=>{},clearPreview:()=>{},apply:async s=>{seen.push(s);await new Promise(r=>release=r);return false;}});
  e.input('C');e.input('L');release();await new Promise(r=>setImmediate(r));assert.deepEqual(seen,['C']);assert.equal(e.active(),false);
});
