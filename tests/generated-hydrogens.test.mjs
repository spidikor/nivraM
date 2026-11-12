import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {webcrypto} from 'node:crypto';
import {deflateSync} from 'node:zlib';
import {generatedDisplayModel} from '../public/modules/generated-hydrogens.mjs';
import {hydrogenDisplay,projectDisplay} from '../public/modules/three-d-display.mjs';
import {mountSupplied3d} from '../public/modules/supplied-3d-viewer.mjs';
import {createThreeDRecord} from '../public/modules/three-d-record.mjs';
import {stampPng,crc32,extractThreeDRecord} from '../public/modules/png-metadata.mjs';
import {validateRecoveryResult,sha256} from '../public/modules/three-d-png.mjs';
const python=process.env.NIVRAM_TEST_PYTHON;assert(python);
function py(code,input){const r=spawnSync(python,['-B','-s','-c',code],{input:JSON.stringify(input),encoding:'utf8',timeout:30000,maxBuffer:2097152});assert.equal(r.status,0,r.stderr);return JSON.parse(r.stdout);}
function chunk(type,payload){const b=Buffer.alloc(payload.length+12);b.writeUInt32BE(payload.length);b.write(type,4);b.set(payload,8);b.writeUInt32BE(crc32(b.subarray(4,-4)),b.length-4);return b;}
const h=Buffer.alloc(13);h.writeUInt32BE(1);h.writeUInt32BE(1,4);h[8]=8;h[9]=2;
const raster=()=>new Blob([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',h),chunk('IDAT',deflateSync(Buffer.alloc(4))),chunk('IEND',Buffer.alloc(0))],{type:'image/png'});
class Node {constructor(){this.handlers={};this.children=[];this.attrs={};this.classList={toggle(){}};this.files=[];this.checked=false;}addEventListener(t,f){(this.handlers[t]??=[]).push(f);}removeEventListener(t,f){this.handlers[t]=this.handlers[t].filter(x=>x!==f);}async fire(t,e={}){for(const f of this.handlers[t]||[])await f(e);}append(...a){this.children.push(...a);}replaceChildren(...a){this.children=a;}setAttribute(k,v){this.attrs[k]=v;}querySelectorAll(){return [];}focus(){}showModal(){this.open=true;}close(){this.open=false;this.fire('close');}}
for(const mode of ['ordinary','mapped','isotope','ethanol','zero']){
 const fixture=py(`import json,hashlib
from tests.test_conformer import request
from nivram.conformer import generate
mode=${JSON.stringify(mode)}
r=request('CCO' if mode=='ethanol' else 'FC(F)(F)F' if mode=='zero' else '[2H]O')
if mode in ['ordinary','mapped']:
 g=json.loads(r['graphText']);g['atoms'][0]['isotope']=0;g['atoms'][0]['mapNum']=7 if mode=='mapped' else 0
 r['graphText']=json.dumps(g,separators=(',',':'));r['sourceHash']=hashlib.sha256(r['graphText'].encode()).hexdigest()
print(json.dumps({'request':r,'result':generate(r)}))`,null);
 const scene=fixture.result.scene,before=structuredClone(scene),display=generatedDisplayModel(scene),count=scene.generatedHydrogens.hydrogens.length,ordinary=mode==='ordinary'?1:0;
 assert.equal(display.atoms.length,scene.atoms.length+count);assert.equal(display.bonds.length,scene.bonds.length+count);
 assert.deepEqual(display.atoms.slice(0,scene.atoms.length),scene.atoms);
 for(const a of display.atoms.slice(scene.atoms.length)){assert(!Object.hasOwn(a,'sourceId'));assert.match(a.generatedId,/^generated-h\d+$/);assert.equal(display.bonds[a.id-scene.atoms.length-1+scene.bonds.length].b,a.id);}
 const implicit=hydrogenDisplay(display,'implicit');assert.equal(implicit.hidden.size,count+ordinary);assert.equal([...implicit.counts.values()].reduce((a,b)=>a+b,0),count+ordinary);assert.equal(hydrogenDisplay(display,'explicit').hidden.size,0);
 for(const space of [false,true])assert(projectDisplay(display,{yaw:.2,pitch:.3,zoom:1},space).every(a=>['x','y','z','r'].every(k=>Number.isFinite(a[k]))));
 assert.deepEqual(scene,before);
 const record=await createThreeDRecord({model:scene,provenance:fixture.result.provenance,sourceContextText:'null'},'test');assert.equal(record.schema,'nivram-png-coordinates/6');assert.equal(record.sourceAtomIds.length,scene.atoms.length);
 const blob=await stampPng(raster(),{coordinateRecord:record,version:'test'}),extracted=await extractThreeDRecord(blob);assert.equal(extracted.generatedHydrogensJson,JSON.stringify(scene.generatedHydrogens));
 const nodes=Object.fromEntries(['dialog','open','close','file','scene','status','atoms','bonds','provenance','export','export-provenance','export-png','png-file','generate','cancel','style','hydrogens','rotate','display-note'].map(id=>['supplied-3d-'+id,new Node()]));const n=k=>nodes['supplied-3d-'+k];
 const dom={getElementById:id=>nodes[id],createElement:()=>new Node(),createElementNS:()=>new Node()};let response,payload;
 const view=mountSupplied3d({dom,cryptoApi:webcrypto,request:async(path,p)=>{assert.equal(path,'coordinate-recovery');payload=p;response=py('import json,sys\nfrom nivram.coordinate_recovery import recover\nprint(json.dumps(recover(json.load(sys.stdin))))',p);return response;}});
 view.open();assert(await view.loadPng(new Uint8Array(await blob.arrayBuffer())),n('status').textContent);assert.equal(n('hydrogens').checked,false);
 const circles=()=>n('scene').children.filter(x=>x.attrs['data-atom']);assert.equal(circles().length,display.atoms.length-count-ordinary);
 n('hydrogens').checked=true;await n('hydrogens').fire('change');assert.equal(circles().length,display.atoms.length);
 n('style').value='space-filling';await n('style').fire('change');assert.equal(n('scene').children.length,display.atoms.length);
 n('hydrogens').checked=false;await n('hydrogens').fire('change');assert.equal(n('scene').children.length,display.atoms.length-count-ordinary);
 assert.deepEqual(view.getState().model.atoms,scene.atoms);assert.deepEqual(view.getState().model.generatedHydrogens,scene.generatedHydrogens);assert.equal(view.getState().provenance.validation.hydrogenGeometryAuthenticated,false);
 const args={record,recordSha256:await sha256(payload.recordText,webcrypto),jobId:payload.jobId,provenance:fixture.result.provenance};
 assert(validateRecoveryResult(response,args));
 for(const field of ['generatedHydrogensSha256','generatedHydrogenTopologyConsistent','hydrogenGeometryAuthenticated']){const bad=structuredClone(response);bad.validation[field]=field==='generatedHydrogensSha256'?'0'.repeat(64):!bad.validation[field];assert.throws(()=>validateRecoveryResult(bad,args));}
 const bad=structuredClone(response);bad.scene.generatedHydrogens.canonicalMolSha256='0'.repeat(64);assert.throws(()=>validateRecoveryResult(bad,args));
 view.destroy();
}
console.log('PASS generated H exact canonical/display separation, ordinary+added H counts, mapped/isotopic retention, zero-added H, schema6 PNG and fresh native recovery, explicit checkbox default/toggles and both styles in controlled DOM; forged response rejection. No browser qualification.');
