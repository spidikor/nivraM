/** Rejected candidates are transient presentation only, never document data. */
export function valenceFeedback(diagnostic,before,after,transform){
  if(diagnostic?.code!=='atom-valence'||!Array.isArray(diagnostic.atomIds)||
    diagnostic.atomIds.some(id=>typeof id!=='string'))return null;
  const atoms=new Map(after.atoms.map(a=>[a.id,a]));
  const ids=new Set(diagnostic.atomIds.filter(id=>atoms.has(id)));
  if(!ids.size)return null;
  const previous=new Map(before.bonds.map(b=>[b.id,b]));
  // Compare chemical bond properties, not incidental JSON key order. Do not
  // color unchanged neighbors merely because they meet the rejected atom.
  const keys=['begin','end','order','direction','stereo'];
  const bonds=after.bonds.filter(b=>{
    const old=previous.get(b.id);
    return (ids.has(b.begin)||ids.has(b.end))&&(!old||keys.some(k=>(b[k]??0)!==(old[k]??0))||
      JSON.stringify(b.stereoAtoms??[])!==JSON.stringify(old.stereoAtoms??[]));
  });
  const point=a=>transform&&[a?.x,a?.y,transform.x0,transform.y0,transform.xScale,transform.yScale].every(Number.isFinite)
    ?{x:transform.x0+a.x*transform.xScale,y:transform.y0+a.y*transform.yScale}:null;
  const lines=bonds.flatMap(b=>{const start=point(atoms.get(b.begin)),end=point(atoms.get(b.end));return start&&end?[{id:b.id,start,end}]:[];});
  const points=lines.length?[]:[...ids].flatMap(id=>{const p=point(atoms.get(id));return p?[{id,...p}]:[];});
  const labels=[...ids].map(id=>{const a=atoms.get(id);return `${a.symbol} (atom ${after.atoms.indexOf(a)+1})`;});
  const message=`Edit not applied: invalid valence at ${labels.join(', ')}. ${lines.length?'The proposed bond is marked in red. ':points.length?'The affected atom is marked in red. ':''}The preceding drawing is retained. Try a different bond order or atom charge. Press Escape to dismiss.`;
  return {lines,points,message,label:lines.length?'Rejected bond: invalid valence':'Rejected atom edit: invalid valence'};
}
export function paintValenceFeedback(host,feedback,doc){
  host.replaceChildren();
  host.setAttribute('role','img');host.setAttribute('aria-label',feedback.label);
  host.setAttribute('aria-hidden','false');
  for(const {id,start,end} of feedback.lines){
    const line=doc.createElementNS('http://www.w3.org/2000/svg','line');
    for(const [key,value] of Object.entries({'data-proposed-bond':id,x1:start.x,y1:start.y,x2:end.x,y2:end.y,stroke:'#dc2626','stroke-width':4,'stroke-dasharray':'7 4','vector-effect':'non-scaling-stroke'}))line.setAttribute(key,String(value));
    host.append(line);
  }
  for(const {id,x,y} of feedback.points??[]){
    const circle=doc.createElementNS('http://www.w3.org/2000/svg','circle');
    for(const [key,value] of Object.entries({'data-rejected-atom':id,cx:x,cy:y,r:14,fill:'none',stroke:'#dc2626','stroke-width':3,'stroke-dasharray':'5 3','vector-effect':'non-scaling-stroke'}))circle.setAttribute(key,String(value));
    host.append(circle);
  }
}
