/** Controlled download dispatch only. No browser, file picker or saved-file qualification. */
import assert from 'node:assert/strict';
import test from 'node:test';
import {webcrypto} from 'node:crypto';
import {mountSupplied3d} from '../public/modules/supplied-3d-viewer.mjs';

class Node {
  constructor(){this.handlers={};this.children=[];this.attrs={};this.disabled=false;this.checked=false;this.textContent='';this.files=[];this.classList={toggle(){}};}
  addEventListener(type,fn){(this.handlers[type]??=[]).push(fn);}
  removeEventListener(type,fn){this.handlers[type]=(this.handlers[type]??[]).filter(value=>value!==fn);}
  async fire(type,event={}){for(const fn of this.handlers[type]??[])await fn(event);}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(key,value){this.attrs[key]=value;}
  querySelectorAll(){return [];}
  focus(){}
  showModal(){this.open=true;}
  close(){this.open=false;this.fire('close');}
  remove(){}
}
const atom=(x,y,z,element)=>[x,y,z].map(value=>value.toFixed(4).padStart(10)).join('')+' '+element.padEnd(3)+' 0  0  0  0  0  0  0  0  0  0  0  0';
const mol=['water',' '.repeat(20)+'3D','','  3  2  0  0  0  0            999 V2000',
  atom(0,0,0,'O'),atom(.7,.5,.2,'H'),atom(-.7,.5,-.2,'H'),
  '  1  2  1  0  0  0  0','  1  3  1  0  0  0  0','M  END',''].join('\r\n');
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
async function fixture(t,{remove=false,nativeAnchor=false,downloadError=null,exporter=null}={}){
  const ids=['dialog','open','close','file','scene','status','atoms','bonds','provenance','export','export-provenance',
    'export-png','png-file','cancel','remove-chemistry-metadata'];
  const nodes=Object.fromEntries(ids.map(id=>['supplied-3d-'+id,new Node()])),n=id=>nodes['supplied-3d-'+id];
  const dispatches=[],encodings=[],anchors=[],blob=new Blob(['controlled PNG encoding'],{type:'image/png'});
  const dom={body:new Node(),getElementById:id=>nodes[id],createElement:type=>{
    const node=new Node();if(type==='a'){node.click=()=>{anchors.push({name:node.download,href:node.href});};}return node;
  },createElementNS:()=>new Node()};
  const download=nativeAnchor?undefined:(...args)=>{if(downloadError)throw downloadError;dispatches.push(args);};
  const view=mountSupplied3d({dom,cryptoApi:webcrypto,download,sceneSnapshot:()=>'<svg/>',pngExport:async(snapshot,options)=>{
    encodings.push({snapshot,options});return exporter?exporter(blob):blob;
  }});
  t.after(()=>view.destroy());view.open();assert.equal(await view.loadBytes(new TextEncoder().encode(mol)),true);
  n('remove-chemistry-metadata').checked=remove;
  return {view,n,blob,dispatches,encodings,anchors};
}
function notSaved(text){assert.doesNotMatch(text,/3D PNG exported|saved and verified|successfully saved|save complete/i);}
function prepared(text){assert.match(text,/3D PNG prepared/);assert.match(text,/save\/download dialog/);assert.match(text,/Saving is not confirmed here\./);notSaved(text);}

for(const remove of [false,true])test(`PNG ${remove?'removed':'retained'} metadata dispatch does not claim a confirmed save`,async t=>{
  const h=await fixture(t,{remove}),before=structuredClone(h.view.getState());
  assert.equal(await h.view.exportPng(),true);
  assert.equal(h.dispatches.length,1);assert.equal(h.dispatches[0][0],h.blob);
  assert.deepEqual(h.dispatches[0].slice(1),['image/png','nivram-3d.png']);
  assert.equal(h.encodings[0].options.removeChemistryMetadata,remove);
  assert.deepEqual(h.view.getState(),before);
  const text=h.n('status').textContent;prepared(text);
  if(remove){assert.match(text,/without recoverable chemistry metadata/);assert.match(text,/Visible artwork and the software label remain/);}
  else {assert.match(text,/with exact MOL coordinates and provenance/);assert.match(text,/Anyone receiving the PNG can recover its metadata/);}
});

test('default anchor dispatch has no save receipt even when click returns normally',async t=>{
  const h=await fixture(t,{nativeAnchor:true});assert.equal(await h.view.exportPng(),true);
  assert.equal(h.anchors.length,1);assert.equal(h.anchors[0].name,'nivram-3d.png');assert.match(h.anchors[0].href,/^blob:/);
  assert.equal(h.dispatches.length,0);prepared(h.n('status').textContent);
});

test('pending encoding and cancellation do not dispatch or announce completion',async t=>{
  const gate=deferred(),h=await fixture(t,{exporter:async blob=>{await gate.promise;return blob;}});
  const pending=h.view.exportPng();assert.equal(h.dispatches.length,0);notSaved(h.n('status').textContent);
  await h.n('cancel').fire('click');const cancelled=h.n('status').textContent;
  gate.resolve();assert.equal(await pending,false);assert.equal(h.dispatches.length,0);
  assert.equal(h.n('status').textContent,cancelled);assert.match(cancelled,/cancelled/);notSaved(cancelled);
});

test('synchronous dispatch failure returns false without a success claim',async t=>{
  const h=await fixture(t,{downloadError:new Error('Download dispatch unavailable')});
  assert.equal(await h.view.exportPng(),false);assert.equal(h.dispatches.length,0);
  assert.match(h.n('status').textContent,/PNG not exported: Download dispatch unavailable/);notSaved(h.n('status').textContent);
});

test('encoding failure does not request a download or claim a saved PNG',async t=>{
  const h=await fixture(t,{exporter:async()=>{throw new Error('Encoding unavailable');}});
  assert.equal(await h.view.exportPng(),false);assert.equal(h.dispatches.length,0);
  assert.match(h.n('status').textContent,/PNG not exported: Encoding unavailable/);notSaved(h.n('status').textContent);
});
