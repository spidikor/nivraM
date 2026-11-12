import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createDocumentStore} from '../public/modules/document.mjs';
// Execute the exact app method with controlled render/change dependencies, not the browser application.
const source=readFileSync(new URL('../public/app.mjs',import.meta.url),'utf8');
const start=source.indexOf('  async openProject(project,'),end=source.indexOf('\n  }\n});',start)+4;
assert(start>=0&&end>start);
const make=new Function('documentStore','jobs','validateProject','change','notify',`return ({${source.slice(start,end)}}).openProject;`);
const graph=id=>({atoms:[{id,symbol:'C',x:0,y:0}],bonds:[]});
for(const race of ['none','graph','style','context','undo']){
 const store=createDocumentStore({schema:'nivram/1',graph:graph('old')}),initial=store.getSnapshot();
 const candidate={...initial.document,graph:graph('accepted')};
 const open=make(store,{begin:()=>({revision:initial.revision,cancel(){},finish(){}})},x=>x,async()=>{
  store.commit(candidate,{expectedRevision:initial.revision});
  if(race!=='none'){
   const snap=store.getSnapshot();
   if(race==='undo')store.commit(initial.document,{mode:'undo'});
   else store.commit({...snap.document,...(race==='graph'?{graph:graph('later')}:race==='style'?{style:{...snap.document.style,bondWidth:2}}:{analysis:{note:'later'}})});
  }
  return true;
 },message=>console.log(race,message));
 const result=await open(candidate,{expectedRevision:initial.revision,asUnsaved:true,returnSnapshot:true});assert.equal(result.accepted,true);
 if(race==='none'){assert.equal(result.snapshot.revision,initial.revision+2);assert.deepEqual(result.snapshot.document.graph,candidate.graph);}else assert.equal(result.snapshot,null,race);
}
console.log('PASS exact app openProject method: receipt only for uninterrupted commit; graph/style/context/undo revision races omit optional receipt. Controlled dependencies, no rendering or Windows qualification.');
