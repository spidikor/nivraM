/** Export only bounded scientific provenance, never names, paths or embedded graphs. */
export const SOURCE_CONTEXT_LIMIT=49152;
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&[Object.prototype,null].includes(Object.getPrototypeOf(value));
const reject=()=>{throw new Error('Image source provenance has unsupported values or exceeds its safe export bounds.');};
const text=max=>value=>{if(typeof value!=='string'||value.length>max||/[\u0000-\u001f\u007f]/.test(value))reject();return value;};
const hash=value=>{if(typeof value!=='string'||! /^[a-f0-9]{64}$/.test(value))reject();return value;};
const integer=min=>value=>{if(!Number.isInteger(value)||value<min||value>32767)reject();return value;};
const fields=(value,spec)=>{if(!object(value))reject();const out={};for(const [key,check] of Object.entries(spec))if(Object.hasOwn(value,key))out[key]=check(value[key]);return out;};
const region=value=>fields(value,{kind:text(32),sourceSha256:hash,pngSha256:hash,sourceWidth:integer(1),sourceHeight:integer(1),width:integer(1),height:integer(1),rect:value=>{if(!object(value)||['x','y','width','height'].some(k=>!Object.hasOwn(value,k)))reject();return fields(value,{x:integer(0),y:integer(0),width:integer(1),height:integer(1)});},scale:text(64),metadataCorrespondence:text(256)});
export function projectSourceContext(document){
  const input=document?.analysis?.imageImport;
  if(input===undefined||input===null)return null;
  if(!object(input)||!Object.hasOwn(input,'kind')||!Object.hasOwn(input,'sourceSha256'))reject();
  const imageImport=fields(input,{
    kind:value=>{if(!['png-user-supplied-structure','png-optical-reconstruction','png-metadata-component-reconstruction'].includes(value))reject();return value;},sourceSha256:hash,
    warnings:value=>{if(!Array.isArray(value)||value.length>32)reject();return value.map(text(1024));},imageCorrespondence:text(256),
    recognition:value=>fields(value,{method:text(64),modelSha256:hash,inputSha256:hash,confidence:value=>{if(value!==null&&(typeof value!=='number'||!Number.isFinite(value)||value<0||value>1))reject();return value;},confidenceMeaning:text(128),coordinates:text(256),status:text(32)}),
    reviewedRegion:region,
    correction:value=>fields(value,{kind:value=>{if(value!=='user-edited-graph')reject();return value;},imageCorrespondence:text(256)})
  });
  const result={schema:'nivram-3d-source-context/1',currentGraphRelationship:'unverified; prior image review does not verify the current graph',imageImport};
  if(new TextEncoder().encode(JSON.stringify(result)).length>SOURCE_CONTEXT_LIMIT)reject();
  return result;
}
export function sourceBinding(snapshot){return {revision:snapshot.revision,graphText:JSON.stringify(snapshot.document.graph),sourceContextText:JSON.stringify(projectSourceContext(snapshot.document))};}
export function sameSource(snapshot,binding){try{const now=sourceBinding(snapshot);return now.revision===binding.revision&&now.graphText===binding.graphText&&now.sourceContextText===binding.sourceContextText;}catch{return false;}}
export function sameJson(a,b){if(a===b)return true;if(Array.isArray(a)&&Array.isArray(b))return a.length===b.length&&a.every((v,i)=>sameJson(v,b[i]));if(object(a)&&object(b)){const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(k=>Object.hasOwn(b,k)&&sameJson(a[k],b[k]));}return false;}
