import test from 'node:test';
import assert from 'node:assert/strict';
import {setBondKind,stereoEditNotice} from '../public/modules/bond-edit.mjs';
import {createDocumentStore} from '../public/modules/document.mjs';

const graph=()=>({atoms:[{id:'c',symbol:'C',x:0,y:0,chiralTag:2},{id:'f',symbol:'F',x:1,y:0,chiralTag:0},{id:'cl',symbol:'Cl',x:0,y:1,chiralTag:0},{id:'br',symbol:'Br',x:-1,y:0,chiralTag:0},{id:'i',symbol:'I',x:0,y:-1,chiralTag:0}],bonds:['f','cl','br','i'].map((end,i)=>({id:'b'+i,begin:'c',end,order:1,direction:0,stereo:0,stereoAtoms:[]}))});
function twoCenters(){
  const candidate=graph();candidate.atoms[1].symbol='C';candidate.atoms[1].chiralTag=1;
  for(const [index,symbol] of ['N','O','F'].entries()){
    const id='neighbor'+index;candidate.atoms.push({id,symbol,x:2,y:index-1,chiralTag:0});
    candidate.bonds.push({id:'neighbor-bond'+index,begin:'f',end:id,order:1,direction:0,stereo:0,stereoAtoms:[]});
  }
  return candidate;
}
const alkene=()=>({atoms:[{id:'f0',symbol:'F',x:-1,y:1},{id:'c0',symbol:'C',x:0,y:0},{id:'c1',symbol:'C',x:1,y:0},{id:'f1',symbol:'F',x:2,y:-1}],bonds:[{id:'left',begin:'f0',end:'c0',order:1,direction:4,stereo:0,stereoAtoms:[]},{id:'double',begin:'c0',end:'c1',order:2,direction:0,stereo:3,stereoAtoms:['f0','f1']},{id:'right',begin:'c1',end:'f1',order:1,direction:4,stereo:0,stereoAtoms:[]}]});

test('choosing the existing single bond is a no-op that preserves tetrahedral stereo',()=>{
  const candidate=graph(),before=structuredClone(candidate);
  const changed=setBondKind(candidate,'b0','1');
  assert.deepEqual(candidate,before);
  assert.equal(changed,false);
});

test('choosing an existing stereobond preserves its assignment',()=>{
  const candidate=graph();candidate.bonds[0].direction=1;const before=structuredClone(candidate);
  const changed=setBondKind(candidate,'b0','wedge');
  assert.deepEqual(candidate,before);assert.equal(changed,false);
});

test('changing wedge direction re-evaluates only its start atom, preserving the other center',()=>{
  const candidate=twoCenters();candidate.bonds[0].direction=1;
  assert.equal(setBondKind(candidate,'b0','dash'),true);
  assert.equal(candidate.atoms[0].chiralTag,0);assert.equal(candidate.atoms[1].chiralTag,1);
  assert.equal(candidate.bonds[0].direction,2);
});

test('changing bond order invalidates affected endpoint assignments for backend revalidation',()=>{
  const candidate=twoCenters();
  assert.equal(setBondKind(candidate,'b0','2'),true);
  assert.equal(candidate.atoms[0].chiralTag,0);assert.equal(candidate.atoms[1].chiralTag,0);
  assert.equal(candidate.bonds[0].stereo,0);assert.deepEqual(candidate.bonds[0].stereoAtoms,[]);
});

test('choosing the existing double bond keeps E/Z references and adds no undo entry',()=>{
  const initial={schema:'nivram/1',graph:alkene()},store=createDocumentStore(initial);
  const candidate=structuredClone(store.getSnapshot().document.graph),before=structuredClone(candidate);
  if(setBondKind(candidate,'double','2'))store.commit({...store.getSnapshot().document,graph:candidate});
  assert.deepEqual(candidate,before);assert.equal(store.historyState().canUndo,false);
  assert.equal(store.getSnapshot().revision,0);
});

test('changing a double bond to single clears its obsolete E/Z references',()=>{
  const candidate=alkene();assert.equal(setBondKind(candidate,'double','1'),true);
  assert.equal(candidate.bonds[1].stereo,0);assert.deepEqual(candidate.bonds[1].stereoAtoms,[]);
});

test('accepted stereo loss produces a review notice with an undo remedy',()=>{
  const before=graph(),after=structuredClone(before);after.atoms[0].chiralTag=0;
  assert.match(stereoEditNotice(before,after),/1 stereo annotation cleared/);
  assert.match(stereoEditNotice(before,after),/Review stereochemistry.*Undo/);
});

test('accepted wedge changes and removed double stereo are reported',()=>{
  const before=graph();before.bonds[0].direction=1;
  const after=structuredClone(before);after.bonds[0].direction=2;after.atoms[0].chiralTag=1;
  assert.match(stereoEditNotice(before,after),/2 stereo annotations changed/);
  const double=alkene(),single=structuredClone(double);setBondKind(single,'double','1');
  assert.match(stereoEditNotice(double,single),/1 stereo annotation cleared/);
});

test('coordinate-only moves and unchanged stereo produce no loss notice',()=>{
  const before=twoCenters(),after=structuredClone(before);for(const atom of after.atoms){atom.x+=3;atom.y-=2;}
  assert.equal(stereoEditNotice(before,after),'');
  assert.equal(stereoEditNotice(alkene(),alkene()),'');
});
