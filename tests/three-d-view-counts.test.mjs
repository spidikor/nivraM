/** Accessibility labels and emitted primitives only; no browser qualification. */
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {webcrypto} from 'node:crypto';
import {pathToFileURL} from 'node:url';
const viewer=process.env.NIVRAM_VIEWER_ROOT?pathToFileURL(process.env.NIVRAM_VIEWER_ROOT+'/public/modules/supplied-3d-viewer.mjs'):new URL('../public/modules/supplied-3d-viewer.mjs',import.meta.url);
const {mountSupplied3d}=await import(viewer);
const python=process.env.NIVRAM_TEST_PYTHON;assert(python);
const fixture=spawnSync(python,['-B','-s','-c',"import json\nfrom tests.test_conformer import request\nfrom nivram.conformer import generate\nr=request('C')\nprint(json.dumps({'request':r,'result':generate(r)}))"],{encoding:'utf8',timeout:30000});assert.equal(fixture.status,0,fixture.stderr);const pair=JSON.parse(fixture.stdout);
class Node {constructor(tag=''){this.localName=tag;this.handlers={};this.children=[];this.attrs={};this.classList={toggle(){}};this.files=[];this.checked=false;}addEventListener(t,f){(this.handlers[t]??=[]).push(f);}removeEventListener(t,f){this.handlers[t]=this.handlers[t].filter(x=>x!==f);}async fire(t,e={}){for(const f of this.handlers[t]||[])await f(e);}append(...a){this.children.push(...a);}replaceChildren(...a){this.children=a;}setAttribute(k,v){this.attrs[k]=v;}querySelectorAll(){return [];}focus(){}showModal(){this.open=true;}close(){this.open=false;this.fire('close');}}
const nodes=Object.fromEntries(['dialog','open','close','file','scene','status','atoms','bonds','provenance','export','export-provenance','export-png','png-file','generate','cancel','style','hydrogens','rotate','display-note'].map(id=>['supplied-3d-'+id,new Node()])),n=k=>nodes['supplied-3d-'+k];
const dom={getElementById:id=>nodes[id],createElement:tag=>new Node(tag),createElementNS:(_,tag)=>new Node(tag)};
const graph=JSON.parse(pair.request.graphText),doc={ready:Promise.resolve(),getSnapshot:()=>({revision:7,document:{graph}}),subscribe:()=>()=>{}};
const view=mountSupplied3d({dom,cryptoApi:webcrypto,getDocument:()=>doc,request:async(path,payload)=>{assert.equal(path,'conformer');const run=spawnSync(python,['-B','-s','-c','import json,sys; from nivram.conformer import generate; print(json.dumps(generate(json.load(sys.stdin))))'],{input:JSON.stringify(payload),encoding:'utf8',timeout:30000});assert.equal(run.status,0,run.stderr);return JSON.parse(run.stdout);}});
function check(canonical,display,rendered){
 const label=n('scene').attrs['aria-label'];
 assert(label.includes(`Canonical source: ${canonical[0]} atoms and ${canonical[1]} bonds.`),label);
 assert(label.includes(`Display model: ${display[0]} atoms and ${display[1]} bonds.`),label);
 assert(label.includes(`Current rendering: ${rendered[0]} atom spheres, ${rendered[1]} bonds drawn, ${rendered[2]} bond line strokes.`),label);
 assert.equal(n('scene').children.filter(x=>x.localName==='g').length,rendered[0]);assert.equal(n('scene').children.filter(x=>x.localName==='line').length,rendered[2]);
}
async function controls(explicit,style){n('hydrogens').checked=explicit;await n('hydrogens').fire('change');n('style').value=style;await n('style').fire('change');}
view.open();assert(await view.generate(),n('status').textContent);assert.equal(n('hydrogens').checked,false);const original=structuredClone(view.getState().model),provenance=structuredClone(view.getState().provenance);
for(const style of ['ball-and-stick','space-filling'])for(const explicit of [false,true]){await controls(explicit,style);check([1,0],[5,4],[explicit?5:1,explicit&&style==='ball-and-stick'?4:0,explicit&&style==='ball-and-stick'?4:0]);assert.deepEqual(view.getState().model,original);assert.deepEqual(view.getState().provenance,provenance);}
const atom=(e,x)=>`${x.toFixed(4).padStart(10)}    0.0000    0.0000 ${e.padEnd(3)} 0  0  0  0  0  0  0  0  0  0  0  0`;
const water=special=>['water',' '.repeat(20)+'3D','','  3  2  0  0  0  0            999 V2000',atom('O',0),atom('H',1),atom('H',-1),'  1  2  1  0  0  0  0','  1  3  1  0  0  0  0',...(special?['M  ISO  1   2   2']:[]),'M  END',''].join('\n');
for(const special of [false,true]){assert(await view.loadBytes(new TextEncoder().encode(water(special))));const before=structuredClone(view.getState().model);for(const style of ['ball-and-stick','space-filling'])for(const explicit of [false,true]){await controls(explicit,style);const atoms=explicit?3:special?2:1,bonds=style==='space-filling'?0:atoms-1;check([3,2],[3,2],[atoms,bonds,bonds]);assert.deepEqual(view.getState().model,before);}}
const double=['double',' '.repeat(20)+'3D','','  2  1  0  0  0  0            999 V2000',atom('C',0),atom('O',1),'  1  2  2  0  0  0  0','M  END',''].join('\n');assert(await view.loadBytes(new TextEncoder().encode(double)));await controls(true,'ball-and-stick');check([2,1],[2,1],[2,1,2]);await controls(true,'space-filling');check([2,1],[2,1],[2,0,0]);
view.destroy();console.log('PASS controlled DOM: methane default/explicit × both styles, ordinary/isotopic supplied H, double-bond conceptual/stroke distinction, actual emitted primitives, source/provenance nonmutation.');
