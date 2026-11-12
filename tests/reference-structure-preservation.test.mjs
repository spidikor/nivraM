/** Reference-inspired preservation contracts, not image matching or chemistry validation.
 * Fixtures are hand-authored synthetic graphs. Coordinates intentionally differ
 * in scale and are not traced from images or proposed as correct drawing rules.
 * Runs without a browser, Windows, RDKit, fonts, network or raster renderer.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {deflateSync} from 'node:zlib';
import {createDocumentStore,validateProject,DEFAULT_STYLE,DEFAULT_EXPORT} from '../public/modules/document.mjs';
import {projectWithProfile} from '../public/modules/profiles.mjs';
import {translateAtoms} from '../public/modules/edit-commands.mjs';
import {snapshotCoordinates} from '../public/modules/png-coordinates.mjs';
import {stampPng,COORDINATE_KEY} from '../public/modules/png-metadata.mjs';

const atom=(id,symbol,x,y,extra={})=>({id,symbol,x,y,charge:0,isotope:0,explicitHs:0,noImplicit:false,aromatic:false,chiralTag:0,radicals:0,mapNum:0,...extra});
const bond=(id,begin,end,extra={})=>({id,begin,end,order:1,direction:0,stereo:0,stereoAtoms:[],...extra});
function mixedGraph(){
  return {atoms:[atom('amine','N',-4.125,2.5,{explicitHs:1,noImplicit:true,isotope:15,mapNum:7}),
    atom('methyl-a','C',-5.625,2.5),atom('methyl-b','C',-4.125,4),
    atom('center','C',10.25,-5.5,{chiralTag:1}),atom('fluorine','F',14,-5.5),
    atom('chlorine','Cl',10.25,-1.75),atom('bromine','Br',6.5,-5.5),atom('iodine','I',10.25,-9.25)],
  bonds:[bond('amine-a','amine','methyl-a'),bond('amine-b','amine','methyl-b'),
    bond('wedge','center','fluorine',{direction:1}),bond('hash','center','chlorine',{direction:2}),
    bond('c-br','center','bromine'),bond('c-i','center','iodine')]};
}
const project=()=>validateProject({schema:'nivram/1',graph:mixedGraph(),analysis:{source:'synthetic reference-policy fixture'}});
const profile=(transparentBackground)=>({schema:'nivram-profile/1',id:'c'.repeat(32),revision:1,name:'Synthetic appearance',
  style:{...DEFAULT_STYLE,bondWidth:1.75,labelSize:28,bondSpacing:.21,labelPadding:.125,hashBondStyle:'spaced',color:false},
  export:{...DEFAULT_EXPORT,width:4096,marginMode:'pixels',margin:3.125,transparentBackground}});
function vectors(graph){
  const atoms=new Map(graph.atoms.map(a=>[a.id,a]));
  return graph.bonds.map(b=>{const a=atoms.get(b.begin),z=atoms.get(b.end);return [b.id,z.x-a.x,z.y-a.y];});
}
function assertSourceScaleDistinction(graph){
  const v=vectors(graph);assert.deepEqual(v.find(x=>x[0]==='amine-a'),['amine-a',-1.5,0]);
  assert.deepEqual(v.find(x=>x[0]==='wedge'),['wedge',3.75,0]);
}

test('project validation and serialization preserve mixed-scale coordinates and represented annotations',()=>{
  const raw=mixedGraph(),rawBefore=structuredClone(raw);
  const input=validateProject({schema:'nivram/1',graph:raw}),before=structuredClone(input);
  assert.deepEqual(input.graph,rawBefore);assert.deepEqual(raw,rawBefore);
  const restored=validateProject(JSON.parse(JSON.stringify(input)));
  assert.deepEqual(restored,input);assert.deepEqual(input,before);assertSourceScaleDistinction(restored.graph);
  assert.deepEqual(JSON.parse(snapshotCoordinates(restored.graph)),input.graph);
});

test('appearance and export profiles never normalize the scale of separate structures',()=>{
  const input=project(),before=structuredClone(input);
  for(const transparent of [false,true]){
    const result=projectWithProfile(input,profile(transparent));
    assert.deepEqual(result.graph,before.graph);assert.deepEqual(result.analysis,before.analysis);
    assert.equal(result.style.hashBondStyle,'spaced');assert.equal(result.export.transparentBackground,transparent);
    assertSourceScaleDistinction(result.graph);assert.deepEqual(JSON.parse(snapshotCoordinates(result.graph)),before.graph);
  }
  assert.deepEqual(input,before);
});

test('moving one disconnected structure preserves all internal vectors and the other structure',()=>{
  const input=mixedGraph(),before=structuredClone(input),ids=['amine','methyl-a','methyl-b'];
  const moved=translateAtoms(input,{atomIds:ids,dx:.375,dy:-1.125});
  assert.equal(moved.changed,true);assert.deepEqual(moved.graph.bonds,input.bonds);
  assert.deepEqual(vectors(moved.graph),vectors(input));assert.deepEqual(input,before);
  for(const original of input.atoms){const actual=moved.graph.atoms.find(a=>a.id===original.id);
    assert.deepEqual(actual,ids.includes(original.id)?{...original,x:original.x+.375,y:original.y-1.125}:original);
  }
});

test('neutral and protonated records stay distinct through commit, undo, redo and coordinate snapshots',()=>{
  const neutral=project(),charged=structuredClone(neutral),n=charged.graph.atoms.find(a=>a.id==='amine');
  n.charge=1;n.explicitHs=2; // Supplied state, not an inferred protonation operation.
  const store=createDocumentStore(neutral),initial=store.getSnapshot();
  store.commit(charged);assert.equal(store.getSnapshot().revision,initial.revision+1);
  assert.deepEqual(store.getSnapshot().document.graph,charged.graph);
  assert.notEqual(snapshotCoordinates(neutral.graph),snapshotCoordinates(charged.graph));
  assert.deepEqual(charged.graph.bonds,neutral.graph.bonds);assert.deepEqual(vectors(charged.graph),vectors(neutral.graph));
  store.commit(store.historyTarget(),{mode:'undo'});assert.deepEqual(store.getSnapshot().document,neutral);
  store.commit(store.historyTarget(true),{mode:'redo'});assert.deepEqual(store.getSnapshot().document,charged);
  assert.deepEqual(neutral,initial.document);
});

test('profile undo and redo retain wedge/hash endpoints, source directions and unequal component geometry',()=>{
  const input=project(),store=createDocumentStore(input),applied=projectWithProfile(input,profile(true));
  store.commit(applied);store.commit(store.historyTarget(),{mode:'undo'});
  assert.deepEqual(store.getSnapshot().document,input);
  store.commit(store.historyTarget(true),{mode:'redo'});assert.deepEqual(store.getSnapshot().document,applied);
  const graph=JSON.parse(snapshotCoordinates(store.getSnapshot().document.graph));
  assert.deepEqual(graph.bonds,input.graph.bonds);assert.equal(graph.atoms.find(a=>a.id==='center').chiralTag,1);
  assert.deepEqual(graph.bonds.filter(b=>b.direction).map(b=>[b.begin,b.end,b.direction]),
    [['center','fluorine',1],['center','chlorine',2]]);assertSourceScaleDistinction(graph);
});

// Independent test-side PNG framing/CRC parser. No production parser is used
// as the oracle. The 1x1 pixel is only a carrier, never a molecular rendering.
function crc(bytes){let value=0xffffffff;for(const byte of bytes){value^=byte;for(let i=0;i<8;i++)value=(value>>>1)^((value&1)?0xedb88320:0);}return (value^0xffffffff)>>>0;}
function chunk(type,data){const b=Buffer.alloc(data.length+12);b.writeUInt32BE(data.length);b.write(type,4);b.set(data,8);b.writeUInt32BE(crc(b.subarray(4,-4)),b.length-4);return b;}
function carrier(){const h=Buffer.alloc(13);h.writeUInt32BE(1);h.writeUInt32BE(1,4);h[8]=8;h[9]=2;
  return new Blob([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',h),chunk('IDAT',deflateSync(Buffer.from([0,255,255,255]))),chunk('IEND',Buffer.alloc(0))],{type:'image/png'});
}
async function records(blob){const b=Buffer.from(await blob.arrayBuffer()),out=[];let p=8;
  assert.deepEqual([...b.subarray(0,8)],[137,80,78,71,13,10,26,10]);
  while(p<b.length){assert.ok(p+12<=b.length);const n=b.readUInt32BE(p);assert.ok(p+n+12<=b.length);
    assert.equal(b.readUInt32BE(p+n+8),crc(b.subarray(p+4,p+n+8)));
    out.push({type:b.toString('ascii',p+4,p+8),data:b.subarray(p+8,p+n+8)});p+=n+12;
  }
  assert.equal(out.at(-1).type,'IEND');return out;
}

test('PNG metadata carries the complete mixed-scale graph without rewriting pixel content or chemical fields',async()=>{
  const original=project(),before=structuredClone(original),base=carrier(),baseRecords=await records(base);
  for(const protonated of [false,true]){
    const candidate=projectWithProfile(original,profile(protonated));
    if(protonated){const n=candidate.graph.atoms.find(a=>a.id==='amine');n.charge=1;n.explicitHs=2;}
    const graphJson=snapshotCoordinates(candidate.graph);
    const output=await records(await stampPng(base,{version:'reference-preservation-test',graphJson}));
    assert.deepEqual(output.filter(c=>['IHDR','IDAT','IEND'].includes(c.type)),baseRecords);
    const text=output.filter(c=>c.type==='iTXt');assert.equal(text.length,1);
    const payload=text[0].data,zero=payload.indexOf(0);
    assert.equal(payload.toString('utf8',0,zero),COORDINATE_KEY);
    assert.deepEqual([...payload.subarray(zero,zero+5)],[0,0,0,0,0]);
    const record=JSON.parse(payload.toString('utf8',zero+5));
    assert.equal(record.graphJson,graphJson);assert.deepEqual(JSON.parse(record.graphJson),candidate.graph);
    assert.equal(record.graphSha256,createHash('sha256').update(graphJson).digest('hex'));
    assert.equal(record.dimension,2);assert.equal(record.origin,'drawing-2d');assert.equal(record.units,'drawing');
    assert.equal(record.schema,'nivram-png-coordinates/1');assert.equal(record.producer,'nivraM');
    assert.ok(!('analysis' in JSON.parse(record.graphJson)));assertSourceScaleDistinction(JSON.parse(record.graphJson));
  }
  assert.deepEqual(original,before);
});
