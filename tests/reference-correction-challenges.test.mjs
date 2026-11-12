import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash,webcrypto} from 'node:crypto';
import {createDocumentStore,validateProject} from '../public/modules/document.mjs';
import {createImageReview} from '../public/modules/image-review.mjs';
import {createBond,deleteEntities,editAtom,editBond} from '../public/modules/edit-commands.mjs';

// Native parsing/recognition/rendering and visual review are controlled seams.
// The one-pixel carrier contains no molecule. These tests measure graph correction,
// transaction and provenance behavior, not OCR, image correspondence or R/S truth.
globalThis.crypto??=webcrypto;
const bank=JSON.parse(readFileSync(new URL('./fixtures/reference-correction-challenges.json',import.meta.url),'utf8'));
const carrier='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGP4DwQACfsD/fteaysAAAAASUVORK5CYII=';
const digest=value=>createHash('sha256').update(value).digest('hex');
const sourceHash=digest(Buffer.from(carrier,'base64'));
const modelHash='32821fa4ba8d17f9a92e374601438c5e757947dbf1141e9ad0ef51cccfa1c501';
const rect={x:0,y:0,width:1,height:1};
const clone=value=>structuredClone(value);

function neighbors(graph,id){return graph.bonds.filter(b=>b.begin===id||b.end===id).map(b=>b.begin===id?b.end:b.begin).sort();}
function atom(graph,id){const value=graph.atoms.find(a=>a.id===id);assert.ok(value,`Missing atom ${id}`);return value;}
function bond(graph,id){const value=graph.bonds.find(b=>b.id===id);assert.ok(value,`Missing bond ${id}`);return value;}
function oracle(id,g){
  // These explicit expectations do not call a chemical importer, application
  // editor operation, renderer, or fixture generator to derive the answer.
  if(id==='same-count-connectivity'){
    assert.equal(g.atoms.length,3);assert.equal(g.bonds.length,2);
    assert.deepEqual(g.atoms.map(a=>a.symbol).sort(),['C','C','O']);
    assert.deepEqual(neighbors(g,'o'),['c1','c2']);assert.deepEqual(neighbors(g,'c1'),['o']);
  }else if(id==='charge-hydrogen-counterion'){
    assert.equal(g.atoms.length,4);assert.equal(g.bonds.length,2);
    assert.equal(atom(g,'n').charge,1);assert.equal(atom(g,'n').explicitHs,3);assert.equal(atom(g,'n').noImplicit,true);
    assert.equal(atom(g,'cl').charge,-1);assert.deepEqual(neighbors(g,'cl'),[]);
    assert.deepEqual(neighbors(g,'n'),['c2']);assert.equal(atom(g,'c1').isotope,13);
    assert.equal(g.atoms.reduce((n,a)=>n+a.charge,0),0);
  }else if(id==='targeted-stereobond'){
    assert.equal(g.atoms.length,6);assert.equal(g.bonds.length,5);
    assert.deepEqual([bond(g,'ab').begin,bond(g,'ab').end,bond(g,'ab').direction],['a','b',2]);
    assert.equal(atom(g,'a').chiralTag,0);assert.equal(atom(g,'b').chiralTag,2);
    assert.deepEqual([bond(g,'bi').begin,bond(g,'bi').end,bond(g,'bi').direction],['b','i',2]);
    assert.deepEqual(neighbors(g,'a'),['b','cl','f']);assert.deepEqual(neighbors(g,'b'),['a','br','i']);
  }else if(id==='cyclic-carbonyl'){
    assert.equal(g.atoms.length,7);assert.equal(g.bonds.length,7);assert.equal(bond(g,'oxo').order,2);
    assert.deepEqual(neighbors(g,'n'),['c1','co','me']);assert.deepEqual(neighbors(g,'co'),['c3','n','o']);
    assert.deepEqual(neighbors(g,'c1'),['c2','n']);assert.deepEqual(neighbors(g,'c2'),['c1','c3']);
    assert.equal(g.bonds.filter(b=>b.order===2).length,1);
  }else if(id==='heteroatom-symbol'){
    assert.equal(g.atoms.length,3);assert.equal(g.bonds.length,2);assert.equal(atom(g,'x').symbol,'O');
    assert.equal(atom(g,'x').mapNum,7);assert.equal(atom(g,'c1').isotope,13);
    assert.deepEqual(neighbors(g,'x'),['c2']);assert.deepEqual(neighbors(g,'c2'),['c1','x']);
  }else throw new Error('Unknown challenge oracle');
}

function operation(graph,op){
  if(op.kind==='deleteBond')return deleteEntities(graph,{bondIds:[op.bondId]}).graph;
  if(op.kind==='createBond')return createBond(graph,{startAtomId:op.begin,endAtomId:op.end,kind:'1',makeId:()=>op.id}).graph;
  if(op.kind==='bondKind')return editBond(graph,{bondId:op.bondId,kind:op.value}).graph;
  if(op.kind==='element')return editAtom(graph,{atomId:op.atomId,symbol:op.symbol}).graph;
  if(op.kind==='atomProperties'){
    // Same detached-record operation as the existing atom-property UI. The
    // controller still owns validation, rendering, history and publication.
    const next=clone(graph);Object.assign(atom(next,op.atomId),{charge:op.charge,explicitHs:op.explicitHs,noImplicit:op.noImplicit});return next;
  }
  throw new Error('Unsupported challenge operation');
}

function fixture(spec){
  const store=createDocumentStore({schema:'nivram/1',graph:{atoms:[{id:'preceding',symbol:'He',x:31,y:-29}],bonds:[]},
    style:{bondWidth:3.1,labelSize:28,bondSpacing:.17,labelPadding:.08,color:false,hashBondStyle:'engine'},
    export:{width:1234,marginMode:'pixels',margin:7.25,transparentBackground:true},analysis:{preceding:'restore with main Undo'}});
  const before=store.getSnapshot(),mode=spec.mode,id=mode==='manual'?'manual-1':'optical-1';
  const selected=[id],calls={open:0,prepare:0,render:0,review:0},fault={};let lastRegion,sequence=0;
  const recognition={method:'MolScribe',modelSha256:modelHash,inputSha256:sourceHash,confidence:null,
    confidenceMeaning:'uncalibrated model score',coordinates:'preserved-from-recognized-molfile'};
  const suppliedStructure=mode==='manual'?{textSha256:digest(spec.suppliedText),coordinates:'from structure text; not traced from image'}:null;
  const inspection={schemaVersion:1,originalSha256:sourceHash,image:{width:1,height:1},engine:{name:'controlled test seam',version:'no native chemistry'},candidates:[],recognition:{status:'not_run'}};
  const component={id,status:'supported',graph:clone(spec.proposal),source:mode==='manual'?'user-supplied-structure':'optical-recognition',
    ...(mode==='manual'?{suppliedStructure}:{recognition})};
  const preview=()=>({graph:clone(spec.proposal),scene:{style:clone(before.document.style)},sourceSha256:sourceHash,
    selectedComponents:selected,warnings:['Controlled proposal; image correspondence is not established.']});
  function proposal(jobId,requestId,crop){
    lastRegion={schemaVersion:1,jobId,requestId,sourceSha256:sourceHash,sourceWidth:1,sourceHeight:1,
      mode:'crop',rect:clone(crop),width:1,height:1,resampling:'none',pngBase64:carrier,pngSha256:sourceHash,
      navigationOnly:false,reviewToken:`fresh_${spec.id}_crop`};
    return {status:mode==='manual'?'drafted':'recognized',jobId,requestId,selectedComponents:selected,
      inspection:{...clone(inspection),candidates:[clone(component)]},region:clone(lastRegion)};
  }
  const native={
    async inspect_image(jobId){return {status:'inspected',jobId,name:'synthetic-one-pixel-carrier.png',inspection:clone(inspection),
      reviewAvailable:true,regionPreviewAvailable:true,opticalRecognition:{status:'configured',message:'Controlled test response only.',modelSha256:modelHash}};},
    async preview_image_draft(jobId,requestId,crop,text){assert.equal(text,spec.suppliedText);return proposal(jobId,requestId,crop);},
    async recognize_image(jobId,requestId,crop){return proposal(jobId,requestId,crop);},
    async preview_image_components(_jobId,ids){assert.deepEqual(ids,selected);return preview();},
    async prepare_image_import(_jobId,ids,_style,token){
      calls.prepare++;assert.deepEqual(ids,selected);assert.equal(token,lastRegion.reviewToken);
      const {requestId,sourceSha256,sourceWidth,sourceHeight,rect,width,height,pngSha256}=lastRegion;
      const result={...preview(),provenance:{kind:mode==='manual'?'png-user-supplied-structure':'png-optical-reconstruction',
        sourceSha256,selectedComponents:selected,
        ...(mode==='manual'?{suppliedStructure:clone(suppliedStructure),suppliedGraph:clone(spec.proposal)}:{recognition:clone(recognition)}),
        reviewedRegion:{kind:'crop',requestId,sourceSha256,sourceWidth,sourceHeight,rect,width,height,pngSha256,
          scale:'one-source-pixel-per-image-pixel',metadataCorrespondence:mode==='manual'?'user-supplied structure; image correspondence not independently verified':'unverified optical recognition of this crop'}}};
      return fault.prepare?fault.prepare(result):result;
    },
    async cancel_image_region(){},async cancel_image_import(){}
  };
  const editor={getSnapshot:store.getSnapshot,subscribe:store.subscribe,async openProject(document,options){
    calls.open++;if(options.signal.aborted)return false;store.commit(document,{expectedRevision:options.expectedRevision});store.markUnsaved();return true;
  }};
  const review=createImageReview({editor,native,sessionToken:'local-test-session',newId:()=>`fresh_request_${++sequence}`,
    reviewSource:async()=>true,reviewRegion:async()=>{calls.review++;return true;},
    render:async(graph,style)=>{calls.render++;if(fault.render)return fault.render(graph,style);return {graph,scene:{style}};},
    prepare:(response,base)=>({project:validateProject({...base,graph:response.graph,style:response.scene.style}),scene:response.scene})});
  async function start(){await review.inspect();return mode==='manual'?review.startFromText(spec.suppliedText,rect):review.recognize(rect);}
  return {review,store,before,calls,fault,start};
}

test('fresh fixture targets have independent structural expectations and all proposals are deliberately distinguishable',()=>{
  assert.equal(bank.status,'exposed-development-only');assert.equal(bank.cases.length,5);
  assert.equal(new Set(bank.cases.map(c=>c.id)).size,5);
  for(const spec of bank.cases){
    oracle(spec.id,spec.target);assert.throws(()=>oracle(spec.id,spec.proposal),assert.AssertionError);
    for(const graph of [spec.proposal,spec.target])assert.deepEqual(validateProject({schema:'nivram/1',graph}).graph,graph);
  }
});

for(const spec of bank.cases)test(`${spec.id}: staged corrections, draft Undo/Redo and one accepted import preserve the declared graph`,async t=>{
  const pristine=clone(spec),f=fixture(spec);t.after(()=>f.review.dispose());
  const result=await f.start();assert.deepEqual(result.prepared.project.graph,spec.proposal);assert.deepEqual(f.store.getSnapshot(),f.before);
  await assert.rejects(f.review.accept(),/Confirm/);assert.equal(f.calls.open,0);
  const stages=[clone(spec.proposal)];let current=clone(spec.proposal);
  for(const op of spec.operations){
    const previous=current,untouched=clone(previous);current=operation(previous,op);assert.deepEqual(previous,untouched);
    const prepared=await f.review.correct(current);assert.deepEqual(prepared.project.graph,current);stages.push(clone(current));
    assert.deepEqual(f.store.getSnapshot(),f.before);
  }
  oracle(spec.id,current);assert.deepEqual(current,spec.target);
  for(let n=stages.length-2;n>=0;n--)assert.deepEqual((await f.review.undoCorrection()).project.graph,stages[n]);
  assert.deepEqual(f.review.correctionHistory,{canUndo:false,canRedo:true});
  for(let n=1;n<stages.length;n++)assert.deepEqual((await f.review.redoCorrection()).project.graph,stages[n]);
  assert.deepEqual(f.store.getSnapshot(),f.before);
  await f.review.accept({confirmed:true});assert.equal(f.calls.open,1);assert.equal(f.calls.prepare,1);assert.equal(f.calls.review,1);
  const accepted=f.store.getSnapshot().document,p=accepted.analysis.imageImport;
  oracle(spec.id,accepted.graph);assert.deepEqual(accepted.graph,spec.target);assert.deepEqual(accepted.style,f.before.document.style);assert.deepEqual(accepted.export,f.before.document.export);
  assert.deepEqual(p.correction.baselineGraph,spec.proposal);assert.deepEqual(p.correction.reviewedGraph,spec.target);
  assert.equal(p.correction.imageCorrespondence,'user-reviewed; not independently verified');assert.equal(p.sourceSha256,sourceHash);
  assert.equal(p.kind,spec.mode==='manual'?'png-user-supplied-structure':'png-optical-reconstruction');
  if(spec.mode==='manual'){assert.equal(p.recognition,undefined);assert.deepEqual(p.suppliedGraph,spec.proposal);assert.equal(p.suppliedStructure.textSha256,digest(spec.suppliedText));}
  else{assert.equal(p.recognition.confidence,null);assert.equal(p.recognition.inputSha256,sourceHash);}
  f.store.commit(f.store.historyTarget(),{mode:'undo'});assert.deepEqual(f.store.getSnapshot().document,f.before.document);assert.equal(f.store.historyState().canUndo,false);
  f.store.commit(f.store.historyTarget(true),{mode:'redo'});assert.deepEqual(f.store.getSnapshot().document,accepted);
  assert.deepEqual(spec,pristine);
});

test('a rejected complex correction leaves the prior draft and history usable',async t=>{
  const spec=bank.cases.find(c=>c.id==='cyclic-carbonyl'),f=fixture(spec);t.after(()=>f.review.dispose());await f.start();
  const malformed=clone(spec.target);malformed.bonds[0].end='missing';
  await assert.rejects(f.review.correct(malformed),/invalid atom reference/);assert.equal(f.calls.render,0);
  f.fault.render=async()=>{throw new Error('Controlled renderer rejection');};
  await assert.rejects(f.review.correct(spec.target),/Controlled renderer rejection/);
  assert.equal(f.review.status,'ready');assert.deepEqual(f.review.correctionHistory,{canUndo:false,canRedo:false});assert.deepEqual(f.store.getSnapshot(),f.before);
  delete f.fault.render;await f.review.correct(spec.target);assert.deepEqual((await f.review.undoCorrection()).project.graph,spec.proposal);
});

test('counterion loss in the final native receipt cannot replace a reviewed multi-component baseline',async t=>{
  const spec=bank.cases.find(c=>c.id==='charge-hydrogen-counterion'),f=fixture(spec);t.after(()=>f.review.dispose());await f.start();await f.review.correct(spec.target);
  f.fault.prepare=result=>{result.graph.atoms=result.graph.atoms.filter(a=>a.id!=='cl');return result;};
  await assert.rejects(f.review.accept({confirmed:true}),/changed the selected components/);
  assert.equal(f.calls.open,0);assert.deepEqual(f.store.getSnapshot(),f.before);assert.equal(f.review.status,'inspected');
});
