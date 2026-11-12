import {sourceBinding} from './conformer-source.mjs';
import {cloneData,validateProject} from './document.mjs';
import {createJobController} from './jobs.mjs';
import {validateCrop,validateRegionReceipt} from './image-region.mjs';
import {preparePngIntake} from './image-intake.mjs';
import {validateOpticalAvailability} from './optical-setup.mjs';

const signature=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
const same=(a,b)=>signature(a)===signature(b);
const freeze=value=>{if(value&&typeof value==='object'){for(const child of Object.values(value))freeze(child);Object.freeze(value);}return value;};
const cancelled=()=>new DOMException('PNG review was cancelled.','AbortError');
const opticalSource='optical-recognition';
const manualSource='user-supplied-structure';
const modelHash='32821fa4ba8d17f9a92e374601438c5e757947dbf1141e9ad0ef51cccfa1c501';
// A native proposal can finish before a failed/cancelled image decode. Its
// transient inventory need not match the last published frontend inspection;
// the original source and embedded components must still match exactly.
const embeddedComponents=inspection=>inspection.candidates.filter(c=>c.source!==manualSource&&c.source!==opticalSource);
function structureText(source){
  if(typeof source!=='string'||!source.trim())throw new Error('Paste nonblank SMILES or 2D MOL text before preparing the draft.');
  if(source.length>1_000_000||new TextEncoder().encode(source).byteLength>1_000_000)throw new Error('Structure text exceeds the 1,000,000-byte limit.');
  return source;
}

function validateInspection(raw,id){
  const result=cloneData(raw),inspection=result.inspection;
  if(result.status!=='inspected'||result.jobId!==id||!inspection||inspection.schemaVersion!==1||!/^[a-f0-9]{64}$/.test(inspection.originalSha256))throw new Error('The image inspection returned an invalid source identity.');
  if(typeof result.name!=='string'||result.name.length>500||typeof result.reviewAvailable!=='boolean')throw new Error('The image inspection returned invalid review information.');
  if(result.regionPreviewAvailable!==undefined&&typeof result.regionPreviewAvailable!=='boolean')throw new Error('The image inspection returned invalid region capability.');
  if(!Array.isArray(inspection.candidates)||inspection.candidates.length>1024)throw new Error('The image inspection returned too many or invalid components.');
  const ids=new Set();
  for(const candidate of inspection.candidates){
    if(typeof candidate.id!=='string'||ids.has(candidate.id)||!['supported','unsupported'].includes(candidate.status))throw new Error('The image inspection returned ambiguous component identifiers.');
    ids.add(candidate.id);
  }
  return result;
}

export function createImageReview({editor,native,sessionToken='',prepare,render,importSource,reviewSource,reviewRegion=async()=>false,resetDestination=()=>{},onInvalidate=()=>{},newId=()=>`png_${crypto.randomUUID()}`,timeoutMs=60000}){
  const jobs=createJobController(()=>editor.getSnapshot().revision,{timeoutMs});
  let current=null;
  const release=id=>{try{Promise.resolve(native.cancel_image_import(id)).catch(()=>{});}catch{ /* The session is already invalidated locally. */ }};
  const releaseRegion=(session,requestId)=>{if(requestId)try{Promise.resolve(native.cancel_image_region(session.id,requestId)).catch(()=>{});}catch{ /* Local receipt revocation is immediate. */ }};
  function invalidateRegion({requireCrop=true}={}){
    const session=current;if(!session)return;
    if(requireCrop)session.requiresCrop=true;
    session.acceptTask?.cancel('The source crop or component selection changed.');
    session.regionEpoch++;session.regionTask?.cancel('The source crop or component selection changed.');session.regionTask=null;
    const ids=new Set([session.regionRequest,session.detail?.requestId]);for(const id of ids)releaseRegion(session,id);
    session.regionRequest=null;session.detail=null;
    if(session.optical||session.manual){session.optical=null;session.manual=null;session.preview=null;session.draft=null;session.status='inspected';}
  }
  const sourceReviewed=session=>Boolean(session?.inspection?.reviewAvailable&&!session.requiresCrop||(session?.detail&&session.preview&&same(session.detail.selected,session.preview.selected)));
  function cancel(message='PNG review cancelled. The drawing is unchanged.'){
    const previous=current;invalidateRegion({requireCrop:false});current=null;jobs.cancelAll(message);
    if(previous)release(previous.id);
  }
  function active(session,task,generation=session.generation){
    if(current!==session||session.generation!==generation)throw cancelled();
    task.assertCurrent();
    if(editor.getSnapshot().revision!==session.revision)throw new Error('The drawing changed. Start a new PNG review before importing.');
  }
  function candidate(raw,session,selected){
    const response=cloneData(raw),inspection=session.inspection.inspection;
    if(response.sourceSha256!==inspection.originalSha256||!same(response.selectedComponents,selected))throw new Error('The preview does not match the reviewed source and selected components.');
    const expected={atoms:[],bonds:[]},available=new Map(inspection.candidates.map(c=>[c.id,c]));
    if(!Array.isArray(selected)||!selected.length||new Set(selected).size!==selected.length)throw new Error('Select one or more distinct components explicitly.');
    const optical=selected.filter(id=>available.get(id)?.source===opticalSource);
    if(optical.length&&(!same(selected,['optical-1'])||!session.optical))throw new Error('Recognize the current crop again before previewing its optical proposal. Optical and metadata components cannot be combined.');
    const manual=selected.filter(id=>available.get(id)?.source===manualSource);
    if(manual.length&&(!same(selected,['manual-1'])||!session.manual))throw new Error('Prepare the supplied structure for the current crop again. Supplied structures must be reviewed separately from other components.');
    for(const id of selected){
      const component=available.get(id);
      if(component?.status!=='supported'||!component.graph)throw new Error('An explicitly selected component is unavailable or unsupported.');
      expected.atoms.push(...component.graph.atoms);expected.bonds.push(...component.graph.bonds);
    }
    if(!same(response.graph,expected))throw new Error('The reconstruction changed the selected components or their coordinates. Nothing was imported.');
    if(!Array.isArray(response.warnings)||response.warnings.some(w=>typeof w!=='string'))throw new Error('The reconstruction returned invalid warnings.');
    const prepared=prepare(cloneData(response),cloneData(session.document));
    if(!same(prepared.project,validateProject({...session.document,graph:expected})))throw new Error('The reconstruction changed other document settings. Nothing was imported.');
    return {response,prepared};
  }
  const unsubscribe=editor.subscribe(({revision})=>{
    if(current&&revision!==current.revision&&!current.committing){cancel();onInvalidate('The drawing changed. Start a new PNG review before importing.');}
  });
  const draftResult=session=>({...session.draft.prepared,project:cloneData(session.draft.prepared.project),
    warnings:[...cloneData(session.preview.response.warnings),...(same(session.draft.prepared.project.graph,session.preview.response.graph)?[]:['This structure was manually corrected. Review the final atoms, bonds, charges and stereochemistry against the original PNG.'])],
    history:{canUndo:session.draft.past.length>0,canRedo:session.draft.future.length>0}});
  async function updateDraft(input,load,{mode='record',recordUnchanged=false}={}){
    const session=current,draft=session?.draft,preview=session?.preview,regionEpoch=session?.regionEpoch;
    if(!session?.preview||!draft||session.status!=='ready')throw new Error('Preview the components before correcting them.');
    const generation=++session.generation,task=jobs.begin('image-correct',{input});
    session.status='correcting';
    try{
      const raw=await task.wait(load(session,task));active(session,task,generation);
      if(session.draft!==draft||session.preview!==preview||session.regionEpoch!==regionEpoch)throw cancelled();
      const prepared=prepare(raw,cloneData(session.document));
      if(!same(prepared.project,validateProject({...session.document,graph:prepared.project.graph})))throw new Error('The correction changed other document settings.');
      // Chemistry validation can normalize aromatic/stereo fields. The normalized
      // graph is displayed for review and becomes the only accept-ready draft.
      const previous=cloneData(draft.prepared.project.graph);
      if(mode==='undo'){draft.past.pop();draft.future.push(previous);}
      else if(mode==='redo'){draft.future.pop();draft.past.push(previous);}
      else if(recordUnchanged||!same(previous,prepared.project.graph)){draft.past=[...draft.past,previous].slice(-80);draft.future=[];}
      draft.prepared=prepared;session.status='ready';return draftResult(session);
    }catch(error){if(current===session&&session.generation===generation&&session.draft===draft&&session.preview===preview)session.status='ready';throw error;}
    finally{task.finish();}
  }
  function correct(graph,{mode='record'}={}){
    return updateDraft({graph},(session,task)=>{
      if(typeof render!=='function')throw new Error('Structure correction is unavailable.');
      const project=validateProject({...session.document,graph});
      return render(cloneData(project.graph),cloneData(project.style),task);
    },{mode});
  }
  return Object.freeze({
    get status(){return current?.status??'idle';},
    get inspection(){return current?.inspection??null;},
    get sourceReviewAvailable(){return sourceReviewed(current);},
    get correctionHistory(){return current?.draft?{canUndo:current.draft.past.length>0,canRedo:current.draft.future.length>0}:{canUndo:false,canRedo:false};},
    updateOpticalAvailability(availability,jobId){
      const session=current;
      if(!session?.inspection||session.id!==jobId||!['inspected','ready'].includes(session.status)||session.regionTask||session.acceptTask)return false;
      const opticalRecognition=validateOpticalAvailability(availability);
      // Setup changes future recognition only. Existing image/crop receipts,
      // corrected drafts and their independent Undo history remain authoritative.
      session.inspection=freeze({...session.inspection,opticalRecognition});return true;
    },
    correct:graph=>correct(graph),
    async replaceFromText(source){
      structureText(source);
      return updateDraft({source},(session,task)=>{
        if(typeof importSource!=='function')throw new Error('Structure text correction is unavailable.');
        return importSource(source,cloneData(session.document.style),task);
      },{recordUnchanged:true});
    },
    undoCorrection(){const graph=current?.draft?.past.at(-1);if(!graph)throw new Error('There is no correction to undo.');return correct(graph,{mode:'undo'});},
    redoCorrection(){const graph=current?.draft?.future.at(-1);if(!graph)throw new Error('There is no correction to redo.');return correct(graph,{mode:'redo'});},
    resetCorrection(){if(!current?.preview)throw new Error('Preview the components first.');return correct(current.preview.response.graph);},
    async inspect(file){
      cancel();const source=editor.getSnapshot(),session={id:newId(),revision:source.revision,document:cloneData(source.document),status:'inspecting',preview:null,committing:false,generation:0,regionEpoch:0,regionTask:null,regionRequest:null,detail:null,requiresCrop:false};
      current=session;const task=jobs.begin('image-inspect',{input:{jobId:session.id},timeout:180000});
      try{
        let bytes=null;
        if(file!==undefined){
          if(typeof native.inspect_image_bytes!=='function'||typeof sessionToken!=='string'||!sessionToken)throw new Error('PNG drop and image paste require the native desktop app.');
          bytes=await task.wait(preparePngIntake(file,task));active(session,task);
        }
        const raw=await task.wait(bytes?native.inspect_image_bytes(bytes.pngBase64,bytes.filename,session.id,sessionToken):native.inspect_image(session.id));active(session,task);
        if(raw?.status==='cancelled'){cancel();return null;}
        const result=validateInspection(raw,session.id);
        if(bytes&&(result.inspection.originalSha256!==bytes.sha256||result.name!==bytes.filename))throw new Error('The image inspection does not match the original byte-source identity.');
        if(result.reviewAvailable){
          try{result.reviewAvailable=(await task.wait(reviewSource(result,task)))===true;}
          catch(error){active(session,task);result.reviewAvailable=false;result.previewLimitation='The original PNG could not be decoded for visual review. Import is disabled.';}
          if(!result.reviewAvailable&&!result.previewLimitation)result.previewLimitation='A verified source-image preview is required before import.';
        }
        active(session,task);session.inspection=freeze(result);session.status='inspected';return session.inspection;
      }catch(error){if(current===session)cancel();throw error;}
      finally{task.finish();}
    },
    invalidatePreview(){
      if(!current)return;invalidateRegion({requireCrop:false});current.preview=null;current.draft=null;current.generation++;
      jobs.cancelAll('The component selection changed. Preview the new selection before importing.');
      current.status=current.inspection?'inspected':'inspecting';
    },
    async preview(ids){
      const session=current;if(!session?.inspection)throw new Error('Inspect a PNG before selecting components.');
      if(session.detail&&!same(ids,session.detail.selected)||session.regionTask)invalidateRegion({requireCrop:false});
      const selected=cloneData(ids),generation=++session.generation;session.preview=null;session.draft=null;session.status='previewing';
      const task=jobs.begin('image-preview',{input:{selected},settings:{jobId:session.id}});
      try{
        const raw=await task.wait(native.preview_image_components(session.id,selected,cloneData(session.document.style)));active(session,task,generation);
        const checked=candidate(raw,session,selected);active(session,task,generation);
        session.preview={...checked,selected};session.status='ready';
        session.draft={prepared:checked.prepared,past:[],future:[]};
        return {...draftResult(session),reviewAvailable:session.inspection.reviewAvailable};
      }catch(error){if(current===session&&session.generation===generation)session.status='inspected';throw error;}
      finally{task.finish();}
    },
    invalidateRegion,
    async startFromText(source,rect){
      structureText(source);
      const session=current;
      if(!session?.inspection?.regionPreviewAvailable||typeof native.preview_image_draft!=='function'||!sessionToken)throw new Error('A bounded source crop and the native desktop app are required to review a supplied structure.');
      const original=session.inspection,image=original.inspection.image,region=validateCrop(rect,image.width,image.height),selected=['manual-1'];
      invalidateRegion();jobs.cancelAll('A supplied structure replaced the pending review.');
      const generation=++session.generation,epoch=session.regionEpoch,requestId=newId(),task=jobs.begin('image-manual',{input:{source,rect:region},settings:{jobId:session.id,requestId},timeout:90000});
      session.preview=null;session.draft=null;session.status='preparing';session.regionTask=task;session.regionRequest=requestId;
      const assertManual=()=>{active(session,task,generation);if(session.regionEpoch!==epoch)throw cancelled();};
      try{
        const digest=await task.wait(crypto.subtle.digest('SHA-256',new TextEncoder().encode(source)));assertManual();
        const textSha256=[...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('');
        const raw=cloneData(await task.wait(native.preview_image_draft(session.id,requestId,{...region},source,cloneData(session.document.style),sessionToken)));assertManual();
        if(raw?.status!=='drafted'||raw.jobId!==session.id||raw.requestId!==requestId||!same(raw.selectedComponents,selected))throw new Error('The supplied draft does not match the current crop request.');
        const updated=validateInspection({...original,inspection:raw.inspection},session.id),details=updated.inspection;
        if(!['originalSha256','image','engine'].every(key=>same(details[key],original.inspection[key]))||!same(embeddedComponents(details),embeddedComponents(original.inspection)))throw new Error('The supplied draft changed the original source or embedded components.');
        const components=details.candidates.filter(c=>c.source===manualSource),component=components[0],supplied=component?.suppliedStructure;
        if(components.length!==1||component.id!=='manual-1'||component.status!=='supported'||!component.graph||component.recognition!==undefined||supplied?.textSha256!==textSha256||supplied.coordinates!=='from structure text; not traced from image')throw new Error('The supplied draft returned invalid structure provenance.');
        const receipt=validateRegionReceipt(raw.region,{jobId:session.id,requestId,sourceSha256:details.originalSha256,sourceWidth:image.width,sourceHeight:image.height,mode:'crop',rect:region,selected});
        if(await task.wait(reviewRegion(receipt,task))!==true)throw new Error('The source crop could not be decoded for review.');assertManual();
        const manual=freeze({requestId,suppliedStructure:cloneData(supplied),rect:{...region}});
        const preview=await task.wait(native.preview_image_components(session.id,selected,cloneData(session.document.style)));assertManual();
        const checked=candidate(preview,{...session,inspection:updated,manual},selected);assertManual();
        const {pngBase64,...proof}=receipt;
        session.inspection=freeze(updated);session.manual=manual;session.detail=freeze({requestId,receipt:cloneData(proof),selected});
        session.preview={...checked,selected};session.draft={prepared:checked.prepared,past:[],future:[]};session.status='ready';
        return {inspection:session.inspection,prepared:draftResult(session),region:freeze(cloneData(receipt))};
      }catch(error){releaseRegion(session,requestId);if(current===session&&session.generation===generation){session.status='inspected';session.preview=null;session.draft=null;}throw error;}
      finally{if(current===session&&session.regionEpoch===epoch){session.regionTask=null;session.regionRequest=null;}task.finish();}
    },
    async recognize(rect){
      const session=current;
      if(!session?.inspection?.regionPreviewAvailable||session.inspection.opticalRecognition?.status!=='configured'||typeof native.recognize_image!=='function'||!sessionToken)throw new Error(session?.inspection?.opticalRecognition?.message||'Local optical recognition is not configured.');
      const original=session.inspection,image=original.inspection.image,region=validateCrop(rect,image.width,image.height),selected=['optical-1'];
      invalidateRegion();jobs.cancelAll('A new recognition request replaced the pending review.');
      const generation=++session.generation,epoch=session.regionEpoch,requestId=newId(),task=jobs.begin('image-recognize',{input:{rect:region},settings:{jobId:session.id,requestId},timeout:350000});
      session.preview=null;session.draft=null;session.status='recognizing';session.regionTask=task;session.regionRequest=requestId;
      const assertRecognition=()=>{active(session,task,generation);if(session.regionEpoch!==epoch)throw cancelled();};
      try{
        const raw=cloneData(await task.wait(native.recognize_image(session.id,requestId,{...region},sessionToken)));assertRecognition();
        if(raw?.status!=='recognized'||raw.jobId!==session.id||raw.requestId!==requestId||!same(raw.selectedComponents,selected))throw new Error('The recognition result does not match the current crop request.');
        const updated=validateInspection({...original,inspection:raw.inspection},session.id),details=updated.inspection;
        if(!['originalSha256','image','engine'].every(key=>same(details[key],original.inspection[key]))||!same(embeddedComponents(details),embeddedComponents(original.inspection)))throw new Error('The recognition result changed the original source or embedded components.');
        const components=details.candidates.filter(c=>c.source===opticalSource),component=components[0],recognition=component?.recognition;
        if(components.length!==1||component.id!=='optical-1'||component.status!=='supported'||!component.graph||recognition?.method!=='MolScribe'||recognition.modelSha256!==(original.opticalRecognition.modelSha256||modelHash)||typeof recognition.coordinates!=='string'||!recognition.coordinates.length||recognition.confidence!=null&&(typeof recognition.confidence!=='number'||!Number.isFinite(recognition.confidence)||recognition.confidence<0||recognition.confidence>1))throw new Error('The recognition result returned an invalid model or structure proposal.');
        const receipt=validateRegionReceipt(raw.region,{jobId:session.id,requestId,sourceSha256:details.originalSha256,sourceWidth:image.width,sourceHeight:image.height,mode:'crop',rect:region,selected});
        if(recognition.inputSha256!==receipt.pngSha256)throw new Error('The recognized pixels do not match the reviewed source crop.');
        if(await task.wait(reviewRegion(receipt,task))!==true)throw new Error('The recognized source crop could not be decoded for review.');assertRecognition();
        const optical=freeze({requestId,recognition:cloneData(recognition),rect:{...region}});
        const preview=await task.wait(native.preview_image_components(session.id,selected,cloneData(session.document.style)));assertRecognition();
        const checked=candidate(preview,{...session,inspection:updated,optical},selected);assertRecognition();
        const {pngBase64,...proof}=receipt;
        session.inspection=freeze(updated);session.optical=optical;session.detail=freeze({requestId,receipt:cloneData(proof),selected});
        session.preview={...checked,selected};session.draft={prepared:checked.prepared,past:[],future:[]};session.status='ready';
        return {inspection:session.inspection,prepared:draftResult(session),region:freeze(cloneData(receipt))};
      }catch(error){releaseRegion(session,requestId);if(current===session&&session.generation===generation){session.status='inspected';session.preview=null;session.draft=null;}throw error;}
      finally{if(current===session&&session.regionEpoch===epoch){session.regionTask=null;session.regionRequest=null;}task.finish();}
    },
    async region({mode='crop',rect=null,selected=[]}={}){
      const session=current;if(!session?.inspection?.regionPreviewAvailable||typeof native.preview_image_region!=='function')throw new Error('Bounded region preview is unavailable for this source.');
      if(selected.some(id=>session.inspection.inspection.candidates.some(c=>c.id===id&&c.source===opticalSource)))throw new Error('Recognize the chosen crop again to review an optical proposal.');
      if(selected.includes('manual-1'))throw new Error('Prepare the supplied structure for the chosen crop again.');
      invalidateRegion({requireCrop:mode==='crop'});
      const image=session.inspection.inspection.image;
      if(!image||!Number.isSafeInteger(image.width)||!Number.isSafeInteger(image.height)||image.width<1||image.height<1||image.width>32768||image.height>32768||image.width*image.height>128_000_000)throw new Error('The source exceeds bounded region limits.');
      if(!['overview','crop'].includes(mode)||!Array.isArray(selected)||new Set(selected).size!==selected.length||selected.some(id=>!session.inspection.inspection.candidates.some(c=>c.id===id&&c.status==='supported')))throw new Error('Select distinct supported components for crop review.');
      if(mode==='overview'&&(rect!==null||selected.length))throw new Error('An overview is navigation only.');
      const region=mode==='crop'?validateCrop(rect,image.width,image.height):null,ids=cloneData(selected);
      const epoch=session.regionEpoch,requestId=newId(),task=jobs.begin('image-region',{input:{mode,rect:region,selected:ids},settings:{jobId:session.id,requestId}});
      session.regionTask=task;session.regionRequest=requestId;
      const assertRegion=()=>{if(current!==session||session.regionEpoch!==epoch)throw cancelled();task.assertCurrent();};
      try{
        const raw=await task.wait(native.preview_image_region(session.id,requestId,region&&{...region},mode,cloneData(ids)));assertRegion();
        const receipt=validateRegionReceipt(raw,{jobId:session.id,requestId,sourceSha256:session.inspection.inspection.originalSha256,sourceWidth:image.width,sourceHeight:image.height,mode,rect:region,selected:ids});
        if(await task.wait(reviewRegion(receipt,task))!==true)throw new Error('The source crop could not be decoded for review.');assertRegion();
        if(mode==='crop'&&ids.length){const {pngBase64,...proof}=receipt;session.detail=freeze({requestId,receipt:cloneData(proof),selected:ids});}
        return freeze(cloneData(receipt));
      }catch(error){releaseRegion(session,requestId);throw error;}
      finally{if(current===session&&session.regionEpoch===epoch){session.regionTask=null;session.regionRequest=null;}task.finish();}
    },
    async accept({confirmed=false}={}){
      const session=current,reviewed=session?.preview,draft=session?.draft,regionEpoch=session?.regionEpoch;
      if(!reviewed||!sourceReviewed(session)||session.status!=='ready')throw new Error('A current source-image and component preview is required before import.');
      if(!session.draft.prepared.project.graph.atoms.length)throw new Error('The corrected structure is empty. Add an atom or reset the draft before importing.');
      if(confirmed!==true)throw new Error('Confirm that you reviewed the source, selected components and limitations before importing.');
      const generation=++session.generation,task=jobs.begin('image-accept',{input:{selected:reviewed.selected},settings:{jobId:session.id}});session.status='accepting';
      session.acceptTask=task;let preparing=true;
      try{
        const token=session.inspection.reviewAvailable&&!session.requiresCrop?null:session.detail?.receipt.reviewToken;
        const raw=await task.wait(native.prepare_image_import(session.id,cloneData(reviewed.selected),cloneData(session.document.style),token));preparing=false;active(session,task,generation);
        const checked=candidate(raw,session,reviewed.selected);
        if(!same(checked.response.graph,reviewed.response.graph)||!same(checked.response.warnings,reviewed.response.warnings))throw new Error('The reconstruction or warnings changed. Preview and review it again.');
        const provenance=checked.response.provenance;
        const optical=session.optical,manual=session.manual,kind=optical?'png-optical-reconstruction':manual?'png-user-supplied-structure':'png-metadata-component-reconstruction';
        if(!provenance||provenance.kind!==kind||provenance.sourceSha256!==session.inspection.inspection.originalSha256||!same(provenance.selectedComponents,reviewed.selected)||optical&&(!token||!same(provenance.recognition,optical.recognition)))throw new Error('Preservation of the original PNG and recognition provenance could not be verified. Nothing was imported.');
        if(manual&&(!token||provenance.recognition!==undefined||!same(provenance.suppliedStructure,manual.suppliedStructure)||!same(provenance.suppliedGraph,checked.response.graph)))throw new Error('Preservation of the supplied structure provenance could not be verified. Nothing was imported.');
        if(token){
          const receipt=session.detail?.receipt,region=provenance.reviewedRegion;
          if(!receipt||!region||region.kind!=='crop'||!['requestId','sourceSha256','sourceWidth','sourceHeight','width','height','pngSha256'].every(key=>region[key]===receipt[key])||!same(region.rect,receipt.rect)||region.scale!=='one-source-pixel-per-image-pixel'||region.metadataCorrespondence!==(optical?'unverified optical recognition of this crop':manual?'user-supplied structure; image correspondence not independently verified':'not established; whole components retained'))throw new Error('The preserved crop provenance does not match the reviewed detail. Nothing was imported.');
        }
        const graph=cloneData(session.draft.prepared.project.graph);
        if(!same(graph,checked.response.graph))provenance.correction={kind:'user-edited-graph',baselineGraph:cloneData(checked.response.graph),reviewedGraph:cloneData(graph),imageCorrespondence:'user-reviewed; not independently verified'};
        active(session,task,generation);session.committing=true;
        const document=validateProject({...session.document,graph,analysis:{imageImport:provenance}});
        // The editor receives the abort signal itself, so cancellation cannot
        // leave an uninterruptible render that commits after the dialog closes.
        const accepted=await editor.openProject(document,{expectedRevision:session.revision,asUnsaved:true,signal:task.signal,rememberView:true,returnSnapshot:true});
        if(accepted!==true&&accepted?.accepted!==true)throw new Error('The reconstruction was not committed. The preceding drawing is retained.');
        let continuationBinding=null;
        try{if(accepted?.snapshot){
          const committed=sourceBinding(accepted.snapshot),reviewedBinding=sourceBinding({revision:accepted.snapshot.revision,document});
          if(committed.graphText===reviewedBinding.graphText&&committed.sourceContextText===reviewedBinding.sourceContextText)continuationBinding=committed;
        }}catch{/* Optional 3D context limits must not turn a committed import into a failure. */}
        resetDestination();if(current===session)current=null;release(session.id);
        return {name:session.inspection.name,provenance:cloneData(provenance),continuationBinding};
      }catch(error){
        if(current===session&&session.generation===generation){
          // Native preparation only preserves the source; it cannot commit the
          // editor. Retain corrections after a rejected preparation while the
          // exact review remains current. Every retry prepares and checks anew.
          // A returned invalid receipt, cancellation, or possible editor commit
          // must never become retryable through this recovery path.
          const recoverable=preparing&&!task.signal.aborted&&session.preview===reviewed&&session.draft===draft&&session.regionEpoch===regionEpoch&&editor.getSnapshot().revision===session.revision;
          if(recoverable)session.status='ready';
          else{session.preview=null;session.draft=null;session.status='inspected';}
        }
        throw error;
      }
      finally{session.committing=false;if(session.acceptTask===task)session.acceptTask=null;task.finish();}
    },
    cancel,
    dispose(){cancel();unsubscribe();}
  });
}
