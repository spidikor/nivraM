/** Apply the bond toolbar choice to the caller's private candidate graph. */
export function setBondKind(graph,bondId,kind){
  const bond=graph.bonds.find(item=>item.id===bondId);
  if(!bond)throw new Error('The selected bond no longer exists.');
  if(!['1','2','3','wedge','dash'].includes(kind))throw new Error('Unsupported bond tool.');
  const order=['wedge','dash'].includes(kind)?1:Number(kind),direction=kind==='wedge'?1:kind==='dash'?2:0;
  if(bond.order===order&&(bond.direction??0)===direction)return false;
  const changedOrder=bond.order!==order;
  // A wedge encodes the configuration at its begin atom, not both endpoints.
  const touched=changedOrder?[bond.begin,bond.end]:[1,2,6].includes(bond.direction)||[1,2].includes(direction)?[bond.begin]:[];
  Object.assign(bond,{order,direction,stereo:0,stereoAtoms:[]});
  for(const atom of graph.atoms)if(touched.includes(atom.id))atom.chiralTag=0;
  return true;
}

/** Describe an accepted edit's changes to existing stereo annotations. */
export function stereoEditNotice(previous,next){
  const atoms=new Map(next.atoms.map(atom=>[atom.id,atom])),bonds=new Map(next.bonds.map(bond=>[bond.id,bond]));
  let cleared=0,changed=0;
  for(const atom of previous.atoms){
    if(!atom.chiralTag)continue;
    const current=atoms.get(atom.id);
    if(!current?.chiralTag)cleared++;else if(current.chiralTag!==atom.chiralTag)changed++;
  }
  const annotation=bond=>[bond.stereo??0,[1,2,6].includes(bond.direction)?bond.direction:0,bond.stereoAtoms??[]];
  for(const bond of previous.bonds){
    if(!bond.stereo&&![1,2,6].includes(bond.direction))continue;
    const current=bonds.get(bond.id);
    if(!current||(!current.stereo&&![1,2,6].includes(current.direction)))cleared++;
    else if(JSON.stringify(annotation(current))!==JSON.stringify(annotation(bond)))changed++;
  }
  if(!cleared&&!changed)return '';
  const parts=[];
  if(cleared)parts.push(`${cleared} stereo annotation${cleared===1?'':'s'} cleared`);
  if(changed)parts.push(`${changed} stereo annotation${changed===1?'':'s'} changed`);
  return `Edit applied: ${parts.join('; ')}. Review stereochemistry. Undo restores the previous drawing.`;
}
