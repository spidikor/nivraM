import test from 'node:test';
import assert from 'node:assert/strict';
import {deflateSync} from 'node:zlib';
import {createThreeDRecord,validateThreeDRecord,parseRecordJson} from '../public/modules/three-d-record.mjs';
import {parseSuppliedMol,suppliedProvenance} from '../public/modules/supplied-3d.mjs';
import {stampPng,extractThreeDRecord,crc32} from '../public/modules/png-metadata.mjs';
const enc=new TextEncoder();
const raw=['fixture',' '.repeat(20)+'3D','','  1  0  0  0  0  0            999 V2000','   -0.0000   +0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0','M  END'].join('\r\n');
const model=parseSuppliedMol(enc.encode(raw)),provenance=await suppliedProvenance(model),record=await createThreeDRecord({model,provenance},'old');
function chunk(type,payload){const b=Buffer.alloc(payload.length+12);b.writeUInt32BE(payload.length);b.write(type,4);b.set(payload,8);b.writeUInt32BE(crc32(b.subarray(4,-4)),b.length-4);return b;}
function png(extra=[]){const h=Buffer.alloc(13);h.writeUInt32BE(1);h.writeUInt32BE(1,4);h[8]=8;h[9]=2;return new Blob([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',h),...extra,chunk('IDAT',deflateSync(Buffer.alloc(4))),chunk('IEND',Buffer.alloc(0))],{type:'image/png'});}
const native=r=>chunk('iTXt',enc.encode(`nivraM Coordinates\0\0\0\0\0${typeof r==='string'?r:JSON.stringify(r)}`));
test('record exact source and historical claims survive re-export with current runtime',async()=>{
 const blob=await stampPng(png(),{coordinateRecord:record,version:'new'}),out=await extractThreeDRecord(blob);
 assert.equal(out.version,'new');assert.equal(out.molText,raw);assert.equal(out.provenanceJson,record.provenanceJson);assert.equal(record.version,'old');
});
test('reject duplicates, unsafe JSON, depth, Unicode, private fields and hashes',async()=>{
 for(const s of ['{"a":1,"a":2}','{"constructor":1}','{"a":"\\ud800"}','['.repeat(14)+'0'+']'.repeat(14),'1e999','{} trailing'])assert.throws(()=>parseRecordJson(s));
 for(const change of [{private:'secret'},{dimension:2},{molText:raw+' '},{molBytes:0},{sourceContextText:'null'},{provenanceJson:record.provenanceJson+' '}])await assert.rejects(validateThreeDRecord({...record,...change}));
 await assert.rejects(validateThreeDRecord(JSON.stringify(record).replace('"dimension":3','"dimension":3,"dimension":3')));
});
test('reject mixed native records, compressed native, missing and damaged PNG framing',async()=>{
 await assert.rejects(extractThreeDRecord(png([native(record),native(record)])));
 await assert.rejects(extractThreeDRecord(png([chunk('zTXt',enc.encode('nivraM Coordinates\0\0compressed'))])));
 await assert.rejects(extractThreeDRecord(png()));
 await assert.rejects(stampPng(png(),{coordinateRecord:record,graphJson:'{}',version:'1'}));
 const bytes=new Uint8Array(await png([native(record)]).arrayBuffer());bytes[20]^=1;await assert.rejects(extractThreeDRecord(bytes));
 const good=new Uint8Array(await png([native(record)]).arrayBuffer());await assert.rejects(extractThreeDRecord(good.subarray(0,-1)));
});
