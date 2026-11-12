/** Whole-component copies only. No chemistry inference, covalent attachment or cuts. */
import {cloneData,validateProject} from './document.mjs';
export const FRAGMENT_SCHEMA='nivram-fragment/1';
const checkedGraph=graph=>validateProject({schema:'nivram/1',graph}).graph;
const signature=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
export function extractFragment(input,{atoms=[],bonds=[]}={}){
  const graph=checkedGraph(input),atomIds=new Set(graph.atoms.map(a=>a.id)),bondIds=new Set(graph.bonds.map(b=>b.id));
  if(!Array.isArray(atoms)||!Array.isArray(bonds)||atoms.some(id=>!atomIds.has(id))||bonds.some(id=>!bondIds.has(id)))throw new Error('The fragment selection is no longer valid. Select it again.');
  const selected=new Set(atoms),chosenBonds=new Set(bonds);
  for(const bond of graph.bonds)if(chosenBonds.has(bond.id)){selected.add(bond.begin);selected.add(bond.end);}
  if(!selected.size)for(const id of atomIds)selected.add(id);
  if(!selected.size)throw new Error('Draw or import a structure first.');
  if(graph.bonds.some(b=>selected.has(b.begin)!==selected.has(b.end)))throw new Error('Select complete disconnected components, or clear the selection to use the whole drawing. Cutting a molecule into a reusable fragment is not supported.');
  const result={atoms:graph.atoms.filter(a=>selected.has(a.id)),bonds:graph.bonds.filter(b=>selected.has(b.begin))};
  if(result.bonds.some(b=>(b.stereoAtoms??[]).some(id=>!selected.has(id))))throw new Error('The selection omits a stereochemistry reference. Select the whole drawing.');
  return checkedGraph(result);
}
export function validateFragmentRecord(input){
  const record=cloneData(input);
  if(!record||Object.keys(record).sort().join(',')!=='graph,id,name,schema'||record.schema!==FRAGMENT_SCHEMA||typeof record.id!=='string'||!(/^[0-9a-f]{32}$/).test(record.id))throw new Error('Unsupported local fragment record.');
  if(typeof record.name!=='string'||!record.name.trim()||record.name.length>80||/[\x00-\x1f\x7f]/.test(record.name))throw new Error('Use a fragment name of 1–80 printable characters.');
  record.graph=checkedGraph(record.graph);
  if(!record.graph.atoms.length)throw new Error('An empty fragment cannot be inserted.');
  return record;
}
export function insertFragment(input,fragment,makeId){
  const graph=checkedGraph(input),source=checkedGraph(fragment);
  if(!source.atoms.length)throw new Error('An empty fragment cannot be inserted.');
  const used=new Set([...graph.atoms,...graph.bonds].map(a=>a.id));
  const fresh=prefix=>{const id=makeId?.(prefix);if(typeof id!=='string'||!id||id.length>80||used.has(id))throw new Error('New fragment entities require unique identifiers.');used.add(id);return id;};
  const ids=new Map(source.atoms.map(a=>[a.id,fresh('a')]));
  const minX=Math.min(...source.atoms.map(a=>a.x));
  const dx=graph.atoms.length?Math.max(...graph.atoms.map(a=>a.x))+3-minX:-minX;
  const centerY=(Math.min(...source.atoms.map(a=>a.y))+Math.max(...source.atoms.map(a=>a.y)))/2;
  const targetY=graph.atoms.length?(Math.min(...graph.atoms.map(a=>a.y))+Math.max(...graph.atoms.map(a=>a.y)))/2:0;
  const atoms=source.atoms.map(a=>({...a,id:ids.get(a.id),x:a.x+dx,y:a.y+targetY-centerY}));
  const bonds=source.bonds.map(b=>({...b,id:fresh('b'),begin:ids.get(b.begin),end:ids.get(b.end),...(b.stereoAtoms===undefined?{}:{stereoAtoms:b.stereoAtoms.map(id=>ids.get(id))})}));
  const next=checkedGraph({atoms:[...graph.atoms,...atoms],bonds:[...graph.bonds,...bonds]});
  return {graph:next,createdAtomIds:atoms.map(a=>a.id),createdBondIds:bonds.map(b=>b.id)};
}
// The render route may normalize a graph. This operation accepts only the
// intended graph, including all old entities and copied annotations. Missing
// optional fields may become their documented zero/false/empty defaults only.
export function assertFragmentRender(expected,result,expectedStyle=null){
  const normalize=graph=>{const value=checkedGraph(graph);return {
    atoms:value.atoms.map(a=>({charge:0,isotope:0,explicitHs:0,noImplicit:false,aromatic:false,chiralTag:0,radicals:0,mapNum:0,...a})),
    bonds:value.bonds.map(b=>({direction:0,stereo:0,stereoAtoms:[],...b}))};};
  if(expectedStyle!==null&&signature(validateProject({schema:'nivram/1',graph:expected,style:result?.scene?.style}).style)!==signature(expectedStyle))throw new Error('The chemistry engine changed drawing appearance. Nothing was inserted.');
  if(signature(normalize(expected))!==signature(normalize(result?.graph)))throw new Error('The chemistry engine changed the proposed fragment or existing drawing. Nothing was inserted.');
}
