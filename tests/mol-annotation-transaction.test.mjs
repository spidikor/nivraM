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
 const state={revision:1,accepted:structuredClone(before),history:[],provenance:{source:'existing'},annotations:{note:'retain'},drawing:'accepted SVG',selection:['c'],notifications:[],timers:[],overlay:[]};
 const host={setAttribute(){},removeAttribute(){},replaceChildren(){state.overlay=[]},append(node){state.overlay.push(node)}};
 const toast={textContent:'',hidden:true};
 const context={valenceEpoch:0,valenceMessage:null,valenceTimer:null,toastTimer:null,clearTimeout(){},documentStore:{getSnapshot:()=>({revision:state.revision})},busy:false,keyboardEditor:{cancel(){}},elementEntry:{close(){}},drag:null,gesture:{replaceChildren(){}},activeJob:null,copy:structuredClone,snapshot:()=>({graph:state.accepted}),scene:{transform:{x0:1,y0:2,xScale:60,yScale:-60}},
  setBusy(v){context.busy=v},notify(text){state.notifications.push(text);toast.textContent=text;toast.hidden=false},
  setTimeout(fn){state.timers.push(fn);return state.timers.length},valenceFeedback,paintValenceFeedback,$:id=>id==='toast'?toast:host,
  document:{createElementNS(){return {attrs:{},setAttribute(k,v){this.attrs[k]=v}}}},
  install(result){state.history.push(state.accepted);state.accepted=result.graph;state.revision++},
  api:async()=>{throw Object.assign(new Error('Rejected'),{diagnostic:{code:'atom-valence',atomIds:['c']}})}};
 const jobs=createJobController(()=>state.revision);context.jobs={begin:(channel,options)=>jobs.begin(channel,structuredClone(options)),cancelAll:()=>jobs.cancelAll()};vm.createContext(context);vm.runInContext(clearWarning+cancelDrag+operation,context);
 const run=()=>context.runDocumentOperation('import',{source:'annotated MOL fixture'});
 return {context,state,run};
}
test('annotation rejection preserves document, history, source provenance and annotations',async()=>{
 const {context,state,run}=fixture();
 const before=structuredClone([state.accepted,state.history,state.provenance,state.annotations,state.drawing,state.selection]);
 context.api=async()=>{throw Object.assign(new Error('MOL atom aliases, atom values and attachment-point annotations cannot be preserved. Use a copy resolved or removed in the source editor.'),{status:'unsupported_atom_annotation'})};
 assert.equal(await run(),false);
 assert.deepEqual([state.accepted,state.history,state.provenance,state.annotations,state.drawing,state.selection],before);
 assert.equal(state.notifications.length,1);assert.match(state.notifications[0],/source editor/);
});
test('late annotation rejection after cancellation or revision change is not adopted or notified',async()=>{
 for(const mode of ['cancel','revision']){
  const {context,state,run}=fixture();let reject;context.api=()=>new Promise((_r,j)=>reject=j);
  const pending=run();if(mode==='cancel')context.jobs.cancelAll();else state.revision++;
  reject(Object.assign(new Error('Unsupported MOL annotation'),{status:'unsupported_atom_annotation'}));
  assert.equal(await pending,false);assert.equal(state.notifications.length,0);assert.equal(state.history.length,0);
  assert.deepEqual(state.accepted,before);assert.deepEqual(state.provenance,{source:'existing'});assert.deepEqual(state.annotations,{note:'retain'});
 }
});
