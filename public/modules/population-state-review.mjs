import {cloneData,prepareRender,validateProject} from './document.mjs';
import {createJobController} from './jobs.mjs';
import {populationTable} from './population-view.mjs';

export const MAX_STRUCTURES_BYTES=2*1024*1024;
const MAX_RESPONSE_BYTES=8*1024*1024,encoder=new TextEncoder();
const LIMITATIONS=[
  'Explicit whole-drawing replacement with a supplied 2D depiction; model applicability to the current drawing remains unverified.',
  'Mapped core, charge, hydrogen stoichiometry and supported chemical identity were checked; assignment of energies to structures was not independently validated.',
  'This operation does not adopt or validate a 3D conformer geometry.'
];
const digest=async text=>[...new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256',encoder.encode(text)))].map(byte=>byte.toString(16).padStart(2,'0')).join('');
const canonical=value=>Array.isArray(value)?`[${value.map(canonical).join(',')}]`:value&&typeof value==='object'?`{${Object.keys(value).sort().map(key=>`${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`:JSON.stringify(value);
const same=(a,b)=>canonical(a)===canonical(b);
const requireThat=(condition,message)=>{if(!condition)throw new Error(message);};
const text=(value,label,max=8192)=>{requireThat(typeof value==='string'&&value.trim().length>0&&value.length<=max&&!value.includes('\0'),`${label} must be bounded nonempty text without NUL characters.`);return value;};
const exact=(value,keys,label)=>requireThat(value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key)),`${label} has an unsupported schema.`);
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const freeze=value=>{if(value&&typeof value==='object'){for(const child of Object.values(value))freeze(child);Object.freeze(value);}return value;};

function validateStructures(raw,model,modelInputHash){
  exact(raw,['schemaVersion','fileKind','modelInputHash','structures'],'State structures file');
  requireThat(raw.schemaVersion===1&&raw.fileKind==='nivram-population-state-structures','Choose a version 1 population state structures file.');
  requireThat(raw.modelInputHash===modelInputHash,'The structures file is not bound to these exact model bytes.');
  requireThat(Array.isArray(raw.structures)&&raw.structures.length>0&&raw.structures.length<=4096,'The structures file requires a bounded nonempty structures list.');
  const modelStates=new Map(model.states.map(state=>[state.state_id,state])),states=new Map();
  for(const entry of raw.structures){
    exact(entry,['stateId','structureId','format','source','coreAtomMaps'],'State structure');
    text(entry.stateId,'State ID');text(entry.structureId,'Structure ID');
    const state=modelStates.get(entry.stateId);
    requireThat(state&&state.structure_id===entry.structureId&&!states.has(entry.stateId),'A structure has a missing, duplicate or mismatched model state identity.');
    requireThat(['smiles','mol'].includes(entry.format),'Only explicit SMILES or Molfile sources are supported.');
    text(entry.source,'Structure source',MAX_STRUCTURES_BYTES);
    requireThat(Array.isArray(entry.coreAtomMaps)&&entry.coreAtomMaps.length>0&&entry.coreAtomMaps.length<=512,'A structure requires bounded explicit atomic core mappings.');
    const labels=new Set(),maps=new Set();
    for(const row of entry.coreAtomMaps){
      exact(row,['atomId','mapNum'],'Atomic core mapping');text(row.atomId,'Core atom ID');
      requireThat(!labels.has(row.atomId)&&Number.isInteger(row.mapNum)&&row.mapNum>=1&&row.mapNum<=999999&&!maps.has(row.mapNum),'Atomic core labels and positive map numbers must be unique.');
      labels.add(row.atomId);maps.add(row.mapNum);
    }
    requireThat(same([...labels].sort(),[...state.atom_ids].sort()),'The structure core labels do not match the supplied model.');
    states.set(entry.stateId,freeze(entry));
  }
  return states;
}

async function readResponse(response){
  const declared=response.headers.get('content-length');
  if(declared!==null&&Number(declared)>MAX_RESPONSE_BYTES){await response.body?.cancel();throw new Error('The state preview response exceeds its size limit.');}
  requireThat(response.body,'The state preview response is empty.');
  const reader=response.body.getReader(),chunks=[];let size=0;
  try{
    while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;requireThat(size<=MAX_RESPONSE_BYTES,'The state preview response exceeds its size limit.');chunks.push(value);}
    const bytes=new Uint8Array(size);let position=0;for(const chunk of chunks){bytes.set(chunk,position);position+=chunk.length;}
    return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
  }catch(error){await reader.cancel().catch(()=>{});throw error;}finally{reader.releaseLock();}
}

async function validateReceipt(raw,{request,selected,reference,state,referenceState,graph,modelSignature}){
  const receipt=cloneData(raw);
  exact(receipt,['schemaVersion','requestId','documentRevision','graphHash','modelInputHash','structuresInputHash','modelSignature','stateId','structureId','referenceStateId','source','referenceSource','coreAtomMaps','formalCharge','relativeProtons','observedHydrogenDifference','identity','referenceIdentity','depiction','modelApplicability','thermodynamics','conformerGeometry','limitations'],'State preview receipt');
  for(const key of ['schemaVersion','requestId','documentRevision','graphHash','modelInputHash','structuresInputHash','stateId'])requireThat(receipt[key]===request[key],`State preview ${key} does not match the current request.`);
  requireThat(receipt.modelSignature===modelSignature&&receipt.structureId===state.structure_id&&receipt.referenceStateId===referenceState.state_id,'The state preview changed its model or state identities.');
  requireThat(receipt.formalCharge===state.formal_charge&&receipt.relativeProtons===state.relative_protons&&receipt.observedHydrogenDifference===state.relative_protons-referenceState.relative_protons,'The state preview does not match the declared charge and proton relationships.');
  requireThat(receipt.depiction==='2d'&&receipt.modelApplicability==='unverified'&&receipt.thermodynamics==='supplied; not independently validated'&&receipt.conformerGeometry==='not adopted','The state preview omitted its scientific limitations.');
  requireThat(same(receipt.limitations,LIMITATIONS),'The state preview must retain its exact scientific limitations.');
  for(const [key,entry] of [['source',selected],['referenceSource',reference]]){
    exact(receipt[key],['format','sha256'],'Structure source receipt');
    requireThat(receipt[key].format===entry.format&&receipt[key].sha256===await digest(entry.source),'The state preview changed its selected or reference structure source.');
  }
  for(const key of ['identity','referenceIdentity']){
    const identity=receipt[key];exact(identity,['canonicalIsomericSmiles','structureHash'],'Structure identity');
    text(identity.canonicalIsomericSmiles,'Canonical isomeric SMILES',32768);
    requireThat(hash(identity.structureHash)&&identity.structureHash===await digest(identity.canonicalIsomericSmiles),'The state preview contains an invalid chemical identity digest.');
  }
  requireThat(Array.isArray(receipt.coreAtomMaps)&&receipt.coreAtomMaps.length===selected.coreAtomMaps.length,'The state preview changed the atomic core.');
  const expected=new Map(selected.coreAtomMaps.map(row=>[row.atomId,row.mapNum])),atoms=new Map(graph.atoms.map(atom=>[atom.id,atom])),used=new Set();
  for(const row of receipt.coreAtomMaps){
    exact(row,['atomId','mapNum','graphAtomId','element','isotope'],'Preview atomic core mapping');
    const atom=atoms.get(row.graphAtomId);
    requireThat(expected.has(row.atomId)&&expected.get(row.atomId)===row.mapNum&&!used.has(row.graphAtomId)&&atom&&atom.mapNum===row.mapNum&&atom.symbol===row.element&&(atom.isotope??0)===row.isotope,'The state preview core does not correspond to its returned graph.');
    expected.delete(row.atomId);used.add(row.graphAtomId);
  }
  requireThat(graph.atoms.length>0&&graph.atoms.every(atom=>used.has(atom.id)||atom.symbol==='H'&&(atom.isotope??0)===0&&(atom.mapNum??0)===0),'The state preview contains an unmapped heavy atom, isotope or atom map.');
  requireThat(graph.atoms.reduce((total,atom)=>total+(atom.charge??0),0)===receipt.formalCharge,'The state preview graph changed its declared formal charge.');
  return freeze(receipt);
}

/** Explicit supplied structures only. Drawing hashes bind freshness, not chemical correspondence. */
export function createPopulationStateReview({editor,token='',result,fetcher=globalThis.fetch,dom=globalThis.document,timeoutMs=25000}){
  const envelope=result?.envelope;
  populationTable(envelope,70); // Require the existing accepted, immutable calculation.
  const initial=editor.getSnapshot(),base=cloneData(initial.document);
  requireThat(initial.revision===envelope.documentRevision,'The drawing changed. Reopen the supplied model first.');
  requireThat(typeof result.modelText==='string'&&hash(result.modelInputHash),'The exact supplied model text is required for state review.');
  const modelText=result.modelText,modelInputHash=result.modelInputHash,modelName=String(result.name).slice(0,256);
  const jobs=createJobController(()=>editor.getSnapshot().revision,{timeoutMs});
  let stopped=false,stale=false,committing=false,sidecar=null,draft=null,status='idle',generation=0,pendingRelease=null;
  function assertCurrent(task,epoch){
    requireThat(!stopped,'Population state review is closed.');
    requireThat(!stale&&editor.getSnapshot().revision===initial.revision,'The drawing changed. Reopen the supplied model before adopting a state.');
    requireThat(epoch===generation,'The state selection changed. Preview it again.');
    task?.assertCurrent();
  }
  function cancel(message='Population state preview cancelled. The drawing is unchanged.'){
    generation++;jobs.cancelAll(message);pendingRelease?.();pendingRelease=null;draft?.release();draft=null;status=sidecar?'loaded':'idle';
  }
  const unsubscribe=editor.subscribe(snapshot=>{if(snapshot.revision!==initial.revision&&!committing){stale=true;cancel('The drawing changed. Reopen the supplied model before adopting a state.');}});
  const exposed=()=>draft?{graph:cloneData(draft.graph),scene:cloneData(draft.scene),fragment:draft.fragment.cloneNode(true),populationState:cloneData(draft.receipt)}:null;
  function hasState(stateId){return !stopped&&!stale&&editor.getSnapshot().revision===initial.revision&&Boolean(sidecar?.states.has(stateId)&&sidecar.states.has(envelope.model.reference_state_id));}
  return Object.freeze({
    get status(){return status;},get preview(){return exposed();},get structuresName(){return sidecar?.name??null;},hasState,cancel,
    dispose(){if(!stopped){cancel();stopped=true;sidecar=null;status='idle';unsubscribe();}},
    async loadText(structuresText,{name='Supplied state structures'}={}){
      cancel();sidecar=null;status='idle';const epoch=generation;
      assertCurrent(null,epoch);
      requireThat(typeof structuresText==='string'&&encoder.encode(structuresText).byteLength<=MAX_STRUCTURES_BYTES,'Choose a structures JSON file no larger than 2 MiB.');
      const states=validateStructures(JSON.parse(structuresText),envelope.model,modelInputHash);
      const task=jobs.begin('population-state-load');status='loading';
      try{
        const [actualModelHash,structuresInputHash]=await task.wait(Promise.all([digest(modelText),digest(structuresText)]));assertCurrent(task,epoch);
        requireThat(actualModelHash===modelInputHash&&same(JSON.parse(modelText),envelope.model),'The exact model bytes do not match the accepted population result.');
        sidecar={name:String(name).slice(0,256),text:structuresText,hash:structuresInputHash,states};status='loaded';
        return {name:sidecar.name,structuresInputHash,stateIds:[...states.keys()],referenceAvailable:states.has(envelope.model.reference_state_id)};
      }catch(error){if(epoch===generation)status='idle';throw error;}finally{task.finish();}
    },
    async previewState(stateId){
      cancel();const epoch=generation;assertCurrent(null,epoch);
      requireThat(hasState(stateId),'Supply explicit structures for both this state and the model reference state before previewing.');
      const selected=sidecar.states.get(stateId),reference=sidecar.states.get(envelope.model.reference_state_id),state=envelope.model.states.find(row=>row.state_id===stateId),referenceState=envelope.model.states.find(row=>row.state_id===envelope.model.reference_state_id);
      const task=jobs.begin('population-state-preview'),requestId=globalThis.crypto.randomUUID();status='previewing';
      let released=false;
      const release=()=>{
        if(released)return;released=true;
        const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),3000);
        Promise.resolve().then(()=>fetcher('/api/populations/cancel-state',{method:'POST',keepalive:true,signal:controller.signal,headers:{'Content-Type':'application/json','X-Nivram-Token':token},body:JSON.stringify({schemaVersion:1,requestId})})).catch(()=>{}).finally(()=>clearTimeout(timer));
      };
      pendingRelease=release;task.signal.addEventListener('abort',release,{once:true});
      try{
        const request={schemaVersion:1,requestId,documentRevision:initial.revision,graphHash:envelope.graphHash,modelText,modelInputHash,structuresText:sidecar.text,structuresInputHash:sidecar.hash,stateId,style:cloneData(base.style)};
        const body=JSON.stringify(request);requireThat(encoder.encode(body).byteLength<=8*1024*1024,'The encoded state preview request exceeds its size limit.');
        const response=await task.wait(fetcher('/api/populations/prepare-state',{method:'POST',signal:task.signal,headers:{'Content-Type':'application/json','X-Nivram-Token':token},body}));
        const raw=await task.wait(readResponse(response));assertCurrent(task,epoch);
        requireThat(response.ok,typeof raw?.error==='string'?raw.error:'The local state preview failed.');
        exact(raw,['graph','scene','populationState'],'State preview response');
        const prepared=prepareRender(raw,base,dom);
        requireThat(same(prepared.project.style,base.style),'The state preview changed the current drawing style.');
        const receipt=await task.wait(validateReceipt(raw.populationState,{request,selected,reference,state,referenceState,graph:prepared.project.graph,modelSignature:envelope.modelSignature}));assertCurrent(task,epoch);
        draft={graph:cloneData(prepared.project.graph),scene:prepared.scene,fragment:prepared.fragment,receipt,release};status='ready';return exposed();
      }catch(error){release();if(epoch===generation){draft=null;status=sidecar?'loaded':'idle';}throw error;}
      finally{task.signal.removeEventListener('abort',release);task.finish();if(pendingRelease===release)pendingRelease=null;}
    },
    async adopt({index,confirmed=false}={}){
      const epoch=generation;assertCurrent(null,epoch);
      requireThat(draft&&status==='ready','Preview the selected 2D state before adopting it.');
      requireThat(confirmed===true,'Confirm replacement of the whole drawing with this supplied 2D state.');
      const table=populationTable(envelope,index),row=table.rows.find(row=>row.stateId===draft.receipt.stateId);
      requireThat(table.status==='computed'&&row,'The selected pH has no supplied-model fraction. Choose a computed sample before adopting.');
      const adoption={schemaVersion:1,kind:'supplied-population-state-2d',model:{name:modelName,sha256:modelInputHash},structures:{name:sidecar.name,sha256:sidecar.hash},
        selectedSample:{index:table.index,ph:table.ph,stateId:row.stateId,fraction:row.fraction,percentage:row.percentage,normalizationScope:table.normalizationScope,representedMass:table.representedMass,omittedMass:table.omittedMass,omittedMassSource:table.omittedMassSource,ensembleComplete:table.ensembleComplete,equilibriumScope:table.equilibriumScope},
        conditions:cloneData(envelope.model.conditions),provenance:cloneData(envelope.model.provenance),receipt:cloneData(draft.receipt)};
      // Array-valued legacy analysis is preserved under previousAnalysis; object keys remain intact.
      const analysis=Array.isArray(base.analysis)?{previousAnalysis:cloneData(base.analysis)}:cloneData(base.analysis??{});
      const candidate=validateProject({...base,graph:cloneData(draft.graph),analysis:{...analysis,populationAdoption:adoption}});
      const task=jobs.begin('population-state-adopt');status='adopting';
      try{
        assertCurrent(task,epoch);committing=true;
        const accepted=await editor.openProject(candidate,{expectedRevision:initial.revision,signal:task.signal});
        requireThat(accepted===true,'The state was not committed. The preceding drawing is retained.');
        // The successful commit changes revision synchronously. Do not invalidate it as a stale response.
        draft?.release();draft=null;pendingRelease=null;sidecar=null;stale=true;status='adopted';return cloneData(adoption);
      }catch(error){if(epoch===generation){draft?.release();draft=null;status=sidecar?'loaded':'idle';}throw error;}
      finally{committing=false;task.finish();}
    }
  });
}
