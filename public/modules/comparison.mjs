import {cloneData,validateProject,prepareRender} from './document.mjs';
import {createJobController} from './jobs.mjs';
import {validMassUnavailable} from './mass-contract.mjs';
const exact=(o,keys)=>o&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).sort().join('|')===[...keys].sort().join('|');
const short=(v,n)=>typeof v==='string'&&v.length<=n;
const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const finiteOrNull=v=>v===null||typeof v==='number'&&Number.isFinite(v);
const warnings=v=>Array.isArray(v)&&v.length<=20&&v.every(s=>short(s,2000));
const fail=()=>{throw new Error('Invalid or mismatched structure comparison result.');};
const randomId=()=>[...crypto.getRandomValues(new Uint8Array(16))].map(x=>x.toString(16).padStart(2,'0')).join('');
const freeze=o=>{if(o&&typeof o==='object'){for(const v of Object.values(o))freeze(v);Object.freeze(o);}return o;};
export function makeComparisonPin(project,label='Pinned drawing',id=randomId()){
  if(!short(label,200)||!/^[0-9a-f]{32}$/.test(id))throw new Error('Invalid comparison pin.');
  return freeze({id,label,project:validateProject(project)});
}
export async function readComparisonFile(file){
  if(!file||!Number.isFinite(file.size)||file.size<1||file.size>4000000||typeof file.text!=='function')throw new Error('Choose one nivraM project smaller than 4 MB.');
  const text=await file.text();if(text.length>4000000)throw new Error('The saved project exceeds the comparison limit.');
  return validateProject(JSON.parse(text));
}
export function validateComparison(result,request){
  if(!exact(result,['schema','requestId','pinId','documentRevision','transformations','pinned','current'])||result.schema!=='nivram-comparison/2'||result.requestId!==request.requestId||result.pinId!==request.pinId||result.documentRevision!==request.documentRevision||!Array.isArray(result.transformations)||result.transformations.length)fail();
  for(const side of ['pinned','current']){
    const r=result[side],g=request[side].graph;
    const unavailable=r&&Object.hasOwn(r,'massUnavailable');
    if(!exact(r,['status','identity','engine','formula','molecularWeight','exactMass','formalCharge','fragmentCount','logP','warnings','preview',...(unavailable?['massUnavailable']:[])])||r.status!==(g.atoms.length?'calculated':'empty'))fail();
    if(unavailable&&!validMassUnavailable(r.massUnavailable,g))fail();
    if(!exact(r.identity,['graphHash','structureHash','smiles','documentRevision'])||!hash(r.identity.graphHash)||!hash(r.identity.structureHash)||!short(r.identity.smiles,100000)||r.identity.documentRevision!==request.documentRevision)fail();
    if(!exact(r.engine,['name','version'])||r.engine.name!=='RDKit'||!short(r.engine.version,80)||!short(r.formula,10000)||!warnings(r.warnings))fail();
    if(!['molecularWeight','exactMass'].every(k=>finiteOrNull(r[k])&&(g.atoms.length&&!unavailable?r[k]!==null:r[k]===null))||!Number.isInteger(r.formalCharge)||Math.abs(r.formalCharge)>4096||!Number.isInteger(r.fragmentCount)||r.fragmentCount<0||r.fragmentCount>g.atoms.length||g.atoms.length>0&&r.fragmentCount===0)fail();
    const l=r.logP;if(!exact(l,['value','status','warnings'])||!finiteOrNull(l.value)||!warnings(l.warnings)||!['calculated','calculated_with_warning','not_applicable'].includes(l.status)||(l.status==='not_applicable')!==(l.value===null))fail();
    const p=r.preview;if(!exact(p,['status','scene','error']))fail();
    if(p.status==='available'){if(!p.scene||typeof p.scene!=='object'||Array.isArray(p.scene)||p.error!==null)fail();}
    else if(p.status==='unavailable'){if(p.scene!==null||!short(p.error,1000)||!p.error)fail();}
    else fail();
  }
  if(result.pinned.engine.version!==result.current.engine.version)fail();
  return cloneData(result);
}
export async function requestComparison(request,{token,signal,fetcher=globalThis.fetch}){
  const response=await fetcher('/api/comparison',{method:'POST',signal,headers:{'Content-Type':'application/json','X-Nivram-Token':token},body:JSON.stringify(request)});
  const text=await response.text();if(text.length>10000000)throw new Error('The comparison response exceeded the supported size.');
  const result=JSON.parse(text);if(!response.ok)throw new Error(short(result.error,1000)?result.error:'Local structure comparison failed.');
  return validateComparison(result,request);
}
const number=(n,d=4)=>Number.isFinite(n)?n.toFixed(d):'Unavailable';
export function comparisonRows(result){
  const a=result.pinned,b=result.current;
  const delta=(x,y,d=4)=>Number.isFinite(x)&&Number.isFinite(y)?number(y-x,d):'Unavailable';
  return [
    ['Formula',a.formula||'Empty',b.formula||'Empty','—'],
    ['Molar mass (g/mol)',number(a.molecularWeight),number(b.molecularWeight),delta(a.molecularWeight,b.molecularWeight)],
    ['Exact mass (Da)',number(a.exactMass,6),number(b.exactMass,6),delta(a.exactMass,b.exactMass,6)],
    ['Formal charge',String(a.formalCharge),String(b.formalCharge),String(b.formalCharge-a.formalCharge)],
    ['Disconnected fragments',String(a.fragmentCount),String(b.fragmentCount),String(b.fragmentCount-a.fragmentCount)],
    ['Calculated LogP (Wildman–Crippen)',number(a.logP.value,3),number(b.logP.value,3),a.logP.status==='calculated'&&b.logP.status==='calculated'?delta(a.logP.value,b.logP.value,3):'Unavailable: applicability warning'],
  ];
}
/** Separate sanitized view, never installed into the live document or scene. */
export function mountComparisonPreview({dom,host,source,result}){
  host.replaceChildren();
  if(result.preview.status!=='available'){host.textContent='Preview unavailable: '+result.preview.error;return false;}
  const scene=result.preview.scene;
  if(scene.smiles!==result.identity.smiles||scene.formula!==result.formula)throw new Error('The preview does not match its property result.');
  const prepared=prepareRender({graph:source.graph,scene},source,dom);
  if(JSON.stringify(prepared.project.style)!==JSON.stringify(source.style))throw new Error('The preview changed the source appearance.');
  const svg=dom.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox',`0 0 ${scene.width} ${scene.height}`);svg.setAttribute('role','img');svg.setAttribute('aria-label','Structure comparison preview');svg.append(prepared.fragment);host.append(svg);return true;
}

export function mountComparison({dom,events,editor,token,fetcher=globalThis.fetch,makeId=randomId,renderPreview=mountComparisonPreview,timeoutMs=20000}){
  const $=id=>dom.getElementById(id),dialog=$('comparison-dialog'),open=$('open-comparison'),pinCurrent=$('comparison-pin-current'),file=$('comparison-file'),unpin=$('comparison-unpin'),refresh=$('comparison-refresh'),cancel=$('comparison-cancel'),close=$('comparison-close'),status=$('comparison-status'),pinLabel=$('comparison-pin-label'),rows=$('comparison-rows'),identity=$('comparison-identity');
  const jobs=createJobController(()=>editor.getSnapshot().revision,{timeoutMs});let pin=null,active=null,epoch=0,closed=false,destroyed=false,lastRevision=editor.getSnapshot().revision;
  const clear=()=>{rows.replaceChildren();identity.textContent='';for(const side of ['pinned','current']){$(`comparison-${side}-view`).replaceChildren();$(`comparison-${side}-notes`).textContent='';$(`comparison-${side}-smiles`).value='';}};
  const sync=()=>{pinLabel.textContent=pin?pin.label:'No structure pinned';unpin.disabled=!pin;refresh.disabled=closed||!pin;pinCurrent.disabled=closed;file.disabled=closed;};
  function invalidate(message){epoch++;jobs.cancelAll(message);active=null;cancel.hidden=true;clear();status.textContent=message;}
  async function runWithPin(chosen,commitPin=false){
    if(closed||destroyed||!chosen)return;
    invalidate('Comparing pinned and current snapshots locally…');const task=jobs.begin('comparison',{input:editor.getSnapshot().document});active=task;cancel.hidden=false;
    const request={requestId:makeId(),pinId:chosen.id,documentRevision:task.revision,pinned:{graph:chosen.project.graph,style:chosen.project.style},current:{graph:task.input.graph,style:task.input.style}};
    try{
      const result=await task.wait(requestComparison(request,{token,signal:task.signal,fetcher}));task.assertCurrent();if(!commitPin&&pin?.id!==chosen.id)throw new Error('The pinned structure changed.');
      // Admit a candidate pin only after both graphs and the response validate.
      // Chemistry/transport failure leaves the preceding accepted pin intact.
      if(commitPin){pin=chosen;sync();}
      for(const side of ['pinned','current']){
        const source=side==='pinned'?chosen.project:task.input,host=$(`comparison-${side}-view`),r=result[side];
        try{renderPreview({dom,host,source,result:r});}catch{host.replaceChildren();host.textContent='Preview unavailable: the returned drawing could not be safely validated.';}
        $(`comparison-${side}-smiles`).value=r.identity.smiles;
        $(`comparison-${side}-notes`).textContent=`RDKit ${r.engine.version}. ${r.warnings.join(' ')} ${r.logP.warnings.join(' ')}`;
      }
      for(const values of comparisonRows(result)){const row=dom.createElement('tr');values.forEach((value,i)=>{const cell=dom.createElement(i?'td':'th');if(!i)cell.setAttribute('scope','row');cell.textContent=value;row.append(cell);});rows.append(row);}
      identity.textContent=result.pinned.identity.smiles===result.current.identity.smiles?'Same RDKit canonical isomeric SMILES in these snapshots. This is not experimental identity verification.':'Different RDKit canonical isomeric SMILES in these snapshots. No atom correspondence has been inferred.';
      status.textContent=`Compared with current drawing revision ${task.revision}. Differences are current minus pinned. The drawing and pinned snapshot are unchanged.`;
    }catch(error){if(active===task&&editor.getSnapshot().revision===task.revision){clear();status.textContent=error.name==='TimeoutError'?'Comparison timed out. No result was adopted.':error.message;}}
    finally{task.finish();if(active===task){active=null;cancel.hidden=true;}}
  }
  const run=()=>runWithPin(pin);
  function setPin(project,label){if(closed||destroyed)return;const candidate=makeComparisonPin(project,label,makeId());void runWithPin(candidate,true);}
  const pinNow=()=>setPin(editor.getSnapshot().document,'Pinned current drawing snapshot');
  async function chooseFile(){
    if(closed||destroyed)return;const selected=file.files?.[0];file.value='';if(!selected)return;
    invalidate('Reading the selected saved project locally…');const ticket=epoch;
    try{const project=await readComparisonFile(selected);if(ticket!==epoch||closed||destroyed)return;setPin(project,`Saved project: ${String(selected.name||'unnamed').slice(0,160)}`);}
    catch(error){if(ticket===epoch&&!closed&&!destroyed){status.textContent=`Pin not replaced: ${error.message}`;sync();}}
  }
  const removePin=()=>{invalidate('Pin removed. The current drawing is unchanged.');pin=null;sync();};
  const abort=()=>invalidate('Comparison cancelled. Pin retained; no result adopted.');
  const show=()=>{if(closed||destroyed)return;if(!dialog.open)dialog.showModal();if(pin)void run();else status.textContent='Pin the current drawing or choose a saved nivraM project.';};
  const hide=()=>{invalidate('Comparison closed. Pin retained for this session.');if(dialog.open)dialog.close();};
  const closing=()=>{closed=true;hide();sync();};const reopen=()=>{closed=false;sync();};
  const unsubscribe=editor.subscribe(snapshot=>{if(snapshot.revision!==lastRevision){lastRevision=snapshot.revision;invalidate('The current drawing changed. Refresh comparison for current results.');}});
  open.addEventListener('click',show);pinCurrent.addEventListener('click',pinNow);file.addEventListener('change',chooseFile);unpin.addEventListener('click',removePin);refresh.addEventListener('click',run);cancel.addEventListener('click',abort);close.addEventListener('click',hide);dialog.addEventListener('cancel',hide);dialog.addEventListener('close',abort);events.addEventListener('nivram-close-review',closing);events.addEventListener('nivram-close-review-ended',reopen);events.addEventListener('pagehide',hide);sync();
  return {run,setPin,chooseFile,getPin:()=>pin?cloneData(pin):null,destroy(){destroyed=true;hide();unsubscribe();open.removeEventListener('click',show);pinCurrent.removeEventListener('click',pinNow);file.removeEventListener('change',chooseFile);unpin.removeEventListener('click',removePin);refresh.removeEventListener('click',run);cancel.removeEventListener('click',abort);close.removeEventListener('click',hide);dialog.removeEventListener('cancel',hide);dialog.removeEventListener('close',abort);events.removeEventListener('nivram-close-review',closing);events.removeEventListener('nivram-close-review-ended',reopen);events.removeEventListener('pagehide',hide);pin=null;}};
}
