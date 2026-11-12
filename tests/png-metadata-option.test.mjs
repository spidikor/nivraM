/** Controlled adapter tests, real PNG byte checks. Not browser/visual qualification. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {deflateSync,inflateSync} from 'node:zlib';
import {webcrypto} from 'node:crypto';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {stampPng,crc32,extractThreeDRecord} from '../public/modules/png-metadata.mjs';
import {snapshotCoordinates} from '../public/modules/png-coordinates.mjs';
import {exportPng} from '../public/modules/export.mjs';
import {exportThreeDPng} from '../public/modules/three-d-png.mjs';
import {parseSuppliedMol,suppliedProvenance} from '../public/modules/supplied-3d.mjs';
import {createThreeDRecord} from '../public/modules/three-d-record.mjs';
import {createBatchExport} from '../public/modules/batch-export.mjs';
import {validateProject} from '../public/modules/document.mjs';
globalThis.crypto??=webcrypto;
const graph={atoms:[{id:'a',symbol:'C',x:1.2345678901234567,y:-0.000000123456789,isotope:13,mapNum:2,charge:1},{id:'b',symbol:'O',x:-.12345678901234568,y:2.345678901234567}],bonds:[{id:'ab',begin:'a',end:'b',order:1}]};
function chunk(type,payload){const out=Buffer.alloc(payload.length+12);out.writeUInt32BE(payload.length);out.write(type,4);out.set(payload,8);out.writeUInt32BE(crc32(out.subarray(4,-4)),out.length-4);return out;}
function png(w=3,h=3,extra=[]){const header=Buffer.alloc(13);header.writeUInt32BE(w);header.writeUInt32BE(h,4);header[8]=8;header[9]=6;const rows=Buffer.alloc((w*4+1)*h,255);for(let i=0;i<h;i++)rows[i*(w*4+1)]=0;return new Blob([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',header),...extra,chunk('IDAT',deflateSync(rows)),chunk('IEND',Buffer.alloc(0))],{type:'image/png'});}
async function chunks(blob){const b=Buffer.from(await blob.arrayBuffer()),out=[];for(let i=8;i<b.length;){const n=b.readUInt32BE(i),type=b.toString('ascii',i+4,i+8);assert.equal(crc32(b.subarray(i+4,i+8+n)),b.readUInt32BE(i+8+n));out.push({type,bytes:b.subarray(i,i+n+12),payload:b.subarray(i+8,i+8+n)});i+=n+12;}return out;}
async function noChem(blob){const c=await chunks(blob);assert.deepEqual(c.filter(x=>['tEXt','iTXt','zTXt'].includes(x.type)).map(x=>x.payload.toString()),['Software\0nivraM test']);return c;}
const write=(name,blob)=>{if(process.env.NIVRAM_METADATA_EVIDENCE)return blob.arrayBuffer().then(b=>{mkdirSync(process.env.NIVRAM_METADATA_EVIDENCE,{recursive:true});writeFileSync(process.env.NIVRAM_METADATA_EVIDENCE+'/'+name,Buffer.from(b));});};
test('default and explicit false are byte-identical, exact 2D recovery retained, true only changes metadata',async()=>{
 const input=png(),graphJson=snapshotCoordinates(graph),before=JSON.stringify(graph);
 const outputs=[];for(const flag of [undefined,false,true])outputs.push(await stampPng(input,{version:'test',graphJson,...(flag===undefined?{}:{removeChemistryMetadata:flag})}));
 assert.deepEqual(await outputs[0].arrayBuffer(),await outputs[1].arrayBuffer());
 const full=await chunks(outputs[0]),clean=await noChem(outputs[2]);
 const record=JSON.parse(full.find(c=>c.type==='iTXt').payload.toString().split('\0').at(-1));assert.equal(record.graphJson,graphJson);assert.deepEqual(JSON.parse(record.graphJson),graph);assert.equal(JSON.stringify(graph),before);
 assert.deepEqual(clean.filter(c=>c.type!=='tEXt'),full.filter(c=>!['tEXt','iTXt'].includes(c.type)));assert.deepEqual(inflateSync(clean.find(c=>c.type==='IDAT').payload),inflateSync(full.find(c=>c.type==='IDAT').payload));
 await write('2d-retained.png',outputs[0]);await write('2d-removed.png',outputs[2]);
});
test('removal handles native key in all text chunk encodings and fails on unexpected metadata or corrupt framing',async()=>{
 for(const type of ['tEXt','zTXt','iTXt']){
  const b=png(3,3,[chunk(type,Buffer.from('nivraM Coordinates\0hidden'))]);await noChem(await stampPng(b,{version:'test',removeChemistryMetadata:true}));
 }
 for(const type of ['tEXt','zTXt','iTXt','eXIf','aaAa'])await assert.rejects(stampPng(png(3,3,[chunk(type,Buffer.from('Comment\0secret chemistry'))]),{version:'test',removeChemistryMetadata:true}));
 const significant=png(3,3,[chunk('sBIT',Buffer.from([8,8,8,8]))]);
 assert.deepEqual((await chunks(await stampPng(significant,{version:'test',removeChemistryMetadata:true}))).find(c=>c.type==='sBIT').payload,Buffer.from([8,8,8,8]));
 for(const value of [[8,8,8],[8,8,8,9],[8,8,8,0],Array(100).fill(8)])await assert.rejects(stampPng(png(3,3,[chunk('sBIT',Buffer.from(value))]),{version:'test',removeChemistryMetadata:true}));
 const bad=Buffer.from(await png().arrayBuffer());bad[30]^=1;await assert.rejects(stampPng(new Blob([bad],{type:'image/png'}),{version:'test',removeChemistryMetadata:true}));
 for(const flag of [null,0,1,'true',{},[]]){await assert.rejects(stampPng(png(),{version:'test',removeChemistryMetadata:flag}));await assert.rejects(exportPng(null,{}, {removeChemistryMetadata:flag}));await assert.rejects(exportThreeDPng({}, {removeChemistryMetadata:flag}));}
});
test('3D supplied and recovered export drops complete MOL/provenance record only when selected',async()=>{
 const atom=(x,y,z,e)=>[x,y,z].map(n=>n.toFixed(4).padStart(10)).join('')+' '+e.padEnd(3)+' 0  0  0  0  0  0  0  0  0  0  0  0';
 const mol=['water',' '.repeat(20)+'3D','','  3  2  0  0  0  0            999 V2000',atom(0,0,0,'O'),atom(.7,.5,.2,'H'),atom(-.7,.5,-.2,'H'),'  1  2  1  0  0  0  0','  1  3  1  0  0  0  0','M  END',''].join('\r\n');
 const model=parseSuppliedMol(new TextEncoder().encode(mol)),snapshot={model,provenance:await suppliedProvenance(model),sourceContextText:null,svgText:'<svg/>'};
 for(const recovered of [false,true]){
  const s=recovered?{...snapshot,record:await createThreeDRecord(snapshot,'historical')}:snapshot,before=JSON.stringify(s),outputs=[];
  for(const flag of [undefined,false,true])outputs.push(await exportThreeDPng(s,{version:async()=>'test',rasterize:async()=>png(),...(flag===undefined?{}:{removeChemistryMetadata:flag})}));
  assert.deepEqual(await outputs[0].arrayBuffer(),await outputs[1].arrayBuffer());assert.equal((await extractThreeDRecord(outputs[0])).molText,mol);await assert.rejects(extractThreeDRecord(outputs[2]),/Missing/);await noChem(outputs[2]);assert.equal(JSON.stringify(s),before);
  assert.deepEqual((await chunks(outputs[0])).filter(c=>!['iTXt','tEXt'].includes(c.type)),(await chunks(outputs[2])).filter(c=>c.type!=='tEXt'));
  await write(`3d-${recovered?'recovered':'supplied'}-retained.png`,outputs[0]);await write(`3d-${recovered?'recovered':'supplied'}-removed.png`,outputs[2]);
 }
});
test('full 2D export flow captures flag and preserves raster bytes, graph and options',async t=>{
 const previous=new Map(),set=(key,value)=>{previous.set(key,Object.getOwnPropertyDescriptor(globalThis,key));Object.defineProperty(globalThis,key,{configurable:true,value});};
 t.after(()=>{for(const [key,value] of previous)value?Object.defineProperty(globalThis,key,value):delete globalThis[key];});
 const draws=[],urls=new Set();let seq=0;
 set('document',{fonts:{ready:Promise.resolve()},body:{append(){}},createElementNS(){return {style:{},setAttribute(){},append(){},remove(){}};},createElement(){const c={width:1,height:1,getContext:()=>({fillRect(){},drawImage(...a){draws.push(a.slice(1));},getImageData(){const data=new Uint8ClampedArray(c.width*c.height*4);for(let y=8;y<c.height-8;y++)for(let x=8;x<c.width-8;x++)data[(y*c.width+x)*4+3]=255;return {data};}}),toBlob:fn=>fn(png(c.width,c.height))};return c;}});
 set('Image',class{async decode(){}removeAttribute(){}});set('XMLSerializer',class{serializeToString(){return '<g><path d="M 0 0 L 100 100"/></g>';}});set('getComputedStyle',()=>({stroke:'none'}));set('fetch',async()=>({ok:true,text:async()=>'{"version":"test"}'}));
 t.mock.method(URL,'createObjectURL',()=>{const u='blob:test'+seq++;urls.add(u);return u;});t.mock.method(URL,'revokeObjectURL',u=>urls.delete(u));
 const group={nodeType:1,removeAttribute(){},remove(){},cloneNode(){return {...this};},querySelector(){return {};},querySelectorAll(){return [];},getBBox(){return {x:0,y:0,width:100,height:100};}},options={width:100,marginMode:'pixels',margin:2},before=JSON.stringify({graph,options}),outputs=[];
 for(const flag of [false,true])outputs.push(await exportPng(group,options,{graph,removeChemistryMetadata:flag}));
 assert.equal(outputs[1].removeChemistryMetadata,true);assert.equal(JSON.stringify({graph,options}),before);assert.equal(urls.size,0);await noChem(outputs[1].blob);
 assert.deepEqual((await chunks(outputs[0].blob)).filter(c=>!['iTXt','tEXt'].includes(c.type)),(await chunks(outputs[1].blob)).filter(c=>c.type!=='tEXt'));assert.equal(outputs[0].width,outputs[1].width);assert.equal(outputs[0].height,outputs[1].height);
});
test('batch setting freezes before chooser awaits; renderer receives separate flag, legacy omission stays false',async()=>{
 for(const flag of [undefined,false,true]){
  const settings={style:{},export:{width:40,margin:0,marginMode:'pixels'},profile:null,...(flag===undefined?{}:{removeChemistryMetadata:flag})};let seen;
  const native={async begin_batch_export(s){seen=s;settings.removeChemistryMetadata=!flag;return {status:'running',sessionId:'x',items:[{id:'a',status:'pending'}]};},async prepare_batch_item(){return {status:'ready',item:{id:'a',status:'rendering'}};},async write_batch_png(){return {status:'exported',item:{id:'a',status:'exported'}};},async finish_batch_export(){return {status:'completed',items:[]};}};
  const runner=createBatchExport({native,render:async(_,{removeChemistryMetadata})=>{assert.equal(removeChemistryMetadata,flag??false);assert(Object.isFrozen(seen));return {blob:png()};},encode:async()=> 'payload'});await runner.run(settings);
 }
 for(const flag of [null,0,'true',{}]){const runner=createBatchExport({native:{begin_batch_export(){assert.fail('must not call chooser');}}});await assert.rejects(runner.run({export:{width:20},removeChemistryMetadata:flag}));}
});
test('exact label, off controls, session-only wiring; chart-only output unchanged',()=>{
 const html=readFileSync(new URL('../public/index.html',import.meta.url),'utf8');for(const id of ['remove-chemistry-metadata','supplied-3d-remove-chemistry-metadata']){const match=html.match(new RegExp(`<input id="${id}"[^>]*> Remove chemistry metadata\\?`));assert(match);assert(!/\bchecked\b/.test(match[0]));}
 for(const path of ['profiles','batch-export']){const text=readFileSync(new URL(`../public/modules/${path}.mjs`,import.meta.url),'utf8');assert(text.includes("type:'checkbox',checked:false"));assert(text.includes("' Remove chemistry metadata?'"));}
 assert.throws(()=>validateProject({schema:'nivram/1',graph,export:{width:100,margin:0,marginMode:'pixels',removeChemistryMetadata:true}}));
});
