import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {createBond} from '../public/modules/edit-commands.mjs';
import {createJobController} from '../public/modules/jobs.mjs';
import {valenceFeedback,paintValenceFeedback} from '../public/modules/valence-feedback.mjs';
// Run the actual app transaction function with controlled boundary adapters.
// This tests asynchronous control flow, not browser rendering or real DOM.
const source=readFileSync(new URL('../public/app.mjs',import.meta.url),'utf8');
const clearWarning=source.slice(source.indexOf('function clearValenceFeedback(){'),source.indexOf("$('keyboard-editor').addEventListener('click',clearValenceFeedback"));
const cancelDrag=source.slice(source.indexOf('function cancelDrag(){'),source.indexOf("svg.addEventListener('pointerdown'"));
const operation=source.slice(source.indexOf('async function runDocumentOperation('),source.indexOf('async function change('));
const before={atoms:[{id:'c',symbol:'C',x:0,y:0},{id:'a',symbol:'C',x:1.5,y:0}],bonds:[{id:'b',begin:'c',end:'a',order:1}]};
const after=structuredClone(before);after.bonds[0].order=3;
function fixture(){
 const state={revision:1,accepted:structuredClone(before),history:[],drawing:'accepted SVG',selection:['c'],notifications:[],timers:[],overlay:[]};
 const host={setAttribute(){},removeAttribute(){},replaceChildren(){state.overlay=[]},append(node){state.overlay.push(node)}};
 const toast={textContent:'',hidden:true};
 const context={valenceEpoch:0,valenceMessage:null,valenceTimer:null,toastTimer:null,clearTimeout(){},documentStore:{getSnapshot:()=>({revision:state.revision})},busy:false,keyboardEditor:{cancel(){}},elementEntry:{close(){}},drag:null,gesture:{replaceChildren(){}},activeJob:null,copy:structuredClone,snapshot:()=>({graph:state.accepted}),scene:{transform:{x0:1,y0:2,xScale:60,yScale:-60}},
  setBusy(v){context.busy=v},notify(text){state.notifications.push(text);toast.textContent=text;toast.hidden=false},
  setTimeout(fn){state.timers.push(fn);return state.timers.length},valenceFeedback,paintValenceFeedback,$:id=>id==='toast'?toast:host,
  document:{createElementNS(){return {attrs:{},setAttribute(k,v){this.attrs[k]=v}}}},
  install(result){state.history.push(state.accepted);state.accepted=result.graph;state.revision++},
  api:async()=>{throw Object.assign(new Error('Rejected'),{diagnostic:{code:'atom-valence',atomIds:['c']}})}};
 const jobs=createJobController(()=>state.revision);context.jobs={begin:(channel,options)=>jobs.begin(channel,structuredClone(options)),cancelAll:()=>jobs.cancelAll()};vm.createContext(context);vm.runInContext(clearWarning+cancelDrag+operation,context);
 const run=()=>context.runDocumentOperation('render',{graph:after},{proposedEdit:{before,after}});
 return {context,state,run};
}
test('rejected proposed bond leaves accepted drawing/selection/history unchanged and next valid edit commits',async()=>{
 const {context,state,run}=fixture();const preserved=JSON.stringify([state.accepted,state.history,state.drawing,state.selection]);
 assert.equal(await run(),false);assert.equal(JSON.stringify([state.accepted,state.history,state.drawing,state.selection]),preserved);
 assert.equal(state.overlay.length,1);assert.equal(context.busy,false);assert.match(state.notifications[0],/invalid valence/);
 context.api=async()=>({graph:after});assert.equal(await run(),true);assert.equal(state.overlay.length,0);assert.equal(state.history.length,1);
});
test('cancelled and stale rejection cannot install warning',async()=>{
 for(const mode of ['cancel','revision']){
  const {context,state,run}=fixture();let reject;context.api=()=>new Promise((_r,j)=>reject=j);
  const pending=run();if(mode==='cancel')context.jobs.cancelAll();else state.revision++;
  reject(Object.assign(new Error('Rejected'),{diagnostic:{code:'atom-valence',atomIds:['c']}}));
  assert.equal(await pending,false);assert.equal(state.overlay.length,0);assert.equal(state.notifications.length,0);assert.equal(state.history.length,0);
 }
});
test('warning expiry clears proposed overlay and generic failures never mark neighbors',async()=>{
 const {context,state,run}=fixture();await run();assert.equal(state.overlay.length,1);state.timers[0]();assert.equal(state.overlay.length,0);
 context.api=async()=>{throw new Error('Explicit valence text without typed diagnostic')};await run();assert.equal(state.overlay.length,0);
});
test('production wiring separates warning from gesture, explains rejection accessibly, and routes both edit paths',()=>{
 const html=readFileSync(new URL('../public/index.html',import.meta.url),'utf8');
 assert.match(html,/<g id="gesture"><\/g><g id="valence-warning" aria-hidden="true" pointer-events="none">/);
 assert.match(html,/id="toast"[^>]*role="alert"/);
 assert.match(operation,/notify\(feedback.message,true\)/);
 assert.match(source,/commit:\(next,options\)=>change\(next,\{\.\.\.options,proposedEdit:/);
 assert.match(source,/change\(candidate.graph,\{\.\.\.options,proposedEdit:/);
 assert.match(source,/function cancelDrag\(\)\{clearValenceFeedback\(\)/);
});

test('tool-change dismissal during pending render prevents late warning recreation',async()=>{
 const {context,state,run}=fixture();let reject;
 context.api=()=>new Promise((_resolve,j)=>reject=j);
 const pending=run();context.cancelDrag(); // Actual setTool dismissal path.
 reject(Object.assign(new Error('Rejected'),{diagnostic:{code:'atom-valence',atomIds:['c']}}));
 assert.equal(await pending,false);assert.equal(state.overlay.length,0);assert.equal(state.notifications.length,0);
 assert.equal(state.history.length,0);
});
test('real owning job timeout retains explanation but never marks invalid chemistry',async()=>{
 const {context,state,run}=fixture(),begin=context.jobs.begin;
 context.jobs.begin=(channel,options)=>begin(channel,{...options,timeout:1});
 context.api=(_action,_data,job)=>job.wait(new Promise(()=>{}));
 assert.equal(await run(),false);assert.equal(state.overlay.length,0);assert.equal(state.notifications.length,1);
 assert.match(state.notifications[0],/timed out.*preceding document is retained/i);assert.equal(state.history.length,0);
});
test('timeout from an obsolete revision does not notify a newer document',async()=>{
 const {context,state,run}=fixture(),begin=context.jobs.begin;
 context.jobs.begin=(channel,options)=>begin(channel,{...options,timeout:1});
 context.api=(_action,_data,job)=>job.wait(new Promise(()=>{}));
 const pending=run();state.revision++;
 assert.equal(await pending,false);assert.equal(state.notifications.length,0);assert.equal(state.overlay.length,0);
});

function pointerHandlers(context){
 const handlers=new Map();let serial=0;
 Object.assign(context,{svg:{addEventListener:(name,handler)=>handlers.set(name,handler)},
   point:e=>({x:e.clientX,y:e.clientY}),world:p=>p,nearestAtom:()=>null,element:'C',uid:p=>p+(++serial),createBond});
 const old$=context.$;context.$=id=>id==='bond-type'?{value:'1'}:old$(id);
 context.editCommand=build=>{const candidate=build();return context.runDocumentOperation('render',{graph:candidate.graph},{proposedEdit:{before,after:candidate.graph}});};
 const pointerSource=source.slice(source.indexOf("svg.addEventListener('pointerup'"),source.indexOf("svg.addEventListener('pointerleave'"));
 vm.runInContext(pointerSource,context);
 context.drag={kind:'bond',base:structuredClone(before),start:'c',w:{x:0,y:0},p:{x:0,y:0}};
 return handlers;
}
test('normal pointerup then implicit capture release retains eventual red rejection',async()=>{
 const {context,state}=fixture(),handlers=pointerHandlers(context);let reject;
 context.api=()=>new Promise((_resolve,j)=>reject=j);
 const pending=handlers.get('pointerup')({clientX:0,clientY:1.5,shiftKey:true});
 assert.equal(context.drag,null);const epoch=context.valenceEpoch;
 handlers.get('lostpointercapture')({});assert.equal(context.valenceEpoch,epoch);
 reject(Object.assign(new Error('Rejected'),{diagnostic:{code:'atom-valence',atomIds:['c']}}));
 await pending;assert.equal(state.overlay.length,1);assert.match(state.notifications[0],/invalid valence/);
 assert.equal(state.history.length,0);assert.deepEqual(state.accepted,before);
});
test('capture loss before pointerup cancels unfinished drag and sends no proposed edit',async()=>{
 const {context,state}=fixture(),handlers=pointerHandlers(context);let requests=0;
 context.api=async()=>{requests++;throw new Error('Unexpected request')};
 const epoch=context.valenceEpoch;handlers.get('lostpointercapture')({});
 assert.equal(context.drag,null);assert.equal(context.valenceEpoch,epoch+1);
 await handlers.get('pointerup')({clientX:0,clientY:1.5,shiftKey:true});
 assert.equal(requests,0);assert.equal(state.overlay.length,0);assert.equal(state.notifications.length,0);assert.equal(state.history.length,0);
});
