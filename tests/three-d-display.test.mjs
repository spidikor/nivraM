import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {parseSuppliedMol} from '../public/modules/supplied-3d.mjs';
import {hydrogenDisplay,projectDisplay,spaceFillingAvailable,VDW_RADII} from '../public/modules/three-d-display.mjs';
import {mountSupplied3d} from '../public/modules/supplied-3d-viewer.mjs';
const a=(id,element,x=0,y=0,z=0,extra={})=>({id,element,x,y,z,charge:0,isotope:0,...extra});
const atom=(x,y,z,e)=>[x,y,z].map(n=>n.toFixed(4).padStart(10)).join('')+' '+e.padEnd(3)+' 0  0  0  0  0  0  0  0  0  0  0  0';
const raw=['water',' '.repeat(20)+'3D','','  3  2  0  0  0  0            999 V2000',atom(0,0,0,'O'),atom(.7,.5,.2,'H'),atom(-.7,.5,-.2,'H'),'  1  2  1  0  0  0  0','  1  3  1  0  0  0  0','M  END',''].join('\r\n');
const model=parseSuppliedMol(new TextEncoder().encode(raw));
const original=structuredClone(model),implicit=hydrogenDisplay(model,'implicit');assert.deepEqual([...implicit.hidden],[2,3]);assert.equal(implicit.counts.get(1),2);assert.deepEqual(model,original);assert.equal(hydrogenDisplay(model).hidden.size,0);
const incomplete={atoms:structuredClone(model.atoms),bonds:structuredClone(model.bonds)};delete incomplete.atoms[1].atomMap;const retained=hydrogenDisplay(incomplete,'implicit');assert.equal(retained.hidden.size,0);assert.equal(retained.unresolved,2);
assert.equal(hydrogenDisplay({...model,sourceText:'unsupported'},'implicit').hidden.size,0);
const stale=structuredClone(model);stale.atoms[0].x+=.01;assert.equal(hydrogenDisplay(stale,'implicit').hidden.size,0);assert.equal(hydrogenDisplay(stale,'implicit').unresolved,2);
const staleBond=structuredClone(model);staleBond.bonds[0].order=2;assert.equal(hydrogenDisplay(staleBond,'implicit').hidden.size,0);
const staleMap=structuredClone(model);staleMap.atoms[1].atomMap=7;assert.equal(hydrogenDisplay(staleMap,'implicit').hidden.size,0);
for(const props of [{isotope:2},{charge:1},{atomMap:7},{radicalElectrons:1},{radical:1},{declarations:{CFG:1}}]){const altered=structuredClone(model);Object.assign(altered.atoms[1],props);assert(!hydrogenDisplay(altered,'implicit').hidden.has(2));}
assert.equal(hydrogenDisplay({atoms:[a(1,'H'),a(2,'H')],bonds:[{a:1,b:2,order:1}]},'implicit').hidden.size,0);
assert.equal(hydrogenDisplay({atoms:[a(1,'C')],bonds:[]},'implicit').counts.size,0);
assert(spaceFillingAvailable(model));assert(!spaceFillingAvailable({atoms:[a(1,'Fe')]}));assert.equal(VDW_RADII.O,1.55);assert.equal(VDW_RADII.H,1.2);
const p=projectDisplay(model,{yaw:0,pitch:0,zoom:1},true);assert(Math.abs(p[0].r/p[1].r-1.55/1.20)<1e-12);for(const q of p){assert(q.x-q.r>=0&&q.x+q.r<=640);assert(q.y-q.r>=0&&q.y+q.r<=400);}
// Positive z is viewer-facing under this projection: positive yaw moves it right.
const direction={atoms:[a(1,'C',0,0,1),a(2,'C',0,0,-1)],bonds:[]};const before=projectDisplay(direction,{yaw:0,pitch:0,zoom:1}),after=projectDisplay(direction,{yaw:.1,pitch:0,zoom:1});assert(after[0].x>before[0].x);assert.equal(after[0].y,before[0].y);
class Node {constructor(){this.handlers={};this.children=[];this.attrs={};this.classList={toggle(){}};this.files=[];this.checked=false;}addEventListener(t,f){(this.handlers[t]??=[]).push(f);}removeEventListener(t,f){this.handlers[t]=this.handlers[t].filter(x=>x!==f);}async fire(t,e={}){for(const f of this.handlers[t]||[])await f(e);}append(...a){this.children.push(...a);}replaceChildren(...a){this.children=a;}setAttribute(k,v){this.attrs[k]=v;}querySelectorAll(){return [];}focus(){}showModal(){this.open=true;}close(){this.open=false;this.fire('close');}}
const ids=['dialog','open','close','file','scene','status','atoms','bonds','provenance','export','export-provenance','export-png','png-file','generate','cancel','style','hydrogens','rotate','display-note','measure-mode','measure-result','measure-clear','measure-1','measure-2','measure-3','measure-4'];
const nodes=Object.fromEntries(ids.map(k=>['supplied-3d-'+k,new Node()]));const n=k=>nodes['supplied-3d-'+k];const dom=Object.assign(new Node(),{getElementById:id=>nodes[id],createElement:()=>new Node(),createElementNS:()=>new Node()});
const motion=new Node();motion.matches=false;let id=0,frames=new Map(),saved=[],snapshot=null;
const requestFrame=fn=>{frames.set(++id,fn);return id;},cancelFrame=id=>frames.delete(id),tick=now=>{const tasks=[...frames.values()];frames.clear();tasks.forEach(fn=>fn(now));};
const ctl=mountSupplied3d({dom,cryptoApi:webcrypto,motionQuery:motion,requestFrame,cancelFrame,download:(...a)=>saved.push(a),sceneSnapshot:()=>JSON.stringify(n('scene').children),pngExport:async s=>{snapshot=s;return new Blob(['test']);}});

n('measure-mode').value='2';
ctl.open();assert(await ctl.loadBytes(new TextEncoder().encode(raw)));assert.equal(n('rotate').checked,false);assert.equal(frames.size,0);const source=structuredClone(ctl.getState().model),prov=structuredClone(ctl.getState().provenance);
assert.equal(n('hydrogens').checked,false);assert.equal(ctl.getState().display.hydrogens,'implicit');n('hydrogens').checked=true;await n('hydrogens').fire('change');assert.equal(n('scene').children.length,5);n('hydrogens').checked=false;await n('hydrogens').fire('change');assert.equal(n('scene').children.length,1);assert.equal(n('scene').children[0].children[2].textContent,'O [H2]');assert.match(n('display-note').textContent,/Missing H counts and positions are not inferred/);assert.equal(n('atoms').children.length,3);
n('style').value='space-filling';await n('style').fire('change');assert.match(n('display-note').textContent,/RDKit 2025.09.4/);assert(await ctl.exportPng());assert.deepEqual(snapshot.model,source);assert.deepEqual(snapshot.provenance,prov);assert.equal(snapshot.model.sourceText,raw);
await n('export').fire('click');assert.equal(new TextDecoder().decode(saved.at(-1)[0]),raw);
n('hydrogens').checked=true;await n('hydrogens').fire('change');
n('measure-1').value='1';n('measure-2').value='2';await n('measure-2').fire('change');assert.match(n('measure-result').textContent,/Distance.*Å.*Supplied/);
const measured=n('measure-result').textContent;await n('scene').fire('keydown',{key:'ArrowRight',preventDefault(){}});assert.equal(n('measure-result').textContent,measured);
assert.equal(await ctl.loadBytes(new TextEncoder().encode('not mol')),false);assert.equal(n('measure-result').textContent,measured);
assert(await ctl.loadBytes(new TextEncoder().encode(raw)));assert.equal(n('measure-1').value,'');assert(!n('measure-result').textContent.includes('Å'));
n('hydrogens').checked=false;await n('hydrogens').fire('change');assert.equal(n('measure-1').children.length,2);
const start=async()=>{n('rotate').checked=true;await n('rotate').fire('change');};await start();assert.equal(frames.size,1);tick(0);tick(100);assert(ctl.getState().view.yaw>0);assert.equal(ctl.getState().view.pitch,0);assert.deepEqual(ctl.getState().model,source);
await n('scene').fire('keydown',{key:'ArrowUp',preventDefault(){}});const pitch=ctl.getState().view.pitch;tick(200);assert.equal(ctl.getState().view.pitch,pitch);
motion.matches=true;await motion.fire('change');assert.equal(frames.size,0);assert.equal(n('rotate').checked,false);await start();assert.equal(frames.size,0);assert.equal(n('rotate').checked,false);
motion.matches=false;await start();dom.hidden=true;await dom.fire('visibilitychange');assert.equal(frames.size,0);dom.hidden=false;await dom.fire('visibilitychange');assert.equal(frames.size,0);
await start();const late=[...frames.values()][0];const prior=ctl.getState().view.yaw;ctl.close();assert.equal(frames.size,0);late(900);assert.equal(ctl.getState().view.yaw,prior);ctl.open();assert.equal(frames.size,0);await start();ctl.destroy();assert.equal(frames.size,0);
console.log('PASS pure and controlled-DOM display: source-preserving H condensation, special-H retention, no absent-H inference, RDKit radius ratios and fitting, rightward screen projection, yaw-only animation, unchecked default, reduced-motion and visibility/close/destroy cleanup, late-frame fencing, MOL and PNG snapshot invariance. No browser qualification.');

const fallback=mountSupplied3d({dom,cryptoApi:webcrypto,motionQuery:motion,requestFrame,cancelFrame});fallback.open();assert(await fallback.loadBytes(new TextEncoder().encode(raw.replace(' O  ',' Fe '))));n('style').value='space-filling';await n('style').fire('change');assert.match(n('display-note').textContent,/unavailable.*ball-and-stick/);assert.equal(n('scene').children.length,1);fallback.destroy();
console.log('PASS unsupported-radius element visibly falls back to ball-and-stick without guessed radii.');
