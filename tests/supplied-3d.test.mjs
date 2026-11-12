import assert from 'node:assert/strict';
import {webcrypto,createHash} from 'node:crypto';
import {parseSuppliedMol,suppliedProvenance,projectSupplied} from '../public/modules/supplied-3d.mjs';
import {mountSupplied3d} from '../public/modules/supplied-3d-viewer.mjs';
class Node {constructor(id=''){this.id=id;this.handlers={};this.children=[];this.attrs={};this.disabled=true;this.classList={toggle(){}};this.dataset={};this.files=[];this.textContent='';}addEventListener(t,f){(this.handlers[t]??=[]).push(f)}removeEventListener(t,f){this.handlers[t]=this.handlers[t].filter(x=>x!==f)}async fire(t,e={}){for(const f of this.handlers[t]||[])await f(e)}append(...v){this.children.push(...v)}replaceChildren(...v){this.children=v}setAttribute(k,v){this.attrs[k]=v}querySelectorAll(){return this.buttons||[]}focus(){this.focused=true}showModal(){this.open=true}close(){this.open=false;this.fire('close')}setPointerCapture(){} }
const ids=['dialog','open','close','file','scene','status','atoms','bonds','provenance','export','export-provenance'];const nodes=Object.fromEntries(ids.map(id=>['supplied-3d-'+id,new Node(id)]));nodes['supplied-3d-dialog'].buttons=['left','right','up','down','in','out','fit'].map(action=>{const n=new Node();n.dataset['3dAction']=action;return n});const dom={getElementById:id=>nodes[id],createElement:()=>new Node(),createElementNS:()=>new Node()};
const atom=(x,y,z,e)=>[x,y,z].map(n=>n.toFixed(4).padStart(10)).join('')+' '+e.padEnd(3)+' 0  0  0  0  0  0  0  0  0  0  0  0';const raw=['water',' '.repeat(20)+'3D','','  3  2  0  0  0  0            999 V2000',atom(0,0,0,'O'),atom(.7,.5,.2,'H'),atom(-.7,.5,-.2,'H'),'  1  2  1  0  0  0  0','  1  3  1  0  0  0  0','M  END',''].join('\r\n');const bytes=new TextEncoder().encode(raw);const m=parseSuppliedMol(bytes);const p=await suppliedProvenance(m,webcrypto);assert.equal(p.sourceSha256,createHash('sha256').update(bytes).digest('hex'));assert.equal(p.generated,false);assert.deepEqual(new TextEncoder().encode(m.sourceText),bytes);assert.notDeepEqual(projectSupplied(m),projectSupplied(m,{yaw:1}));assert.equal(m.atoms[1].z,.2);
for(const bad of [raw.replace('3D','2D'),raw+'$$$$',raw.replace('V2000','V3000'),raw.replace('M  END','M  CHG  1  1  1\r\nM  END'),raw.replace('  1  2  1  0','  1  2  1  1')])assert.throws(()=>parseSuppliedMol(new TextEncoder().encode(bad)));
let gate=null;const cryptoApi={subtle:{digest:async(...args)=>{if(gate)await gate.promise;return webcrypto.subtle.digest(...args)}}};let downloads=[];const ctl=mountSupplied3d({dom,cryptoApi,download:(...a)=>downloads.push(a)});ctl.open();assert(nodes['supplied-3d-dialog'].open);assert(await ctl.loadBytes(bytes));assert.equal(nodes['supplied-3d-scene'].children.length,1);const old=ctl.getState().model;
await nodes['supplied-3d-dialog'].buttons[1].fire('click');assert(ctl.getState().view.yaw>0);await nodes['supplied-3d-scene'].fire('keydown',{key:'Home',preventDefault(){}});assert.equal(ctl.getState().view.yaw,0);assert.equal(await ctl.loadBytes(new TextEncoder().encode('bad')),false);assert.equal(ctl.getState().model,old);await nodes['supplied-3d-export'].fire('click');assert.deepEqual(downloads[0][0],bytes);
// Unsupported V3000 declarations must retain the last accepted model.
const unsupported3000=['x',' '.repeat(20)+'3D','','  0  0  0  0  0  0  0  0  0  0999 V3000','M  V30 BEGIN CTAB','M  V30 COUNTS 1 0 0 0 0','M  V30 BEGIN ATOM','M  V30 1 O 0 0 0 0 CHG=1','M  V30 END ATOM','M  V30 END CTAB','M  END'].join('\n');
for(const property of ['CHG=9','MASS=401','CFG=1','VAL=1']){assert.equal(await ctl.loadBytes(new TextEncoder().encode(unsupported3000.replace('CHG=1',property))),false);assert.equal(ctl.getState().model,old);}
assert.equal(await ctl.loadBytes(new TextEncoder().encode(unsupported3000.replace('0 CHG=1','1000000'))),false);assert.equal(ctl.getState().model,old);
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};gate=deferred();let pending=ctl.loadBytes(bytes);ctl.close();gate.resolve();assert.equal(await pending,false);assert.equal(ctl.getState().model,old);assert(nodes['supplied-3d-open'].focused);
ctl.open();gate=deferred();pending=ctl.loadBytes(bytes);let saved=gate;gate=null;assert(await ctl.loadBytes(new TextEncoder().encode(raw.replace('water','replacement'))));saved.resolve();assert.equal(await pending,false);assert(ctl.getState().model.sourceText.startsWith('replacement'));
await nodes['supplied-3d-scene'].fire('pointerdown',{button:0,clientX:1,clientY:1,pointerId:1});await nodes['supplied-3d-scene'].fire('pointermove',{clientX:31,clientY:21,pointerId:1});assert.equal(ctl.getState().view.yaw,.3);assert.equal(ctl.getState().model.atoms[1].z,.2);ctl.destroy();console.log('PASS supplied-coordinate parser/provenance/projection and bounded fake-DOM controller: controls, input failure retention, exact export, stale/closed asynchronous load exclusion. Not visual/browser qualification.');
// Generated results deliberately bypass the restrictive supplied-MOL parser.
let revision=1;const observers=new Set(),graph={atoms:[{id:'source-o',symbol:'O',x:0,y:0}],bonds:[]};
const doc={ready:Promise.resolve(),getSnapshot:()=>({revision,document:{graph}}),subscribe:fn=>{observers.add(fn);return ()=>observers.delete(fn)}};
const generatedMol='generated\n3D\nV3000\nM  END\n';downloads=[];
const generated=mountSupplied3d({dom,cryptoApi:webcrypto,getDocument:()=>doc,download:(...a)=>downloads.push(a),request:async(path,p)=>({sourceHash:p.sourceHash,sourceRevision:p.sourceRevision,sourceContextHash:p.sourceContextHash,scene:{kind:'generated_coordinates',sourceText:generatedMol,atoms:[{id:1,sourceId:'source-o',element:'O',x:0,y:0,z:0,charge:0,isotope:0}],bonds:[]},provenance:{sourceContext:JSON.parse(p.sourceContextText),sourceContextSha256:p.sourceContextHash,generated:true,origin:'generated_coordinates',sourceRevision:p.sourceRevision,sourceSha256:p.sourceHash,outputSha256:createHash('sha256').update(generatedMol).digest('hex')}})});
generated.open();assert(await generated.generate());assert.equal(generated.getState().model.kind,'generated_coordinates');assert.match(nodes['supplied-3d-scene'].attrs['aria-label'],/^Generated/);
await nodes['supplied-3d-export'].fire('click');await nodes['supplied-3d-export-provenance'].fire('click');assert.equal(downloads[0][2],'generated-3d.mol');assert.equal(new TextDecoder().decode(downloads[0][0]),generatedMol);assert.equal(downloads[1][2],'generated-3d-provenance.json');
revision++;for(const fn of observers)fn(doc.getSnapshot());assert.equal(generated.getState().model,null);assert(nodes['supplied-3d-export'].disabled);
assert(await generated.generate());generated.close();assert.equal(generated.getState().model,null);generated.destroy();assert.equal(observers.size,0);
console.log('PASS generated fake-DOM view, distinct MOL/provenance exports, source-edit and close invalidation.');

const declared=mountSupplied3d({dom,cryptoApi:webcrypto});
assert(await declared.loadBytes(new TextEncoder().encode(unsupported3000.replace('0 CHG=1','7 CHG=-2 MASS=18'))));
const row=nodes['supplied-3d-atoms'].children[0].children.map(c=>c.textContent);
assert.deepEqual(row,['1','O','0','0','0','-2','18','—','7','not declared','not declared','RAD not declared']);
assert.equal(nodes['supplied-3d-scene'].children[0].children[2].textContent,'18O2−');
assert.match(nodes['supplied-3d-scene'].children[0].children[0].textContent,/atom map 7/);
assert.equal(declared.getState().provenance.chemistryVerified,false);declared.destroy();
console.log('PASS visible unverified isotope/charge labels and map table, separate from drawing IDs.');

const unspecified=mountSupplied3d({dom,cryptoApi:webcrypto});
assert(await unspecified.loadBytes(new TextEncoder().encode(unsupported3000.replace(' CHG=1',''))));
assert.equal(unspecified.getState().model.atoms[0].isotope,0);
assert.equal(nodes['supplied-3d-atoms'].children[0].children[6].textContent,'not specified');
assert.match(nodes['supplied-3d-scene'].children[0].children[0].textContent,/isotope not specified/);
assert.doesNotMatch(nodes['supplied-3d-scene'].children[0].children[0].textContent,/isotope 0/);
assert.equal(nodes['supplied-3d-scene'].children[0].children[2].textContent,'O');
assert.equal(unspecified.getState().model.sourceText,unsupported3000.replace(' CHG=1',''));
unspecified.destroy();
console.log('PASS absent isotope displays not specified while internal sentinel and original bytes remain unchanged.');

// V2000 uses the same visible charge/isotope labels, but never native validation.
const v2000=mountSupplied3d({dom,cryptoApi:webcrypto,request:()=>{throw Error('Declaration-only input must not request native validation');}});
const v2000Text=raw.replace('M  END','M  CHG  1   1  -2\r\nM  ISO  1   1  18\r\nM  END');
assert(await v2000.loadBytes(new TextEncoder().encode(v2000Text)));
assert(nodes['supplied-3d-scene'].children.some(n=>n.children[2]?.textContent==='18O2− [H2]'));
assert.equal(nodes['supplied-3d-atoms'].children[0].children[5].textContent,'-2');
assert.equal(nodes['supplied-3d-atoms'].children[0].children[6].textContent,'18');
assert.equal(v2000.getState().provenance.chemistryVerified,false);v2000.destroy();
console.log('PASS V2000 visible charge/isotope declarations, without native-validation call.');
