// Local request/response association, not independent chemical or SVG verification.
import {cloneData,validateProject} from './document.mjs';
import {sameJson,validatePropertyIdentity,validateProperties} from './property-contract.mjs';
export const IMPORT_SCHEMA='nivram-import/1';
const require=ok=>{if(!ok)throw new Error('Invalid or mismatched bound import response. The drawing is unchanged.');};
const freeze=v=>{if(v&&typeof v==='object'){for(const x of Object.values(v))freeze(x);Object.freeze(v);}return v;};
const id=()=>Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');
const closed=(v,keys)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
export function captureImportRequest(payload,documentRevision,requestId=id()){
 require(Number.isSafeInteger(documentRevision)&&documentRevision>=0&&typeof requestId==='string'&&/^[0-9a-f]{32}$/.test(requestId));
 const input=cloneData(payload);
 require(input&&typeof input==='object'&&!Array.isArray(input)&&Object.keys(input).every(k=>['source','style'].includes(k)));
 require(typeof input.source==='string'&&input.source.length>0&&input.source.length<=4_000_000&&!/[\uD800-\uDFFF]/u.test(input.source));
 const bytes=new TextEncoder().encode(input.source).byteLength;require(bytes<=4_000_000);
 const style=validateProject({schema:'nivram/1',graph:{atoms:[],bonds:[]},...(Object.hasOwn(input,'style')?{style:input.style}:{})}).style;
 return freeze({source:input.source,sourceBytes:bytes,style,requestId,documentRevision});
}
export async function admitImportResponse(response,captured,{assertCurrent=()=>{},cryptoImpl=globalThis.crypto}={}){
 assertCurrent();const result=cloneData(response);
 require(closed(result,['schema','action','requestId','documentRevision','source','style','outputIdentity','outputGraphJson','graph','scene']));
 require(result.schema===IMPORT_SCHEMA&&result.action==='import'&&result.requestId===captured.requestId&&result.documentRevision===captured.documentRevision);
 require(closed(result.source,['sha256','bytes'])&&typeof result.source.sha256==='string'&&/^[0-9a-f]{64}$/.test(result.source.sha256)&&result.source.bytes===captured.sourceBytes);
 require(sameJson(result.style,captured.style)&&result.scene&&sameJson(result.scene.style,captured.style));
 require(typeof result.outputGraphJson==='string'&&result.outputGraphJson.length<=2_000_000&&sameJson(cloneData(JSON.parse(result.outputGraphJson)),result.graph));
 validatePropertyIdentity(result.outputIdentity,null);
 validateProject({schema:'nivram/1',graph:result.graph,style:result.style});
 require(result.scene.properties&&sameJson(result.scene.properties.inputIdentity,result.outputIdentity)&&result.scene.smiles===result.outputIdentity.smiles);
 validateProperties(result.scene.properties,result.graph);
 require(typeof cryptoImpl?.subtle?.digest==='function');
 const digest=async text=>{assertCurrent();const bytes=await cryptoImpl.subtle.digest('SHA-256',new TextEncoder().encode(text));assertCurrent();return Array.from(new Uint8Array(bytes),v=>v.toString(16).padStart(2,'0')).join('');};
 require(await digest(captured.source)===result.source.sha256);assertCurrent();
 require(await digest(result.outputGraphJson)===result.outputIdentity.graphHash);assertCurrent();
 require(await digest(result.outputIdentity.smiles)===result.outputIdentity.structureHash);assertCurrent();
 return freeze(result);
}
export async function requestImport(payload,{job,revision=job?.revision,signal=job?.signal,assertCurrent=()=>job?.assertCurrent(),token='',fetchImpl=globalThis.fetch,requestId}={}){
 const current=()=>{assertCurrent();if(signal?.aborted)throw signal.reason??new Error('Import cancelled.');};
 current();const captured=captureImportRequest(payload,revision,requestId);
 const wait=async promise=>{const value=await (job?job.wait(promise):promise);current();return value;};
 const response=await wait(fetchImpl('/api/import',{method:'POST',headers:{'Content-Type':'application/json','X-Nivram-Token':token},body:JSON.stringify({source:captured.source,style:captured.style,sourceBinding:IMPORT_SCHEMA,requestId:captured.requestId,documentRevision:captured.documentRevision}),...(signal?{signal}:{})}));
 const body=await wait(response.text());if(body.length>4_000_000)throw new Error('The local response exceeded the supported size.');
 const result=JSON.parse(body);if(!response.ok){const error=new Error(result.error||'Local request failed.');error.diagnostic=result.diagnostic;throw error;}
 const accepted=await wait(admitImportResponse(result,captured,{assertCurrent:current}));current();return accepted;
}
