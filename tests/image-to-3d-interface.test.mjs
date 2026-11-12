/** Opt-in offline integration, not optical inference or browser acceptance. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createHash,webcrypto} from 'node:crypto';
import {createDocumentStore,validateProject} from '../public/modules/document.mjs';
import {createImageReview} from '../public/modules/image-review.mjs';
import {mountImageTo3d} from '../public/modules/image-to-3d.mjs';
import {createConformerController} from '../public/modules/conformer.mjs';

const python=process.env.NIVRAM_TEST_PYTHON;
assert(python,'Set NIVRAM_TEST_PYTHON to the approved scientific interpreter.');
const root=fileURLToPath(new URL('../',import.meta.url));
const helper=fileURLToPath(new URL('image-to-3d-interface-helper.py',import.meta.url));
const hash=s=>createHash('sha256').update(s).digest('hex');
const clone=x=>structuredClone(x);
function chemistry(data){
  const run=spawnSync(python,[helper],{cwd:root,env:{...process.env,PYTHONPATH:root,PYTHONDONTWRITEBYTECODE:'1'},input:JSON.stringify(data),encoding:'utf8',timeout:45000,maxBuffer:4*1024*1024});
  assert.equal(run.status,0,run.stderr||String(run.error));return JSON.parse(run.stdout);
}
class Events{
  handlers=new Map();hidden=false;
  addEventListener(type,fn){if(!this.handlers.has(type))this.handlers.set(type,new Set());this.handlers.get(type).add(fn);}
  removeEventListener(type,fn){this.handlers.get(type)?.delete(fn);}
  async fire(type,detail){for(const fn of [...this.handlers.get(type)||[]])await fn({detail});}
}
const defer=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function fixture(scientific){
  const store=createDocumentStore({schema:'nivram/1',graph:{atoms:[{id:'previous',symbol:'F',x:0,y:0}],bonds:[]}});
  const doc={...store,ready:Promise.resolve()},before=store.getSnapshot(),events=new Events(),button=new Events();
  const calls={generation:[],cancellation:[],results:[],states:[],opened:0,accepted:0},control={};
  // Adapter mirrors the app's successful committed-document receipt contract.
  // It is not the full app editor/render lifecycle and is deliberately labeled.
  const editor={...doc,async openProject(project,options){
    assert.equal(options.returnSnapshot,true);if(options.signal.aborted)return false;
    store.commit(project,{expectedRevision:options.expectedRevision});store.markUnsaved();calls.accepted++;
    return {accepted:true,snapshot:store.getSnapshot()};
  }};
  const native={
    async inspect_image(jobId){return {...clone(scientific.inspection),jobId};},
    async preview_image_components(_jobId,ids){assert.deepEqual(ids,scientific.selected);return clone(scientific.candidate);},
    async prepare_image_import(_jobId,ids){assert.deepEqual(ids,scientific.selected);return clone(scientific.prepared);},
    async cancel_image_import(){return {status:'cancelled'};}
  };
  const review=createImageReview({editor,native,reviewSource:async()=>true,newId:()=> 'review_fixture_001',
    render:async(graph)=>{assert.deepEqual(graph,scientific.corrected.graph);return clone(scientific.corrected);},
    prepare:(response,base)=>({project:validateProject({...base,graph:response.graph,style:response.scene.style}),scene:response.scene})});
  const controller=createConformerController({getDocument:()=>doc,cryptoApi:webcrypto,
    onResult:r=>calls.results.push(r),onState:(...args)=>calls.states.push(args),request:async(route,payload)=>{
      if(route==='conformer/cancel'){calls.cancellation.push(payload);return {};}
      assert.equal(route,'conformer');calls.generation.push(clone(payload));
      const result=chemistry({action:'conformer',payload});
      if(control.hold){control.started.resolve();await control.hold.promise;}
      return result;
    }});
  const bridge=mountImageTo3d({events,button,getDocument:()=>doc,open:()=>calls.opened++,generate:options=>controller.generate(options)});
  return {store,before,review,controller,button,events,calls,control,close(){review.dispose();bridge.destroy();controller.destroy();}};
}

for(const sample of [
  {name:'corrected ethanol metadata to ethylamine',smiles:'CCO',correctedSmiles:'CCN'},
  {name:'specified stereochemistry',smiles:'C[C@H](O)C(=O)O'},
  {name:'isotope and charge',smiles:'[13CH3][NH3+]'}
])test(`real PNG review receipt to explicit local 3D: ${sample.name}`,async()=>{
  const scientific=chemistry({action:'fixture',...sample}),f=fixture(scientific);
  try{
    assert.equal(scientific.rdkitVersion,'2025.09.4');
    assert.equal(hash(Buffer.from(scientific.pngBase64,'base64')),scientific.sourceSha256);
    assert.equal(scientific.inspection.inspection.recognition.status,'not_run');
    await f.review.inspect();await f.review.preview(scientific.selected);
    assert.deepEqual(f.store.getSnapshot(),f.before);assert.equal(f.calls.generation.length,0);assert(f.button.hidden);
    await assert.rejects(f.review.accept(),/Confirm/);assert.equal(f.calls.accepted,0);
    if(sample.correctedSmiles)await f.review.correct(scientific.corrected.graph);
    const accepted=await f.review.accept({confirmed:true}),snapshot=f.store.getSnapshot();
    assert(accepted.continuationBinding,'Actual review acceptance must issue continuation receipt');
    assert.equal(accepted.continuationBinding.revision,snapshot.revision);
    assert.deepEqual(JSON.parse(accepted.continuationBinding.graphText),snapshot.document.graph);
    assert.equal(snapshot.document.analysis.imageImport.sourceSha256,scientific.sourceSha256);
    assert.equal(snapshot.document.analysis.imageImport.kind,'png-metadata-component-reconstruction');
    assert.equal(snapshot.document.analysis.imageImport.recognition,undefined);
    if(sample.correctedSmiles){
      assert.deepEqual(snapshot.document.graph,scientific.corrected.graph);
      assert.deepEqual(snapshot.document.analysis.imageImport.correction.baselineGraph,scientific.candidate.graph);
      assert.deepEqual(snapshot.document.analysis.imageImport.correction.reviewedGraph,scientific.corrected.graph);
    }else{
      // Document JSON cloning normalizes IEEE negative zero; this is not a
      // coordinate displacement or a chemical-identity change.
      assert.deepEqual(snapshot.document.graph,JSON.parse(JSON.stringify(scientific.candidate.graph)));
      for(const [i,atom] of snapshot.document.graph.atoms.entries())for(const [axis,j] of [['x',0],['y',1]])
        assert(Math.abs(atom[axis]-scientific.originalCoordinates[i][j])<0.00011,'MOL metadata coordinate precision retained');
    }
    await f.events.fire('nivram-image-accepted',accepted.continuationBinding);
    assert(!f.button.hidden);assert.equal(f.calls.generation.length,0,'Acceptance/offer must not generate');
    await f.button.fire('click');
    assert.equal(f.calls.opened,1);assert.equal(f.calls.generation.length,1);assert.equal(f.calls.results.length,1);assert(f.button.hidden);
    const result=f.calls.results[0],sent=f.calls.generation[0];
    assert.equal(sent.graphText,accepted.continuationBinding.graphText);
    assert.equal(sent.sourceContextText,accepted.continuationBinding.sourceContextText);
    assert.equal(result.provenance.sourceSha256,hash(sent.graphText));
    assert.equal(result.provenance.sourceContextSha256,hash(sent.sourceContextText));
    assert.equal(result.provenance.outputSha256,hash(result.scene.sourceText));
    assert.equal(result.provenance.sourceContext.imageImport.sourceSha256,scientific.sourceSha256);
    assert.equal(result.scene.kind,'generated_coordinates');assert.equal(result.provenance.generated,true);
    assert.match(f.calls.states.at(-1)[1],/Not an experimental structure/);
    const identity=chemistry({action:'identity',graph:snapshot.document.graph,molText:result.scene.sourceText});
    assert.equal(identity.generated,identity.source);
    for(const [i,atom] of result.scene.atoms.entries()){
      assert.equal(atom.sourceId,snapshot.document.graph.atoms[i].id);
      for(const [axis,j] of [['x',0],['y',1],['z',2]])assert(Math.abs(atom[axis]-identity.coordinates[i][j])<1e-6);
    }
    assert.deepEqual(f.store.getSnapshot(),snapshot,'3D generation must not change canonical 2D graph, layout, provenance or history revision');
    for(const key of ['sourceName','sourceStorage','baselineGraph','reviewedGraph'])assert(!sent.sourceContextText.includes(`"${key}"`));
    assert.deepEqual(f.store.historyTarget(),f.before.document,'Accepted PNG remains one undo action');
    f.store.commit(f.store.historyTarget(),{mode:'undo'});
    assert.deepEqual(f.store.getSnapshot().document,f.before.document);assert.equal(f.controller.exportContext(),null);
  }finally{f.close();}
});

test('accepted receipt fences subsequent edits and cancelled/late actual worker responses',async()=>{
  const scientific=chemistry({action:'fixture',smiles:'CCO'});
  for(const mode of ['before-click','in-flight-edit','in-flight-cancel']){
    const f=fixture(scientific);
    try{
      await f.review.inspect();await f.review.preview(scientific.selected);
      const accepted=await f.review.accept({confirmed:true});await f.events.fire('nivram-image-accepted',accepted.continuationBinding);
      const edit=()=>{const snapshot=f.store.getSnapshot();const graph=clone(snapshot.document.graph);graph.atoms[0].x+=1;f.store.commit({...snapshot.document,graph});};
      if(mode==='before-click'){
        edit();await f.button.fire('click');assert(f.button.hidden);assert.equal(f.calls.generation.length,0);
      }else{
        f.control.hold=defer();f.control.started=defer();const pending=f.button.fire('click');
        await f.control.started.promise;
        if(mode==='in-flight-edit')edit();else f.controller.cancel();
        const after=f.store.getSnapshot();f.control.hold.resolve();await pending;
        assert.equal(f.calls.generation.length,1);assert.equal(f.calls.cancellation.length,1);
        const request=f.calls.generation[0];assert.deepEqual(f.calls.cancellation[0],{clientId:request.clientId,jobId:request.jobId,expiresAt:request.expiresAt});
        assert.deepEqual(f.store.getSnapshot(),after);assert.equal(f.controller.exportContext(),null);
      }
      assert.equal(f.calls.results.length,0,'Stale/cancelled response cannot publish a model');
    }finally{f.control.hold?.resolve();f.close();}
  }
});
