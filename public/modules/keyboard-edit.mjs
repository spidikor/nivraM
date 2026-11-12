import {editAtom,createBond,editBond,placeRing,deleteEntities,translateAtoms} from './edit-commands.mjs';

/** Native form controls provide the same document transactions as pointer edits.
 * Target inspection and an unfinished bond are transient, never project data. */
export function initializeKeyboardEditor({document:doc,getState,getSelection,setSelection,commit,makeId,isBusy,reveal,pan,inspect=()=>{}}){
  const $=id=>doc.getElementById('keyboard-'+id),host=$('editor');
  let pending=null,working=false,targetSignature=null,suspendedFocus=null;
  const say=text=>{$('status').textContent=text;};
  const target=()=>{const value=$('target').value;return value==='empty'?{kind:'empty',id:null}:{kind:value.slice(0,value.indexOf(':')),id:value.slice(value.indexOf(':')+1)};};
  const graph=()=>getState().document.graph;
  const number=id=>{const field=$(id);if(!field.value.trim()||!field.checkValidity()||!Number.isFinite(field.valueAsNumber))throw new Error('Enter a valid '+field.labels[0].textContent.trim()+'.');return field.valueAsNumber;};
  const position=()=>({x:number('x'),y:number('y')});
  function cancel(message='Unfinished bond cancelled. No atoms or bonds were added.',restoreFocus=false){
    const had=!!pending;pending=null;if(had)say(message);refresh();
    if(had&&restoreFocus&&!isBusy())$('target').focus();return had;
  }
  function refresh(){
    const state=getState(),g=state.document.graph;
    if(pending&&pending.revision!==state.revision){pending=null;say('The document changed. Start the bond again.');}
    const signature=JSON.stringify(g);
    if(signature!==targetSignature){
      const previous=$('target').value,fragment=doc.createDocumentFragment();
      const option=(value,label)=>{const node=doc.createElement('option');node.value=value;node.textContent=label;fragment.append(node);};
      option('empty','Empty position');
      const names=new Map(g.atoms.map((a,i)=>[a.id,`${i+1} ${a.symbol}`]));
      for(const a of g.atoms){const neighbors=g.bonds.filter(b=>b.begin===a.id||b.end===a.id).map(b=>names.get(b.begin===a.id?b.end:b.begin));option('atom:'+a.id,`Atom ${names.get(a.id)}${a.isotope?', isotope '+a.isotope:''}${a.charge?', charge '+a.charge:''}${neighbors.length?'; bonded to '+neighbors.join(', '):'; isolated'}`);}
      for(const [i,b] of g.bonds.entries())option('bond:'+b.id,`Bond ${i+1}: ${names.get(b.begin)} to ${names.get(b.end)}; ${b.direction===1?'solid wedge':b.direction===2?'hashed wedge':b.order===1?'single':b.order===2?'double':b.order===3?'triple':'aromatic'}`);
      $('target').replaceChildren(fragment);$('target').value=previous;if(!$('target').value)$('target').value='empty';targetSignature=signature;
    }
    const t=target(),blocked=working||isBusy(),selection=getSelection();
    if(blocked&&host.contains(doc.activeElement))suspendedFocus=doc.activeElement;
    for(const node of host.querySelectorAll('button'))node.disabled=blocked;
    for(const node of host.querySelectorAll('input,select'))node.disabled=blocked;
    $('atom').textContent=t.kind==='atom'?'Change atom element':'Add atom';
    $('atom').disabled=blocked||t.kind==='bond';
    $('edit-bond').disabled=blocked||t.kind!=='bond';$('start-bond').disabled=blocked||!!pending||t.kind==='bond';
    $('finish-bond').disabled=blocked||!pending||t.kind==='bond';$('cancel-bond').disabled=!pending;
    $('select').disabled=$('toggle-select').disabled=blocked||t.kind==='empty';
    $('endpoints').disabled=blocked||t.kind!=='bond';$('delete').disabled=blocked||(!selection.atoms.length&&!selection.bonds.length);
    $('move').disabled=blocked||!selection.atoms.length;
    $('ring-side').disabled=blocked||t.kind!=='bond';$('free-bond').disabled=blocked;
    $('x').disabled=$('y').disabled=blocked||t.kind!=='empty';
    if(!blocked&&suspendedFocus){
      if(doc.activeElement===doc.body)(suspendedFocus.disabled?$('target'):suspendedFocus).focus();
      suspendedFocus=null;
    }
    inspect(host.open?t:{kind:'empty',id:null});
  }
  async function apply(build,{clear=false}={}){
    if(working||isBusy()||doc.querySelector('dialog[open]'))return;
    const focus=doc.activeElement;
    try{
      const before=getState(),candidate=build(before.document.graph,before.revision);
      if(!candidate.changed){say(candidate.reason==='already-bonded'?'Those atoms are already bonded. Select the bond to change its type.':candidate.reason==='same-atom'?'Choose a different endpoint for the bond.':'Nothing changed.');return;}
      pending=null;working=true;refresh();
      const accepted=await commit(candidate.graph,{fit:before.document.graph.atoms.length===0,resetSelection:clear});
      if(accepted){
        const id=candidate.atomId??candidate.bondId??candidate.createdAtomIds?.[0];
        if(id){$('target').value=(candidate.atomId||candidate.createdAtomIds?.includes(id)?'atom:':'bond:')+id;}
        say('Drawing updated. Undo restores the preceding drawing.');
      }else say('The edit was not applied. The preceding drawing is retained.');
    }catch(error){say(error.message);}
    finally{
      working=false;refresh();
      if(doc.activeElement===doc.body&&host.contains(focus)){
        (focus.disabled?$('target'):focus).focus();
      }
    }
  }
  $('target').addEventListener('change',()=>{refresh();say(`${$('target').selectedOptions[0].textContent}. ${pending?'Choose Finish bond to connect this endpoint.':'Selection is unchanged.'}`);});
  $('atom').onclick=()=>apply(g=>{const t=target();return editAtom(g,{atomId:t.kind==='atom'?t.id:null,position:t.kind==='empty'?position():undefined,symbol:$('element').value,makeId});});
  $('edit-bond').onclick=()=>apply(g=>editBond(g,{bondId:target().id,kind:$('bond-type').value}));
  $('start-bond').onclick=()=>{
    if(working||isBusy()||pending)return;
    try{
      const t=target(),s=getState();if(t.kind==='bond')throw new Error('Choose an atom or an empty position.');
      const point=t.kind==='atom'?s.document.graph.atoms.find(a=>a.id===t.id):position();
      if(!point)throw new Error('The selected atom no longer exists.');
      pending={revision:s.revision,startAtomId:t.kind==='atom'?t.id:null,startPosition:{x:point.x,y:point.y}};
      $('target').value='empty';$('x').value=point.x+1.5*Math.cos(Math.PI/6);$('y').value=point.y+.75;
      say('Bond started. Choose its endpoint, then Finish bond. Cancel adds nothing.');refresh();$('target').focus();
    }catch(error){say(error.message);}
  };
  $('finish-bond').onclick=()=>apply((g,revision)=>{
    if(!pending||pending.revision!==revision)throw new Error('The document changed. Start the bond again.');
    const t=target();return createBond(g,{...pending,endAtomId:t.kind==='atom'?t.id:null,endPosition:t.kind==='empty'?position():undefined,endSymbol:$('element').value,kind:$('bond-type').value,freeAngle:$('free-bond').checked,makeId});
  });
  $('cancel-bond').onclick=()=>cancel(undefined,true);
  $('ring').onclick=()=>apply(g=>{
    const t=target();let p=t.kind==='empty'?position():{x:0,y:0};
    if(t.kind==='bond'){
      const b=g.bonds.find(b=>b.id===t.id),a=g.atoms.find(a=>a.id===b.begin),end=g.atoms.find(a=>a.id===b.end),side=$('ring-side').value==='left'?1:-1;
      p={x:(a.x+end.x)/2-side*(end.y-a.y),y:(a.y+end.y)/2+side*(end.x-a.x)};
    }
    return placeRing(g,{position:p,atomId:t.kind==='atom'?t.id:null,bondId:t.kind==='bond'?t.id:null,type:$('ring-type').value,makeId});
  });
  function select(mode){
    if(working||isBusy())return;
    const t=target(),s=getSelection();let atoms=new Set(s.atoms),bonds=new Set(s.bonds);
    if(mode==='all'){atoms=new Set(graph().atoms.map(a=>a.id));bonds.clear();}
    else if(mode==='clear'){atoms.clear();bonds.clear();}
    else if(mode==='endpoints'){const b=graph().bonds.find(b=>b.id===t.id);if(!b)return;atoms=new Set([b.begin,b.end]);bonds.clear();}
    else{
      if(t.kind==='empty')return;
      if(mode==='only'){atoms.clear();bonds.clear();}
      const set=t.kind==='atom'?atoms:bonds;if(mode==='toggle'&&set.has(t.id))set.delete(t.id);else set.add(t.id);
    }
    setSelection([...atoms],[...bonds]);say(`${atoms.size} atoms and ${bonds.size} bonds selected.`);refresh();
  }
  for(const [id,mode] of [['select','only'],['toggle-select','toggle'],['endpoints','endpoints'],['select-all','all'],['clear-selection','clear']])$(id).onclick=()=>select(mode);
  $('delete').onclick=()=>apply(g=>{const s=getSelection();return deleteEntities(g,{atomIds:s.atoms,bondIds:s.bonds});},{clear:true});
  $('move').onclick=()=>apply(g=>translateAtoms(g,{atomIds:getSelection().atoms,dx:number('dx'),dy:number('dy')}));
  $('reveal').onclick=()=>{try{reveal(target(),target().kind==='empty'?position():null);}catch(error){say(error.message);}};
  for(const [id,dx,dy] of [['left',-1,0],['right',1,0],['up',0,-1],['down',0,1]])$('pan-'+id).onclick=()=>pan(dx,dy);
  host.addEventListener('keydown',event=>{if(event.key==='Escape'&&!event.isComposing&&pending){event.preventDefault();event.stopPropagation();cancel(undefined,true);}});
  host.addEventListener('toggle',refresh);
  refresh();return {refresh,cancel};
}
