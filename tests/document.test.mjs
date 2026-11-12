import test from 'node:test';
import assert from 'node:assert/strict';
import {createDocumentStore,validateProject} from '../public/modules/document.mjs';
import {createJobController} from '../public/modules/jobs.mjs';

const empty=()=>({schema:'nivram/1',version:'0.1.0-alpha.1',graph:{atoms:[],bonds:[]}});
const single=()=>({...empty(),graph:{atoms:[{id:'a0',symbol:'C',x:0,y:0,charge:1,isotope:13,explicitHs:3,noImplicit:true,aromatic:false,chiralTag:0,radicals:1,mapNum:7}],bonds:[]}});
const changedExport=(snapshot,width)=>({...snapshot.document,export:{...snapshot.document.export,width}});

test('alpha projects retain all supported graph values and receive only documented defaults',()=>{
  const input=single(),before=structuredClone(input),result=validateProject(input);
  assert.deepEqual(input,before);assert.deepEqual(result.graph,input.graph);
  assert.deepEqual(result.export,{width:2000,marginMode:'percent',margin:2,transparentBackground:false});
  assert.equal(result.style.bondWidth,4.2);assert.equal(result.schema,'nivram/1');
});
test('unsupported project versions and unknown chemistry are rejected instead of discarded',()=>{
  assert.throws(()=>validateProject({...single(),schema:'nivram/2'}),/version.*not supported/);
  const project=single();project.graph.atoms[0].unsupportedStereo='unknown';
  assert.throws(()=>validateProject(project),/Unsupported atom field/);
  assert.equal(project.graph.atoms[0].unsupportedStereo,'unknown');
});
test('label clearance defaults preserve old projects and validate finite bounded fractions without rounding',()=>{
  assert.equal(validateProject(single()).style.labelPadding,0.04);
  for(const value of [0,0.145,0.2])assert.equal(validateProject({...single(),style:{labelPadding:value}}).style.labelPadding,value);
  for(const value of [-0.01,0.201,NaN,Infinity,'0.1',true])assert.throws(()=>validateProject({...single(),style:{labelPadding:value}}),/Extra label clearance must be a number from 0% to 20%/);
});
test('invalid references and nonfinite document values are rejected',()=>{
  const project=single();project.graph.bonds=[{id:'b0',begin:'a0',end:'missing',order:1}];
  assert.throws(()=>validateProject(project),/invalid atom reference/);
  project.graph.bonds=[];project.graph.atoms[0].x=Infinity;
  assert.throws(()=>validateProject(project),/finite/);
});
test('a failed view application preserves document, revision, dirty state, undo and redo',()=>{
  const store=createDocumentStore(empty());store.commit(single());
  const before=store.getSnapshot(),history=store.historyState();
  assert.throws(()=>store.commit(changedExport(before,9999),{apply:()=>{throw new Error('view failure');}}),/view failure/);
  assert.deepEqual(store.getSnapshot(),before);assert.deepEqual(store.historyState(),history);
  store.commit(store.historyTarget(),{mode:'undo'});assert.equal(store.getSnapshot().document.graph.atoms.length,0);
});
test('dirty state follows the last durable document, including undo and redo',()=>{
  const store=createDocumentStore(empty());store.commit(single());
  const saved=store.getSnapshot();assert.equal(saved.dirty,true);assert.equal(store.markSaved(saved.revision),true);
  store.commit(changedExport(store.getSnapshot(),1000));assert.equal(store.getSnapshot().dirty,true);
  store.commit(store.historyTarget(),{mode:'undo'});assert.equal(store.getSnapshot().dirty,false);
  store.commit(store.historyTarget(true),{mode:'redo'});assert.equal(store.getSnapshot().dirty,true);
});
test('a save receipt for an older revision cannot mark later edits clean',()=>{
  const store=createDocumentStore(empty());store.commit(single());const saving=store.getSnapshot();
  store.commit(changedExport(saving,1000));
  assert.equal(store.markSaved(saving.revision),false);assert.equal(store.getSnapshot().dirty,true);
  assert.equal(store.markSaved(saving.revision,saving.document),true);assert.equal(store.getSnapshot().dirty,true);
  store.commit(store.historyTarget(),{mode:'undo'});assert.equal(store.getSnapshot().dirty,false);
  assert.equal(store.markSaved(saving.revision,empty()),false);
});
test('opening recovery as unsaved stays dirty even when its content equals a prior saved document',()=>{
  const store=createDocumentStore(single()),before=store.getSnapshot();assert.equal(before.dirty,false);
  store.markUnsaved();assert.equal(store.getSnapshot().dirty,true);
  assert.equal(store.markSaved(before.revision,before.document),false);
  const current=store.getSnapshot();assert.equal(store.markSaved(current.revision,current.document),true);assert.equal(store.getSnapshot().dirty,false);
});
test('snapshots and observers cannot mutate or roll back a committed document',()=>{
  const store=createDocumentStore(empty());store.subscribe(()=>{throw new Error('observer failure');});
  const input=single();store.commit(input);input.graph.atoms[0].symbol='N';
  const snapshot=store.getSnapshot();snapshot.document.graph.atoms[0].symbol='O';
  assert.equal(store.getSnapshot().document.graph.atoms[0].symbol,'C');assert.equal(store.getSnapshot().revision,1);
});
test('stale commits are rejected even after undo restores the same molecular content',()=>{
  const store=createDocumentStore(empty());const revision=store.getSnapshot().revision;
  store.commit(single());store.commit(store.historyTarget(),{mode:'undo'});
  assert.equal(store.getSnapshot().document.graph.atoms.length,0);
  assert.throws(()=>store.commit(single(),{expectedRevision:revision}),/document changed/);
});
test('job inputs are immutable and results are invalidated by a later revision',async()=>{
  let revision=0;const jobs=createJobController(()=>revision),input=single();
  const job=jobs.begin('analysis',{input});input.graph.atoms[0].symbol='N';
  assert.equal(job.input.graph.atoms[0].symbol,'C');assert.throws(()=>{job.input.graph.atoms[0].symbol='O';},TypeError);
  revision++;assert.throws(()=>job.assertCurrent(),/document changed/);job.finish();
});
test('superseded and cancelled jobs reject late responses without cancelling their replacements',async()=>{
  const jobs=createJobController(()=>0);let resolveOld;
  const old=jobs.begin('analysis'),pending=old.wait(new Promise(resolve=>{resolveOld=resolve;}));
  const replacement=jobs.begin('analysis');await assert.rejects(pending,/newer operation/);
  resolveOld('late');old.finish();replacement.assertCurrent();
  const waiting=replacement.wait(new Promise(()=>{}));jobs.cancelAll();await assert.rejects(waiting,/cancelled/);
  assert.throws(()=>replacement.assertCurrent(),/cancelled/);
});
test('a timed-out uninterruptible operation cannot later apply its result',async()=>{
  const jobs=createJobController(()=>0,{timeoutMs:20});let resolve;
  const job=jobs.begin('file'),pending=job.wait(new Promise(done=>{resolve=done;}));
  await assert.rejects(pending,/timed out/);resolve('late file');assert.throws(()=>job.assertCurrent(),/timed out/);job.finish();
});
