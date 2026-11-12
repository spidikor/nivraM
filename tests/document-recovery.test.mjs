import test from 'node:test';
import assert from 'node:assert/strict';
import {createDocumentStore} from '../public/modules/document.mjs';

const empty=()=>({schema:'nivram/1',graph:{atoms:[],bonds:[]}});
test('host recovery identity binds once per editor lifetime and stays outside project contents',()=>{
  const store=createDocumentStore(empty()),original=store.getSnapshot();
  store.setRecoveryGeneration('a'.repeat(32));
  store.setRecoveryGeneration('a'.repeat(32));
  assert.throws(()=>store.setRecoveryGeneration('b'.repeat(32)));
  let now=store.getSnapshot();
  assert.deepEqual(now.document,original.document);assert.equal(now.revision,0);assert.equal(now.dirty,false);
  assert.equal(now.recoveryGeneration,'a'.repeat(32));
  store.commit({...now.document,export:{...now.document.export,width:1200}});
  store.markUnsaved();now=store.suspendMutations();
  assert.equal(now.recoveryGeneration,'a'.repeat(32));assert.equal(now.revision,2);
  store.resumeMutations();assert.equal(store.getSnapshot().recoveryGeneration,'a'.repeat(32));
  assert.equal(createDocumentStore(empty()).getSnapshot().recoveryGeneration,undefined);
});

test('invalid recovery identity never binds an editor',()=>{
  for(const value of [null,undefined,0,'','a'.repeat(31),'A'.repeat(32),'a'.repeat(33)]){
    const store=createDocumentStore(empty());
    assert.throws(()=>store.setRecoveryGeneration(value));
    assert.equal(store.getSnapshot().recoveryGeneration,undefined);
  }
});
