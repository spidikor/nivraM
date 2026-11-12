import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {MAX_PNG_INPUT,preparePngIntake,attachPngIntake} from '../public/modules/image-intake.mjs';

const bytes=Uint8Array.from([137,80,78,71,13,10,26,10,1,2,3,0,255]);
const task=()=>({assertCurrent(){},wait:promise=>Promise.resolve(promise)});
const file=(overrides={})=>({name:'original.png',type:'image/png',size:bytes.length,arrayBuffer:async()=>bytes.slice().buffer,...overrides});

test('byte intake preserves original bytes, name and full SHA without raster re-encoding',async()=>{
  const result=await preparePngIntake(file(),task());
  assert.deepEqual(Buffer.from(result.pngBase64,'base64'),Buffer.from(bytes));
  assert.equal(result.filename,'original.png');
  assert.equal(result.sha256,createHash('sha256').update(bytes).digest('hex'));
  assert.equal((await preparePngIntake(file({name:undefined}),task())).filename,'clipboard.png');
});

test('base64 chunk boundaries and the cancellable yield preserve all original bytes',async()=>{
  const original=new Uint8Array(24576*65+2);original.set(bytes);
  for(let i=bytes.length;i<original.length;i++)original[i]=i%251;
  const result=await preparePngIntake(file({size:original.length,arrayBuffer:async()=>original.buffer}),task());
  assert.deepEqual(Buffer.from(result.pngBase64,'base64'),Buffer.from(original));
  assert.equal(result.sha256,createHash('sha256').update(original).digest('hex'));
});

test('size, path label, type and bytes are checked before bridge dispatch',async()=>{
  let reads=0;
  for(const overrides of [{size:0},{size:MAX_PNG_INPUT+1},{size:NaN},{name:'../secret.png'},{name:'x\n.png'},
    {name:'C:\\x.png'},{name:'x.jpg',type:'image/jpeg'}]){
    await assert.rejects(preparePngIntake(file({...overrides,arrayBuffer:async()=>{reads++;return bytes.buffer;}}),task()));
  }
  assert.equal(reads,0);
  await assert.rejects(preparePngIntake(file({size:bytes.length+1}),task()),/size/);
  await assert.rejects(preparePngIntake(file({arrayBuffer:async()=>new ArrayBuffer(bytes.length)}),task()),/PNG/);
});

test('reading cancellation is observed before encoding or any further result',async()=>{
  let cancelled=false;
  const current={assertCurrent(){if(cancelled)throw new Error('cancelled');},wait:promise=>Promise.resolve(promise)};
  await assert.rejects(preparePngIntake(file({arrayBuffer:async()=>{cancelled=true;return bytes.buffer;}}),current),/cancelled/);
});

function events(){
  const handlers=new Map(),accepted=[],errors=[];let allowed=true;
  const target={addEventListener:(kind,fn)=>handlers.set(kind,fn),removeEventListener:(kind,fn)=>{if(handlers.get(kind)===fn)handlers.delete(kind);}};
  const dispose=attachPngIntake({target,onFile:f=>accepted.push(f),onError:error=>errors.push(error.message),canStart:()=>allowed});
  function dispatch(kind,files=[],options={}){
    const transfer={files,types:files.length?['Files']:[],items:[]};
    const event={target:{closest:()=>null},defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},
      ...(kind==='paste'?{clipboardData:transfer}:{dataTransfer:transfer}),...options};
    handlers.get(kind)?.(event);return event;
  }
  return {accepted,errors,dispatch,dispose,handlers,block:()=>{allowed=false;}};
}

test('one PNG drop and image clipboard paste enter the same callback once',()=>{
  const f=events(),png=file();
  assert.equal(f.dispatch('dragover',[png]).defaultPrevented,true);
  assert.equal(f.dispatch('drop',[png]).defaultPrevented,true);
  const pasted=f.dispatch('paste',[],{clipboardData:{files:[],items:[{kind:'file',getAsFile:()=>png}]}});
  assert.equal(pasted.defaultPrevented,true);
  assert.deepEqual(f.accepted,[png,png]);assert.deepEqual(f.errors,[]);
  f.dispose();assert.equal(f.handlers.size,0);
});

test('multiple or unsupported image files never auto-select a candidate or navigate',()=>{
  const f=events();
  assert.equal(f.dispatch('drop',[file(),file()]).defaultPrevented,true);
  assert.equal(f.dispatch('drop',[file({name:'x.jpg',type:'image/jpeg'})]).defaultPrevented,true);
  assert.equal(f.accepted.length,0);assert.equal(f.errors.length,2);
});

test('text paste, editable fields, active dialogs and closing review do not start intake',()=>{
  const f=events();
  assert.equal(f.dispatch('paste').defaultPrevented,false);
  assert.equal(f.dispatch('paste',[file()],{target:{closest:()=>({})}}).defaultPrevented,false);
  assert.equal(f.dispatch('drop',[file()],{defaultPrevented:true}).defaultPrevented,true);
  f.block();f.dispatch('paste',[file()]);assert.equal(f.dispatch('drop',[file()]).defaultPrevented,true);
  assert.equal(f.accepted.length,0);assert.equal(f.errors.length,0);
});
