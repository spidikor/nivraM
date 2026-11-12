import test from 'node:test';
import assert from 'node:assert/strict';
import {extractFragment,insertFragment,assertFragmentRender,validateFragmentRecord} from '../public/modules/fragments.mjs';
import {createDocumentStore} from '../public/modules/document.mjs';
import {createJobController} from '../public/modules/jobs.mjs';
const atom=(id,x=0,y=0,extra={})=>({id,symbol:'C',x,y,...extra});
const bond=(id,begin,end,extra={})=>({id,begin,end,order:1,...extra});
const make=()=>{let n=0;return p=>p+(++n);};
const graph=()=>({atoms:[atom('old-a',0,0,{chiralTag:1,mapNum:72,isotope:13}),atom('old-b',1,1),atom('old-c',2,1),atom('old-d',3,0),atom('separate',-4)],bonds:[bond('old-ab','old-a','old-b'),bond('old-bc','old-b','old-c',{order:2,stereo:3,stereoAtoms:['old-a','old-d']}),bond('old-cd','old-c','old-d',{direction:2})]});
test('closed extraction retains annotations/order and never mutates original',()=>{
  const g=graph(),before=structuredClone(g),fragment=extractFragment(g,{atoms:['old-d','old-b','old-a','old-c']});
  assert.deepEqual(fragment,{atoms:g.atoms.slice(0,4),bonds:g.bonds});assert.deepEqual(g,before);
  assert.deepEqual(extractFragment(g),g);assert.deepEqual(extractFragment(g,{atoms:['separate']}),{atoms:[g.atoms[4]],bonds:[]});
});
test('partial molecular cuts, stale selection, stereo reference exclusion and empty inputs reject',()=>{
  const g=graph();assert.throws(()=>extractFragment(g,{atoms:['old-b']}),/complete disconnected/);
  assert.throws(()=>extractFragment(g,{bonds:['old-bc']}),/complete disconnected/);
  assert.throws(()=>extractFragment(g,{atoms:['missing']}),/no longer valid/);
  assert.throws(()=>extractFragment({atoms:[],bonds:[]}),/Draw or import/);
  g.bonds[1].stereoAtoms=['old-a','separate'];assert.throws(()=>extractFragment(g,{atoms:g.atoms.slice(0,4).map(a=>a.id)}),/stereochemistry reference/);
});
test('selected bond endpoints work when they constitute an entire component',()=>{
  const g={atoms:[atom('x'),atom('y',1)],bonds:[bond('xy','x','y')]};assert.deepEqual(extractFragment(g,{bonds:['xy']}),g);
});
test('disconnected insertion remaps all identities and stereo refs, retains maps and neighbor order',()=>{
  const g=graph(),before=structuredClone(g),fragment=extractFragment(g,{atoms:g.atoms.slice(0,4).map(a=>a.id)}),result=insertFragment(g,fragment,make());
  assert.deepEqual(g,before);assert.deepEqual(result.graph.atoms.slice(0,5),g.atoms);assert.deepEqual(result.graph.bonds.slice(0,3),g.bonds);
  const aa=result.graph.atoms.slice(5),bb=result.graph.bonds.slice(3);
  assert.equal(aa[0].mapNum,72);assert.equal(aa[0].chiralTag,1);assert.equal(aa[0].isotope,13);
  assert.deepEqual(bb[1].stereoAtoms,[aa[0].id,aa[3].id]);assert.equal(bb[1].stereo,3);assert.equal(bb[2].direction,2);
  assert.equal(Math.min(...aa.map(a=>a.x))-Math.max(...g.atoms.map(a=>a.x)),3);
  assert.equal(new Set([...result.graph.atoms,...result.graph.bonds].map(a=>a.id)).size,15);
  assert.ok(bb.every(b=>result.createdAtomIds.includes(b.begin)&&result.createdAtomIds.includes(b.end)));
});
test('insert rejects collisions, size overflow and coordinate overflow without mutating base',()=>{
  const g=graph();assert.throws(()=>insertFragment(g,g,()=>g.atoms[0].id),/unique identifiers/);
  const huge={atoms:Array.from({length:512},(_,i)=>atom('o'+i)),bonds:[]};assert.throws(()=>insertFragment(huge,{atoms:[atom('new')],bonds:[]},make()),/limits/);
  assert.throws(()=>insertFragment({atoms:[atom('far',9999)],bonds:[]},{atoms:[atom('f')],bonds:[]},make()),/range/);
});
test('empty-drawing placement centers vertically and retains relative coordinates',()=>{
  const g={atoms:[atom('x',4,8),atom('y',5,10)],bonds:[bond('xy','x','y')]};const result=insertFragment({atoms:[],bonds:[]},g,make());assert.deepEqual(result.graph.atoms.map(a=>[a.x,a.y]),[[0,-1],[1,1]]);
});
test('fidelity gate admits only documented omitted defaults and rejects graph mutations',()=>{
  const expected=insertFragment(graph(),graph(),make()).graph;assert.doesNotThrow(()=>assertFragmentRender(expected,{graph:expected}));
  const normalized=structuredClone(expected);for(const a of normalized.atoms)Object.assign(a,{charge:0,explicitHs:0,noImplicit:false,aromatic:false,radicals:0});assert.doesNotThrow(()=>assertFragmentRender(expected,{graph:normalized}));
  for(const mutate of [g=>g.atoms[0].mapNum++,g=>g.atoms[0].x++,g=>g.atoms.reverse(),g=>g.bonds[1].stereoAtoms.reverse(),g=>g.atoms[5].chiralTag=2,g=>g.bonds.pop()]){const g=structuredClone(expected);mutate(g);assert.throws(()=>assertFragmentRender(expected,{graph:g}));}
});
test('record validation rejects unknown fields and markup stays plain data',()=>{
  const r={schema:'nivram-fragment/1',id:'a'.repeat(32),name:'<img src=x>',graph:graph()};assert.deepEqual(validateFragmentRecord(r),r);
  assert.throws(()=>validateFragmentRecord({...r,path:'/tmp/x'}));assert.throws(()=>validateFragmentRecord({...r,name:'\0'}));assert.throws(()=>validateFragmentRecord({...r,graph:{atoms:[],bonds:[]}}));
});
test('actual job/document stores enforce stale cancellation and one undo transaction',()=>{
  const store=createDocumentStore({schema:'nivram/1',graph:graph()}),before=store.getSnapshot();
  const jobs=createJobController(()=>store.getSnapshot().revision),job=jobs.begin('fragment');
  const candidate=insertFragment(before.document.graph,graph(),make()).graph;
  assertFragmentRender(candidate,{graph:candidate});job.assertCurrent();store.commit({...before.document,graph:candidate},{expectedRevision:job.revision});job.finish();
  assert.equal(store.getSnapshot().revision,1);store.commit(store.historyTarget(),{mode:'undo'});assert.deepEqual(store.getSnapshot().document,before.document);assert.equal(store.historyState().canUndo,false);
  const stale=jobs.begin('fragment');store.commit({...before.document,graph:candidate});assert.throws(()=>stale.assertCurrent(),/changed/);stale.finish();
  const cancel=jobs.begin('fragment');cancel.cancel();assert.throws(()=>cancel.assertCurrent(),/cancelled/);assert.deepEqual(store.getSnapshot().document.graph,candidate);
});
