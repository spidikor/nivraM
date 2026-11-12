import test from 'node:test';
import assert from 'node:assert/strict';
import {createBatchExport,estimateBatchTransportBytes,MAX_BATCH_PNG_BYTES,MAX_BATCH_TRANSPORT_ESTIMATE_BYTES} from '../public/modules/batch-export.mjs';
import {DEFAULT_STYLE} from '../public/modules/document.mjs';

const settings=()=>({style:{...DEFAULT_STYLE},export:{width:640,marginMode:'pixels',margin:8},profile:null});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};

function fixture(count=3){
  const calls=[],progress=[],items=Array.from({length:count},(_,i)=>({id:`item${i}`,name:`input${i}.mol`,status:'pending'}));
  const faults={};let active=0,maxActive=0;
  const receipt=()=>({status:items.some(item=>item.status==='cancelled')?'cancelled':'completed',items:structuredClone(items),manifestName:'manifest.json'});
  const native={
    async begin_batch_export(value){calls.push(['begin',value]);return faults.begin?faults.begin(value):{status:'running',sessionId:'batch',items:structuredClone(items)};},
    async prepare_batch_item(session,id){calls.push(['prepare',id]);if(faults.prepare)return faults.prepare(id);const item=items.find(item=>item.id===id);item.status='rendering';return {status:'ready',item:structuredClone(item),project:{export:settings().export}};},
    async write_batch_png(session,id,payload){calls.push(['write',id,payload]);const item=items.find(item=>item.id===id);if(item.status!=='cancelled'){item.status='exported';item.outputName=`${id}.png`;}return {status:item.status,item:structuredClone(item)};},
    async fail_batch_item(session,id,error){calls.push(['fail',id,error]);const item=items.find(item=>item.id===id);item.status='failed';item.error={code:error};return {status:'failed',item:structuredClone(item)};},
    async cancel_batch_export(session){calls.push(['cancel',session]);for(const item of items)if(!['exported','failed'].includes(item.status))item.status='cancelled';return receipt();},
    async finish_batch_export(session){calls.push(['finish',session]);return receipt();}
  };
  const render=async(prepared,{signal})=>{
    active++;maxActive=Math.max(maxActive,active);calls.push(['render',prepared.item.id]);
    try{return faults.render?await faults.render(prepared,{signal}):{blob:new Blob(['synthetic'],{type:'image/png'})};}
    finally{active--;}
  };
  const encode=async(blob,{signal})=>{calls.push(['encode']);return faults.encode?faults.encode(blob,{signal}):'encoded-fixture';};
  const runner=createBatchExport({native,render,encode,onProgress:value=>progress.push(value)});
  return {runner,native,faults,calls,progress,items,get maxActive(){return maxActive;}};
}

test('each isolated input renders, encodes and saves sequentially, with progress',async()=>{
  const f=fixture();const result=await f.runner.run(settings());
  assert.equal(f.maxActive,1);assert.equal(result.status,'completed');assert.equal(f.runner.busy,false);
  assert.deepEqual(f.calls.map(call=>call[0]),['begin','prepare','render','encode','write','prepare','render','encode','write','prepare','render','encode','write','finish']);
  assert.equal(f.progress.at(-1).completed,3);assert.ok(result.items.every(item=>item.status==='exported'));
});

test('a later render failure preserves success and continues to the remaining input',async()=>{
  const f=fixture();f.faults.render=async prepared=>{if(prepared.item.id==='item1')throw new Error('Raster budget');return {blob:new Blob(['x'])};};
  const result=await f.runner.run(settings());
  assert.deepEqual(result.items.map(item=>item.status),['exported','failed','exported']);
  assert.deepEqual(f.calls.find(call=>call[0]==='fail'),['fail','item1','export_failed']);
});

test('a malformed prepared input is a per-file failure and never reaches the renderer',async()=>{
  const f=fixture(2);f.faults.prepare=id=>{
    const item=f.items.find(value=>value.id===id);item.status='failed';item.error={code:'invalid_input'};
    return {status:'failed',item:structuredClone(item)};
  };
  const result=await f.runner.run(settings());
  assert.ok(result.items.every(item=>item.status==='failed'));
  assert.equal(f.calls.some(call=>['render','encode','write'].includes(call[0])),false);
});

test('cancel during raster work aborts its signal, rejects a late result, and retains earlier files',async()=>{
  const f=fixture(),pending=deferred();let signal;
  f.faults.render=async(prepared,options)=>{
    if(prepared.item.id==='item1'){signal=options.signal;return pending.promise;}
    return {blob:new Blob(['x'])};
  };
  const running=f.runner.run(settings());
  while(!signal)await new Promise(resolve=>setImmediate(resolve));
  f.runner.cancel();assert.equal(signal.aborted,true);
  pending.resolve({blob:new Blob(['late'])});
  const result=await running;
  assert.deepEqual(result.items.map(item=>item.status),['exported','cancelled','cancelled']);
  assert.deepEqual(f.calls.filter(call=>call[0]==='write').map(call=>call[1]),['item0']);
  assert.equal(f.calls.filter(call=>call[0]==='cancel').length,1);
});

test('cancel while file dialogs are pending cancels the late native session before any input preparation',async()=>{
  const f=fixture(),choose=deferred();f.faults.begin=()=>choose.promise;
  const running=f.runner.run(settings());f.runner.cancel();
  choose.resolve({status:'running',sessionId:'batch',items:structuredClone(f.items)});
  const result=await running;
  assert.equal(result.status,'cancelled');assert.equal(f.calls.some(call=>call[0]==='prepare'),false);
  assert.equal(f.calls.filter(call=>call[0]==='cancel').length,1);
});

test('cancel between encoding and saving cannot publish a late PNG',async()=>{
  const f=fixture(),encoding=deferred();f.faults.encode=()=>encoding.promise;
  const running=f.runner.run(settings());
  while(!f.calls.some(call=>call[0]==='encode'))await new Promise(resolve=>setImmediate(resolve));
  f.runner.cancel();encoding.resolve('late-payload');await running;
  assert.equal(f.calls.some(call=>call[0]==='write'),false);
});

test('a repeated start is refused until the active export settles',async()=>{
  const f=fixture(),render=deferred();f.faults.render=()=>render.promise;
  const first=f.runner.run(settings());
  await assert.rejects(f.runner.run(settings()),/already running/);
  f.runner.cancel();render.resolve({blob:new Blob(['late'])});await first;
  assert.equal(f.runner.busy,false);
});

test('native settings are frozen copies and rejected dialogs produce no exports',async()=>{
  const f=fixture(),source=settings();f.faults.begin=()=>({status:'cancelled'});
  const task=f.runner.run(source);source.export.width=9999;
  const result=await task;
  assert.equal(result.status,'cancelled');assert.equal(f.calls[0][1].export.width,640);
  assert.ok(Object.isFrozen(f.calls[0][1].export));assert.deepEqual(f.calls.map(call=>call[0]),['begin']);
});

test('bridge failures cancel the session and remain failures rather than fabricated completion',async()=>{
  const f=fixture();f.faults.prepare=()=>{throw new Error('Connection lost');};
  await assert.rejects(f.runner.run(settings()),/Connection lost/);
  assert.equal(f.calls.filter(call=>call[0]==='cancel').length,1);
  assert.equal(f.calls.some(call=>call[0]==='finish'),false);
  assert.equal(f.runner.busy,false);
});

for(const published of [false,true]){
  test(`a lost write acknowledgement stops the batch (publication ${published?'completed':'unknown'})`,async()=>{
    const f=fixture(),write=f.native.write_batch_png;
    f.native.write_batch_png=async(...args)=>{
      if(published)await write(...args);
      throw new Error('Write acknowledgement lost');
    };
    await assert.rejects(f.runner.run(settings()),/Write acknowledgement lost/);
    assert.deepEqual(f.calls.filter(call=>call[0]==='prepare').map(call=>call[1]),['item0']);
    assert.equal(f.calls.some(call=>call[0]==='fail'),false);
    assert.equal(f.calls.some(call=>call[0]==='finish'),false);
    assert.equal(f.calls.filter(call=>call[0]==='cancel').length,1);
    assert.deepEqual(f.items.map(item=>item.status),[published?'exported':'cancelled','cancelled','cancelled']);
    assert.equal(f.progress.at(-1).phase,'stopped');
    assert.deepEqual(f.progress.at(-1).items,f.items);
    assert.equal(f.runner.busy,false);
  });
}

test('native publication uncertainty stops before the next preparation and shows its terminal receipt',async()=>{
  const f=fixture(),prepare=f.native.prepare_batch_item,cancel=f.native.cancel_batch_export;
  f.native.prepare_batch_item=async(session,id)=>{
    if(id==='item0')return prepare(session,id);
    f.calls.push(['prepare',id]);return {status:'cancelled',item:structuredClone(f.items.find(item=>item.id===id))};
  };
  f.native.write_batch_png=async(session,id,payload)=>{
    f.calls.push(['write',id,payload]);
    Object.assign(f.items[0],{status:'failed',outputName:'unverified.png',outputSha256:null,dimensions:null,
      error:{code:'write_unverified',message:'The PNG publication could not be verified.'}});
    for(const item of f.items.slice(1))item.status='cancelled';
    return {status:'stopped',item:structuredClone(f.items[0]),manifestError:null};
  };
  f.native.cancel_batch_export=async(...args)=>({...await cancel(...args),status:'stopped'});
  await assert.rejects(f.runner.run(settings()),/publication could not be verified/i);
  assert.deepEqual(f.calls.filter(call=>call[0]==='prepare').map(call=>call[1]),['item0']);
  assert.equal(f.calls.some(call=>['fail','finish'].includes(call[0])),false);
  assert.equal(f.calls.filter(call=>call[0]==='cancel').length,1);
  assert.equal(f.progress.at(-1).phase,'stopped');
  assert.deepEqual(f.progress.at(-1).items,f.items);
  assert.equal(f.runner.busy,false);
});

test('a missing cancellation receipt preserves the original error without inventing terminal progress',async()=>{
  const f=fixture();
  f.native.write_batch_png=async()=>{throw new Error('Original write acknowledgement lost');};
  f.native.cancel_batch_export=async()=>{throw new Error('Cancellation acknowledgement lost too');};
  await assert.rejects(f.runner.run(settings()),/Original write acknowledgement lost/);
  assert.equal(f.progress.some(value=>['completed','cancelled','stopped'].includes(value.phase)),false);
  assert.equal(f.calls.some(call=>call[0]==='finish'),false);
  assert.equal(f.runner.busy,false);
});

test('manifest failure stops further rendering with completed output retained by native state',async()=>{
  const f=fixture();f.faults.prepare=id=>({status:'cancelled',item:{...f.items[0],status:'cancelled'},manifestError:'Manifest could not be saved'});
  await assert.rejects(f.runner.run(settings()),/Manifest could not be saved/);
  assert.equal(f.calls.some(call=>call[0]==='render'),false);
  assert.equal(f.calls.filter(call=>call[0]==='prepare').length,1);
});

test('a manifest failure after publication stops without relabeling the saved item',async()=>{
  const f=fixture(),write=f.native.write_batch_png;
  f.native.write_batch_png=async(...args)=>({...await write(...args),manifestError:'Manifest checkpoint failed'});
  await assert.rejects(f.runner.run(settings()),/Manifest checkpoint failed/);
  assert.equal(f.calls.filter(call=>call[0]==='prepare').length,1);
  assert.equal(f.calls.some(call=>call[0]==='fail'),false);
  assert.equal(f.calls.some(call=>call[0]==='finish'),false);
  assert.deepEqual(f.items.map(item=>item.status),['exported','cancelled','cancelled']);
  assert.equal(f.runner.busy,false);
});

test('a local encoding failure remains a per-file failure and later inputs can finish',async()=>{
  const f=fixture(2);let attempts=0;
  f.faults.encode=()=>{if(++attempts===1)throw new Error('Encoding failed');return 'encoded-fixture';};
  const result=await f.runner.run(settings());
  assert.deepEqual(result.items.map(item=>item.status),['failed','exported']);
  assert.deepEqual(f.calls.find(call=>call[0]==='fail'),['fail','item0','encoding_failed']);
  assert.equal(f.calls.some(call=>call[0]==='cancel'),false);
});

test('encoded bridge copies have a separate bounded estimate',()=>{
  assert.equal(estimateBatchTransportBytes(3),38);
  assert.ok(estimateBatchTransportBytes(MAX_BATCH_PNG_BYTES)<MAX_BATCH_TRANSPORT_ESTIMATE_BYTES);
  for(const bytes of [-1,NaN,1.5,MAX_BATCH_PNG_BYTES+1])assert.throws(()=>estimateBatchTransportBytes(bytes));
});
