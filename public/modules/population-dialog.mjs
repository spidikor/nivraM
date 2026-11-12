import {createPopulationAnalysis, MAX_MODEL_BYTES} from './population-analysis.mjs';
import {mountPopulationChart} from './supplied-population-chart.mjs';
import {serializePopulationJson, populationCsv, createPhSelection, referenceView} from './population-view.mjs';
import {savePopulationExport} from './population-save.mjs';
import {createPopulationStateReview} from './population-state-review.mjs';

export function mountPopulationDialog({editor, token, dom=document, fetcher=globalThis.fetch, initialCloseReview=false}) {
  const view=dom.defaultView, find=id=>dom.getElementById(id), removers=[];
  const button=find('open-population-model'), dialog=find('population-dialog'), file=find('population-model-file');
  const status=find('population-model-status'), host=find('population-model-chart'), cancelButton=find('cancel-population-model');
  const chooseButton=find('choose-population-model'), fileName=find('population-model-name');
  const contextSelect=find('population-context'), contextStatus=find('population-context-status');
  const exportStatus=find('population-export-status'), exportButtons=['json','csv','png'].map(kind=>find(`population-export-${kind}`));
  const structuresFile=find('population-structures-file'), structuresButton=find('choose-population-structures');
  const statePanel=find('population-state-panel'), stateSummary=find('population-state-summary'), stateStatus=find('population-state-status');
  const stateCanvas=find('population-state-preview'), stateLimits=find('population-state-limitations'), adoptButton=find('population-state-adopt'), cancelStateButton=find('population-state-cancel'), previewStateButton=find('population-state-preview-button');
  let stopped=false, closeReview=initialCloseReview, chart=null, epoch=0, dataset=null, explorer=null, unsubscribe=()=>{}, exporting=null;
  let stateReview=null, stateEpoch=0, stateBusy=false, adopting=false, selectionUnsubscribe=()=>{};
  let referenceError=null;
  const theme=()=>dom.documentElement.dataset.theme==='dark'?'dark':'light';
  const observer=new view.MutationObserver(()=>chart?.setTheme(theme()));
  const listen=(target,event,callback)=>{target.addEventListener(event,callback);removers.push(()=>target.removeEventListener(event,callback));};
  function refreshReferenceControls(){contextSelect.disabled=!explorer||stopped||closeReview||adopting||!!exporting;}
  function refreshExports(){for(const control of exportButtons)control.disabled=!chart||!!exporting||stopped||closeReview||adopting||!!referenceError;refreshReferenceControls();}
  function referenceFailed(error){
    referenceError=error;contextStatus.textContent='The selected reference could not be displayed. Population exports are disabled until a reference update succeeds. Choose another context to retry.';refreshExports();
  }
  function syncReference(context){
    contextSelect.value=context.contextId??'';
    try{chart?.updateContext({contextId:context.contextId,comparison:context.comparison});}
    catch(error){referenceFailed(error);throw error;}
    referenceError=null;contextStatus.textContent='Reference context updated. Supplied-model values and the drawing are unchanged.';refreshExports();
  }
  function clearStatePreview(){stateCanvas.replaceChildren();stateCanvas.setAttribute('hidden','');stateLimits.hidden=true;adoptButton.disabled=true;}
  function refreshStateSummary(){
    if(!chart||!analysis.result)return;
    const snapshot=chart.getSnapshot(),selected=analysis.result.envelope.model.states.find(state=>state.state_id===snapshot.selectedStateId);
    const row=snapshot.table.rows.find(value=>value.stateId===snapshot.selectedStateId);
    stateSummary.textContent=`State ${selected.state_id}; supplied structure identity: ${selected.structure_id}. Selected pH ${snapshot.selection.ph.toFixed(1)}: ${row?row.percentage.toLocaleString('en-US',{maximumSignificantDigits:6})+'% ('+snapshot.table.normalizationScope+').':'population unavailable; no value is substituted.'}`;
    adoptButton.disabled=adopting||stateBusy||stateReview?.status!=='ready'||!row||snapshot.table.status!=='computed';
    adoptButton.title=row?'':'Choose a pH with a computed population before using this state.';
    previewStateButton.disabled=adopting||stateBusy||!stateReview?.hasState(snapshot.selectedStateId);
  }
  function releaseChart() {
    exporting?.controller.abort();exporting=null;
    stateEpoch++;stateReview?.dispose();stateReview=null;stateBusy=false;selectionUnsubscribe();selectionUnsubscribe=()=>{};
    clearStatePreview();statePanel.hidden=true;structuresButton.disabled=true;previewStateButton.disabled=true;cancelStateButton.hidden=true;
    structuresFile.value='';find('population-structures-name').textContent='';stateStatus.textContent='';stateSummary.textContent='';
    chart?.destroy();chart=null;host.replaceChildren();observer.disconnect();refreshExports();
  }
  function cancel(message='Calculation cancelled. Your drawing is unchanged.') {
    epoch++;analysis.cancel();releaseChart();cancelButton.hidden=true;chooseButton.disabled=false;
    file.value='';fileName.textContent='';status.textContent=message;
    exportStatus.textContent='';
  }
  const analysis=createPopulationAnalysis({editor, token, fetcher, onInvalidate:message=>{if(!adopting)cancel(message);}});
  function render(result) {
    releaseChart();
    const context=explorer?.getSnapshot()??{contextId:null,comparison:null};
    const selection=createPhSelection();
    stateReview=createPopulationStateReview({editor,token,result,fetcher,dom});
    chart=mountPopulationChart(host,{Chart:view.Chart,envelope:result.envelope,selection,dataset,...context,theme:theme(),onStateSelect:()=>{
      if(adopting){stateEpoch++;stateReview?.cancel();clearStatePreview();}else void inspectSelected();
    }});
    referenceError=null;
    if(explorer)contextStatus.textContent='Reference context is shared with the editor controls. Existing session comparisons are retained.';
    selectionUnsubscribe=selection.subscribe(refreshStateSummary);
    statePanel.hidden=false;structuresButton.disabled=false;refreshStateSummary();
    stateStatus.textContent='Choose a structure file matching this model to preview a state. The drawing is unchanged.';
    observer.observe(dom.documentElement,{attributes:true,attributeFilter:['data-theme']});
    exportStatus.textContent='JSON and typed CSV retain all states and samples. PNG saves the visible curves on white unless transparent background is selected. These are supplied-model results.';
    refreshExports();
  }
  async function inspectSelected(){
    if(!chart||!stateReview||adopting||stopped||closeReview)return;
    const owned=stateReview,currentChart=chart,generation=++stateEpoch,stateId=chart.getSnapshot().selectedStateId;
    owned.cancel();clearStatePreview();refreshStateSummary();
    if(!owned.hasState(stateId)){
      stateBusy=false;cancelStateButton.hidden=true;
      stateStatus.textContent='A matching structure file must include this state and the reference state before a preview is available.';
      return;
    }
    stateBusy=true;previewStateButton.disabled=true;cancelStateButton.hidden=false;stateStatus.textContent='Preparing and checking the supplied 2D state.';
    try{
      const preview=await owned.previewState(stateId);
      if(generation!==stateEpoch||stateReview!==owned||chart!==currentChart||!dialog.open||stopped)return;
      stateCanvas.setAttribute('viewBox',`0 0 ${preview.scene.width} ${preview.scene.height}`);
      stateCanvas.replaceChildren(preview.fragment.cloneNode(true));stateCanvas.removeAttribute('hidden');stateLimits.hidden=false;
      stateCanvas.setAttribute('aria-label',`Supplied 2D preview of state ${stateId}`);
      stateStatus.textContent='Preview ready. Review the structure before using it. The current drawing is unchanged.';
    }catch(error){if(generation===stateEpoch&&stateReview===owned&&!stopped)stateStatus.textContent=error.message;}
    finally{if(generation===stateEpoch){stateBusy=false;cancelStateButton.hidden=true;refreshStateSummary();}}
  }
  async function readStructures(){
    const selected=structuresFile.files?.[0];
    if(!selected||!stateReview||stopped||closeReview||!dialog.open||adopting)return;
    const owned=stateReview,generation=++stateEpoch;
    owned.cancel();clearStatePreview();stateBusy=true;previewStateButton.disabled=true;structuresButton.disabled=true;cancelStateButton.hidden=false;
    stateStatus.textContent='Reading supplied state structures.';
    try{
      if(selected.size>MAX_MODEL_BYTES)throw new Error('Choose a state structure file no larger than 2 MiB.');
      const bytes=await selected.arrayBuffer();
      if(generation!==stateEpoch||stateReview!==owned||!dialog.open||stopped)return;
      if(bytes.byteLength>MAX_MODEL_BYTES)throw new Error('The state structure file exceeds 2 MiB.');
      await owned.loadText(new TextDecoder('utf-8',{fatal:true}).decode(bytes),{name:selected.name});
      if(generation!==stateEpoch||stateReview!==owned||!dialog.open||stopped)return;
      find('population-structures-name').textContent=selected.name;
      await inspectSelected();
    }catch(error){if(generation===stateEpoch&&stateReview===owned&&!stopped)stateStatus.textContent=error.message;}
    finally{if(stateReview===owned){
      if(generation===stateEpoch){stateBusy=false;cancelStateButton.hidden=true;}
      structuresButton.disabled=adopting;structuresFile.value='';if(!stateBusy)cancelStateButton.hidden=true;refreshStateSummary();
    }}
  }
  async function adoptState(){
    if(!chart||!stateReview||adoptButton.disabled||stateBusy||adopting||stopped||closeReview)return;
    const owned=stateReview,index=chart.getSnapshot().selection.index;
    adopting=true;adoptButton.disabled=true;previewStateButton.disabled=true;chooseButton.disabled=true;structuresButton.disabled=true;refreshExports();
    stateStatus.textContent='Applying the reviewed 2D state.';
    try{
      await owned.adopt({index,confirmed:true});
      // Its own document notification clears the calculation, but must not
      // cancel the already-committed editor transaction while it resolves.
      close();
    }catch(error){
      if(stateReview===owned&&!stopped){
        if(!analysis.result)cancel('The drawing changed. Reopen the model before using another state.');
        else{clearStatePreview();stateStatus.textContent=error.message;}
      }
    }finally{adopting=false;chooseButton.disabled=false;structuresButton.disabled=!chart||stopped||closeReview;refreshExports();refreshStateSummary();}
  }
  async function exportPopulation(kind) {
    if(!chart||!analysis.result||exporting||stopped||closeReview||adopting||referenceError)return;
    const owned=chart,result=analysis.result,snapshot=owned.getSnapshot(),operation={controller:new AbortController()};
    exporting=operation;refreshExports();chooseButton.disabled=true;
    exportStatus.textContent='Preparing population export.';
    try {
      const options={source:{name:result.name,modelInputHash:result.modelInputHash},index:snapshot.selection.index,
        visibleStateIds:snapshot.visibleStateIds,reference:snapshot.reference.schemaVersion===1?snapshot.reference:null};
      const transparentBackground=find('population-transparent-background')?.checked??false;
      const blob=kind==='png'?await owned.exportPng({transparentBackground}):new Blob([
        kind==='json'?serializePopulationJson(result.envelope,options):populationCsv(result.envelope,options)
      ],{type:kind==='json'?'application/json':'text/csv;charset=utf-8'});
      if(stopped||chart!==owned||exporting!==operation||!dialog.open)return;
      const outcome=await savePopulationExport({kind,blob,...(kind==='png'?{transparentBackground}:{}),signal:operation.controller.signal,token,
        native:view.pywebview?(view.pywebview.api??{}):null});
      if(stopped||chart!==owned||exporting!==operation||!dialog.open)return;
      const messages={saved:`Population ${kind.toUpperCase()} saved and verified.`,
        prepared:`Population ${kind.toUpperCase()} prepared. Complete the save/download dialog.`,
        cancelled:'Population export cancelled. The chart is unchanged.',
        conflict:'The destination changed. Choose a different filename to preserve it.',
        unsupported:'Protected replacement is unavailable. Choose a new filename on a local drive.',
        failed:'The population export could not be saved. Keep the chart open and try another destination.',
        unconfirmed:'Saving could not be confirmed. Inspect the destination before trying again.'};
      exportStatus.textContent=messages[outcome.status];
    }catch(error){if(!stopped&&chart===owned&&exporting===operation)exportStatus.textContent=error.message;}
    finally{if(exporting===operation){exporting=null;chooseButton.disabled=false;refreshExports();}}
  }
  function close() {cancel('Open a complete supplied model to calculate its pH curves.');if(dialog.open)dialog.close();}
  async function readFile() {
    const selected=file.files?.[0];
    if(!selected||stopped||closeReview||!dialog.open||adopting)return;
    cancel('Reading supplied model.');
    const generation=++epoch;
    chooseButton.disabled=true;cancelButton.hidden=false;fileName.textContent=selected.name;
    try {
      if(selected.size>MAX_MODEL_BYTES)throw new Error('Choose a model JSON file no larger than 2 MiB.');
      const bytes=await selected.arrayBuffer();
      if(generation!==epoch||stopped||!dialog.open)return;
      if(bytes.byteLength>MAX_MODEL_BYTES)throw new Error('The model file exceeds 2 MiB.');
      const text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);
      status.textContent='Calculating locally from the supplied model.';
      const result=await analysis.loadText(text,{name:selected.name});
      if(generation!==epoch||stopped||!dialog.open)return;
      render(result);
      status.textContent='Supplied-model calculation ready. Model validity and correspondence to the drawing have not been verified.';
    } catch(error) {
      if(generation===epoch&&!stopped){analysis.cancel();releaseChart();status.textContent=error.name==='TimeoutError'?'The calculation timed out. Your drawing is unchanged.':error.message;}
    } finally {
      if(generation===epoch){chooseButton.disabled=false;cancelButton.hidden=true;file.value='';}
    }
  }
  listen(button,'click',()=>{
    if(stopped||closeReview||dialog.open)return;
    status.textContent='Choose a complete model JSON file. Calculations stay on this computer.';
    dialog.showModal();chooseButton.focus();
  });
  listen(chooseButton,'click',()=>{if(!stopped&&!closeReview)file.click();});
  listen(file,'change',()=>{void readFile();});
  listen(structuresButton,'click',()=>{if(stateReview&&!adopting&&!stopped&&!closeReview)structuresFile.click();});
  listen(structuresFile,'change',()=>{void readStructures();});
  listen(previewStateButton,'click',()=>{void inspectSelected();});
  listen(adoptButton,'click',()=>{void adoptState();});
  listen(cancelStateButton,'click',()=>{stateEpoch++;stateReview?.cancel();stateBusy=false;structuresButton.disabled=!chart;cancelStateButton.hidden=true;clearStatePreview();refreshStateSummary();stateStatus.textContent='State preview cancelled. The drawing is unchanged.';});
  for(const kind of ['json','csv','png'])listen(find(`population-export-${kind}`),'click',()=>{void exportPopulation(kind);});
  listen(cancelButton,'click',()=>cancel());
  listen(find('close-population-model'),'click',close);
  listen(dialog,'cancel',event=>{event.preventDefault();close();});
  listen(dialog,'close',()=>{if(!dialog.open)cancel('Open a complete supplied model to calculate its pH curves.');});
  listen(contextSelect,'change',()=>{
    if(!explorer||stopped||closeReview||adopting||exporting||!dialog.open){contextSelect.value=explorer?.getSnapshot().contextId??'';return;}
    try{const context=explorer.selectContext(contextSelect.value||null);if(referenceError)syncReference(context);}
    catch(error){contextSelect.value=explorer.getSnapshot().contextId??'';if(!referenceError)contextStatus.textContent=error.message;}
  });
  listen(view,'nivram-close-review',()=>{closeReview=true;close();button.disabled=true;});
  listen(view,'nivram-close-review-ended',()=>{closeReview=false;button.disabled=stopped;refreshReferenceControls();});
  const api=Object.freeze({
    setReferences({dataset:accepted,explorer:source}) {
      if(stopped)return;
      const nextDataset=structuredClone(accepted);
      let context=source.getSnapshot(),active=false;
      referenceView(nextDataset,context.contextId,context.comparison);
      const none=dom.createElement('option');none.value='';none.textContent='No reference context';
      const options=[none];
      for(const preset of nextDataset.presets){const option=dom.createElement('option');option.value=preset.id;option.textContent=preset.label;options.push(option);}
      // Subscribe before replacing the working binding. Initial publication may
      // fail, so it validates/stages only and cannot change the chart or controls.
      const stop=source.subscribe(next=>{
        try{referenceView(nextDataset,next.contextId,next.comparison);}
        catch(error){if(active&&!stopped)referenceFailed(error);throw error;}
        context=next;
        if(!active||stopped)return;
        syncReference(context);
      });
      if(typeof stop!=='function')throw new TypeError('The reference subscription must provide a disposer.');
      try{chart?.setReferenceDataset(nextDataset,context);}catch(error){stop();referenceFailed(error);throw error;}
      unsubscribe();dataset=nextDataset;explorer=source;active=true;
      unsubscribe=()=>{active=false;stop();};
      contextSelect.replaceChildren(...options);contextSelect.value=context.contextId??'';
      referenceError=null;
      contextStatus.textContent='Reference context is shared with the editor controls. Existing session comparisons are retained.';
      refreshExports();
    },
    destroy(){if(stopped)return;stopped=true;close();analysis.dispose();unsubscribe();for(const remove of removers.splice(0))remove();button.disabled=true;}
  });
  button.disabled=closeReview;
  refreshReferenceControls();
  listen(view,'pagehide',()=>api.destroy());
  return api;
}
