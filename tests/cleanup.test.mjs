import test from 'node:test';
import assert from 'node:assert/strict';
import {createDocumentStore,validateProject} from '../public/modules/document.mjs';
import {prepareCleanupPreview} from '../public/modules/cleanup.mjs';

const initial=()=>({schema:'nivram/1',graph:{atoms:[
  {id:'a0',symbol:'C',x:0,y:0,isotope:13,mapNum:7},
  {id:'a1',symbol:'O',x:1.5,y:0},
  {id:'a2',symbol:'N',x:3,y:1},
],bonds:[{id:'b0',begin:'a0',end:'a1',order:1},{id:'b1',begin:'a1',end:'a2',order:1}]},export:{width:1234,marginMode:'pixels',margin:17},analysis:[{kind:'fixture',status:'unavailable'}]});
const backend={id:'rdkit',name:'RDKit',version:'test-version',limitations:'Experimental. Marvin equivalence is not established.'};
function fixture(selected=[]){
  const store=createDocumentStore(initial()),source=store.getSnapshot();
  const graph=structuredClone(source.document.graph),chosen=new Set(selected.length?selected:graph.atoms.map(a=>a.id));
  for(const atom of graph.atoms)if(chosen.has(atom.id)){atom.x+=.4;atom.y-=.25;}
  const result={graph,scene:{style:source.document.style}},calls=[];
  const options={source,result,selected,backend,getRevision:()=>store.getSnapshot().revision,
    prepare:(result,document)=>({project:validateProject({...document,graph:result.graph,style:result.scene.style}),scene:result.scene,fragment:null}),
    commit:(result,settings)=>{calls.push(settings);store.commit({...settings.baseProject,graph:result.graph,style:result.scene.style},settings);}};
  return {store,source,result,calls,options};
}

test('preparing and cancelling a cleanup preserves the complete document and history',()=>{
  const {store,source,calls,options}=fixture(['a1']),history=store.historyState();
  const preview=prepareCleanupPreview(options);
  assert.deepEqual(store.getSnapshot(),source);assert.equal(calls.length,0);
  assert.equal(preview.changedAtoms,1);assert.equal(preview.scope,'selection');
  preview.cancel();assert.equal(preview.state,'cancelled');
  assert.deepEqual(store.getSnapshot(),source);assert.deepEqual(store.historyState(),history);
  assert.throws(()=>preview.apply(),/no longer available/i);assert.equal(calls.length,0);
});

test('Apply commits once and one Undo restores all document fields',()=>{
  const {store,source,result,calls,options}=fixture();const preview=prepareCleanupPreview(options);
  preview.apply();const applied=store.getSnapshot();
  assert.equal(calls.length,1);assert.equal(applied.revision,source.revision+1);assert.equal(preview.state,'applied');
  assert.deepEqual(applied.document,{...source.document,graph:result.graph});
  assert.throws(()=>preview.apply(),/no longer available/i);
  store.commit(store.historyTarget(),{mode:'undo'});assert.deepEqual(store.getSnapshot().document,source.document);
  assert.deepEqual(store.historyState(),{canUndo:false,canRedo:true});
  store.commit(store.historyTarget(true),{mode:'redo'});assert.deepEqual(store.getSnapshot().document,applied.document);
});

test('selected cleanup retains exact unselected coordinates',()=>{
  const {store,source,options}=fixture(['a1']);prepareCleanupPreview(options).apply();
  for(const i of [0,2])assert.deepEqual(store.getSnapshot().document.graph.atoms[i],source.document.graph.atoms[i]);
  const bad=fixture(['a1']);bad.result.graph.atoms[0].x+=Number.EPSILON;
  assert.throws(()=>prepareCleanupPreview(bad.options),/outside the selection/i);
  assert.deepEqual(bad.store.getSnapshot(),bad.source);assert.equal(bad.calls.length,0);
});

test('cleanup rejects changes to chemical fields, identifiers, atom order and bonds',()=>{
  const changes=[g=>g.atoms[1].charge=1,g=>g.atoms[0].isotope=14,g=>g.atoms[1].chiralTag=1,
    g=>g.atoms[0].mapNum=8,g=>g.atoms[2].radicals=1,g=>g.atoms.reverse(),
    g=>g.bonds[0].order=2,g=>g.bonds[0].direction=1,g=>g.bonds[0].stereo=1,g=>g.bonds[0].id='other'];
  for(const change of changes){const f=fixture();change(f.result.graph);assert.throws(()=>prepareCleanupPreview(f.options),/chemical|atom|bond|stereochemistry/i);assert.deepEqual(f.store.getSnapshot(),f.source);}
});

test('invalid selections, nonfinite coordinates and changed appearance fail before preview',()=>{
  for(const selected of [['missing'],['a1','a1']]){const f=fixture();f.options.selected=selected;assert.throws(()=>prepareCleanupPreview(f.options),/selection/i);}
  const numeric=fixture();numeric.result.graph.atoms[0].x=NaN;assert.throws(()=>prepareCleanupPreview(numeric.options),/finite/i);
  const styled=fixture();styled.result.scene.style={...styled.result.scene.style,labelSize:20};assert.throws(()=>prepareCleanupPreview(styled.options),/appearance|settings/i);
});

test('a revision change, including edit then undo to identical content, invalidates Apply',()=>{
  const f=fixture(),preview=prepareCleanupPreview(f.options),changed=structuredClone(f.source.document);changed.export.width++;
  f.store.commit(changed);f.store.commit(f.store.historyTarget(),{mode:'undo'});const before=f.store.getSnapshot();
  assert.deepEqual(before.document,f.source.document);assert.throws(()=>preview.apply(),/document changed/i);
  assert.deepEqual(f.store.getSnapshot(),before);assert.equal(f.calls.length,0);
});

test('staged source, response, selection and backend cannot be changed by their callers',()=>{
  const f=fixture(['a1']),identity={...backend};f.options.backend=identity;
  const expected=structuredClone(f.result.graph),preview=prepareCleanupPreview(f.options);
  f.source.document.export.width=999;f.result.graph.atoms[1].charge=3;f.options.selected.length=0;identity.name='Mislabelled';
  preview.apply();assert.deepEqual(f.store.getSnapshot().document.graph,expected);assert.equal(f.store.getSnapshot().document.export.width,1234);
  assert.equal(preview.backend.name,'RDKit');assert.throws(()=>preview.backend.name='changed',TypeError);
});

test('render validation and failed view installation cannot partially commit cleanup',()=>{
  const f=fixture();f.options.prepare=()=>{throw new Error('Malformed SVG');};
  assert.throws(()=>prepareCleanupPreview(f.options),/Malformed SVG/);assert.deepEqual(f.store.getSnapshot(),f.source);
  const g=fixture();g.options.commit=(result,settings)=>g.store.commit({...settings.baseProject,graph:result.graph},{...settings,apply:()=>{throw new Error('View failure');}});
  const preview=prepareCleanupPreview(g.options);assert.throws(()=>preview.apply(),/View failure/);assert.deepEqual(g.store.getSnapshot(),g.source);assert.equal(preview.state,'pending');
});
