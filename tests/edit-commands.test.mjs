import test from 'node:test';
import assert from 'node:assert/strict';
import {editAtom,createBond,editBond,placeRing,deleteEntities,translateAtoms} from '../public/modules/edit-commands.mjs';

const empty=()=>({atoms:[],bonds:[]});
const ids=()=>{let i=0;return prefix=>`${prefix}-new-${++i}`;};
const atom=(id,x=0,y=0,extra={})=>({id,symbol:'C',x,y,charge:0,isotope:0,explicitHs:0,noImplicit:false,aromatic:false,chiralTag:0,radicals:0,mapNum:0,...extra});
const bond=(id,begin,end,extra={})=>({id,begin,end,order:1,direction:0,stereo:0,stereoAtoms:[],...extra});
const pair=()=>({atoms:[atom('a',0,0,{chiralTag:1}),atom('b',1.5,0,{chiralTag:2}),atom('c',3,1,{chiralTag:1})],bonds:[bond('ab','a','b')]});
const near=(actual,expected)=>assert.ok(Math.abs(actual-expected)<1e-10,`${actual} != ${expected}`);
function unchanged(input,operation){const before=structuredClone(input);try{return operation();}finally{assert.deepEqual(input,before,'The input graph must never be mutated.');}}

test('atom addition returns a complete new atom and candidate identity without changing input',()=>{
  const input=empty(),r=unchanged(input,()=>editAtom(input,{position:{x:2,y:-1},symbol:'O',makeId:ids()}));
  assert.equal(r.changed,true);assert.equal(r.atomId,'a-new-1');assert.deepEqual(r.createdAtomIds,[r.atomId]);
  assert.deepEqual(r.createdBondIds,[]);assert.deepEqual(r.graph.atoms,[atom(r.atomId,2,-1,{symbol:'O'})]);
});

test('atom replacement retains identity, charge, isotope and existing stereo while resetting pointer-edit fields',()=>{
  const input={atoms:[atom('a',2,3,{symbol:'N',charge:1,isotope:15,explicitHs:2,noImplicit:true,aromatic:true,chiralTag:2,radicals:1,mapNum:7})],bonds:[]};
  const r=unchanged(input,()=>editAtom(input,{atomId:'a',symbol:'O'}));
  assert.deepEqual(r.graph.atoms,[atom('a',2,3,{symbol:'O',charge:1,isotope:15,chiralTag:2,mapNum:7})]);
  assert.equal(r.atomId,'a');assert.deepEqual(r.createdAtomIds,[]);assert.equal(r.changed,true);
  const noOp=editAtom(r.graph,{atomId:'a',symbol:'O'});assert.equal(noOp.changed,false);assert.deepEqual(noOp.graph,r.graph);
});

test('bond from empty space creates carbon start, chosen endpoint and bond together at pointer default angle and length',()=>{
  const input=empty(),r=unchanged(input,()=>createBond(input,{startPosition:{x:2,y:3},endPosition:{x:2,y:3},endSymbol:'N',kind:'wedge',makeId:ids()}));
  assert.equal(r.changed,true);assert.equal(r.graph.atoms.length,2);assert.equal(r.graph.bonds.length,1);
  const [a,b]=r.graph.atoms;assert.equal(a.symbol,'C');assert.equal(b.symbol,'N');near(b.x,2+1.5*Math.cos(Math.PI/6));near(b.y,3.75);
  assert.deepEqual(r.createdAtomIds,[a.id,b.id]);assert.equal(r.atomId,b.id);assert.equal(r.bondId,r.graph.bonds[0].id);
  assert.deepEqual(r.graph.bonds[0],bond(r.bondId,a.id,b.id,{direction:1}));
});

test('bond endpoint snap and Shift-style free geometry match the existing pointer rules',()=>{
  const input={atoms:[atom('a',0,0)],bonds:[]};
  const snapped=createBond(input,{startAtomId:'a',endPosition:{x:2,y:.1},makeId:ids()});
  near(snapped.graph.atoms[1].x,1.5);near(snapped.graph.atoms[1].y,0);
  const free=createBond(input,{startAtomId:'a',endPosition:{x:2,y:.1},freeAngle:true,makeId:ids()});
  near(free.graph.atoms[1].x,2);near(free.graph.atoms[1].y,.1);
  const short=createBond(input,{startAtomId:'a',endPosition:{x:.1,y:0},freeAngle:true,makeId:ids()});
  near(short.graph.atoms[1].x,.6*Math.cos(Math.PI/6));near(short.graph.atoms[1].y,.3);
});

test('connecting existing atoms preserves coordinates, creates no atoms, and invalidates only endpoint chirality',()=>{
  const input=pair(),r=unchanged(input,()=>createBond(input,{startAtomId:'b',endAtomId:'c',kind:'2',makeId:ids()}));
  assert.deepEqual(r.createdAtomIds,[]);assert.equal(r.atomId,'c');assert.equal(r.graph.atoms.length,3);
  assert.deepEqual(r.graph.atoms.map(a=>[a.x,a.y]),input.atoms.map(a=>[a.x,a.y]));
  assert.deepEqual(r.graph.atoms.map(a=>a.chiralTag),[1,0,0]);
  assert.deepEqual(r.graph.bonds.at(-1),bond(r.bondId,'b','c',{order:2}));
});

test('self and duplicate bonds are no-ops and never allocate identities',()=>{
  const input=pair(),makeId=()=>assert.fail('No identities should be allocated for a rejected edge.');
  for(const [endAtomId,startAtomId,reason] of [['a','a','same-atom'],['a','b','already-bonded']]){
    const r=unchanged(input,()=>createBond(input,{startAtomId,endAtomId,makeId}));
    assert.equal(r.changed,false);assert.equal(r.reason,reason);assert.deepEqual(r.graph,input);assert.deepEqual(r.createdBondIds,[]);
  }
});

test('bond type uses shared stereo policy, with no-op preservation and direction-specific invalidation',()=>{
  const input=pair();input.bonds[0].direction=1;
  const same=unchanged(input,()=>editBond(input,{bondId:'ab',kind:'wedge'}));assert.equal(same.changed,false);assert.deepEqual(same.graph,input);
  const r=unchanged(input,()=>editBond(input,{bondId:'ab',kind:'dash'}));
  assert.equal(r.bondId,'ab');assert.equal(r.graph.bonds[0].direction,2);assert.deepEqual(r.graph.atoms.map(a=>a.chiralTag),[0,2,1]);
  const order=editBond(input,{bondId:'ab',kind:'2'});assert.deepEqual(order.graph.atoms.map(a=>a.chiralTag),[0,0,1]);
});

test('empty rings have supported sizes, carbon vertices, closed 1.5-length edges and alternating benzene orders',()=>{
  for(const type of ['3','4','5','6','7','8','benzene']){
    const input=empty(),r=unchanged(input,()=>placeRing(input,{position:{x:3,y:-2},type,makeId:ids()})),n=type==='benzene'?6:Number(type);
    assert.equal(r.graph.atoms.length,n);assert.equal(r.graph.bonds.length,n);assert.equal(r.createdAtomIds.length,n);assert.equal(r.createdBondIds.length,n);
    const byId=new Map(r.graph.atoms.map(a=>[a.id,a]));
    for(const [index,b] of r.graph.bonds.entries()){
      const a=byId.get(b.begin),c=byId.get(b.end);near(Math.hypot(c.x-a.x,c.y-a.y),1.5);
      assert.equal(b.order,type==='benzene'?(index%2===0?1:2):1);assert.equal(a.symbol,'C');
    }
    near(r.graph.atoms.reduce((sum,a)=>sum+a.x,0)/n,3);near(r.graph.atoms.reduce((sum,a)=>sum+a.y,0)/n,-2);
  }
});

test('atom-attached ring reuses anchor identity and position and clears only affected atom stereo',()=>{
  const input=pair(),r=unchanged(input,()=>placeRing(input,{atomId:'a',type:'5',makeId:ids()}));
  assert.equal(r.graph.atoms.length,input.atoms.length+4);assert.equal(r.createdAtomIds.length,4);
  assert.deepEqual(r.graph.atoms[0],{...input.atoms[0],chiralTag:0});assert.deepEqual(r.graph.atoms.slice(1,3),input.atoms.slice(1));
  assert.equal(r.graph.bonds.filter(b=>b.begin==='a'||b.end==='a').length,3);
});

test('six-membered defaults have top/bottom vertices and vertical left/right edges',()=>{
  for(const type of ['6','benzene']){
    const {graph}=placeRing(empty(),{position:{x:3,y:-2},type,makeId:ids()});
    const ordered=[...graph.atoms].sort((a,b)=>a.y-b.y),byId=new Map(graph.atoms.map(a=>[a.id,a]));
    near(ordered[0].x,3);near(ordered[0].y,-3.5);near(ordered.at(-1).x,3);near(ordered.at(-1).y,-.5);
    const vertical=graph.bonds.map(b=>[byId.get(b.begin),byId.get(b.end)]).filter(([a,b])=>Math.abs(a.x-b.x)<1e-10);
    assert.equal(vertical.length,2);
    const x=vertical.map(([a])=>a.x).sort((a,b)=>a-b);near(x[0],3-Math.sqrt(3)*.75);near(x[1],3+Math.sqrt(3)*.75);
  }
});

test('five-membered default is the left-right reflection of the preceding geometry',()=>{
  const {graph}=placeRing(empty(),{position:{x:2,y:-3},type:'5',makeId:ids()});
  const radius=1.5/(2*Math.sin(Math.PI/5));
  for(const [i,atom] of graph.atoms.entries()){
    const oldAngle=Math.PI+i*2*Math.PI/5;
    near(atom.x,2-radius*Math.cos(oldAngle));near(atom.y,-3+radius*Math.sin(oldAngle));
  }
  near(Math.max(...graph.atoms.map(a=>a.x)),2+radius);
  const left=graph.atoms.filter(a=>Math.abs(a.x-Math.min(...graph.atoms.map(a=>a.x)))<1e-10);
  assert.equal(left.length,2,'The mirrored pentagon has a vertical left side.');
});

test('new five/six-membered atom attachments use the leftmost lower anchor without moving existing atoms',()=>{
  for(const type of ['5','6','benzene']){
    const input={atoms:[atom('anchor',5,-2,{charge:1,isotope:13,mapNum:9}),atom('other',-3,8)],bonds:[]};
    const result=unchanged(input,()=>placeRing(input,{atomId:'anchor',type,makeId:ids()}));
    assert.deepEqual(result.graph.atoms.slice(0,2),input.atoms);
    const ring=[result.graph.atoms[0],...result.graph.atoms.slice(2)];
    const minX=Math.min(...ring.map(a=>a.x));near(minX,5);
    const left=ring.filter(a=>Math.abs(a.x-minX)<1e-10);assert.equal(left.length,2);
    near(Math.min(...left.map(a=>a.y)),-2);
  }
});

test('other ring sizes retain the preceding blank and atom-attached coordinate defaults',()=>{
  for(const n of [3,4,7,8])for(const attached of [false,true]){
    const radius=1.5/(2*Math.sin(Math.PI/n)),input=attached?{atoms:[atom('anchor',2,-3)],bonds:[]}:empty();
    const {graph}=placeRing(input,{position:{x:2,y:-3},atomId:attached?'anchor':null,type:String(n),makeId:ids()});
    for(const [i,point] of graph.atoms.entries()){
      if(attached&&i===0){assert.deepEqual(point,input.atoms[0]);continue;}
      const angle=Math.PI+i*2*Math.PI/n;
      assert.equal(point.x,2+(attached?radius:0)+radius*Math.cos(angle));
      assert.equal(point.y,-3+radius*Math.sin(angle));
    }
  }
});

test('fused five/six-membered geometry keeps its prior shared-bond placement on either side',()=>{
  const expected={
    '5':[[1.9635254915624212,1.4265847744427302],[.75,2.30826265288144],[-.4635254915624212,1.4265847744427307]],
    '6':[[2.25,1.299038105676658],[1.5,2.598076211353316],[0,2.598076211353316],[-.75,1.299038105676658]]
  };
  for(const type of ['5','6','benzene'])for(const side of [-1,1]){
    const input=pair(),r=unchanged(input,()=>placeRing(input,{bondId:'ab',position:{x:.75,y:side},type,makeId:ids()}));
    assert.deepEqual(r.graph.bonds[0],input.bonds[0]);
    for(const [i,a] of r.graph.atoms.slice(3).entries()){
      const [x,y]=expected[type==='benzene'?'6':type][i];near(a.x,x);near(a.y,y*side);
    }
  }
});

test('bond-fused ring retains the existing bond and endpoints and supports both sides',()=>{
  const input=pair();input.bonds[0].order=2;
  for(const y of [-1,1]){
    const r=unchanged(input,()=>placeRing(input,{bondId:'ab',position:{x:.75,y},type:'benzene',makeId:ids()}));
    assert.equal(r.graph.atoms.length,7);assert.equal(r.createdAtomIds.length,4);assert.equal(r.createdBondIds.length,5);
    assert.deepEqual(r.graph.bonds[0],input.bonds[0]);assert.deepEqual(r.graph.bonds.slice(1).map(b=>b.order),[1,2,1,2,1]);
    assert.ok(r.graph.atoms.slice(3).every(a=>a.y*y>0));assert.deepEqual(r.graph.atoms.map(a=>a.chiralTag).slice(0,3),[0,0,1]);
  }
});

test('delete removes dependent edges and stale E/Z references while retaining unrelated fields',()=>{
  const input={atoms:[atom('a'),atom('b',1,0,{chiralTag:1}),atom('c',2,0,{chiralTag:2}),atom('d',3,0,{isotope:13})],
    bonds:[bond('ab','a','b'),bond('bc','b','c',{order:2,stereo:3,stereoAtoms:['a','d']}),bond('cd','c','d')]};
  const r=unchanged(input,()=>deleteEntities(input,{atomIds:['a']}));
  assert.deepEqual(r.graph.atoms.map(a=>a.id),['b','c','d']);assert.deepEqual(r.graph.bonds.map(b=>b.id),['bc','cd']);
  assert.equal(r.graph.atoms[0].chiralTag,0);assert.equal(r.graph.atoms[1].chiralTag,2);assert.equal(r.graph.atoms[2].isotope,13);
  assert.equal(r.graph.bonds[0].stereo,0);assert.deepEqual(r.graph.bonds[0].stereoAtoms,[]);
  const edge=deleteEntities(input,{bondIds:['bc']});assert.deepEqual(edge.graph.atoms.map(a=>a.chiralTag),[0,0,0,0]);assert.equal(edge.graph.atoms.length,4);
});

test('translation changes only selected coordinates and preserves stereo and topology',()=>{
  const input=pair(),r=unchanged(input,()=>translateAtoms(input,{atomIds:['a','c','a'],dx:.25,dy:-.5}));
  assert.deepEqual(r.graph.bonds,input.bonds);assert.deepEqual(r.graph.atoms[1],input.atoms[1]);
  for(const index of [0,2])assert.deepEqual(r.graph.atoms[index],{...input.atoms[index],x:input.atoms[index].x+.25,y:input.atoms[index].y-.5});
  assert.equal(translateAtoms(input,{atomIds:['a'],dx:0,dy:0}).changed,false);
  assert.equal(deleteEntities(input,{}).changed,false);assert.equal(translateAtoms(input,{atomIds:[],dx:1,dy:2}).changed,false);
});

test('invalid targets, unsupported choices, nonfinite coordinates and identity collisions reject without mutating input',()=>{
  const input=pair();
  const calls=[
    ()=>editAtom(input,{atomId:'missing',symbol:'N'}),
    ()=>editAtom(input,{position:{x:NaN,y:0},makeId:ids()}),
    ()=>editAtom(input,{position:{x:0,y:0},symbol:'not-an-element',makeId:ids()}),
    ()=>editAtom(input,{position:{x:0,y:0},makeId:()=> 'a'}),
    ()=>createBond(input,{startAtomId:'missing',endAtomId:'b',makeId:ids()}),
    ()=>createBond(input,{startAtomId:'a',endAtomId:'missing',makeId:ids()}),
    ()=>createBond(input,{startAtomId:'a',endPosition:{x:Infinity,y:0},makeId:ids()}),
    ()=>createBond(input,{startAtomId:'b',endAtomId:'c',kind:'4',makeId:ids()}),
    ()=>editBond(input,{bondId:'missing',kind:'1'}),
    ()=>placeRing(input,{atomId:'missing',type:'6',makeId:ids()}),
    ()=>placeRing(input,{bondId:'missing',position:{x:0,y:0},type:'6',makeId:ids()}),
    ()=>placeRing(input,{position:{x:0,y:0},type:'9',makeId:ids()}),
    ()=>deleteEntities(input,{atomIds:['missing']}),
    ()=>deleteEntities(input,{bondIds:['missing']}),
    ()=>translateAtoms(input,{atomIds:['missing'],dx:1,dy:1}),
    ()=>translateAtoms(input,{atomIds:['a'],dx:1,dy:NaN}),
  ];
  for(const call of calls)unchanged(input,()=>assert.throws(call));
  input.bonds[0].order=3;
  unchanged(input,()=>assert.throws(()=>placeRing(input,{bondId:'ab',position:{x:1,y:1},type:'6',makeId:ids()}),/single or double/i));
});

test('late identity failure in a multi-entity edit discards the private partial candidate',()=>{
  const input=pair();let allocations=0;
  const makeId=prefix=>++allocations<3?`${prefix}-temporary-${allocations}`:'a';
  unchanged(input,()=>assert.throws(()=>placeRing(input,{position:{x:5,y:5},type:'6',makeId}),/unique valid identifier/i));
  assert.equal(allocations,3,'The failure must happen after two private atoms have already been created.');
  assert.equal(input.atoms.length,3);assert.equal(input.bonds.length,1);
});
