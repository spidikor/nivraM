import {createJobController} from './jobs.mjs';
import {mountPhysiologyExplorer} from './physiology.mjs';
import {mountPhReferenceDialog} from './ph-reference.mjs';
import {mountPopulationDialog} from './population-dialog.mjs';

const $ = id => document.getElementById(id);
let started = false;
let clientId=null;
const format = (value, digits = 4) => Number.isFinite(value) ? value.toFixed(digits) : 'Unavailable';

function randomId(){
  const bytes=new Uint8Array(16);globalThis.crypto.getRandomValues(bytes);
  return [...bytes].map(value=>value.toString(16).padStart(2,'0')).join('');
}

/** Cancellation uses a new request, not the calculation's aborted signal. */
export async function requestAnalysis(action,task,{token,fetcher=globalThis.fetch}={}){
  const identity=action==='name'?{clientId:clientId??=randomId(),jobId:randomId(),expiresAt:Date.now()+30000}:null;
  let cancellationSent=false;
  const cancel=()=>{
    if(!identity||cancellationSent)return;
    cancellationSent=true;
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),3000);
    // The request is tiny enough for keepalive. An unconfirmed transport error
    // is not promoted to proof that the native child already exited.
    Promise.resolve().then(()=>fetcher('/api/cancel-job',{method:'POST',signal:controller.signal,keepalive:true,
      headers:{'Content-Type':'application/json','X-Nivram-Token':token},body:JSON.stringify(identity)}))
      .catch(()=>{}).finally(()=>clearTimeout(timer));
  };
  if(identity)task.signal.addEventListener('abort',cancel,{once:true});
  try{
    if(task.signal.aborted){cancel();throw task.signal.reason;}
    const response=await fetcher(`/api/${action}`,{method:'POST',signal:task.signal,
      headers:{'Content-Type':'application/json','X-Nivram-Token':token},
      body:JSON.stringify({graph:task.input.graph,documentRevision:task.revision,...identity})});
    const result=await response.json();
    if(!response.ok)throw new Error(result.error||'The local calculation failed.');
    task.assertCurrent();
    if(identity&&(result.clientId!==identity.clientId||result.jobId!==identity.jobId))throw new Error('The naming response does not match this job.');
    if(result.inputIdentity?.documentRevision!==task.revision)throw new Error('The result does not match this document revision.');
    return result;
  }catch(error){
    // Transport/validation failure also abandons this computation. Its owned
    // worker must not consume capacity solely because the fetch ended early.
    cancel();throw error;
  }finally{if(identity)task.signal.removeEventListener('abort',cancel);}
}

async function start() {
  if (started || !window.nivramDocument) return;
  started = true;
  const editor = window.nivramDocument;
  await editor.ready;
  const token = document.querySelector('meta[name="nivram-token"]').content;
  const jobs = createJobController(() => editor.getSnapshot().revision);
  let timer, namingId = null, propertiesId = null, closeReview=false,lastRevision = editor.getSnapshot().revision;
  const request=(action,task)=>requestAnalysis(action,task,{token});
  async function properties() {
    if(closeReview)return;
    const task = jobs.begin('properties', {input:editor.getSnapshot().document});
    propertiesId = task.id;
    try {
      const result = await task.wait(request('properties', task));
      task.assertCurrent();
      $('property-formula').textContent = result.formula || 'No structure';
      $('property-mass').textContent = `${format(result.molecularWeight.value)} ${result.molecularWeight.unit}`;
      $('property-exact-mass').textContent = `${format(result.exactMass.value, 6)} ${result.exactMass.unit}`;
      $('property-logp').textContent = `${format(result.logP.value, 3)}${result.logP.status === 'calculated_with_warning' ? ' · applicability warning' : ''}`;
      $('property-smiles').value = result.smiles;
      $('property-provenance').textContent = `${result.engine.name} ${result.engine.version}; exact as-drawn structure. ${result.warnings.join(' ')}`;
    } catch (error) {
      if (propertiesId === task.id && editor.getSnapshot().revision === task.revision) {
        if (error.name === 'TimeoutError') {
          for (const id of ['property-formula','property-mass','property-exact-mass','property-logp']) $(id).textContent = 'Timed out';
          $('property-smiles').value = '';
          $('property-provenance').textContent = 'Local property calculation timed out. The drawing is unchanged.';
        } else if (!task.signal.aborted) $('property-provenance').textContent = error.message;
      }
    } finally { task.finish(); }
  }
  async function name() {
    if(closeReview)return;
    const task = jobs.begin('naming', {input:editor.getSnapshot().document, timeout:25000});
    namingId = task.id;
    $('name-result').textContent = 'Calculating locally…';
    $('name-details').textContent = '';
    $('cancel-name').hidden = false;
    $('cancel-name').onclick = () => {
      task.cancel(); $('name-result').textContent = 'Cancelled'; $('cancel-name').hidden = true;
    };
    try {
      const result = await task.wait(request('name', task));
      task.assertCurrent();
      $('name-result').textContent = result.name || 'No accepted name';
      $('name-details').textContent = `${result.status.replaceAll('_',' ')} · ${result.engine.name} ${result.engine.version || ''}. ${result.warnings.join(' ')} ${result.roundTrip.warnings?.join(' ') || ''} A parser round trip is not a Preferred IUPAC Name claim.`;
    } catch (error) {
      if (namingId === task.id && editor.getSnapshot().revision === task.revision) {
        if (error.name === 'TimeoutError') $('name-result').textContent = 'Local naming timed out. Try again or continue editing.';
        else if (!task.signal.aborted) $('name-result').textContent = error.message;
      }
    } finally { task.finish(); if (namingId === task.id) $('cancel-name').hidden = true; }
  }
  editor.subscribe(snapshot => {
    if (snapshot.revision === lastRevision) return;
    lastRevision = snapshot.revision;
    jobs.cancelAll('The drawing changed.');
    for (const id of ['property-formula','property-mass','property-exact-mass','property-logp']) $(id).textContent = 'Updating…';
    $('property-smiles').value = '';
    $('name-result').textContent = 'Calculate a name for the current structure.';
    $('name-details').textContent = '';
    clearTimeout(timer); timer = setTimeout(properties, 350);
  });
  $('calculate-name').onclick = name;
  window.addEventListener('nivram-close-review',()=>{
    closeReview=true;clearTimeout(timer);jobs.cancelAll('The application is closing.');
    $('cancel-name').hidden=true;$('name-result').textContent='Naming paused while closing is reviewed.';
  });
  window.addEventListener('nivram-close-review-ended',()=>{
    closeReview=false;$('name-result').textContent='Calculate a name for the current structure.';
    properties();
  });
  properties();

  const populations=mountPopulationDialog({editor,token,initialCloseReview:closeReview});
  try {
    const response = await fetch('/api/physiology');
    if (!response.ok) throw new Error('The physiology reference dataset is unavailable.');
    const dataset = await response.json();
    const explorer=mountPhysiologyExplorer({dataset, select: $('physiology-context'), detail: $('physiology-detail')});
    populations.setReferences({dataset,explorer});
    const references=mountPhReferenceDialog({dataset,explorer,initialCloseReview:closeReview});
    window.addEventListener('pagehide',()=>{references.destroy();explorer.destroy();},{once:true});
  } catch (error) {
    $('physiology-detail').textContent = error.message;
    $('ph-chart-status').textContent='The pH reference chart is unavailable because its reference data could not be loaded.';
  }
}
if(typeof window!=='undefined'){
  window.addEventListener('nivram-document-ready', start);
  start();
}
