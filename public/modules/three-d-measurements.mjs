/** Cartesian geometry only. MOL coordinates are interpreted in angstroms, not calibrated from pixels. */
export const MIN_LENGTH=1e-8, MIN_SINE=1e-8;
const fail=message=>{throw new Error(message);};
const subtract=(a,b)=>a.map((v,i)=>v-b[i]);
const dot=(a,b)=>a.reduce((sum,v,i)=>sum+v*b[i],0);
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const unit=v=>{const n=Math.hypot(...v);if(n<=MIN_LENGTH)fail('Coincident or nearly coincident atoms: measurement undefined.');return v.map(x=>x/n);};
export function measureCoordinates(atoms,ids){
  if(!Array.isArray(atoms)||atoms.length>256||!Array.isArray(ids)||ids.length<2||ids.length>4||new Set(ids).size!==ids.length)fail('Choose two, three or four distinct atoms in order.');
  const p=ids.map(id=>{const a=atoms.find(a=>a.id===id);if(!a||['x','y','z'].some(k=>!Number.isFinite(a[k])||Math.abs(a[k])>10000))fail('Selected coordinates are unavailable or out of bounds.');return [a.x,a.y,a.z];});
  const first=subtract(p[0],p[1]);
  if(ids.length===2)return {kind:'Distance',value:Math.hypot(...first),unit:'Å'};
  const u=unit(first),v=unit(subtract(p[2],p[1]));
  if(ids.length===3)return {kind:'Angle at atom 2',value:Math.atan2(Math.hypot(...cross(u,v)),dot(u,v))*180/Math.PI,unit:'°'};
  const w=unit(subtract(p[3],p[2])),a=subtract(u,v.map(x=>x*dot(u,v))),b=subtract(w,v.map(x=>x*dot(w,v)));
  if(Math.hypot(...a)<=MIN_SINE||Math.hypot(...b)<=MIN_SINE)fail('Collinear or nearly collinear torsion: measurement undefined.');
  return {kind:'Signed torsion (1–2–3–4)',value:Math.atan2(dot(cross(v,a),b),dot(a,b))*180/Math.PI,unit:'°'};
}
export function measurementOrigin(model){return model?.kind==='generated_coordinates'?'Generated model coordinates; not an experimental structure.':model?.kind==='recovered_coordinates'?'Recovered coordinates; historical origin and generation claims are unverified.':'Supplied coordinates; experimental origin is unverified.';}
export function mountMeasurements({dom,find,getModel,getHydrogens,isCurrent=()=>true}){
  const mode=find('supplied-3d-measure-mode'),output=find('supplied-3d-measure-result'),clear=find('supplied-3d-measure-clear'),selectors=[1,2,3,4].map(i=>find('supplied-3d-measure-'+i));
  if(!mode||!output||selectors.some(s=>!s))return {reset(){},destroy(){}};
  const handlers=[];const on=(n,t,f)=>{n?.addEventListener(t,f);handlers.push(()=>n?.removeEventListener(t,f));};
  function update(){
    if(!isCurrent())return;
    const model=getModel(),count=Number(mode.value)||2;selectors.forEach((s,i)=>{s.disabled=!model||i>=count;});
    const ids=selectors.slice(0,count).map(s=>Number(s.value));
    if(!model||ids.some(id=>!id)){output.textContent='Select distinct visible atoms in order to measure accepted 3D coordinates.';return;}
    try{const result=measureCoordinates(model.atoms,ids);output.textContent=`${result.kind} [${ids.join(' → ')}]: ${result.value.toFixed(4)} ${result.unit}. ${measurementOrigin(model)} Distances assume MOL coordinate units are Å; no image scale calibration, bond, energy or physical-validity inference.`;}catch(error){output.textContent=error.message;}
  }
  function reset(){const model=getModel(),hidden=model?getHydrogens(model).hidden:new Set();for(const s of selectors){s.replaceChildren();const placeholder=dom.createElement('option');placeholder.value='';placeholder.textContent='Choose atom';s.append(placeholder);for(const a of model?.atoms||[]){if(hidden.has(a.id))continue;const option=dom.createElement('option');option.value=String(a.id);option.textContent=a.generatedId?`${a.id}: H attachment ${a.generatedId} (parent ${a.parentId})`:`${a.id}: ${a.element} (source atom)`;s.append(option);}s.value='';}update();}
  on(mode,'change',reset);on(clear,'click',reset);for(const s of selectors)on(s,'change',update);reset();return {reset,destroy(){handlers.forEach(f=>f());}};
}
