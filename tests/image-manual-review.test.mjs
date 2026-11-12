import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createDocumentStore,validateProject} from '../public/modules/document.mjs';
import {createImageReview} from '../public/modules/image-review.mjs';

const sourceHash='a'.repeat(64),cropHash='b'.repeat(64),rect={x:2,y:3,width:50,height:40};
const graph=(symbol='C')=>({atoms:[{id:'manual-a0',symbol,x:3,y:4}],bonds:[]});
const deferred=()=>{let resolve;return {promise:new Promise(r=>{resolve=r;}),resolve};};
const hash=text=>createHash('sha256').update(text).digest('hex');
function fixture({reviewRegion=async()=>true,regionAvailable=true,nativeAvailable=true,configured=false}={}){
  const store=createDocumentStore({schema:'nivram/1',graph:graph('O'),analysis:{previous:'retained by Undo'}}),before=store.getSnapshot();
  const calls={manual:[],preview:[],open:0,cancel:[],regionCancel:[],tokens:[]},fault={};
  const inspection={schemaVersion:1,originalSha256:sourceHash,image:{width:100,height:100},engine:{name:'RDKit',version:'fixture'},candidates:[],recognition:{status:'not_run'}};
  let sequence=0,component,lastRegion;
  const preview=ids=>({graph:structuredClone(component.graph),scene:{style:before.document.style},sourceSha256:sourceHash,selectedComponents:ids,warnings:['Structure supplied by you, not recognized from this image.']});
  const native={
    async inspect_image(jobId){return {status:'inspected',jobId,name:'ordinary.png',inspection:structuredClone(inspection),reviewAvailable:true,regionPreviewAvailable:regionAvailable,opticalRecognition:{status:configured?'configured':'missing-model',message:'No model installed.'}};},
    async preview_image_draft(jobId,requestId,crop,source,style,token){
      calls.manual.push({jobId,requestId,crop,source,style,token});
      component={id:'manual-1',source:'user-supplied-structure',status:'supported',graph:graph(source==='N'?'N':'C'),suppliedStructure:{textSha256:hash(source),coordinates:'from structure text; not traced from image'}};
      lastRegion={schemaVersion:1,jobId,requestId,sourceSha256:sourceHash,sourceWidth:100,sourceHeight:100,mode:'crop',rect:{...crop},width:crop.width,height:crop.height,resampling:'none',pngBase64:'aGVsbG8=',pngSha256:cropHash,navigationOnly:false,reviewToken:'manual_crop_token'};
      const result={status:'drafted',jobId,requestId,inspection:{...structuredClone(inspection),candidates:[structuredClone(component)]},region:structuredClone(lastRegion),selectedComponents:['manual-1']};
      return fault.manual?fault.manual(result):result;
    },
    async preview_image_components(_jobId,ids){calls.preview.push(ids);return preview(ids);},
    async preview_image_region(){throw new Error('Manual crop needs a fresh supplied draft.');},
    async prepare_image_import(_jobId,ids,_style,token){
      calls.tokens.push(token);
      const {requestId,sourceSha256,sourceWidth,sourceHeight,rect,width,height,pngSha256}=lastRegion;
      const result={...preview(ids),provenance:{kind:'png-user-supplied-structure',sourceSha256,selectedComponents:ids,suppliedStructure:structuredClone(component.suppliedStructure),suppliedGraph:structuredClone(component.graph),reviewedRegion:{kind:'crop',requestId,sourceSha256,sourceWidth,sourceHeight,rect,width,height,pngSha256,scale:'one-source-pixel-per-image-pixel',metadataCorrespondence:'user-supplied structure; image correspondence not independently verified'}}};
      return fault.prepare?fault.prepare(result):result;
    },
    async cancel_image_region(jobId,requestId){calls.regionCancel.push({jobId,requestId});},
    async cancel_image_import(jobId){calls.cancel.push(jobId);}
  };
  if(!nativeAvailable)delete native.preview_image_draft;
  const editor={getSnapshot:store.getSnapshot,subscribe:store.subscribe,async openProject(document,options){calls.open++;if(options.signal.aborted)return false;store.commit(document,{expectedRevision:options.expectedRevision});store.markUnsaved();return true;}};
  const review=createImageReview({editor,native,sessionToken:'local-session',reviewSource:async()=>true,reviewRegion,newId:()=>`manual_request_${++sequence}`,render:async(graph,style)=>({graph,scene:{style}}),importSource:async(_text,style)=>({graph:graph('F'),scene:{style}}),prepare:(response,base)=>({project:validateProject({...base,graph:response.graph,style:response.scene.style}),scene:response.scene})});
  return {review,store,before,calls,fault,inspection,native};
}

test('switching proposal modes recovers after native success but failed source-crop decoding',async()=>{
  for(const firstMode of ['manual','optical']){
    let decoded=false,manual,optic;
    const f=fixture({configured:true,reviewRegion:async()=>decoded});await f.review.inspect();
    f.fault.manual=r=>{
      manual=structuredClone(r.inspection.candidates[0]);
      if(optic){r.inspection.candidates.unshift(structuredClone(optic));r.inspection.recognition={status:'proposed',...optic.recognition};}
      return r;
    };
    f.native.recognize_image=async(jobId,requestId,crop)=>{
      optic={id:'optical-1',source:'optical-recognition',status:'supported',graph:graph(),recognition:{method:'MolScribe',modelSha256:'32821fa4ba8d17f9a92e374601438c5e757947dbf1141e9ad0ef51cccfa1c501',inputSha256:cropHash,coordinates:'preserved-from-recognized-molfile',confidence:null}};
      return {status:'recognized',jobId,requestId,selectedComponents:['optical-1'],inspection:{...structuredClone(f.inspection),recognition:{status:'proposed',...optic.recognition},candidates:[...(manual?[structuredClone(manual)]:[]),structuredClone(optic)]},
        region:{schemaVersion:1,jobId,requestId,sourceSha256:sourceHash,sourceWidth:100,sourceHeight:100,mode:'crop',rect:{...crop},width:crop.width,height:crop.height,resampling:'none',pngBase64:'aGVsbG8=',pngSha256:cropHash,navigationOnly:false,reviewToken:'optical_crop_token'}};
    };
    if(firstMode==='manual'){
      await assert.rejects(f.review.startFromText('C',rect),/decoded/);decoded=true;
      await f.review.recognize(rect);
    }else{
      await assert.rejects(f.review.recognize(rect),/decoded/);decoded=true;
      await f.review.startFromText('C',rect);
    }
    assert.equal(f.review.status,'ready');assert.equal(f.review.sourceReviewAvailable,true);
    assert.deepEqual(f.store.getSnapshot(),f.before);f.review.dispose();
  }
});

test('ordinary PNG with no model starts from supplied text, corrects with Undo, and commits truthful crop provenance once',async()=>{
  const f=fixture();await f.review.inspect();assert.deepEqual(f.review.inspection.inspection.candidates,[]);
  const source='\n  supplied 2D MOL headers preserved\n',result=await f.review.startFromText(source,rect);
  assert.equal(f.calls.manual[0].source,source);assert.equal(f.calls.manual[0].token,'local-session');
  assert.deepEqual(f.calls.manual[0].style,f.before.document.style);assert.deepEqual(f.calls.manual[0].crop,rect);
  assert.deepEqual(result.prepared.project.graph,graph());assert.deepEqual(result.prepared.history,{canUndo:false,canRedo:false});
  assert.equal(f.review.sourceReviewAvailable,true);assert.deepEqual(f.store.getSnapshot(),f.before);
  assert.deepEqual(f.review.inspection.inspection.recognition,{status:'not_run'});
  await assert.rejects(f.review.accept(),/Confirm/);
  await f.review.replaceFromText('F');assert.deepEqual((await f.review.undoCorrection()).project.graph,graph());
  assert.deepEqual((await f.review.redoCorrection()).project.graph,graph('F'));
  await f.review.accept({confirmed:true});const doc=f.store.getSnapshot().document,p=doc.analysis.imageImport;
  assert.equal(p.kind,'png-user-supplied-structure');assert.equal(p.suppliedStructure.textSha256,hash(source));
  assert.equal(p.recognition,undefined);assert.deepEqual(p.suppliedGraph,graph());assert.deepEqual(doc.graph,graph('F'));
  assert.deepEqual(p.correction.baselineGraph,graph());assert.deepEqual(p.correction.reviewedGraph,doc.graph);
  assert.deepEqual(p.reviewedRegion.rect,rect);assert.deepEqual(f.calls.tokens,['manual_crop_token']);assert.equal(f.calls.open,1);
  f.store.commit(f.store.historyTarget(),{mode:'undo'});assert.deepEqual(f.store.getSnapshot().document,f.before.document);
  assert.equal(f.store.historyState().canUndo,false);f.review.dispose();
});

test('invalid text, missing native capability and invalid crop never dispatch a manual operation',async()=>{
  for(const source of ['', '  ',42,'a'.repeat(1_000_001),'\u00e9'.repeat(500_001)]){
    const f=fixture();await f.review.inspect();await assert.rejects(f.review.startFromText(source,rect));assert.equal(f.calls.manual.length,0);f.review.dispose();
  }
  for(const options of [{regionAvailable:false},{nativeAvailable:false}]){
    const f=fixture(options);await f.review.inspect();await assert.rejects(f.review.startFromText('N',rect),/crop|native/);assert.equal(f.calls.manual.length,0);f.review.dispose();
  }
  const f=fixture();await f.review.inspect();await assert.rejects(f.review.startFromText('N',{...rect,width:200}));assert.equal(f.calls.manual.length,0);f.review.dispose();
});

test('a mismatched source, crop, text identity or recognition claim cannot authorize a supplied draft',async()=>{
  const mutations=[r=>r.status='recognized',r=>r.requestId='old',r=>r.selectedComponents=['optical-1'],r=>r.inspection.originalSha256='c'.repeat(64),
    r=>r.inspection.image.width=101,r=>r.region.rect.x++,r=>delete r.region.reviewToken,
    r=>r.inspection.candidates[0].id='manual-2',r=>r.inspection.candidates[0].source='metadata',
    r=>r.inspection.candidates[0].suppliedStructure.textSha256='d'.repeat(64),r=>r.inspection.candidates[0].suppliedStructure.coordinates='recognized',
    r=>r.inspection.candidates[0].recognition={method:'MolScribe'}];
  for(const mutate of mutations){const f=fixture();await f.review.inspect();f.fault.manual=r=>{mutate(r);return r;};await assert.rejects(f.review.startFromText('N',rect));assert.equal(f.calls.preview.length,0);assert.deepEqual(f.store.getSnapshot(),f.before);f.review.dispose();}
  const f=fixture({reviewRegion:async()=>false});await f.review.inspect();await assert.rejects(f.review.startFromText('N',rect),/decoded/);assert.equal(f.calls.preview.length,0);f.review.dispose();
});

test('crop and selection changes revoke the supplied draft; a different crop cannot reuse it',async()=>{
  for(const invalidate of ['invalidateRegion','invalidatePreview']){
    const f=fixture();await f.review.inspect();await f.review.startFromText('N',rect);f.review[invalidate]();
    assert.equal(f.review.sourceReviewAvailable,false);await assert.rejects(f.review.accept({confirmed:true}));
    await assert.rejects(f.review.preview(['manual-1']),/supplied/);assert.deepEqual(f.store.getSnapshot(),f.before);f.review.dispose();
  }
  const f=fixture();await f.review.inspect();await f.review.startFromText('N',rect);
  await assert.rejects(f.review.region({rect:{...rect,x:4},selected:['manual-1']}),/supplied/);f.review.dispose();
});

test('closing, changing crop, replacing source or editing the document rejects a late supplied draft',async()=>{
  for(const invalidate of [f=>f.review.cancel(),f=>f.review.invalidateRegion(),f=>f.review.invalidatePreview(),f=>f.store.commit({...f.before.document,graph:graph('F')}),f=>f.review.inspect()]){
    const f=fixture(),late=deferred(),started=deferred();await f.review.inspect();let receipt;
    f.fault.manual=r=>{receipt=r;started.resolve();return late.promise;};
    const pending=f.review.startFromText('N',rect),rejected=assert.rejects(pending,/cancel|changed|replaced/i);await started.promise;
    await invalidate(f);await rejected;const snapshot=f.store.getSnapshot();late.resolve(receipt);await new Promise(r=>setTimeout(r,0));
    assert.equal(f.calls.preview.length,0);assert.equal(f.calls.open,0);assert.deepEqual(f.store.getSnapshot(),snapshot);f.review.dispose();
  }
});

test('a newer supplied request retains authority when the old request resolves late',async()=>{
  const f=fixture(),late=deferred(),started=deferred();await f.review.inspect();let receipt,first=true;
  f.fault.manual=r=>{if(!first)return r;first=false;receipt=r;started.resolve();return late.promise;};
  const pending=f.review.startFromText('C',rect),rejected=assert.rejects(pending,/cancel|changed|replaced/i);await started.promise;
  const newRect={...rect,x:7};await f.review.startFromText('N',newRect);await rejected;late.resolve(receipt);await new Promise(r=>setTimeout(r,0));
  assert.equal(f.review.status,'ready');assert.equal(f.calls.preview.length,1);await f.review.accept({confirmed:true});
  assert.deepEqual(f.store.getSnapshot().document.graph,graph('N'));assert.deepEqual(f.store.getSnapshot().document.analysis.imageImport.reviewedRegion.rect,newRect);f.review.dispose();
});

test('acceptance rejects altered supplied baseline, supplied text identity and crop provenance',async()=>{
  for(const mutate of [r=>r.provenance.kind='png-optical-reconstruction',r=>r.provenance.suppliedStructure.textSha256='c'.repeat(64),r=>r.provenance.suppliedGraph=graph('F'),r=>r.provenance.recognition={method:'MolScribe'},r=>r.provenance.reviewedRegion.rect={...rect,x:4},r=>r.provenance.reviewedRegion.metadataCorrespondence='unverified optical recognition of this crop']){
    const f=fixture();await f.review.inspect();await f.review.startFromText('N',rect);f.fault.prepare=r=>{mutate(r);return r;};
    await assert.rejects(f.review.accept({confirmed:true}));assert.equal(f.calls.open,0);assert.deepEqual(f.store.getSnapshot(),f.before);f.review.dispose();
  }
});
