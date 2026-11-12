/** Derived display geometry, never a canonical scene or source-atom mapping. */
export function validateGeneratedHydrogens(value,sourceCount,canonicalHash){
  const need=ok=>{if(!ok)throw new Error('Invalid generated hydrogen display attachment.');};
  const keys=(v,ks)=>need(v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===ks.length&&ks.every(k=>Object.hasOwn(v,k)));
  keys(value,['schema','canonicalMolSha256','hydrogens']);
  need(value.schema==='nivram-generated-hydrogens/1'&&/^[a-f0-9]{64}$/.test(value.canonicalMolSha256)&&value.canonicalMolSha256===canonicalHash);
  need(Number.isInteger(sourceCount)&&sourceCount>=1&&sourceCount<=128&&Array.isArray(value.hydrogens)&&value.hydrogens.length+sourceCount<=256);
  for(const [i,h] of value.hydrogens.entries()){
    keys(h,['id','parentId','x','y','z']);
    need(h.id===`generated-h${i+1}`&&Number.isInteger(h.parentId)&&h.parentId>=1&&h.parentId<=sourceCount);
    need(['x','y','z'].every(k=>typeof h[k]==='number'&&Number.isFinite(h[k])&&Math.abs(h[k])<=10000));
  }
  return value;
}
export function generatedDisplayModel(model){
  if(!model.generatedHydrogens)return model;
  const value=model.generatedHydrogens;
  validateGeneratedHydrogens(value,model.atoms.length,value.canonicalMolSha256);
  if(!['generated_coordinates','recovered_coordinates'].includes(model.kind)||model.bonds.length+value.hydrogens.length>512)throw new Error('Invalid generated hydrogen display scene.');
  const atoms=model.atoms.map(a=>({...a})),bonds=model.bonds.map(b=>({...b}));
  for(const h of value.hydrogens){
    const parent=model.atoms[h.parentId-1];
    if(!parent||parent.id!==h.parentId||parent.element==='H')throw new Error('Invalid generated hydrogen parent.');
    const id=atoms.length+1;
    atoms.push({id,generatedId:h.id,parentId:h.parentId,element:'H',charge:0,isotope:0,atomMap:0,x:h.x,y:h.y,z:h.z});
    bonds.push({id:bonds.length+1,a:h.parentId,b:id,order:1});
  }
  return {...model,atoms,bonds,displaySourceAtomCount:model.atoms.length};
}
