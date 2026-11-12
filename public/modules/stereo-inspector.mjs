import {createJobController} from './jobs.mjs';
const exact=(o,keys)=>o&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).sort().join('|')===[...keys].sort().join('|');
const bad=()=>{throw new Error('Invalid or mismatched stereochemistry inspection result.');};
const short=(s,max)=>typeof s==='string'&&s.length<=max;
export function validateStereoInspection(result,request){
  if(!exact(result,['schema','status','inputIdentity','engine','sites','unsupportedCount','transformations','warnings','requestId'])||result.schema!=='nivram-stereo-inspector/1'||result.requestId!==request.requestId)bad();
  const identity=result.inputIdentity,engine=result.engine,graph=request.graph;
  if(!exact(identity,['graphHash','structureHash','smiles','documentRevision'])||identity.documentRevision!==request.documentRevision||!['graphHash','structureHash'].every(k=>typeof identity[k]==='string'&&/^[a-f0-9]{64}$/.test(identity[k]))||!short(identity.smiles,100000))bad();
  if(!exact(engine,['name','version','method'])||engine.name!=='RDKit'||!short(engine.version,80)||engine.method!=='FindPotentialStereo + rdCIPLabeler.AssignCIPLabels')bad();
  if(result.status!==(graph.atoms.length?'inspected':'empty')||!Array.isArray(result.sites)||result.sites.length>graph.atoms.length+graph.bonds.length||!Number.isInteger(result.unsupportedCount)||result.unsupportedCount<0||result.unsupportedCount+result.sites.length>graph.atoms.length+graph.bonds.length||!Array.isArray(result.transformations)||result.transformations.length||!Array.isArray(result.warnings)||result.warnings.length>10||result.warnings.some(w=>!short(w,1000)))bad();
  const seen=new Set();
  for(const site of result.sites){
    if(!exact(site,['kind','id','index','status','label','reason'])||!['atom','bond'].includes(site.kind)||!Number.isInteger(site.index)||site.index<0)bad();
    const source=(site.kind==='atom'?graph.atoms:graph.bonds)[site.index];
    if(!source||source.id!==site.id||!short(site.id,80)||seen.has(site.kind+':'+site.id)||site.kind==='bond'&&source.order!==2)bad();
    seen.add(site.kind+':'+site.id);
    if(site.status==='assigned'){
      if(site.reason!=='specified'||!(site.kind==='atom'?['R','S']:['E','Z']).includes(site.label))bad();
    }else if(site.status==='unresolved'){
      if(site.label!==null||!['unspecified','unknown'].includes(site.reason))bad();
    }else if(site.status==='unsupported'){
      if(site.label!==null||!['unsupported_label','unsupported_type'].includes(site.reason))bad();
    }else bad();
  }
  return structuredClone(result);
}

export async function requestStereoInspection(request,{signal,token,fetcher=globalThis.fetch}){
  const response=await fetcher('/api/stereo-inspector',{method:'POST',signal,headers:{'Content-Type':'application/json','X-Nivram-Token':token},body:JSON.stringify(request)});
  const text=await response.text();if(text.length>1000000)throw new Error('The inspection response exceeded the supported size.');
  const result=JSON.parse(text);
  if(!response.ok)throw new Error(short(result.error,1000)?result.error:'Local stereochemistry inspection failed.');
  return validateStereoInspection(result,request);
}
const randomId=()=>[...crypto.getRandomValues(new Uint8Array(16))].map(n=>n.toString(16).padStart(2,'0')).join('');
/** Ephemeral results only. No document commit, renderer, layout or file writes. */
export function mountStereoInspector({dom,events,editor,token,selectTarget=()=>{},fetcher=globalThis.fetch,makeId=randomId,timeoutMs=15000}){
  const $=id=>dom.getElementById(id),inspect=$('inspect-stereo'),cancel=$('cancel-stereo-inspector'),status=$('stereo-inspector-status'),list=$('stereo-inspector-list'),details=$('stereo-inspector-details');
  const jobs=createJobController(()=>editor.getSnapshot().revision,{timeoutMs});
  let active=null,binding=null,closed=false,destroyed=false,lastRevision=editor.getSnapshot().revision;
  const clearResults=()=>{binding=null;list.replaceChildren();details.textContent='';};
  function invalidate(message){jobs.cancelAll(message);active=null;cancel.hidden=true;clearResults();status.textContent=message;}
  async function run(){
    if(closed||destroyed)return;
    clearResults();const task=jobs.begin('stereo-inspector',{input:editor.getSnapshot().document});active=task;cancel.hidden=false;status.textContent='Inspecting the current structure locally…';
    const request={graph:task.input.graph,documentRevision:task.revision,requestId:makeId()};
    try{
      const result=await task.wait(requestStereoInspection(request,{signal:task.signal,token,fetcher}));task.assertCurrent();
      binding={revision:task.revision,requestId:request.requestId};
      const assigned=result.sites.filter(s=>s.status==='assigned').length,unresolved=result.sites.filter(s=>s.status==='unresolved').length,unsupported=result.unsupportedCount+result.sites.filter(s=>s.status==='unsupported').length;
      status.textContent=result.status==='empty'?'The drawing is empty.':`${assigned} labeled; ${unresolved} unresolved; ${unsupported} unsupported. No stereochemistry was changed.`;
      if(result.status!=='empty'&&!result.sites.length&&!result.unsupportedCount)status.textContent='No potential stereo sites were detected within the supported RDKit scope. This is not a comprehensive stereochemistry validation.';
      for(const site of result.sites){
        const row=dom.createElement('li'),button=dom.createElement('button');button.type='button';
        const name=site.kind==='atom'?`Atom ${site.index+1}`:`Bond ${site.index+1}`;
        const notation=site.label??(site.status==='unresolved'?(site.kind==='atom'?'R/S?':'E/Z?'):'not supported');
        button.textContent=`Select ${name.toLowerCase()}`;button.setAttribute('aria-label',`Select ${name.toLowerCase()} (${site.id})`);
        const label=dom.createElement('span');label.textContent=`${name} (${site.id}): ${notation} · ${site.reason.replaceAll('_',' ')}. `;
        row.append(label,button);list.append(row);
        button.addEventListener('click',()=>{if(!closed&&!destroyed&&binding?.requestId===request.requestId&&editor.getSnapshot().revision===task.revision)selectTarget(site,task.revision);});
      }
      details.textContent=`${result.engine.name} ${result.engine.version}; ${result.engine.method}. ${result.warnings.join(' ')}`;
    }catch(error){
      if(active===task&&editor.getSnapshot().revision===task.revision){clearResults();status.textContent=error.name==='TimeoutError'?'Inspection timed out. No result was adopted; the drawing is unchanged.':error.message;}
    }finally{task.finish();if(active===task){active=null;cancel.hidden=true;}}
  }
  const abort=()=>invalidate('Inspection cancelled. No result was adopted; the drawing is unchanged.');
  const close=()=>{closed=true;inspect.disabled=true;invalidate('Inspection paused while closing is reviewed.');};
  const reopen=()=>{closed=false;inspect.disabled=false;status.textContent='Inspect the current structure for supported R/S and E/Z labels.';};
  const unsubscribe=editor.subscribe(snapshot=>{if(snapshot.revision!==lastRevision){lastRevision=snapshot.revision;invalidate('The drawing changed. Inspect again for current results.');}});
  inspect.addEventListener('click',run);cancel.addEventListener('click',abort);events.addEventListener('nivram-close-review',close);events.addEventListener('nivram-close-review-ended',reopen);events.addEventListener('pagehide',abort);
  return {run,destroy(){destroyed=true;abort();unsubscribe();inspect.removeEventListener('click',run);cancel.removeEventListener('click',abort);events.removeEventListener('nivram-close-review',close);events.removeEventListener('nivram-close-review-ended',reopen);events.removeEventListener('pagehide',abort);}};
}
