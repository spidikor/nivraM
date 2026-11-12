/** Execute the production app entry function with actual graph/store/jobs helpers.
 * The response transport is a contract fixture, not a chemical validation claim. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {insertFragment,assertFragmentRender} from '../public/modules/fragments.mjs';
import {createDocumentStore,validateProject} from '../public/modules/document.mjs';
import {createJobController} from '../public/modules/jobs.mjs';
const app=readFileSync(new URL('../public/app.mjs',import.meta.url),'utf8');
const entry=app.slice(app.indexOf('async function insertReusableFragment('),app.indexOf('\nfragmentShelf=initializeFragmentShelf'));
const g={atoms:[{id:'original',symbol:'C',x:0,y:0,mapNum:123}],bonds:[]};
function fixture(){
  const store=createDocumentStore({schema:'nivram/1',graph:g,style:{labelSize:22},export:{width:1440}}),jobs=createJobController(()=>store.getSnapshot().revision);let uid=0,respond;
  const context={busy:false,documentStore:store,insertFragment,validateProject,assertFragmentRender,uid:p=>p+(++uid),
    runDocumentOperation:async(action,data,options)=>{
      assert.equal(action,'render');assert.equal(options.shouldFit,true);assert.equal(options.resetSelection,true);
      const task=jobs.begin('document');
      try{const result=await new Promise(resolve=>respond=resolve);task.assertCurrent();options.verifyResult(result);store.commit({...options.baseProject,graph:result.graph},{expectedRevision:task.revision});return true;}
      finally{task.finish();}
    }};
  context.insert=new Function(...Object.keys(context),entry+';return insertReusableFragment;')(...Object.values(context));
  return {context,store,jobs,respond:result=>respond(result)};
}
test('production insertion entry binds source and renders exactly once before one history commit',async()=>{
  const f=fixture(),source=f.store.getSnapshot(),work=f.context.insert(g,source);
  const intended=insertFragment(g,g,(()=>{let n=0;return p=>p+(++n);})()).graph;
  f.respond({graph:intended,scene:{style:source.document.style}});assert.equal(await work,true);
  assert.equal(f.store.getSnapshot().revision,1);assert.deepEqual(f.store.getSnapshot().document.export,source.document.export);assert.deepEqual(f.store.getSnapshot().document.style,source.document.style);
  f.store.commit(f.store.historyTarget(),{mode:'undo'});assert.deepEqual(f.store.getSnapshot().document,source.document);assert.equal(f.store.historyState().canUndo,false);
});
test('production entry refuses already stale source, changed renderer graph and changed appearance',async()=>{
  const f=fixture(),source=f.store.getSnapshot();await assert.rejects(f.context.insert(g,{...source,revision:99}),/changed/);
  const work=f.context.insert(g,source);f.respond({graph:g,scene:{style:source.document.style}});await assert.rejects(work,/changed/);assert.deepEqual(f.store.getSnapshot(),source);
  const next=f.context.insert(g,source),intended=insertFragment(g,g,(()=>{let n=1;return p=>p+(++n);})()).graph;
  f.respond({graph:intended,scene:{style:{...source.document.style,labelSize:30}}});await assert.rejects(next,/appearance/);assert.deepEqual(f.store.getSnapshot(),source);
});
test('late cancellation and close review cannot commit a pending insertion',async()=>{
  for(const close of [false,true]){const f=fixture(),source=f.store.getSnapshot(),work=f.context.insert(g,source);if(close)f.store.suspendMutations();else f.jobs.cancelAll();const intended=insertFragment(g,g,(()=>{let n=0;return p=>p+(++n);})()).graph;f.respond({graph:intended,scene:{style:source.document.style}});await assert.rejects(work,close?/closing/:/cancelled/);assert.deepEqual(f.store.getSnapshot(),source);assert.equal(f.store.historyState().canUndo,false);}
});
