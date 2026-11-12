import test from 'node:test';
import assert from 'node:assert/strict';
import {deleteEntities} from '../public/modules/edit-commands.mjs';
import {stereoEditNotice} from '../public/modules/bond-edit.mjs';
import {createDocumentStore} from '../public/modules/document.mjs';

const atom=(id,x,y,extra={})=>({id,symbol:'C',x,y,charge:0,isotope:0,explicitHs:0,
  noImplicit:false,aromatic:false,chiralTag:0,radicals:0,mapNum:0,...extra});
const bond=(id,begin,end,extra={})=>({id,begin,end,order:1,direction:0,stereo:0,stereoAtoms:[],...extra});
function alkene(suffix=''){
  return {atoms:[atom('a'+suffix,-1,1),atom('b'+suffix,0,0),atom('c'+suffix,1.5,0),atom('d'+suffix,2.5,-1)],
    bonds:[bond('ab'+suffix,'a'+suffix,'b'+suffix,{direction:4}),
      bond('bc'+suffix,'b'+suffix,'c'+suffix,{order:2,stereo:3,stereoAtoms:['a'+suffix,'d'+suffix]}),
      bond('cd'+suffix,'c'+suffix,'d'+suffix,{direction:4})]};
}
function deleting(input,selection){
  const before=structuredClone(input),result=deleteEntities(input,selection);
  assert.deepEqual(input,before,'Deletion must not mutate the accepted graph.');
  return result;
}

test('deleting either reference bond clears only its invalidated E/Z annotation',()=>{
  for(const reverseDouble of [false,true])for(const reverseEdges of [false,true])for(const selected of ['ab','cd']){
    const input=alkene(),other=alkene('x');input.atoms.push(...other.atoms);input.bonds.push(...other.bonds);
    if(reverseDouble){const b=input.bonds[1];[b.begin,b.end]=[b.end,b.begin];b.stereoAtoms.reverse();}
    if(reverseEdges)for(const i of [0,2]){const b=input.bonds[i];[b.begin,b.end]=[b.end,b.begin];}
    const result=deleting(input,{bondIds:[selected]}),double=result.graph.bonds.find(b=>b.id==='bc');
    assert.equal(result.changed,true);assert.equal(result.graph.atoms.length,8);
    assert.equal(double.stereo,0);assert.deepEqual(double.stereoAtoms,[]);
    assert.deepEqual(result.graph.atoms,input.atoms);
    assert.deepEqual(result.graph.bonds.filter(b=>b.id.endsWith('x')),other.bonds);
    assert.equal(stereoEditNotice(input,result.graph),
      'Edit applied: 1 stereo annotation cleared. Review stereochemistry. Undo restores the previous drawing.');
  }
});

test('a surviving alternative substituent is not silently chosen as a new stereo reference',()=>{
  const input=alkene();input.atoms.push(atom('e',-1,-1,{symbol:'Cl',isotope:37,mapNum:8}));
  input.bonds.push(bond('be','b','e'));
  const result=deleting(input,{bondIds:['ab']});
  assert.deepEqual(result.graph.atoms,input.atoms);
  assert.deepEqual(result.graph.bonds.find(b=>b.id==='bc'),{...input.bonds[1],stereo:0,stereoAtoms:[]});
  assert.deepEqual(result.graph.bonds.find(b=>b.id==='be'),input.bonds[3]);
});

test('reference atom deletion, combined deletion and duplicate selections clear stale references',()=>{
  for(const selection of [{atomIds:['a']},{atomIds:['d']},{bondIds:['ab','cd']},
    {atomIds:['a','a'],bondIds:['cd','cd']}]){
    const result=deleting(alkene(),selection),double=result.graph.bonds.find(b=>b.id==='bc');
    assert.equal(double.stereo,0);assert.deepEqual(double.stereoAtoms,[]);
  }
});

test('nonreference and empty deletion preserve the supported stereo reference pair',()=>{
  const input=alkene();input.atoms.push(atom('e',-1,-1,{symbol:'Cl'}));input.bonds.push(bond('be','b','e'));
  for(const selection of [{bondIds:['be']},{atomIds:['e']}]){
    const result=deleting(input,selection);
    assert.deepEqual(result.graph.bonds.find(b=>b.id==='bc'),input.bonds[1]);
  }
  const empty=deleting(input,{});assert.equal(empty.changed,false);assert.deepEqual(empty.graph,input);
});

test('deletion preserves unrelated atom fields, wedge directions and existing chirality policy',()=>{
  const input=alkene();
  Object.assign(input.atoms[0],{isotope:13,mapNum:7});input.atoms[1].chiralTag=1;input.atoms[2].chiralTag=2;
  input.atoms.push(atom('e',10,0,{symbol:'N',charge:1,explicitHs:4,noImplicit:true,radicals:0,mapNum:9}));
  input.atoms.push(atom('f',20,0,{chiralTag:1}),atom('g',21,1));input.bonds.push(bond('fg','f','g',{direction:1}));
  const result=deleting(input,{bondIds:['ab']});
  assert.deepEqual(result.graph.atoms,input.atoms.map(a=>a.id==='b'?{...a,chiralTag:0}:a));
  assert.deepEqual(result.graph.bonds.find(b=>b.id==='fg'),input.bonds.at(-1));
});

test('empty deletion does not repair a preexisting invalid reference graph',()=>{
  const input=alkene();input.bonds.splice(0,1);
  const result=deleting(input,{});assert.equal(result.changed,false);assert.deepEqual(result.graph,input);
});

test('accepted deletion is one undo step and rejected view application retains the original graph',()=>{
  const graph=alkene(),store=createDocumentStore({schema:'nivram/1',graph});
  const before=store.getSnapshot(),candidate={...before.document,graph:deleteEntities(graph,{bondIds:['ab']}).graph};
  assert.throws(()=>store.commit(candidate,{expectedRevision:before.revision,apply:()=>{throw new Error('view failed');}}),/view failed/);
  assert.deepEqual(store.getSnapshot(),before);assert.deepEqual(store.historyState(),{canUndo:false,canRedo:false});
  const accepted=store.commit(candidate,{expectedRevision:before.revision});
  assert.equal(accepted.revision,before.revision+1);assert.equal(accepted.dirty,true);
  assert.equal(accepted.document.graph.bonds.find(b=>b.id==='bc').stereo,0);
  store.commit(store.historyTarget(),{mode:'undo'});
  assert.deepEqual(store.getSnapshot().document,before.document);assert.equal(store.getSnapshot().dirty,false);
  assert.deepEqual(store.historyState(),{canUndo:false,canRedo:true});
  store.commit(store.historyTarget(true),{mode:'redo'});
  assert.deepEqual(store.getSnapshot().document,candidate);assert.equal(store.getSnapshot().dirty,true);
});
