/** Regression derived from independent V2 review; controlled pending generation. */
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {mountSupplied3d} from '../public/modules/supplied-3d-viewer.mjs';
globalThis.crypto??=webcrypto;
class Node {constructor(id=''){this.id=id;this.handlers={};this.children=[];this.attrs={};this.disabled=false;this.checked=false;this.classList={toggle(){}};this.dataset={};this.files=[];this.textContent='';}addEventListener(t,f){(this.handlers[t]??=[]).push(f)}removeEventListener(t,f){this.handlers[t]=this.handlers[t].filter(x=>x!==f)}async fire(t,e={}){for(const f of this.handlers[t]||[])await f(e)}append(...v){this.children.push(...v)}replaceChildren(...v){this.children=v}setAttribute(k,v){this.attrs[k]=v}querySelectorAll(){return []}focus(){}showModal(){this.open=true}close(){this.open=false;this.fire('close')}setPointerCapture(){}}
const ids=['dialog','open','close','file','scene','status','atoms','bonds','provenance','export','export-provenance','export-png','png-file','cancel','remove-chemistry-metadata'];
const nodes=Object.fromEntries(ids.map(id=>['supplied-3d-'+id,new Node(id)]));const dom={getElementById:id=>nodes[id],createElement:()=>new Node(),createElementNS:()=>new Node()};
let release;const ready=new Promise(r=>release=r);
const view=mountSupplied3d({dom,cryptoApi:webcrypto,getDocument:()=>({ready}),request:async()=>{throw Error('No request expected');}});
view.open();const pending=view.generate();const button=nodes['supplied-3d-cancel'],removal=nodes['supplied-3d-remove-chemistry-metadata'];
assert.equal(button.disabled,false);const previousText=nodes['supplied-3d-status'].textContent;removal.checked=true;await removal.fire('change');const disabled=button.disabled;assert.equal(nodes['supplied-3d-status'].textContent,previousText);
console.log(JSON.stringify({test:'Metadata toggle during conformer generation',cancelDisabledBeforeToggle:false,cancelDisabledAfterToggle:disabled}));
view.destroy();release();await pending;assert.equal(disabled,false,'A metadata-only change must not disable the pending conformer Cancel operation');
