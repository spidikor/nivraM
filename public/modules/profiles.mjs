import {cloneData,validateProject,sanitizeSvg} from './document.mjs';
import {exportPng,downloadBlob} from './export.mjs';

export function projectWithProfile(document,profile){
  if(profile?.schema!=='nivram-profile/1'||!Number.isInteger(profile.revision)||profile.revision<1||
      !/^[0-9a-f]{32}$/.test(profile.id)||typeof profile.name!=='string')throw new Error('Unsupported rendering profile.');
  const candidate=validateProject({...cloneData(document),style:cloneData(profile.style),export:cloneData(profile.export)});
  if(JSON.stringify(candidate.graph)!==JSON.stringify(document.graph))throw new Error('A profile cannot change molecular geometry or chemistry.');
  return candidate;
}

function element(tag,text,properties={}){
  const node=document.createElement(tag);if(text!==undefined)node.textContent=text;
  Object.assign(node,properties);return node;
}

export async function initializeProfiles(editor,native){
  const panel=document.querySelector('.appearance-panel');
  if(!panel||panel.querySelector('.profile-controls')||typeof native.list_profiles!=='function')return;
  const details=element('details',undefined,{className:'profile-controls'});
  details.append(element('summary','Drawing and export profiles'));
  const name=element('input',undefined,{id:'profile-name',maxLength:80,value:'My profile'});
  const choices=element('select',undefined,{id:'profile-choice'});
  const save=element('button','Save new profile',{id:'save-profile-button',type:'button'});
  const revision=element('button','Save next revision',{type:'button',disabled:true});
  const apply=element('button','Apply selected profile',{id:'apply-profile-button',type:'button',disabled:true});
  const preview=element('button','Preview exact PNG',{id:'preview-profile-button',type:'button'});
  const removeMetadata=element('input',undefined,{id:'preview-remove-chemistry-metadata',type:'checkbox',checked:false,autocomplete:'off'});
  const metadataLabel=element('label',undefined,{className:'check-label'});metadataLabel.append(removeMetadata,document.createTextNode(' Remove chemistry metadata?'));
  const status=element('p','Profiles change drawing appearance and export settings; molecular coordinates stay fixed.');
  status.setAttribute('role','status');
  const nameLabel=element('label','Profile name');nameLabel.htmlFor=name.id;
  const choiceLabel=element('label','Saved profile revision');choiceLabel.htmlFor=choices.id;
  details.append(nameLabel,name,choiceLabel,choices,save,revision,apply,metadataLabel,preview,status);panel.append(details);
  let profiles=[],pending=null,previewUrl=null;
  const selected=()=>profiles.find(p=>`${p.id}/${p.revision}`===choices.value);
  const dialog=element('dialog',undefined,{id:'png-preview-dialog'});
  dialog.style.width='min(90vw,1100px)';
  const heading=element('h2','PNG export preview',{id:'png-preview-title'});
  dialog.setAttribute('aria-labelledby',heading.id);
  const info=element('p','');
  const viewport=element('div',undefined,{id:'png-preview-viewport',tabIndex:0});Object.assign(viewport.style,{overflow:'auto',maxHeight:'65vh',background:'#ddd'});
  viewport.setAttribute('role','region');viewport.setAttribute('aria-label','PNG preview; scroll to inspect the image');
  const image=element('img',undefined,{id:'png-preview-image',alt:'Exact rendered PNG preview'});
  Object.assign(image.style,{display:'block',maxWidth:'none',maxHeight:'none'});
  const fit=element('button','Fit view',{type:'button'});
  const pixels=element('button','One image pixel per screen pixel',{type:'button'});
  const download=element('button','Save this PNG',{type:'button',disabled:true});
  const close=element('button','Close / cancel preview',{type:'button'});
  viewport.append(image);dialog.append(heading,info,viewport,fit,pixels,download,close);document.body.append(dialog);
  let rendered=null;
  function clearPreview(){
    pending?.abort();pending=null;rendered=null;download.disabled=true;
    image.removeAttribute('src');if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=null;
  }
  removeMetadata.onchange=()=>{clearPreview();if(dialog.open)info.textContent='Metadata choice changed. Close and generate a fresh preview.';};
  close.onclick=()=>dialog.close();
  dialog.addEventListener('close',clearPreview);
  dialog.addEventListener('cancel',clearPreview);
  window.addEventListener('nivram-close-review',()=>{clearPreview();if(dialog.open)dialog.close();});
  fit.onclick=()=>{image.style.width='100%';image.style.height='auto';};
  pixels.onclick=()=>{if(rendered){image.style.width=`${rendered.width/(window.devicePixelRatio||1)}px`;image.style.height='auto';}};
  download.onclick=()=>{if(rendered)downloadBlob(rendered.blob,'structure.png');};
  async function reload(choose){
    const listing=await native.list_profiles();profiles=listing.profiles;
    choices.replaceChildren(element('option','Current drawing settings',{value:''}));
    for(const profile of profiles)choices.append(element('option',`${profile.name} · revision ${profile.revision}`,{value:`${profile.id}/${profile.revision}`}));
    if(choose)choices.value=`${choose.id}/${choose.revision}`;
    apply.disabled=revision.disabled=!selected();
    if(listing.invalidRecords)status.textContent=`${listing.invalidRecords} damaged profile record(s) were retained. Saving is disabled by the local store until repaired.`;
  }
  choices.onchange=()=>{apply.disabled=revision.disabled=!selected();if(selected())name.value=selected().name;clearPreview();};
  async function saveProfile(update){
    const snapshot=editor.getSnapshot(),prior=update?selected():null;
    if(update&&!prior)return;
    save.disabled=revision.disabled=true;
    try{
      const record=await native.save_profile(name.value,snapshot.document.style,snapshot.document.export,prior?.id??null,prior?.revision??null);
      await reload(record);
      status.textContent=`Saved ${record.name}, revision ${record.revision}. Previous revisions remain available.`;
    }catch(error){status.textContent=`Profile not saved: ${error.message??error}`;}
    finally{save.disabled=false;revision.disabled=!selected();}
  }
  save.onclick=()=>saveProfile(false);revision.onclick=()=>saveProfile(true);
  apply.onclick=async()=>{
    const profile=selected();if(!profile)return;
    const snapshot=editor.getSnapshot();apply.disabled=true;
    try{
      const candidate=projectWithProfile(snapshot.document,profile);
      const accepted=await editor.openProject(candidate,{expectedRevision:snapshot.revision});
      if(accepted===false)throw new Error('The drawing changed; apply the profile again.');
      status.textContent=`Applied ${profile.name}, revision ${profile.revision}. Undo restores the preceding appearance.`;
    }catch(error){status.textContent=`Profile not applied: ${error.message??error}`;}
    finally{apply.disabled=!selected();}
  };
  preview.onclick=async()=>{
    clearPreview();const controller=new AbortController();pending=controller;
    const snapshot=editor.getSnapshot(),removeChemistryMetadata=removeMetadata.checked;let stage=null;
    dialog.showModal();info.textContent='Rendering the exact export dimensions…';
    try{
      const candidate=selected()?projectWithProfile(snapshot.document,selected()):cloneData(snapshot.document);
      const response=await fetch('/api/render',{method:'POST',signal:controller.signal,
        headers:{'Content-Type':'application/json','X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},
        body:JSON.stringify({graph:candidate.graph,style:candidate.style})});
      const result=await response.json();if(!response.ok)throw new Error(result.error??'Rendering failed.');
      if(editor.getSnapshot().revision!==snapshot.revision)throw new Error('The drawing changed during preview.');
      stage=document.createElementNS('http://www.w3.org/2000/svg','svg');
      stage.setAttribute('width','1000');stage.setAttribute('height','1000');
      Object.assign(stage.style,{position:'fixed',left:'-100000px',visibility:'hidden'});
      const group=document.createElementNS(stage.namespaceURI,'g');group.append(sanitizeSvg(result.scene.svg,document,candidate.style));stage.append(group);document.body.append(stage);
      const output=await exportPng(group,candidate.export,{signal:controller.signal,graph:candidate.graph,removeChemistryMetadata});
      if(controller.signal.aborted||editor.getSnapshot().revision!==snapshot.revision)throw new Error('The preview is no longer current.');
      rendered=output;previewUrl=URL.createObjectURL(output.blob);image.src=previewUrl;
      await image.decode();if(controller.signal.aborted)throw new Error('Preview cancelled.');
      pixels.click();download.disabled=false;
      info.textContent=`${output.width} × ${output.height} pixels · ${output.padding} px margin per side · ${output.transparentBackground?'transparent background':'opaque white'}. ${output.removeChemistryMetadata?'Chemistry metadata removed.':'Recoverable chemistry metadata retained.'} The saved PNG uses these exact bytes. Display scaling affects only this view.`;
    }catch(error){if(!controller.signal.aborted)info.textContent=`Preview unavailable: ${error.message??error}`;}
    finally{stage?.remove();if(pending===controller)pending=null;}
  };
  let lastRevision=editor.getSnapshot().revision;
  editor.subscribe(snapshot=>{if(snapshot.revision!==lastRevision){lastRevision=snapshot.revision;clearPreview();if(dialog.open)info.textContent='The drawing changed. Close and generate a fresh preview.';}});
  try{await reload();}catch(error){status.textContent=`Profiles unavailable: ${error.message??error}`;}
}
