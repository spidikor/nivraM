import {cloneData,validateProject} from './document.mjs';

const signature=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
const same=(a,b)=>signature(a)===signature(b);
const chemicalFields=graph=>({...graph,atoms:graph.atoms.map(({x,y,...atom})=>atom)});

// Coordinates are the only document data a cleanup backend may change. The
// chemistry backend also checks canonical isomeric identity independently.
export function prepareCleanupPreview({source,result,selected=[],backend,getRevision,prepare,commit}){
  const captured=cloneData(source),response=cloneData(result),selection=cloneData(selected),identity=cloneData(backend);
  if(!Number.isInteger(captured.revision)||captured.revision<0)throw new Error('Invalid cleanup source revision.');
  const before=validateProject(captured.document),candidate=validateProject({...before,graph:response.graph});
  const ids=new Set(before.graph.atoms.map(atom=>atom.id));
  if(!Array.isArray(selection)||new Set(selection).size!==selection.length||selection.some(id=>!ids.has(id)))throw new Error('Cleanup selection contains an invalid or repeated atom.');
  if(!same(chemicalFields(before.graph),chemicalFields(candidate.graph)))throw new Error('Cleanup changed chemical atom, bond or stereochemistry data. The proposed layout was rejected.');
  const chosen=new Set(selection.length?selection:ids);let changedAtoms=0;
  for(let i=0;i<before.graph.atoms.length;i++){
    const a=before.graph.atoms[i],b=candidate.graph.atoms[i],changed=a.x!==b.x||a.y!==b.y;
    if(changed&&!chosen.has(a.id))throw new Error('Cleanup moved atoms outside the selection. The proposed layout was rejected.');
    if(changed)changedAtoms++;
  }
  for(const key of ['id','name','version','limitations'])if(typeof identity[key]!=='string'||!identity[key].trim()||identity[key].length>1000)throw new Error('The cleanup backend has no valid identity or limitations.');
  const prepared=prepare(cloneData(response),cloneData(before));
  if(!same(prepared.project,{...before,graph:candidate.graph}))throw new Error('Cleanup changed appearance or other document settings. The proposed layout was rejected.');
  let state='pending';
  return Object.freeze({
    revision:captured.revision,backend:Object.freeze(identity),scope:selection.length?'selection':'whole',changedAtoms,
    scene:prepared.scene,fragment:prepared.fragment,
    get state(){return state;},
    cancel(){if(state==='pending')state='cancelled';},
    apply(){
      if(state!=='pending')throw new Error('This cleanup preview is no longer available. Create a new preview.');
      if(getRevision()!==captured.revision){state='cancelled';throw new Error('The document changed after this cleanup preview. Create a new preview before applying.');}
      // The prepared fragment is for inspection only; commit re-prepares the
      // private captured response, so preview DOM cannot modify saved chemistry.
      commit(cloneData(response),{baseProject:cloneData(before),expectedRevision:captured.revision,shouldFit:selection.length===0,mode:'record'});
      state='applied';
    }
  });
}
