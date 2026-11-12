import {downloadBlob} from './export.mjs';
import {pngBase64} from './batch-export.mjs';
import {POPULATION_EXPORT_MAX_BYTES} from './population-view.mjs';

const TYPES={json:'application/json',csv:'text/csv;charset=utf-8',png:'image/png'};
const check=signal=>{if(signal?.aborted)throw signal.reason??new DOMException('Population export cancelled.','AbortError');};

export async function savePopulationExport({kind,blob,transparentBackground=false,native=null,token='',signal,download=downloadBlob}={}){
  check(signal);
  if(typeof transparentBackground!=='boolean'||(kind!=='png'&&transparentBackground))throw new Error('Invalid PNG background option.');
  if(!Object.hasOwn(TYPES,kind)||!(blob instanceof Blob)||blob.type!==TYPES[kind]||blob.size<1||blob.size>POPULATION_EXPORT_MAX_BYTES)
    throw new Error('The population export has an unsupported format or exceeds 32 MiB.');
  // Preserve the click's activation for ordinary browser downloads.
  if(native===null){download(blob,`nivram-supplied-populations.${kind}`);return {status:'prepared'};}
  if(typeof native?.save_population_export!=='function'||typeof token!=='string'||!token||token.length>128)
    throw new Error('Native population saving is unavailable. Keep the chart open and try again.');
  const payload=kind==='png'?await pngBase64(blob,{signal}):await blob.text();
  check(signal);
  const bytes=await blob.arrayBuffer();check(signal);
  const digest=await crypto.subtle.digest('SHA-256',bytes);check(signal);
  const expected=[...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('');
  let result;
  try{result=await native.save_population_export(kind,payload,token,...(transparentBackground?[true]:[]));}
  catch{return {status:'unconfirmed'};}
  if(result?.status==='saved')return {status:result.bytes===blob.size&&result.sha256===expected?'saved':'unconfirmed'};
  if(['cancelled','failed','conflict','unsupported','unconfirmed'].includes(result?.status))return {status:result.status};
  return {status:'unconfirmed'};
}
