import {snapshotCoordinates} from './png-coordinates.mjs';
/** Pure validation + native SVG-to-PNG export. No layout or chemistry calls. */
import {stampPng,runtimeVersion} from './png-metadata.mjs';
import {rebaseBondPaint} from './bond-paint.mjs';
export const MAX_WIDTH = 9999;
export const MAX_DIMENSION = 32767;
export const MAX_PIXELS = 128_000_000;
export const MAX_MEMORY_BYTES = 512 * 1024 * 1024;
let exportInProgress=false;
export function validateOptions(options) {
  const {width, marginMode='percent', margin=2, transparentBackground=false} = options ?? {};
  if (!Number.isInteger(width) || width<1 || width>MAX_WIDTH) throw new Error('Width must be an integer between 1 and 9999 pixels.');
  if (!['percent','pixels'].includes(marginMode)) throw new Error('Choose percent or pixel margins.');
  if (typeof margin!=='number' || !Number.isFinite(margin) || margin<0) throw new Error('Margin must be a nonnegative finite number.');
  if(typeof transparentBackground!=='boolean')throw new Error('Transparent background must be true or false.');
  const padding=Math.ceil(marginMode==='percent' ? width*margin/100 : margin);
  const contentWidth=width-2*padding;
  if(contentWidth<1) throw new Error('The margins leave no space for the drawing.');
  return {width,padding,contentWidth,marginMode,margin,transparentBackground};
}
export function exportGeometry(bounds,options) {
  const o=validateOptions(options);
  if(!bounds || !Number.isFinite(bounds.width)||!Number.isFinite(bounds.height)||bounds.width<=0||bounds.height<=0) throw new Error('The drawing has no visible bounds.');
  const scale=o.contentWidth/bounds.width;
  const height=Math.ceil(bounds.height*scale)+2*o.padding;
  checkAllocation(o.width,height);
  const retainedPixels=(o.contentWidth+16)*(height-2*o.padding+16);
  const estimatedPeakBytes=estimatePeakBytes(o.width,height,{retainedPixels});
  return {...o,height,scale,estimatedPeakBytes};
}
export function preflightExport(bounds,options){
  const geometry=exportGeometry(bounds,options);
  enforceMemoryBudget(geometry.estimatedPeakBytes);
  return geometry;
}
function checkAllocation(w,h){
  if(!Number.isInteger(w)||!Number.isInteger(h)||w<1||h<1||w>MAX_DIMENSION||h>MAX_DIMENSION||w*h>MAX_PIXELS)
    throw new Error('This export exceeds the safe raster limit (32,767 pixels per side or 128 million pixels). Reduce the width; no lower-resolution file was created.');
}
/** Conservative working estimate, not a promise about a browser's total RSS.
 * Reserve four RGBA-sized buffers for raster/decode/readback or output/encoding,
 * plus a retained source canvas and SVG text/blob/serialization storage.
 */
export function estimatePeakBytes(w,h,{retainedPixels=0,sourceCharacters=0}={}){
  checkAllocation(w,h);
  if(!Number.isSafeInteger(retainedPixels)||retainedPixels<0||!Number.isSafeInteger(sourceCharacters)||sourceCharacters<0)throw new Error('Invalid raster memory estimate.');
  return 16*w*h+4*retainedPixels+8*sourceCharacters;
}
function checkMemory(w,h,options){
  const estimate=estimatePeakBytes(w,h,options);
  enforceMemoryBudget(estimate);
  return estimate;
}
function enforceMemoryBudget(estimate){
  if(estimate>MAX_MEMORY_BYTES)throw new Error('Estimated export memory exceeds the 512 MiB budget. Reduce the width; no lower-resolution file was created.');
}
function release(c){if(c){c.width=1;c.height=1;}}
function checkCancelled(signal){if(signal?.aborted)throw signal.reason??new DOMException('The export was cancelled.','AbortError');}
async function abortable(promise,signal){
  const pending=Promise.resolve(promise);let onAbort;
  try{
    checkCancelled(signal);
    if(!signal)return await pending;
    return await Promise.race([pending,new Promise((_resolve,reject)=>{
      onAbort=()=>reject(signal.reason??new DOMException('The export was cancelled.','AbortError'));
      signal.addEventListener('abort',onAbort,{once:true});
    })]);
  }catch(error){pending.catch(()=>{});throw error;}
  finally{if(onAbort)signal.removeEventListener('abort',onAbort);}
}
function canvas(w,h){
  checkAllocation(w,h);const c=document.createElement('canvas');
  try{
    c.width=w;c.height=h;
    if(c.width!==w||c.height!==h)throw new Error('The browser could not allocate the requested image dimensions.');
    return c;
  }catch(error){release(c);throw error;}
}
async function inkBounds(c,signal){
  checkCancelled(signal);
  const ctx=c.getContext('2d',{willReadFrequently:true});
  if(!ctx)throw new Error('Canvas rendering is unavailable.');
  const {width:w,height:h}=c;const d=ctx.getImageData(0,0,w,h).data;
  let l=w,t=h,r=-1,b=-1;
  for(let y=0;y<h;y++){
    for(let x=0,i=(y*w)*4+3;x<w;x++,i+=4)if(d[i]>0){if(x<l)l=x;if(x>r)r=x;if(y<t)t=y;if(y>b)b=y;}
    if(y>0&&y%256===0){await new Promise(resolve=>setTimeout(resolve,0));checkCancelled(signal);}
  }
  if(r<l)throw new Error('The drawing is empty at this export size.');
  return {x:l,y:t,width:r-l+1,height:b-t+1};
}
function vectorSource(group,box,w,h){
  const clone=group.cloneNode(true);
  clone.removeAttribute('transform');
  const body=new XMLSerializer().serializeToString(clone);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="${box.x} ${box.y} ${box.width} ${box.height}">${body}</svg>`;
}
async function raster(group,box,w,h,signal){
  checkCancelled(signal);
  const source=vectorSource(group,box,w,h);
  const estimatedPeakBytes=checkMemory(w,h,{sourceCharacters:source.length});
  let c=null,image=null,url=null;
  try{
    c=canvas(w,h);const ctx=c.getContext('2d',{willReadFrequently:true});if(!ctx)throw new Error('Canvas rendering is unavailable.');
    url=URL.createObjectURL(new Blob([source],{type:'image/svg+xml'}));
    image=new Image();image.src=url;await abortable(image.decode(),signal);checkCancelled(signal);ctx.drawImage(image,0,0,w,h);
    const result={canvas:c,sourceCharacters:source.length,estimatedPeakBytes};c=null;return result;
  }catch(error){if(signal?.aborted)throw error;throw new Error('SVG rasterization failed: '+error.message);}
  finally{release(c);image?.removeAttribute('src');if(url)URL.revokeObjectURL(url);}
}
export function visibleVectorBounds(group){
  if(!group||!group.querySelector('path,line,text,circle,ellipse,polygon,polyline,rect'))throw new Error('Draw or import a molecule before exporting.');
  const b=group.getBBox();let stroke=0;
  for(const el of group.querySelectorAll('path,line,circle,ellipse,polygon,polyline,rect')){
    const style=getComputedStyle(el);
    if(style.stroke!=='none')stroke=Math.max(stroke,parseFloat(style.strokeWidth)||0);
  }
  const guard=stroke/2+1;
  return {x:b.x-guard,y:b.y-guard,width:b.width+2*guard,height:b.height+2*guard};
}
function fixedScaleBounds(sourceGroup){
  const stage=document.createElementNS('http://www.w3.org/2000/svg','svg');
  stage.setAttribute('width','1000');stage.setAttribute('height','1000');
  stage.setAttribute('aria-hidden','true');
  Object.assign(stage.style,{position:'fixed',left:'-100000px',top:'0',visibility:'hidden',pointerEvents:'none'});
  // Browser text bounds can vary with the editor's zoom. Measure the same
  // connected, untransformed clone for main, preview, and batch exports.
  sourceGroup.removeAttribute('transform');stage.append(sourceGroup);
  try{document.body.append(stage);return visibleVectorBounds(sourceGroup);}
  finally{sourceGroup.remove();stage.remove();}
}
/**
 * Measure paint, then rerasterize the VECTOR at the requested resolution.
 * Final cropping only removes transparent pixels; it never scales a bitmap.
 * Content is centered with at most one pixel of left/right rounding asymmetry.
 */
export async function exportPng(group,options,{signal,graph,removeChemistryMetadata=false}={}){
  if(typeof removeChemistryMetadata!=='boolean')throw new TypeError('Remove chemistry metadata must be true or false.');
  checkCancelled(signal);
  if(exportInProgress)throw new Error('Another PNG export is still using the raster memory budget. Wait for it to finish.');
  const o=validateOptions(options);
  if(!graph)throw new Error('Molecular PNG export requires its coordinate snapshot.');
  const graphJson=snapshotCoordinates(graph);exportInProgress=true;
  let measure=null,painted=null,out=null;
  try{
    const version=await abortable(runtimeVersion(),signal);
    await abortable(document.fonts.ready,signal);checkCancelled(signal);
    if(!group)throw new Error('Draw or import a molecule before exporting.');
    const sourceGroup=rebaseBondPaint(group.cloneNode(true)),rough=fixedScaleBounds(sourceGroup);
    const geometry=preflightExport(rough,options);let peak=geometry.estimatedPeakBytes;
    const ratio=rough.height/rough.width;
    // Only the measurement pass may use a reduced sampling size. Final artwork
    // is always rerasterized from vectors at the requested output resolution.
    const mw=Math.max(1,Math.min(1400,Math.max(300,o.contentWidth),Math.floor(MAX_DIMENSION/ratio),Math.floor(Math.sqrt(MAX_MEMORY_BYTES/32/ratio))));
    const mh=Math.max(1,Math.ceil(mw*ratio));
    let rasterized=await raster(sourceGroup,rough,mw,mh,signal);measure=rasterized.canvas;peak=Math.max(peak,rasterized.estimatedPeakBytes);
    const ink=await inkBounds(measure,signal);
    const box={x:rough.x+ink.x*rough.width/mw,y:rough.y+ink.y*rough.height/mh,width:ink.width*rough.width/mw,height:ink.height*rough.height/mh};
    release(measure);measure=null;
    const guard=8;let scale=o.contentWidth/box.width,found=null;
    for(let pass=0;pass<4;pass++){
      checkCancelled(signal);release(painted);painted=null;
      const cw=Math.ceil(box.width*scale)+2*guard,ch=Math.ceil(box.height*scale)+2*guard;
      const vb={x:box.x-guard/scale,y:box.y-guard/scale,width:cw/scale,height:ch/scale};
      rasterized=await raster(sourceGroup,vb,cw,ch,signal);painted=rasterized.canvas;peak=Math.max(peak,rasterized.estimatedPeakBytes);
      found=await inkBounds(painted,signal);
      if(found.x===0||found.y===0||found.x+found.width>=cw||found.y+found.height>=ch)throw new Error('The drawing reached the raster safety boundary. Export was stopped to prevent clipping.');
      if(found.width<=o.contentWidth&&found.width>=o.contentWidth-1)break;
      scale*=(o.contentWidth-0.35)/found.width;
    }
    if(found.width>o.contentWidth||found.width<o.contentWidth-2)throw new Error('Could not fit the requested pixel width precisely without resampling. Try a slightly different width.');
    const height=found.height+2*o.padding;
    peak=Math.max(peak,checkMemory(o.width,height,{retainedPixels:painted.width*painted.height,sourceCharacters:rasterized.sourceCharacters}));
    out=canvas(o.width,height);const ctx=out.getContext('2d');if(!ctx)throw new Error('Canvas rendering is unavailable.');
    if(!o.transparentBackground){ctx.fillStyle='#FFFFFF';ctx.fillRect(0,0,o.width,height);}
    const left=o.padding+Math.floor((o.contentWidth-found.width)/2);
    ctx.drawImage(painted,found.x,found.y,found.width,found.height,left,o.padding,found.width,found.height);
    release(painted);painted=null;checkCancelled(signal);
    // Native PNG encoding cannot be interrupted reliably. Retain exclusive
    // budget ownership until its callback, then reject a cancelled result.
    const encoded=await new Promise((resolve,reject)=>out.toBlob(b=>b?resolve(b):reject(new Error('PNG encoding failed.')),'image/png'));
    checkCancelled(signal);
    const blob=await stampPng(encoded,{graphJson,version,removeChemistryMetadata});checkCancelled(signal);
    return {blob,removeChemistryMetadata,transparentBackground:o.transparentBackground,width:o.width,height,padding:o.padding,paintedWidth:found.width,paintedHeight:found.height,left,estimatedPeakBytes:peak,memoryBudgetBytes:MAX_MEMORY_BYTES};
  }finally{release(measure);release(painted);release(out);exportInProgress=false;}
}
export function downloadBlob(blob,filename,beforeDispatch){
  const url=URL.createObjectURL(blob);
  try{
    const a=document.createElement('a');a.href=url;a.download=filename;
    // An optional metadata observer must never prevent the user's download.
    if(typeof beforeDispatch==='function'){try{beforeDispatch();}catch{}}
    a.click();
  }
  catch(error){URL.revokeObjectURL(url);throw error;}
  setTimeout(()=>URL.revokeObjectURL(url),30000);
}
