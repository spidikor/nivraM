import {cloneData,prepareRender} from './document.mjs';
import {exportPng,validateOptions} from './export.mjs';

export const MAX_BATCH_PNG_BYTES=32*1024*1024;
export const MAX_BATCH_TRANSPORT_ESTIMATE_BYTES=512*1024*1024;
export function estimateBatchTransportBytes(bytes){
  if(!Number.isSafeInteger(bytes)||bytes<0||bytes>MAX_BATCH_PNG_BYTES)throw new Error('PNG exceeds the batch byte limit.');
  // Reserve two raw-size allocations plus up to four two-byte encoded strings
  // across FileReader, serialization and native bridge handoff. This is a
  // working estimate, not a guarantee about browser/native process total RSS.
  return 2*bytes+8*(4*Math.ceil(bytes/3));
}
const SVG_NS='http://www.w3.org/2000/svg';
const abortError=()=>new DOMException('Batch export cancelled.','AbortError');
const check=signal=>{if(signal?.aborted)throw signal.reason??abortError();};
const frozen=value=>{if(value&&typeof value==='object'){for(const child of Object.values(value))frozen(child);Object.freeze(value);}return value;};

/** Reuse the exact production renderer in an isolated, connected SVG stage. */
export async function renderBatchPng(prepared,{signal,ownerDocument=globalThis.document,removeChemistryMetadata=false}={}){
  check(signal);
  const valid=prepareRender(prepared,prepared.project,ownerDocument);
  if(JSON.stringify(valid.project.graph)!==JSON.stringify(prepared.project.graph))throw new Error('The batch scene changed the input graph.');
  const stage=ownerDocument.createElementNS(SVG_NS,'svg');
  stage.setAttribute('width',String(valid.scene.width));stage.setAttribute('height',String(valid.scene.height));
  stage.setAttribute('aria-hidden','true');
  Object.assign(stage.style,{position:'fixed',left:'-100000px',top:'0',visibility:'hidden',pointerEvents:'none'});
  const group=ownerDocument.createElementNS(SVG_NS,'g');group.append(valid.fragment);stage.append(group);
  ownerDocument.body.append(stage);
  try{return await exportPng(group,valid.project.export,{signal,graph:valid.project.graph,removeChemistryMetadata});}
  finally{stage.remove();}
}

/** FileReader avoids a second JS byte-array plus a whole binary-string copy. */
export function pngBase64(blob,{signal}={}){
  check(signal);
  if(!(blob instanceof Blob)||blob.type!=='image/png'||blob.size<1||blob.size>MAX_BATCH_PNG_BYTES)
    return Promise.reject(new Error('The PNG exceeds the 32 MiB batch transport limit. Reduce the export width.'));
  if(estimateBatchTransportBytes(blob.size)>MAX_BATCH_TRANSPORT_ESTIMATE_BYTES)
    return Promise.reject(new Error('The PNG exceeds the separate batch transport memory budget.'));
  return new Promise((resolve,reject)=>{
    const reader=new FileReader();
    const clean=()=>{signal?.removeEventListener('abort',abort);reader.onload=reader.onerror=reader.onabort=null;};
    const abort=()=>{reader.abort();clean();reject(signal.reason??abortError());};
    reader.onerror=()=>{clean();reject(new Error('PNG encoding failed.'));};
    reader.onabort=()=>{clean();reject(abortError());};
    reader.onload=()=>{
      try{
        check(signal);const prefix='data:image/png;base64,';
        if(typeof reader.result!=='string'||!reader.result.startsWith(prefix))throw new Error('Unexpected PNG encoding.');
        const encoded=reader.result.slice(prefix.length);clean();resolve(encoded);
      }catch(error){clean();reject(error);}
    };
    signal?.addEventListener('abort',abort,{once:true});
    reader.readAsDataURL(blob);
  });
}

/** Testable sequential orchestration. No editor commit/save method is accepted. */
export function createBatchExport({native,render=renderBatchPng,encode=pngBase64,onProgress=()=>{}}){
  let current=null;
  function progress(job,phase,item=null){
    onProgress({phase,item,completed:job.items.filter(value=>['exported','failed','cancelled'].includes(value.status)).length,
      total:job.items.length,items:cloneData(job.items)});
  }
  function replace(job,item){const index=job.items.findIndex(value=>value.id===item?.id);if(index>=0)job.items[index]=cloneData(item);}
  function requestCancel(job){
    if(!job.controller.signal.aborted)job.controller.abort(abortError());
    if(job.session&&!job.cancelPromise){
      job.cancelPromise=Promise.resolve().then(()=>native.cancel_batch_export(job.session));
      job.cancelPromise.catch(()=>{});
    }
    return job.cancelPromise;
  }
  return Object.freeze({
    get busy(){return current!==null;},
    cancel(){if(current){requestCancel(current);progress(current,'cancelling');}},
    async run(settings){
      if(current)throw new Error('A batch is already running.');
      const immutable=frozen(cloneData(settings));validateOptions(immutable.export);
      const removeChemistryMetadata=immutable.removeChemistryMetadata??false;
      if(typeof removeChemistryMetadata!=='boolean'||immutable.removeChemistryMetadata===null)throw new TypeError('Remove chemistry metadata must be true or false.');
      const job={controller:new AbortController(),session:null,cancelPromise:null,items:[]};current=job;
      const signal=job.controller.signal;
      try{
        progress(job,'choosing');
        const chosen=await native.begin_batch_export(immutable);
        if(chosen.status!=='running')return chosen;
        job.session=chosen.sessionId;job.items=cloneData(chosen.items);
        if(signal.aborted)requestCancel(job);
        for(const initial of [...job.items]){
          if(signal.aborted)break;
          if(initial.status!=='pending'){progress(job,'progress',initial);continue;}
          let failure='render_failed';
          progress(job,'preparing',initial);
          const prepared=await native.prepare_batch_item(job.session,initial.id);
          replace(job,prepared.item);
          if(prepared.manifestError)throw new Error(prepared.manifestError);
          if(signal.aborted)break;
          if(prepared.status!=='ready'){progress(job,'progress',prepared.item);continue;}
          let encoded;
          try{
            check(signal);progress(job,'rendering',prepared.item);
            failure='export_failed';
            let rendered=await render(prepared,{signal,removeChemistryMetadata});
            check(signal);failure='encoding_failed';
            encoded=await encode(rendered.blob,{signal});
            rendered=null;check(signal);
          }catch(error){
            if(signal.aborted)break;
            const failed=await native.fail_batch_item(job.session,initial.id,failure);
            replace(job,failed.item);
            if(failed.manifestError)throw new Error(failed.manifestError);
            progress(job,'progress',job.items.find(value=>value.id===initial.id));
            continue;
          }
          // A failed bridge acknowledgement leaves publication uncertain. Stop
          // the session instead of reclassifying it as a local rendering error.
          progress(job,'saving',prepared.item);
          const saved=await native.write_batch_png(job.session,initial.id,encoded);
          encoded=null;replace(job,saved.item);
          if(saved.manifestError)throw new Error(saved.manifestError);
          if(saved.status==='stopped')throw new Error('PNG publication could not be verified. The batch stopped.');
          progress(job,'progress',job.items.find(value=>value.id===initial.id));
        }
        if(signal.aborted){requestCancel(job);await job.cancelPromise;}
        const result=await native.finish_batch_export(job.session);
        job.items=cloneData(result.items);progress(job,result.status);
        return result;
      }catch(error){
        if(job.session){
          requestCancel(job);
          try{
            const terminal=await job.cancelPromise;
            if(['cancelled','stopped','completed'].includes(terminal?.status)&&Array.isArray(terminal.items)){
              job.items=cloneData(terminal.items);progress(job,'stopped');
            }
          }catch{/* The original error remains visible; no terminal receipt is invented. */}
        }
        throw error;
      }finally{if(current===job)current=null;}
    }
  });
}

function element(tag,text,properties={}){
  const node=document.createElement(tag);if(text!==undefined)node.textContent=text;
  Object.assign(node,properties);return node;
}

let initialized=false;
export async function initializeBatchExport(){
  if(initialized||!globalThis.window?.nivramDocument||!window.pywebview?.api?.begin_batch_export)return;
  const panel=document.querySelector('.appearance-panel');if(!panel)return;
  initialized=true;
  const editor=window.nivramDocument,native=window.pywebview.api;await editor.ready;
  const details=element('details',undefined,{id:'batch-export-controls'});
  details.append(element('summary','Batch PNG export'));
  const choice=element('select',undefined,{id:'batch-export-profile'});
  const label=element('label','Drawing profile');label.htmlFor=choice.id;
  const refresh=element('button','Refresh profiles',{type:'button'});
  const start=element('button','Choose files and export PNGs',{id:'batch-export-button',type:'button'});
  const cancel=element('button','Cancel batch',{id:'batch-export-cancel',type:'button',disabled:true});
  const removeMetadata=element('input',undefined,{id:'batch-remove-chemistry-metadata',type:'checkbox',checked:false,autocomplete:'off'});
  const metadataLabel=element('label',undefined,{className:'check-label'});metadataLabel.append(removeMetadata,document.createTextNode(' Remove chemistry metadata?'));
  const info=element('p','Choose .nivram or 2D MOL files and an output folder. Existing files keep their names; new exports receive unique names.');
  const status=element('p','Ready for a batch export.',{id:'batch-export-status'});status.setAttribute('role','status');
  const meter=element('progress',undefined,{max:1,value:0});meter.setAttribute('aria-label','Batch files processed');
  const results=element('ol',undefined,{id:'batch-export-results'});
  details.append(label,choice,refresh,metadataLabel,info,start,cancel,meter,status,results);panel.append(details);
  let profiles=[],closeReview=false;
  function summarizeSettings(){
    const profile=profiles.find(value=>`${value.id}/${value.revision}`===choice.value);
    const source=profile??editor.getSnapshot().document;
    info.textContent=`${source.export.width} pixels wide; ${source.export.margin} ${source.export.marginMode==='percent'?'percent':'pixel'} margins; ${source.export.transparentBackground?'transparent background':'opaque white'}. ${profile?profile.name+' (revision '+profile.revision+')':'Current drawing settings'} will be frozen when the batch starts. ${removeMetadata.checked?'Chemistry metadata will be removed from PNGs.':'Recoverable chemistry metadata will be retained.'} Original inputs and the batch manifest are not scrubbed; existing output files are preserved.`;
  }
  async function reload(){
    const selected=choice.value;
    profiles=typeof native.list_profiles==='function'?(await native.list_profiles()).profiles:[];
    choice.replaceChildren(element('option','Current drawing settings',{value:''}));
    for(const profile of profiles)choice.append(element('option',`${profile.name}, revision ${profile.revision}`,{value:`${profile.id}/${profile.revision}`}));
    if([...choice.options].some(option=>option.value===selected))choice.value=selected;
    summarizeSettings();
  }
  const runner=createBatchExport({native,onProgress:update=>{
    meter.max=Math.max(1,update.total);meter.value=update.completed;
    status.textContent=update.phase==='choosing'?'Choose input files, then an output folder in the native dialogs.':
      update.phase==='cancelling'?'Cancelling; completed PNG files will be retained.':
      `${update.completed} of ${update.total} files processed${update.item?': '+update.item.name:''}.`;
    results.replaceChildren(...update.items.map(item=>element('li',`${item.name}: ${item.status}${item.outputName?' → '+item.outputName:''}${item.error?' — '+item.error.message:''}`)));
  }});
  cancel.onclick=()=>runner.cancel();choice.onchange=summarizeSettings;removeMetadata.onchange=summarizeSettings;
  refresh.onclick=async()=>{try{await reload();}catch{status.textContent='Saved profiles could not be loaded.';}};
  editor.subscribe(()=>{if(!runner.busy)summarizeSettings();});
  window.addEventListener('nivram-close-review',()=>{
    closeReview=true;runner.cancel();start.disabled=choice.disabled=refresh.disabled=removeMetadata.disabled=true;
  });
  window.addEventListener('nivram-close-review-ended',()=>{
    closeReview=false;if(!runner.busy)start.disabled=choice.disabled=refresh.disabled=removeMetadata.disabled=false;
  });
  start.onclick=async()=>{
    if(closeReview)return;
    const profile=profiles.find(value=>`${value.id}/${value.revision}`===choice.value);
    const document=editor.getSnapshot().document;
    const settings={style:cloneData(profile?.style??document.style),export:cloneData(profile?.export??document.export),
      profile:profile?{id:profile.id,revision:profile.revision,name:profile.name}:null,removeChemistryMetadata:removeMetadata.checked};
    start.disabled=choice.disabled=refresh.disabled=removeMetadata.disabled=true;cancel.disabled=false;
    try{
      const result=await runner.run(settings);
      if(result.items){
        const successful=result.items.filter(item=>item.status==='exported').length;
        status.textContent=`${successful} of ${result.items.length} PNG files saved. ${result.status==='cancelled'?'Batch cancelled. ':''}${result.manifestError??'Manifest: '+result.manifestName+'.'} Completed files were retained.`;
      }else status.textContent='Batch selection cancelled.';
    }catch{status.textContent='The batch stopped before completion. Saved PNGs and the latest available manifest were retained.';}
    finally{start.disabled=choice.disabled=refresh.disabled=removeMetadata.disabled=closeReview;cancel.disabled=true;}
  };
  try{await reload();}catch{choice.append(element('option','Current drawing settings',{value:''}));summarizeSettings();status.textContent='Saved profiles are unavailable; current drawing settings can still be used.';}
}

if(typeof window!=='undefined'){
  window.addEventListener('nivram-native-ready',()=>void initializeBatchExport());
  void initializeBatchExport();
}
