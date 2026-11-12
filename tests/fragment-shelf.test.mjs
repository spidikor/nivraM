import test from 'node:test';
import assert from 'node:assert/strict';
import {initializeFragmentShelf} from '../public/modules/fragment-shelf.mjs';
const graph={atoms:[{id:'a',symbol:'C',x:0,y:0,mapNum:9}],bonds:[]};
const record={schema:'nivram-fragment/1',id:'a'.repeat(32),name:'<b>name</b>',graph};
function fixture(native){
  class Element{
    constructor(){this.value='';this.textContent='';this.children=[];this.listeners={};}
    addEventListener(type,fn){this.listeners[type]=fn;}
    replaceChildren(){this.children=[];}
    append(el){this.children.push(el);}
    click(){return this.listeners.click();}
  }
  const elements=new Map();const document={getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement:()=>new Element()};
  let busy=false;const calls=[];
  const shelf=initializeFragmentShelf({document,getSnapshot:()=>({revision:7,document:{schema:'nivram/1',graph}}),getSelection:()=>({atoms:[],bonds:[]}),isBusy:()=>busy,native:()=>native,insert:async(g,s)=>{calls.push({g,s});return true;},duplicate:async(g,s)=>{calls.push({g,s});return true;}});
  return {shelf,el:id=>document.getElementById(id),calls,setBusy:value=>busy=value};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('desktop missing: storage is disabled but duplication uses captured source',async()=>{
  const f=fixture(null);await f.shelf.reload();assert.equal(f.el('fragment-save').disabled,true);assert.equal(f.el('fragment-duplicate').disabled,false);
  f.el('fragment-duplicate').click();await flush();assert.deepEqual(f.calls,[{g:graph,s:{revision:7,document:{schema:'nivram/1',graph}}}]);
});
test('native records are text-only labels and insertion uses selected graph without setting destination',async()=>{
  const f=fixture({list_fragments:async()=>({fragments:[record],invalidRecords:0}),save_fragment:async()=>{throw new Error('unused');}});await f.shelf.reload();
  const option=f.el('fragment-choice').children[0];assert.equal(option.textContent,'<b>name</b>');assert.equal(option.innerHTML,undefined);
  f.el('fragment-choice').value=record.id;f.el('fragment-insert').click();await flush();assert.deepEqual(f.calls[0].g,graph);assert.equal(f.calls[0].s.revision,7);
});
test('save acknowledgements bind exact captured graph; uncertain saves are not retried',async()=>{
  let saves=0;const f=fixture({list_fragments:async()=>({fragments:[],invalidRecords:0}),save_fragment:async(name,g)=>{saves++;return {status:'saved',fragment:{...record,name,graph:{...g,atoms:[{...g.atoms[0],mapNum:0}]}}};}});
  f.el('fragment-name').value='Named';await f.el('fragment-save').click();assert.equal(saves,1);assert.match(f.el('fragment-status').textContent,/could not be confirmed/);assert.equal(f.calls.length,0);
});
test('pending close review discards late UI results and does not pretend to cancel publication',async()=>{
  let done;const f=fixture({list_fragments:async()=>({fragments:[],invalidRecords:0}),save_fragment:()=>new Promise(resolve=>done=resolve)});
  f.el('fragment-name').value='Named';const work=f.el('fragment-save').click();assert.equal(f.el('fragment-duplicate').disabled,true);f.shelf.beginClose();
  done({status:'saved',fragment:{...record,name:'Named'}});await work;assert.equal(f.el('fragment-choice').children.length,0);f.shelf.endClose();assert.match(f.el('fragment-status').textContent,/Reload.*pending/);
});
test('busy and invalid listings do not insert or replace current drawing',async()=>{
  const f=fixture({list_fragments:async()=>({fragments:[{...record,path:'x'}],invalidRecords:0}),save_fragment:()=>{throw new Error('unused');}});
  await f.shelf.reload();assert.match(f.el('fragment-status').textContent,/could not be read/);f.setBusy(true);f.shelf.refresh();assert.equal(f.el('fragment-duplicate').disabled,true);f.el('fragment-duplicate').click();await flush();assert.equal(f.calls.length,0);
});
