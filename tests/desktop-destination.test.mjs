import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const source=await readFile(new URL('../public/modules/desktop.mjs',import.meta.url),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const HASH='a'.repeat(64);

async function setup(api){
  const elements=new Map();
  const get=id=>{if(!elements.has(id))elements.set(id,{classList:{toggle(){}},click(){}});return elements.get(id);};
  let snapshot={revision:0,document:{schema:'nivram/1',graph:{atoms:[],bonds:[]}},dirty:false};
  const saved=[];
  const editor={ready:Promise.resolve(),getSnapshot:()=>snapshot,subscribe(){},
    async openProject(document){snapshot={revision:snapshot.revision+1,document,dirty:true};return true;},
    markSaved(...args){saved.push(args);}};
  const window={nivramDocument:editor,pywebview:{api},addEventListener(){},dispatchEvent(){}};
  const context={window,document:{getElementById:get,addEventListener(){}},confirm:()=>true,setTimeout,clearTimeout,Event:class Event{}};
  vm.runInNewContext(source,context);
  await tick();
  return {native:window.nivramNative,saved,elements};
}

test('a late old Save receipt cannot restore the destination after a replacement',async()=>{
  const pending=deferred(),calls=[];
  const app=await setup({save_project(...args){calls.push(args);return calls.length===1?pending.promise:Promise.resolve({status:'cancelled'});}});
  const oldSave=app.native.save();
  app.native.resetDestination();
  pending.resolve({status:'saved',disk_hash:HASH,name:'old.nivram'});
  await oldSave;
  await app.native.save();
  assert.equal(calls[1][1],null);
  assert.equal(calls[1][2],true,'new document requires Save As even after late old save');
  assert.equal(app.saved.length,0,'late save cannot mark a replacement document saved');
});

test('a late open acknowledgement cannot attach a destination to a subsequent document',async()=>{
  const pending=deferred(),accepted=deferred(),calls=[];
  const app=await setup({
    save_project(...args){calls.push(args);return Promise.resolve({status:'cancelled'});},
    open_project:async()=>({status:'opened',open_id:'test',disk_hash:'opened-hash',name:'opened.nivram',document:{schema:'nivram/1',graph:{atoms:[],bonds:[]}}}),
    accept_open(){accepted.resolve();return pending.promise;}
  });
  const opening=app.native.open();
  await accepted.promise;
  app.native.resetDestination();
  pending.resolve({status:'accepted'});
  await opening;
  await app.native.save();
  assert.equal(calls[0][1],null);
  assert.equal(calls[0][2],true);
  assert.equal(app.saved.length,0,'stale open is not marked durably saved');
});

test('cancelling the molecule picker alone leaves the existing destination usable',async()=>{
  const calls=[];
  const app=await setup({save_project(...args){calls.push(args);return Promise.resolve({status:'saved',disk_hash:HASH,name:'known.nivram'});}});
  await app.native.save();
  app.elements.get('open-molecule-button').onclick();
  await app.native.save();
  assert.equal(calls[1][1],HASH);
  assert.equal(calls[1][2],false);
});

for(const outcome of ['unknown','committed','not_committed']){
  test(`an unconfirmed ${outcome} save cannot mark saved or reuse the destination`,async()=>{
    const calls=[];
    const replies=[{status:'saved',disk_hash:HASH,name:'known.nivram'},
      {status:'unconfirmed',outcome,message:'Save could not be confirmed.'},{status:'cancelled'}];
    const app=await setup({save_project(...args){calls.push(args);return Promise.resolve(replies.shift());}});
    await app.native.save();
    await app.native.save();
    assert.equal(app.saved.length,1,'only the acknowledged first save is marked saved');
    assert.match(app.elements.get('toast').textContent,/confirm|inspect/i);
    await app.native.save();
    assert.equal(calls[2][1],HASH,'failure cannot advance the known hash');
    assert.equal(calls[2][2],true,'next user save must select a destination');
  });
}

test('unsupported protected updates require Save As and do not claim success',async()=>{
  const calls=[];
  const app=await setup({save_project(...args){calls.push(args);return Promise.resolve(calls.length===1?
    {status:'unsupported',outcome:'not_committed',message:'Protected updates unavailable.'}:{status:'cancelled'});}});
  await app.native.save();
  assert.equal(app.saved.length,0);
  assert.match(app.elements.get('toast').textContent,/Save As/);
  await app.native.save();
  assert.equal(calls[1][2],true);
});

test('a definite precommit failure leaves the known destination available for an explicit retry',async()=>{
  const calls=[];
  const replies=[{status:'saved',disk_hash:HASH,name:'known.nivram'},
    {status:'failed',outcome:'not_committed',message:'Save failed before commit.'},{status:'cancelled'}];
  const app=await setup({save_project(...args){calls.push(args);return Promise.resolve(replies.shift());}});
  await app.native.save();
  await app.native.save();
  assert.equal(app.saved.length,1);
  assert.match(app.elements.get('toast').textContent,/failed/i);
  await app.native.save();
  assert.equal(calls[2][1],HASH);
  assert.equal(calls[2][2],false);
});

test('a bridge rejection reports uncertainty without claiming existing files or recovery were retained',async()=>{
  const calls=[];
  const app=await setup({save_project(...args){calls.push(args);return calls.length===1?
    Promise.reject(new Error('private path and native details')):Promise.resolve({status:'cancelled'});}});
  await app.native.save();
  assert.equal(app.saved.length,0);
  const text=app.elements.get('toast').textContent;
  assert.match(text,/confirm|inspect/i);
  assert.doesNotMatch(text,/existing files and recovery are retained|private path/);
  await app.native.save();
  assert.equal(calls[1][2],true);
});

for(const reply of [null,{status:'unexpected'},{status:'saved',disk_hash:'bad-hash',name:'bad.nivram'},
  {status:'failed',outcome:'committed'},{status:'unsupported',outcome:'unknown'}]){
  test(`malformed or contradictory save receipt ${JSON.stringify(reply)} remains unconfirmed`,async()=>{
    const calls=[];
    const app=await setup({save_project(...args){calls.push(args);return Promise.resolve(calls.length===1?reply:{status:'cancelled'});}});
    await app.native.save();
    assert.equal(app.saved.length,0);
    assert.match(app.elements.get('toast').textContent,/confirm|inspect/i);
    await app.native.save();
    assert.equal(calls[1][2],true);
  });
}
