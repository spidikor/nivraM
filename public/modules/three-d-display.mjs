import {parseSuppliedMol,MAX_MOL_BYTES} from './supplied-3d.mjs';
// Finite conventional display radii, angstroms. Read from RDKit 2025.09.4
// Chem.GetPeriodicTable().GetRvdw; version-bound numerical receipt accompanies candidate.
// No invented fallback for unsupported elements, and no claim of exact Bondi values.
export const VDW_RADII=Object.freeze({"H":1.2,"He":1.4,"C":1.7,"N":1.6,"O":1.55,"F":1.5,"Ne":1.54,"Si":2.1,"P":1.95,"S":1.8,"Cl":1.8,"Ar":1.88,"Br":1.9,"Kr":2.02,"I":2.1,"Xe":2.16});
// Read-only syntax annotations, never a new accepted scene or validation receipt.
// Incomplete worker scenes cannot establish that an absent map/declaration is zero.
export function displayAnnotations(model){
  try{
    if(typeof model.sourceText!=='string'||model.sourceText.length>MAX_MOL_BYTES)return null;
    const source=parseSuppliedMol(new TextEncoder().encode(model.sourceText),{allowNativeDeclarations:true});
    if(source.atoms.length!==model.atoms.length||source.bonds.length!==model.bonds.length)return null;
    for(let i=0;i<source.atoms.length;i++){
      const a=source.atoms[i],b=model.atoms[i];
      if(['id','element','x','y','z','charge','isotope'].some(k=>a[k]!==b[k])||(b.atomMap!==undefined&&a.atomMap!==b.atomMap))return null;
    }
    for(let i=0;i<source.bonds.length;i++)if(['id','a','b','order'].some(k=>source.bonds[i][k]!==model.bonds[i][k]))return null;
    return source;
  }catch{return null;}
}
export function hydrogenDisplay(model,mode='explicit'){
  const hidden=new Set(),counts=new Map();let unresolved=0;
  if(mode!=='implicit')return {hidden,counts,unresolved};
  if(model.generatedHydrogens&&Number.isInteger(model.displaySourceAtomCount)){
    // Preserve existing source-H condensation and add only new attachment IDs.
    const n=model.displaySourceAtomCount;
    const canonical={...model,atoms:model.atoms.slice(0,n),bonds:model.bonds.filter(b=>b.a<=n&&b.b<=n)};
    delete canonical.generatedHydrogens;
    const source=hydrogenDisplay(canonical,mode);
    for(const id of source.hidden)hidden.add(id);
    for(const [id,count] of source.counts)counts.set(id,count);
    unresolved=source.unresolved;
    for(const a of model.atoms)if(a.generatedId){hidden.add(a.id);counts.set(a.parentId,(counts.get(a.parentId)||0)+1);}
    return {hidden,counts,unresolved};
  }
  const annotated=displayAnnotations(model);
  if(!annotated)return {hidden,counts,unresolved:model.atoms.filter(a=>a.element==='H').length};
  for(const atom of annotated.atoms){
    const original=model.atoms[atom.id-1];
    // Both source declarations and any additional accepted scene semantics survive.
    if(atom.element!=='H'||atom.charge!==0||atom.isotope!==0||atom.atomMap||Object.hasOwn(atom,'radicalCode')||Object.hasOwn(original,'radicalCode')||original.radicalElectrons||original.radical||Object.keys(atom.declarations||{}).length||Object.keys(original.declarations||{}).length)continue;
    const bonds=annotated.bonds.filter(b=>b.a===atom.id||b.b===atom.id);
    if(bonds.length!==1||bonds[0].order!==1||Object.keys(bonds[0].declarations||{}).length||Object.keys(model.bonds[bonds[0].id-1].declarations||{}).length)continue;
    const parentId=bonds[0].a===atom.id?bonds[0].b:bonds[0].a;
    const parent=annotated.atoms.find(a=>a.id===parentId);if(!parent||parent.element==='H')continue;
    hidden.add(atom.id);counts.set(parentId,(counts.get(parentId)||0)+1);
  }
  return {hidden,counts,unresolved};
}
export function spaceFillingAvailable(model){return model.atoms.every(a=>Object.hasOwn(VDW_RADII,a.element));}
export function projectDisplay(model,view,spaceFilling=false){
  const center=['x','y','z'].map(k=>(Math.min(...model.atoms.map(a=>a[k]))+Math.max(...model.atoms.map(a=>a[k])))/2);
  const radius=Math.max(.5,...model.atoms.map(a=>Math.hypot(a.x-center[0],a.y-center[1],a.z-center[2])+(spaceFilling?VDW_RADII[a.element]:0)));
  const scale=336/(2*radius)*view.zoom,cy=Math.cos(view.yaw),sy=Math.sin(view.yaw),cp=Math.cos(view.pitch),sp=Math.sin(view.pitch);
  return model.atoms.map(a=>{const x=a.x-center[0],y=a.y-center[1],z=a.z-center[2],xx=cy*x+sy*z,zz=-sy*x+cy*z;return {...a,x:320+xx*scale,y:200-(cp*y-sp*zz)*scale,z:sp*y+cp*zz,r:spaceFilling?VDW_RADII[a.element]*scale:14};});
}
// Positive yaw matches the manual Rotate right control. Camera pitch never changes.
export function createSlowRotation({step,canRun,requestFrame=globalThis.requestAnimationFrame?.bind(globalThis),cancelFrame=globalThis.cancelAnimationFrame?.bind(globalThis)}){
  let frame=null,last=null,epoch=0;
  function stop(){epoch++;if(frame!==null)cancelFrame?.(frame);frame=null;last=null;}
  function start(){stop();if(!requestFrame||!cancelFrame||!canRun())return false;const ticket=epoch;
    const tick=now=>{if(ticket!==epoch)return;frame=null;if(!canRun()){stop();return;}if(last!==null)step(Math.max(0,Math.min(100,now-last))*Math.PI/30000);last=now;frame=requestFrame(tick);};
    frame=requestFrame(tick);return true;
  }
  return {start,stop};
}
