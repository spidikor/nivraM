import test from 'node:test';
import assert from 'node:assert/strict';
import {createDocumentStore} from '../public/modules/document.mjs';

const project=()=>({schema:'nivram/1',graph:{atoms:[],bonds:[]}});
const candidate=(store,width)=>({...store.getSnapshot().document,export:{...store.getSnapshot().document.export,width}});
const context=x=>({autoFit:false,view:{x,y:12,w:800,h:500}});

test('history context is cloned and never becomes project, saved identity or recovery data',()=>{
  const store=createDocumentStore(project()),before=store.getSnapshot(),view=context(10);
  store.commit(candidate(store,3000),{historyContext:view});view.view.x=999;
  assert.deepEqual(store.historyContext(),context(10));
  const returned=store.historyContext();returned.view.x=500;
  assert.deepEqual(store.historyContext(),context(10));
  assert.deepEqual(store.historyTarget(),before.document);
  assert.deepEqual(Object.keys(store.getSnapshot()).sort(),['dirty','document','revision']);
  assert.equal(JSON.stringify(store.getSnapshot()).includes('autoFit'),false);
  store.markUnsaved();assert.deepEqual(store.historyContext(),context(10));
  store.commit(store.historyTarget(),{mode:'undo',historyContext:context(20)});
  assert.deepEqual(store.getSnapshot().document,before.document);
  assert.deepEqual(store.historyContext(true),context(20));
  const restored=store.getSnapshot();assert.equal(store.markSaved(restored.revision,restored.document),true);
  assert.equal(store.getSnapshot().dirty,false);
  store.commit(store.historyTarget(true),{mode:'redo',historyContext:context(30)});
  assert.deepEqual(store.historyContext(),context(30));
});

test('ordinary edits do not detach import view context and redo follows the same boundary',()=>{
  const store=createDocumentStore(project());
  store.commit(candidate(store,3000),{historyContext:context(1)});
  store.commit(candidate(store,4000));assert.equal(store.historyContext(),null);
  store.commit(store.historyTarget(),{mode:'undo'});
  assert.deepEqual(store.historyContext(),context(1));assert.equal(store.historyContext(true),null);
  store.commit(store.historyTarget(),{mode:'undo',historyContext:context(2)});
  assert.deepEqual(store.historyContext(true),context(2));
  store.commit(store.historyTarget(true),{mode:'redo',historyContext:context(3)});
  assert.deepEqual(store.historyContext(),context(3));assert.equal(store.historyContext(true),null);
  store.commit(store.historyTarget(true),{mode:'redo'});assert.equal(store.historyContext(),null);
  store.commit(store.historyTarget(),{mode:'undo'});assert.deepEqual(store.historyContext(),context(3));
});

test('stale, failed and invalid-context commits preserve both history directions',()=>{
  const store=createDocumentStore(project());
  store.commit(candidate(store,3000),{historyContext:context(1)});
  store.commit(store.historyTarget(),{mode:'undo',historyContext:context(2)});
  const before=store.getSnapshot(),history=store.historyState(),future=store.historyTarget(true);
  let applied=false;
  assert.throws(()=>store.commit(candidate(store,4000),{historyContext:context(3),apply:()=>{throw Error('render failed');}}),/render failed/);
  assert.throws(()=>store.commit(candidate(store,4000),{expectedRevision:0,historyContext:context(3)}),/document changed/);
  assert.throws(()=>store.commit(candidate(store,4000),{historyContext:context(Infinity),apply:()=>{applied=true;}}),/finite/);
  assert.equal(applied,false);assert.deepEqual(store.getSnapshot(),before);assert.deepEqual(store.historyState(),history);
  assert.deepEqual(store.historyTarget(true),future);assert.deepEqual(store.historyContext(true),context(2));
});

test('grouped edits retain the first document and first context in one Undo entry',()=>{
  const store=createDocumentStore(project()),before=store.getSnapshot(),group=store.beginHistoryGroup();
  store.commit(candidate(store,3000),{historyGroup:group,historyContext:context(1)});
  store.commit(candidate(store,4000),{historyGroup:group,historyContext:context(2)});
  store.endHistoryGroup(group);
  assert.deepEqual(store.historyContext(),context(1));assert.deepEqual(store.historyTarget(),before.document);
  store.commit(store.historyTarget(),{mode:'undo',historyContext:context(3)});
  assert.equal(store.historyState().canUndo,false);assert.deepEqual(store.getSnapshot().document,before.document);
});

test('no-op, history limit and a new branch retain existing document history semantics',()=>{
  const store=createDocumentStore(project(),{historyLimit:2}),before=store.getSnapshot();
  let changed;
  store.commit(before.document,{historyContext:context(99),apply:(_next,receipt)=>{changed=receipt.changed;}});
  assert.equal(changed,false);
  assert.deepEqual(store.getSnapshot(),before);assert.equal(store.historyContext(),null);
  for(const [width,x] of [[3000,1],[4000,2],[5000,3]])store.commit(candidate(store,width),{historyContext:context(x)});
  store.commit(store.historyTarget(),{mode:'undo',historyContext:context(4)});
  assert.deepEqual(store.historyContext(),context(2));
  store.commit(store.historyTarget(),{mode:'undo',historyContext:context(5)});
  assert.equal(store.historyState().canUndo,false);assert.equal(store.historyContext(),null);
  store.commit(candidate(store,6000));
  assert.equal(store.historyState().canRedo,false);assert.equal(store.historyContext(true),null);
  assert.equal(store.historyContext(),null);
});
