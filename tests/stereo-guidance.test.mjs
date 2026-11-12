import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {validateStereoDenial,placeStereoFlags,boxesOverlap,segmentHitsBox,STEREO_MESSAGE} from '../public/modules/stereo-guidance.mjs';
import {createConformerController} from '../public/modules/conformer.mjs';
const graph={atoms:[{id:'a',x:0,y:0},{id:'b',x:1,y:0}],bonds:[{id:'q',begin:'a',end:'b',order:2}]};
const req={graphText:JSON.stringify(graph),sourceHash:'1'.repeat(64),sourceContextHash:'2'.repeat(64),sourceRevision:4};
const response=p=>({sourceHash:p.sourceHash,sourceContextHash:p.sourceContextHash,sourceRevision:p.sourceRevision,denial:{schema:'nivram-stereo-denial/1',targets:[{kind:'atom',id:'a',notation:'R/S?'},{kind:'bond',id:'q',notation:'E/Z?'}],unsupportedCount:0}});
assert.equal(STEREO_MESSAGE,'Invalid stereochemistry! Please specify');
assert.equal(validateStereoDenial(response(req),req).targets.length,2);
for(const change of [r=>r.sourceHash='stale',r=>r.sourceRevision++,r=>r.sourceContextHash='stale',r=>r.scene={},r=>r.denial.targets[0].id='unknown',r=>r.denial.targets[0].notation='cis/trans?',r=>r.denial.targets[1].id='a',r=>r.denial.targets.push(r.denial.targets[0]),r=>r.denial.unsupportedCount=-1,r=>r.denial.targets=[],r=>r.denial.unsupportedCount=385,r=>r.denial.targets[0].extra=1,r=>r.denial.targets[0].id=2]){const r=response(req);change(r);assert.throws(()=>validateStereoDenial(r,req));}
assert.throws(()=>validateStereoDenial(response(req),{...req,graphText:JSON.stringify({...graph,bonds:[{...graph.bonds[0],order:1}]})}));
const fallback=response(req);fallback.denial={schema:'nivram-stereo-denial/1',targets:[],unsupportedCount:1};assert.equal(validateStereoDenial(fallback,req).unsupportedCount,1);
const bounds={x:0,y:0,width:600,height:400},target={id:'a',kind:'atom',notation:'R/S?',x:300,y:200,width:60,height:24,radius:35};
const obstacleSets=[[],[{x:290,y:190,width:20,height:20}],[{x:285,y:70,width:30,height:100},{x:290,y:190,width:20,height:20}],[{x:100,y:0,width:60,height:290},{x:180,y:100,width:70,height:80},{x:350,y:40,width:30,height:110}]];
for(const obstacles of obstacleSets){const result=placeStereoFlags([target,{...target,id:'b',x:400}],obstacles,bounds);assert(result.placed.length);assert.deepEqual(result,placeStereoFlags([target,{...target,id:'b',x:400}],obstacles,bounds));for(const p of result.placed){assert(p.box.x>=0&&p.box.y>=0&&p.box.x+p.box.width<=600&&p.box.y+p.box.height<=400);for(const o of obstacles){assert(!boxesOverlap(p.box,o));assert(!segmentHitsBox(p.start,p.end,o));}}for(let i=0;i<result.placed.length;i++)for(let j=i+1;j<result.placed.length;j++)assert(!boxesOverlap(result.placed[i].box,result.placed[j].box));}
assert.equal(placeStereoFlags([target],[bounds],bounds).hidden.length,1);
assert.equal(placeStereoFlags([{...target,x:NaN}],[],bounds).hidden.length,1);
assert.equal(placeStereoFlags([target],[{x:NaN,y:0,width:1,height:1}],bounds).hidden.length,1);
assert.equal(placeStereoFlags([target],[],{x:0,y:0,width:10,height:10}).hidden.length,1);
assert(segmentHitsBox({x:0,y:0},{x:10,y:10},{x:5,y:5,width:2,height:2}));assert(!segmentHitsBox({x:0,y:0},{x:10,y:0},{x:5,y:1,width:2,height:2}));
let revision=4,activeDoc,listeners=new Set(),denials=[],outputs=[],states=[],resolve=null,held=false;
const doc={ready:Promise.resolve(),getSnapshot:()=>({revision,document:{graph}}),subscribe:f=>{listeners.add(f);return ()=>listeners.delete(f)}};activeDoc=doc;
const ctl=createConformerController({getDocument:()=>activeDoc,cryptoApi:webcrypto,onDenial:r=>denials.push(r),onResult:r=>outputs.push(r),onState:(s,t)=>states.push([s,t]),request:async(path,p)=>{if(path.endsWith('/cancel'))return {};if(held)return new Promise(r=>resolve=()=>r(response(p)));return response(p);}});
const original=JSON.stringify(graph);assert.equal(await ctl.generate(),false);assert.equal(denials.length,1);assert.equal(outputs.length,0);assert.equal(JSON.stringify(graph),original);assert(states.at(-1)[1].startsWith(STEREO_MESSAGE));
async function pending(){held=true;resolve=null;const job=ctl.generate();for(let i=0;i<100&&!resolve;i++)await new Promise(r=>setTimeout(r,0));assert(resolve);return {job,done:resolve};}
let p=await pending();revision++;for(const f of listeners)f(doc.getSnapshot());p.done();assert.equal(await p.job,false);assert.equal(denials.length,1);
p=await pending();activeDoc={...doc};p.done();assert.equal(await p.job,false);assert.equal(denials.length,1);activeDoc=doc;
p=await pending();ctl.cancel();p.done();assert.equal(await p.job,false);assert.equal(denials.length,1);
p=await pending();ctl.destroy();p.done();assert.equal(await p.job,false);assert.equal(denials.length,1);assert.equal(listeners.size,0);
console.log('PASS stereo guidance: strict source/target diagnostic validation, deterministic bounded geometry/fallback, stale/mutated/replaced/cancelled source suppression. Controlled JS only; no native chemistry or visual validation.');
// Minimal controlled DOM contract. This is not a browser rendering test.
const {mountStereoGuidance}=await import('../public/modules/stereo-guidance.mjs');
const {sourceBinding}=await import('../public/modules/conformer-source.mjs');
class Node extends EventTarget{
  constructor(tag){super();this.tag=tag;this.children=[];this.attrs={};this.style={};this.textContent='';this.open=false;this.hidden=false;}
  append(...nodes){for(const n of nodes){this.children.push(n);n.parentNode=this;}}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(n=>n!==this);}
  setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k]??null;}removeAttribute(k){delete this.attrs[k];}
  getBBox(){if(this.badBox)throw new Error('bbox unavailable');return this.box||{x:-2,y:-19,width:this.textContent.length*12,height:24};}
  getCTM(){return {a:1,b:0,c:0,d:1,e:0,f:0,inverse(){return this;},multiply(){return this;}};}
  querySelectorAll(){return this.children;}
  show(){this.open=true;}close(){this.open=false;}focus(){}
}
const body=new Node('body'),parent=new Node('div'),canvas=new Node('svg'),drawing=new Node('g');parent.append(canvas);canvas.append(drawing);body.append(parent);const region=new Node('div'),status=new Node('div'),requestStatus=new Node('span');requestStatus.textContent='Ready';status.append(requestStatus);parent.append(status);region.hidden=true;body.append(region);
const dom={body,createElement:t=>new Node(t),createElementNS:(_,t)=>new Node(t),getElementById:id=>id==='stereo-guidance-region'?region:id==='structure-status'?status:null};
const events=new EventTarget(),uiListeners=new Set();let uiRevision=1;
const uiDoc={getSnapshot:()=>({revision:uiRevision,document:{graph,style:{labelSize:30}}}),subscribe:f=>{uiListeners.add(f);return ()=>uiListeners.delete(f)}};
const scene={positions:[{id:'a',x:250,y:200},{id:'b',x:400,y:200}]};
let time=1000,timerId=0,selection={atoms:[],bonds:[]};const timers=new Map(),cancelledTimers=[];
assert.throws(()=>mountStereoGuidance({dom:{...dom,getElementById:()=>null},events,canvas,drawing,getDocument:()=>uiDoc,getScene:()=>scene,getView:()=>bounds}),/stereochemistry guidance hosts/);
const ui=mountStereoGuidance({dom,events,canvas,drawing,getDocument:()=>uiDoc,getScene:()=>scene,getView:()=>bounds,getSelection:()=>selection,now:()=>time,setTimer:(fn,ms)=>{assert.equal(ms,30000);timers.set(++timerId,fn);return timerId;},clearTimer:id=>{cancelledTimers.push(id);timers.delete(id);}});
let receiptId=0,lastEvent;
function emit(){const binding=sourceBinding(uiDoc.getSnapshot()),request={...req,clientId:'c',jobId:String(++receiptId),graphText:binding.graphText,sourceContextText:binding.sourceContextText,sourceRevision:binding.revision};lastEvent={binding,request,result:response(request)};events.dispatchEvent(Object.assign(new Event('nivram-stereo-denial'),{detail:lastEvent}));}
emit();const popup=body.children.find(n=>n.tag==='dialog'),layer=canvas.children.at(-1),note=region.children.find(n=>n.tag==='p'),dismissAll=status.children.at(-1);
assert(popup.open);assert.equal(note.parentNode,region);assert.equal(dismissAll.parentNode,status);assert.equal(status.attrs['data-stereo-guidance'],'active');assert.equal(dismissAll.attrs['aria-label'],'Dismiss stereochemical flags');assert(!parent.children.includes(note));assert(!parent.children.includes(dismissAll));assert.equal(region.hidden,false);assert(!dismissAll.hidden);assert.equal(dismissAll.textContent,'Dismiss stereochemical flags');assert.equal(popup.children[0].textContent,STEREO_MESSAGE);assert.match(popup.children[1].textContent,/unspecified/);assert(layer.children.some(n=>n.children.some(c=>c.tag==='text')));for(const n of layer.children.flatMap(n=>n.children).filter(n=>n.tag==='text')){assert.equal(n.attrs['font-style'],'italic');assert(!n.textContent.includes('*'));}assert(!note.hidden);
uiRevision++;for(const f of uiListeners)f();assert.equal(layer.children.length,0);assert(!popup.open);assert(note.hidden);
const wall=new Node('path');wall.box=bounds;drawing.append(wall);emit();assert.equal(layer.children.length,0);assert.match(note.textContent,/No safe visible placement/);assert.match(note.textContent,/atom a/);assert.match(note.textContent,/bond q/);
wall.badBox=true;emit();assert.equal(layer.children.length,0);assert.match(note.textContent,/No safe/);
ui.clear();drawing.replaceChildren();emit();assert.equal(layer.children.length,2);const oldTimer=timers.get(timerId);
selection={atoms:['a'],bonds:[]};events.dispatchEvent(new Event('nivram-stereo-selection'));assert.equal(layer.children.length,1);assert.equal(layer.children[0].attrs['data-stereo-target'],'bond:q');assert(!popup.open);events.dispatchEvent(Object.assign(new Event('nivram-stereo-denial'),{detail:lastEvent}));assert.equal(layer.children.length,1);
selection={atoms:[],bonds:[]};events.dispatchEvent(new Event('nivram-stereo-selection'));assert.equal(layer.children.length,1); // no resurrection
selection={atoms:['b'],bonds:[]};events.dispatchEvent(new Event('nivram-stereo-selection'));assert.equal(layer.children.length,1); // endpoint is not target bond
selection={atoms:[],bonds:['q']};events.dispatchEvent(new Event('nivram-stereo-selection'));assert.equal(layer.children.length,0);assert.equal(timers.size,0);
selection={atoms:['a'],bonds:[]};emit();assert.equal(layer.children.length,1);assert.equal(layer.children[0].attrs['data-stereo-target'],'bond:q');
selection={atoms:[],bonds:[]};emit();assert.equal(layer.children.length,2);oldTimer();assert.equal(layer.children.length,2); // stale timeout cannot remove replacement
const currentTimer=timers.get(timerId);currentTimer();assert.equal(layer.children.length,0);assert(!popup.open);assert.equal(timers.size,0);
emit();time+=30000;events.dispatchEvent(new Event('visibilitychange'));assert.equal(layer.children.length,0);assert(!popup.open);
emit();time+=29999;events.dispatchEvent(new Event('nivram-stereo-selection'));assert.equal(layer.children.length,2);time++;events.dispatchEvent(new Event('nivram-stereo-selection'));assert.equal(layer.children.length,0);
emit();popup.children.at(-1).dispatchEvent(new Event('click'));assert.equal(layer.children.length,0);assert.equal(timers.size,0);
emit();requestStatus.textContent='Updated while guidance shown';const statusNodes=[...status.children];dom.activeElement=dismissAll;let focuses=0;canvas.focus=()=>focuses++;dismissAll.dispatchEvent(new Event('click'));assert.equal(layer.children.length,0);assert(dismissAll.hidden);assert(note.hidden);assert.equal(region.hidden,true);assert.equal(status.attrs['data-stereo-guidance'],undefined);assert.equal(timers.size,0);assert.equal(focuses,1);assert.deepEqual(status.children,statusNodes);assert.equal(requestStatus.textContent,'Updated while guidance shown');dom.activeElement=null;emit();assert(!dismissAll.hidden);assert.equal(layer.children.length,2);ui.destroy();assert.equal(timers.size,0);assert.equal(uiListeners.size,0);assert(!body.children.includes(popup));
console.log('PASS controlled DOM: exact popup, explanatory text, italic labels, dense/unmeasurable fallback, ephemeral mutation clearing and teardown. Browser visuals not tested.');

console.log('PASS timed lifecycle: independent atom/bond selection dismissal, preselection, no resurrection, absolute 30-second expiry, resumed visibility, stale timeout isolation, replacement/close/destroy disarm.');
// Denial-only transition must not discard a previously accepted generated scene.
const {mountSupplied3d}=await import('../public/modules/supplied-3d-viewer.mjs');
class ViewerNode{
  constructor(){this.handlers={};this.children=[];this.attrs={};this.classList={toggle(){}};this.files=[];this.checked=false;}
  addEventListener(t,f){(this.handlers[t]??=[]).push(f);}removeEventListener(t,f){this.handlers[t]=this.handlers[t].filter(x=>x!==f);}
  async fire(t,e={}){for(const f of this.handlers[t]||[])await f(e);}append(...n){this.children.push(...n);}replaceChildren(...n){this.children=n;}
  setAttribute(k,v){this.attrs[k]=v;}querySelectorAll(){return [];}focus(){}showModal(){this.open=true;}close(){this.open=false;this.fire('close');}
}
const names=['dialog','open','close','file','scene','status','atoms','bonds','provenance','export','export-provenance','export-png','png-file','generate','cancel','style','hydrogens','rotate','display-note','measure-mode','measure-result','measure-clear','measure-1','measure-2','measure-3','measure-4'];
const vn=Object.fromEntries(names.map(k=>['supplied-3d-'+k,new ViewerNode()])),vd=Object.assign(new ViewerNode(),{getElementById:id=>vn[id],createElement:()=>new ViewerNode(),createElementNS:()=>new ViewerNode()});
const viewerDoc={ready:Promise.resolve(),getSnapshot:()=>({revision:1,document:{graph}}),subscribe:()=>()=>{}};let deny=false;
const sourceText='generated\nM  END\n',hash=Array.from(new Uint8Array(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(sourceText))),x=>x.toString(16).padStart(2,'0')).join('');
const viewer=mountSupplied3d({dom:vd,cryptoApi:webcrypto,getDocument:()=>viewerDoc,motionQuery:null,request:async(path,p)=>{if(path.endsWith('/cancel'))return {};if(deny)return response(p);return {sourceHash:p.sourceHash,sourceRevision:p.sourceRevision,sourceContextHash:p.sourceContextHash,scene:{kind:'generated_coordinates',atoms:[{id:1,element:'C',x:0,y:0,z:0},{id:2,element:'C',x:1,y:0,z:0}],bonds:[{id:1,a:1,b:2,order:2}],sourceText},provenance:{generated:true,origin:'generated_coordinates',sourceRevision:p.sourceRevision,sourceSha256:p.sourceHash,sourceContext:JSON.parse(p.sourceContextText),sourceContextSha256:p.sourceContextHash,outputSha256:hash}};}});
viewer.open();assert(await viewer.generate());const accepted=JSON.stringify(viewer.getState().model);deny=true;assert.equal(await viewer.generate(),false);assert.equal(vn['supplied-3d-dialog'].open,false);assert.equal(JSON.stringify(viewer.getState().model),accepted);assert.equal(vn['supplied-3d-export'].disabled,false);viewer.open();assert.equal(JSON.stringify(viewer.getState().model),accepted);viewer.close();assert.equal(viewer.getState().model,null);viewer.destroy();
console.log('PASS controlled viewer: stereo denial returns to editing and preserves previous valid generated scene; ordinary close still invalidates it.');
