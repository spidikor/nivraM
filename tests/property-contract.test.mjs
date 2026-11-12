import test from 'node:test';import assert from 'node:assert/strict';
import {propertyEnvelope,propertyReport} from './property-response-fixtures.mjs';
import {validatePropertyEnvelope,validateProperties} from '../public/modules/property-contract.mjs';
import {requestAnalysis} from '../public/modules/analysis.mjs';
import {validateComparison} from '../public/modules/comparison.mjs';
const request=()=>({requestId:'a'.repeat(32),documentRevision:7,graph:{atoms:[{id:'a',symbol:'C',x:0,y:0}],bonds:[]}});
const task=()=>({revision:7,input:{graph:request().graph},signal:new AbortController().signal,assertCurrent(){}});
test('complete report, exact graph, and identity bind actual property client',async()=>{
  const job=task(),r=await requestAnalysis('properties',job,{token:'test',fetcher:async(_u,o)=>({ok:true,json:async()=>propertyEnvelope(JSON.parse(o.body),'methane')})});assert.equal(r.formula,'CH4');
  const mutations=[e=>delete e.properties.molecularWeight,e=>e.properties.exactMass.unit='g/mol',e=>e.properties.logP.value=true,e=>e.properties.logP.status='not_applicable',e=>e.properties.inputIdentity.graphHash='0'.repeat(64),e=>e.properties.inputIdentity.structureHash='0'.repeat(64),e=>e.sourceGraph.atoms[0].x=1,e=>e.sourceGraph.atoms[0].charge=0,e=>e.requestId='b'.repeat(32),e=>e.properties.fragmentCount=2,e=>e.properties.formalCharge=true,e=>e.properties.warnings=['invented'],e=>e.properties.extra=1];
  for(const mutate of mutations)await assert.rejects(requestAnalysis('properties',task(),{token:'test',fetcher:async(_u,o)=>{const e=propertyEnvelope(JSON.parse(o.body),'methane');mutate(e);return {ok:true,json:async()=>e};}}));
});
test('JSON object order independent; arrays and omitted defaults significant',()=>{
  const req=request(),e=propertyEnvelope(req,'methane');e.sourceGraph={bonds:[],atoms:[{y:0,x:0,symbol:'C',id:'a'}]};assert.equal(validatePropertyEnvelope(e,req).formula,'CH4');
  e.sourceGraph.atoms[0].charge=0;assert.throws(()=>validatePropertyEnvelope(e,req));
});
test('empty, negative LogP, paired missing mass and coherent fabrication distinctions',()=>{
  const empty=propertyReport('empty');assert.equal(validateProperties(empty,{atoms:[],bonds:[]}),empty);
  const r=propertyReport('methane');r.logP.value=-1;validateProperties(r,request().graph);
  for(const key of ['molecularWeight','exactMass','logP'])r[key].value=999;validateProperties(r,request().graph);
  const missing=propertyReport('missing'),g=request().graph;g.atoms[0].isotope=400;validateProperties(missing,g);
  missing.exactMass.value=400;assert.throws(()=>validateProperties(missing,g));
});
test('late property reply still calls current-task gate before validation',async()=>{
  const job=task();let finish;const p=requestAnalysis('properties',job,{token:'test',fetcher:(_u,o)=>new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>propertyEnvelope(JSON.parse(o.body),'methane')});})});
  job.assertCurrent=()=>{throw new Error('stale');};finish();await assert.rejects(p,/stale/);
});

test('explicit null graph annotations are not default zero',()=>{
  for(const key of ['charge','radicals']){const req=request();req.graph.atoms[0][key]=null;assert.throws(()=>validatePropertyEnvelope(propertyEnvelope(req,'methane'),req));}
});

test('a fabricated radical warning cannot excuse unavailable LogP',()=>{
  const r=propertyReport('methane');r.logP.value=null;r.logP.status='not_applicable';r.logP.warnings=['Radical LogP is outside the evaluated application domain.'];r.warnings=[...r.logP.warnings];assert.throws(()=>validateProperties(r,request().graph));
});
test('native charge separation on a neutral nitro source remains accepted',()=>{
  const graph={atoms:['C','N','O','O'].map((symbol,i)=>({id:String(i),symbol,x:i,y:0})),bonds:[{id:'b0',begin:'0',end:'1',order:1},{id:'b1',begin:'1',end:'2',order:2},{id:'b2',begin:'1',end:'3',order:2}]};
  const before=JSON.stringify(graph),r=propertyReport('nitro');validateProperties(r,graph);assert.equal(r.smiles,'C[N+](=O)[O-]');assert.equal(r.logP.status,'calculated_with_warning');assert.equal(JSON.stringify(graph),before);
});
