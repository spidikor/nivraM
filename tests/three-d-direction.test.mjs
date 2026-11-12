import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import {mountSupplied3d} from '../public/modules/supplied-3d-viewer.mjs';
class Node {
  constructor(){this.handlers={};this.children=[];this.attrs={};this.classList={toggle(){}};this.checked=false;}
  addEventListener(t,f){(this.handlers[t]??=[]).push(f);}
  removeEventListener(t,f){this.handlers[t]=this.handlers[t].filter(x=>x!==f);}
  async fire(t,e={}){for(const f of this.handlers[t]||[])await f(e);}
  append(...a){this.children.push(...a);} replaceChildren(...a){this.children=a;}
  setAttribute(k,v){this.attrs[k]=v;} querySelectorAll(){return [];} focus(){}
  showModal(){this.open=true;} close(){this.open=false;this.fire('close');}
}
const ids=['dialog','open','close','file','scene','status','atoms','bonds','provenance','export','export-provenance','export-png','png-file','generate','cancel','style','hydrogens','rotate','direction','display-note'];
const atom=z=>[0,0,z].map(n=>n.toFixed(4).padStart(10)).join('')+' C   0  0  0  0  0  0  0  0  0  0  0  0';
const raw=['direction probe',' '.repeat(20)+'3D','','  2  0  0  0  0  0            999 V2000',atom(1),atom(-1),'M  END',''].join('\r\n');
const html=readFileSync(new URL('../public/index.html',import.meta.url),'utf8');
assert.match(html,/<label for="supplied-3d-direction">Rotation direction <select id="supplied-3d-direction"><option value="left-to-right">Left to right<\/option><option value="right-to-left">Right to left<\/option><\/select><\/label>/);
assert.match(html,/<input id="supplied-3d-rotate" type="checkbox"> Slow rotate/);
for(const selected of ['left-to-right','right-to-left']){
  const nodes=Object.fromEntries(ids.map(k=>[k,new Node()])),n=k=>nodes[k];
  const dom=Object.assign(new Node(),{getElementById:id=>nodes[id.replace('supplied-3d-','')],createElement:()=>new Node(),createElementNS:()=>new Node()});
  const motion=new Node();motion.matches=false;
  let id=0,frames=new Map(),saved=[],snapshot;
  const requestFrame=fn=>{frames.set(++id,fn);return id;},cancelFrame=id=>frames.delete(id);
  const tick=now=>{const tasks=[...frames.values()];frames.clear();tasks.forEach(fn=>fn(now));};
  const ctl=mountSupplied3d({dom,cryptoApi:webcrypto,motionQuery:motion,requestFrame,cancelFrame,download:(...a)=>saved.push(a),sceneSnapshot:()=>JSON.stringify(n('scene').children),pngExport:async s=>{snapshot=s;return new Blob(['test']);}});
  const choose=async value=>{n('direction').value=value;await n('direction').fire('change');};
  const start=async()=>{n('rotate').checked=true;await n('rotate').fire('change');};
  const front=()=>n('scene').children.find(g=>g.attrs['data-atom']==='1').children[1].attrs;
  assert.equal(n('direction').value,'left-to-right');assert.equal(ctl.getState().display.direction,'left-to-right');
  await choose(selected);assert.equal(frames.size,0);assert.equal(n('rotate').checked,false);
  await start();assert.equal(frames.size,0); // No loaded/open view, no motion.
  ctl.open();assert(await ctl.loadBytes(new TextEncoder().encode(raw)));
  const source=structuredClone(ctl.getState().model),provenance=structuredClone(ctl.getState().provenance);
  assert.equal(n('rotate').checked,false);assert.equal(n('hydrogens').checked,false);assert.equal(frames.size,0);
  const initial={...front()};await start();tick(0);tick(100);
  const moved={...front()};assert(selected==='left-to-right'?+moved.cx>+initial.cx:+moved.cx<+initial.cx);
  assert.equal(moved.cy,initial.cy);assert.equal(ctl.getState().view.pitch,0);assert.equal(frames.size,1);
  const camera=ctl.getState().view,frameId=[...frames.keys()][0],requests=id;
  await choose(selected==='left-to-right'?'right-to-left':'left-to-right');
  assert.deepEqual(ctl.getState().view,camera);assert.equal(id,requests);assert.equal([...frames.keys()][0],frameId);
  tick(200);assert(Math.abs(+front().cx-+initial.cx)<1e-9);assert.equal(frames.size,1);
  // Existing manual camera API exercised only; no keyboard handler changes.
  await n('scene').fire('keydown',{key:'ArrowUp',preventDefault(){}});
  const pitch=ctl.getState().view.pitch;tick(300);assert.equal(ctl.getState().view.pitch,pitch);
  const atExport=JSON.stringify(n('scene').children);assert(await ctl.exportPng());tick(400);
  assert.equal(snapshot.svgText,atExport);assert.deepEqual(snapshot.model,source);assert.deepEqual(snapshot.provenance,provenance);
  await n('export').fire('click');assert.equal(new TextDecoder().decode(saved.at(-1)[0]),raw);
  await n('export-provenance').fire('click');assert.deepEqual(JSON.parse(new TextDecoder().decode(saved.at(-1)[0])),provenance);
  n('rotate').checked=false;await n('rotate').fire('change');const stopped=ctl.getState().view;
  await choose(selected);assert.equal(frames.size,0);assert.deepEqual(ctl.getState().view,stopped);
  for(const reason of ['reduced-motion','visibility','close']){
    await start();const late=[...frames.values()][0],prior=ctl.getState().view;
    if(reason==='reduced-motion'){motion.matches=true;await motion.fire('change');}
    if(reason==='visibility'){dom.hidden=true;await dom.fire('visibilitychange');}
    if(reason==='close')ctl.close();
    assert.equal(frames.size,0);assert.equal(n('rotate').checked,false);late(900);assert.deepEqual(ctl.getState().view,prior);assert.equal(frames.size,0);
    if(reason==='reduced-motion'){await start();assert.equal(frames.size,0);assert.equal(n('rotate').checked,false);motion.matches=false;await motion.fire('change');}
    if(reason==='visibility'){await start();assert.equal(frames.size,0);dom.hidden=false;await dom.fire('visibilitychange');}
    if(reason==='close')ctl.open();
    assert.equal(frames.size,0);assert.equal(n('rotate').checked,false);
  }
  await start();const late=[...frames.values()][0],prior=ctl.getState().view;ctl.destroy();late(1000);
  assert.equal(frames.size,0);assert.deepEqual(ctl.getState().view,prior);assert.deepEqual(ctl.getState().model,source);assert.deepEqual(ctl.getState().provenance,provenance);
  assert.equal(n('direction').handlers.change.length,0);
  console.log(`PASS ${selected}: actual rendered SVG front-atom horizontal motion, smooth same-loop reversal, fixed pitch, defaults/off, source/MOL/provenance/PNG snapshot invariance, reduced-motion/visibility/close/destroy and late callbacks.`);
}
console.log('PASS native labelled direction selector static contract. Controlled DOM only; no real-browser/Windows qualification.');
