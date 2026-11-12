/** Exact binding of a fresh disposable-worker result. Historical evidence is not trust. */
import {sameJson} from './conformer-source.mjs';
import {suppliedProvenance} from './supplied-3d.mjs';
export const SUPPLIED_VALIDATION_WARNING='Supplied declarations and coordinates passed an RDKit parse-consistency check. Stereo interpretation is engine-derived from declarations and 3D geometry; experimental origin and generation history are not verified. The 2D drawing is unchanged.';
const fail=()=>{throw new Error('Invalid or mismatched supplied native validation.');};
export function validateSuppliedEvidence(value){
 const smiles=value?.engineDerivedIsomericSmiles;
 if(typeof smiles!=='string'||smiles.length<1||smiles.length>8192||/[^\x21-\x7e]/.test(smiles))fail();
 const expected={schema:'nivram-supplied-3d-validation/1',engine:'RDKit',engineVersion:'2025.09.4',engineDerivedIsomericSmiles:smiles,interpretation:'engine-derived from supplied declarations and 3D coordinates',declarationsPreserved:true,coordinatesPreserved:true,generationClaimsVerified:false,experimentalOriginVerified:false,sourceBindingReverified:false};
 if(!sameJson(value,expected))fail();
 return structuredClone(expected);
}
export function validateSuppliedResult(result,{model,molSha256,clientId,jobId,expiresAt}){
 if(!Number.isSafeInteger(expiresAt)||expiresAt<=Date.now()||expiresAt>Date.now()+60000||!/^[a-f0-9]{64}$/.test(molSha256)||![clientId,jobId].every(s=>typeof s==='string'&&/^[a-f0-9]{32}$/.test(s)))fail();
 const scene={kind:'supplied_coordinates',sourceText:model.sourceText,atoms:model.atoms.map(a=>({id:a.id,element:a.element,x:a.x,y:a.y,z:a.z,atomMap:a.atomMap,charge:a.charge,isotope:a.isotope,declarations:{...a.declarations}})),bonds:model.bonds.map(b=>({id:b.id,order:b.order,a:b.a,b:b.b,declarations:{...b.declarations}}))};
 const validation=validateSuppliedEvidence(result?.validation);
 if(new TextEncoder().encode(JSON.stringify(result)).length>524288||!sameJson(result,{molSha256,clientId,jobId,scene,validation}))fail();
 return {scene:structuredClone(model),validation};
}
export async function validatedSuppliedProvenance(model,validation,cryptoApi=globalThis.crypto){
 const base=await suppliedProvenance(model,cryptoApi);
 return {...base,schema:'nivram-supplied-3d-view/2',validation:validateSuppliedEvidence(validation),limitations:[
  'A local RDKit parse-consistency check preserved declarations and coordinates; it does not establish experimental identity, source authenticity or generation history.',
  'Stereochemistry is engine-derived from supplied declarations and 3D coordinates. Not all stereo interpretation is explicitly declared in the file.',
  'View rotation and zoom do not change source coordinates. The 2D drawing is unchanged.',
  'Export is the exact accepted input bytes, not a converted or optimized structure.'
 ]};
}
