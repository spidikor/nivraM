import {validateGeneratedHydrogens,generatedDisplayModel} from './generated-hydrogens.mjs';
import {sameJson} from './conformer-source.mjs';
import {parseRecordJson} from './three-d-record.mjs';
import {stampPng,runtimeVersion,extractThreeDRecord} from './png-metadata.mjs';
import {createThreeDRecord,validateThreeDRecord} from './three-d-record.mjs';
export {extractThreeDRecord,validateThreeDRecord};
export const MAX_3D_PNG_BYTES=32*1024*1024;
export const RECOVERY_WARNING='Recovered coordinates are detached from the current drawing. File generation claims are historical and unverified; source binding is recorded, not independently reverified. Hashes establish internal consistency, not authenticity or correspondence to visible pixels.';
const enc=new TextEncoder();
export async function sha256(text,cryptoApi=globalThis.crypto){return Array.from(new Uint8Array(await cryptoApi.subtle.digest('SHA-256',enc.encode(text))),b=>b.toString(16).padStart(2,'0')).join('');}
// Copy only locally rendered primitives and inert attributes. Never serialize arbitrary
// DOM, links, style sheets, filters, scripts, images or external resources.
export function snapshotScene(scene,dom=document){
  const tags=new Set(['g','line','circle','text','title']);
  const attributes=new Set(['x','y','x1','y1','x2','y2','cx','cy','r','fill','stroke','stroke-width','stroke-dasharray','text-anchor','font-size','font-weight','data-atom']);
  const root=dom.createElementNS('http://www.w3.org/2000/svg','svg');
  root.setAttribute('xmlns','http://www.w3.org/2000/svg');root.setAttribute('width','1280');root.setAttribute('height','800');root.setAttribute('viewBox','0 0 640 400');root.setAttribute('font-family','Arial,Helvetica,sans-serif');
  function copy(node,parent){
    const tag=node.localName;if(!tags.has(tag))throw new Error('Unsupported 3D scene primitive.');
    const next=dom.createElementNS('http://www.w3.org/2000/svg',tag);
    for(const attr of Array.from(node.attributes||[])){
      if(!attributes.has(attr.name)||/url\s*\(|[<>]/i.test(attr.value))throw new Error('Unsupported 3D scene attribute.');
      next.setAttribute(attr.name,attr.value);
    }
    if(tag==='text'||tag==='title')next.textContent=node.textContent;
    else for(const child of Array.from(node.children||[]))copy(child,next);
    parent.append(next);
  }
  for(const child of Array.from(scene.children||[]))copy(child,root);
  return new XMLSerializer().serializeToString(root);
}
export async function rasterizeScene(svgText,{dom=document,ImageClass=Image,urlApi=URL,transparentBackground=false}={}){
  if(typeof transparentBackground!=='boolean')throw new TypeError('transparentBackground must be a boolean.');
  let url=null,image=null,canvas=null;
  try{
    if(dom.fonts?.ready)await dom.fonts.ready;
    url=urlApi.createObjectURL(new Blob([svgText],{type:'image/svg+xml'}));
    image=new ImageClass();image.src=url;await image.decode();
    canvas=dom.createElement('canvas');canvas.width=1280;canvas.height=800;
    const context=canvas.getContext('2d');if(!context)throw new Error('PNG rasterization is unavailable.');
    if(!transparentBackground){context.fillStyle='#ffffff';context.fillRect(0,0,1280,800);}context.drawImage(image,0,0,1280,800);
    const blob=await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('PNG encoding failed.')),'image/png'));
    if(blob.size>MAX_3D_PNG_BYTES)throw new Error('3D PNG exceeds the 32 MiB bound.');return blob;
  }finally{if(image)image.src='';if(url!==null)urlApi.revokeObjectURL(url);if(canvas){canvas.width=0;canvas.height=0;}}
}
export async function exportThreeDPng(snapshot,{rasterize=rasterizeScene,version=runtimeVersion,transparentBackground=false,removeChemistryMetadata=false}={}){
  if(typeof removeChemistryMetadata!=='boolean')throw new TypeError('Remove chemistry metadata must be true or false.');
  if(typeof transparentBackground!=='boolean')throw new TypeError('transparentBackground must be a boolean.');
  const blob=await rasterize(snapshot.svgText,{transparentBackground});
  // Identity is resolved at finalization, never copied from historical provenance.
  const currentVersion=await version();
  const record=removeChemistryMetadata?null:snapshot.record||await createThreeDRecord(snapshot,currentVersion);
  return stampPng(blob,{coordinateRecord:record,version:currentVersion,removeChemistryMetadata});
}
export function validateRecoveryResult(result,{record,recordSha256,jobId,provenance}){
  const fail=()=>{throw new Error('Invalid or mismatched recovered coordinate result.');};
  if(result?.recordSha256!==recordSha256||result?.molSha256!==record.molSha256||result?.jobId!==jobId)fail();
  const validation=result.validation,scene=result.scene;
  if(validation?.schema!==(record.schema==='nivram-png-coordinates/6'?'nivram-3d-recovery-validation/2':'nivram-3d-recovery-validation/1')||validation.engine!=='RDKit'||typeof validation.engineVersion!=='string'||validation.chemicalIdentityConsistent!==true||validation.sourceBindingReverified!==false||validation.generationClaimsVerified!==false)fail();
  if(validation.canonicalIsomericSmiles!==provenance.canonicalIsomericSmiles)fail();
  if(scene?.kind!=='recovered_coordinates'||scene.sourceText!==record.molText||!Array.isArray(scene.atoms)||scene.atoms.length!==record.sourceAtomIds.length||!Array.isArray(scene.bonds)||scene.bonds.length>256)fail();
  for(const [i,a] of scene.atoms.entries())if(a.id!==i+1||a.sourceId!==record.sourceAtomIds[i]||typeof a.element!=='string'||!/^([A-Z][a-z]?)$/.test(a.element)||!Number.isInteger(a.charge)||!Number.isInteger(a.isotope)||['x','y','z'].some(k=>!Number.isFinite(a[k])||Math.abs(a[k])>10000))fail();
  const pairs=new Set();for(const [i,b] of scene.bonds.entries()){
    if(b.id!==i+1||![b.a,b.b].every(n=>Number.isInteger(n)&&n>=1&&n<=scene.atoms.length)||b.a===b.b||![1,1.5,2,3,4].includes(b.order))fail();
    const pair=[b.a,b.b].sort((a,b)=>a-b).join(':');if(pairs.has(pair))fail();pairs.add(pair);
  }
  if(record.schema==='nivram-png-coordinates/6'){
    if(validation.generatedHydrogensSha256!==record.generatedHydrogensSha256||validation.generatedHydrogenTopologyConsistent!==true||validation.hydrogenGeometryAuthenticated!==false)fail();
    validateGeneratedHydrogens(scene.generatedHydrogens,scene.atoms.length,record.molSha256);
    if(!sameJson(scene.generatedHydrogens,parseRecordJson(record.generatedHydrogensJson,65536)))fail();
    generatedDisplayModel(scene);
  }else if(Object.hasOwn(scene,'generatedHydrogens'))fail();
  return {scene:structuredClone(scene),validation:structuredClone(validation)};
}
