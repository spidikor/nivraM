import test from 'node:test';
import assert from 'node:assert/strict';
import {createDocumentStore,validateProject} from '../public/modules/document.mjs';
import {createImageReview} from '../public/modules/image-review.mjs';

const sourceHash='a'.repeat(64),cropHash='b'.repeat(64),modelHash='32821fa4ba8d17f9a92e374601438c5e757947dbf1141e9ad0ef51cccfa1c501';
const graph=(symbol='C')=>({atoms:[{id:'ocr-a0',symbol,x:3,y:4}],bonds:[]});
const rect={x:2,y:5,width:50,height:40};
const deferred=()=>{let resolve;return {promise:new Promise(r=>{resolve=r;}),resolve:value=>resolve(value)};};
function fixture({configured=true,reviewRegion=async()=>true}={}){
  const store=createDocumentStore({schema:'nivram/1',graph:graph('O')}),before=store.getSnapshot(),calls={recognize:[],regions:[],preview:[],open:0,cancel:[],tokens:[]},fault={};
  const metadata={id:'metadata-1',source:'rdkit',status:'supported',graph:{atoms:[{id:'m1',symbol:'N',x:0,y:0}],bonds:[]}};
  const inspection={schemaVersion:1,originalSha256:sourceHash,image:{width:100,height:100},engine:{name:'RDKit',version:'fixture'},candidates:[metadata]};
  const recognition={method:'MolScribe',modelSha256:modelHash,inputSha256:cropHash,confidence:0.73,confidenceMeaning:'uncalibrated model score',coordinates:'preserved-from-recognized-molfile'};
  const optical={id:'optical-1',source:'optical-recognition',status:'supported',graph:graph(),recognition};
  let sequence=0,lastRegion;
  const preview=ids=>({graph:{atoms:ids.flatMap(id=>(id==='optical-1'?optical:metadata).graph.atoms),bonds:[]},scene:{style:before.document.style},sourceSha256:sourceHash,selectedComponents:ids,warnings:['Review the optical proposal against the crop.']});
  const native={
    async inspect_image(jobId){return {status:'inspected',jobId,name:'optical.png',inspection:structuredClone(inspection),reviewAvailable:true,regionPreviewAvailable:true,opticalRecognition:{status:configured?'configured':'missing-model',message:'The local model must be configured.',modelSha256:modelHash}};},
    async recognize_image(jobId,requestId,crop,token){
      calls.recognize.push({jobId,requestId,crop,token});
      const region={schemaVersion:1,jobId,requestId,sourceSha256:sourceHash,sourceWidth:100,sourceHeight:100,mode:'crop',rect:{...crop},width:crop.width,height:crop.height,resampling:'none',pngBase64:'aGVsbG8=',pngSha256:cropHash,navigationOnly:false,reviewToken:'optical_crop_token'};
      lastRegion=region;
      const result={status:'recognized',jobId,requestId,inspection:{...structuredClone(inspection),candidates:[structuredClone(metadata),structuredClone(optical)]},region,selectedComponents:['optical-1']};
      return fault.recognize?fault.recognize(result):result;
    },
    async preview_image_components(_jobId,ids){calls.preview.push(ids);return preview(ids);},
    async preview_image_region(){throw new Error('Optical crops should be recognized again.');},
    async prepare_image_import(_jobId,ids,_style,token){
      calls.tokens.push(token);
      const {requestId,sourceSha256,sourceWidth,sourceHeight,rect,width,height,pngSha256}=lastRegion;
      const result={...preview(ids),provenance:{kind:'png-optical-reconstruction',sourceSha256,selectedComponents:ids,recognition:structuredClone(recognition),reviewedRegion:{kind:'crop',requestId,sourceSha256,sourceWidth,sourceHeight,rect,width,height,pngSha256,scale:'one-source-pixel-per-image-pixel',metadataCorrespondence:'unverified optical recognition of this crop'}}};
      return fault.prepare?fault.prepare(result):result;
    },
    async cancel_image_region(jobId,requestId){calls.regions.push({jobId,requestId});},
    async cancel_image_import(jobId){calls.cancel.push(jobId);}
  };
  const editor={getSnapshot:store.getSnapshot,subscribe:store.subscribe,async openProject(document,options){calls.open++;if(options.signal.aborted)return false;store.commit(document,{expectedRevision:options.expectedRevision});store.markUnsaved();return true;}};
  const review=createImageReview({editor,native,sessionToken:'local-session',reviewSource:async()=>true,reviewRegion,newId:()=>`optical_request_${++sequence}`,render:async(graph,style)=>({graph,scene:{style}}),prepare:(response,base)=>({project:validateProject({...base,graph:response.graph,style:response.scene.style}),scene:response.scene})});
  return {review,store,before,calls,fault,recognition,inspection,native};
}

test('optical crop proposal is explicitly requested, corrected in isolation, and accepted with model and crop provenance',async()=>{
  const f=fixture();await f.review.inspect();assert.equal(f.calls.recognize.length,0);
  const result=await f.review.recognize(rect);
  assert.equal(f.calls.recognize[0].token,'local-session');assert.deepEqual(f.calls.recognize[0].crop,rect);
  assert.deepEqual(result.prepared.project.graph,graph());assert.equal(result.inspection.inspection.candidates.at(-1).id,'optical-1');
  assert.equal(f.review.sourceReviewAvailable,true);assert.deepEqual(f.store.getSnapshot(),f.before);
  await assert.rejects(f.review.accept(),/Confirm/);
  const corrected=graph('N');corrected.atoms[0].charge=1;await f.review.correct(corrected);
  assert.deepEqual(f.store.getSnapshot(),f.before);await f.review.accept({confirmed:true});
  const imported=f.store.getSnapshot().document,provenance=imported.analysis.imageImport;
  assert.deepEqual(imported.graph,corrected);assert.equal(provenance.kind,'png-optical-reconstruction');assert.deepEqual(provenance.recognition,f.recognition);
  assert.deepEqual(provenance.reviewedRegion.rect,rect);assert.equal(provenance.reviewedRegion.pngSha256,cropHash);
  assert.deepEqual(provenance.correction.baselineGraph,graph());assert.deepEqual(provenance.correction.reviewedGraph,corrected);
  assert.deepEqual(f.calls.tokens,['optical_crop_token']);assert.equal(f.calls.open,1);
  storeUndo(f);f.review.dispose();
});
function storeUndo(f){f.store.commit(f.store.historyTarget(),{mode:'undo'});assert.deepEqual(f.store.getSnapshot().document,f.before.document);assert.equal(f.store.historyState().canUndo,false);}

test('unconfigured recognition and invalid crop never dispatch a model operation',async()=>{
  const f=fixture({configured:false});await f.review.inspect();await assert.rejects(f.review.recognize(rect),/configured/);assert.equal(f.calls.recognize.length,0);f.review.dispose();
  const ready=fixture();await ready.review.inspect();await assert.rejects(ready.review.recognize({...rect,width:200}));assert.equal(ready.calls.recognize.length,0);ready.review.dispose();
});

test('setup availability refresh enables the current review without reinspection or recognition',async()=>{
  const f=fixture({configured:false});const original=await f.review.inspect();
  assert.equal(f.review.updateOpticalAvailability({status:'configured',message:'Runtime checked on recognition.',modelSha256:modelHash},original.jobId),true);
  assert.deepEqual(f.review.inspection.inspection,original.inspection);assert.equal(Object.isFrozen(f.review.inspection.opticalRecognition),true);
  assert.equal(original.opticalRecognition.status,'missing-model');assert.equal(f.calls.recognize.length,0);assert.equal(f.calls.cancel.length,0);
  assert.deepEqual(f.store.getSnapshot(),f.before);await f.review.recognize(rect);assert.equal(f.calls.recognize.length,1);f.review.dispose();
});

test('setup refresh preserves corrected draft history, crop authority and acceptance provenance',async()=>{
  const f=fixture();await f.review.inspect();await f.review.recognize(rect);await f.review.correct(graph('N'));
  const original=f.review.inspection,history=f.review.correctionHistory;
  assert.equal(f.review.updateOpticalAvailability({status:'configured',message:'Saved model verified; runtime checked later.',modelSha256:modelHash},original.jobId),true);
  assert.deepEqual(f.review.inspection.inspection,original.inspection);assert.deepEqual(f.review.correctionHistory,history);assert.equal(f.review.sourceReviewAvailable,true);
  assert.deepEqual((await f.review.undoCorrection()).project.graph,graph());assert.deepEqual((await f.review.redoCorrection()).project.graph,graph('N'));
  await f.review.accept({confirmed:true});assert.deepEqual(f.store.getSnapshot().document.graph,graph('N'));
  assert.deepEqual(f.store.getSnapshot().document.analysis.imageImport.recognition,f.recognition);storeUndo(f);f.review.dispose();
});

test('stale, invalid and busy setup updates cannot alter another review or an active request',async()=>{
  const f=fixture();const prior=await f.review.inspect();await f.review.inspect();const current=f.review.inspection;
  const available={status:'configured',message:'Configured.',modelSha256:modelHash};
  assert.equal(f.review.updateOpticalAvailability(available,prior.jobId),false);assert.equal(f.review.inspection,current);
  assert.throws(()=>f.review.updateOpticalAvailability({...available,modelSha256:'c'.repeat(64)},current.jobId));assert.equal(f.review.inspection,current);
  const late=deferred();let receipt;f.fault.recognize=result=>{receipt=result;return late.promise;};const pending=f.review.recognize(rect);
  assert.equal(f.review.updateOpticalAvailability(available,current.jobId),false);late.resolve(receipt);await pending;
  f.review.cancel();assert.equal(f.review.updateOpticalAvailability(available,current.jobId),false);f.review.dispose();
});

test('source, crop, model and proposal mismatches cannot establish optical review authority',async()=>{
  const mutations=[
    r=>r.requestId='stale',r=>r.selectedComponents=['metadata-1'],r=>r.inspection.originalSha256='c'.repeat(64),
    r=>r.inspection.image.width=101,r=>r.inspection.engine.version='changed',r=>r.inspection.candidates[0].graph.atoms[0].symbol='O',
    r=>r.region.rect.x++,r=>r.region.pngSha256='c'.repeat(64),r=>delete r.region.reviewToken,
    r=>r.inspection.candidates[1].recognition.modelSha256='c'.repeat(64),r=>r.inspection.candidates[1].recognition.inputSha256='c'.repeat(64),
    r=>r.inspection.candidates[1].recognition.confidence=1.5,r=>r.inspection.candidates[1].source='metadata',r=>r.inspection.candidates[1].id='optical-2'
  ];
  for(const mutate of mutations){const f=fixture();await f.review.inspect();f.fault.recognize=result=>{mutate(result);return result;};await assert.rejects(f.review.recognize(rect));assert.equal(f.review.sourceReviewAvailable,false);assert.equal(f.calls.preview.length,0);assert.deepEqual(f.store.getSnapshot(),f.before);f.review.dispose();}
  const undecoded=fixture({reviewRegion:async()=>false});await undecoded.review.inspect();await assert.rejects(undecoded.review.recognize(rect),/decoded/);assert.equal(undecoded.calls.preview.length,0);undecoded.review.dispose();
});

test('crop and selection changes revoke optical proposals; mixed metadata import cannot reuse them',async()=>{
  for(const invalidate of ['invalidateRegion','invalidatePreview']){
    const f=fixture();await f.review.inspect();await f.review.recognize(rect);f.review[invalidate]();
    assert.equal(f.review.sourceReviewAvailable,false);await assert.rejects(f.review.accept({confirmed:true}));
    await assert.rejects(f.review.preview(['optical-1']),/Recognize/);assert.deepEqual(f.store.getSnapshot(),f.before);f.review.dispose();
  }
  const f=fixture();await f.review.inspect();await f.review.recognize(rect);
  await assert.rejects(f.review.preview(['optical-1','metadata-1']),/Recognize|combined/);
  await f.review.recognize(rect);await assert.rejects(f.review.region({rect:{...rect,x:3},selected:['optical-1']}),/Recognize/);f.review.dispose();
});

test('closed review, crop invalidation and document edit reject late optical results without publication',async()=>{
  for(const invalidate of [f=>f.review.cancel(),f=>f.review.invalidateRegion(),f=>f.review.invalidatePreview(),f=>f.store.commit({...f.before.document,graph:graph('F')})]){
    const f=fixture(),late=deferred();await f.review.inspect();let receipt;
    f.fault.recognize=result=>{receipt=result;return late.promise;};const pending=f.review.recognize(rect),rejected=assert.rejects(pending,/cancel|changed/i);
    invalidate(f);await rejected;const snapshot=f.store.getSnapshot();late.resolve(receipt);await new Promise(r=>setTimeout(r,0));
    assert.equal(f.calls.preview.length,0);assert.equal(f.calls.open,0);assert.equal(f.review.sourceReviewAvailable,false);assert.deepEqual(f.store.getSnapshot(),snapshot);f.review.dispose();
  }
});

test('a newer recognition request retains authority after the earlier request resolves late',async()=>{
  const f=fixture(),late=deferred();await f.review.inspect();let receipt,first=true;
  f.fault.recognize=result=>first?(first=false,receipt=result,late.promise):result;
  const pending=f.review.recognize(rect),rejected=assert.rejects(pending,/cancel|changed|replaced/i),newRect={...rect,x:7};
  await f.review.recognize(newRect);await rejected;late.resolve(receipt);await new Promise(r=>setTimeout(r,0));
  assert.equal(f.review.status,'ready');assert.equal(f.calls.preview.length,1);await f.review.accept({confirmed:true});
  assert.deepEqual(f.store.getSnapshot().document.analysis.imageImport.reviewedRegion.rect,newRect);f.review.dispose();
});

test('acceptance requires unchanged model, crop and optical provenance',async()=>{
  for(const mutate of [r=>r.provenance.kind='png-metadata-component-reconstruction',r=>r.provenance.recognition.modelSha256='c'.repeat(64),r=>r.provenance.recognition.inputSha256='c'.repeat(64),r=>r.provenance.reviewedRegion.rect={...rect,x:3},r=>r.provenance.reviewedRegion.metadataCorrespondence='not established; whole components retained']){
    const f=fixture();await f.review.inspect();await f.review.recognize(rect);f.fault.prepare=result=>{mutate(result);return result;};
    await assert.rejects(f.review.accept({confirmed:true}));assert.equal(f.calls.open,0);assert.deepEqual(f.store.getSnapshot(),f.before);f.review.dispose();
  }
});
