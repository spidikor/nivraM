import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {writeFile,readFile,mkdir} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const require=createRequire(import.meta.url),{createCanvas,Image,loadImage}=require('@napi-rs/canvas');
const source=process.env.NIVRAM_TRANSPARENCY_SOURCE?pathToFileURL(process.env.NIVRAM_TRANSPARENCY_SOURCE+'/'):new URL('../',import.meta.url);
const {exportPng,validateOptions}=await import(new URL('public/modules/export.mjs',source));
const {validateProject,createDocumentStore}=await import(new URL('public/modules/document.mjs',source));
const graph={atoms:[{id:'a',symbol:'C',x:0,y:0},{id:'b',symbol:'O',x:1,y:0}],bonds:[{id:'b1',begin:'a',end:'b',order:1}]};
const project={schema:'nivram/1',graph};
const options={width:360,marginMode:'pixels',margin:12};
function fixture(t){
 const globals={document:{fonts:{ready:Promise.resolve()},body:{append(){}},createElement(tag){assert.equal(tag,'canvas');const c=createCanvas(1,1);c.toBlob=callback=>callback(new Blob([c.toBuffer('image/png')],{type:'image/png'}));return c;},createElementNS(){return {style:{},setAttribute(){},append(){},remove(){}};}},XMLSerializer:class{serializeToString(){return '<g><path d="M 5 10 L 95 10" stroke="#222222" stroke-width="4"/><circle cx="95" cy="10" r="5" fill="#ff0000"/></g>';}},getComputedStyle:()=>({stroke:'#222222',strokeWidth:'4'}),fetch:async()=>({ok:true,text:async()=>JSON.stringify({version:'0.2.0-dev.1'})})};
 const urls=new Map();let serial=0;
 t.mock.method(URL,'createObjectURL',b=>{const url='blob:test-'+serial++;urls.set(url,b);return url;});
 t.mock.method(URL,'revokeObjectURL',u=>urls.delete(u));
 globals.Image=class extends Image{set src(url){this.url=url;}async decode(){super.src=Buffer.from(await urls.get(this.url).arrayBuffer());await super.decode();}removeAttribute(){}};
 for(const [key,value]of Object.entries(globals)){const old=Object.getOwnPropertyDescriptor(globalThis,key);Object.defineProperty(globalThis,key,{configurable:true,value});t.after(()=>old?Object.defineProperty(globalThis,key,old):delete globalThis[key]);}
 const shape={};const group={nodeType:1,removeAttribute(){},remove(){},cloneNode(){return {...this};},querySelector(){return shape;},querySelectorAll(selector){return selector==='path,line,circle,ellipse,polygon,polyline,rect'?[shape]:[];},getBBox(){return {x:3,y:5,width:97,height:10};}};
 return {group,urls};
}
test('strict option, old project white default, persistence, undo and redo',()=>{
 assert.equal(validateOptions(options).transparentBackground,false);
 for(const value of [null,0,1,'true',{},[]])assert.throws(()=>validateOptions({...options,transparentBackground:value}));
 const old=validateProject(project);assert.equal(old.export.transparentBackground,false);
 const store=createDocumentStore(old),on={...old,export:{...old.export,transparentBackground:true}};
 store.commit(on);assert.equal(store.getSnapshot().document.export.transparentBackground,true);
 const serialized=JSON.parse(JSON.stringify(store.getSnapshot().document));assert.equal(validateProject(serialized).export.transparentBackground,true);
 store.commit(store.historyTarget(),{mode:'undo'});assert.equal(store.getSnapshot().document.export.transparentBackground,false);
 store.commit(store.historyTarget(true),{mode:'redo'});assert.equal(store.getSnapshot().document.export.transparentBackground,true);
});
test('real nonbrowser SVG raster and PNG encoder preserve alpha, white default and metadata',async t=>{
 const {group,urls}=fixture(t);const outputs=[];
 for(const flag of [undefined,false,true]){
  const result=await exportPng(group,{...options,...(flag===undefined?{}:{transparentBackground:flag})},{graph});
  const bytes=Buffer.from(await result.blob.arrayBuffer()),image=await loadImage(bytes),c=createCanvas(image.width,image.height);c.getContext('2d').drawImage(image,0,0);const pixels=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
  assert.equal(c.width,360);assert.equal(result.padding,12);
  let ink=0,partial=0;
  for(let y=0;y<c.height;y++)for(let x=0;x<c.width;x++){
   const p=pixels.subarray((y*c.width+x)*4,(y*c.width+x+1)*4),border=x<12||x>=c.width-12||y<12||y>=c.height-12;
   if(flag===true){if(border)assert.equal(p[3],0);if(p[3]>0)ink++;if(p[3]>0&&p[3]<255)partial++;}
   else{assert.equal(p[3],255);if(border)assert.deepEqual([...p],[255,255,255,255]);}
  }
  if(flag===true){assert.ok(ink>0);assert.ok(partial>0,'antialias alpha survives');}
  assert.ok(bytes.includes(Buffer.from('nivraM Coordinates')));assert.ok(bytes.includes(Buffer.from('Software\0nivraM 0.2.0-dev.1')));assert.ok(bytes.includes(Buffer.from('drawing-2d')));
  outputs.push({result,bytes,pixels});
  if(process.env.NIVRAM_TRANSPARENCY_EVIDENCE){await mkdir(process.env.NIVRAM_TRANSPARENCY_EVIDENCE,{recursive:true});await writeFile(process.env.NIVRAM_TRANSPARENCY_EVIDENCE+`/drawing-${flag===true?'transparent':flag===false?'white-explicit':'white-default'}.png`,bytes);}
 }
 assert.deepEqual(outputs[0].bytes,outputs[1].bytes);assert.equal(outputs[1].result.height,outputs[2].result.height);assert.equal(outputs[1].result.paintedWidth,outputs[2].result.paintedWidth);assert.equal(urls.size,0);
 for(let i=0;i<outputs[2].pixels.length;i+=4)if(outputs[2].pixels[i+3]===255)assert.deepEqual(outputs[2].pixels.subarray(i,i+4),outputs[1].pixels.subarray(i,i+4));
});
test('all image save checkboxes use exact label and unchecked markup; main state wiring includes option',async()=>{
 const html=await readFile(new URL('public/index.html',source),'utf8');
 for(const id of ['transparent-background','supplied-3d-transparent-background','population-transparent-background','ph-transparent-background']){
  const match=html.match(new RegExp(`<label[^>]*><input id="${id}"[^>]*> Transparent background\\?</label>`));assert.ok(match,id);assert.ok(!/\bchecked\b/.test(match[0]));
 }
 const app=await readFile(new URL('public/app.mjs',source),'utf8');assert.ok(app.includes("$('transparent-background').checked=nextDocument.export.transparentBackground"));
 assert.ok(app.includes("transparentBackground:$('transparent-background').checked"));
 assert.ok(app.includes("'export-width','margin-mode','margin-value','transparent-background','reset-margin'"));
});

test('PNG alpha selection crosses each native save boundary without changing default signatures',async t=>{
 const {saveReferenceExport}=await import('../public/modules/reference-save.mjs');
 const {savePopulationExport}=await import('../public/modules/population-save.mjs');
 const {createHash}=await import('node:crypto');
 const previous=Object.getOwnPropertyDescriptor(globalThis,'FileReader');
 Object.defineProperty(globalThis,'FileReader',{configurable:true,value:class{
  async readAsDataURL(blob){this.result='data:image/png;base64,'+Buffer.from(await blob.arrayBuffer()).toString('base64');this.onload?.();}abort(){this.onabort?.();}
 }});t.after(()=>previous?Object.defineProperty(globalThis,'FileReader',previous):delete globalThis.FileReader);
 const bytes=createCanvas(10,10).toBuffer('image/png'),blob=new Blob([bytes],{type:'image/png'});
 for(const [save,method]of [[saveReferenceExport,'save_reference_export'],[savePopulationExport,'save_population_export']]){
  for(const flag of [undefined,false,true]){
   let args;const native={[method]:async(...a)=>{args=a;return {status:'saved',bytes:blob.size,sha256:createHash('sha256').update(bytes).digest('hex')};}};
   assert.deepEqual(await save({kind:'png',blob,native,token:'test-token',...(flag===undefined?{}:{transparentBackground:flag})}),{status:'saved'});
   assert.deepEqual(args,['png',bytes.toString('base64'),'test-token',...(flag===true?[true]:[])]);
  }
  for(const bad of [null,0,1,'true',{}])await assert.rejects(save({kind:'png',blob,transparentBackground:bad}),/background option/);
  await assert.rejects(save({kind:'json',blob:new Blob(['{}'],{type:'application/json'}),transparentBackground:true}),/background option/);
 }
});
