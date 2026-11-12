/** Real isolated chemistry + fake DOM integration; not browser qualification. */
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {webcrypto} from 'node:crypto';
import {deflateSync} from 'node:zlib';
import {hydrogenDisplay} from '../public/modules/three-d-display.mjs';
import {mountSupplied3d} from '../public/modules/supplied-3d-viewer.mjs';
import {createThreeDRecord} from '../public/modules/three-d-record.mjs';
import {stampPng,crc32,extractThreeDRecord} from '../public/modules/png-metadata.mjs';
import {exportThreeDPng} from '../public/modules/three-d-png.mjs';
const python=process.env.NIVRAM_TEST_PYTHON;
assert(python,'Set NIVRAM_TEST_PYTHON to the approved scientific interpreter.');
function py(code,input){const r=spawnSync(python,['-B','-s','-c',code],{input:JSON.stringify(input),encoding:'utf8',timeout:30000,maxBuffer:2097152});assert.equal(r.status,0,r.stderr);return JSON.parse(r.stdout);}
const generated=py(`import json
from tests.test_conformer import request
from nivram.conformer import run_request
from nivram.worker_processes import WorkerSupervisor,CancellationToken
s=WorkerSupervisor(max_workers=1)
try:
 r=request('C[C@H](O)C(=O)O'); print(json.dumps({'request':r,'result':run_request(r,s,CancellationToken())}))
finally: s.shutdown()`);
function chunk(type,payload){const out=Buffer.alloc(payload.length+12);out.writeUInt32BE(payload.length);out.write(type,4);out.set(payload,8);out.writeUInt32BE(crc32(out.subarray(4,-4)),out.length-4);return out;}
const header=Buffer.alloc(13);header.writeUInt32BE(1);header.writeUInt32BE(1,4);header[8]=8;header[9]=2;
const raster=()=>new Blob([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',header),chunk('IDAT',deflateSync(Buffer.from([0,255,255,255]))),chunk('IEND',Buffer.alloc(0))],{type:'image/png'});
const record=await createThreeDRecord({model:generated.result.scene,provenance:generated.result.provenance,sourceContextText:generated.request.sourceContextText},'historical-1');
const png=new Uint8Array(await (await stampPng(raster(),{coordinateRecord:record,version:'historical-1'})).arrayBuffer());
class Node {constructor(){this.handlers={};this.children=[];this.attrs={};this.disabled=true;this.classList={toggle(){}};this.files=[];}addEventListener(t,f){(this.handlers[t]??=[]).push(f);}removeEventListener(t,f){this.handlers[t]=this.handlers[t].filter(x=>x!==f);}async fire(t,e={}){for(const f of this.handlers[t]||[])await f(e);}append(...a){this.children.push(...a);}replaceChildren(...a){this.children=a;}setAttribute(k,v){this.attrs[k]=v;}querySelectorAll(){return [];}focus(){}showModal(){this.open=true;}close(){this.open=false;this.fire('close');}}
const nodes=Object.fromEntries(['dialog','open','close','file','scene','status','atoms','bonds','provenance','export','export-provenance','export-png','png-file','generate','cancel'].map(id=>['supplied-3d-'+id,new Node()]));
const dom={getElementById:id=>nodes[id],createElement:()=>new Node(),createElementNS:()=>new Node()};
const defer=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
let downloads=[],delay=null,entered=null,forge=false,cancels=0,recoveries=0;
const view=mountSupplied3d({dom,cryptoApi:webcrypto,download:(...args)=>downloads.push(args),sceneSnapshot:()=>'<svg/>',pngExport:s=>exportThreeDPng(s,{rasterize:async()=>raster(),version:async()=> 'current-2'}),request:async(path,p)=>{
 if(path==='coordinate-recovery/cancel'){cancels++;return {cancelled:true};}
 assert.equal(path,'coordinate-recovery');recoveries++;
 const response=py(`import json,sys
from nivram.coordinate_recovery import run_request
from nivram.worker_processes import WorkerSupervisor,CancellationToken
s=WorkerSupervisor(max_workers=1)
try: print(json.dumps(run_request(json.load(sys.stdin),s,CancellationToken())))
finally: s.shutdown()`,p);
 if(forge)response.validation.generationClaimsVerified=true;
 entered?.resolve();if(delay)await delay.promise;
 return response;
}});
view.open();assert(await view.loadPng(png));
const accepted=view.getState().model;
assert.equal(accepted.kind,'recovered_coordinates');assert.equal(accepted.sourceText,generated.result.scene.sourceText);assert.deepEqual(accepted.atoms,generated.result.scene.atoms);assert.deepEqual(accepted.bonds,generated.result.scene.bonds);
assert.equal(view.getState().provenance.generated,false);assert.equal(view.getState().provenance.generationClaimsVerified,false);assert.equal(view.getState().provenance.validation.generationClaimsVerified,false);assert.match(nodes['supplied-3d-scene'].attrs['aria-label'],/^Recovered/);
await nodes['supplied-3d-export'].fire('click');assert.equal(new TextDecoder().decode(downloads.at(-1)[0]),generated.result.scene.sourceText);assert.equal(downloads.at(-1)[2],'recovered-3d.mol');
assert(await view.exportPng());const reexport=await extractThreeDRecord(downloads.at(-1)[0]);assert.equal(reexport.version,'current-2');assert.equal(reexport.provenanceJson,record.provenanceJson);assert.equal(reexport.molText,record.molText);assert.equal(reexport.recordedOrigin,'generated_coordinates');
forge=true;assert.equal(await view.loadPng(png),false);assert.equal(view.getState().model,accepted);forge=false;
// A completed native parse delivered after close must not replace the last view.
entered=defer();delay=defer();let pending=view.loadPng(png);await entered.promise;view.close();delay.resolve();assert.equal(await pending,false);assert.equal(view.getState().model,accepted);assert(cancels>0);
// A newer load owns the view even if an older native response arrives afterward.
view.open();entered=defer();delay=defer();pending=view.loadPng(png);await entered.promise;const oldDelay=delay;delay=null;entered=null;assert(await view.loadPng(png));const latest=view.getState().model;oldDelay.resolve();assert.equal(await pending,false);assert.equal(view.getState().model,latest);
// Explicit cancel fences a late native response as well.
entered=defer();delay=defer();pending=view.loadPng(png);await entered.promise;await nodes['supplied-3d-cancel'].fire('click');delay.resolve();assert.equal(await pending,false);assert.equal(view.getState().model,latest);
view.destroy();assert.equal(recoveries,6);console.log('PASS real isolated chiral V3000 generation → native PNG → parser-only disposable recovery → fake-DOM viewer → exact MOL/PNG re-export; historical trust, forged validation rejection, close/new-load/cancel fencing. No browser or Windows qualification.');
// Plain generated MOL reopened as supplied must round-trip without native recovery
// or retaining the previous generated source binding/provenance.
const plain=py("from tests.test_conformer import request\nfrom nivram.conformer import generate\nimport json\nprint(json.dumps(generate(request('[13CH3:7][NH3+:9]'))))",null).scene.sourceText;
const suppliedView=mountSupplied3d({dom,cryptoApi:webcrypto,download:(...args)=>downloads.push(args),sceneSnapshot:()=>'<svg/>',pngExport:s=>exportThreeDPng(s,{rasterize:async()=>raster(),version:async()=> 'current-2'}),request:async()=>{throw Error('Supplied import must not call native recovery or generation');}});
suppliedView.open();assert(await suppliedView.loadBytes(new TextEncoder().encode(plain)));
assert.equal(suppliedView.getState().model.atoms[0].isotope,13);assert.equal(suppliedView.getState().model.atoms[0].atomMap,7);assert.equal(suppliedView.getState().model.atoms[1].charge,1);assert.equal(suppliedView.getState().model.atoms[1].atomMap,9);assert.equal(suppliedView.getState().provenance.generated,false);assert.equal(suppliedView.getState().model.kind,'supplied_coordinates');
assert(await suppliedView.exportPng());const suppliedPng=downloads.at(-1)[0];const suppliedRecord=await extractThreeDRecord(suppliedPng);
assert.equal(suppliedRecord.schema,'nivram-png-coordinates/3');assert.equal(suppliedRecord.sourceAtomIds,null);assert.equal(suppliedRecord.sourceContextText,null);
assert(await suppliedView.loadPng(new Uint8Array(await suppliedPng.arrayBuffer())));
assert.equal(suppliedView.getState().model.atoms[0].isotope,13);assert.equal(suppliedView.getState().model.atoms[1].charge,1);assert.equal(suppliedView.getState().model.atoms[1].atomMap,9);assert.equal(suppliedView.getState().model.sourceText,plain);assert.equal(suppliedView.getState().model.kind,'recovered_coordinates');assert.equal(suppliedView.getState().provenance.generationClaimsVerified,false);
assert(await suppliedView.exportPng());assert.equal((await extractThreeDRecord(downloads.at(-1)[0])).molText,plain);
suppliedView.destroy();console.log('PASS charged/isotopic/mapped V3000 MOL → supplied view → v3 PNG → detached recovered view → exact PNG re-export without worker or generation claims.');

// Real source H maps survive generation and parser-only PNG recovery even though
// historical worker scene schemas omit atomMap/declarations. Display annotations
// come from exact source bytes, not from treating omitted fields as zero.
for(const mapNum of [7,0]){
 const fixture=py(`import json,hashlib
from tests.test_conformer import request
from nivram.conformer import generate
r=request('[2H]O')
g=json.loads(r['graphText']);g['atoms'][0]['isotope']=0;g['atoms'][0]['mapNum']=${mapNum}
r['graphText']=json.dumps(g,separators=(',',':'));r['sourceHash']=hashlib.sha256(r['graphText'].encode()).hexdigest()
print(json.dumps({'request':r,'result':generate(r)}))`,null);
 const before=structuredClone(fixture.result.scene),expected=mapNum===0?1:0;
 assert.equal(fixture.result.scene.atoms[0].atomMap,undefined);
 assert.equal(hydrogenDisplay(fixture.result.scene,'implicit').hidden.size,expected);
 assert.deepEqual(fixture.result.scene,before);
 const graph=JSON.parse(fixture.request.graphText),doc={ready:Promise.resolve(),getSnapshot:()=>({revision:7,document:{graph}}),subscribe:()=>()=>{}};
 const generatedView=mountSupplied3d({dom,cryptoApi:webcrypto,getDocument:()=>doc,request:async(path,p)=>path.endsWith('/cancel')?{cancelled:true}:py('import json,sys\nfrom nivram.conformer import generate\nprint(json.dumps(generate(json.load(sys.stdin))))',p)});
 generatedView.open();assert(await generatedView.generate(),nodes['supplied-3d-status'].textContent);
 assert.equal(hydrogenDisplay(generatedView.getState().model,'implicit').hidden.size,expected);
 assert.equal(nodes['supplied-3d-scene'].children.some(n=>n.attrs['data-atom']==='1'),mapNum!==0);
 generatedView.destroy();
 const hRecord=await createThreeDRecord({model:fixture.result.scene,provenance:fixture.result.provenance,sourceContextText:fixture.request.sourceContextText},'mapped-h-test');
 const hPng=new Uint8Array(await (await stampPng(raster(),{coordinateRecord:hRecord,version:'mapped-h-test'})).arrayBuffer());
 const hView=mountSupplied3d({dom,cryptoApi:webcrypto,request:async(path,p)=>{
   if(path.endsWith('/cancel'))return {cancelled:true};
   assert.equal(path,'coordinate-recovery');
   return py(`import json,sys
from nivram.coordinate_recovery import run_request
from nivram.worker_processes import WorkerSupervisor,CancellationToken
s=WorkerSupervisor(max_workers=1)
try: print(json.dumps(run_request(json.load(sys.stdin),s,CancellationToken())))
finally: s.shutdown()`,p);
 }});
 hView.open();assert(await hView.loadPng(hPng));const recovered=hView.getState().model;
 assert.equal(recovered.atoms[0].atomMap,undefined);assert.equal(recovered.sourceText,before.sourceText);
 assert.equal(hydrogenDisplay(recovered,'implicit').hidden.size,expected);
 assert.equal(nodes['supplied-3d-scene'].children.some(n=>n.attrs['data-atom']==='1'),mapNum!==0);
 assert.deepEqual(recovered.atoms,before.atoms);assert.deepEqual(recovered.bonds,before.bonds);
 hView.destroy();
}
console.log('PASS real generated and PNG-recovered mapped H retained; ordinary H condensed from matching source annotations; accepted MOL/scene unchanged.');
