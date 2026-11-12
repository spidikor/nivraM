import {requestImport} from './import-contract.mjs';
import {requestRender} from './render-contract.mjs';
import {prepareRender} from './document.mjs';
import {createImageReview} from './image-review.mjs';
import {validateCrop,regionImageData,overviewPoint,cropBetween} from './image-region.mjs';
import {attachPngIntake} from './image-intake.mjs';
import {createImageCorrection} from './image-correction.mjs';
import {attachOpticalSetup} from './optical-setup.mjs';

const $=id=>document.getElementById(id);
let initialized=false;

async function initialize(){
  if(initialized||!window.nivramDocument||!window.pywebview?.api?.inspect_image||!window.nivramNative?.resetDestination)return;
  initialized=true;const editor=window.nivramDocument;await editor.ready;
  const dialog=$('image-import-dialog'),source=$('image-import-source'),canvas=$('image-import-candidate');
  const overview=$('image-import-overview'),sourceWrap=$('image-import-source-wrap'),cropKeys=['x','y','width','height'];
  let epoch=0,busy=false,inspection=null,previewReady=false,dragStart=null,closing=false;
  const selected=()=>[...$('image-import-components').querySelectorAll('input:checked')].map(input=>input.value);
  const proposalSelected=()=>selected().some(id=>id==='optical-1'||id==='manual-1');
  function controls(){
    correction.setDisabled(busy||!previewReady);
    for(const input of $('image-import-components').querySelectorAll('input'))input.disabled=busy||input.dataset.supported!=='true';
    $('image-import-preview').disabled=busy||!selected().length;
    $('image-import-acknowledge').disabled=busy||!previewReady||!review.sourceReviewAvailable;
    $('image-import-accept').disabled=busy||!previewReady||!review.sourceReviewAvailable||!$('image-import-acknowledge').checked;
    $('image-import-overview-button').disabled=busy||!inspection?.regionPreviewAvailable;
    $('image-import-crop-button').disabled=busy||!inspection?.regionPreviewAvailable||proposalSelected();
    $('image-import-recognize').disabled=busy||!inspection?.regionPreviewAvailable||inspection?.opticalRecognition?.status!=='configured'||typeof window.pywebview.api.recognize_image!=='function';
    $('image-import-setup').disabled=busy;
    $('optical-setup-button').disabled=busy;
    $('image-import-manual').hidden=!inspection?.regionPreviewAvailable||typeof window.pywebview.api.preview_image_draft!=='function'||previewReady;
    $('image-import-manual-source').disabled=busy;
    $('image-import-manual-start').disabled=busy||$('image-import-manual').hidden||!$('image-import-manual-source').value.trim();
    for(const key of cropKeys)$('image-import-crop-'+key).disabled=busy;
  }
  function status(text,error=false){$('image-import-status').textContent=text;$('image-import-status').classList.toggle('image-import-error',error);}
  function clearPreview(){previewReady=false;correction.clear();$('image-import-acknowledge').checked=false;controls();}
  function resetRegionDisplay(){overview.removeAttribute('src');overview.hidden=true;sourceWrap.classList.remove('image-import-detail');source.removeAttribute('width');source.removeAttribute('height');source.alt='Original PNG selected for review';$('image-import-source-title').textContent='Original PNG';$('image-import-region').hidden=true;$('image-import-crop-status').textContent='No detail crop reviewed.';dragStart=null;}
  function clearDetail(){if(sourceWrap.classList.contains('image-import-detail')){source.removeAttribute('src');source.hidden=true;}$('image-import-acknowledge').checked=false;$('image-import-crop-status').textContent='Load the current crop and selection again before acknowledging.';controls();}
  function close(){epoch++;review.cancel();source.removeAttribute('src');source.hidden=true;resetRegionDisplay();correction.clear();inspection=null;previewReady=false;busy=false;$('image-import-manual-source').value='';controls();dialog.close();}
  function warnings(items){const list=$('image-import-warnings');list.replaceChildren();for(const text of [...new Set(items)]){const li=document.createElement('li');li.textContent=text;list.append(li);}}
  async function sourceReview(result,job){
    const prefix='data:image/png;base64,',data=result.sourcePreview,image=result.inspection.image;
    if(typeof data!=='string'||!data.startsWith(prefix)||data.length>5_592_440||!image||!Number.isInteger(image.width)||!Number.isInteger(image.height)||image.width<1||image.height<1||image.width*image.height>16_000_000)throw new Error('The source exceeds the supported preview limits.');
    const encoded=data.slice(prefix.length);if(!/^[A-Za-z0-9+/]+={0,2}$/.test(encoded))throw new Error('Invalid PNG preview encoding.');
    const bytes=Uint8Array.from(atob(encoded.slice(0,64)),character=>character.charCodeAt(0));
    if(bytes.length<24||[137,80,78,71,13,10,26,10].some((value,i)=>bytes[i]!==value))throw new Error('Invalid PNG preview signature.');
    const header=new DataView(bytes.buffer);if(header.getUint32(16)!==image.width||header.getUint32(20)!==image.height)throw new Error('The source preview dimensions do not match inspection.');
    source.src=data;source.hidden=false;await job.wait(source.decode());
    if(source.naturalWidth!==image.width||source.naturalHeight!==image.height)throw new Error('The decoded source dimensions do not match inspection.');
    return true;
  }
  async function regionReview(receipt,job){
    const requestEpoch=epoch,data=await job.wait(regionImageData(receipt)),decoded=new Image();decoded.src=data;
    await job.wait(decoded.decode());job.assertCurrent();
    if(requestEpoch!==epoch||!dialog.open)throw new DOMException('The source crop was cancelled.','AbortError');
    if(decoded.naturalWidth!==receipt.width||decoded.naturalHeight!==receipt.height)throw new Error('The decoded region dimensions do not match its receipt.');
    if(receipt.mode==='overview'){overview.src=data;overview.hidden=false;}
    else{
      sourceWrap.classList.add('image-import-detail');source.width=receipt.width;source.height=receipt.height;source.src=data;source.hidden=false;
      source.alt=`Original source crop at x ${receipt.rect.x}, y ${receipt.rect.y}, width ${receipt.width}, height ${receipt.height} pixels`;
      $('image-import-source-title').textContent='Original detail at 100%';
      $('image-import-crop-status').textContent=`Current detail: x ${receipt.rect.x}, y ${receipt.rect.y}, ${receipt.width} × ${receipt.height} source pixels. One source pixel per CSS pixel at 100% browser zoom. Scroll the detail viewport to inspect all edges.`;
    }
    return true;
  }
  const review=createImageReview({editor,native:window.pywebview.api,sessionToken:document.querySelector('meta[name="nivram-token"]')?.content||'',prepare:(result,base)=>prepareRender(result,base,document),reviewSource:sourceReview,
    render:(graph,style,job)=>requestRender('render',{graph,style},{job,token:document.querySelector('meta[name="nivram-token"]').content}),
    importSource:(source,style,job)=>requestImport({source,style},{job,token:document.querySelector('meta[name="nivram-token"]').content}),
    reviewRegion:regionReview,
    resetDestination:()=>window.nivramNative.resetDestination(),
    onInvalidate:message=>{close();const toast=$('toast');toast.textContent=message;toast.hidden=false;toast.classList.add('error');}});
  async function updateCorrection(operation){
    const request=++epoch;busy=true;$('image-import-acknowledge').checked=false;controls();status('Checking the corrected structure…');
    try{
      const prepared=await operation();if(request!==epoch)return;
      correction.history(prepared.history);
      warnings([...(inspection.inspection.warnings||[]),...prepared.warnings,...(inspection.inspection.unsupportedSources||[]).flatMap(item=>item.features||[])]);
      status('Draft updated. Compare the corrected structure with the source, then acknowledge it again before importing.');
      return prepared;
    }catch(error){if(request===epoch)status(error.message,true);throw error;}
    finally{if(request===epoch){busy=false;controls();}}
  }
  const correction=createImageCorrection({root:$('image-import-correction'),canvas,
    onChange:graph=>updateCorrection(()=>review.correct(graph)),
    onReplace:source=>updateCorrection(()=>review.replaceFromText(source)),
    onUndo:()=>updateCorrection(()=>review.undoCorrection()),onRedo:()=>updateCorrection(()=>review.redoCorrection()),onReset:()=>updateCorrection(()=>review.resetCorrection()),
    onError:error=>{if(dialog.open)status(error.message,true);}});
  const opticalStatus=availability=>{$('image-import-optical-status').textContent=availability?.status==='configured'?'Local recognition is configured. Runtime compatibility is checked when recognition starts. Choose a crop around one complete structure, then recognize it. Review and correct the proposal before accepting.':availability?.message||'Local optical recognition is not configured. Supported embedded structures can still be reviewed.';};
  attachOpticalSetup({document,events:window,native:window.pywebview.api,sessionToken:document.querySelector('meta[name="nivram-token"]')?.content||'',
    canOpen:()=>!busy&&!closing&&![...document.querySelectorAll('dialog[open]')].some(open=>open!==dialog),
    reviewIdentity:()=>review.inspection?.jobId??null,
    onSaved:(availability,jobId)=>{
      if(jobId&&review.updateOpticalAvailability(availability,jobId)){inspection=review.inspection;opticalStatus(availability);controls();}
      const toast=$('toast');toast.textContent='Local recognition setup saved. Model verified; runtime compatibility is checked when recognition starts.';toast.hidden=false;toast.classList.remove('error');
    }});
  function showInspection(result,{preserveRegion=false,selectedIds=[]}={}){
    inspection=result;const details=result.inspection,image=details.image;
    $('image-import-name').textContent=`${result.name} · ${image.width} × ${image.height} pixels`;
    $('image-import-engine').textContent=`Embedded molecular metadata · ${details.engine.name} ${details.engine.version}`;
    opticalStatus(result.opticalRecognition);
    if(preserveRegion)$('image-import-engine').textContent=selectedIds.includes('manual-1')?'Structure supplied by you; not recognized from the image':'Local MolScribe optical recognition proposal';
    $('image-import-limit').textContent=result.previewLimitation||'Compare the source with the reconstruction. Parsing does not independently verify image correspondence.';
    $('image-import-region').hidden=!result.regionPreviewAvailable;
    if(result.regionPreviewAvailable&&!preserveRegion){
      for(const [key,value] of Object.entries({x:0,y:0,width:Math.min(image.width,1000),height:Math.min(image.height,1000)}))$('image-import-crop-'+key).value=String(value);
      $('image-import-crop-x').max=String(image.width-1);$('image-import-crop-y').max=String(image.height-1);
    }
    if(!result.reviewAvailable&&!preserveRegion){source.removeAttribute('src');source.hidden=true;}
    const list=$('image-import-components');list.replaceChildren();
    for(const candidate of details.candidates){
      const row=document.createElement('div'),label=document.createElement('label'),input=document.createElement('input'),text=document.createElement('span');
      row.className='image-import-component';label.className='check-label';input.type='checkbox';input.value=candidate.id;input.dataset.supported=String(candidate.status==='supported');
      input.checked=selectedIds.includes(candidate.id);
      text.textContent=`${candidate.label||candidate.id} · charge ${candidate.formalCharge??'unreported'} · ${candidate.source||'metadata'}${candidate.status==='supported'?'':' · unsupported'}`;
      input.addEventListener('change',()=>{
        if(input.checked)for(const other of list.querySelectorAll('input'))if(other!==input&&(['optical-1','manual-1'].includes(input.value)||['optical-1','manual-1'].includes(other.value)))other.checked=false;
        epoch++;review.invalidatePreview();clearDetail();clearPreview();status(input.value==='manual-1'?'Supply structure text again to start a fresh review of the selected crop.':input.value==='optical-1'?'Recognize the selected crop again to review its optical proposal.':'Preview the explicitly selected components and review the current source detail.');
      });label.append(input,text);row.append(label);
      const reasons=[...(candidate.warnings||[]),...(candidate.unsupportedFeatures||[])];
      if(reasons.length){const detail=document.createElement('p');detail.className='muted';detail.textContent=reasons.join(' ');row.append(detail);}list.append(row);
    }
    const omitted=(details.unsupportedSources||[]).flatMap(item=>(item.features||[]).map(feature=>`${item.source}: ${feature}`));
    warnings([...(details.warnings||[]),...omitted,details.preservation||'Retain the original PNG; this is a component reconstruction.']);
    status(details.status==='three_d_coordinates'?'Open this PNG using Open 3D PNG in the optional 3D viewer. Embedded 3D coordinates are not flattened into the drawing.':details.candidates.some(c=>c.status==='supported')?'Select components to preview, or supply structure text for the selected crop.':result.regionPreviewAvailable&&typeof window.pywebview.api.preview_image_draft==='function'?'No supported embedded structures were found. Supply SMILES or 2D MOL text to start a review against the selected crop.':result.opticalRecognition?.status==='configured'?'No supported embedded structures were found. Choose a crop around a structure and recognize it locally.':'No supported embedded structures were found. Local recognition needs its model configured.');
  }
  $('image-import-button').hidden=false;
  async function startImport(file){
    if(busy||closing||document.querySelector('dialog[open]'))return;
    const request=++epoch;review.cancel();inspection=null;previewReady=false;source.removeAttribute('src');source.hidden=true;resetRegionDisplay();correction.clear();$('image-import-components').replaceChildren();warnings([]);
    $('image-import-name').textContent='';$('image-import-engine').textContent='';$('image-import-limit').textContent='';$('image-import-acknowledge').checked=false;$('image-import-manual-source').value='';
    busy=true;controls();status(file?'Reading the original PNG bytes locally.':'Choose a PNG to inspect locally.');dialog.showModal();
    try{const result=await review.inspect(file);if(request!==epoch)return;if(!result){close();return;}showInspection(result);}
    catch(error){if(request===epoch)status(error.message,true);}
    finally{if(request===epoch){busy=false;controls();}}
  };
  $('image-import-button').onclick=()=>startImport();
  if(typeof window.pywebview.api.inspect_image_bytes==='function')attachPngIntake({target:document,onFile:startImport,
    canStart:()=>!busy&&!closing&&!document.querySelector('dialog[open]'),
    onError:error=>{const toast=$('toast');toast.textContent=error.message;toast.hidden=false;toast.classList.add('error');}});
  $('image-import-preview').onclick=async()=>{
    const request=++epoch,ids=selected();clearPreview();busy=true;controls();status('Preparing the selected components…');
    try{
      const prepared=await review.preview(ids);if(request!==epoch)return;
      correction.load(prepared);correction.history(prepared.history);
      previewReady=true;warnings([...(inspection.inspection.warnings||[]),...prepared.warnings,...(inspection.inspection.unsupportedSources||[]).flatMap(item=>item.features||[])]);
      status(review.sourceReviewAvailable?'Correct the draft as needed, review both panels and the warnings, then confirm before importing.':'Metadata draft prepared. Load a detail crop for the current selection, correct the draft as needed, and review both panels before confirming.');
    }catch(error){if(request===epoch)status(error.message,true);}
    finally{if(request===epoch){busy=false;controls();}}
  };
  const cropInput=()=>validateCrop(Object.fromEntries(cropKeys.map(key=>[key,$('image-import-crop-'+key).value.trim()===''?NaN:Number($('image-import-crop-'+key).value)])),inspection.inspection.image.width,inspection.inspection.image.height);
  function changeCrop(){epoch++;review.invalidateRegion();clearDetail();if(proposalSelected()){clearPreview();status(selected().includes('manual-1')?'The crop changed. Supply structure text again to start a review of this crop.':'The crop changed. Recognize it again before correcting or accepting an optical proposal.');}}
  for(const key of cropKeys)$('image-import-crop-'+key).addEventListener('input',changeCrop);
  async function loadRegion(mode){
    let rect=null;
    try{if(mode==='crop')rect=cropInput();}catch(error){changeCrop();status(error.message,true);return;}
    const request=++epoch;review.invalidateRegion({requireCrop:mode==='crop'});clearDetail();if(proposalSelected())clearPreview();busy=true;controls();status(mode==='overview'?'Preparing a bounded navigation overview.':'Preparing an unscaled source detail crop.');
    try{
      await review.region({mode,rect,selected:mode==='crop'?selected():[]});if(request!==epoch)return;
      status(mode==='overview'?'Overview ready for navigation. It cannot authorize import. Choose a crop and select the complete metadata components to review.':!selected().length?'Detail ready for pixel review. No components are selected for import.':previewReady?'Detail ready. Review every selected component against the source and read the limitations before confirming.':'Detail ready. Preview the selected components, compare both panels, and read the limitations before confirming.');
    }catch(error){if(request===epoch){clearDetail();status(error.message,true);}}
    finally{if(request===epoch){busy=false;controls();}}
  }
  $('image-import-overview-button').onclick=()=>loadRegion('overview');$('image-import-crop-button').onclick=()=>loadRegion('crop');
  $('image-import-manual-source').addEventListener('input',controls);
  $('image-import-manual-start').onclick=async()=>{
    let rect;try{rect=cropInput();}catch(error){changeCrop();status(error.message,true);return;}
    const text=$('image-import-manual-source').value,request=++epoch;clearPreview();clearDetail();busy=true;controls();status('Preparing your structure and its source crop locally.');
    try{
      const result=await review.startFromText(text,rect);if(request!==epoch)return;
      showInspection(result.inspection,{preserveRegion:true,selectedIds:['manual-1']});
      correction.load(result.prepared);correction.history(result.prepared.history);previewReady=true;$('image-import-manual-source').value='';
      warnings([...(inspection.inspection.warnings||[]),...result.prepared.warnings]);
      status('Your supplied structure is ready for review. Compare it with the crop, make corrections, then acknowledge it before importing.');
    }catch(error){if(request===epoch){clearDetail();status(error.message,true);}}
    finally{if(request===epoch){busy=false;controls();}}
  };
  $('image-import-recognize').onclick=async()=>{
    let rect;try{rect=cropInput();}catch(error){changeCrop();status(error.message,true);return;}
    const request=++epoch;clearPreview();clearDetail();busy=true;controls();status('Recognizing the selected crop locally, with a 90-second processing limit. Cancel keeps the drawing unchanged.');
    try{
      const result=await review.recognize(rect);if(request!==epoch)return;
      showInspection(result.inspection,{preserveRegion:true,selectedIds:['optical-1']});
      correction.load(result.prepared);correction.history(result.prepared.history);previewReady=true;
      warnings([...(inspection.inspection.warnings||[]),...result.prepared.warnings,'Optical recognition can misread atoms, bonds, charges and stereochemistry. Any model score is uncalibrated and is not the probability that this structure is correct.']);
      status('Local recognition proposal ready. Correct it against the displayed crop, review all atoms and bonds, then acknowledge before importing.');
    }catch(error){if(request===epoch){clearDetail();status(error.message,true);}}
    finally{if(request===epoch){busy=false;controls();}}
  };
  const pointerPoint=event=>inspection&&overview.naturalWidth?overviewPoint(event.clientX,event.clientY,overview.getBoundingClientRect(),inspection.inspection.image.width,inspection.inspection.image.height,overview.naturalWidth,overview.naturalHeight):null;
  overview.addEventListener('pointerdown',event=>{if(busy||event.button!==0)return;dragStart=pointerPoint(event);if(dragStart){overview.setPointerCapture(event.pointerId);event.preventDefault();}});
  overview.addEventListener('pointerup',event=>{
    const start=dragStart;dragStart=null;if(overview.hasPointerCapture(event.pointerId))overview.releasePointerCapture(event.pointerId);if(!start||busy)return;
    const end=pointerPoint(event);if(!end)return;
    changeCrop();try{const rect=cropBetween(start,end,inspection.inspection.image.width,inspection.inspection.image.height);for(const key of cropKeys)$('image-import-crop-'+key).value=String(rect[key]);status('Crop coordinates selected. Load the detail at 100% to review it.');}catch(error){status(error.message,true);}
  });
  overview.addEventListener('pointercancel',()=>{dragStart=null;});
  overview.addEventListener('dragstart',event=>event.preventDefault());
  $('image-import-accept').onclick=async()=>{
    const request=++epoch,confirmed=$('image-import-acknowledge').checked;
    busy=true;$('image-import-acknowledge').checked=false;controls();status('Verifying the preserved source and importing…');
    try{
      const accepted=await review.accept({confirmed});
      if(request!==epoch)return;
      $('filename').value=accepted.name.replace(/\.png$/i,'');close();
      window.dispatchEvent(new CustomEvent('nivram-image-accepted',{detail:accepted.continuationBinding}));
      const toast=$('toast');toast.textContent='Reviewed components imported as an unsaved project. Undo restores the preceding drawing. Use Save As to choose a destination.';toast.hidden=false;toast.classList.remove('error');
    }catch(error){if(request===epoch){if(review.status!=='ready')clearPreview();status(error.message,true);}}
    finally{if(request===epoch){busy=false;controls();}}
  };
  $('image-import-acknowledge').onchange=controls;
  $('image-import-cancel').onclick=close;$('image-import-close').onclick=close;
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  window.addEventListener('nivram-close-review',()=>{closing=true;close();});
  window.addEventListener('nivram-close-review-ended',()=>{closing=false;});
}
for(const name of ['nivram-document-ready','pywebviewready','nivram-native-ready'])window.addEventListener(name,initialize);
initialize();
