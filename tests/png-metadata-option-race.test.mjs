/** Regression derived from independent reviewer's V1 reproduction; adapter only. */
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {mountSupplied3d} from '../public/modules/supplied-3d-viewer.mjs';
globalThis.crypto??=webcrypto;
class Node {constructor(id=''){this.id=id;this.handlers={};this.children=[];this.attrs={};this.disabled=false;this.checked=false;this.classList={toggle(){}};this.dataset={};this.files=[];this.textContent='';}addEventListener(t,f){(this.handlers[t]??=[]).push(f)}removeEventListener(t,f){this.handlers[t]=this.handlers[t].filter(x=>x!==f)}async fire(t,e={}){for(const f of this.handlers[t]||[])await f(e)}append(...v){this.children.push(...v)}replaceChildren(...v){this.children=v}setAttribute(k,v){this.attrs[k]=v}querySelectorAll(){return []}focus(){}showModal(){this.open=true}close(){this.open=false;this.fire('close')}setPointerCapture(){}}
const ids=['dialog','open','close','file','scene','status','atoms','bonds','provenance','export','export-provenance','export-png','png-file','cancel','remove-chemistry-metadata'];
const nodes=Object.fromEntries(ids.map(id=>['supplied-3d-'+id,new Node(id)]));const dom={getElementById:id=>nodes[id],createElement:()=>new Node(),createElementNS:()=>new Node()};
const atom=(x,y,z,e)=>[x,y,z].map(n=>n.toFixed(4).padStart(10)).join('')+' '+e.padEnd(3)+' 0  0  0  0  0  0  0  0  0  0  0  0';
const raw=['water',' '.repeat(20)+'3D','','  3  2  0  0  0  0            999 V2000',atom(0,0,0,'O'),atom(.7,.5,.2,'H'),atom(-.7,.5,-.2,'H'),'  1  2  1  0  0  0  0','  1  3  1  0  0  0  0','M  END',''].join('\r\n');
let release, captured,downloads=[];const gate=new Promise(r=>release=r);
const view=mountSupplied3d({dom,cryptoApi:webcrypto,download:(...args)=>downloads.push(args),sceneSnapshot:()=>'<svg/>',pngExport:async(s,options)=>{captured=options;await gate;return new Blob(['stub']);}});
view.open();assert(await view.loadBytes(new TextEncoder().encode(raw)));
const removal=nodes['supplied-3d-remove-chemistry-metadata'];const promise=view.exportPng();
const locked=removal.disabled;
if(!locked){removal.checked=true;await removal.fire('change');}
release();const published=await promise;
const safe=locked||!published;
console.log(JSON.stringify({test:'3D in-flight privacy choice',capturedFlag:captured.removeChemistryMetadata,locked,visibleChecked:removal.checked,published,downloadCount:downloads.length,safe},null,2));
assert(safe,'Unchecked 3D export publishes metadata-bearing result after user can check Remove chemistry metadata while it is pending.');

assert.equal(downloads.length,0);assert(await view.exportPng());assert.equal(captured.removeChemistryMetadata,true);assert.equal(downloads.length,1);assert.match(nodes['supplied-3d-status'].textContent,/without recoverable/);view.destroy();
