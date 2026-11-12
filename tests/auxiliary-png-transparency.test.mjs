import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {rasterizeScene,exportThreeDPng} from '../public/modules/three-d-png.mjs';
import {mountReferenceChart} from '../public/modules/population-chart.mjs';
import {mountPopulationChart} from '../public/modules/supplied-population-chart.mjs';
import {validateCapabilities,validatePopulationEnvelope} from '../public/modules/population-view.mjs';
const require=createRequire(import.meta.url);
const {createCanvas,loadImage}=require('@napi-rs/canvas');
const Chart=require('../public/vendor/chart.umd.min.js');
const fixture=JSON.parse(readFileSync(new URL('./fixtures/population-analytical.json',import.meta.url)));
const dataset=JSON.parse(readFileSync(new URL('../nivram/data/physiology.json',import.meta.url)));
// Real Chart.js and PNG encoder with a minimal DOM adapter. No browser qualification.
class Node {
 constructor(){this.style={};this.dataset={};this.children=[];this.attrs={};this.classList={toggle(){}};}
 append(...nodes){this.children.push(...nodes);} replaceChildren(...nodes){this.children=nodes;}
 setAttribute(k,v){this.attrs[k]=v;} addEventListener(){} removeEventListener(){} remove(){} focus(){}
}
function domFixture(){
 const canvases=[];
 const dom={createElement(tag){if(tag!=='canvas')return new Node();
  const canvas=createCanvas(1000,560);canvas.style={};canvas.dataset={};canvas.setAttribute=()=>{};canvas.addEventListener=()=>{};canvas.removeEventListener=()=>{};
  const widthDescriptor=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(canvas),'width');Object.defineProperty(canvas,'width',{get(){return widthDescriptor.get.call(this);},set(value){this.lastWidth=value;widthDescriptor.set.call(this,value);}});
  canvas.toBlob=cb=>cb(new Blob([canvas.toBuffer('image/png')],{type:'image/png'}));canvases.push(canvas);return canvas;}};
 const host=new Node();host.ownerDocument=dom;return {host,dom,canvases};
}
async function pixels(blob){const image=await loadImage(Buffer.from(await blob.arrayBuffer()));const canvas=createCanvas(image.width,image.height);const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);return ctx.getImageData(0,0,image.width,image.height).data;}
function checkAlpha(data,transparent){assert.deepEqual([...data.slice(0,4)],transparent?[0,0,0,0]:[255,255,255,255]);assert.ok(data.some((v,i)=>i%4===3&&v===255),'foreground remains visible');if(!transparent)assert.ok(data.every((v,i)=>i%4!==3||v===255),'default is fully opaque');}
for(const kind of ['reference','population'])test(`${kind}: real Chart.js PNG alpha, light ink, unchanged live dark theme, metadata and validation`,async t=>{
 const originalFetch=globalThis.fetch;globalThis.fetch=async()=>({ok:true,text:async()=>'{"version":"test-transparency"}'});t.after(()=>{globalThis.fetch=originalFetch;});
 const {host,canvases}=domFixture();
 const chart=kind==='reference'?mountReferenceChart(host,{Chart,dataset,capabilities:validateCapabilities(fixture.capabilities),theme:'dark'}):mountPopulationChart(host,{Chart,dataset,envelope:validatePopulationEnvelope(fixture.complete.envelope,fixture.complete.binding),theme:'dark'});
 t.after(()=>chart.destroy());
 for(const options of [{},{transparentBackground:true},{transparentBackground:false}]){const blob=await chart.exportPng(options);checkAlpha(await pixels(blob),options.transparentBackground===true);assert.ok(Buffer.from(await blob.arrayBuffer()).includes(Buffer.from('nivraM test-transparency')));assert.equal(chart.getSnapshot().theme,'dark');assert.equal(canvases.at(-1).lastWidth,0);}
 for(const value of [null,0,1,'true','false',{},[]])await assert.rejects(chart.exportPng({transparentBackground:value}),/must be a boolean/);
});
test('3D real PNG alpha and cleanup, strict Boolean before raster work',async()=>{
 for(const transparentBackground of [undefined,false,true]){
  const {dom,canvases}=domFixture();let source,revoked=false;
  class ImageAdapter{async decode(){source=await loadImage(Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="800"><circle cx="640" cy="400" r="100" fill="red"/></svg>'));}}
  const originalCreate=dom.createElement;
  dom.createElement=tag=>{const canvas=originalCreate(tag);const get=canvas.getContext.bind(canvas);canvas.getContext=()=>{const ctx=get('2d');const draw=ctx.drawImage.bind(ctx);ctx.drawImage=(_, ...args)=>draw(source,...args);return ctx;};return canvas;};
  const blob=await rasterizeScene('<svg/>',{dom,ImageClass:ImageAdapter,urlApi:{createObjectURL:()=> 'blob:fixture',revokeObjectURL:()=>{revoked=true;}},...(transparentBackground===undefined?{}:{transparentBackground})});
  checkAlpha(await pixels(blob),transparentBackground===true);assert.ok(revoked);assert.equal(canvases[0].lastWidth,0);
 }
 for(const value of [null,0,1,'true',{},[]]){
  await assert.rejects(rasterizeScene('',{dom:{},ImageClass:class{},transparentBackground:value}),/must be a boolean/);
  await assert.rejects(exportThreeDPng({}, {transparentBackground:value,rasterize:()=>{throw Error('should not run');}}),/must be a boolean/);
 }
});
test('3D export forwards the selected alpha option without changing snapshot data',async()=>{
 const snapshot=Object.freeze({svgText:'<svg/>',record:Object.freeze({marker:'unchanged'})});
 for(const options of [{},{transparentBackground:false},{transparentBackground:true}]){
  let called=false;
  await assert.rejects(exportThreeDPng(snapshot,{...options,rasterize:async(text,received)=>{called=true;assert.equal(text,snapshot.svgText);assert.deepEqual(received,{transparentBackground:options.transparentBackground??false});throw new Error('raster-stop');},version:()=>{throw Error('Must not finalize failed raster');}}),/raster-stop/);
  assert.ok(called);assert.equal(snapshot.record.marker,'unchanged');
 }
});
