/** Bounded, uncompressed metadata. Checksums bind bytes, not chemical truth. */
export const COORDINATE_KEY='nivraM Coordinates';
const MAX_RECORD=1024*1024, MAX_PNG=32*1024*1024;
const enc=new TextEncoder();
export async function runtimeVersion(){
  const response=await fetch('/api/status',{cache:'no-store'});
  if(!response.ok)throw new Error('Cannot identify the running nivraM version; no PNG was exported.');
  const text=await response.text();
  if(text.length>16384)throw new Error('Invalid runtime status.');
  const version=JSON.parse(text).version;
  if(typeof version!=='string'||! /^[A-Za-z0-9][A-Za-z0-9.+_-]{0,79}$/.test(version))throw new Error('Invalid runtime version; no PNG was exported.');
  return version;
}
async function digest(bytes){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),v=>v.toString(16).padStart(2,'0')).join('');}
export function crc32(bytes){let crc=0xffffffff;for(const byte of bytes){crc^=byte;for(let i=0;i<8;i++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);}return (crc^0xffffffff)>>>0;}
function chunk(type,payload){const out=new Uint8Array(payload.length+12),view=new DataView(out.buffer);view.setUint32(0,payload.length);out.set(enc.encode(type),4);out.set(payload,8);view.setUint32(out.length-4,crc32(out.subarray(4,out.length-4)));return out;}
export async function stampPng(blob,{graphJson=null,version=null,coordinateRecord=null,removeChemistryMetadata=false}={}){
  if(typeof removeChemistryMetadata!=='boolean')throw new TypeError('Remove chemistry metadata must be true or false.');
  if(!(blob instanceof Blob)||blob.type!=='image/png'||blob.size>MAX_PNG)throw new Error('Invalid PNG encoding.');
  version=version??await runtimeVersion();
  if(typeof version!=='string'||! /^[A-Za-z0-9][A-Za-z0-9.+_-]{0,79}$/.test(version))throw new Error('Invalid runtime version.');
  if(graphJson!==null&&coordinateRecord!==null)throw new Error('Only one coordinate authority is supported.');
  const extra=[chunk('tEXt',enc.encode(`Software\0nivraM ${version}`))];
  if(graphJson!==null&&!removeChemistryMetadata){
    if(typeof graphJson!=='string'||enc.encode(graphJson).length>MAX_RECORD/2)throw new Error('Coordinate metadata exceeds its bound.');
    // Revalidate even when called independently of exportPng.
    const {snapshotCoordinates}=await import('./png-coordinates.mjs');
    if(snapshotCoordinates(JSON.parse(graphJson))!==graphJson)throw new Error('Coordinate metadata must use the exact validated snapshot serialization.');
    const record=JSON.stringify({schema:'nivram-png-coordinates/1',producer:'nivraM',version,dimension:2,origin:'drawing-2d',units:'drawing',graphJson,graphSha256:await digest(enc.encode(graphJson))});
    const payload=enc.encode(`${COORDINATE_KEY}\0\0\0\0\0${record}`);
    if(payload.length>MAX_RECORD)throw new Error('Coordinate metadata exceeds its bound.');
    extra.push(chunk('iTXt',payload));
  }
  if(coordinateRecord!==null&&!removeChemistryMetadata){
    const {validateThreeDRecord}=await import('./three-d-record.mjs');
    const {record}=await validateThreeDRecord({...coordinateRecord,version});
    const payload=enc.encode(`${COORDINATE_KEY}\0\0\0\0\0${JSON.stringify(record)}`);
    if(payload.length>MAX_RECORD)throw new Error('Coordinate metadata exceeds its bound.');
    extra.push(chunk('iTXt',payload));
  }
  const bytes=new Uint8Array(await blob.arrayBuffer()),view=new DataView(bytes.buffer);
  if(bytes.length<20||[137,80,78,71,13,10,26,10].some((v,i)=>bytes[i]!==v))throw new Error('Invalid PNG signature.');
  let offset=8,end=-1,first=true,count=0;const retained=[bytes.subarray(0,8)];
  while(offset+12<=bytes.length){
    if(++count>10000)throw new Error('Too many PNG chunks.');
    const n=view.getUint32(offset),type=String.fromCharCode(...bytes.subarray(offset+4,offset+8));
    if(n>MAX_PNG||offset+n+12>bytes.length||view.getUint32(offset+n+8)!==crc32(bytes.subarray(offset+4,offset+n+8)))throw new Error('Invalid PNG chunk.');
    if(first&&(type!=='IHDR'||n!==13))throw new Error('Missing PNG header.');
    if(!first&&type==='IHDR')throw new Error('Duplicate PNG header.');first=false;
    let omit=false;
    if(removeChemistryMetadata&&type==='sBIT'){
      const expected={0:1,2:3,3:3,4:2,6:4}[bytes[25]],limit=bytes[25]===3?8:bytes[24];
      if(n!==expected||bytes.subarray(offset+8,offset+8+n).some(v=>v<1||v>limit))throw new Error('Invalid PNG significant-bit-depth chunk.');
    }
    if(removeChemistryMetadata&&!['IHDR','IDAT','IEND','tEXt','zTXt','iTXt','sRGB','gAMA','cHRM','pHYs','iCCP','cICP','mDCV','cLLI','sBIT'].includes(type))throw new Error('Unexpected PNG chunk; no stripped export created.');
    if(['tEXt','zTXt','iTXt'].includes(type)){
      const payload=bytes.subarray(offset+8,offset+8+n),zero=payload.indexOf(0),key=new TextDecoder().decode(payload.subarray(0,zero));
      if(zero<1||zero>79)throw new Error('Invalid PNG text keyword.');
      omit=removeChemistryMetadata&&key===COORDINATE_KEY;
      if(removeChemistryMetadata&&!omit)throw new Error('Unexpected preexisting PNG text metadata; no stripped export created.');
      if(key==='Software'||key===COORDINATE_KEY&&!omit)throw new Error('PNG already contains producer or coordinate metadata; original retained.');
    }
    if(type==='IEND'){if(n!==0||offset+12!==bytes.length)throw new Error('Invalid PNG ending.');end=offset;break;}
    if(!omit)retained.push(bytes.subarray(offset,offset+n+12));
    offset+=n+12;
  }
  if(end<0)throw new Error('Missing PNG ending.');
  const output=new Blob([...retained,...extra,bytes.subarray(end)],{type:'image/png'});
  if(output.size>MAX_PNG)throw new Error('PNG metadata exceeds output size bound.');
  return output;
}

/** Framing/metadata validation only: deliberately does not decode uploaded IDAT. */
export async function extractThreeDRecord(input){
  if(input instanceof Blob){if(input.size>MAX_PNG)throw new Error('PNG exceeds its bound.');input=new Uint8Array(await input.arrayBuffer());}
  if(!(input instanceof Uint8Array)||input.length>MAX_PNG||input.length<20||[137,80,78,71,13,10,26,10].some((v,i)=>input[i]!==v))throw new Error('Invalid PNG signature or size.');
  const view=new DataView(input.buffer,input.byteOffset,input.byteLength),dec=new TextDecoder('utf-8',{fatal:true});let offset=8,count=0,record=null,ended=false;
  while(offset+12<=input.length){
    if(++count>10000)throw new Error('Too many PNG chunks.');
    const n=view.getUint32(offset),type=String.fromCharCode(...input.subarray(offset+4,offset+8));
    if(n>MAX_PNG||offset+n+12>input.length||view.getUint32(offset+n+8)!==crc32(input.subarray(offset+4,offset+n+8)))throw new Error('Invalid PNG chunk.');
    if(count===1&&(type!=='IHDR'||n!==13))throw new Error('Missing PNG header.');
    if(count>1&&type==='IHDR')throw new Error('Duplicate PNG header.');
    if(['tEXt','zTXt','iTXt'].includes(type)){
      const payload=input.subarray(offset+8,offset+8+n),zero=payload.indexOf(0);
      if(zero<0)throw new Error('Invalid PNG text keyword.');
      const key=dec.decode(payload.subarray(0,zero));
      if(key===COORDINATE_KEY){
        if(record!==null||type!=='iTXt'||n>MAX_RECORD||payload.length<zero+5||payload.subarray(zero,zero+5).some(v=>v!==0))throw new Error('Unsupported or duplicate native coordinate record.');
        record=dec.decode(payload.subarray(zero+5));
      }
    }
    if(type==='IEND'){if(n!==0||offset+12!==input.length)throw new Error('Invalid PNG ending.');ended=true;break;}
    offset+=n+12;
  }
  if(!ended||record===null)throw new Error('Missing 3D coordinate metadata or PNG ending.');
  const {validateThreeDRecord}=await import('./three-d-record.mjs');return (await validateThreeDRecord(record)).record;
}
