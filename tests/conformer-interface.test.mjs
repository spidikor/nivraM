/** Opt-in real chemistry/interface test using the explicitly selected interpreter. */
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {webcrypto} from 'node:crypto';
import {validateGeneratedResult} from '../public/modules/conformer.mjs';
import {projectSupplied} from '../public/modules/supplied-3d.mjs';
const python=process.env.NIVRAM_TEST_PYTHON;
assert(python,'Set NIVRAM_TEST_PYTHON to the approved scientific interpreter.');
const result=spawnSync(python,['-c',`import json
from tests.test_conformer import request
from nivram.conformer import generate
print(json.dumps([{'request':r,'result':generate(r)} for r in [request('CCO'),request('C[C@H](O)C(=O)O'),request('[13CH3][NH3+]'),request('c1ccccc1')]]))`],{encoding:'utf8',timeout:30000,maxBuffer:1048576});
assert.equal(result.status,0,result.stderr);
for(const pair of JSON.parse(result.stdout)){
  const output=validateGeneratedResult(pair.result,pair.request);
  assert.equal(output.provenance.format,'MOL V3000');
  const hash=Buffer.from(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(output.scene.sourceText))).toString('hex');
  assert.equal(hash,output.provenance.outputSha256);
  const points=projectSupplied(output.scene,{yaw:1,pitch:.5,zoom:1});
  assert.equal(points.length,output.scene.atoms.length);assert(points.every(a=>Number.isFinite(a.x)&&Number.isFinite(a.y)));
}
console.log('PASS four real RDKit responses through generated JS validator, view projection and exact MOL output hash. No browser/native packaging qualification.');
// Actual document-store consumer -> safe projection -> real Python -> viewer/export.
const {createDocumentStore}=await import('../public/modules/document.mjs');
const {mountSupplied3d}=await import('../public/modules/supplied-3d-viewer.mjs');
const specimen=JSON.parse(result.stdout)[1];
const imageImport={kind:'png-optical-reconstruction',sourceSha256:'a'.repeat(64),sourceName:'/private/person/drawing.png',sourceStorage:'/private/profile',warnings:['Optical recognition can misread stereochemistry.','Model score is uncalibrated.'],imageCorrespondence:'user-reviewed; not independently verified',recognition:{method:'MolScribe',modelSha256:'b'.repeat(64),inputSha256:'c'.repeat(64),confidence:.8,confidenceMeaning:'uncalibrated model score',coordinates:'preserved-from-recognized-molfile'},reviewedRegion:{kind:'crop',sourceSha256:'a'.repeat(64),pngSha256:'c'.repeat(64),sourceWidth:100,sourceHeight:100,width:80,height:80,rect:{x:10,y:10,width:80,height:80},scale:'one-source-pixel-per-image-pixel',metadataCorrespondence:'unverified optical recognition of this crop'},correction:{kind:'user-edited-graph',baselineGraph:JSON.parse(specimen.request.graphText),reviewedGraph:JSON.parse(specimen.request.graphText),imageCorrespondence:'user-reviewed; not independently verified'}};
const store=createDocumentStore({schema:'nivram/1',graph:JSON.parse(specimen.request.graphText),analysis:{imageImport}});
class Node {constructor(){this.handlers={};this.children=[];this.attrs={};this.disabled=true;this.classList={toggle(){}};this.files=[];}addEventListener(t,f){(this.handlers[t]??=[]).push(f);}removeEventListener(t,f){this.handlers[t]=this.handlers[t].filter(x=>x!==f);}async fire(t,e={}){for(const f of this.handlers[t]||[])await f(e);}append(...a){this.children.push(...a);}replaceChildren(...a){this.children=a;}setAttribute(k,v){this.attrs[k]=v;}querySelectorAll(){return [];}focus(){}showModal(){this.open=true;}close(){this.open=false;this.fire('close');}}
const nodes=Object.fromEntries(['dialog','open','close','file','scene','status','atoms','bonds','provenance','export','export-provenance','generate','cancel'].map(id=>['supplied-3d-'+id,new Node()]));
const dom={getElementById:id=>nodes[id],createElement:()=>new Node(),createElementNS:()=>new Node()};let sent,downloads=[];
const view=mountSupplied3d({dom,cryptoApi:webcrypto,getDocument:()=>({...store,ready:Promise.resolve()}),download:(...args)=>downloads.push(args),request:async(path,p)=>{assert.equal(path,'conformer');sent=p;const execution=spawnSync(python,['-c','import json,sys; from nivram.conformer import generate; print(json.dumps(generate(json.load(sys.stdin))))'],{input:JSON.stringify(p),encoding:'utf8',timeout:30000,maxBuffer:1048576});assert.equal(execution.status,0,execution.stderr);return JSON.parse(execution.stdout);}});
view.open();assert(await view.generate());assert.equal(view.getState().model.kind,'generated_coordinates');await nodes['supplied-3d-export-provenance'].fire('click');
const exported=JSON.parse(new TextDecoder().decode(downloads[0][0]));assert.equal(downloads[0][2],'generated-3d-provenance.json');assert.equal(exported.sourceContext.imageImport.sourceSha256,imageImport.sourceSha256);assert.deepEqual(exported.sourceContext.imageImport.warnings,imageImport.warnings);assert.equal(exported.sourceContext.imageImport.recognition.confidence,.8);assert.deepEqual(exported.sourceContext.imageImport.reviewedRegion.rect,imageImport.reviewedRegion.rect);assert.equal(exported.sourceContext.imageImport.correction.kind,'user-edited-graph');assert.match(exported.sourceContext.currentGraphRelationship,/does not verify the current graph/);
for(const word of ['sourceName','sourceStorage','baselineGraph','reviewedGraph','/private'])assert(!JSON.stringify(exported).includes(word));assert.equal(JSON.stringify(exported.sourceContext),sent.sourceContextText);assert.deepEqual(store.getSnapshot().document.analysis.imageImport,imageImport);view.destroy();
console.log('PASS actual document-store PNG provenance -> safe projection -> real RDKit -> viewer -> explicit provenance download; private fields excluded and original document unchanged.');
