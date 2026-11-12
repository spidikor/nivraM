import {requestImport} from './modules/import-contract.mjs';
import {requestRender} from './modules/render-contract.mjs';
import {mountComparison} from './modules/comparison.mjs';
import {mountStereoInspector} from './modules/stereo-inspector.mjs';
import {insertFragment,assertFragmentRender} from './modules/fragments.mjs';
import {initializeFragmentShelf} from './modules/fragment-shelf.mjs';
import {mountStereoGuidance} from './modules/stereo-guidance.mjs';
import {valenceFeedback,paintValenceFeedback} from './modules/valence-feedback.mjs';
import {exportSvg} from './modules/svg-export.mjs';
import {validateOptions,exportPng,downloadBlob} from './modules/export.mjs';
import {createDocumentStore,validateProject,prepareRender,cloneData,DEFAULT_EXPORT} from './modules/document.mjs';
import {createJobController} from './modules/jobs.mjs';
import {prepareCleanupPreview} from './modules/cleanup.mjs';
import {stereoEditNotice} from './modules/bond-edit.mjs';
import {editAtom,createBond,editBond,placeRing as ringCandidate,deleteEntities,translateAtoms} from './modules/edit-commands.mjs';
import {initializeKeyboardEditor} from './modules/keyboard-edit.mjs';
import {initializeThemeToggle} from './modules/theme.mjs';
import {initializeCanvasMenu} from './modules/canvas-menu.mjs';
import {createElementEntry} from './modules/element-entry.mjs';
import {initializeElementPalette} from './modules/element-palette.mjs';
import {fitInkView,drawingView,measureMedianBondLength,restoreHistoryView} from './modules/viewport.mjs';

const $=id=>document.getElementById(id);
const themeController=initializeThemeToggle(document.documentElement,$('theme-toggle'),{warn:message=>notify(message,true)});
window.nivramTheme=Object.freeze({ready:themeController.ready,getState:themeController.getState,flush:themeController.flush});
function connectTheme(){
  const native=window.pywebview?.api;
  if(typeof native?.get_appearance==='function'&&typeof native?.set_appearance==='function')void themeController.connect(native);
}
window.addEventListener('pywebviewready',connectTheme);
queueMicrotask(connectTheme);
const token=document.querySelector('meta[name="nivram-token"]').content;
const svg=$('canvas'), drawing=$('drawing'), overlay=$('selection'), gesture=$('gesture');
const NS='http://www.w3.org/2000/svg';
const empty=()=>({atoms:[],bonds:[]});
let graph=empty(),scene=null,tool='select',element='C',selected=new Set(),selectedBonds=new Set();
let style={bondWidth:4.2,labelSize:36,bondSpacing:0.14,labelPadding:0.04,color:true,hashBondStyle:'engine'};
let busy=false,drag=null,view={x:0,y:0,width:800,height:500},activeJob=null;
let keyboardEditor=null,fragmentShelf=null;
let canvasMenu=null;
let elementEntry=null,elementPalette=null,hover=null,autoFit=true,resizeFitPending=false;
let symbolPreview=null;
let cleanupPreview=null,cleanupJob=null,cleanupVersion='version not reported';
const documentStore=createDocumentStore({schema:'nivram/1',version:'0.2.0-dev.1',graph,style,export:{...DEFAULT_EXPORT}});
const jobs=createJobController(()=>documentStore.getSnapshot().revision);
let toastTimer, valenceTimer, valenceMessage=null, valenceEpoch=0;
function clearValenceFeedback(){
  valenceEpoch++;
  clearTimeout(valenceTimer);$('valence-warning').replaceChildren();
  $('valence-warning').setAttribute('aria-hidden','true');
  $('valence-warning').removeAttribute('aria-label');
  if(valenceMessage&&$('toast').textContent===valenceMessage){clearTimeout(toastTimer);$('toast').hidden=true;}
  valenceMessage=null;
}
$('keyboard-editor').addEventListener('click',clearValenceFeedback,{capture:true});
const copy=cloneData;
const uid=prefix=>prefix+[...crypto.getRandomValues(new Uint8Array(8))].map(v=>v.toString(16).padStart(2,'0')).join('');

function notify(message,error=false,duration=error?11000:4500){
  clearTimeout(toastTimer);$('toast').textContent=message;$('toast').classList.toggle('error',error);$('toast').hidden=false;
  toastTimer=setTimeout(()=>$('toast').hidden=true,duration);
}
function setBusy(value,label='Working…'){
  if(value){canvasMenu?.close();elementPalette?.close();}
  busy=value;document.body.classList.toggle('busy',value);$('request-status').textContent=value?label:'Ready';
  for(const id of ['export-width','margin-mode','margin-value','transparent-background','reset-margin','export-svg-button','remove-chemistry-metadata'])$(id).disabled=value;
  keyboardEditor?.refresh();fragmentShelf?.refresh();
  // Consume a resize synchronously at the busy boundary, including rejection
  // and cancellation. Read the current drawing/mode; retain no old view/job.
  if(!value&&resizeFitPending){resizeFitPending=false;if(autoFit)fit();}
}
async function api(action,data,job=null){
  if(['render','clean','reflect','rotate'].includes(action))return requestRender(action,data,{job,token});
  if(action==='import')return requestImport(data,{job,token});
  const wait=promise=>job?job.wait(promise):promise;
  const response=await wait(fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':token},body:JSON.stringify(data),...(job?{signal:job.signal}:{})}));
  const body=await wait(response.text());if(body.length>4_000_000)throw new Error('The local response exceeded the supported size.');
  const result=JSON.parse(body);if(!response.ok){const error=new Error(result.error||'Local request failed.');error.diagnostic=result.diagnostic;throw error;}return result;
}
function snapshot(){return documentStore.getSnapshot().document;}
function labelPaddingPercent(value){
  // Shift the decimal representation without exposing binary multiplication
  // noise (for example, a saved .145 displays as 14.5 percent).
  const [mantissa,exponent]=value.toExponential().split('e');
  return Number(`${mantissa}e${Number(exponent)+2}`);
}
function enteredLabelPadding(){
  const percent=$('label-padding').valueAsNumber;
  // An unchanged display preserves every supported saved fraction exactly.
  // An empty input remains NaN and is rejected by document validation.
  return percent===labelPaddingPercent(style.labelPadding)?style.labelPadding:percent/100;
}
function syncHistory(){const state=documentStore.historyState();$('undo-button').disabled=!state.canUndo;$('redo-button').disabled=!state.canRedo;}
documentStore.subscribe(syncHistory);
let stereoGuidance=null;
function setView(v){stereoGuidance?.clear();view=v;svg.setAttribute('viewBox',`${v.x} ${v.y} ${v.width} ${v.height}`);}
function fit(){
  resizeFitPending=false;
  elementEntry?.close();cancelDrag();autoFit=true;
  const rect=svg.getBoundingClientRect(),t=scene?.transform;
  const v=graph.atoms.length?fitInkView({bounds:drawing.getBBox(),viewportWidth:rect.width,viewportHeight:rect.height,medianBondLength:measureMedianBondLength(scene,graph)}):drawingView({viewportWidth:rect.width,viewportHeight:rect.height,svgBondLength:Math.abs(t?.xScale||60)*1.5,centerX:t?.x0??400,centerY:t?.y0??250});
  if(v)setView({x:v.x,y:v.y,width:v.w,height:v.h});
}
function zoom(factor,center=null){
  elementEntry?.close();cancelDrag();autoFit=false;
  const p=center||{x:view.x+view.width/2,y:view.y+view.height/2};
  const nextW=view.width*factor;if(nextW<40||nextW>20000)return;
  setView({x:p.x+(view.x-p.x)*factor,y:p.y+(view.y-p.y)*factor,width:nextW,height:view.height*factor});
}
function install(result,{baseProject=snapshot(),shouldFit=false,resetSelection=false,expectedRevision,mode='record',historyGroup=null,rememberView=false}={}){
  // Parse, validate and sanitize every candidate dependency before touching live state.
  const prepared=prepareRender(result,baseProject,document);
  const restored=(mode==='undo'||mode==='redo')?documentStore.historyContext(mode==='redo'):null;
  const rect=svg.getBoundingClientRect();
  const departing=(rememberView||restored)&&scene?{autoFit,view:{x:view.x,y:view.y,w:view.width,h:view.height},
    transform:copy(scene.transform),viewport:{width:rect.width,height:rect.height}}:null;
  documentStore.commit(prepared.project,{expectedRevision,mode,historyGroup,historyContext:departing,apply:(nextDocument,{changed})=>{
    const previous={graph,scene,style,autoFit,resizeFitPending,view:{...view},selected,selectedBonds,nodes:[...drawing.childNodes],overlays:[...overlay.childNodes]};
    const textFields=new Set(['counts','formula','molecular-weight','margin-summary','atom-info']);
    const controls=['empty-state','counts','formula','molecular-weight','bond-width','label-size','bond-spacing','label-padding','hash-bond-style','element-color','export-width','margin-mode','margin-value','transparent-background','margin-summary','atom-info','atom-charge','atom-isotope','atom-radicals','apply-atom'].map(id=>{
      const node=$(id);return {node,text:textFields.has(id)?node.textContent:undefined,value:node.value,checked:node.checked,hidden:node.hidden,disabled:node.disabled};
    });
    try{
      const old=scene?.transform;
      graph=nextDocument.graph;style=nextDocument.style;scene=prepared.scene;
      drawing.replaceChildren(prepared.fragment);
      if(restored){
        if(restored.autoFit)fit();
        else{
          const saved=restoreHistoryView({view:restored.view,previousTransform:restored.transform,
            nextTransform:scene.transform,previousViewport:restored.viewport,viewportWidth:rect.width,viewportHeight:rect.height});
          autoFit=false;setView({x:saved.x,y:saved.y,width:saved.w,height:saved.h});
        }
      }else if(old&&(!shouldFit||(rememberView&&!changed))){
        const t=scene.transform,rx=t.xScale/old.xScale,ry=t.yScale/old.yScale;
        setView({x:t.x0+(view.x-old.x0)*rx,y:t.y0+(view.y-old.y0)*ry,width:view.width*rx,height:view.height*ry});
      }else fit();
      const ids=new Set(graph.atoms.map(a=>a.id)),bondIds=new Set(graph.bonds.map(b=>b.id));
      selected=new Set(resetSelection?[]:[...selected].filter(id=>ids.has(id)));
      selectedBonds=new Set(resetSelection?[]:[...selectedBonds].filter(id=>bondIds.has(id)));
      $('empty-state').hidden=graph.atoms.length>0;
      $('counts').textContent=`${graph.atoms.length} atoms · ${graph.bonds.length} bonds`;
      $('formula').textContent=scene.formula;$('molecular-weight').textContent=scene.mass===null?'Molar mass unavailable':scene.mass?`${scene.mass.toFixed(2)} g/mol`:'';
      $('molecular-weight').title=scene.properties?.massUnavailable?.message||'';
      $('bond-width').value=style.bondWidth;$('label-size').value=style.labelSize;$('bond-spacing').value=style.bondSpacing;$('label-padding').value=labelPaddingPercent(style.labelPadding);$('element-color').checked=style.color;$('hash-bond-style').value=style.hashBondStyle;
      $('export-width').value=nextDocument.export.width;$('margin-mode').value=nextDocument.export.marginMode;$('margin-value').value=nextDocument.export.margin;$('transparent-background').checked=nextDocument.export.transparentBackground;
      updateMarginSummary();paintSelection();if(symbolPreview)paintSymbolPreview();
    }catch(error){
      ({graph,scene,style,autoFit,resizeFitPending,selected,selectedBonds}=previous);
      drawing.replaceChildren(...previous.nodes);overlay.replaceChildren(...previous.overlays);setView(previous.view);
      for(const state of controls){if(state.text!==undefined)state.node.textContent=state.text;for(const key of ['value','checked','hidden','disabled'])if(state[key]!==undefined)state.node[key]=state[key];}
      throw error;
    }
  }});
  syncHistory();
}
async function runDocumentOperation(action,data,{baseProject=snapshot(),job=null,shouldFit=false,resetSelection=false,mode='record',historyGroup=null,rememberView=false,label='Checking structure…',proposedEdit=null,verifyResult=null}={}){
  if(busy)return false;
  clearValenceFeedback();
  if(!historyGroup)elementEntry?.close();
  keyboardEditor?.cancel('The operation changed. Start the bond again.');drag=null;gesture.replaceChildren();
  const feedbackEpoch=valenceEpoch;
  const input=copy(data),task=job||jobs.begin('document',{input,settings:{action,shouldFit,mode}});
  try{
    task.assertCurrent();activeJob=task;setBusy(true,label);
    const result=await api(action,input,task);task.assertCurrent();
    verifyResult?.(result);
    install(result,{baseProject,shouldFit,resetSelection,expectedRevision:task.revision,mode,historyGroup,rememberView});
    return true;
  }catch(error){
    // Late, cancelled or superseded rejections must not decorate a newer drawing.
    try{task.assertCurrent();}catch{
      // A timeout aborts its own task, unlike a user cancellation or a newer
      // operation. Keep the failure explanation only for its current owner.
      if(activeJob===task&&task.revision===documentStore.getSnapshot().revision&&
        error===task.signal.reason&&error?.name==='TimeoutError')notify(error.message,true);
      return false;
    }
    const feedback=proposedEdit?valenceFeedback(error.diagnostic,proposedEdit.before,proposedEdit.after,scene?.transform):null;
    if(feedback){
      if(feedbackEpoch!==valenceEpoch)return false;
      paintValenceFeedback($('valence-warning'),feedback,document);
      valenceMessage=feedback.message;notify(feedback.message,true);valenceTimer=setTimeout(clearValenceFeedback,11000);
    }else notify(error.message,true);
    return false;
  }
  finally{task.finish();if(activeJob===task){activeJob=null;setBusy(false);gesture.replaceChildren();}}
}
async function change(next,{fit:shouldFit=false,record=true,newStyle=style,baseProject=snapshot(),job=null,resetSelection=false,historyGroup=null,rememberView=false,proposedEdit=null}={}){
  clearValenceFeedback();
  try{
    const candidate=validateProject({...baseProject,graph:next,style:newStyle});
    const accepted=await runDocumentOperation('render',{graph:candidate.graph,style:candidate.style},{baseProject:candidate,job,shouldFit,resetSelection,mode:record?'record':'none',historyGroup,rememberView,proposedEdit});
    if(accepted){const message=stereoEditNotice(baseProject.graph,snapshot().graph);if(message)notify(message,false,11000);}
    return accepted;
  }catch(error){notify(error.message,true);return false;}
}
async function loadText(text,{job=null}={}){
  const accepted=await runDocumentOperation('import',{source:text,style:copy(style)},{job,shouldFit:true,resetSelection:true,label:'Importing…'});
  if(accepted){documentStore.markUnsaved();window.nivramNative?.resetDestination();}
  return accepted;
}
async function reflectDrawing({mode,axis,selected:chosen,expectedRevision}){
  if(busy)return false;
  if(documentStore.getSnapshot().revision!==expectedRevision){notify('The drawing changed. Open the menu again.',true);return false;}
  const baseProject=snapshot();
  const accepted=await runDocumentOperation('reflect',{graph:baseProject.graph,style:baseProject.style,mode,axis,selected:chosen},
    {baseProject,label:mode==='flip'?'Flipping while preserving stereochemistry.':'Mirroring stereochemistry.'});
  if(accepted)notify(mode==='flip'?'Drawing flipped. Stereochemistry preserved. Undo restores the previous drawing.':'Drawing mirrored. Defined stereochemistry was reflected. Undo restores the previous drawing.');
  return accepted;
}
function rotationTarget(){return [...new Set([...selected,...graph.bonds.filter(b=>selectedBonds.has(b.id)).flatMap(b=>[b.begin,b.end])])];}
async function rotateDrawing({degrees,selected:chosen=rotationTarget(),expectedRevision=documentStore.getSnapshot().revision}){
  if(busy||!graph.atoms.length)return false;
  if(documentStore.getSnapshot().revision!==expectedRevision){notify('The drawing changed. Open the menu again.',true);return false;}
  const baseProject=snapshot();
  return runDocumentOperation('rotate',{graph:baseProject.graph,style:baseProject.style,degrees,selected:chosen},{baseProject,label:'Rotating 30°…'});
}
async function undo(redo=false){
  if(elementEntry?.pending()){
    const pending=elementEntry.cancel();jobs.cancelAll();if(activeJob){activeJob=null;setBusy(false);}const accepted=await pending;
    if(!accepted)return;
  }
  elementEntry?.close();
  if(busy)return;const entry=documentStore.historyTarget(redo);if(!entry)return;
  return runDocumentOperation('render',{graph:entry.graph,style:entry.style},{baseProject:entry,resetSelection:true,mode:redo?'redo':'undo',label:redo?'Restoring…':'Undoing…'});
}
async function clean(){
  if(busy)return;if(!graph.atoms.length)return notify('Draw or import a molecule first.',true);
  cancelCleanup();const source=documentStore.getSnapshot(),chosen=[...new Set([...selected,...graph.bonds.filter(b=>selectedBonds.has(b.id)).flatMap(b=>[b.begin,b.end])])],currentScene=scene;
  const task=jobs.begin('cleanup',{input:source.document,settings:{selected:chosen,backend:'rdkit'}});
  cleanupJob=task;activeJob=task;setBusy(true,'Preparing cleanup preview…');
  try{
    const result=await api('clean',{graph:task.input.graph,style:task.input.style,selected:task.settings.selected},task);task.assertCurrent();
    const preview=prepareCleanupPreview({source,result,selected:chosen,
      backend:{id:'rdkit',name:'RDKit',version:cleanupVersion,limitations:'Experimental local layout. Marvin JS cleanup equivalence is not established. Selected cleanup holds unselected atoms fixed; review boundary bonds and fragment placement.'},
      getRevision:()=>documentStore.getSnapshot().revision,prepare:(candidate,base)=>prepareRender(candidate,base,document),commit:install});
    const current=prepareRender({graph:source.document.graph,scene:currentScene},source.document,document);
    task.assertCurrent();cleanupPreview=preview;
    const reference=current.scene.transform;
    for(const [id,prepared] of [['cleanup-current',current],['cleanup-proposed',preview]]){
      const canvas=$(id),t=prepared.scene.transform,rx=reference.xScale/t.xScale,ry=reference.yScale/t.yScale;
      canvas.replaceChildren();const layer=mark('g',{transform:`matrix(${rx} 0 0 ${ry} ${reference.x0-t.x0*rx} ${reference.y0-t.y0*ry})`},canvas);layer.append(prepared.fragment);
    }
    $('cleanup-backend').textContent=`${preview.backend.name} ${preview.backend.version} · ${preview.scope==='selection'?`${chosen.length} selected atom${chosen.length===1?'':'s'}`:'whole structure'}`;
    $('cleanup-limitations').textContent=preview.backend.limitations;
    $('cleanup-summary').textContent=preview.changedAtoms?`${preview.changedAtoms} atom coordinate${preview.changedAtoms===1?'':'s'} changed. The drawing stays unchanged until Apply. Apply creates one undoable change.`:'The backend proposed no coordinate changes. Your drawing is unchanged.';
    $('apply-cleanup').disabled=preview.changedAtoms===0;$('cleanup-dialog').showModal();
    // Both panes use the same molecular coordinate frame and scale. A backend's
    // renderer translation/zoom must not conceal changed placement or orientation.
    const bounds=[$('cleanup-current').getBBox(),$('cleanup-proposed').getBBox()];
    const x=Math.min(...bounds.map(b=>b.x)),y=Math.min(...bounds.map(b=>b.y));
    const width=Math.max(...bounds.map(b=>b.x+b.width))-x,height=Math.max(...bounds.map(b=>b.y+b.height))-y,pad=Math.max(12,.08*Math.max(width,height));
    for(const id of ['cleanup-current','cleanup-proposed'])$(id).setAttribute('viewBox',`${x-pad} ${y-pad} ${Math.max(1,width)+2*pad} ${Math.max(1,height)+2*pad}`);
  }catch(error){
    if(cleanupJob===task){cancelCleanup();notify(error.message,true);}
  }finally{task.finish();if(cleanupJob===task)cleanupJob=null;if(activeJob===task){activeJob=null;setBusy(false);}}
}
function cancelCleanup(){
  cleanupPreview?.cancel();cleanupPreview=null;
  if(cleanupJob){cleanupJob.cancel('Cleanup preview cancelled. The drawing is unchanged.');if(activeJob===cleanupJob){activeJob=null;setBusy(false);}cleanupJob=null;}
  $('cleanup-dialog').close();$('cleanup-current').replaceChildren();$('cleanup-proposed').replaceChildren();
}
function applyCleanup(){
  const preview=cleanupPreview;if(!preview)return;
  // Clear the transient preview before the commit notifies document observers.
  cleanupPreview=null;
  try{preview.apply();cancelCleanup();notify('Cleanup applied. Undo restores the preceding drawing.');}
  catch(error){preview.cancel();cancelCleanup();notify(error.message,true);}
}
documentStore.subscribe(({revision})=>{
  if(cleanupPreview&&cleanupPreview.revision!==revision){cancelCleanup();notify('The document changed. Create a new cleanup preview before applying.',true);}
});
function mark(tag,attrs,parent=overlay){const el=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))el.setAttribute(k,String(v));parent.append(el);return el;}
const symbolLayer=mark('g',{'aria-hidden':'true','pointer-events':'none',id:'element-entry-preview'},svg);
function paintSymbolPreview(){
  symbolLayer.replaceChildren();if(!symbolPreview)return;
  const p=scene?.positions.find(p=>p.id===symbolPreview.id);if(!p)return;
  const dark=document.documentElement.dataset.theme==='dark',s=symbolPreview.symbol;
  const colors={N:'#333399',O:'#ff0000',F:'#996600',P:'#ff8000',S:'#999900',Cl:'#009900',Br:'#663333',I:'#660099'};
  mark('rect',{x:p.x-26,y:p.y-24,width:52,height:48,rx:4,fill:dark?'#101010':'#ffffff'},symbolLayer);
  const label=mark('text',{x:p.x,y:p.y,'text-anchor':'middle','dominant-baseline':'central','font-size':style.labelSize,'font-family':'Arial, sans-serif',fill:style.color&&colors[s]?colors[s]:dark?'#d6d6d6':'#000000',stroke:dark?'#ededed':'none','stroke-width':dark?1.2:0,'paint-order':'stroke'},symbolLayer);label.textContent=s;
}
function paintSelection(){
  window.dispatchEvent(new CustomEvent('nivram-stereo-selection'));
  overlay.replaceChildren();if(!scene)return;
  const pos=new Map(scene.positions.map(p=>[p.id,p]));
  for(const b of graph.bonds){if(selectedBonds.has(b.id)){const a=pos.get(b.begin),c=pos.get(b.end);mark('line',{x1:a.x,y1:a.y,x2:c.x,y2:c.y,stroke:'var(--selection-stroke)','stroke-width':8,opacity:.2});}}
  for(const id of selected){const p=pos.get(id);if(p)mark('circle',{cx:p.x,cy:p.y,r:11,fill:'var(--selection-fill)','fill-opacity':.10,stroke:'var(--selection-stroke)','stroke-width':1.4});}
  const atom=selected.size===1?graph.atoms.find(a=>a.id===[...selected][0]):null;
  $('atom-info').textContent=atom?`${atom.symbol} · ${atom.id}`:'Select one atom to edit.';
  for(const id of ['atom-charge','atom-isotope','atom-radicals','apply-atom'])$(id).disabled=!atom;
  $('atom-charge').value=atom?.charge||0;$('atom-isotope').value=atom?.isotope||0;$('atom-radicals').value=atom?.radicals||0;
  keyboardEditor?.refresh();
}
function setTool(value){elementEntry?.close();cancelDrag();tool=value;for(const b of document.querySelectorAll('[data-tool]')){const active=b.dataset.tool===value;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));}
  const hints={select:'Select atoms, drag a selection rectangle, or drag selected atoms.',atom:`Click an atom to change it to ${element}, or click empty space to add one.`,bond:'Drag from an atom to create a bond. Click a bond to change its type.',ring:'Click empty space, an atom, or a bond to place a ring.',erase:'Click an atom or bond, or drag from empty space to erase atoms on release.'};
  $('tool-hint').textContent=hints[value];svg.style.cursor=value==='select'?'default':'crosshair';
}
function setElement(symbol){element=symbol;elementPalette?.refresh();setTool('atom');}
function point(event){const p=svg.createSVGPoint();p.x=event.clientX;p.y=event.clientY;return p.matrixTransform(svg.getScreenCTM().inverse());}
function world(p){const t=scene?.transform||{x0:400,y0:250,xScale:60,yScale:-60};return {x:(p.x-t.x0)/t.xScale,y:(p.y-t.y0)/t.yScale};}
function tolerance(){return 14/(svg.getScreenCTM()?.a||1);}
function nearestAtom(p){let hit=null,dist=tolerance();for(const a of scene?.positions||[]){const d=Math.hypot(p.x-a.x,p.y-a.y);if(d<dist){hit=a.id;dist=d;}}return hit;}
function nearestBond(p){
  const pos=new Map((scene?.positions||[]).map(a=>[a.id,a]));let hit=null,best=tolerance()*.6;
  for(const b of graph.bonds){const a=pos.get(b.begin),c=pos.get(b.end);if(!a||!c)continue;const dx=c.x-a.x,dy=c.y-a.y,len=dx*dx+dy*dy;
    if(!len)continue;const t=Math.max(0,Math.min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/len));const d=Math.hypot(p.x-a.x-t*dx,p.y-a.y-t*dy);
    if(d<best){hit=b.id;best=d;}}
  return hit;
}
function currentHover(){
  if(!hover)return null;const rect=svg.getBoundingClientRect();
  if(hover.x<rect.left||hover.x>rect.right||hover.y<rect.top||hover.y>rect.bottom)return null;
  const p=point({clientX:hover.x,clientY:hover.y}),atomId=nearestAtom(p);
  return {...hover,atomId,bondId:atomId?null:nearestBond(p)};
}
async function editCommand(build,options={}){
  clearValenceFeedback();
  try{const before=copy(graph),candidate=build();if(candidate.changed)return await change(candidate.graph,{...options,proposedEdit:{before,after:candidate.graph}});
    if(candidate.reason==='already-bonded')notify('Those atoms are already bonded. Select the bond to change its type.');return false;
  }catch(error){notify(error.message,true);return false;}
}
async function deleteSelection(){if(!selected.size&&!selectedBonds.size)return;await editCommand(()=>deleteEntities(graph,{atomIds:[...selected],bondIds:[...selectedBonds]}),{resetSelection:true});}
async function placeRing(p,atomId,bondId){return editCommand(()=>ringCandidate(graph,{position:p,atomId,bondId,type:$('ring-type').value,makeId:uid}));}
function rectangleAtoms(p,start){return (scene?.positions||[]).filter(a=>a.x>=Math.min(p.x,start.x)&&a.x<=Math.max(p.x,start.x)&&a.y>=Math.min(p.y,start.y)&&a.y<=Math.max(p.y,start.y)).map(a=>a.id);}
function cancelDrag(){clearValenceFeedback();drag=null;gesture.replaceChildren();}
svg.addEventListener('pointerdown',async event=>{
  elementEntry?.close();clearValenceFeedback();
  if(busy||event.button===2)return;keyboardEditor?.cancel('Pointer editing cancelled the unfinished bond.');svg.focus();const p=point(event),w=world(p),aid=nearestAtom(p),bid=aid?null:nearestBond(p);
  svg.setPointerCapture(event.pointerId);
  if(event.altKey||event.button===1){autoFit=false;drag={kind:'pan',p,view:{...view}};return;}
  if(tool==='select'){
    if(aid){if(!event.shiftKey&&!selected.has(aid))selected.clear();if(event.shiftKey&&selected.has(aid))selected.delete(aid);else selected.add(aid);selectedBonds.clear();paintSelection();drag={kind:'move',p,w,ids:[...selected],base:copy(graph)};}
    else if(bid){if(!event.shiftKey){selected.clear();selectedBonds.clear();}selectedBonds.add(bid);paintSelection();}
    else{if(!event.shiftKey){selected.clear();selectedBonds.clear();paintSelection();}drag={kind:'rect',p,append:event.shiftKey};}
  }else if(tool==='erase'){
    if(aid||bid)await editCommand(()=>deleteEntities(graph,{atomIds:aid?[aid]:[],bondIds:bid?[bid]:[]}));
    else drag={kind:'erase-rect',p,clientX:event.clientX,clientY:event.clientY,active:false};
  }else if(tool==='atom'){
    await editCommand(()=>editAtom(graph,{atomId:aid,position:w,symbol:element,makeId:uid}));
  }else if(tool==='ring'){await placeRing(w,aid,bid);}
  else if(tool==='bond'){
    if(bid){await editCommand(()=>editBond(graph,{bondId:bid,kind:$('bond-type').value}));
    }else drag={kind:'bond',p,w,start:aid,base:copy(graph)};
  }
});
svg.addEventListener('pointermove',event=>{
  if(!hover||hover.x!==event.clientX||hover.y!==event.clientY)elementEntry?.close();
  const hp=point(event),ha=nearestAtom(hp);hover={x:event.clientX,y:event.clientY,atomId:ha,bondId:ha?null:nearestBond(hp)};
  if(!drag||busy)return;const p=point(event);gesture.replaceChildren();
  if(drag.kind==='pan'){const dx=p.x-drag.p.x,dy=p.y-drag.p.y;setView({...view,x:view.x-dx,y:view.y-dy});return;}
  if(drag.kind==='rect'){mark('rect',{x:Math.min(p.x,drag.p.x),y:Math.min(p.y,drag.p.y),width:Math.abs(p.x-drag.p.x),height:Math.abs(p.y-drag.p.y),fill:'var(--gesture-fill)','fill-opacity':.08,stroke:'var(--gesture-stroke)','stroke-width':1},gesture);}
  if(drag.kind==='erase-rect'){
    drag.active=Math.hypot(event.clientX-drag.clientX,event.clientY-drag.clientY)>4;
    if(drag.active){
      const ids=new Set(rectangleAtoms(p,drag.p)),positions=new Map(scene.positions.map(a=>[a.id,a]));
      mark('rect',{x:Math.min(p.x,drag.p.x),y:Math.min(p.y,drag.p.y),width:Math.abs(p.x-drag.p.x),height:Math.abs(p.y-drag.p.y),fill:'var(--erase-fill)','fill-opacity':.15,stroke:'var(--erase-stroke)','stroke-width':1},gesture);
      for(const b of graph.bonds)if(ids.has(b.begin)||ids.has(b.end)){const a=positions.get(b.begin),c=positions.get(b.end);mark('line',{x1:a.x,y1:a.y,x2:c.x,y2:c.y,stroke:'var(--erase-stroke)','stroke-width':8,opacity:.5},gesture);}
      for(const id of ids){const a=positions.get(id);mark('circle',{cx:a.x,cy:a.y,r:12,fill:'var(--erase-fill)',stroke:'var(--erase-stroke)'},gesture);}
    }
  }
  if(drag.kind==='bond')mark('line',{x1:drag.p.x,y1:drag.p.y,x2:p.x,y2:p.y,stroke:'var(--gesture-stroke)','stroke-width':2,'stroke-dasharray':'5 4'},gesture);
  if(drag.kind==='move')for(const id of drag.ids){const a=scene.positions.find(a=>a.id===id);if(a)mark('circle',{cx:a.x+p.x-drag.p.x,cy:a.y+p.y-drag.p.y,r:10,fill:'var(--gesture-fill)','fill-opacity':.2,stroke:'var(--gesture-stroke)'},gesture);}
});
svg.addEventListener('pointerup',async event=>{
  if(!drag)return;const d=drag;drag=null;const p=point(event),w=world(p);gesture.replaceChildren();
  if(d.kind==='rect'){
    for(const id of rectangleAtoms(p,d.p))selected.add(id);
    paintSelection();
  }else if(d.kind==='erase-rect'&&Math.hypot(event.clientX-d.clientX,event.clientY-d.clientY)>4){
    await editCommand(()=>deleteEntities(graph,{atomIds:rectangleAtoms(p,d.p)}),{resetSelection:true});
  }else if(d.kind==='move'&&Math.hypot(p.x-d.p.x,p.y-d.p.y)>1){
    await editCommand(()=>translateAtoms(d.base,{atomIds:d.ids,dx:w.x-d.w.x,dy:w.y-d.w.y}));
  }else if(d.kind==='bond'){
    await editCommand(()=>createBond(d.base,{startAtomId:d.start,startPosition:d.w,endAtomId:nearestAtom(p),endPosition:w,endSymbol:element,kind:$('bond-type').value,freeAngle:event.shiftKey,makeId:uid}));
  }
});
svg.addEventListener('pointercancel',cancelDrag);
// Completed pointerup clears drag before awaiting validation. Its natural
// capture release must not dismiss the still-pending rejection feedback.
svg.addEventListener('lostpointercapture',()=>{if(drag)cancelDrag();});
svg.addEventListener('pointerleave',()=>{hover=null;elementEntry?.close();});
window.addEventListener('blur',()=>{hover=null;elementEntry?.close();cancelDrag();});
document.addEventListener('compositionstart',()=>elementEntry?.close());
document.addEventListener('focusin',()=>elementEntry?.close());
svg.addEventListener('wheel',event=>{event.preventDefault();if(!busy)zoom(event.deltaY>0?1.12:1/1.12,point(event));},{passive:false});

function exportOptions(){return {width:Number($('export-width').value),marginMode:$('margin-mode').value,margin:$('margin-value').valueAsNumber,transparentBackground:$('transparent-background').checked};}
function updateMarginSummary(){const o=validateOptions(exportOptions());$('margin-summary').textContent=`${o.padding} px ${o.transparentBackground?'transparent':'white'} margin on every side`;}
function syncMargin(){
  try{
    updateMarginSummary();if(busy)return;
    documentStore.commit({...snapshot(),export:exportOptions()});syncHistory();
  }catch(error){$('margin-summary').textContent=error.message;}
}
function name(){let n=$('filename').value.trim().replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').replace(/[. ]+$/,'')||'structure';if(/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/i.test(n))n='structure_'+n;return n;}
function saveProject(){
  if(busy)return;
  try{
    const doc=validateProject({...snapshot(),export:exportOptions()});
    downloadBlob(new Blob([JSON.stringify(doc,null,2)],{type:'application/json'}),name()+'.nivram');
    notify('Project download requested. Check the downloaded file; unsaved changes remain protected.');
  }catch(error){notify(error.message,true);}
}
async function png(){
  if(busy)return;let job=null;
  try{
    const options=exportOptions(),filename=name()+'.png',removeChemistryMetadata=$('remove-chemistry-metadata').checked;validateOptions(options);
    job=jobs.begin('png-export',{input:snapshot(),settings:options,timeout:120000});activeJob=job;
    setBusy(true,'Rendering PNG…');$('export-result').textContent='Rendering vector artwork at the requested resolution…';
    const result=await exportPng(drawing,options,{signal:job.signal,graph,removeChemistryMetadata});job.assertCurrent();
    downloadBlob(result.blob,filename);$('export-result').textContent=`PNG prepared: ${result.width} × ${result.height} px · ${result.padding} px margins · ${result.transparentBackground?'transparent background':'opaque white'} · ${result.removeChemistryMetadata?'chemistry metadata removed':'recoverable chemistry metadata retained'}. Complete the save/download dialog.`;
    notify(`PNG prepared: ${result.width} × ${result.height} pixels. Complete the save/download dialog.`);
  }catch(error){if(!job||activeJob===job||(!activeJob&&!busy)){$('export-result').textContent='No PNG created.';notify(error.message,true);}}
  finally{job?.finish();if(job&&activeJob===job){activeJob=null;setBusy(false);}}
}
async function svgExport(){
  if(busy)return;let job=null;
  try{
    const options=exportOptions(),filename=name()+'.svg';validateOptions(options);
    if(!scene||!graph.atoms.length)throw new Error('Draw or import a molecule before exporting.');
    job=jobs.begin('svg-export',{input:snapshot(),settings:options});activeJob=job;
    setBusy(true,'Preparing SVG…');
    const result=exportSvg(scene.svg,options,{signal:job.signal,paintStyle:job.input.style});job.assertCurrent();
    downloadBlob(result.blob,filename);$('export-result').textContent=`SVG prepared: ${result.width} × ${result.height} nominal px · vector artwork · ${result.transparentBackground?'transparent background':'opaque white'}. Complete the save/download dialog.`;
    notify('SVG artwork prepared without cleanup. Complete the save/download dialog.');
  }catch(error){if(!job||activeJob===job||(!activeJob&&!busy)){$('export-result').textContent='No SVG created.';notify(error.message,true);}}
  finally{job?.finish();if(job&&activeJob===job){activeJob=null;setBusy(false);}}
}
async function mol(){
  if(busy)return;setBusy(true,'Preparing MOL…');try{const r=await api('export-mol',{graph});downloadBlob(new Blob([r.text],{type:'chemical/x-mdl-molfile'}),name()+'.mol');notify('2D MOL prepared without cleanup. Complete the save/download dialog.');}
  catch(error){notify(error.message,true);}finally{setBusy(false);}
}
function openImport(){$('import-dialog').showModal();setTimeout(()=>$('structure-input').focus(),40);}
$('import-button').onclick=openImport;$('empty-import').onclick=openImport;
$('load-structure').onclick=async()=>{if(await loadText($('structure-input').value))$('import-dialog').close();};
$('new-button').onclick=async()=>{if(busy)return;if(graph.atoms.length&&!confirm('Clear the current drawing? Undo will remain available.'))return;if(await change(empty(),{fit:true,resetSelection:true})){documentStore.markUnsaved();window.nivramNative?.resetDestination();}};
$('open-button').onclick=()=>$('file-input').click();
$('file-input').onchange=async event=>{
  const file=event.target.files[0];event.target.value='';if(!file)return;
  if(file.size>4_000_000)return notify('Files must be smaller than 4 MB in this alpha.',true);
  const job=jobs.begin('document-open',{input:{name:file.name,size:file.size}});
  try{
    const text=await job.wait(file.text());job.assertCurrent();let ok;
    if(/\.(nivram|json)$/i.test(file.name)){
      const doc=validateProject(JSON.parse(text));
      ok=await change(doc.graph,{newStyle:doc.style,baseProject:doc,job,fit:true,resetSelection:true});
      if(ok){documentStore.markSaved(documentStore.getSnapshot().revision);window.nivramNative?.resetDestination();}
    }else ok=await loadText(text,{job});
    if(ok)$('filename').value=file.name.replace(/\.[^.]+$/,'');
  }catch(error){notify('File not opened: '+error.message,true);}
  finally{job.finish();}
};
$('save-button').onclick=saveProject;$('undo-button').onclick=()=>undo();$('redo-button').onclick=()=>undo(true);
$('rotate-ccw').onclick=()=>rotateDrawing({degrees:30});$('rotate-cw').onclick=()=>rotateDrawing({degrees:-30});
$('clean-button').onclick=clean;$('delete-button').onclick=deleteSelection;$('export-button').onclick=png;$('export-svg-button').onclick=svgExport;$('export-mol-button').onclick=mol;
$('apply-cleanup').onclick=applyCleanup;$('cancel-cleanup').onclick=cancelCleanup;$('close-cleanup').onclick=cancelCleanup;
$('cleanup-dialog').addEventListener('cancel',event=>{event.preventDefault();cancelCleanup();});
$('fit-button').onclick=fit;$('zoom-in').onclick=()=>zoom(.8);$('zoom-out').onclick=()=>zoom(1.25);
$('reset-margin').onclick=()=>{$('margin-mode').value='percent';$('margin-value').value=2;syncMargin();};
for(const id of ['export-width','margin-mode','margin-value','transparent-background'])$(id).addEventListener('input',syncMargin);
$('apply-style').onclick=()=>change(copy(graph),{newStyle:{...style,bondWidth:Number($('bond-width').value),labelSize:Number($('label-size').value),bondSpacing:Number($('bond-spacing').value),labelPadding:enteredLabelPadding(),color:$('element-color').checked,hashBondStyle:$('hash-bond-style').value}});
$('apply-atom').onclick=async()=>{
  if(selected.size!==1)return;const g=copy(graph),a=g.atoms.find(a=>a.id===[...selected][0]);a.charge=$('atom-charge').valueAsNumber;a.isotope=$('atom-isotope').valueAsNumber;a.radicals=$('atom-radicals').valueAsNumber;await change(g,{proposedEdit:{before:copy(graph),after:g}});
};
for(const b of document.querySelectorAll('[data-tool]'))b.onclick=()=>setTool(b.dataset.tool);
elementPalette=initializeElementPalette({document,parent:$('elements'),onSelect:setElement,getSelected:()=>element});
elementEntry=createElementEntry({
  getTarget:()=>{const hit=currentHover();return hit?.atomId?{id:hit.atomId,x:hit.x,y:hit.y}:null;},
  getRevision:()=>documentStore.getSnapshot().revision,isBusy:()=>busy,
  beginGroup:()=>documentStore.beginHistoryGroup(),endGroup:token=>documentStore.endHistoryGroup(token),
  preview:(id,symbol)=>{symbolPreview={id,symbol};paintSymbolPreview();},
  clearPreview:()=>{symbolPreview=null;symbolLayer.replaceChildren();},
  apply:(symbol,historyGroup,id)=>{
    if(!graph.atoms.some(a=>a.id===id))return Promise.resolve(false);
    try{const next=editAtom(graph,{atomId:id,symbol,makeId:uid}).graph;return change(next,{historyGroup,proposedEdit:{before:copy(graph),after:next}});}
    catch(error){notify(error.message,true);return Promise.resolve(false);}
  }
});
const examples=[['Ethanol','CCO'],['Aspirin','CC(=O)Oc1ccccc1C(=O)O'],['Caffeine','Cn1c(=O)c2c(ncn2C)n(C)c1=O'],['Pyridine','c1ccncc1'],['Cyclohexane','C1CCCCC1'],['Indole','c1ccc2[nH]ccc2c1'],['Chiral test','N[C@@H](C)C(=O)O'],['Isotope / charge','[13CH3][C@@H](O)C(=O)[O-]']];
for(const [label,smi] of examples){const b=document.createElement('button');b.textContent=label;b.onclick=async()=>{if(await loadText(smi)){$('examples-dialog').close();$('filename').value=label.toLowerCase().replace(/[^a-z0-9]+/g,'-');}};$('example-list').append(b);}
$('samples-button').onclick=()=>$('examples-dialog').showModal();$('close-examples').onclick=()=>$('examples-dialog').close();
$('help-button').onclick=()=>$('about-dialog').showModal();$('close-about').onclick=()=>$('about-dialog').close();
document.addEventListener('keydown',event=>{
  if(event.defaultPrevented||event.isComposing)return;
  if(event.key==='Escape'){
    if(cleanupPreview||cleanupJob||$('cleanup-dialog').open){event.preventDefault();cancelCleanup();return;}
    if(document.querySelector('dialog[open]')){cancelPending();return;}
    if(keyboardEditor?.cancel()){event.preventDefault();return;}
    cancelPending();selected.clear();selectedBonds.clear();drag=null;gesture.replaceChildren();paintSelection();return;
  }
  if(document.querySelector('dialog[open]'))return;
  const editing=['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName)||event.target.isContentEditable;
  if(editing)return;
  if(event.key==='Shift')return;
  if(elementPalette.isOpen()||event.target.closest?.('[role="menu"]')){elementEntry.close();return;}
  if(event.ctrlKey||event.metaKey){
    elementEntry.close();
    const key=event.key.toLowerCase();if(['z','y','s','o','2'].includes(key)){event.preventDefault();if(key==='z')undo(event.shiftKey);else if(key==='y')undo(true);else if(key==='s')saveProject();else if(key==='o')$('file-input').click();else clean();}return;
  }
  if(event.repeat)return;
  if(event.altKey){elementEntry.close();return;}
  const hit=currentHover();
  if(!drag&&hit?.atomId&&/^[a-z]$/i.test(event.key)&&elementEntry.input(event.key)){event.preventDefault();return;}
  elementEntry.close();
  if(['1','2','3'].includes(event.key)){
    event.preventDefault();if(busy)return;
    if(hit?.bondId)editCommand(()=>editBond(graph,{bondId:hit.bondId,kind:event.key}));
    else{$('bond-type').value=event.key;setTool('bond');}return;
  }
  if(event.key==='Delete'||event.key==='Backspace'){event.preventDefault();if(!busy)deleteSelection();return;}
  if(event.target!==svg)return;
  const hotkeys={v:'select',a:'atom',b:'bond',r:'ring',e:'erase'};if(hotkeys[event.key.toLowerCase()])setTool(hotkeys[event.key.toLowerCase()]);
});
window.addEventListener('beforeunload',event=>{if(documentStore.getSnapshot().dirty){event.preventDefault();event.returnValue='';}});
function cancelPending(){cancelDrag();void elementEntry?.cancel();keyboardEditor?.cancel();cancelCleanup();jobs.cancelAll();if(activeJob){activeJob=null;setBusy(false);}}
document.addEventListener('pointerdown',event=>{if(event.target!==svg&&!svg.contains(event.target))elementEntry?.close();},true);
new ResizeObserver(()=>{
  elementEntry?.close();
  if(!autoFit)return;
  if(busy)resizeFitPending=true;else fit();
}).observe(svg);
let resolveReady,rejectReady;
const ready=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject;});
ready.catch(()=>{});
window.nivramDocument=Object.freeze({
  ready,
  getSnapshot:documentStore.getSnapshot,
  setRecoveryGeneration:documentStore.setRecoveryGeneration,
  subscribe:documentStore.subscribe,
  markSaved:documentStore.markSaved,
  cancelPending,
  beginCloseReview(){const appearance=themeController.beginCloseReview();documentStore.suspendMutations();cancelPending();setBusy(true,'Closing review…');window.dispatchEvent(new Event('nivram-close-review'));return {...documentStore.getSnapshot(),appearance};},
  endCloseReview(){themeController.endCloseReview();documentStore.resumeMutations();if(!activeJob)setBusy(false);window.dispatchEvent(new Event('nivram-close-review-ended'));},
  async openProject(project,{expectedRevision=documentStore.getSnapshot().revision,asUnsaved=false,signal=null,rememberView=false,returnSnapshot=false}={}){
    const job=jobs.begin('document-open',{input:project});
    const abort=()=>job.cancel(signal.reason?.message||'Project opening was cancelled.');
    if(signal){if(signal.aborted)abort();else signal.addEventListener('abort',abort,{once:true});}
    try{
      if(job.revision!==expectedRevision)throw new Error('The document changed while the file dialog was open. Open the file again to replace this drawing.');
      const candidate=validateProject(project);
      const accepted=await change(candidate.graph,{newStyle:candidate.style,baseProject:candidate,job,fit:true,resetSelection:true,rememberView});
      // Only a single changed-document commit may issue an optional receipt.
      // Any intervening edit (including style/context/undo) increments revision.
      const receiptCurrent=accepted&&documentStore.getSnapshot().revision===expectedRevision+1;
      if(accepted&&asUnsaved)documentStore.markUnsaved();
      const receipt=receiptCurrent&&documentStore.getSnapshot().revision===expectedRevision+(asUnsaved?2:1)?documentStore.getSnapshot():null;
      return accepted&&returnSnapshot?{accepted:true,snapshot:receipt}:accepted;
    }catch(error){notify(error.message,true);return false;}
    finally{signal?.removeEventListener('abort',abort);job.finish();}
  }
});
async function insertReusableFragment(fragment,source){
  if(busy)return false;
  if(source.revision!==documentStore.getSnapshot().revision)throw new Error('The drawing changed. Choose the fragment again.');
  const candidate=insertFragment(source.document.graph,fragment,uid);
  const baseProject=validateProject({...source.document,graph:candidate.graph});
  return runDocumentOperation('render',{graph:candidate.graph,style:baseProject.style},{baseProject,
    shouldFit:true,resetSelection:true,label:'Checking disconnected fragment copy…',
    verifyResult:result=>assertFragmentRender(candidate.graph,result,baseProject.style)});
}
fragmentShelf=initializeFragmentShelf({document,getSnapshot:documentStore.getSnapshot,
  getSelection:()=>({atoms:[...selected],bonds:[...selectedBonds]}),isBusy:()=>busy,
  insert:insertReusableFragment,duplicate:insertReusableFragment});
window.addEventListener('pywebviewready',()=>void fragmentShelf.reload());
window.addEventListener('nivram-close-review',()=>fragmentShelf.beginClose());
window.addEventListener('nivram-close-review-ended',()=>fragmentShelf.endClose());
keyboardEditor=initializeKeyboardEditor({document,getState:documentStore.getSnapshot,
  getSelection:()=>({atoms:[...selected],bonds:[...selectedBonds]}),
  setSelection:(atoms,bonds)=>{selected=new Set(atoms);selectedBonds=new Set(bonds);paintSelection();},
  commit:(next,options)=>change(next,{...options,proposedEdit:{before:copy(graph),after:next}}),makeId:uid,isBusy:()=>busy,
  inspect:target=>{
    const layer=$('keyboard-target-overlay');layer.replaceChildren();
    const positions=new Map((scene?.positions??[]).map(p=>[p.id,p]));
    const attrs={fill:'none',stroke:'currentColor','stroke-width':2,'stroke-dasharray':'4 3'};
    if(target.kind==='atom'){const p=positions.get(target.id);if(p)mark('circle',{...attrs,cx:p.x,cy:p.y,r:16},layer);}
    if(target.kind==='bond'){const b=graph.bonds.find(b=>b.id===target.id),a=positions.get(b?.begin),end=positions.get(b?.end);if(a&&end)mark('line',{...attrs,x1:a.x,y1:a.y,x2:end.x,y2:end.y,'stroke-width':5},layer);}
  },
  reveal:(target,position)=>{
    elementEntry.close();cancelDrag();autoFit=false;
    let p=position;
    if(target.kind==='atom')p=graph.atoms.find(a=>a.id===target.id);
    if(target.kind==='bond'){const b=graph.bonds.find(b=>b.id===target.id),a=graph.atoms.find(a=>a.id===b?.begin),end=graph.atoms.find(a=>a.id===b?.end);if(a&&end)p={x:(a.x+end.x)/2,y:(a.y+end.y)/2};}
    if(p){const t=scene?.transform||{x0:400,y0:250,xScale:60,yScale:-60};setView({...view,x:t.x0+p.x*t.xScale-view.width/2,y:t.y0+p.y*t.yScale-view.height/2});}
  },pan:(dx,dy)=>{elementEntry.close();cancelDrag();autoFit=false;setView({...view,x:view.x+dx*view.width/4,y:view.y+dy*view.height/4});}});
canvasMenu=initializeCanvasMenu({document,canvas:svg,getState:documentStore.getSnapshot,
  getSelection:()=>({atoms:[...selected],bonds:[...selectedBonds]}),
  hitTest:event=>{const p=point(event),atomId=nearestAtom(p);return atomId?{atomId}:{bondId:nearestBond(p)};},
  isBusy:()=>busy||!!drag,reflect:reflectDrawing,rotate:rotateDrawing,notify});
documentStore.subscribe(()=>{keyboardEditor.refresh();canvasMenu.close();});
window.dispatchEvent(new Event('nivram-document-ready'));
updateMarginSummary();
const startupJob=jobs.begin('document',{input:snapshot(),settings:{action:'startup'}});
try{
  activeJob=startupJob;setBusy(true,'Starting local engine…');const r=await api('render',{graph,style},startupJob);startupJob.assertCurrent();install(r,{shouldFit:true,expectedRevision:startupJob.revision,mode:'none'});
  const info=await (await fetch('/api/status')).json();cleanupVersion=typeof info.rdkit==='string'&&info.rdkit.trim()?info.rdkit:'version not reported';$('engine-version').textContent=`nivraM ${info.version} · RDKit ${info.rdkit}.`;
  activeJob=null;setBusy(false);resolveReady(documentStore.getSnapshot());
}catch(error){activeJob=null;setBusy(false);$('request-status').textContent='Connection failed';notify(error.message+' Restart the local server and reload.',true);rejectReady(error);}
finally{startupJob.finish();}

// Guidance is an independent UI layer, excluded from document and export data.
stereoGuidance=mountStereoGuidance({dom:document,events:window,canvas:svg,drawing,getDocument:()=>documentStore,getScene:()=>scene,getView:()=>view,getSelection:()=>({atoms:[...selected],bonds:[...selectedBonds]})});

void ready.then(()=>fragmentShelf.reload()).catch(()=>{});

const stereoInspector=mountStereoInspector({dom:document,events:window,editor:documentStore,token,selectTarget:(site,revision)=>{
  if(busy||documentStore.getSnapshot().revision!==revision)return;
  cancelDrag();setTool('select');selected=new Set(site.kind==='atom'?[site.id]:[]);selectedBonds=new Set(site.kind==='bond'?[site.id]:[]);paintSelection();svg.focus();
}});
window.addEventListener('pagehide',()=>stereoInspector.destroy(),{once:true});

const comparison=mountComparison({dom:document,events:window,editor:documentStore,token});
window.addEventListener('pagehide',()=>comparison.destroy(),{once:true});
