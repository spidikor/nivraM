import {extractFragment,validateFragmentRecord} from './fragments.mjs';
/** UI owns only shelf state. All drawing mutations remain editor transactions. */
export function initializeFragmentShelf({document,getSnapshot,getSelection,isBusy,insert,duplicate,native=()=>globalThis.pywebview?.api}={}){
  const $=id=>document.getElementById(id),status=$('fragment-status');
  let records=[],pending=false,closed=false,epoch=0;
  const message=text=>{status.textContent=text;};
  const supported=()=>typeof native()?.list_fragments==='function'&&typeof native()?.save_fragment==='function';
  function refresh(){
    const blocked=pending||closed||isBusy();
    $('fragment-save').disabled=blocked||!supported();$('fragment-reload').disabled=blocked||!supported();
    $('fragment-insert').disabled=blocked||!records.length;$('fragment-duplicate').disabled=blocked;
    $('fragment-name').disabled=blocked;$('fragment-choice').disabled=blocked||!records.length;
  }
  function populate(listing){
    if(!listing||!Array.isArray(listing.fragments)||listing.fragments.length>128||!Number.isInteger(listing.invalidRecords)||listing.invalidRecords<0)throw new Error('Invalid local fragment listing.');
    const next=listing.fragments.map(validateFragmentRecord);
    if(new Set(next.map(r=>r.id)).size!==next.length)throw new Error('Repeated local fragment identifiers.');
    records=next;$('fragment-choice').replaceChildren();
    for(const record of records){const option=document.createElement('option');option.value=record.id;option.textContent=record.name;$('fragment-choice').append(option);}
    message(listing.invalidRecords?'Some local records could not be read. They were retained. Valid records remain available.':records.length?`${records.length} saved local fragment${records.length===1?'':'s'}.`:'No saved fragments yet.');
  }
  async function reload(){
    if(pending||closed||isBusy())return;
    if(!supported()){message('Saving a fragment shelf requires the desktop app. Duplicate works in this editor.');refresh();return;}
    pending=true;const current=++epoch;refresh();
    try{const listing=await native().list_fragments();if(current===epoch&&!closed)populate(listing);}
    catch{if(current===epoch&&!closed)message('The local fragment shelf could not be read. Existing records are retained.');}
    finally{pending=false;refresh();}
  }
  async function save(){
    if(pending||closed||isBusy()||!supported())return;
    let graph,name;
    try{graph=extractFragment(getSnapshot().document.graph,getSelection());name=$('fragment-name').value.trim();if(!name||name.length>80||/[\x00-\x1f\x7f]/.test(name))throw new Error('Use a fragment name of 1–80 printable characters.');}
    catch(error){message(error.message);return;}
    pending=true;const current=++epoch;refresh();message('Saving a local copy. The drawing is unchanged.');
    try{
      const result=await native().save_fragment(name,graph);
      if(current!==epoch||closed)return;
      if(result?.status==='saved'){
        const record=validateFragmentRecord(result.fragment);
        // Acknowledgement must refer to exactly the captured user request.
        if(record.name!==name||JSON.stringify(record.graph)!==JSON.stringify(graph))throw new Error('Unconfirmed fragment save.');
        const next=[...records.filter(r=>r.id!==record.id),record];populate({fragments:next,invalidRecords:0});$('fragment-choice').value=record.id;
        message('Fragment saved locally and verified. The drawing and project save destination are unchanged.');
      }else message(result?.status==='failed'?'The fragment was not saved. Existing records are unchanged.':'Saving could not be confirmed. Reload the shelf before trying again; the fragment may already exist.');
    }catch{if(current===epoch&&!closed)message('Saving could not be confirmed. Reload the shelf before trying again; the fragment may already exist.');}
    finally{pending=false;refresh();}
  }
  async function place(useSelection){
    if(pending||closed||isBusy())return;
    try{
      const source=getSnapshot();
      const fragment=useSelection?extractFragment(source.document.graph,getSelection()):records.find(r=>r.id===$('fragment-choice').value)?.graph;
      if(!fragment)throw new Error('Choose a saved fragment first.');
      const accepted=await (useSelection?duplicate:insert)(fragment,source);
      if(accepted&&!closed)message('Inserted a disconnected copy to the right. Atom-map labels are retained. Undo restores the preceding drawing.');
    }catch(error){if(!closed)message(error.message);}
    refresh();
  }
  $('fragment-save').addEventListener('click',save);$('fragment-reload').addEventListener('click',reload);
  $('fragment-insert').addEventListener('click',()=>place(false));$('fragment-duplicate').addEventListener('click',()=>place(true));
  refresh();return Object.freeze({refresh,reload,beginClose(){closed=true;epoch++;refresh();},endClose(){closed=false;refresh();message('Reload the shelf to check any save that was pending while closing was reviewed.');}});
}
