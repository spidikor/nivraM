import {cloneData} from './document.mjs';
import {editAtom,createBond,editBond,placeRing,deleteEntities,translateAtoms} from './edit-commands.mjs';
import {stereoEditNotice} from './bond-edit.mjs';
import {createImageCorrectionView} from './image-correction-view.mjs';

/** A private editor for the import candidate. The owner validates, renders and
 * records every proposed graph; this module never changes the active document.
 * Callbacks may return a prepared render or call load themselves. */
export function createImageCorrection({root,canvas,onChange,onError=()=>{},onUndo,onRedo,onReset,onReplace}){
  const doc=root.ownerDocument,ns='http://www.w3.org/2000/svg',listeners=[];
  let graph=null,scene=null,disabled=false,working=false,destroyed=false,generation=0;
  let selectedAtoms=new Set(),selectedBonds=new Set(),drag=null,pendingBond=null;
  let canUndo=false,canRedo=false;
  const view=createImageCorrectionView();
  const controls=new Map();
  const drawing=doc.createElementNS(ns,'g'),selection=doc.createElementNS(ns,'g'),gesture=doc.createElementNS(ns,'g');
  selection.setAttribute('pointer-events','none');gesture.setAttribute('pointer-events','none');
  const host=doc.createElement('div');host.className='image-correction-editor';root.append(host);
  const toolbar=doc.createElement('div');toolbar.className='image-correction-toolbar';toolbar.setAttribute('role','group');toolbar.setAttribute('aria-label','Correct imported structure');host.append(toolbar);
  const hint=doc.createElement('p');hint.className='muted image-correction-hint';host.append(hint);
  const details=doc.createElement('details'),summary=doc.createElement('summary');summary.textContent='Atom properties and keyboard editing';details.append(summary);host.append(details);
  const fields=doc.createElement('div');fields.className='image-correction-fields';details.append(fields);
  const replacement=doc.createElement('div');replacement.className='image-correction-source';replacement.hidden=typeof onReplace!=='function';host.append(replacement);
  const sourceLabel=doc.createElement('label'),structureSource=doc.createElement('textarea');sourceLabel.textContent='SMILES or 2D MOL';
  structureSource.dataset.correction='structure-source';structureSource.rows=3;structureSource.maxLength=1_000_000;structureSource.spellcheck=false;structureSource.autocomplete='off';structureSource.setAttribute('autocapitalize','off');
  sourceLabel.append(structureSource);replacement.append(sourceLabel);controls.set('structure-source',structureSource);
  const replacementHint=doc.createElement('p');replacementHint.className='muted image-correction-hint';replacementHint.textContent='Replaces the entire review draft. The open drawing stays unchanged until import.';replacement.append(replacementHint);
  const status=doc.createElement('p');status.className='image-correction-status';status.setAttribute('role','status');status.setAttribute('aria-live','polite');host.append(status);
  const listen=(target,type,handler,options)=>{target.addEventListener(type,handler,options);listeners.push(()=>target.removeEventListener(type,handler,options));};
  function field(parent,key,label,type,options=[]){
    const wrapper=doc.createElement('label'),text=doc.createElement('span'),input=doc.createElement(type==='select'?'select':'input');
    text.textContent=label;input.dataset.correction=key;
    if(type==='select')for(const [value,name] of options){const option=doc.createElement('option');option.value=value;option.textContent=name;input.append(option);}
    else input.type=type;
    wrapper.append(text,input);parent.append(wrapper);controls.set(key,input);return input;
  }
  function button(parent,key,label,action){
    const node=doc.createElement('button');node.type='button';node.textContent=label;node.dataset.correction=key;
    controls.set(key,node);parent.append(node);listen(node,'click',action);return node;
  }
  const navigation=doc.createElement('div');navigation.className='image-correction-navigation';navigation.setAttribute('role','group');navigation.setAttribute('aria-label','Draft view');host.prepend(navigation);
  button(navigation,'zoom-in','Zoom in',()=>navigate(()=>view.zoom(1/1.2)));
  button(navigation,'zoom-out','Zoom out',()=>navigate(()=>view.zoom(1.2)));
  button(navigation,'fit','Fit draft',()=>navigate(()=>view.fit()));
  const navigationHint=doc.createElement('span');navigationHint.textContent='Scroll to zoom. Use Pan to move the view.';navigation.append(navigationHint);
  const tool=field(toolbar,'tool','Tool','select',[['select','Select / move'],['pan','Pan view'],['atom','Atom'],['bond','Bond'],['ring','Ring'],['erase','Delete']]);
  const element=field(toolbar,'element','Element','text');element.value='C';element.maxLength=2;element.size=3;element.autocomplete='off';element.spellcheck=false;
  const bondKind=field(toolbar,'bond-kind','Bond','select',[['1','Single'],['2','Double'],['3','Triple'],['wedge','Solid wedge'],['dash','Hashed wedge']]);
  const ringType=field(toolbar,'ring-type','Ring','select',[['benzene','Benzene'],...['3','4','5','6','7','8'].map(n=>[n,`${n}-membered`])]);
  button(toolbar,'undo','Undo correction',()=>runHistory(onUndo));
  button(toolbar,'redo','Redo correction',()=>runHistory(onRedo));
  button(toolbar,'reset','Reset corrections',()=>runHistory(onReset));
  button(replacement,'replace-source','Replace review draft',replaceSource);
  const target=field(fields,'target','Target','select');
  const charge=field(fields,'charge','Charge','number'),isotope=field(fields,'isotope','Isotope (0 = natural)','number'),explicitHs=field(fields,'explicit-hs','Explicit hydrogens','number');
  for(const [input,min,max] of [[charge,-8,8],[isotope,0,400],[explicitHs,0,8]]){input.min=String(min);input.max=String(max);input.step='1';input.value='0';}
  const noImplicit=field(fields,'no-implicit','Disable implicit hydrogens','checkbox');
  button(fields,'apply-atom','Apply atom properties',()=>apply(()=>{
    const t=currentTarget();if(t.kind!=='atom')throw new Error('Choose an atom first.');
    const original=graph.atoms.find(a=>a.id===t.id);
    const result=original.symbol===symbol()?{graph:cloneData(graph),atomId:t.id}:editAtom(graph,{atomId:t.id,symbol:symbol()});
    const atom=result.graph.atoms.find(a=>a.id===t.id);
    Object.assign(atom,{charge:number(charge),isotope:number(isotope),explicitHs:number(explicitHs),noImplicit:noImplicit.checked});
    return {...result,changed:JSON.stringify(graph)!==JSON.stringify(result.graph)};
  }));
  const x=field(fields,'x','Position x','number'),y=field(fields,'y','Position y','number');
  for(const input of [x,y]){input.value='0';input.step='0.1';input.min='-10000';input.max='10000';}
  button(fields,'apply-tool','Apply tool to target',()=>apply(()=>{
    if(tool.value==='pan')throw new Error('Pan moves the view only. Choose an editing tool to change an atom or bond.');
    const t=currentTarget(),p={x:number(x),y:number(y)};
    if(tool.value==='atom'){
      if(t.kind==='bond')throw new Error('Choose an atom or an empty position.');
      return editAtom(graph,{atomId:t.kind==='atom'?t.id:null,position:p,symbol:symbol(),makeId});
    }
    if(tool.value==='bond'){
      if(t.kind!=='bond')throw new Error('Choose a bond to change it, or use Start bond and Finish bond.');
      return editBond(graph,{bondId:t.id,kind:bondKind.value});
    }
    if(tool.value==='ring')return placeRing(graph,{position:p,atomId:t.kind==='atom'?t.id:null,bondId:t.kind==='bond'?t.id:null,type:ringType.value,makeId});
    if(tool.value==='erase')return deleteEntities(graph,{atomIds:t.kind==='atom'?[t.id]:[],bondIds:t.kind==='bond'?[t.id]:[]});
    if(t.kind!=='atom')throw new Error('Choose an atom to move.');
    const atom=graph.atoms.find(a=>a.id===t.id);return translateAtoms(graph,{atomIds:[t.id],dx:p.x-atom.x,dy:p.y-atom.y});
  }));
  button(fields,'start-bond','Start bond',()=>{
    if(blocked())return;
    try{
      const t=currentTarget();if(t.kind==='bond')throw new Error('Choose an atom or an empty position.');
      const p=t.kind==='atom'?graph.atoms.find(a=>a.id===t.id):{x:number(x),y:number(y)};
      pendingBond={startAtomId:t.kind==='atom'?t.id:null,startPosition:{x:p.x,y:p.y}};
      target.value='empty';selectedAtoms.clear();selectedBonds.clear();x.value=String(p.x+1.5);y.value=String(p.y);
      say('Choose another atom or an empty endpoint, then Finish bond.');paintSelection();refresh();
    }catch(error){fail(error);}
  });
  button(fields,'finish-bond','Finish bond',()=>apply(()=>{
    const t=currentTarget();if(!pendingBond||t.kind==='bond')throw new Error('Start a bond and choose an atom or empty endpoint.');
    return createBond(graph,{...pendingBond,endAtomId:t.kind==='atom'?t.id:null,endPosition:{x:number(x),y:number(y)},endSymbol:symbol(),kind:bondKind.value,makeId});
  }));
  button(fields,'cancel-bond','Cancel bond',()=>{pendingBond=null;say('Unfinished bond cancelled.');refresh();});
  canvas.setAttribute('tabindex','0');canvas.setAttribute('role','img');canvas.setAttribute('aria-label','Editable import candidate. Select a tool, then use the canvas or the keyboard editing controls.');canvas.style.touchAction='none';
  const blocked=()=>destroyed||disabled||working||!graph;
  const makeId=prefix=>`${prefix}_${doc.defaultView.crypto.randomUUID().replaceAll('-','')}`;
  const symbol=()=>{const value=element.value.trim();return value?value[0].toUpperCase()+value.slice(1).toLowerCase():value;};
  function number(input){if(!input.value.trim()||!input.checkValidity()||!Number.isFinite(input.valueAsNumber))throw new Error('Enter a valid '+input.parentElement.firstChild.textContent+'.');return input.valueAsNumber;}
  function currentTarget(){const value=target.value,colon=value.indexOf(':');return colon<0?{kind:'empty',id:null}:{kind:value.slice(0,colon),id:value.slice(colon+1)};}
  function say(message){status.textContent=message;status.classList.remove('image-import-error');}
  function fail(error){status.textContent=error.message||String(error);status.classList.add('image-import-error');onError(error);}
  function refresh(){
    const off=blocked(),t=currentTarget();
    for(const input of controls.values())input.disabled=off;
    controls.get('undo').disabled=off||!canUndo||typeof onUndo!=='function';
    controls.get('redo').disabled=off||!canRedo||typeof onRedo!=='function';
    controls.get('reset').disabled=off||typeof onReset!=='function';
    for(const key of ['charge','isotope','explicit-hs','no-implicit','apply-atom'])controls.get(key).disabled=off||t.kind!=='atom';
    controls.get('start-bond').disabled=off||!!pendingBond||t.kind==='bond';
    controls.get('finish-bond').disabled=off||!pendingBond||t.kind==='bond';
    controls.get('cancel-bond').disabled=off||!pendingBond;
    controls.get('apply-tool').disabled=off||tool.value==='pan';
    const replacementAvailable=typeof onReplace==='function',sourceTooLarge=new TextEncoder().encode(structureSource.value).byteLength>1_000_000;
    structureSource.disabled=off||!replacementAvailable;
    structureSource.setCustomValidity(sourceTooLarge?'The replacement structure must be no larger than 1,000,000 UTF-8 bytes.':'');
    controls.get('replace-source').disabled=off||!replacementAvailable||!structureSource.value.trim()||sourceTooLarge;
    canvas.setAttribute('aria-disabled',String(off));canvas.setAttribute('aria-busy',String(working));
    canvas.style.cursor=tool.value==='select'?'default':tool.value==='pan'?(drag?'grabbing':'grab'):'crosshair';
    const hints={select:'Click an atom or bond to select it. Drag selected atoms to move them; Shift selects several. Arrow keys move selected atoms. Delete removes the selection.',pan:'Drag to pan without moving atoms. Arrow keys pan the view. Escape stops panning. Fit draft shows the whole structure.',atom:'Click an atom to change its element, or empty space to add an atom.',bond:'Drag between atoms or into empty space to draw a bond. Click a bond to change its type. Shift-drag allows a free angle.',ring:'Click empty space, an atom, or a bond to place a ring.',erase:'Click an atom or bond to delete it.'};
    hint.textContent=hints[tool.value]+' Corrections stay in this review until you accept the import.';
  }
  function updateTargets(){
    const previous=target.value;target.replaceChildren();
    function option(value,text){const node=doc.createElement('option');node.value=value;node.textContent=text;target.append(node);}
    option('empty','Empty position');
    if(graph){
      const names=new Map(graph.atoms.map((a,i)=>[a.id,`${i+1} ${a.symbol}`]));
      for(const a of graph.atoms)option('atom:'+a.id,`Atom ${names.get(a.id)}${a.charge?`, charge ${a.charge}`:''}${a.isotope?`, isotope ${a.isotope}`:''}`);
      for(const [i,b] of graph.bonds.entries())option('bond:'+b.id,`Bond ${i+1}: ${names.get(b.begin)} to ${names.get(b.end)}`);
    }
    target.value=previous;if(!target.value)target.value='empty';
  }
  function inspectTarget(){
    if(!graph)return;const t=currentTarget();
    if(t.kind==='atom'){
      const a=graph.atoms.find(a=>a.id===t.id);if(!a)return;
      element.value=a.symbol;charge.value=String(a.charge??0);isotope.value=String(a.isotope??0);explicitHs.value=String(a.explicitHs??0);noImplicit.checked=a.noImplicit??false;
      x.value=String(a.x);y.value=String(a.y);
    }else if(t.kind==='bond'){
      const b=graph.bonds.find(b=>b.id===t.id);if(!b)return;
      bondKind.value=b.direction===1?'wedge':b.direction===2?'dash':String(b.order);
      const a=graph.atoms.find(a=>a.id===b.begin),end=graph.atoms.find(a=>a.id===b.end);
      x.value=String((a.x+end.x)/2-(end.y-a.y));y.value=String((a.y+end.y)/2+end.x-a.x);
    }
  }
  function mark(tag,attributes,parent=selection){const node=doc.createElementNS(ns,tag);for(const [key,value] of Object.entries(attributes))node.setAttribute(key,String(value));parent.append(node);}
  function paintSelection(){
    selection.replaceChildren();if(!scene)return;
    const positions=new Map(scene.positions.map(a=>[a.id,a]));
    for(const id of selectedBonds){const b=graph.bonds.find(b=>b.id===id),a=b&&positions.get(b.begin),end=b&&positions.get(b.end);if(a&&end)mark('line',{x1:a.x,y1:a.y,x2:end.x,y2:end.y,stroke:'var(--gesture-stroke, #3979d6)','stroke-width':8,opacity:.45});}
    for(const id of selectedAtoms){const p=positions.get(id);if(p)mark('circle',{cx:p.x,cy:p.y,r:13,fill:'none',stroke:'var(--gesture-stroke, #3979d6)','stroke-width':2});}
  }
  function select(atomId,bondId,{append=false,inspect=true}={}){
    if(!append){selectedAtoms.clear();selectedBonds.clear();}
    if(atomId){if(append&&selectedAtoms.has(atomId))selectedAtoms.delete(atomId);else selectedAtoms.add(atomId);target.value='atom:'+atomId;}
    else if(bondId){if(append&&selectedBonds.has(bondId))selectedBonds.delete(bondId);else selectedBonds.add(bondId);target.value='bond:'+bondId;}
    else target.value='empty';
    if(inspect)inspectTarget();paintSelection();refresh();
  }
  function cancelGesture(){const previous=drag;drag=null;if(previous&&canvas.hasPointerCapture(previous.pointerId))canvas.releasePointerCapture(previous.pointerId);gesture.replaceChildren();if(tool.value==='pan')canvas.style.cursor='grab';}
  function displayView(next){if(next)canvas.setAttribute('viewBox',`${next.x} ${next.y} ${next.w} ${next.h}`);}
  function navigate(action){if(blocked())return;try{cancelGesture();pendingBond=null;displayView(action());refresh();}catch(error){fail(error);}}
  function point(event){const matrix=canvas.getScreenCTM();if(!matrix)throw new Error('The correction canvas is not visible.');const p=canvas.createSVGPoint();p.x=event.clientX;p.y=event.clientY;return p.matrixTransform(matrix.inverse());}
  function world(p){const t=scene.transform;return {x:(p.x-t.x0)/t.xScale,y:(p.y-t.y0)/t.yScale};}
  function hit(p){
    const matrix=canvas.getScreenCTM(),tolerance=14/(matrix?Math.hypot(matrix.a,matrix.b):1);let atomId=null,bondId=null,best=tolerance;
    for(const a of scene.positions){const distance=Math.hypot(p.x-a.x,p.y-a.y);if(distance<best){atomId=a.id;best=distance;}}
    if(atomId)return {atomId,bondId};
    const positions=new Map(scene.positions.map(a=>[a.id,a]));best=tolerance*.6;
    for(const b of graph.bonds){const a=positions.get(b.begin),end=positions.get(b.end),dx=end.x-a.x,dy=end.y-a.y,length=dx*dx+dy*dy;if(!length)continue;const t=Math.max(0,Math.min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/length)),distance=Math.hypot(p.x-a.x-t*dx,p.y-a.y-t*dy);if(distance<best){bondId=b.id;best=distance;}}
    return {atomId,bondId};
  }
  async function apply(build){
    if(blocked())return;const epoch=generation,focus=doc.activeElement;
    try{
      const candidate=build();if(!candidate.changed){say(candidate.reason==='already-bonded'?'Those atoms are already bonded. Use the Bond tool to change their bond.':candidate.reason==='same-atom'?'Choose a different endpoint for the bond.':'No correction was needed.');return;}
      const notice=stereoEditNotice(graph,candidate.graph);
      working=true;cancelGesture();refresh();
      const prepared=await onChange(cloneData(candidate.graph));
      if(epoch!==generation||destroyed)return;
      if(!prepared?.project||!prepared?.scene){say('The correction was not applied. The previous preview is retained.');return;}
      load(prepared);
      pendingBond=null;
      const id=candidate.atomId??candidate.createdAtomIds?.[0];
      if(id&&graph.atoms.some(a=>a.id===id))select(id,null);
      say(notice||'Correction applied to the preview. Review it before accepting the import.');
    }catch(error){if(epoch===generation&&!destroyed)fail(error);}
    finally{working=false;refresh();if(!destroyed&&doc.activeElement===doc.body&&(host.contains(focus)||focus===canvas)&&!focus.disabled)focus.focus();}
  }
  async function runHistory(callback){
    if(blocked()||typeof callback!=='function')return;const epoch=generation;
    working=true;cancelGesture();pendingBond=null;refresh();
    try{const prepared=await callback();if(epoch===generation&&!destroyed){if(prepared?.project&&prepared?.scene)load(prepared);say('Correction preview updated.');}}
    catch(error){if(epoch===generation&&!destroyed)fail(error);}
    finally{working=false;refresh();}
  }
  async function replaceSource(){
    if(blocked()||typeof onReplace!=='function')return;const epoch=generation,source=structureSource.value;
    if(!source.trim())return;
    if(new TextEncoder().encode(source).byteLength>1_000_000){fail(new Error('The replacement structure must be no larger than 1,000,000 UTF-8 bytes.'));return;}
    working=true;cancelGesture();pendingBond=null;refresh();
    try{
      const prepared=await onReplace(source);if(epoch!==generation||destroyed)return;
      if(!prepared?.project||!prepared?.scene){say('The replacement was not applied. The previous preview is retained.');return;}
      selectedAtoms.clear();selectedBonds.clear();target.value='empty';view.clear();load(prepared);structureSource.value='';
      say('Review draft replaced. Compare it with the source before accepting the import.');
    }catch(error){if(epoch===generation&&!destroyed)fail(error);}
    finally{working=false;refresh();}
  }
  function load(prepared){
    generation++;cancelGesture();graph=cloneData(prepared.project.graph);scene=cloneData(prepared.scene);pendingBond=null;
    drawing.replaceChildren(prepared.fragment.cloneNode(true));canvas.replaceChildren(drawing,selection,gesture);
    canvas.setAttribute('viewBox',`0 0 ${scene.width} ${scene.height}`);
    const rect=canvas.getBoundingClientRect();
    displayView(view.load({scene,graph,bounds:drawing.getBBox(),width:rect.width,height:rect.height}));
    selectedAtoms=new Set([...selectedAtoms].filter(id=>graph.atoms.some(a=>a.id===id)));selectedBonds=new Set([...selectedBonds].filter(id=>graph.bonds.some(b=>b.id===id)));
    if(prepared.history){canUndo=!!prepared.history.canUndo;canRedo=!!prepared.history.canRedo;}
    updateTargets();inspectTarget();paintSelection();host.hidden=false;refresh();
  }
  function clear(){generation++;cancelGesture();view.clear();graph=null;scene=null;pendingBond=null;selectedAtoms.clear();selectedBonds.clear();canUndo=false;canRedo=false;canvas.replaceChildren();structureSource.value='';updateTargets();status.textContent='';host.hidden=true;refresh();}
  listen(structureSource,'input',()=>{refresh();if(!structureSource.validity.valid)say(structureSource.validationMessage);});
  listen(tool,'change',()=>{cancelGesture();pendingBond=null;refresh();});
  listen(target,'change',()=>{const t=currentTarget();select(t.kind==='atom'?t.id:null,t.kind==='bond'?t.id:null);});
  listen(canvas,'pointerdown',event=>{
    if(blocked()||event.button!==0)return;event.preventDefault();canvas.focus();pendingBond=null;
    try{
      const p=point(event),w=world(p),{atomId,bondId}=hit(p);
      if(tool.value==='pan'){
        drag={kind:'pan',pointerId:event.pointerId,p};canvas.setPointerCapture(event.pointerId);refresh();
      }else if(tool.value==='select'){
        if(!atomId||event.shiftKey||!selectedAtoms.has(atomId))select(atomId,bondId,{append:event.shiftKey});
        if(atomId&&selectedAtoms.size){drag={kind:'move',pointerId:event.pointerId,p,w,base:cloneData(graph),ids:[...selectedAtoms]};canvas.setPointerCapture(event.pointerId);}
      }else if(tool.value==='atom')void apply(()=>editAtom(graph,{atomId,position:w,symbol:symbol(),makeId}));
      else if(tool.value==='erase')void apply(()=>deleteEntities(graph,{atomIds:atomId?[atomId]:[],bondIds:bondId?[bondId]:[]}));
      else if(tool.value==='ring')void apply(()=>placeRing(graph,{position:w,atomId,bondId,type:ringType.value,makeId}));
      else if(bondId)void apply(()=>editBond(graph,{bondId,kind:bondKind.value}));
      else{drag={kind:'bond',pointerId:event.pointerId,p,w,start:atomId,base:cloneData(graph)};canvas.setPointerCapture(event.pointerId);}
    }catch(error){cancelGesture();fail(error);}
  });
  listen(canvas,'pointermove',event=>{
    if(!drag||blocked()||event.pointerId!==drag.pointerId)return;
    try{const p=point(event);gesture.replaceChildren();
      if(drag.kind==='pan')displayView(view.pan(drag.p.x-p.x,drag.p.y-p.y));
      else if(drag.kind==='bond')mark('line',{x1:drag.p.x,y1:drag.p.y,x2:p.x,y2:p.y,stroke:'var(--gesture-stroke, #3979d6)','stroke-width':2,'stroke-dasharray':'5 4'},gesture);
      else for(const id of drag.ids){const a=scene.positions.find(a=>a.id===id);if(a)mark('circle',{cx:a.x+p.x-drag.p.x,cy:a.y+p.y-drag.p.y,r:12,fill:'none',stroke:'var(--gesture-stroke, #3979d6)','stroke-width':2},gesture);}
    }catch(error){cancelGesture();fail(error);}
  });
  listen(canvas,'pointerup',event=>{
    if(!drag||event.pointerId!==drag.pointerId)return;const d=drag;cancelGesture();refresh();if(blocked()||d.kind==='pan')return;
    try{const p=point(event),w=world(p);
      if(d.kind==='move'){if(Math.hypot(p.x-d.p.x,p.y-d.p.y)>1)void apply(()=>translateAtoms(d.base,{atomIds:d.ids,dx:w.x-d.w.x,dy:w.y-d.w.y}));}
      else void apply(()=>createBond(d.base,{startAtomId:d.start,startPosition:d.w,endAtomId:hit(p).atomId,endPosition:w,endSymbol:symbol(),kind:bondKind.value,freeAngle:event.shiftKey,makeId}));
    }catch(error){fail(error);}
  });
  listen(canvas,'pointercancel',cancelGesture);listen(canvas,'lostpointercapture',cancelGesture);listen(canvas,'dragstart',event=>event.preventDefault());
  listen(canvas,'wheel',event=>{
    if(blocked()||!Number.isFinite(event.deltaY)||!event.deltaY)return;
    event.preventDefault();event.stopPropagation();
    const magnitude=event.deltaMode===1?event.deltaY*16:event.deltaMode===2?event.deltaY*300:event.deltaY;
    navigate(()=>view.zoom(Math.exp(Math.max(-1,Math.min(1,magnitude*.002))),point(event)));
  },{passive:false});
  if(typeof doc.defaultView.ResizeObserver==='function'){
    const observer=new doc.defaultView.ResizeObserver(()=>{
      if(!scene||destroyed)return;
      const size=canvas.getBoundingClientRect();displayView(view.resize(size.width,size.height));
    });
    observer.observe(canvas);listeners.push(()=>observer.disconnect());
  }
  listen(canvas,'keydown',event=>{
    if(blocked()||event.isComposing)return;
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();event.stopPropagation();void runHistory(event.shiftKey?onRedo:onUndo);return;}
    if(event.key==='Escape'&&(drag||pendingBond)){event.preventDefault();event.stopPropagation();cancelGesture();pendingBond=null;refresh();return;}
    if(!event.ctrlKey&&!event.metaKey&&!event.altKey&&['+','=','-'].includes(event.key)){
      event.preventDefault();event.stopPropagation();navigate(()=>view.zoom(event.key==='-'?1.2:1/1.2));return;
    }
    if(event.key==='Delete'||event.key==='Backspace'){event.preventDefault();event.stopPropagation();if(tool.value==='pan')return;void apply(()=>deleteEntities(graph,{atomIds:[...selectedAtoms],bondIds:[...selectedBonds]}));return;}
    const direction={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,1],ArrowDown:[0,-1]}[event.key];
    if(direction&&tool.value==='pan'&&!event.ctrlKey&&!event.metaKey&&!event.altKey){
      event.preventDefault();event.stopPropagation();const current=view.snapshot(),size=canvas.getBoundingClientRect(),step=event.shiftKey?80:24;
      navigate(()=>view.pan(direction[0]*step*current.w/size.width,-direction[1]*step*current.h/size.height));return;
    }
    if(direction&&selectedAtoms.size&&!event.ctrlKey&&!event.metaKey&&!event.altKey){event.preventDefault();event.stopPropagation();const step=event.shiftKey?.5:.1;void apply(()=>translateAtoms(graph,{atomIds:[...selectedAtoms],dx:direction[0]*step,dy:direction[1]*step}));}
  });
  clear();
  return {load,clear,setDisabled(value){disabled=!!value;if(disabled)cancelGesture();refresh();},history(state={}){canUndo=!!state.canUndo;canRedo=!!state.canRedo;refresh();},destroy(){clear();destroyed=true;for(const remove of listeners)remove();host.remove();}};
}
