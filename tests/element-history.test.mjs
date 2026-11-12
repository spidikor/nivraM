import test from 'node:test';
import assert from 'node:assert/strict';
import {ELEMENT_SYMBOLS,canonicalElement,isElementSymbol,isElementPrefix,COMMON_ELEMENTS,MORE_ELEMENTS} from '../public/modules/elements.mjs';
import {createDocumentStore,validateProject} from '../public/modules/document.mjs';

const project=symbol=>({schema:'nivram/1',graph:{atoms:[{id:'target',symbol,x:0,y:0}],bonds:[]}});
test('all 118 canonical elements are separate from the 32-element palette',()=>{
  assert.equal(ELEMENT_SYMBOLS.length,118);assert.equal(new Set(ELEMENT_SYMBOLS).size,118);
  assert.equal(ELEMENT_SYMBOLS[0],'H');assert.equal(ELEMENT_SYMBOLS.at(-1),'Og');
  assert.equal(new Set([...COMMON_ELEMENTS,...MORE_ELEMENTS]).size,32);
  for(const symbol of ELEMENT_SYMBOLS){assert.ok(isElementSymbol(symbol));assert.equal(canonicalElement(symbol.toUpperCase()),symbol);validateProject(project(symbol));}
  assert.equal(canonicalElement('cl'),'Cl');assert.equal(canonicalElement('u'),'U');
  for(const value of ['Aa','D','T','Cll',' Carbon','',null]){assert.equal(canonicalElement(value),null);assert.throws(()=>validateProject(project(value)),/element/);}
  assert.equal(isElementPrefix('l'),true);assert.equal(isElementPrefix('j'),false);
});
test('two visible accepted element changes form one undo and one redo',()=>{
  const store=createDocumentStore(project('N')),group=store.beginHistoryGroup();
  store.commit(project('C'),{historyGroup:group});assert.equal(store.getSnapshot().revision,1);
  store.commit(project('Cl'),{historyGroup:group});assert.equal(store.getSnapshot().revision,2);
  store.endHistoryGroup(group);
  store.commit(store.historyTarget(),{mode:'undo'});assert.equal(store.getSnapshot().document.graph.atoms[0].symbol,'N');
  assert.equal(store.historyState().canUndo,false);
  store.commit(store.historyTarget(true),{mode:'redo'});assert.equal(store.getSnapshot().document.graph.atoms[0].symbol,'Cl');
});
test('saved intermediate graph cannot mark the later grouped edit clean',()=>{
  const store=createDocumentStore(project('N')),group=store.beginHistoryGroup();
  store.commit(project('C'),{historyGroup:group});const saving=store.getSnapshot();
  store.commit(project('Cl'),{historyGroup:group});assert.equal(store.markSaved(saving.revision,saving.document),true);
  assert.equal(store.getSnapshot().dirty,true);
  store.commit(store.historyTarget(),{mode:'undo'});assert.equal(store.getSnapshot().dirty,true);
});
test('intervening edit and explicit expiry prevent late coalescing',()=>{
  const store=createDocumentStore(project('N')),group=store.beginHistoryGroup();
  store.commit(project('C'),{historyGroup:group});store.commit(project('O'));
  const before=store.getSnapshot();assert.throws(()=>store.commit(project('Cl'),{historyGroup:group}),/no longer current/);
  assert.deepEqual(store.getSnapshot(),before);
  const second=store.beginHistoryGroup();store.endHistoryGroup(second);
  assert.throws(()=>store.commit(project('F'),{historyGroup:second}),/no longer current/);
});
test('failed view update retains prior history and permits the same valid gesture',()=>{
  const store=createDocumentStore(project('N')),group=store.beginHistoryGroup();
  store.commit(project('C'),{historyGroup:group});const before=store.getSnapshot();
  assert.throws(()=>store.commit(project('Cl'),{historyGroup:group,apply(){throw new Error('view failed');}}),/view failed/);
  assert.deepEqual(store.getSnapshot(),before);
  store.commit(project('Cl'),{historyGroup:group});store.commit(store.historyTarget(),{mode:'undo'});
  assert.equal(store.getSnapshot().document.graph.atoms[0].symbol,'N');
});
test('no-op first letter does not consume a history slot; close invalidates token',()=>{
  const store=createDocumentStore(project('C')),group=store.beginHistoryGroup();
  store.commit(project('C'),{historyGroup:group});assert.equal(store.historyState().canUndo,false);
  store.commit(project('Cl'),{historyGroup:group});store.commit(store.historyTarget(),{mode:'undo'});
  assert.equal(store.getSnapshot().document.graph.atoms[0].symbol,'C');assert.equal(store.historyState().canUndo,false);
  const late=store.beginHistoryGroup();store.suspendMutations();store.resumeMutations();
  assert.throws(()=>store.commit(project('F'),{historyGroup:late}),/no longer current/);
});
