/** Standalone current-layout 2D artwork. No raster, layout or chemistry calls. */
import {validateOptions,MAX_DIMENSION} from './export.mjs';
import {sanitizeSvg} from './document.mjs';
const NS='http://www.w3.org/2000/svg';
export const MAX_SVG_BYTES=8_000_000;
const MAX_NODES=100_000;
const SHAPES=new Set(['path','line','circle','ellipse','polygon','polyline','rect']);
const NUMBER='[+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?';
const SCALAR=new RegExp(`^${NUMBER}(?:px)?$`);
const NUMBERS=new RegExp(`^${NUMBER}(?:[ ,]+${NUMBER})*$`);
function cancelled(signal){if(signal?.aborted)throw signal.reason??new DOMException('The export was cancelled.','AbortError');}

/** Width is the nominal display width, not a raster resolution. */
export function svgGeometry(bounds,options){
  const o=validateOptions(options);
  if(!bounds||!['x','y','width','height'].every(k=>typeof bounds[k]==='number'&&Number.isFinite(bounds[k]))||bounds.width<=0||bounds.height<=0)
    throw new Error('The drawing has no finite visible bounds.');
  const scale=o.contentWidth/bounds.width,height=Math.ceil(bounds.height*scale)+2*o.padding;
  // Retain bounded initial dimensions without allocating any pixel buffers.
  if(!Number.isFinite(scale)||scale<=0||!Number.isSafeInteger(height)||height<1||height>MAX_DIMENSION)
    throw new Error('This SVG exceeds the supported initial height of 32,767 pixels. Reduce the width.');
  const viewBox={x:bounds.x-o.padding/scale,y:bounds.y-(height/scale-bounds.height)/2,width:o.width/scale,height:height/scale};
  if(!Object.values(viewBox).every(Number.isFinite)||viewBox.width<=0||viewBox.height<=0)throw new Error('The SVG bounds cannot be represented safely.');
  return {...o,height,scale,viewBox};
}
function paintValue(key,value,ids){
  if(key==='fill'||key==='stroke')return /^(?:none|#[\da-fA-F]{3}|#[\da-fA-F]{6})$/.test(value)||(/^url\(#[\w-]+\)$/.test(value)&&ids.has(value.slice(5,-1)));
  if(key==='stroke-linecap')return ['butt','round','square'].includes(value);
  if(key==='stroke-linejoin')return ['miter','round','bevel'].includes(value);
  if(key==='fill-rule')return ['nonzero','evenodd'].includes(value);
  if(key==='stroke-dasharray')return value==='none'||NUMBERS.test(value);
  if(['stroke-width','stroke-miterlimit','stroke-dashoffset','opacity','fill-opacity','stroke-opacity'].includes(key))return SCALAR.test(value)&&Number.isFinite(parseFloat(value));
  return false;
}
function portableArtwork(group){
  const nodes=[...group.querySelectorAll('*')];
  if(nodes.length>MAX_NODES)throw new Error('The SVG contains too many vector elements.');
  const ids=new Set();
  for(const node of nodes)if(node.localName==='linearGradient'){
    if(node.getAttribute('data-nivram-bond-paint')!=='1'||!node.id||ids.has(node.id))throw new Error('Unsupported SVG paint definition.');
    ids.add(node.id);
  }
  for(const node of [group,...nodes])for(const child of [...node.childNodes])if(child.nodeType!==1){
    if(child.nodeType!==8&&!(child.nodeType===3&&!child.textContent.trim()))throw new Error('Non-vector SVG content is not supported.');
    child.remove();
  }
  let shapes=0;
  for(const node of nodes){
    if(!SHAPES.has(node.localName)&&!['g','defs','linearGradient','stop'].includes(node.localName))
      throw new Error('SVG export requires outlined vector artwork; text and nested viewports are not supported.');
    if(node.hasAttribute('transform'))throw new Error('Transformed SVG elements are not supported by this export.');
    if(SHAPES.has(node.localName))shapes++;
    // Only local generated paint is admitted after the engine SVG sanitizer.
    const style=node.getAttribute('style');
    if(style)for(const part of style.split(';').filter(p=>p.trim())){
      const colon=part.indexOf(':'),key=part.slice(0,colon).trim(),value=part.slice(colon+1).trim();
      if(colon<0||!paintValue(key,value,ids))throw new Error('Unsupported portable SVG styling.');
    }
    for(const key of ['fill','stroke','stroke-width','stroke-linecap','stroke-linejoin','stroke-miterlimit','stroke-dasharray','stroke-dashoffset','fill-rule','opacity','fill-opacity','stroke-opacity'])
      if(node.hasAttribute(key)&&!paintValue(key,node.getAttribute(key).trim(),ids))throw new Error('Unsupported portable SVG paint.');
    node.removeAttribute('class');
    if(node.localName!=='linearGradient')node.removeAttribute('id');
    node.removeAttribute('data-nivram-bond-paint');
  }
  if(!shapes)throw new Error('Draw or import a molecule before exporting.');
}
function artworkBounds(group,doc){
  const stage=doc.createElementNS(NS,'svg');stage.setAttribute('width','1000');stage.setAttribute('height','1000');stage.setAttribute('aria-hidden','true');
  Object.assign(stage.style,{position:'fixed',left:'-100000px',top:'0',visibility:'hidden',pointerEvents:'none'});
  stage.append(group);
  try{
    doc.body.append(stage);
    const b=group.getBBox();let guard=1;
    if(!['x','y','width','height'].every(k=>Number.isFinite(b[k]))||b.width<0||b.height<0||(!b.width&&!b.height))
      throw new Error('The drawing has no measurable vector geometry.');
    for(const node of group.querySelectorAll('path,line,circle,ellipse,polygon,polyline,rect')){
      const css=doc.defaultView.getComputedStyle(node);
      if(css.stroke!=='none'){
        const width=parseFloat(css.strokeWidth),miter=css.strokeLinejoin==='miter'?Math.max(1,parseFloat(css.strokeMiterlimit)||4):1;
        const cap=css.strokeLinecap==='square'?Math.SQRT2:1;
        if(!Number.isFinite(width)||width<0||!Number.isFinite(miter))throw new Error('The SVG stroke bounds are invalid.');
        guard=Math.max(guard,width*Math.max(miter,cap)/2+1);
      }
    }
    return {x:b.x-guard,y:b.y-guard,width:b.width+2*guard,height:b.height+2*guard};
  }finally{group.remove();stage.remove();}
}
export function exportSvg(source,options,{paintStyle={},signal,ownerDocument=globalThis.document}={}){
  cancelled(signal);validateOptions(options);
  const doc=ownerDocument,group=doc.createElementNS(NS,'g');
  // Rebuild exactly the engine-approved artwork and local paint, never clone the
  // canvas, selection overlays, CSS theme, or transient editor decorations.
  group.append(sanitizeSvg(source,doc,paintStyle));
  portableArtwork(group);cancelled(signal);
  const bounds=artworkBounds(group,doc),geometry=svgGeometry(bounds,options),v=geometry.viewBox;
  const svg=doc.createElementNS(NS,'svg');
  for(const [key,value] of Object.entries({xmlns:NS,width:geometry.width,height:geometry.height,viewBox:`${v.x} ${v.y} ${v.width} ${v.height}`,fill:'#000000',stroke:'none'}))svg.setAttribute(key,String(value));
  if(!geometry.transparentBackground){
    const background=doc.createElementNS(NS,'rect');
    for(const [key,value] of Object.entries({x:v.x,y:v.y,width:v.width,height:v.height,fill:'#FFFFFF',stroke:'none'}))background.setAttribute(key,String(value));
    svg.append(background);
  }
  svg.append(group);
  const text=new doc.defaultView.XMLSerializer().serializeToString(svg);
  const blob=new Blob([text],{type:'image/svg+xml'});
  if(blob.size>MAX_SVG_BYTES)throw new Error('The SVG exceeds the 8 MB output limit. No file was created.');
  cancelled(signal);
  return {blob,width:geometry.width,height:geometry.height,padding:geometry.padding,transparentBackground:geometry.transparentBackground};
}
