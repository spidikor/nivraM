import test from 'node:test';
import assert from 'node:assert/strict';
import {createDocumentStore} from '../public/modules/document.mjs';

test('close review centrally blocks document and history mutations until explicitly resumed',()=>{
  const store=createDocumentStore({schema:'nivram/1',graph:{atoms:[],bonds:[]}});
  store.commit({...store.getSnapshot().document,graph:{atoms:[{id:'a',symbol:'C',x:0,y:0}],bonds:[]}});
  const frozen=store.suspendMutations(),history=store.historyState();let applied=false;
  const changed={...frozen.document,export:{...frozen.document.export,width:1000}};
  assert.throws(()=>store.commit(changed,{apply:()=>{applied=true;}}),/closing/);
  assert.throws(()=>store.commit(store.historyTarget(),{mode:'undo'}),/closing/);
  assert.throws(()=>store.markUnsaved(),/closing/);assert.equal(applied,false);
  assert.deepEqual(store.getSnapshot(),frozen);assert.deepEqual(store.historyState(),history);
  assert.equal(store.markSaved(frozen.revision,frozen.document),true);
  assert.deepEqual(store.getSnapshot().document,frozen.document);assert.equal(store.getSnapshot().revision,frozen.revision);
  store.resumeMutations();store.commit(changed);assert.equal(store.getSnapshot().document.export.width,1000);
});
