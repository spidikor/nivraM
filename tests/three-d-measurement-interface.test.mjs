/** Real isolated RDKit plus controlled DOM/PNG encoding; not browser or raster qualification. */
import assert from 'node:assert/strict';
import test from 'node:test';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {webcrypto} from 'node:crypto';
import {deflateSync} from 'node:zlib';
import {createDocumentStore} from '../public/modules/document.mjs';
import {mountSupplied3d} from '../public/modules/supplied-3d-viewer.mjs';
import {exportThreeDPng} from '../public/modules/three-d-png.mjs';
import {crc32,extractThreeDRecord} from '../public/modules/png-metadata.mjs';

const python=process.env.NIVRAM_TEST_PYTHON;
assert(python,'Set NIVRAM_TEST_PYTHON to the approved scientific interpreter.');
const helper=fileURLToPath(new URL('./three-d-measurement-interface-helper.py',import.meta.url));
function native(data){
  const result=spawnSync(python,['-B','-s',helper],{input:JSON.stringify(data),encoding:'utf8',timeout:30000,maxBuffer:2097152});
  assert.equal(result.status,0,result.stderr);return JSON.parse(result.stdout);
}
class Node {
  constructor(){this.handlers={};this.children=[];this.attrs={};this.classList={toggle(){}};this.files=[];this.checked=false;this.value='';}
  addEventListener(type,fn){(this.handlers[type]??=[]).push(fn);}
  removeEventListener(type,fn){this.handlers[type]=(this.handlers[type]??[]).filter(value=>value!==fn);}
  async fire(type,event={}){for(const fn of this.handlers[type]??[])await fn(event);}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(key,value){this.attrs[key]=value;}
  querySelectorAll(){return [];}
  focus(){}
  showModal(){this.open=true;}
  close(){this.open=false;this.fire('close');}
}
function chunk(type,payload){
  const out=Buffer.alloc(payload.length+12);out.writeUInt32BE(payload.length);out.write(type,4);out.set(payload,8);
  out.writeUInt32BE(crc32(out.subarray(4,-4)),out.length-4);return out;
}
function raster(){
  const header=Buffer.alloc(13);header.writeUInt32BE(1);header.writeUInt32BE(1,4);header[8]=8;header[9]=2;
  return new Blob([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',header),
    chunk('IDAT',deflateSync(Buffer.from([0,255,255,255]))),chunk('IEND',Buffer.alloc(0))],{type:'image/png'});
}
function mounted(graph){
  const ids=['dialog','open','close','file','scene','status','atoms','bonds','provenance','export','export-provenance',
    'export-png','png-file','generate','cancel','style','hydrogens','rotate','display-note',
    'measure-mode','measure-result','measure-clear','measure-1','measure-2','measure-3','measure-4'];
  const nodes=Object.fromEntries(ids.map(id=>['supplied-3d-'+id,new Node()])),n=id=>nodes['supplied-3d-'+id];
  n('measure-mode').value='2';
  const dom=Object.assign(new Node(),{getElementById:id=>nodes[id],createElement:()=>new Node(),createElementNS:()=>new Node()});
  const store=createDocumentStore({schema:'nivram/1',graph}),downloads=[],requests=[];
  const view=mountSupplied3d({dom,cryptoApi:webcrypto,getDocument:()=>({...store,ready:Promise.resolve()}),
    download:(...args)=>downloads.push(args),sceneSnapshot:()=>'<svg/>',
    pngExport:snapshot=>exportThreeDPng(snapshot,{rasterize:async()=>raster(),version:async()=>'measurement-test'}),
    request:async(path,payload)=>{
      requests.push(path);
      if(path.endsWith('/cancel'))return {cancelled:true};
      assert(['conformer','coordinate-recovery'].includes(path));
      return native({action:path,payload});
    }});
  view.open();return {view,n,store,downloads,requests};
}
async function select(harness,ids){
  const {n}=harness;n('measure-mode').value=String(ids.length);await n('measure-mode').fire('change');
  for(const [index,id] of ids.entries()){
    const selector=n('measure-'+(index+1));
    assert.equal(selector.disabled,false);
    assert(selector.children.some(option=>option.value===String(id)),`Atom ${id} must be available in selector ${index+1}`);
    selector.value=String(id);
  }
  await n('measure-'+ids.length).fire('change');return n('measure-result').textContent;
}
function checkValue(text,expected,origin){
  const match=text.match(/\]: (-?\d+\.\d{4}) (Å|°)\./);assert(match,text);
  assert.equal(match[2],expected.ids.length===2?'Å':'°');
  assert(Math.abs(Number(match[1])-expected.value)<=0.00005001,`${text}; independent RDKit value ${expected.value}`);
  assert.match(text,origin);assert.match(text,/no image scale calibration, bond, energy or physical-validity inference/);
}

test('real generated and recovered measurement controls preserve Cartesian geometry and identity',async t=>{
  const fixture=native({action:'fixture'}),h=mounted(JSON.parse(fixture.graphText));t.after(()=>h.view.destroy());
  const initial=structuredClone(h.store.getSnapshot());
  assert.equal(await h.view.generate(),true,h.n('status').textContent);
  const generated=structuredClone(h.view.getState().model),provenance=structuredClone(h.view.getState().provenance);
  assert.equal(generated.kind,'generated_coordinates');assert.equal(generated.atoms.length,3);
  const added=generated.generatedHydrogens.hydrogens;
  const index=added.findIndex(atom=>atom.parentId===3);assert(index>=0,'Ethanol must have an oxygen-attached generated H');
  const hydrogenId=generated.atoms.length+index+1;
  const selections=[[1,2],[1,2,3],[3,hydrogenId],[2,3,hydrogenId],[1,2,3,hydrogenId]];
  const oracle=native({action:'oracle',scene:generated,selections});assert.equal(oracle.version,'2025.09.4');
  assert.equal(h.n('measure-1').children.length,4,'Default implicit H exposes canonical source atoms only');
  for(const expected of oracle.results.slice(0,2))checkValue(await select(h,expected.ids),expected,/Generated model coordinates; not an experimental structure/);

  await t.test('explicit generated H participates in distance, angle and signed torsion',async()=>{
    h.n('hydrogens').checked=true;await h.n('hydrogens').fire('change');
    assert.equal(h.n('measure-1').value,'');
    const option=h.n('measure-1').children.find(value=>value.value===String(hydrogenId));
    assert(option,`Generated hydrogen ${hydrogenId} must be available in the measurement selector`);
    assert.match(option.textContent,new RegExp(`H attachment ${added[index].id} \\(parent 3\\)`));
    for(const expected of oracle.results)checkValue(await select(h,expected.ids),expected,/Generated model coordinates/);
    const measured=h.n('measure-result').textContent;
    for(const key of ['ArrowRight','ArrowUp','+'])await h.n('scene').fire('keydown',{key,preventDefault(){}});
    h.n('style').value='space-filling';await h.n('style').fire('change');
    assert.equal(h.n('measure-result').textContent,measured);
    assert.notDeepEqual(h.view.getState().view,{yaw:0,pitch:0,zoom:1});
    assert.equal(await h.view.loadBytes(new TextEncoder().encode('not a MOL')),false);
    assert.equal(h.n('measure-result').textContent,measured);
    assert.deepEqual(h.view.getState().model,generated);assert.deepEqual(h.view.getState().provenance,provenance);
    assert.deepEqual(h.store.getSnapshot(),initial);
  });

  let png,record;
  await t.test('schema6 PNG recovery preserves values and distinguishes unverified historical origin',async()=>{
    assert.equal(await h.view.exportPng(),true);png=new Uint8Array(await h.downloads.at(-1)[0].arrayBuffer());
    record=await extractThreeDRecord(png);assert.equal(record.schema,'nivram-png-coordinates/6');
    assert.equal(record.molText,generated.sourceText);assert.equal(record.generatedHydrogensJson,JSON.stringify(generated.generatedHydrogens));
    assert.equal(await h.view.loadPng(png),true,h.n('status').textContent);
    assert.equal(h.n('measure-1').value,'');
    const recovered=h.view.getState().model;assert.equal(recovered.kind,'recovered_coordinates');
    assert.deepEqual(recovered.generatedHydrogens,generated.generatedHydrogens);
    assert.equal(h.view.getState().provenance.validation.hydrogenGeometryAuthenticated,false);
    for(const expected of oracle.results)checkValue(await select(h,expected.ids),expected,/Recovered coordinates; historical origin and generation claims are unverified/);
    const measured=h.n('measure-result').textContent;
    assert.equal(await h.view.loadPng(new Uint8Array([1,2,3])),false);assert.equal(h.n('measure-result').textContent,measured);
    assert.equal(h.view.getState().model,recovered);
    await h.n('export').fire('click');assert.equal(new TextDecoder().decode(h.downloads.at(-1)[0]),generated.sourceText);
    assert.equal(await h.view.exportPng(),true);
    const again=await extractThreeDRecord(h.downloads.at(-1)[0]);
    assert.equal(again.molText,record.molText);assert.equal(again.generatedHydrogensJson,record.generatedHydrogensJson);
    assert.equal(again.provenanceJson,record.provenanceJson);assert.deepEqual(h.store.getSnapshot(),initial);
  });

  await t.test('visibility, close and generated-source invalidation clear measurements without rewriting chemistry',async()=>{
    h.n('hydrogens').checked=false;await h.n('hydrogens').fire('change');
    assert.equal(h.n('measure-1').children.length,4);assert.equal(h.n('measure-1').value,'');
    assert(!h.n('measure-result').textContent.includes('Å'));
    await select(h,[1,2]);h.view.close();assert.equal(h.n('measure-1').value,'');
    h.view.open();assert.equal(await h.view.generate(),true,h.n('status').textContent);
    await select(h,[1,2]);
    const snapshot=h.store.getSnapshot(),changed=structuredClone(snapshot.document);changed.graph.atoms[0].x+=1;
    h.store.commit(changed,{expectedRevision:snapshot.revision});
    assert.equal(h.view.getState().model,null);assert.equal(h.n('measure-1').value,'');assert.equal(h.n('measure-1').disabled,true);
    assert(!h.n('measure-result').textContent.includes('Å'));
    assert.deepEqual(h.store.getSnapshot().document.graph,changed.graph);
    assert.equal(record.molText,generated.sourceText);assert.deepEqual(generated.generatedHydrogens,JSON.parse(record.generatedHydrogensJson));
  });
  assert.deepEqual(h.requests,['conformer','coordinate-recovery','conformer']);
});
