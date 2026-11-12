import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {createDocumentStore} from '../public/modules/document.mjs';

const source=await readFile(new URL('../public/modules/desktop.mjs',import.meta.url),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const GENERATION='b'.repeat(32);
const receipt=(call,changes={})=>({status:'recovered-copy-saved',id:'1789900000000000000-'+'a'.repeat(32),generation:call.generation,revision:call.revision,...changes});

async function setup(context={generation:GENERATION}){
  const elements=new Map(),timers=new Map(),calls=[],saves=[];
  let time=0,nextTimer=0;
  const get=id=>{if(!elements.has(id))elements.set(id,{classList:{toggle(){}},click(){}});return elements.get(id);};
  const store=createDocumentStore({schema:'nivram/1',graph:{atoms:[],bonds:[]}});
  const editor={...store,ready:Promise.resolve()};
  const api={recovery_context:async()=>context,save_project:async(...args)=>{saves.push(args);return {status:'cancelled'};},autosave_project(document,generation,revision){
    const pending=deferred();calls.push({document,generation,revision,...pending});return pending.promise;
  }};
  const window={nivramDocument:editor,pywebview:{api},addEventListener(){},dispatchEvent(){}};
  vm.runInNewContext(source,{window,document:{getElementById:get,addEventListener(){}},confirm:()=>true,
    Event:class Event{},setTimeout(callback,delay){const id=++nextTimer;timers.set(id,{callback,at:time+delay});return id;},
    clearTimeout(id){timers.delete(id);}});
  await tick();
  return {calls,saves,store,elements,native:window.nivramNative,status:()=>get('save-status').textContent,
    edit(width){const snapshot=store.getSnapshot();store.commit({...snapshot.document,export:{...snapshot.document.export,width}});},
    async advance(milliseconds){
      time+=milliseconds;
      for(const [id,timer] of [...timers])if(timer.at<=time){timers.delete(id);void timer.callback();}
      await tick();
    }};
}

test('recovery waits for two seconds of settled edits and saves the latest immutable snapshot',async()=>{
  const app=await setup();app.edit(1000);await app.advance(1999);
  assert.equal(app.calls.length,0);
  app.edit(1200);await app.advance(1999);assert.equal(app.calls.length,0);
  await app.advance(1);assert.equal(app.calls.length,1);assert.equal(app.calls[0].document.export.width,1200);
  app.edit(1400);assert.equal(app.calls[0].document.export.width,1200);
  app.calls[0].resolve(receipt(app.calls[0]));await tick();
  assert.equal(app.status(),'Unsaved changes','An old revision receipt must not claim the new revision was recovered');
  await app.advance(2000);assert.equal(app.calls.length,2);assert.equal(app.calls[1].document.export.width,1400);
  app.calls[1].resolve(receipt(app.calls[1]));await tick();assert.match(app.status(),/^Recovery copy saved/);
});

for(const reply of [null,{}, {status:'saved'}, {status:'recovered-copy-saved'},
  {status:'recovered-copy-saved',id:'private/path'}, {status:'recovered-copy-saved',id:'0-'+'a'.repeat(32)},
  {status:'recovered-copy-saved',id:'1-'+'a'.repeat(33)}]){
  test(`malformed recovery acknowledgement ${JSON.stringify(reply)} cannot claim saved`,async()=>{
    const app=await setup();app.edit(1000);await app.advance(2000);
    app.calls[0].resolve(reply);await tick();
    assert.match(app.status(),/could not be confirmed/);
    assert.doesNotMatch(app.status(),/Recovery copy saved|private/);
    assert.equal(app.store.getSnapshot().dirty,true);
  });
}

test('older recovery failure cannot replace the success status of a newer recovered revision',async()=>{
  const app=await setup();app.edit(1000);await app.advance(2000);
  app.edit(1200);await app.advance(2000);assert.equal(app.calls.length,2);
  app.calls[1].resolve(receipt(app.calls[1]));await tick();const current=app.status();assert.match(current,/^Recovery copy saved/);
  app.calls[0].reject(new Error('private underlying error'));await tick();
  assert.equal(app.status(),current);
});

test('older recovery failure cannot overwrite a manually saved state',async()=>{
  const app=await setup();app.edit(1000);await app.advance(2000);
  const snapshot=app.store.getSnapshot();app.store.markSaved(snapshot.revision,snapshot.document);
  assert.equal(app.status(),'Saved state');
  app.calls[0].reject(new Error('late failure'));await tick();assert.equal(app.status(),'Saved state');
});

test('an old successful acknowledgement cannot regress the latest acknowledged revision',async()=>{
  const app=await setup();app.edit(1000);await app.advance(2000);
  app.edit(1200);await app.advance(2000);
  app.calls[1].resolve(receipt(app.calls[1]));await tick();app.calls[0].resolve(receipt(app.calls[0]));await tick();
  // A late manual acknowledgement of revision1 leaves revision2 dirty and emits it again.
  app.store.markSaved(1,app.calls[0].document);await app.advance(2000);
  assert.equal(app.calls.length,2,'A stale acknowledgement must not cause duplicate recovery of revision2');
});


test('autosave submits the host page identity and immutable document revision',async()=>{
  const app=await setup();app.edit(1000);await app.advance(2000);
  assert.equal(app.calls[0].generation,GENERATION);
  assert.equal(app.calls[0].revision,app.store.getSnapshot().revision);
  assert.equal(app.store.getSnapshot().recoveryGeneration,GENERATION);
  app.calls[0].resolve(receipt(app.calls[0]));await tick();
});

for(const changes of [{generation:'c'.repeat(32)},{generation:undefined},{revision:undefined},
  {revision:0},{revision:true},{revision:1.5},{status:'superseded'}]){
  test(`mismatched recovery tuple ${JSON.stringify(changes)} cannot claim saved`,async()=>{
    const app=await setup();app.edit(1000);await app.advance(2000);
    app.calls[0].resolve(receipt(app.calls[0],changes));await tick();
    assert.match(app.status(),/could not be confirmed/);
    assert.equal(app.store.getSnapshot().dirty,true);
  });
}

for(const context of [null,{}, {generation:'invalid'}, {generation:'a'.repeat(33)}]){
  test(`invalid host context ${JSON.stringify(context)} keeps native manual saving available`,async()=>{
    const app=await setup(context);app.edit(1000);await app.advance(2000);
    assert.equal(app.calls.length,0);
    assert.match(app.status(),/Recovery unavailable/);
    assert.equal(typeof app.elements.get('save-button').onclick,'function');
  });
}

test('failure of a duplicate same-revision request cannot erase its verified recovery status',async()=>{
  const app=await setup();app.edit(1000);const older=app.store.getSnapshot();
  app.edit(1200);await app.advance(2000);
  app.store.markSaved(older.revision,older.document);await app.advance(2000);
  assert.equal(app.calls.length,2);
  assert.equal(app.calls[0].revision,app.calls[1].revision);
  app.calls[0].resolve(receipt(app.calls[0]));await tick();const saved=app.status();
  app.calls[1].reject(new Error('late duplicate failure'));await tick();
  assert.match(saved,/^Recovery copy saved/);assert.equal(app.status(),saved);
});


test('a held recovery-context response cannot block native manual save',async()=>{
  const pending=deferred(),app=await setup(pending.promise);
  assert.equal(typeof app.native?.save,'function');
  app.edit(1000);await app.native.save();assert.equal(app.saves.length,1);
  assert.equal(app.calls.length,0);
  pending.resolve({generation:GENERATION});await tick();
});

test('context initialization schedules edits that settled while the host response was pending',async()=>{
  const pending=deferred(),app=await setup(pending.promise);
  app.edit(1000);await app.advance(2000);assert.equal(app.calls.length,0);
  pending.resolve({generation:GENERATION});await tick();await app.advance(2000);
  assert.equal(app.calls.length,1);assert.equal(app.calls[0].document.export.width,1000);
  app.calls[0].resolve(receipt(app.calls[0]));await tick();assert.match(app.status(),/^Recovery copy saved/);
});

test('an expired context request cannot bind late or disable native save',async()=>{
  const pending=deferred(),app=await setup(pending.promise);
  app.edit(1000);await app.advance(5000);
  assert.match(app.status(),/Recovery unavailable/);
  pending.resolve({generation:GENERATION});await tick();await app.advance(2000);
  assert.equal(app.calls.length,0);assert.equal(app.store.getSnapshot().recoveryGeneration,undefined);
  await app.native.save();assert.equal(app.saves.length,1);
});
