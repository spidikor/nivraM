import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {saveReferenceExport} from '../public/modules/reference-save.mjs';

const hash=text=>createHash('sha256').update(text).digest('hex');
const token='t'.repeat(48);
const formats=[['json','application/json','{"exportKind":"ph-reference-context"}\n'],
  ['csv','text/csv;charset=utf-8','"path","type","value","spreadsheet_escaped"\r\n']];

test('native reference exports send the exact text and session token, then verify receipt bytes',async()=>{
  for(const [kind,type,text] of formats){
    const calls=[],blob=new Blob([text],{type});
    const native={async save_reference_export(...args){calls.push(args);return {status:'saved',bytes:Buffer.byteLength(text),sha256:hash(text)};}};
    const result=await saveReferenceExport({kind,blob,native,token,download(){assert.fail('Native saving must not trigger a browser download.');}});
    assert.deepEqual(calls,[[kind,text,token]]);assert.deepEqual(result,{status:'saved'});
  }
});

test('browser-only reference download dispatches synchronously before returning its promise',async()=>{
  let dispatched=false,observed=false;
  const blob=new Blob(['{}'],{type:'application/json'}),beforeDispatch=()=>{observed=true;};
  const pending=saveReferenceExport({kind:'json',blob,beforeDispatch,download(value,name,observer){
    assert.equal(value,blob);assert.equal(name,'nivram-ph-reference.json');observer();dispatched=true;
  }});
  assert.equal(dispatched,true);assert.equal(observed,true);
  assert.deepEqual(await pending,{status:'prepared'});
});

test('native cancellation, failure and uncertainty never fall through to browser download',async()=>{
  for(const status of ['cancelled','failed','conflict','unsupported','unconfirmed']){
    const result=await saveReferenceExport({kind:'json',blob:new Blob(['{}'],{type:'application/json'}),token,
      native:{async save_reference_export(){return {status,message:'PRIVATE PATH AND UPSTREAM DETAIL'};}},
      download(){assert.fail('No fallback after a native save attempt.');}});
    assert.equal(result.status,status);assert.equal(JSON.stringify(result).includes('PRIVATE'),false);
  }
  for(const response of [null,{}, {status:'saved',bytes:2,sha256:'0'.repeat(64)}, {status:'saved',bytes:3,sha256:hash('{}')}]){
    const result=await saveReferenceExport({kind:'json',blob:new Blob(['{}'],{type:'application/json'}),token,
      native:{async save_reference_export(){return response;}}});
    assert.equal(result.status,'unconfirmed');
  }
  const rejected=await saveReferenceExport({kind:'json',blob:new Blob(['{}'],{type:'application/json'}),token,
    native:{async save_reference_export(){throw new Error('PRIVATE PATH');}}});
  assert.deepEqual(rejected,{status:'unconfirmed'});
});

test('missing native capability, wrong format and oversized data cannot dispatch a save',async()=>{
  const native={save_reference_export(){assert.fail('Invalid input must not cross the native boundary.');}};
  for(const options of [
    {kind:'txt',blob:new Blob(['{}'],{type:'application/json'})},
    {kind:'json',blob:new Blob(['{}'],{type:'text/plain'})},
    {kind:'json',blob:new Blob([],{type:'application/json'})},
    {kind:'json',blob:new Blob(['x'.repeat(4000001)],{type:'application/json'})},
  ])await assert.rejects(saveReferenceExport({...options,native,token}),/reference export/i);
  await assert.rejects(saveReferenceExport({kind:'json',blob:new Blob(['{}'],{type:'application/json'}),native:{},token}),/native reference saving/i);
});

test('cancelling while native encoding is pending prevents dispatch and preserves the source blob',async()=>{
  let release;const gate=new Promise(resolve=>{release=resolve;}),controller=new AbortController();
  const blob=new Blob(['{}'],{type:'application/json'});
  const original=blob.text.bind(blob);blob.text=async()=>{await gate;return original();};
  const pending=saveReferenceExport({kind:'json',blob,native:{save_reference_export(){assert.fail('Cancelled export must not dispatch.');}},token,signal:controller.signal});
  controller.abort();release();
  await assert.rejects(pending,error=>error.name==='AbortError');
  assert.equal(await original(),'{}');
});
