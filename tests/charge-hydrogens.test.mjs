// Actual UI handler/transaction/history with native chemistry, no browser/socket.
// Render installation is an adapter: Arial and live SVG acceptance remain separate.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {createDocumentStore,validateProject} from '../public/modules/document.mjs';
import {createJobController} from '../public/modules/jobs.mjs';
import {stereoEditNotice} from '../public/modules/bond-edit.mjs';
const source=readFileSync(new URL('../public/app.mjs',import.meta.url),'utf8');
const python=process.env.NIVRAM_TEST_PYTHON;assert(python,'Set NIVRAM_TEST_PYTHON to the qualified RDKit environment');
function chemical(graph){
 const run=spawnSync(python,['-B','-s','-c',`import json,sys
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors
from rdkit.Chem.Draw import rdMolDraw2D
from nivram.chemistry import graph_to_mol
from nivram.scene import hydrogen_carbon_labels
g=json.load(sys.stdin)
try:
 m=graph_to_mol(g)
 d=rdMolDraw2D.PrepareMolForDrawing(Chem.Mol(m),kekulize=True,addChiralHs=False,wedgeBonds=True,forceCoords=False)
 print(json.dumps(dict(smiles=Chem.MolToSmiles(m),formula=rdMolDescriptors.CalcMolFormula(m),implicitHs=[a.GetNumImplicitHs() for a in m.GetAtoms()],drawingHs=[a.GetTotalNumHs() for a in d.GetAtoms()],labels=hydrogen_carbon_labels(d))))
except ValueError as e: print(json.dumps(dict(error=str(e))))`],{input:JSON.stringify(graph),encoding:'utf8',timeout:30000});
 assert.equal(run.status,0,run.stderr);const result=JSON.parse(run.stdout);if(result.error)throw new Error(result.error);return result;
}
function atom(symbol='N',extra={}){return {id:'a',symbol,x:0,y:0,charge:0,isotope:0,explicitHs:0,noImplicit:false,aromatic:false,chiralTag:0,radicals:0,mapNum:0,...extra};}
function fixture(graph={atoms:[atom()],bonds:[]}){
 const store=createDocumentStore({schema:'nivram/1',version:'0.2.0-dev.1',graph});
 const nodes=Object.fromEntries(['apply-atom','atom-charge','atom-isotope','atom-radicals'].map(id=>[id,{valueAsNumber:0}]));
 const errors=[];const context={documentStore:store,graph:store.getSnapshot().document.graph,style:store.getSnapshot().document.style,selected:new Set(['a']),copy:structuredClone,validateProject:value=>validateProject(structuredClone(value)),stereoEditNotice,busy:false,activeJob:null,valenceEpoch:0,drag:null,scene:chemical(graph),
 $:id=>nodes[id],snapshot:()=>store.getSnapshot().document,clearValenceFeedback(){},elementEntry:{close(){},pending(){return false;}},keyboardEditor:{cancel(){}},gesture:{replaceChildren(){}},setBusy(v){context.busy=v;},notify:m=>errors.push(m),valenceFeedback:()=>null,
 api:async(action,data)=>{assert.equal(action,'render');return {graph:data.graph,scene:chemical(data.graph)};},
 install(result,options){store.commit({...options.baseProject,graph:result.graph},{expectedRevision:options.expectedRevision,mode:options.mode});context.graph=store.getSnapshot().document.graph;context.scene=result.scene;}};
 const jobs=createJobController(()=>store.getSnapshot().revision);context.jobs={begin:(channel,options)=>jobs.begin(channel,structuredClone(options)),cancelAll:()=>jobs.cancelAll()};vm.createContext(context);
 for(const [start,end] of [['async function runDocumentOperation(','async function loadText('],['async function undo(','async function clean('],["$('apply-atom').onclick=async()=>{","for(const b of document.querySelectorAll('[data-tool]'))"]]){
  const a=source.indexOf(start),b=source.indexOf(end,a);assert(a>=0&&b>a);vm.runInContext(source.slice(a,b),context);
 }
 return {context,store,errors,async apply(charge,radicals=0){nodes['atom-charge'].valueAsNumber=charge;nodes['atom-radicals'].valueAsNumber=radicals;await nodes['apply-atom'].onclick();}};
}
test('actual Apply command N → ammonium → N updates implicit and drawing H, with atomic Undo/Redo',async()=>{
 const f=fixture(),initial=f.store.getSnapshot();assert.equal(f.context.scene.formula,'H3N');
 await f.apply(1);assert.equal(f.context.scene.smiles,'[NH4+]');assert.deepEqual(f.context.scene.implicitHs,[4]);assert.deepEqual(f.context.scene.drawingHs,[4]);assert.equal(f.context.scene.formula,'H4N+');
 const charged=f.store.getSnapshot();assert.equal(charged.revision,initial.revision+1);assert.equal(charged.document.graph.atoms[0].explicitHs,0);assert.equal(charged.document.graph.atoms[0].noImplicit,false);
 await f.apply(0);assert.equal(f.context.scene.smiles,'N');assert.deepEqual(f.context.scene.drawingHs,[3]);
 await f.context.undo();assert.deepEqual(f.store.getSnapshot().document,charged.document);assert.equal(f.context.scene.smiles,'[NH4+]');
 await f.context.undo(true);assert.equal(f.context.scene.smiles,'N');assert.deepEqual(f.context.scene.implicitHs,[3]);assert.deepEqual(f.errors,[]);
});
test('bond order and charge/radical edits recalculate H without persisting a derived override',async()=>{
 const f=fixture();await f.apply(1,1);assert.deepEqual(f.context.scene.implicitHs,[3]);assert.deepEqual(f.context.scene.drawingHs,[3]);await f.context.undo();assert.deepEqual(f.context.scene.drawingHs,[3]);assert.equal(f.context.graph.atoms[0].radicals,0);
 const c=fixture({atoms:[atom('C'),{...atom('C'),id:'b',x:1.5}],bonds:[{id:'ab',begin:'a',end:'b',order:1}]});
 assert.deepEqual(c.context.scene.labels,{'0':'CH<sub>3</sub>','1':'CH<sub>3</sub>'});
 const next=structuredClone(c.context.graph);next.bonds[0].order=2;assert(await c.context.change(next));assert.deepEqual(c.context.scene.labels,{'0':'CH<sub>2</sub>','1':'CH<sub>2</sub>'});await c.context.undo();assert.deepEqual(c.context.scene.implicitHs,[3,3]);
 await c.apply(1);assert.deepEqual(c.context.scene.implicitHs,[2,3]);assert.equal(c.context.scene.labels['0'],'CH<sub>2</sub><sup>+</sup>');assert.deepEqual(c.errors,[]);
});
test('explicit fixed H and noImplicit survive charge edits; invalid neutralization preserves history',async()=>{
 const f=fixture({atoms:[atom('N',{charge:1,explicitHs:4,noImplicit:true})],bonds:[]});const before=f.store.getSnapshot(),history=f.store.historyState();
 await f.apply(0);assert.deepEqual(f.store.getSnapshot(),before);assert.deepEqual(f.store.historyState(),history);assert.equal(f.context.scene.smiles,'[NH4+]');assert.equal(f.errors.length,1);
 const fixed=fixture({atoms:[atom('N',{explicitHs:3,noImplicit:true})],bonds:[]});await fixed.apply(1,1);assert.equal(fixed.context.scene.smiles,'[NH3+]');assert.deepEqual(fixed.context.scene.implicitHs,[0]);assert.deepEqual(fixed.context.scene.drawingHs,[3]);assert.equal(fixed.context.graph.atoms[0].explicitHs,3);assert.equal(fixed.context.graph.atoms[0].noImplicit,true);await fixed.context.undo();assert.equal(fixed.context.scene.smiles,'N');assert.deepEqual(fixed.errors,[]);
});
