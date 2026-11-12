import {snapshotCoordinates} from '../public/modules/png-coordinates.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {deflateSync} from 'node:zlib';
import {webcrypto} from 'node:crypto';
import {writeFileSync} from 'node:fs';
import {stampPng,crc32,runtimeVersion} from '../public/modules/png-metadata.mjs';
globalThis.crypto??=webcrypto;
const enc=new TextEncoder();
function chunk(type,payload){const out=Buffer.alloc(payload.length+12);out.writeUInt32BE(payload.length);out.write(type,4);out.set(payload,8);out.writeUInt32BE(crc32(out.subarray(4,-4)),out.length-4);return out;}
function png(extra=[]){const header=Buffer.alloc(13);header.writeUInt32BE(3);header.writeUInt32BE(3,4);header[8]=8;header[9]=2;const rows=Buffer.alloc(30,255);for(let i=0;i<3;i++)rows[i*10]=0;return new Blob([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',header),...extra,chunk('IDAT',deflateSync(rows)),chunk('IEND',Buffer.alloc(0))],{type:'image/png'});}
function records(bytes){let offset=8,out=[];const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);while(offset<bytes.length){const n=view.getUint32(offset),type=new TextDecoder().decode(bytes.subarray(offset+4,offset+8));assert.equal(view.getUint32(offset+n+8),crc32(bytes.subarray(offset+4,offset+n+8)));out.push({type,payload:bytes.subarray(offset+8,offset+n+8)});offset+=n+12;}return out;}
const graph={atoms:[{id:'a',symbol:'C',x:1.2345678901234567,y:-0.000000123456789},{id:'b',symbol:'O',x:-0.12345678901234568,y:2.345678901234567}],bonds:[{id:'ab',begin:'a',end:'b',order:1}]};
test('molecular PNG retains exact graph numbers and IDAT, with independent runtime software identity',async()=>{
 const input=png([chunk('tEXt',enc.encode('Comment\0existing metadata'))]);
 const graphJson=snapshotCoordinates(graph),blob=await stampPng(input,{version:'0.9.0-test',graphJson});
 const bytes=new Uint8Array(await blob.arrayBuffer()),before=records(new Uint8Array(await input.arrayBuffer())),after=records(bytes);
 assert.deepEqual(after.find(x=>x.type==='IDAT'),before.find(x=>x.type==='IDAT'));
 assert.ok(after.some(x=>new TextDecoder().decode(x.payload)==='Comment\0existing metadata'));
 assert.ok(after.some(x=>new TextDecoder().decode(x.payload)==='Software\0nivraM 0.9.0-test'));
 const text=new TextDecoder().decode(after.find(x=>x.type==='iTXt').payload),record=JSON.parse(text.slice(text.indexOf('\0')+5));
 assert.deepEqual(JSON.parse(record.graphJson),graph);assert.equal(record.dimension,2);assert.equal(record.origin,'drawing-2d');
 assert.equal(record.graphSha256,Buffer.from(await crypto.subtle.digest('SHA-256',enc.encode(graphJson))).toString('hex'));
 if(process.env.NIVRAM_PNG_FIXTURE)writeFileSync(process.env.NIVRAM_PNG_FIXTURE,bytes);
});
test('chart-only PNG identifies software without invented coordinates',async()=>{
 const after=records(new Uint8Array(await (await stampPng(png(),{version:'9.8.7'})).arrayBuffer()));
 assert.equal(after.filter(x=>x.type==='iTXt').length,0);assert.equal(after.filter(x=>x.type==='tEXt').length,1);
});
test('malformed PNG, conflicting metadata and invalid graphs fail closed',async()=>{
 await assert.rejects(stampPng(new Blob(['not PNG'],{type:'image/png'}),{version:'1'}));
 await assert.rejects(stampPng(png([chunk('tEXt',enc.encode('Software\0older'))]),{version:'1'}));
 assert.throws(()=>snapshotCoordinates({...graph,analysis:{private:'secret'}}));
 assert.throws(()=>snapshotCoordinates({atoms:[{...graph.atoms[0],z:1}],bonds:[]}));
 await assert.rejects(stampPng(png(),{version:'1',graphJson:'x'.repeat(524289)}));
 const damaged=new Uint8Array(await png().arrayBuffer());damaged[20]^=1;
 await assert.rejects(stampPng(new Blob([damaged],{type:'image/png'}),{version:'1'}));
});
test('producer version comes from live status and missing/invalid status never invents a fallback',async()=>{
 const old=globalThis.fetch;try{
 globalThis.fetch=async(url,opts)=>{assert.equal(url,'/api/status');assert.equal(opts.cache,'no-store');return {ok:true,text:async()=>'{"version":"0.9.0-current"}'};};assert.equal(await runtimeVersion(),'0.9.0-current');
 for(const value of [null,'', 'bad\nversion']){globalThis.fetch=async()=>({ok:true,text:async()=>JSON.stringify({version:value})});await assert.rejects(runtimeVersion());}
 globalThis.fetch=async()=>({ok:false});await assert.rejects(runtimeVersion());
 }finally{globalThis.fetch=old;}
});

test('raw graph metadata rejects duplicate/noncanonical JSON, empty graphs and invalid Unicode without normalization',async()=>{
 const invalid=[
  '{"atoms":[],"atoms":[{"id":"a","symbol":"C","x":0,"y":0}],"bonds":[]}',
  '{"atoms":[],"bonds":[]}',
  JSON.stringify({atoms:[{id:'\ud800',symbol:'C',x:0,y:0}],bonds:[]}),
  JSON.stringify({atoms:[{id:'\udc00',symbol:'C',x:0,y:0}],bonds:[]}),
  JSON.stringify(graph,null,2),
 ];
 for(const graphJson of invalid)await assert.rejects(stampPng(png(),{version:'1',graphJson}));
 assert.throws(()=>snapshotCoordinates({atoms:[],bonds:[]}));
 assert.throws(()=>snapshotCoordinates({atoms:[{id:'\ud800',symbol:'C',x:0,y:0}],bonds:[]}));
 const valid={atoms:[{id:'atom-😀',symbol:'C',x:0.12345678901234568,y:-10000}],bonds:[]};
 const graphJson=snapshotCoordinates(valid),blob=await stampPng(png(),{version:'1',graphJson});
 const record=records(new Uint8Array(await blob.arrayBuffer())).find(x=>x.type==='iTXt');
 const text=new TextDecoder().decode(record.payload),payload=JSON.parse(text.slice(text.indexOf('\0')+5));
 assert.equal(payload.graphJson,graphJson);assert.deepEqual(JSON.parse(payload.graphJson),valid);
 if(process.env.NIVRAM_PNG_UNICODE_FIXTURE)writeFileSync(process.env.NIVRAM_PNG_UNICODE_FIXTURE,new Uint8Array(await blob.arrayBuffer()));
});
