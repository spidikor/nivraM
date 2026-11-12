import test from 'node:test';
import assert from 'node:assert/strict';
import {createReferenceExportTrace} from '../public/modules/export-activation.mjs';
import {downloadBlob} from '../public/modules/export.mjs';

test('reference trace records distinct handler and dispatch activation without retaining the event',()=>{
  let active=true;
  const trace=createReferenceExportTrace(()=>active);
  const event={isTrusted:true,get target(){throw new Error('PRIVATE');},secret:'PRIVATE'};
  const dispatch=trace.begin('json',event);
  assert.deepEqual(trace.snapshot(),{schema:1,seen:1,dropped:0,events:[{
    sequence:1,format:'json',trusted:true,handler_active:true,dispatch_active:null,dispatch_observed:false
  }]});
  active=false;dispatch();event.isTrusted=false;active=true;dispatch();
  const result=trace.snapshot();
  assert.deepEqual(result.events[0],{sequence:1,format:'json',trusted:true,handler_active:true,dispatch_active:false,dispatch_observed:true});
  assert.ok(!JSON.stringify(result).includes('PRIVATE'));
  result.events[0].trusted=false;result.events.push({secret:'PRIVATE'});
  assert.equal(trace.snapshot().events.length,1);assert.equal(trace.snapshot().events[0].trusted,true);
});

test('unavailable activation is null and delayed dispatch preserves the original operation',()=>{
  const trace=createReferenceExportTrace(()=>{throw new Error('PRIVATE');});
  const event={get isTrusted(){throw new Error('PRIVATE');}};
  const png=trace.begin('png',event);trace.begin('csv',{isTrusted:false})();png();
  assert.deepEqual(trace.snapshot().events.map(row=>[row.sequence,row.format,row.trusted,row.handler_active,row.dispatch_active,row.dispatch_observed]),[
    [1,'png',null,null,null,true],[2,'csv',false,null,null,true]
  ]);
  assert.ok(!JSON.stringify(trace.snapshot()).includes('PRIVATE'));
});

test('trace keeps only four fixed-format operations with detached bounded metadata',()=>{
  const trace=createReferenceExportTrace(()=>false);
  for(let i=0;i<7;i++)trace.begin('csv',{isTrusted:1})();
  trace.begin('PRIVATE',{get isTrusted(){throw new Error('must not read');}})();
  const result=trace.snapshot();
  assert.equal(result.seen,7);assert.equal(result.dropped,3);
  assert.deepEqual(result.events.map(row=>row.sequence),[4,5,6,7]);
  assert.ok(result.events.every(row=>row.trusted===null&&row.handler_active===false));
  assert.ok(JSON.stringify(result).length<1000);
});

function downloadFixture(t,{clickError=null}={}){
  const calls=[],previous=Object.getOwnPropertyDescriptor(globalThis,'document');
  Object.defineProperty(globalThis,'document',{configurable:true,value:{createElement(name){assert.equal(name,'a');return {click(){calls.push('click');if(clickError)throw clickError;}};}}});
  t.after(()=>previous?Object.defineProperty(globalThis,'document',previous):delete globalThis.document);
  t.mock.method(URL,'createObjectURL',()=>{calls.push('create');return 'blob:fixture';});
  t.mock.method(URL,'revokeObjectURL',url=>{assert.equal(url,'blob:fixture');calls.push('revoke');});
  t.mock.method(globalThis,'setTimeout',(callback,delay)=>{assert.equal(delay,30000);calls.push('schedule');return 1;});
  return calls;
}

test('download samples at dispatch immediately before anchor click',t=>{
  const calls=downloadFixture(t);
  downloadBlob(new Blob(['fixture']),'fixture.json',()=>calls.push('dispatch'));
  assert.deepEqual(calls,['create','dispatch','click','schedule']);
});

test('a diagnostic callback failure cannot prevent the download',t=>{
  const calls=downloadFixture(t);
  assert.doesNotThrow(()=>downloadBlob(new Blob(['fixture']),'fixture.csv',()=>{calls.push('dispatch');throw new Error('PRIVATE');}));
  assert.deepEqual(calls,['create','dispatch','click','schedule']);
});

test('a real anchor failure still revokes its URL and remains a failure',t=>{
  const failure=new Error('click failed'),calls=downloadFixture(t,{clickError:failure});
  assert.throws(()=>downloadBlob(new Blob(['fixture']),'fixture.csv',()=>calls.push('dispatch')),error=>error===failure);
  assert.deepEqual(calls,['create','dispatch','click','revoke']);
});
