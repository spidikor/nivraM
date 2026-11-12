import {mountReferenceChart} from './population-chart.mjs';
import {createPhSelection, validateCapabilities, serializeReferenceJson, referenceCsv} from './population-view.mjs';
import {saveReferenceExport} from './reference-save.mjs';
import {referenceExportTrace} from './export-activation.mjs';

/** Connects session-only reference controls. It has no molecular document access. */
export function mountPhReferenceDialog({dataset, explorer, dom=document, fetcher=globalThis.fetch, initialCloseReview=false}={}) {
  const view=dom.defaultView, find=id=>dom.getElementById(id);
  const button=find('open-ph-chart'), dialog=find('ph-reference-dialog'), host=find('ph-chart-host');
  const status=find('ph-chart-status'), exportStatus=find('ph-export-status');
  const exportButtons=['png','csv','json'].map(kind=>find(`ph-export-${kind}`));
  const selection=createPhSelection(), removers=[];
  let capabilities=null, loading=null, request=null, chart=null, unsubscribe=()=>{};
  let stopped=false, closeReview=initialCloseReview, exporting=null;
  const theme=()=>dom.documentElement.dataset.theme==='dark'?'dark':'light';
  const observer=new view.MutationObserver(()=>{if(chart)chart.setTheme(theme());});
  const listen=(target,event,callback)=>{
    target.addEventListener(event,callback);
    removers.push(()=>target.removeEventListener(event,callback));
  };
  function refreshButton() {button.disabled=stopped||closeReview||loading!==null;}
  function releaseChart() {
    exporting?.controller.abort();
    exporting=null;
    observer.disconnect();unsubscribe();unsubscribe=()=>{};
    const owned=chart;chart=null;owned?.destroy();
    for(const control of exportButtons)control.disabled=true;
  }
  function close() {
    releaseChart();
    if(dialog.open)dialog.close();
  }
  function loadCapabilities() {
    if(stopped)return Promise.resolve(false);
    if(capabilities)return Promise.resolve(true);
    if(loading)return loading;
    const controller=new AbortController();request=controller;
    const timer=setTimeout(()=>controller.abort(new Error('The local capability check timed out.')),10000);
    status.textContent='Checking local pH reference availability.';
    loading=(async()=>{
      try {
        const response=await fetcher('/api/science-capabilities',{signal:controller.signal});
        if(!response.ok)throw new Error('The local scientific capability check is unavailable.');
        const accepted=validateCapabilities(await response.json());
        if(stopped)return false;
        capabilities=accepted;
        button.textContent='Open pH reference chart';
        status.textContent='Reference chart available. Molecular population predictions remain unavailable.';
        return true;
      } catch(error) {
        if(!stopped){
          button.textContent='Retry pH reference chart';
          status.textContent=`${error.message} The textual physiology references remain available.`;
        }
        return false;
      } finally {
        clearTimeout(timer);
        if(request===controller)request=null;
        loading=null;refreshButton();
      }
    })();
    refreshButton();return loading;
  }
  async function open() {
    if(stopped||closeReview||dialog.open)return;
    if(!await loadCapabilities()||stopped||closeReview||dialog.open)return;
    try {
      exportStatus.textContent='Reference values and unverified comparisons only. PNG exports use a white background unless transparent background is selected.';
      dialog.showModal();
      const current=explorer.getSnapshot();
      chart=mountReferenceChart(host,{Chart:view.Chart,dataset,capabilities,selection,
        contextId:current.contextId,comparison:current.comparison,theme:theme()});
      unsubscribe=explorer.subscribe(accepted=>{
        if(chart)chart.updateContext({contextId:accepted.contextId,comparison:accepted.comparison});
      });
      observer.observe(dom.documentElement,{attributes:true,attributeFilter:['data-theme']});
      for(const control of exportButtons)control.disabled=false;
      host.querySelector('canvas').focus({preventScroll:true});
    } catch(error) {
      close();status.textContent=`The pH chart could not open: ${error.message} Textual references remain available.`;
    }
  }
  async function exportReference(kind,event) {
    if(!chart||exporting||stopped||closeReview)return;
    const beforeDispatch=referenceExportTrace.begin(kind,event);
    const owned=chart, operation={controller:new AbortController()}, snapshot=owned.getSnapshot();
    exporting=operation;
    for(const control of exportButtons)control.disabled=true;
    exportStatus.textContent='Preparing reference export.';
    try {
      const options={index:snapshot.selection.index};
      const transparentBackground=find('ph-transparent-background')?.checked??false;
      const blob=kind==='png'?await owned.exportPng({transparentBackground}):new Blob([
        kind==='json'?serializeReferenceJson(snapshot.reference,options):referenceCsv(snapshot.reference,options)
      ],{type:kind==='json'?'application/json':'text/csv;charset=utf-8'});
      if(stopped||chart!==owned||exporting!==operation||!dialog.open)return;
      const outcome=await saveReferenceExport({kind,blob,...(kind==='png'?{transparentBackground}:{}),beforeDispatch,signal:operation.controller.signal,
        native:view.pywebview?(view.pywebview.api??{}):null,
        token:dom.querySelector('meta[name="nivram-token"]')?.content??''});
      if(stopped||chart!==owned||exporting!==operation||!dialog.open)return;
      const messages={
        saved:`Reference ${kind.toUpperCase()} saved and verified. This export contains no molecular population results.`,
        prepared:`Reference ${kind.toUpperCase()} prepared. Complete the save/download dialog. This export contains no molecular population results.`,
        cancelled:'Reference export cancelled. The chart is unchanged.',
        conflict:'The destination changed or another file appeared. Choose a different filename to preserve it.',
        unsupported:'This destination does not support protected replacement. Choose a new filename.',
        failed:'The reference export could not be saved. Keep the chart open and choose another destination.',
        unconfirmed:'Reference save could not be confirmed. Inspect the chosen destination before trying again.',
      };
      exportStatus.textContent=messages[outcome.status];
    } catch(error) {
      if(!stopped&&chart===owned&&exporting===operation)exportStatus.textContent=error.message;
    } finally {
      if(exporting===operation){
        exporting=null;
        for(const control of exportButtons)control.disabled=!chart||stopped||closeReview;
      }
    }
  }
  listen(button,'click',open);
  listen(find('close-ph-chart'),'click',close);
  listen(dialog,'cancel',event=>{event.preventDefault();close();});
  listen(dialog,'close',()=>{if(!dialog.open)releaseChart();});
  listen(find('ph-change-context'),'click',()=>{
    close();const select=find('physiology-context');select.closest('details').open=true;select.focus();
  });
  for(const kind of ['png','csv','json'])listen(find(`ph-export-${kind}`),'click',event=>exportReference(kind,event));
  listen(view,'nivram-close-review',()=>{closeReview=true;close();refreshButton();});
  listen(view,'nivram-close-review-ended',()=>{closeReview=false;refreshButton();});
  const api=Object.freeze({destroy(){
    if(stopped)return;
    stopped=true;request?.abort();close();
    for(const remove of removers.splice(0))remove();
    refreshButton();
  }});
  listen(view,'pagehide',()=>api.destroy());
  void loadCapabilities();
  return api;
}
