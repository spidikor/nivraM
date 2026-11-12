import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const source=await readFile(new URL('../public/modules/desktop.mjs',import.meta.url),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const HASH='a'.repeat(64),TOKEN='c'.repeat(32),KEY='1700000000000000000-'+'d'.repeat(32);
const BASE={schema:'nivram/1',graph:{atoms:[],bonds:[]},export:{width:1000}};
const RECOVERED={...BASE,export:{width:1200}};
const inventory=(issues={},records=[{id:KEY,created_ns:1700000000000000000}])=>({
  schemaVersion:1,status:'ready',records,issues:{invalid:0,unsupported:0,unreadable:0,interrupted:0,...issues}
});
const recovered=()=>({status:'recovered',recovery_id:TOKEN,document:RECOVERED});

async function setup(options={}){
  const elements=new Map(),timers=new Map(),calls={inventory:0,load:[],accept:[],save:[],open:0,prompt:[],render:[]};
  let nextTimer=0,snapshot={revision:0,document:structuredClone(BASE),dirty:false},marked=0;
  const get=id=>{if(!elements.has(id))elements.set(id,{classList:{toggle(){}},click(){}});return elements.get(id);};
  const editor={ready:Promise.resolve(),getSnapshot:()=>snapshot,subscribe(){},
    setRecoveryGeneration(generation){snapshot={...snapshot,recoveryGeneration:generation};},
    markSaved(){marked++;snapshot={...snapshot,dirty:false};},
    async openProject(document,settings){
      calls.render.push({document,settings});
      if(options.render)return options.render(document,settings);
      snapshot={...snapshot,revision:snapshot.revision+1,document,dirty:true};return true;
    }
  };
  const api={
    recovery_context:async()=>({generation:'b'.repeat(32)}),
    async save_project(...args){calls.save.push(args);return {status:'saved',disk_hash:HASH,name:'known.nivram'};},
    async open_project(){calls.open++;return {status:'cancelled'};},
    async recovery_inventory(){calls.inventory++;return options.inventory?options.inventory():inventory();},
    async recover_project(key){calls.load.push(key);return options.load?options.load(key):recovered();},
    async accept_recovery(token){calls.accept.push(token);return options.accept?options.accept(token):{status:'accepted'};}
  };
  const window={nivramDocument:editor,pywebview:{api},addEventListener(){},dispatchEvent(){}};
  vm.runInNewContext(source,{window,document:{getElementById:get,addEventListener(){}},Event:class Event{},
    confirm(text){calls.prompt.push(text);return options.confirm!==false;},
    setTimeout(fn,ms){const id=++nextTimer;timers.set(id,{fn,ms});return id;},
    clearTimeout(id){timers.delete(id);}
  });
  await tick();
  return {native:window.nivramNative,calls,elements,timers,snapshot:()=>structuredClone(snapshot),marked:()=>marked,
    edit(){snapshot={...snapshot,revision:snapshot.revision+1,document:{...BASE,export:{width:1400}},dirty:true};},
    async expire(){const deadlines=[...timers].filter(([,t])=>t.ms===5000);assert.equal(deadlines.length,1);
      for(const [id,timer] of deadlines){timers.delete(id);timer.fn();}await tick();}
  };
}

test('empty recovery reports absence without loading, detaching or marking saved',async()=>{
  const app=await setup({inventory:()=>inventory({},[])});
  await app.native.save();const before=app.snapshot();
  await app.native.recover();
  assert.deepEqual(app.snapshot(),before);assert.equal(app.calls.prompt.length,0);assert.equal(app.calls.load.length,0);
  assert.match(app.elements.get('toast').textContent,/No recovery copies/);
  await app.native.save();assert.equal(app.calls.save[1][1],HASH);assert.equal(app.calls.save[1][2],false);
});

test('damaged, unsupported, unreadable and interrupted evidence is explicit with no usable record',async()=>{
  const app=await setup({inventory:()=>inventory({invalid:1,unsupported:1,unreadable:1,interrupted:1},[])});
  const before=app.snapshot();await app.native.recover();
  const text=app.elements.get('toast').textContent;
  for(const pattern of [/Damaged/,/unsupported format/,/could not be read/,/Interrupted/,/retained/,/Save As/])assert.match(text,pattern);
  assert.deepEqual(app.snapshot(),before);assert.equal(app.calls.load.length,0);
});

test('valid recovery with retained warnings requires confirmation and acknowledgement and remains unsaved',async()=>{
  const app=await setup({inventory:()=>inventory({invalid:1,unsupported:1})});
  await app.native.save();await app.native.recover();
  assert.match(app.calls.prompt[0],/Damaged/);assert.match(app.calls.prompt[0],/unsupported/);
  assert.match(app.calls.prompt[0],/most recent readable/);
  assert.deepEqual(app.calls.load,[KEY]);assert.deepEqual(app.calls.accept,[TOKEN]);
  assert.equal(app.calls.render[0].settings.asUnsaved,true);
  assert.deepEqual(app.snapshot().document,RECOVERED);assert.equal(app.snapshot().dirty,true);
  assert.equal(app.marked(),1);
  await app.native.save();assert.equal(app.calls.save[1][1],null);assert.equal(app.calls.save[1][2],true);
});

test('declining recovery preserves the existing destination and document',async()=>{
  const app=await setup({confirm:false});await app.native.save();const before=app.snapshot();
  await app.native.recover();
  assert.deepEqual(app.snapshot(),before);assert.equal(app.calls.load.length,0);assert.equal(app.calls.accept.length,0);
  await app.native.save();assert.equal(app.calls.save[1][1],HASH);assert.equal(app.calls.save[1][2],false);
});

test('an unavailable inventory is distinct from empty and cannot prepare recovery',async()=>{
  const app=await setup({inventory:()=>({...inventory({},[]),status:'unavailable'})});
  const before=app.snapshot();await app.native.recover();
  assert.match(app.elements.get('toast').textContent,/could not be checked/);
  assert.match(app.elements.get('toast').textContent,/Save As/);
  assert.deepEqual(app.snapshot(),before);assert.equal(app.calls.prompt.length,0);
});

for(const bad of [
  {...inventory(),schemaVersion:2}, {...inventory(),issues:{}},
  inventory({invalid:-1}),inventory({unsupported:1001}),
  {...inventory(),records:[{id:'../foreign',created_ns:1}]},
  {...inventory(),records:[{id:KEY,created_ns:Infinity}]},
  {...inventory(),records:[{id:KEY,created_ns:1},{id:KEY,created_ns:2}]}
]){
  test('malformed inventory cannot open a drawing or silently claim no recovery: '+JSON.stringify(bad),async()=>{
    const app=await setup({inventory:()=>bad});const before=app.snapshot();await app.native.recover();
    assert.deepEqual(app.snapshot(),before);assert.equal(app.calls.load.length,0);
    assert.match(app.elements.get('toast').textContent,/could not be opened/);
  });
}

for(const phase of ['inventory','load']){
  test('an edit during held '+phase+' prevents stale recovery and concurrent native operations',async()=>{
    const pending=deferred(),options={[phase]:()=>pending.promise};const app=await setup(options);
    await app.native.save();const running=app.native.recover();await tick();
    await app.native.save();await app.native.open();await app.native.recover();
    assert.equal(app.calls.save.length,1);assert.equal(app.calls.open,0);assert.equal(app.calls.inventory,1);
    app.edit();const current=app.snapshot();
    pending.resolve(phase==='inventory'?inventory():recovered());await running;
    assert.deepEqual(app.snapshot(),current);assert.equal(app.calls.render.length,0);assert.equal(app.calls.accept.length,0);
    assert.match(app.elements.get('toast').textContent,/drawing changed/);
    await app.native.save();assert.equal(app.calls.save[1][2],false);
  });
}

test('destination epoch changes during inventory also reject the old recovery request',async()=>{
  const pending=deferred(),app=await setup({inventory:()=>pending.promise});
  const running=app.native.recover();app.native.resetDestination();pending.resolve(inventory());await running;
  assert.equal(app.calls.load.length,0);assert.equal(app.calls.render.length,0);
});

for(const render of [async()=>false,async()=>{throw Error('render failed');}]){
  test('rejected rendering preserves the destination and never acknowledges recovery: '+render,async()=>{
    const app=await setup({render});await app.native.save();const before=app.snapshot();
    await app.native.recover();
    assert.deepEqual(app.snapshot(),before);assert.equal(app.calls.accept.length,0);
    await app.native.save();assert.equal(app.calls.save[1][1],HASH);assert.equal(app.calls.save[1][2],false);
  });
}

for(const accept of [async()=>({status:'unknown'}),async()=>{throw Error('lost acknowledgement');}]){
  test('failed acknowledgement leaves recovered contents unsaved and forces Save As: '+accept,async()=>{
    const app=await setup({accept});await app.native.save();await app.native.recover();
    assert.deepEqual(app.snapshot().document,RECOVERED);assert.equal(app.snapshot().dirty,true);
    assert.match(app.elements.get('toast').textContent,/destination could not be confirmed/);
    await app.native.save();assert.equal(app.calls.save[1][1],null);assert.equal(app.calls.save[1][2],true);
  });
}

for(const phase of ['inventory','load','accept']){
  test('expired '+phase+' response releases saving and cannot apply a late result',async()=>{
    const pending=deferred(),app=await setup({[phase]:()=>pending.promise});
    await app.native.save();const running=app.native.recover();await tick();await app.expire();await running;
    app.edit();const current=app.snapshot();
    pending.resolve(phase==='inventory'?inventory():phase==='load'?recovered():{status:'accepted'});await tick();
    assert.deepEqual(app.snapshot(),current);
    assert.equal(app.calls.render.length,phase==='accept'?1:0);
    await app.native.save();assert.equal(app.calls.save.length,2);
    assert.equal(app.calls.save[1][2],phase==='accept');
  });
}

