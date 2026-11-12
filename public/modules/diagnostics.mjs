import {referenceExportDiagnostics} from './export-activation.mjs';

export function initializeDiagnostics(native){
  const host=document.querySelector('#about-dialog');
  if(!host||typeof native.diagnostics!=='function'||document.getElementById('diagnostics-button'))return;
  const button=document.createElement('button');button.id='diagnostics-button';button.type='button';button.textContent='Inspect local diagnostics';host.append(button);
  const dialog=document.createElement('dialog');dialog.id='diagnostics-dialog';dialog.style.width='min(85vw,900px)';
  const heading=document.createElement('h2');heading.id='diagnostics-title';heading.textContent='Local diagnostics';
  dialog.setAttribute('aria-labelledby',heading.id);
  const explanation=document.createElement('p');explanation.textContent='Review this report before saving. It contains application/runtime versions and configuration; it excludes molecular drawings, personal file paths and credentials. Nothing is uploaded.';
  const contents=document.createElement('pre');contents.id='diagnostics-report';contents.tabIndex=0;contents.setAttribute('role','region');contents.setAttribute('aria-label','Reviewed local diagnostic report');Object.assign(contents.style,{maxHeight:'55vh',overflow:'auto',whiteSpace:'pre-wrap',overflowWrap:'anywhere'});
  const status=document.createElement('p');status.setAttribute('role','status');
  const save=document.createElement('button');save.type='button';save.textContent='Save reviewed report';save.disabled=true;
  const close=document.createElement('button');close.type='button';close.textContent='Close';close.onclick=()=>dialog.close();
  dialog.append(heading,explanation,contents,status,save,close);document.body.append(dialog);
  let reportId=null,generation=0;
  dialog.addEventListener('close',()=>{generation++;reportId=null;save.disabled=true;});
  button.onclick=async()=>{
    const current=++generation;reportId=null;save.disabled=true;contents.textContent='Loading local metadata…';status.textContent='';dialog.showModal();
    try{
      const result=await native.diagnostics(referenceExportDiagnostics());if(current!==generation)return;
      reportId=result.reportId;contents.textContent=JSON.stringify(result.data,null,2);save.disabled=false;
    }catch{if(current===generation)contents.textContent='Local diagnostics could not be read.';}
  };
  save.onclick=async()=>{
    if(!reportId)return;save.disabled=true;
    try{const result=await native.save_diagnostics(reportId);status.textContent=result.status==='saved'?'The exact reviewed report was saved locally.':'Save cancelled.';}
    catch{status.textContent='The report could not be saved. Reopen diagnostics before trying again.';}
    finally{save.disabled=!reportId;}
  };
  window.addEventListener('nivram-close-review',()=>{if(dialog.open)dialog.close();});
}
