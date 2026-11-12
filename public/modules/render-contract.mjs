// Local response association only, not independent chemical or SVG verification.
import {cloneData,DEFAULT_STYLE,validateProject} from './document.mjs';
import {sameJson,validatePropertyIdentity,validateProperties} from './property-contract.mjs';
export const RENDER_SCHEMA='nivram-render/1';
const fields={render:['graph','style'],clean:['graph','style','selected'],reflect:['graph','style','mode','axis','selected'],rotate:['graph','style','degrees','selected']};
const fail=()=>{throw new Error('Invalid or mismatched bound scene response. The drawing is unchanged.');};
const require=ok=>{if(!ok)fail();};
const freeze=v=>{if(v&&typeof v==='object'){for(const x of Object.values(v))freeze(x);Object.freeze(v);}return v;};
const id=()=>Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');
export function captureRenderRequest(action,payload,documentRevision,requestId=id()){
 require(Object.hasOwn(fields,action)&&Number.isSafeInteger(documentRevision)&&documentRevision>=0&&typeof requestId==='string'&&/^[0-9a-f]{32}$/.test(requestId));
 const sourceRequest=cloneData(payload);
 require(sourceRequest&&typeof sourceRequest==='object'&&!Array.isArray(sourceRequest)&&Object.hasOwn(sourceRequest,'graph')&&Object.keys(sourceRequest).every(k=>fields[action].includes(k)));
 validateProject({schema:'nivram/1',graph:sourceRequest.graph,...(Object.hasOwn(sourceRequest,'style')?{style:sourceRequest.style}:{})});
 return freeze({action,requestId,documentRevision,sourceRequest});
}
// Synchronous structural checks only. Consumers must await admitRenderResponse.
export function validateRenderResponse(response,captured){
 const result=cloneData(response),keys=['schema','requestId','documentRevision','action','sourceRequest','sourceIdentity','outputIdentity','outputGraphJson','graph','scene'];
 require(result&&typeof result==='object'&&!Array.isArray(result)&&Object.keys(result).length===keys.length&&keys.every(k=>Object.hasOwn(result,k)));
 require(result.schema===RENDER_SCHEMA&&result.requestId===captured.requestId&&result.documentRevision===captured.documentRevision&&result.action===captured.action&&sameJson(result.sourceRequest,captured.sourceRequest));
 require(typeof result.outputGraphJson==='string'&&result.outputGraphJson.length<=2_000_000&&sameJson(cloneData(JSON.parse(result.outputGraphJson)),result.graph));
 validatePropertyIdentity(result.sourceIdentity,null);validatePropertyIdentity(result.outputIdentity,null);
 if(captured.action==='render')require(result.sourceIdentity.structureHash===result.outputIdentity.structureHash&&result.sourceIdentity.smiles===result.outputIdentity.smiles);
 require(result.scene&&sameJson(result.scene.style,{...DEFAULT_STYLE,...(captured.sourceRequest.style??{})}));
 validateProject({schema:'nivram/1',graph:result.graph,style:result.scene.style});
 require(result.scene.properties&&sameJson(result.scene.properties.inputIdentity,result.outputIdentity)&&result.scene.smiles===result.outputIdentity.smiles);
 validateProperties(result.scene.properties,result.graph);
 return freeze(result);
}
export async function admitRenderResponse(response,captured,{assertCurrent=()=>{},cryptoImpl=globalThis.crypto}={}){
 assertCurrent();const result=validateRenderResponse(response,captured);
 require(typeof cryptoImpl?.subtle?.digest==='function');
 const digest=async text=>{const bytes=await cryptoImpl.subtle.digest('SHA-256',new TextEncoder().encode(text));assertCurrent();return Array.from(new Uint8Array(bytes),v=>v.toString(16).padStart(2,'0')).join('');};
 require(await digest(result.outputGraphJson)===result.outputIdentity.graphHash);
 require(await digest(result.outputIdentity.smiles)===result.outputIdentity.structureHash);
 assertCurrent();return result;
}
export async function requestRender(action,payload,{job,revision=job?.revision,signal=job?.signal,assertCurrent=()=>job?.assertCurrent(),token='',fetchImpl=globalThis.fetch,requestId}={}){
 assertCurrent();
 const captured=captureRenderRequest(action,payload,revision,requestId);
 const wait=async promise=>{const value=await (job?job.wait(promise):promise);assertCurrent();if(signal?.aborted)throw signal.reason;return value;};
 const response=await wait(fetchImpl('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':token},body:JSON.stringify({...captured.sourceRequest,sourceBinding:RENDER_SCHEMA,requestId:captured.requestId,documentRevision:captured.documentRevision}),...(signal?{signal}:{})}));
 const body=await wait(response.text());if(body.length>4_000_000)throw new Error('The local response exceeded the supported size.');
 const result=JSON.parse(body);if(!response.ok){const error=new Error(result.error||'Local request failed.');error.diagnostic=result.diagnostic;throw error;}
 const accepted=await wait(admitRenderResponse(result,captured,{assertCurrent:()=>{assertCurrent();if(signal?.aborted)throw signal.reason;}}));assertCurrent();return accepted;
}
