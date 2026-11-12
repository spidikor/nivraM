import assert from 'node:assert/strict';
import {webcrypto,createHash} from 'node:crypto';
import {parseSuppliedMol,suppliedProvenance,projectSupplied} from '../public/modules/supplied-3d.mjs';
import {mountSupplied3d} from '../public/modules/supplied-3d-viewer.mjs';
import {createThreeDRecord} from '../public/modules/three-d-record.mjs';
import {deflateSync} from 'node:zlib';
import {stampPng,crc32} from '../public/modules/png-metadata.mjs';
function chunk(type,payload){const out=Buffer.alloc(payload.length+12);out.writeUInt32BE(payload.length);out.write(type,4);out.set(payload,8);out.writeUInt32BE(crc32(out.subarray(4,-4)),out.length-4);return out;}
const header=Buffer.alloc(13);header.writeUInt32BE(1);header.writeUInt32BE(1,4);header[8]=8;header[9]=2;
const tiny=Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',header),chunk('IDAT',deflateSync(Buffer.from([0,255,255,255]))),chunk('IEND',Buffer.alloc(0))]);
class Node {constructor(id=''){this.id=id;this.handlers={};this.children=[];this.attrs={};this.disabled=true;this.classList={toggle(){}};this.dataset={};this.files=[];this.textContent='';}addEventListener(t,f){(this.handlers[t]??=[]).push(f)}removeEventListener(t,f){this.handlers[t]=this.handlers[t].filter(x=>x!==f)}async fire(t,e={}){for(const f of this.handlers[t]||[])await f(e)}append(...v){this.children.push(...v)}replaceChildren(...v){this.children=v}setAttribute(k,v){this.attrs[k]=v}querySelectorAll(){return this.buttons||[]}focus(){this.focused=true}showModal(){this.open=true}close(){this.open=false;this.fire('close')}setPointerCapture(){} }
const ids=['dialog','open','close','file','scene','status','atoms','bonds','provenance','export','export-provenance','export-png','png-file','cancel'];const nodes=Object.fromEntries(ids.map(id=>['supplied-3d-'+id,new Node(id)]));nodes['supplied-3d-dialog'].buttons=['left','right','up','down','in','out','fit'].map(action=>{const n=new Node();n.dataset['3dAction']=action;return n});const dom={getElementById:id=>nodes[id],createElement:()=>new Node(),createElementNS:()=>new Node()};
const atom=(x,y,z,e)=>[x,y,z].map(n=>n.toFixed(4).padStart(10)).join('')+' '+e.padEnd(3)+' 0  0  0  0  0  0  0  0  0  0  0  0';const raw=['water',' '.repeat(20)+'3D','','  3  2  0  0  0  0            999 V2000',atom(0,0,0,'O'),atom(.7,.5,.2,'H'),atom(-.7,.5,-.2,'H'),'  1  2  1  0  0  0  0','  1  3  1  0  0  0  0','M  END',''].join('\r\n');const bytes=new TextEncoder().encode(raw);const m=parseSuppliedMol(bytes);const p=await suppliedProvenance(m,webcrypto);assert.equal(p.sourceSha256,createHash('sha256').update(bytes).digest('hex'));assert.equal(p.generated,false);assert.deepEqual(new TextEncoder().encode(m.sourceText),bytes);assert.notDeepEqual(projectSupplied(m),projectSupplied(m,{yaw:1}));assert.equal(m.atoms[1].z,.2);
for(const bad of [raw.replace('3D','2D'),raw+'$$$$',raw.replace('V2000','V3000'),raw.replace('M  END','M  CHG  1  1  1\r\nM  END'),raw.replace('  1  2  1  0','  1  2  1  1')])assert.throws(()=>parseSuppliedMol(new TextEncoder().encode(bad)));

const defer=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};
let downloads=[],gate=null,snapshots=[];
const ctl=mountSupplied3d({dom,cryptoApi:webcrypto,download:(...a)=>downloads.push(a),sceneSnapshot:()=>'<svg/>',pngExport:async s=>{snapshots.push(s);if(gate)await gate.promise;return new Blob(['png']);}});
ctl.open();assert(await ctl.loadBytes(bytes));
assert.equal(await ctl.exportPng(),true);assert.equal(snapshots[0].model.sourceText,raw);assert.equal(downloads[0][2],'nivram-3d.png');
gate=defer();let pending=ctl.exportPng();await nodes['supplied-3d-dialog'].buttons[1].fire('click');gate.resolve();assert(await pending,'camera gestures permit captured immutable view publication');
gate=defer();pending=ctl.exportPng();ctl.close();gate.resolve();assert.equal(await pending,false);
ctl.open();gate=defer();pending=ctl.exportPng();await ctl.loadBytes(bytes);gate.resolve();assert.equal(await pending,false);gate=null;
const record=await createThreeDRecord({model:m,provenance:p,sourceContextText:null},'1.2.3');
const png=await stampPng(new Blob([tiny],{type:'image/png'}),{coordinateRecord:record,version:'1.2.3'});
assert(await ctl.loadPng(new Uint8Array(await png.arrayBuffer())));
assert.equal(ctl.getState().model.kind,'recovered_coordinates');assert.equal(ctl.getState().model.sourceText,raw);
assert.equal(ctl.getState().provenance.generated,false);assert.equal(ctl.getState().provenance.sourceBindingReverified,false);
assert.match(nodes['supplied-3d-scene'].attrs['aria-label'],/^Recovered/);
await nodes['supplied-3d-export'].fire('click');assert.equal(downloads.at(-1)[2],'recovered-3d.mol');assert.deepEqual(downloads.at(-1)[0],bytes);
await ctl.exportPng();assert.equal(snapshots.at(-1).record.molText,raw);
const previous=ctl.getState().model;assert.equal(await ctl.loadPng(new Uint8Array([1,2,3])),false);assert.equal(ctl.getState().model,previous);
ctl.destroy();console.log('PASS 3D PNG supplied recovery, detached trust, exact MOL export, immutable snapshots, camera/model/close fences (fake DOM; not browser qualification).');
// Raster allocation and cleanup are tested with injected adapters, not a browser.
const {rasterizeScene,snapshotScene}=await import('../public/modules/three-d-png.mjs');
let revoked=[],paint=[],canvas;
class FakeImage{async decode(){}};
const rasterDom={fonts:{ready:Promise.resolve()},createElement(){canvas={getContext:()=>({fillRect:(...a)=>paint.push(a),drawImage:(...a)=>paint.push(a)}),toBlob:fn=>fn(new Blob([tiny],{type:'image/png'}))};return canvas;}};
await rasterizeScene('<svg/>',{dom:rasterDom,ImageClass:FakeImage,urlApi:{createObjectURL:()=> 'blob:test',revokeObjectURL:u=>revoked.push(u)}});
assert.deepEqual(paint[0],[0,0,1280,800]);assert.equal(canvas.width,0);assert.equal(canvas.height,0);assert.deepEqual(revoked,['blob:test']);
class BadImage{async decode(){throw new Error('decode failed');}};
await assert.rejects(rasterizeScene('<svg/>',{dom:rasterDom,ImageClass:BadImage,urlApi:{createObjectURL:()=> 'blob:failed',revokeObjectURL:u=>revoked.push(u)}}),/decode failed/);assert.equal(revoked.at(-1),'blob:failed');
assert.throws(()=>snapshotScene({children:[{localName:'image'}]},dom),/Unsupported/);
console.log('PASS fixed raster dimensions and cleanup after success/decode failure; external scene primitive rejected. Adapter tests only.');
// New schema5 RAD declarations must reopen without a native chemistry reroute.
const radicalCtl=mountSupplied3d({dom,cryptoApi:webcrypto,download:(...a)=>downloads.push(a),request:()=>{throw new Error('Supplied radicals must not invoke native chemistry');},sceneSnapshot:()=>'<svg/>',pngExport:async s=>{snapshots.push(s);return new Blob(['png']);}});
radicalCtl.open();
for(const code of [0,1,2,3]){
 const text=raw.replace('M  END',`M  RAD  1   2   ${code}\r\nM  END`),model=parseSuppliedMol(new TextEncoder().encode(text));
 assert(await radicalCtl.loadBytes(new TextEncoder().encode(text)));assert.match(nodes['supplied-3d-atoms'].children[1].children.at(-1).textContent,new RegExp(`RAD${code} .* declaration`));
 const r=await createThreeDRecord({model,provenance:await suppliedProvenance(model)},'test');
 const blob=await stampPng(new Blob([tiny],{type:'image/png'}),{coordinateRecord:r,version:'test'});assert(await radicalCtl.loadPng(new Uint8Array(await blob.arrayBuffer())));
 assert.equal(radicalCtl.getState().model.atoms[1].radicalCode,code);await nodes['supplied-3d-export'].fire('click');assert.equal(new TextDecoder().decode(downloads.at(-1)[0]),text);
 await radicalCtl.exportPng();assert.equal(snapshots.at(-1).record.schema,'nivram-png-coordinates/5');assert.equal(snapshots.at(-1).record.molText,text);
}
radicalCtl.destroy();console.log('PASS RAD0/1/2/3 fake-DOM table, exact MOL export, PNG recovery/re-export and no native worker reroute.');
