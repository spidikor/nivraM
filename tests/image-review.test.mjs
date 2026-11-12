import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createDocumentStore,validateProject} from '../public/modules/document.mjs';
import {createImageReview} from '../public/modules/image-review.mjs';

const hash='a'.repeat(64),otherHash='b'.repeat(64);
const graph=(id,symbol='C')=>({atoms:[{id,symbol,x:0,y:0}],bonds:[]});
const deferred=()=>{let resolve;return {promise:new Promise(r=>{resolve=r;}),resolve:value=>resolve(value)};};
function fixture({sourceHash=hash,reviewAvailable=true,regionPreviewAvailable=false,reviewSource=async()=>true,reviewRegion=async()=>true,timeoutMs=60000}={}){
  const store=createDocumentStore({schema:'nivram/1',graph:graph('old'),analysis:[{kind:'preceding-document'}]}),before=store.getSnapshot();
  const components=[{id:'c1',status:'supported',graph:graph('a0'),label:'Component 1'},{id:'c2',status:'supported',graph:graph('a1','O'),label:'Component 2'},{id:'blocked',status:'unsupported',graph:null,label:'Unsupported'}];
  const inspection={schemaVersion:1,originalSha256:sourceHash,candidates:components,image:{width:100,height:100},engine:{name:'RDKit',version:'fixture'},warnings:['Components are not verified compounds.']};
  const calls={reset:0,open:0,cancel:[],preview:[],prepare:[],regions:[],regionCancel:[],tokens:[],chooser:0,bytes:[],imports:[]},fault={};let sequence=0;
  const candidate=ids=>({graph:{atoms:ids.flatMap(id=>components.find(c=>c.id===id)?.graph?.atoms||[]),bonds:[]},scene:{style:before.document.style},sourceSha256:sourceHash,selectedComponents:ids,warnings:['Review stereo against source.']});
  const native={
    async inspect_image(id){calls.chooser++;return fault.inspect?fault.inspect(id):{status:'inspected',jobId:id,name:'sample.png',inspection,reviewAvailable,regionPreviewAvailable,sourcePreview:reviewAvailable?'test-source':null,previewLimitation:reviewAvailable?null:'A detail crop is required.'};},
    async inspect_image_bytes(encoded,name,id,token){calls.bytes.push({encoded,name,id,token});return fault.bytes?fault.bytes(id):{status:'inspected',jobId:id,name,inspection,reviewAvailable,regionPreviewAvailable,sourcePreview:reviewAvailable?'test-source':null};},
    async preview_image_components(id,ids){calls.preview.push(ids);return fault.preview?fault.preview(id,ids):candidate(ids);},
    async prepare_image_import(id,ids,_style,token){calls.prepare.push(ids);calls.tokens.push(token);if(fault.prepare)return fault.prepare(id,ids);const region=calls.regions.at(-1);return {...candidate(ids),provenance:{kind:'png-metadata-component-reconstruction',sourceSha256:sourceHash,selectedComponents:ids,sourceStorage:'local-profile/import-sources',...(token?{reviewedRegion:{kind:'crop',requestId:region.requestId,sourceSha256:sourceHash,sourceWidth:100,sourceHeight:100,rect:region.rect,width:region.rect.width,height:region.rect.height,pngSha256:otherHash,scale:'one-source-pixel-per-image-pixel',metadataCorrespondence:'not established; whole components retained'}}:{})}};},
    async preview_image_region(id,requestId,rect,mode,ids){calls.regions.push({id,requestId,rect,mode,ids});const result={schemaVersion:1,jobId:id,requestId,sourceSha256:hash,sourceWidth:100,sourceHeight:100,mode,rect:rect||{x:0,y:0,width:100,height:100},width:rect?.width||100,height:rect?.height||100,resampling:mode==='overview'?'nearest-center':'none',pngBase64:'aGVsbG8=',pngSha256:otherHash,navigationOnly:mode==='overview',...(mode==='crop'&&ids.length?{reviewToken:'native_detail_token'}:{})};return fault.region?fault.region(result):result;},
    async cancel_image_region(id,requestId){calls.regionCancel.push({id,requestId});return {status:'cancelled'};},
    async cancel_image_import(id){calls.cancel.push(id);return {status:'cancelled'};}
  };
  const editor={getSnapshot:store.getSnapshot,subscribe:store.subscribe,async openProject(document,options){
    calls.open++;if(fault.open)await fault.open(options);
    if(options.signal?.aborted)return false;
    try{store.commit(document,{expectedRevision:options.expectedRevision});if(options.asUnsaved)store.markUnsaved();const receipt=store.getSnapshot();if(fault.afterCommit)fault.afterCommit();return options.returnSnapshot?{accepted:true,snapshot:fault.receipt?fault.receipt(receipt):receipt}:true;}catch{return false;}
  }};
  const invalidations=[];
  const review=createImageReview({editor,native,sessionToken:'local-fixture-token',render:async(graph,style,task)=>fault.render?fault.render(graph,style,task):{graph,scene:{style}},importSource:async(source,style,task)=>{calls.imports.push({source,style,task});return fault.importSource?fault.importSource(source,style,task):{graph:graph('text','N'),scene:{style}};},reviewSource,reviewRegion,timeoutMs,newId:()=>`image_test_${++sequence}`,resetDestination:()=>calls.reset++,onInvalidate:message=>invalidations.push(message),prepare:(response,base)=>({project:validateProject({...base,graph:response.graph,style:response.scene.style}),scene:response.scene,fragment:null})});
  return {review,store,before,calls,fault,candidate,inspection,invalidations};
}

const pngBytes=Uint8Array.from([137,80,78,71,13,10,26,10,1,2,3,255]);
const pngHash=createHash('sha256').update(pngBytes).digest('hex');
const pngFile=(read=async()=>pngBytes.slice().buffer)=>({name:'dropped.png',type:'image/png',size:pngBytes.length,arrayBuffer:read});

test('changing a metadata crop discards a pending text replacement without altering draft history',async()=>{
  const f=fixture({regionPreviewAvailable:true}),late=deferred();
  await f.review.inspect();await f.review.preview(['c1']);
  f.fault.importSource=()=>late.promise;
  const pending=f.review.replaceFromText('N');
  f.review.invalidateRegion();
  late.resolve({graph:graph('late','N'),scene:{style:f.before.document.style}});
  await assert.rejects(pending,/cancelled|changed/i);
  assert.equal(f.review.status,'ready');
  assert.deepEqual(f.review.correctionHistory,{canUndo:false,canRedo:false});
  assert.equal(f.review.sourceReviewAvailable,false);
  assert.deepEqual(f.store.getSnapshot(),f.before);
  await f.review.region({mode:'crop',rect:{x:4,y:8,width:40,height:50},selected:['c1']});
  await f.review.accept({confirmed:true});
  assert.deepEqual(f.store.getSnapshot().document.graph,graph('a0'));
  f.review.dispose();
});

test('pasted source replaces only the review draft as one correction and preserves PNG provenance on acceptance',async()=>{
  const f=fixture();await f.review.inspect();await f.review.preview(['c1']);
  const source='[NH4+]\n',replacement=graph('text','N');replacement.atoms[0].charge=1;
  f.fault.importSource=async(_source,style)=>({graph:replacement,scene:{style}});
  const result=await f.review.replaceFromText(source);
  assert.equal(f.calls.imports[0].source,source);assert.deepEqual(f.calls.imports[0].style,f.before.document.style);
  assert.deepEqual(result.project.graph,replacement);assert.deepEqual(f.store.getSnapshot(),f.before);
  assert.deepEqual({...result.project,graph:f.before.document.graph},f.before.document);
  result.project.graph.atoms[0].symbol='O';
  assert.deepEqual((await f.review.undoCorrection()).project.graph,graph('a0'));
  assert.equal(f.review.correctionHistory.canUndo,false,'replacement creates exactly one draft history step');
  assert.deepEqual((await f.review.redoCorrection()).project.graph,replacement);
  await f.review.accept({confirmed:true});
  const provenance=f.store.getSnapshot().document.analysis.imageImport;
  assert.equal(provenance.kind,'png-metadata-component-reconstruction');assert.equal(provenance.recognition,undefined);
  assert.equal(provenance.sourceSha256,hash);assert.deepEqual(provenance.selectedComponents,['c1']);
  assert.deepEqual(provenance.correction.baselineGraph,graph('a0'));assert.deepEqual(provenance.correction.reviewedGraph,replacement);
  assert.equal(f.calls.open,1);assert.deepEqual(f.store.historyTarget(),f.before.document);f.review.dispose();
});

test('text replacement checks nonblank input and the UTF-8 byte limit before parsing',async()=>{
  const f=fixture();await f.review.inspect();await f.review.preview(['c1']);
  for(const source of [null,{},'', ' \t\r\n', 'a'.repeat(1_000_001), 'é'.repeat(500_001)]){
    await assert.rejects(f.review.replaceFromText(source),/text|blank|1,000,000|limit/i);
    assert.equal(f.review.status,'ready');assert.equal(f.calls.imports.length,0);
  }
  await f.review.replaceFromText('é'.repeat(500_000));assert.equal(f.calls.imports.length,1);
  assert.deepEqual(f.store.getSnapshot(),f.before);f.review.dispose();
});

test('invalid parsing or changed settings retains the previous valid draft and redo history',async()=>{
  for(const failure of [async()=>{throw new Error('Invalid chemical source');},async(_source,style)=>({graph:graph('bad','N'),scene:{style:{...style,color:!style.color}}})]){
    const f=fixture();await f.review.inspect();await f.review.preview(['c1']);await f.review.correct(graph('a0','O'));await f.review.undoCorrection();
    f.fault.importSource=failure;await assert.rejects(f.review.replaceFromText('not valid'));
    assert.equal(f.review.status,'ready');assert.deepEqual(f.review.correctionHistory,{canUndo:false,canRedo:true});
    assert.deepEqual((await f.review.redoCorrection()).project.graph,graph('a0','O'));
    await f.review.undoCorrection();delete f.fault.importSource;
    await f.review.replaceFromText('N');assert.deepEqual(f.review.correctionHistory,{canUndo:true,canRedo:false});
    assert.deepEqual(f.store.getSnapshot(),f.before);f.review.dispose();
  }
});

test('pending text replacement rejects concurrent draft edits and cannot commit after cancel or replacement review',async()=>{
  for(const action of ['cancel','invalidatePreview','edit-document']){
    const f=fixture(),late=deferred();await f.review.inspect();await f.review.preview(['c1']);await f.review.correct(graph('a0','O'));await f.review.undoCorrection();
    f.fault.importSource=()=>late.promise;const pending=f.review.replaceFromText('N'),rejected=assert.rejects(pending,/cancelled|changed/i);
    await assert.rejects(f.review.correct(graph('a0','S')),/preview/i);
    await assert.rejects(f.review.redoCorrection(),/preview/i);
    await assert.rejects(f.review.replaceFromText('O'),/preview/i);
    await assert.rejects(f.review.accept({confirmed:true}),/preview/i);
    if(action==='edit-document')f.store.commit({...f.before.document,graph:graph('changed')});else f.review[action]();
    await rejected;assert.equal(f.calls.imports[0].task.signal.aborted,true);
    if(action!=='invalidatePreview')await f.review.inspect();await f.review.preview(['c2']);
    late.resolve({graph:graph('late','N'),scene:{style:f.before.document.style}});await new Promise(resolve=>setTimeout(resolve,0));
    assert.equal(f.review.status,'ready');assert.deepEqual(f.review.correctionHistory,{canUndo:false,canRedo:false});
    await f.review.accept({confirmed:true});assert.deepEqual(f.store.getSnapshot().document.graph,graph('a1','O'));f.review.dispose();
  }
});

test('timed-out source parsing retains the draft and a late response cannot overwrite its retry',async()=>{
  const f=fixture({timeoutMs:25}),late=deferred();await f.review.inspect();await f.review.preview(['c1']);
  f.fault.importSource=()=>late.promise;await assert.rejects(f.review.replaceFromText('N'),/timed out/i);
  assert.equal(f.review.status,'ready');assert.deepEqual(f.review.correctionHistory,{canUndo:false,canRedo:false});
  delete f.fault.importSource;await f.review.replaceFromText('N');
  late.resolve({graph:graph('late','S'),scene:{style:f.before.document.style}});await new Promise(resolve=>setTimeout(resolve,0));
  await f.review.accept({confirmed:true});assert.deepEqual(f.store.getSnapshot().document.graph,graph('text','N'));f.review.dispose();
});

test('a successful replacement clears redo even when it reproduces the present draft',async()=>{
  const f=fixture();await f.review.inspect();await f.review.preview(['c1']);await f.review.correct(graph('a0','O'));await f.review.undoCorrection();
  f.fault.importSource=async(_source,style)=>({graph:graph('a0'),scene:{style}});
  await f.review.replaceFromText('C');assert.deepEqual(f.review.correctionHistory,{canUndo:true,canRedo:false});
  await f.review.undoCorrection();assert.equal(f.review.correctionHistory.canUndo,false);
  assert.deepEqual(f.store.getSnapshot(),f.before);f.review.dispose();
});

test('a new component preview supersedes text parsing even if its old worker replies later',async()=>{
  const f=fixture(),late=deferred();await f.review.inspect();await f.review.preview(['c1']);
  f.fault.importSource=()=>late.promise;const pending=f.review.replaceFromText('N'),rejected=assert.rejects(pending,/cancelled/i);
  await f.review.preview(['c2']);late.resolve({graph:graph('late','N'),scene:{style:f.before.document.style}});await rejected;
  assert.equal(f.review.status,'ready');assert.deepEqual(f.review.correctionHistory,{canUndo:false,canRedo:false});
  await f.review.accept({confirmed:true});assert.deepEqual(f.store.getSnapshot().document.graph,graph('a1','O'));f.review.dispose();
});

test('corrections stay isolated, keep their own history, and accept with original source provenance in one undo',async()=>{
  const f=fixture();await f.review.inspect();await f.review.preview(['c1']);
  const corrected=graph('a0','N');corrected.atoms[0].charge=1;
  const draft=await f.review.correct(corrected);
  assert.deepEqual(draft.project.graph,corrected);assert.deepEqual(f.store.getSnapshot(),f.before);
  draft.project.graph.atoms[0].symbol='O'; // Caller-owned view data cannot change the accepted draft.
  assert.equal(f.review.correctionHistory.canUndo,true);
  assert.deepEqual((await f.review.undoCorrection()).project.graph,graph('a0'));
  assert.deepEqual((await f.review.redoCorrection()).project.graph,corrected);
  assert.deepEqual((await f.review.resetCorrection()).project.graph,graph('a0'));
  await f.review.undoCorrection();await f.review.accept({confirmed:true});
  const imported=f.store.getSnapshot();assert.deepEqual(imported.document.graph,corrected);
  const provenance=imported.document.analysis.imageImport;
  assert.equal(provenance.sourceSha256,hash);assert.deepEqual(provenance.selectedComponents,['c1']);
  assert.deepEqual(provenance.correction.baselineGraph,graph('a0'));assert.deepEqual(provenance.correction.reviewedGraph,corrected);
  assert.deepEqual(f.store.historyTarget(),f.before.document);assert.equal(f.calls.open,1);assert.equal(f.calls.reset,1);
  f.review.dispose();
});

test('invalid or empty correction never replaces the active drawing; failed validation retains the last draft',async()=>{
  const f=fixture();await f.review.inspect();await f.review.preview(['c1']);
  f.fault.render=async()=>{throw new Error('Invalid valence');};
  await assert.rejects(f.review.correct(graph('a0','O')),/Invalid valence/);
  assert.deepEqual(f.store.getSnapshot(),f.before);assert.equal(f.review.status,'ready');
  assert.equal(f.review.correctionHistory.canUndo,false);
  delete f.fault.render;await f.review.correct({atoms:[],bonds:[]});
  await assert.rejects(f.review.accept({confirmed:true}),/empty/);assert.equal(f.calls.open,0);
  await f.review.resetCorrection();await f.review.accept({confirmed:true});
  assert.equal(f.store.getSnapshot().document.analysis.imageImport.correction,undefined);f.review.dispose();
});

test('cancel, selection change and active document change discard a pending correction',async()=>{
  for(const invalidate of [f=>f.review.cancel(),f=>f.review.invalidatePreview(),f=>f.store.commit({...f.before.document,graph:graph('changed')})]){
    const f=fixture(),late=deferred();await f.review.inspect();await f.review.preview(['c1']);
    f.fault.render=()=>late.promise;const pending=f.review.correct(graph('a0','N'));
    await assert.rejects(f.review.accept({confirmed:true}),/preview/);
    const rejected=assert.rejects(pending,/cancelled|changed/i);invalidate(f);await rejected;
    late.resolve({graph:graph('a0','N'),scene:{style:f.before.document.style}});
    await new Promise(resolve=>setTimeout(resolve,0));assert.equal(f.calls.open,0);assert.equal(f.calls.reset,0);
    assert.equal(f.review.correctionHistory.canUndo,false);f.review.dispose();
  }
});

test('byte intake enters the same explicit review transaction with exact source hash and token',async()=>{
  const f=fixture({sourceHash:pngHash});await f.review.inspect(pngFile());
  assert.equal(f.calls.chooser,0);assert.equal(f.calls.bytes.length,1);
  assert.equal(f.calls.bytes[0].token,'local-fixture-token');assert.equal(f.calls.bytes[0].name,'dropped.png');
  assert.deepEqual(Buffer.from(f.calls.bytes[0].encoded,'base64'),Buffer.from(pngBytes));
  assert.deepEqual(f.store.getSnapshot(),f.before);assert.deepEqual(f.calls.prepare,[]);
  await assert.rejects(f.review.accept({confirmed:true}),/preview/i);
  await f.review.preview(['c1']);await assert.rejects(f.review.accept(),/Confirm/);
  await f.review.accept({confirmed:true});assert.equal(f.calls.open,1);assert.equal(f.calls.reset,1);
  assert.equal(f.store.getSnapshot().document.analysis.imageImport.sourceSha256,pngHash);
  f.review.dispose();
});

test('mismatched byte-source inspection cannot open visual review or publish a candidate',async()=>{
  let previews=0;const f=fixture({reviewSource:async()=>{previews++;return true;}});
  await assert.rejects(f.review.inspect(pngFile()),/source identity/);
  assert.equal(previews,0);assert.deepEqual(f.store.getSnapshot(),f.before);assert.equal(f.review.status,'idle');f.review.dispose();
});

test('cancel or document edit while reading dropped bytes prevents native dispatch',async()=>{
  for(const mutate of [f=>f.review.cancel(),f=>f.store.commit({...f.before.document,graph:graph('changed')})]){
    const f=fixture({sourceHash:pngHash}),read=deferred(),pending=f.review.inspect(pngFile(()=>read.promise));
    const rejected=assert.rejects(pending,/cancelled/i);mutate(f);await rejected;
    read.resolve(pngBytes.slice().buffer);await new Promise(resolve=>setTimeout(resolve,0));
    assert.equal(f.calls.bytes.length,0);assert.equal(f.calls.chooser,0);assert.equal(f.calls.open,0);f.review.dispose();
  }
});

test('late byte receipt after cancellation cannot replace the next file chooser review',async()=>{
  const f=fixture({sourceHash:pngHash}),late=deferred();f.fault.bytes=()=>late.promise;
  const pending=f.review.inspect(pngFile()),rejected=assert.rejects(pending,/cancelled/i);
  // Consume expected-rejection failures too when running this regression on old code.
  rejected.catch(()=>{});
  try{
    for(let attempt=0;!f.calls.bytes.length&&attempt<100;attempt++)await new Promise(resolve=>setTimeout(resolve,1));
    assert.equal(f.calls.bytes.length,1,'the real byte dispatch boundary was reached');
    f.review.cancel();await rejected;await f.review.inspect();
    late.resolve({status:'inspected',jobId:f.calls.bytes[0].id,name:'dropped.png',inspection:f.inspection,reviewAvailable:true});
    await new Promise(resolve=>setTimeout(resolve,0));assert.equal(f.review.status,'inspected');assert.equal(f.calls.chooser,1);
    assert.deepEqual(f.store.getSnapshot(),f.before);
  }finally{late.resolve(null);f.review.dispose();}
});

test('PNG inspection and explicit component preview do not modify the document',async()=>{
  const f=fixture();await f.review.inspect();assert.deepEqual(f.calls.preview,[]);
  await f.review.preview(['c1','c2']);assert.deepEqual(f.calls.preview,[['c1','c2']]);
  assert.deepEqual(f.store.getSnapshot(),f.before);assert.equal(f.calls.reset,0);
  await assert.rejects(f.review.accept(),/Confirm/);assert.equal(f.calls.open,0);
  f.review.cancel();assert.deepEqual(f.store.getSnapshot(),f.before);assert.equal(f.review.status,'idle');
});

test('accepted PNG preserves provenance, becomes unsaved, resets destination and has one undo entry',async()=>{
  const f=fixture();await f.review.inspect();await f.review.preview(['c1','c2']);
  const accepted=await f.review.accept({confirmed:true});assert.equal(accepted.name,'sample.png');
  assert.equal(f.calls.open,1);assert.equal(f.calls.reset,1);assert.equal(f.store.getSnapshot().dirty,true);
  assert.equal(f.store.getSnapshot().document.analysis.imageImport.sourceSha256,hash);
  assert.deepEqual(f.store.getSnapshot().document.graph.atoms.map(a=>a.id),['a0','a1']);
  f.store.commit(f.store.historyTarget(),{mode:'undo'});assert.deepEqual(f.store.getSnapshot().document,f.before.document);assert.equal(f.store.historyState().canUndo,false);
});

test('crop-required and undecodable originals allow metadata inspection but never acceptance',async()=>{
  for(const options of [{reviewAvailable:false},{reviewSource:async()=>{throw new Error('Decode failure');}}]){
    const f=fixture(options);const result=await f.review.inspect();assert.equal(result.reviewAvailable,false);
    await f.review.preview(['c1']);await assert.rejects(f.review.accept({confirmed:true}),/source-image/);
    assert.equal(f.calls.prepare.length,0);assert.deepEqual(f.store.getSnapshot(),f.before);f.review.dispose();
  }
});

test('changed source, omitted components, modified coordinates or unsafe render cannot be reviewed',async()=>{
  for(const alter of [r=>r.sourceSha256=otherHash,r=>r.selectedComponents=['c2'],r=>r.graph.atoms[0].x=1,r=>r.scene.style={...r.scene.style,color:false}]){
    const f=fixture();await f.review.inspect();f.fault.preview=(_id,ids)=>{const r=f.candidate(ids);alter(r);return r;};
    await assert.rejects(f.review.preview(['c1']));await assert.rejects(f.review.accept({confirmed:true}));
    assert.deepEqual(f.store.getSnapshot(),f.before);f.review.dispose();
  }
});

test('source-storage failure retains corrections and both history branches for a freshly acknowledged acceptance',async()=>{
  const f=fixture();await f.review.inspect();await f.review.preview(['c1']);
  await f.review.correct(graph('a0','S'));await f.review.replaceFromText('N');await f.review.undoCorrection();
  f.fault.prepare=()=>{throw new Error('The local source-image store is full. Existing source images were retained.');};
  await assert.rejects(f.review.accept({confirmed:true}),/store is full/);
  assert.equal(f.review.status,'ready');assert.deepEqual(f.review.correctionHistory,{canUndo:true,canRedo:true});
  assert.equal(f.calls.open,0);assert.equal(f.calls.reset,0);assert.deepEqual(f.store.getSnapshot(),f.before);
  await assert.rejects(f.review.accept(),/Confirm/);assert.equal(f.calls.prepare.length,1);
  assert.deepEqual((await f.review.undoCorrection()).project.graph,graph('a0'));
  assert.deepEqual((await f.review.redoCorrection()).project.graph,graph('a0','S'));
  assert.deepEqual((await f.review.redoCorrection()).project.graph,graph('text','N'));
  f.fault.prepare=null;await f.review.accept({confirmed:true});
  assert.equal(f.calls.prepare.length,2);assert.equal(f.calls.open,1);
  assert.deepEqual(f.store.getSnapshot().document.graph,graph('text','N'));
  f.store.commit(f.store.historyTarget(),{mode:'undo'});assert.deepEqual(f.store.getSnapshot().document,f.before.document);
  f.review.dispose();
});

test('a failed native prepare cannot retain stale crop or document acceptance authority',async()=>{
  for(const invalidate of [f=>f.review.invalidateRegion(),f=>f.review.cancel(),f=>f.store.commit({...f.before.document,graph:graph('changed')})]){
    const f=fixture({regionPreviewAvailable:true}),late=deferred();
    await f.review.inspect();await f.review.preview(['c1']);await f.review.replaceFromText('N');
    await f.review.region({mode:'crop',rect:{x:4,y:8,width:40,height:50},selected:['c1']});
    f.fault.prepare=()=>late.promise;const pending=f.review.accept({confirmed:true}),rejected=assert.rejects(pending,/cancelled|changed/i);
    invalidate(f);await rejected;late.resolve(f.candidate(['c1']));
    f.fault.prepare=null;await assert.rejects(f.review.accept({confirmed:true}),/preview/i);
    assert.equal(f.calls.prepare.length,1);assert.equal(f.calls.open,0);f.review.dispose();
  }
});

test('editor-commit failure never makes a corrected draft retryable',async()=>{
  for(const commitBeforeFailure of [false,true]){
    const f=fixture();await f.review.inspect();await f.review.preview(['c1']);await f.review.replaceFromText('N');
    f.fault.open=()=>{
      if(commitBeforeFailure)f.store.commit({...f.before.document,graph:graph('uncertain','N')});
      throw new Error('The editor did not confirm completion.');
    };
    await assert.rejects(f.review.accept({confirmed:true}),/did not confirm/);
    assert.equal(f.review.status,'inspected');assert.deepEqual(f.review.correctionHistory,{canUndo:false,canRedo:false});
    f.fault.open=null;await assert.rejects(f.review.accept({confirmed:true}),/preview/i);
    assert.equal(f.calls.prepare.length,1);assert.equal(f.calls.open,1);assert.equal(f.calls.reset,0);
    if(!commitBeforeFailure)assert.deepEqual(f.store.getSnapshot(),f.before);
    f.review.dispose();
  }
});

test('acceptance rechecks preserved-source identity and the exact warnings that were reviewed',async()=>{
  for(const alter of [r=>r.provenance.sourceSha256=otherHash,r=>r.warnings.push('New omission'),r=>r.graph.atoms[0].symbol='N']){
    const f=fixture();await f.review.inspect();await f.review.preview(['c1']);await f.review.replaceFromText('N');
    f.fault.prepare=(_id,ids)=>{const r={...f.candidate(ids),provenance:{kind:'png-metadata-component-reconstruction',sourceSha256:hash,selectedComponents:ids}};alter(r);return r;};
    await assert.rejects(f.review.accept({confirmed:true}));assert.equal(f.calls.open,0);assert.equal(f.calls.reset,0);assert.deepEqual(f.store.getSnapshot(),f.before);
    assert.equal(f.review.status,'inspected');assert.deepEqual(f.review.correctionHistory,{canUndo:false,canRedo:false});
    await assert.rejects(f.review.accept({confirmed:true}),/preview/i);assert.equal(f.calls.prepare.length,1);f.review.dispose();
  }
});

test('selection change cancels an old preview without clearing its replacement',async()=>{
  const f=fixture(),old=deferred();await f.review.inspect();let first=true;
  f.fault.preview=(_id,ids)=>first?(first=false,old.promise):f.candidate(ids);
  const pending=f.review.preview(['c1']);const rejected=assert.rejects(pending,/selection changed|cancelled/i);
  f.review.invalidatePreview();await f.review.preview(['c2']);await rejected;
  old.resolve(f.candidate(['c1']));assert.equal(f.review.status,'ready');
  await f.review.accept({confirmed:true});assert.deepEqual(f.store.getSnapshot().document.graph.atoms.map(a=>a.id),['a1']);
});

test('editing the document invalidates the entire PNG review and rejects a late result',async()=>{
  const f=fixture(),pending=deferred();await f.review.inspect();f.fault.preview=()=>pending.promise;
  const result=f.review.preview(['c1']),rejected=assert.rejects(result,/cancelled/);
  f.store.commit({...f.before.document,graph:graph('new','N')});const edited=f.store.getSnapshot();await rejected;
  pending.resolve(f.candidate(['c1']));await assert.rejects(f.review.accept({confirmed:true}));
  assert.deepEqual(f.store.getSnapshot(),edited);assert.equal(f.invalidations.length,1);assert.equal(f.calls.reset,0);
});

test('cancel during the final editor render reaches its abort signal and cannot commit late',async()=>{
  const f=fixture(),render=deferred();await f.review.inspect();await f.review.preview(['c1']);
  f.fault.open=()=>render.promise;const accepted=f.review.accept({confirmed:true});
  while(!f.calls.open)await new Promise(resolve=>setTimeout(resolve,0));
  f.review.cancel();render.resolve();await assert.rejects(accepted,/not committed/);
  assert.deepEqual(f.store.getSnapshot(),f.before);assert.equal(f.calls.reset,0);
});

test('a cancelled inspection cannot reopen a replaced review',async()=>{
  const f=fixture(),first=deferred();f.fault.inspect=()=>first.promise;
  const pending=f.review.inspect(),rejected=assert.rejects(pending,/cancelled/);f.review.cancel();await rejected;
  f.fault.inspect=null;await f.review.inspect();first.resolve({status:'cancelled'});
  assert.equal(f.review.status,'inspected');await f.review.preview(['c1']);assert.equal(f.review.status,'ready');f.review.dispose();
});

const crop={x:4,y:8,width:40,height:50};
test('overview cannot authorize import; decoded detail binds the complete component selection',async()=>{
  const f=fixture({reviewAvailable:false,regionPreviewAvailable:true});await f.review.inspect();await f.review.preview(['c1','c2']);
  await f.review.region({mode:'overview'});assert.equal(f.review.sourceReviewAvailable,false);
  await assert.rejects(f.review.accept({confirmed:true}),/source-image/);assert.equal(f.calls.prepare.length,0);
  await f.review.region({mode:'crop',rect:crop,selected:['c1','c2']});assert.equal(f.review.sourceReviewAvailable,true);
  await f.review.accept({confirmed:true});assert.equal(f.calls.tokens.at(-1),'native_detail_token');
  assert.deepEqual(f.store.getSnapshot().document.graph.atoms.map(atom=>atom.id),['a0','a1']);
  f.store.commit(f.store.historyTarget(),{mode:'undo'});assert.deepEqual(f.store.getSnapshot().document,f.before.document);
});

test('crop or selection changes revoke detail acknowledgement without altering the drawing',async()=>{
  const f=fixture({reviewAvailable:false,regionPreviewAvailable:true});await f.review.inspect();await f.review.preview(['c1']);
  await f.review.region({mode:'crop',rect:crop,selected:['c1']});f.review.invalidateRegion();
  assert.equal(f.review.sourceReviewAvailable,false);await assert.rejects(f.review.accept({confirmed:true}));assert.equal(f.calls.regionCancel.length,1);
  await f.review.region({mode:'crop',rect:crop,selected:['c1']});await f.review.preview(['c2']);
  assert.equal(f.review.sourceReviewAvailable,false);assert.deepEqual(f.store.getSnapshot(),f.before);f.review.dispose();
});

test('incorrect or undecodable region receipts never create acceptance authority',async()=>{
  for(const change of [r=>r.sourceSha256=otherHash,r=>r.requestId='wrong_request',r=>r.sourceWidth=101,r=>r.width--,r=>r.rect.x++,r=>r.navigationOnly=true,r=>delete r.reviewToken]){
    const f=fixture({reviewAvailable:false,regionPreviewAvailable:true});await f.review.inspect();await f.review.preview(['c1']);
    f.fault.region=r=>{change(r);return r;};await assert.rejects(f.review.region({mode:'crop',rect:crop,selected:['c1']}));
    assert.equal(f.review.sourceReviewAvailable,false);assert.deepEqual(f.store.getSnapshot(),f.before);f.review.dispose();
  }
  const f=fixture({reviewAvailable:false,regionPreviewAvailable:true,reviewRegion:async()=>false});await f.review.inspect();await f.review.preview(['c1']);
  await assert.rejects(f.review.region({mode:'crop',rect:crop,selected:['c1']}),/decoded/);assert.equal(f.review.sourceReviewAvailable,false);f.review.dispose();
});

test('late region results cannot restore revoked or closed review authority',async()=>{
  for(const action of ['invalidateRegion','invalidatePreview','cancel']){
    const f=fixture({reviewAvailable:false,regionPreviewAvailable:true}),late=deferred();await f.review.inspect();await f.review.preview(['c1']);
    let receipt;f.fault.region=r=>{receipt=r;return late.promise;};
    const pending=f.review.region({mode:'crop',rect:crop,selected:['c1']}),rejected=assert.rejects(pending,/cancelled|changed/i);
    f.review[action]();await rejected;late.resolve(receipt);assert.equal(f.review.sourceReviewAvailable,false);
    assert.deepEqual(f.store.getSnapshot(),f.before);f.review.dispose();
  }
});

test('changed crop provenance prevents commit and crop invalidation cancels pending acceptance',async()=>{
  const f=fixture({reviewAvailable:false,regionPreviewAvailable:true});await f.review.inspect();await f.review.preview(['c1']);
  await f.review.region({mode:'crop',rect:crop,selected:['c1']});
  f.fault.prepare=(_id,ids)=>({...f.candidate(ids),provenance:{kind:'png-metadata-component-reconstruction',sourceSha256:hash,selectedComponents:ids}});
  await assert.rejects(f.review.accept({confirmed:true}),/crop provenance/);assert.equal(f.calls.open,0);
  await f.review.preview(['c1']);await f.review.region({mode:'crop',rect:crop,selected:['c1']});
  const pending=deferred();f.fault.prepare=()=>pending.promise;
  const result=f.review.accept({confirmed:true}),rejected=assert.rejects(result,/changed/i);f.review.invalidateRegion();await rejected;
  pending.resolve({});assert.deepEqual(f.store.getSnapshot(),f.before);f.review.dispose();
});

test('small originals keep ordinary acceptance until a crop edit or detail request opts into crop review',async()=>{
  const f=fixture({regionPreviewAvailable:true});await f.review.inspect();await f.review.preview(['c1']);
  assert.equal(f.review.sourceReviewAvailable,true);await f.review.region({mode:'overview'});assert.equal(f.review.sourceReviewAvailable,true);
  f.review.invalidateRegion();assert.equal(f.review.sourceReviewAvailable,false);await assert.rejects(f.review.accept({confirmed:true}));
  await f.review.region({mode:'overview'});assert.equal(f.review.sourceReviewAvailable,false);
  await f.review.region({mode:'crop',rect:crop,selected:['c1']});assert.equal(f.review.sourceReviewAvailable,true);
  await f.review.accept({confirmed:true});assert.equal(f.calls.tokens.at(-1),'native_detail_token');f.review.dispose();
});

test('a decoded crop with no selected components remains view-only',async()=>{
  const f=fixture({reviewAvailable:false,regionPreviewAvailable:true});await f.review.inspect();await f.review.preview(['c1']);
  const result=await f.review.region({mode:'crop',rect:crop,selected:[]});assert.equal(result.navigationOnly,false);assert.equal(result.reviewToken,undefined);
  assert.equal(f.review.sourceReviewAvailable,false);await assert.rejects(f.review.accept({confirmed:true}));f.review.dispose();
});

 test('accepted continuation binding uses returned commit receipt, not a later edit',async()=>{
  const f=fixture();await f.review.inspect();await f.review.preview(['c1']);
  f.fault.afterCommit=()=>{const snap=f.store.getSnapshot();f.store.commit({...snap.document,graph:graph('later')});};
  const accepted=await f.review.accept({confirmed:true});
  assert.equal(JSON.parse(accepted.continuationBinding.graphText).atoms[0].id,'a0');
  assert(accepted.continuationBinding.revision<f.store.getSnapshot().revision);
  f.review.dispose();
 });
 test('successful import with missing or mismatched receipt has no continuation binding',async()=>{
  for(const variant of ['missing','graph','context']){
   const f=fixture();await f.review.inspect();await f.review.preview(['c1']);
   f.fault.receipt=receipt=>variant==='missing'?null:{...receipt,document:{...receipt.document,...(variant==='graph'?{graph:graph('wrong')}:{analysis:{}})}};
   const accepted=await f.review.accept({confirmed:true});assert.equal(accepted.continuationBinding,null);assert.equal(accepted.name,'sample.png');assert.equal(f.calls.reset,1);f.review.dispose();
  }
 });
