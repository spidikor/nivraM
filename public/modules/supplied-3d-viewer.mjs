import {mountMeasurements} from './three-d-measurements.mjs';
import {mountImageTo3d} from './image-to-3d.mjs';
import {generatedDisplayModel} from './generated-hydrogens.mjs';
export function radicalLabel(a){return Object.hasOwn(a,'radicalCode')?`RAD${a.radicalCode} ${['none','singlet','doublet','triplet'][a.radicalCode]} declaration (not electron count or verified spin)`:'RAD not declared';}
import {hydrogenDisplay,spaceFillingAvailable,projectDisplay,createSlowRotation} from './three-d-display.mjs';
import {parseSuppliedMol,suppliedProvenance,MAX_MOL_BYTES} from './supplied-3d.mjs';
import {validateSuppliedResult,validatedSuppliedProvenance,SUPPLIED_VALIDATION_WARNING} from './supplied-validation.mjs';
import {parseRecordJson} from './three-d-record.mjs';
import {createConformerController} from './conformer.mjs';
import {snapshotScene,exportThreeDPng,extractThreeDRecord,validateThreeDRecord,validateRecoveryResult,sha256,MAX_3D_PNG_BYTES,RECOVERY_WARNING} from './three-d-png.mjs';
const SVG='http://www.w3.org/2000/svg';
const COLORS={H:'#ffffff',C:'#526070',N:'#3165b8',O:'#ce3c39',F:'#34945b',Cl:'#34945b',Br:'#963f35',I:'#76539a',P:'#b16a10',S:'#b59b10'};
export function mountSupplied3d({dom=document,cryptoApi=globalThis.crypto,download,getDocument=()=>globalThis.window?.nivramDocument,request,pngExport=exportThreeDPng,sceneSnapshot=snapshotScene,motionQuery=globalThis.matchMedia?.('(prefers-reduced-motion: reduce)'),requestFrame=globalThis.requestAnimationFrame?.bind(globalThis),cancelFrame=globalThis.cancelAnimationFrame?.bind(globalThis)}={}){
  const find=id=>dom.getElementById(id),dialog=find('supplied-3d-dialog');if(!dialog)return null;
  const openButton=find('supplied-3d-open'),closeButton=find('supplied-3d-close'),input=find('supplied-3d-file'),scene=find('supplied-3d-scene'),status=find('supplied-3d-status');
  const atomRows=find('supplied-3d-atoms'),bondRows=find('supplied-3d-bonds'),provenanceView=find('supplied-3d-provenance'),exportMol=find('supplied-3d-export'),exportProvenance=find('supplied-3d-export-provenance');
  const exportPng=find('supplied-3d-export-png'),pngInput=find('supplied-3d-png-file');
  const styleControl=find('supplied-3d-style'),hydrogenControl=find('supplied-3d-hydrogens'),rotateControl=find('supplied-3d-rotate'),directionControl=find('supplied-3d-direction'),displayNote=find('supplied-3d-display-note');
  let display={style:'ball-and-stick',hydrogens:'implicit',direction:'left-to-right'};
  if(styleControl)styleControl.value=display.style;if(hydrogenControl)hydrogenControl.checked=false;if(rotateControl)rotateControl.checked=false;if(directionControl)directionControl.value=display.direction;
  let recoveredRecord=null,recovery=null,exportSequence=0,pendingPngExport=null,conformerPending=false,stereoReviewClose=false;
  let model=null,provenance=null,view={yaw:0,pitch:0,zoom:1},generation=0,drag=null,destroyed=false;
  const rotation=createSlowRotation({requestFrame,cancelFrame,canRun:()=>!destroyed&&dialog.open&&!dom.hidden&&!!model&&!!rotateControl?.checked&&!motionQuery?.matches,step:delta=>{view.yaw=(view.yaw+delta*(display.direction==='right-to-left'?-1:1))%(Math.PI*2);draw();}});
  function stopRotation(){rotation.stop();if(rotateControl)rotateControl.checked=false;}
  function pngEnabled(){if(exportPng)exportPng.disabled=!model;}
  const randomId=()=>Array.from((cryptoApi.getRandomValues?cryptoApi:globalThis.crypto).getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
  function cancelRecovery(){const job=recovery;recovery=null;if(job){clearTimeout(job.timer);job.controller.abort();if(job.sent)post((job.route||'coordinate-recovery')+'/cancel',{clientId:job.clientId,jobId:job.jobId,expiresAt:job.expiresAt}).catch(()=>{});}if(cancelButton)cancelButton.disabled=true;}
  const handlers=[];function on(node,type,fn){if(!node)return;node.addEventListener(type,fn);handlers.push(()=>node.removeEventListener(type,fn));}
  const generateButton=find('supplied-3d-generate'),cancelButton=find('supplied-3d-cancel');
  const post=request|| (async(action,data,options={})=>{const response=await fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':dom.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify(data),...options});const text=await response.text();if(new TextEncoder().encode(text).length>524288)throw new Error('Local validation response exceeds its bound.');const result=parseRecordJson(text,524288);if(!response.ok)throw new Error(result.error||'Local generation request failed.');return result;});
  const conformers=createConformerController({getDocument,request:post,onClearGuidance:()=>globalThis.window?.dispatchEvent(new CustomEvent('nivram-stereo-clear')),onDenial:detail=>{if(dialog.open){stereoReviewClose=true;dialog.close();}globalThis.window?.dispatchEvent(new CustomEvent('nivram-stereo-denial',{detail}));},cryptoApi:{subtle:cryptoApi.subtle,getRandomValues:bytes=>(cryptoApi.getRandomValues?cryptoApi:globalThis.crypto).getRandomValues(bytes)},onState:(state,text)=>{conformerPending=state==='pending';if(generateButton)generateButton.disabled=state==='pending';if(cancelButton)cancelButton.disabled=state!=='pending';message(text,state==='error');},onResult:result=>{cancelRecovery();generation++;recoveredRecord=null;model=result.scene;provenance=result.provenance;view={yaw:0,pitch:0,zoom:1};tables();measurements.reset();draw();exportMol.disabled=exportProvenance.disabled=false;exportMol.textContent='Download generated 3D MOL';pngEnabled();},onInvalidate:reason=>{if(model?.kind==='generated_coordinates'){generation++;model=null;stopRotation();pngEnabled();provenance=null;exportMol.disabled=exportProvenance.disabled=true;atomRows.replaceChildren();bondRows.replaceChildren();provenanceView.textContent='Generated source is no longer current.';measurements.reset();draw();message(reason);}}});
  const measurements=mountMeasurements({dom,find,getModel:()=>model?generatedDisplayModel(model):null,getHydrogens:m=>hydrogenDisplay(m,display.hydrogens),isCurrent:()=>model?.kind!=='generated_coordinates'||conformers.assertCurrent()});
  const continuation=mountImageTo3d({button:find('image-continue-3d'),events:globalThis.window,getDocument,open,generate,canOpen:()=>!destroyed&&!dom.querySelector?.('dialog[open]')});
  function message(text,error=false){status.textContent=text;status.classList.toggle('supplied-3d-error',error);}
  function save(bytes,type,name){if(download){download(bytes,type,name);return;}const blob=new Blob([bytes],{type}),url=URL.createObjectURL(blob),a=dom.createElement('a');a.href=url;a.download=name;dom.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function svg(tag,attrs){const n=dom.createElementNS(SVG,tag);for(const [key,value] of Object.entries(attrs))n.setAttribute(key,String(value));return n;}
  function draw(){
    scene.replaceChildren();scene.setAttribute('viewBox','0 0 640 400');
    if(!model){const t=svg('text',{x:320,y:200,'text-anchor':'middle',fill:'#536176'});t.textContent='Open a 3D MOL or generate from the current drawing';scene.append(t);return;}
    const displayModel=generatedDisplayModel(model);
    const space=display.style==='space-filling'&&spaceFillingAvailable(displayModel),hydrogens=hydrogenDisplay(displayModel,display.hydrogens);
    const notice=`${space?'Space-filling: RDKit 2025.09.4 conventional van der Waals radii (Å); orthographic sphere schematic, not an exact molecular surface.':display.style==='space-filling'?'Space-filling unavailable for one or more elements: showing ball-and-stick without guessed radii.':'Ball-and-stick: illustrative sphere sizes.'} ${display.hydrogens==='implicit'?`Implicit display: ${hydrogens.hidden.size} ordinary coordinate-bearing H atoms condensed into parent labels. Special H remains explicit. ${hydrogens.unresolved} H retained because complete matching source annotations are unavailable.`:'Explicit display: only H atoms with existing coordinates are shown.'} ${model.generatedHydrogens?'Embedding-generated H coordinates are retained in a separate display attachment. Special source H remains explicit; recovered attachments are internally checked, not authenticated.':'Missing H counts and positions are not inferred or generated.'} Source coordinates and exports are unchanged; PNG reopening does not restore display settings.`;
    if(displayNote&&displayNote.textContent!==notice)displayNote.textContent=notice;
    const points=projectDisplay(displayModel,view,space),objects=[];
    let renderedAtoms=0,renderedBonds=0,bondStrokes=0;
    for(const bond of displayModel.bonds){if(space||hydrogens.hidden.has(bond.a)||hydrogens.hidden.has(bond.b))continue;renderedBonds++;const a=points[bond.a-1],b=points[bond.b-1];objects.push({z:(a.z+b.z)/2,draw:()=>{const dx=b.x-a.x,dy=b.y-a.y,length=Math.hypot(dx,dy)||1,n=[1.5,4].includes(bond.order)?1:bond.order;
      for(let i=0;i<n;i++){bondStrokes++;const offset=(i-(n-1)/2)*5;scene.append(svg('line',{x1:a.x-dy/length*offset,y1:a.y+dx/length*offset,x2:b.x-dy/length*offset,y2:b.y+dx/length*offset,stroke:'#5c6675','stroke-width':3,...([1.5,4].includes(bond.order)?{'stroke-dasharray':'6 4'}:{})}));}}});}
    for(const a of points.filter(p=>!hydrogens.hidden.has(p.id)))objects.push({z:a.z,draw:()=>{renderedAtoms++;const group=svg('g',{'data-atom':a.id}),title=svg('title',{});title.textContent=`Atom ${a.id}: ${a.element}; charge ${a.charge??'unverified'}, isotope ${a.isotope===0?'not specified':a.isotope??'unverified'}, atom map ${a.atomMap??'not available'}; ${radicalLabel(a)}`;group.append(title,svg('circle',{cx:a.x,cy:a.y,r:a.r,fill:COLORS[a.element]||'#8b7c73',stroke:'#253248','stroke-width':1.5}));const label=svg('text',{x:a.x,y:a.y+4,'text-anchor':'middle',fill:a.element==='H'?'#202737':'#fff','font-size':12,'font-weight':'bold'});label.textContent=`${a.isotope||''}${a.element}${a.charge?`${Math.abs(a.charge)===1?'':Math.abs(a.charge)}${a.charge>0?'+':'−'}`:''}`;if(Object.hasOwn(a,'radicalCode'))label.textContent+=` [RAD${a.radicalCode}]`;if(hydrogens.counts.has(a.id))label.textContent+=` [H${hydrogens.counts.get(a.id)>1?hydrogens.counts.get(a.id):''}]`;group.append(label);scene.append(group);}});
    objects.sort((a,b)=>a.z-b.z).forEach(o=>o.draw());scene.setAttribute('aria-label',`${model.kind==='recovered_coordinates'?'Recovered':model.kind==='generated_coordinates'?'Generated':'Supplied'} 3D molecular coordinate view. Canonical source: ${model.atoms.length} atoms and ${model.bonds.length} bonds. Display model: ${displayModel.atoms.length} atoms and ${displayModel.bonds.length} bonds. Current rendering: ${renderedAtoms} atom spheres, ${renderedBonds} bonds drawn, ${bondStrokes} bond line strokes. ${notice} Use controls or arrow keys to rotate. ${model.kind==='recovered_coordinates'?RECOVERY_WARNING+(provenance?.validation?.schema==='nivram-supplied-3d-validation/1'?' '+SUPPLIED_VALIDATION_WARNING:''):model.kind==='generated_coordinates'?'Generated geometry is a model, not an experimental structure.':provenance?.validation?SUPPLIED_VALIDATION_WARNING:'Geometry is not chemically validated.'}`);
  }
  function cells(row,values){for(const value of values){const td=dom.createElement('td');td.textContent=String(value);row.append(td);}}
  function tables(){atomRows.replaceChildren();bondRows.replaceChildren();for(const a of model.atoms){const row=dom.createElement('tr');cells(row,[a.id,a.element,a.x,a.y,a.z,a.charge??'not validated',a.isotope===0?'not specified':a.isotope??'not validated',a.sourceId??'—',a.atomMap??'—',a.declarations?.CFG??'not declared',a.declarations?.VAL??'not declared',radicalLabel(a)]);atomRows.append(row);}for(const b of model.bonds){const row=dom.createElement('tr');cells(row,[b.id,b.a,b.b,[1.5,4].includes(b.order)?'aromatic':b.order,b.declarations?.CFG??'not declared']);bondRows.append(row);}provenanceView.textContent=JSON.stringify(provenance,null,2);}
  async function validateNative(next,job,current){
    const molSha256=await sha256(next.sourceText,cryptoApi);if(!current())return null;
    job.route='supplied-validation';job.sent=true;
    const data={molText:next.sourceText,molSha256,clientId:job.clientId,jobId:job.jobId,expiresAt:job.expiresAt};
    const result=await post(job.route,data,{signal:job.controller.signal});if(!current())return null;
    return validateSuppliedResult(result,{model:next,...data});
  }
  async function loadBytes(bytes){
    cancelRecovery();conformers.cancel();const ticket=++generation;
    const job={controller:new AbortController(),clientId:randomId(),jobId:randomId(),expiresAt:Date.now()+25000,sent:false,route:'supplied-validation'};recovery=job;
    const current=()=>!destroyed&&ticket===generation&&recovery===job;
    job.timer=setTimeout(()=>{if(current()){generation++;cancelRecovery();message('Supplied validation timed out. Previous view is retained.',true);}},25000);
    if(cancelButton)cancelButton.disabled=false;message('Checking supplied coordinates locally…');
    try{
      let next=parseSuppliedMol(bytes,{allowNativeDeclarations:true}),identity;
      if(next.requiresNativeValidation){
        message('Checking supplied stereo and valence declarations in the bounded local reader…');
        const checked=await validateNative(next,job,current);if(!current()||!checked)return false;
        next=checked.scene;identity=await validatedSuppliedProvenance(next,checked.validation,cryptoApi);
      }else identity=await suppliedProvenance(next,cryptoApi);
      if(!current())return false;if(Date.now()>=job.expiresAt)throw new Error('Coordinate validation deadline expired.');
      recoveredRecord=null;model=next;provenance=identity;view={yaw:0,pitch:0,zoom:1};tables();measurements.reset();draw();exportMol.disabled=exportProvenance.disabled=false;exportMol.textContent='Download original 3D MOL';pngEnabled();
      message(identity.validation?SUPPLIED_VALIDATION_WARNING:`Supplied coordinates: ${model.atoms.length} atoms, ${model.bonds.length} bonds. Chemistry and experimental origin are unverified. The 2D drawing is unchanged.`);return true;
    }catch(error){if(current())message(`Not loaded: ${error.message} Previous view is retained.`,true);return false;}
    finally{if(recovery===job){clearTimeout(job.timer);recovery=null;if(cancelButton)cancelButton.disabled=true;}}
  }
  function transform(action){if(!model)return;const step=Math.PI/12;if(action==='left')view.yaw-=step;if(action==='right')view.yaw+=step;if(action==='up')view.pitch+=step;if(action==='down')view.pitch-=step;if(action==='in')view.zoom=Math.min(5,view.zoom*1.2);if(action==='out')view.zoom=Math.max(0.2,view.zoom/1.2);if(action==='fit')view={yaw:0,pitch:0,zoom:1};draw();}
  function close(){measurements.reset();stopRotation();cancelRecovery();conformers.invalidate('3D view closed. Generate again to bind the current drawing.');generation++;drag=null;if(dialog.open)dialog.close();openButton.focus();}
  function open(){if(!dialog.open)dialog.showModal();closeButton.focus();draw();}
  if(globalThis.window?.addEventListener){on(globalThis.window,'nivram-close-review',()=>{stopRotation();generation++;cancelRecovery();conformers.invalidate('Application close review started.');});on(globalThis.window,'pagehide',()=>{stopRotation();generation++;cancelRecovery();conformers.invalidate('Page closed.');});}
  if(dom.addEventListener)on(dom,'visibilitychange',()=>{if(dom.hidden)stopRotation();});
  on(styleControl,'change',()=>{display.style=styleControl.value==='space-filling'?'space-filling':'ball-and-stick';draw();});
  on(hydrogenControl,'change',()=>{display.hydrogens=hydrogenControl.checked?'explicit':'implicit';measurements.reset();draw();});
  // Change only the next step's sign: preserve camera, timing and the single active loop.
  on(directionControl,'change',()=>{display.direction=directionControl.value==='right-to-left'?'right-to-left':'left-to-right';});
  on(rotateControl,'change',()=>{if(!rotateControl.checked){stopRotation();return;}if(!rotation.start()){stopRotation();message(motionQuery?.matches?'Slow rotation is off because reduced motion is enabled.':'Open a loaded 3D view to start slow rotation.');}});
  if(motionQuery?.addEventListener)on(motionQuery,'change',()=>{if(motionQuery.matches){stopRotation();message('Slow rotation stopped because reduced motion is enabled.');}});
  on(openButton,'click',open);on(closeButton,'click',close);on(dialog,'cancel',event=>{event.preventDefault();close();});on(dialog,'close',()=>{measurements.reset();stopRotation();cancelRecovery();if(!stereoReviewClose)conformers.invalidate('3D view closed.');stereoReviewClose=false;generation++;drag=null;});
  async function generate(options={}){generation++;cancelRecovery();return conformers.generate(options);}
  on(generateButton,'click',generate);on(cancelButton,'click',()=>{generation++;cancelRecovery();conformers.cancel();message('3D operation cancelled. Previous view is retained.');});
  on(input,'change',async()=>{cancelRecovery();conformers.cancel();const file=input.files?.[0];if(!file)return;const ticket=++generation;try{if(file.size>MAX_MOL_BYTES)throw new Error('Choose a MOL file no larger than 256 KiB.');const bytes=new Uint8Array(await file.arrayBuffer());if(ticket!==generation||destroyed)return;await loadBytes(bytes);}catch(error){if(ticket===generation)message(`Not loaded: ${error.message} Previous view is retained.`,true);}finally{input.value='';}});
  for(const button of dialog.querySelectorAll('[data-3d-action]'))on(button,'click',()=>transform(button.dataset['3dAction']));
  on(scene,'keydown',event=>{if(event.ctrlKey||event.altKey||event.metaKey)return;const action={ArrowLeft:'left',ArrowRight:'right',ArrowUp:'up',ArrowDown:'down','+':'in','=':'in','-':'out',Home:'fit'}[event.key];if(action){event.preventDefault();transform(action);}});
  on(scene,'pointerdown',event=>{if(event.button!==0||!model)return;drag={x:event.clientX,y:event.clientY,id:event.pointerId};scene.setPointerCapture(event.pointerId);});
  on(scene,'pointermove',event=>{if(!drag||drag.id!==event.pointerId)return;view.yaw+=(event.clientX-drag.x)/100;view.pitch+=(event.clientY-drag.y)/100;drag={x:event.clientX,y:event.clientY,id:event.pointerId};draw();});
  for(const name of ['pointerup','pointercancel','lostpointercapture'])on(scene,name,()=>{drag=null;});
  on(exportMol,'click',()=>{if(model?.kind==='generated_coordinates'&&!conformers.assertCurrent())return;if(model)save(new TextEncoder().encode(model.sourceText),'chemical/x-mdl-molfile',model.kind==='recovered_coordinates'?'recovered-3d.mol':model.kind==='generated_coordinates'?'generated-3d.mol':'supplied-3d.mol');});
  on(exportProvenance,'click',()=>{if(model?.kind==='generated_coordinates'&&!conformers.assertCurrent())return;if(provenance)save(new TextEncoder().encode(JSON.stringify(provenance,null,2)+'\n'),'application/json',model?.kind==='recovered_coordinates'?'recovered-3d-provenance.json':provenance.generated?'generated-3d-provenance.json':'supplied-3d-provenance.json');});
  async function loadPng(bytes){
    cancelRecovery();conformers.cancel();const ticket=++generation;
    const job={controller:new AbortController(),clientId:randomId(),jobId:randomId(),expiresAt:Date.now()+25000,sent:false};recovery=job;
    const current=()=>!destroyed&&ticket===generation&&recovery===job;
    job.timer=setTimeout(()=>{if(current()){generation++;cancelRecovery();message('Coordinate recovery timed out. Previous view is retained.',true);}},25000);
    if(cancelButton)cancelButton.disabled=false;message('Checking embedded 3D coordinates locally…');
    try{
      if(!(bytes instanceof Uint8Array)||bytes.length>MAX_3D_PNG_BYTES)throw new Error('Choose a PNG no larger than 32 MiB.');
      const record=await extractThreeDRecord(bytes);await validateThreeDRecord(record);
      if(!current())return false;
      const historical=JSON.parse(record.provenanceJson);let next,validation=null;
      if(record.recordedOrigin==='supplied_coordinates'){
        next=parseSuppliedMol(new TextEncoder().encode(record.molText),{allowNativeDeclarations:record.schema==='nivram-png-coordinates/4'});
        // A PNG receipt, checksum or saved identity is never current validation.
        if(record.schema==='nivram-png-coordinates/4'){
          const checked=await validateNative(next,job,current);if(!current()||!checked)return false;
          next=checked.scene;validation=checked.validation;
        }
        next={...next,kind:'recovered_coordinates'};
      }
      else {
        const recordText=JSON.stringify(record),recordSha256=await sha256(recordText,cryptoApi);if(!current())return false;
        job.sent=true;const result=await post('coordinate-recovery',{recordText,recordSha256,clientId:job.clientId,jobId:job.jobId,expiresAt:job.expiresAt},{signal:job.controller.signal});if(!current())return false;
        ({scene:next,validation}=validateRecoveryResult(result,{record,recordSha256,jobId:job.jobId,provenance:historical}));
      }
      if(!current())return false;if(Date.now()>=job.expiresAt)throw new Error('Coordinate validation deadline expired.');
      recoveredRecord=structuredClone(record);model=next;provenance={schema:'nivram-recovered-3d-view/1',origin:'recovered_coordinates',generated:false,generationClaimsVerified:false,sourceBindingReverified:false,warning:RECOVERY_WARNING,historicalProvenance:historical,validation};
      view={yaw:0,pitch:0,zoom:1};tables();measurements.reset();draw();exportMol.disabled=exportProvenance.disabled=false;exportMol.textContent='Download recovered 3D MOL';pngEnabled();message(validation?.schema==='nivram-supplied-3d-validation/1'?SUPPLIED_VALIDATION_WARNING+' '+RECOVERY_WARNING:RECOVERY_WARNING);return true;
    }catch(error){if(current())message(`Not loaded: ${error.message} Previous view is retained.`,true);return false;}
    finally{if(recovery===job){clearTimeout(job.timer);recovery=null;if(cancelButton)cancelButton.disabled=true;}}
  }
  async function exportPngView(){
    if(!model||model.kind==='generated_coordinates'&&!conformers.assertCurrent())return false;
    const ticket=generation,accepted=model,exportTicket=++exportSequence;pendingPngExport=exportTicket;
    if(cancelButton)cancelButton.disabled=false;
    try{
      const context=model.kind==='generated_coordinates'?conformers.exportContext():null;
      if(model.kind==='generated_coordinates'&&!context)throw new Error('Generated source is no longer current.');
      const snapshot={model:structuredClone(model),provenance:structuredClone(provenance),sourceContextText:context?.sourceContextText??null,record:recoveredRecord?structuredClone(recoveredRecord):null,svgText:sceneSnapshot(scene,dom)};
      const transparentBackground=find('supplied-3d-transparent-background')?.checked??false;
      const removeChemistryMetadata=find('supplied-3d-remove-chemistry-metadata')?.checked??false;
      const blob=await pngExport(snapshot,{transparentBackground,removeChemistryMetadata});
      if(destroyed||ticket!==generation||exportTicket!==exportSequence||model!==accepted||model.kind==='generated_coordinates'&&!conformers.assertCurrent())return false;
      save(blob,'image/png','nivram-3d.png');
      // Download dispatch has no save-completion receipt. True means requested, not saved.
      const detail=removeChemistryMetadata?'without recoverable chemistry metadata. Visible artwork and the software label remain.':'with exact MOL coordinates and provenance. Anyone receiving the PNG can recover its metadata.';
      message(`3D PNG prepared ${detail} Complete the save/download dialog and check the file. Saving is not confirmed here.`);return true;
    }catch(error){if(!destroyed&&ticket===generation&&exportTicket===exportSequence)message(`PNG not exported: ${error.message}`,true);return false;}
    finally{if(pendingPngExport===exportTicket)pendingPngExport=null;if(cancelButton&&ticket===generation&&exportTicket===exportSequence&&!recovery&&!conformerPending)cancelButton.disabled=true;}
  }
  on(find('supplied-3d-remove-chemistry-metadata'),'change',()=>{
    const hadPendingExport=pendingPngExport!==null;exportSequence++;pendingPngExport=null;
    if(hadPendingExport&&!recovery&&!conformerPending){
      if(cancelButton)cancelButton.disabled=true;
      message('Metadata choice changed. Export again to use the new choice.');
    }
  });
  on(exportPng,'click',exportPngView);
  on(pngInput,'change',async()=>{cancelRecovery();conformers.cancel();const file=pngInput.files?.[0];if(!file)return;const ticket=++generation;try{if(file.size>MAX_3D_PNG_BYTES)throw new Error('Choose a PNG no larger than 32 MiB.');const bytes=new Uint8Array(await file.arrayBuffer());if(ticket!==generation||destroyed)return;await loadPng(bytes);}catch(error){if(ticket===generation)message(`Not loaded: ${error.message} Previous view is retained.`,true);}finally{pngInput.value='';}});
  draw();return {open,close,loadBytes,loadPng,exportPng:exportPngView,generate,getState:()=>({model,provenance,view:{...view},display:{...display}}),destroy:()=>{continuation.destroy();measurements.destroy();destroyed=true;stopRotation();cancelRecovery();conformers.destroy();generation++;handlers.forEach(fn=>fn());if(dialog.open)dialog.close();}};
}
if(typeof document!=='undefined')mountSupplied3d();
