/** Reference artifacts use dialog-scoped native saving when hosted on desktop. */
import {downloadBlob} from './export.mjs';
import {pngBase64} from './batch-export.mjs';

const TYPES={json:'application/json',csv:'text/csv;charset=utf-8',png:'image/png'};
const MAX_BYTES=4000000;
const check=signal=>{if(signal?.aborted)throw signal.reason??new DOMException('Reference export cancelled.','AbortError');};

export async function saveReferenceExport({kind,blob,transparentBackground=false,native=null,token='',signal,download=downloadBlob,beforeDispatch}={}){
  check(signal);
  if(typeof transparentBackground!=='boolean'||(kind!=='png'&&transparentBackground))throw new Error('Invalid PNG background option.');
  if(!Object.hasOwn(TYPES,kind)||!(blob instanceof Blob)||blob.type!==TYPES[kind]||blob.size<1||blob.size>MAX_BYTES)
    throw new Error('The reference export has an unsupported format or exceeds its local save limit.');
  // Keep browser dispatch before the first await to preserve user activation.
  if(native===null){download(blob,`nivram-ph-reference.${kind}`,beforeDispatch);return {status:'prepared'};}
  if(typeof native?.save_reference_export!=='function')
    throw new Error('Native reference saving is unavailable. Keep the chart open and try again.');
  if(typeof token!=='string'||!token||token.length>256)
    throw new Error('Native reference saving is unavailable for this session.');
  const payload=kind==='png'?await pngBase64(blob,{signal}):await blob.text();
  check(signal);
  const bytes=await blob.arrayBuffer();check(signal);
  const digest=await crypto.subtle.digest('SHA-256',bytes);check(signal);
  const expected=[...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('');
  try{beforeDispatch?.();}catch{/* Diagnostic observation cannot prevent saving. */}
  let result;
  try{result=await native.save_reference_export(kind,payload,token,...(transparentBackground?[true]:[]));}
  catch{return {status:'unconfirmed'};}
  if(result?.status==='saved')return {status:result.bytes===blob.size&&result.sha256===expected?'saved':'unconfirmed'};
  if(['cancelled','failed','conflict','unsupported','unconfirmed'].includes(result?.status))return {status:result.status};
  return {status:'unconfirmed'};
}
