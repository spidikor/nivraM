/** Trusted paint derived only after the input SVG has passed its strict sanitizer.
 * Original bond paths and coordinates stay intact. Incoming paint servers are
 * never accepted; these local definitions belong to the prepared fragment. */
const SVG_NS='http://www.w3.org/2000/svg';
const MARKER='data-nivram-bond-paint';
const MAX_GRADIENTS=3072; // At most three strokes or one filled wedge per bond.
const NUMBER='[+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?';
const LINE=new RegExp(`^\\s*M\\s*(${NUMBER})[\\s,]+(${NUMBER})\\s+L\\s*(${NUMBER})[\\s,]+(${NUMBER})\\s*$`);
const CLOSED=new RegExp(`^\\s*M\\s*(${NUMBER})[\\s,]+(${NUMBER})((?:\\s+L\\s*${NUMBER}[\\s,]+${NUMBER}){2,3})\\s+Z\\s*$`);
const VERTEX=new RegExp(`L\\s*(${NUMBER})[\\s,]+(${NUMBER})`,'g');
const WIDTH=new RegExp(`^(${NUMBER})(?:px)?$`);
const PROPERTIES=new Set(['fill','fill-rule','stroke','stroke-width','stroke-linecap','stroke-linejoin','stroke-miterlimit','stroke-opacity','fill-opacity','opacity']);
let idPrefix=null,sequence=0;

function freshId(root,reserved){
  if(idPrefix===null)idPrefix='nivram-bond-'+[...globalThis.crypto.getRandomValues(new Uint8Array(16))].map(value=>value.toString(16).padStart(2,'0')).join('');
  let id;
  do{id=`${idPrefix}-${++sequence}`;}while(reserved.has(id)||root.ownerDocument.getElementById(id));
  reserved.add(id);return id;
}
function existingIds(root){
  const ids=new Set([...root.querySelectorAll('[id]')].map(node=>node.id));
  if(root.nodeType===1&&root.id)ids.add(root.id);
  return ids;
}
function setPaint(node,property,id){
  const pattern=new RegExp(`(^|;)(\\s*${property}\\s*:)\\s*[^;]*`);
  node.setAttribute('style',node.getAttribute('style').replace(pattern,(_match,separator,key)=>`${separator}${key}url(#${id})`));
}
function paintedPath(node){
  if(node?.namespaceURI!==SVG_NS||node.localName!=='path')return null;
  if([...node.attributes].some(attribute=>!['class','d','style','id'].includes(attribute.name)))return null;
  const classes=node.getAttribute('class')??'';
  if(!/^bond-\d+\s+atom-\d+\s+atom-\d+$/.test(classes))return null;
  const paint=new Map();
  for(const declaration of (node.getAttribute('style')??'').split(';').filter(value=>value.trim())){
    const colon=declaration.indexOf(':'),key=declaration.slice(0,colon).trim(),value=declaration.slice(colon+1).trim();
    if(colon<0||!PROPERTIES.has(key)||paint.has(key))return null;
    paint.set(key,value);
  }
  if(!/^#[0-9a-fA-F]{6}$/.test(paint.get('stroke')??''))return null;
  for(const key of ['opacity','stroke-opacity','fill-opacity'])if(paint.has(key)&&Number(paint.get(key))!==1)return null;
  const width=WIDTH.exec(paint.get('stroke-width')??'');if(!width)return null;
  const strokeWidth=Number(width[1]);if(!Number.isFinite(strokeWidth)||strokeWidth<=0)return null;
  return {node,classes,paint,color:paint.get('stroke').toUpperCase(),strokeWidth};
}
function ordinary(node){
  const item=paintedPath(node);if(!item||item.paint.get('fill')!=='none')return null;
  const line=LINE.exec(node.getAttribute('d')??'');if(!line)return null;
  const values=line.slice(1).map(Number);if(!values.every(Number.isFinite))return null;
  const points=[{x:values[0],y:values[1]},{x:values[2],y:values[3]}];
  const length=Math.hypot(points[1].x-points[0].x,points[1].y-points[0].y);
  if(!Number.isFinite(length)||length===0)return null;
  const paint=new Map(item.paint);paint.delete('stroke');
  return {...item,points,length,style:JSON.stringify([...paint].sort(([a],[b])=>a.localeCompare(b)))};
}
function filled(node){
  const item=paintedPath(node);if(!item||item.paint.get('fill')?.toUpperCase()!==item.color)return null;
  const path=CLOSED.exec(node.getAttribute('d')??'');if(!path)return null;
  const points=[{x:Number(path[1]),y:Number(path[2])},...[...path[3].matchAll(VERTEX)].map(match=>({x:Number(match[1]),y:Number(match[2])}))];
  if(!points.every(point=>Number.isFinite(point.x)&&Number.isFinite(point.y))||points.some((point,i)=>points.slice(i+1).some(other=>samePoint(point,other))))return null;
  const turns=points.map((a,i)=>{const b=points[(i+1)%points.length],c=points[(i+2)%points.length];return (b.x-a.x)*(c.y-b.y)-(b.y-a.y)*(c.x-b.x);});
  if(!turns.every(value=>Number.isFinite(value)&&value!==0&&Math.sign(value)===Math.sign(turns[0])))return null;
  const paint=new Map(item.paint);paint.delete('stroke');paint.delete('fill');
  return {...item,points,style:JSON.stringify([...paint].sort(([a],[b])=>a.localeCompare(b)))};
}
const samePoint=(a,b)=>a.x===b.x&&a.y===b.y;
function pair(first,second){
  if(!first||!second||first.classes!==second.classes||first.style!==second.style||first.color===second.color)return null;
  let match=null;
  for(let a=0;a<2;a++)for(let b=0;b<2;b++)if(samePoint(first.points[a],second.points[b])){
    if(match)return null;
    match={shared:first.points[a],start:first.points[1-a],end:second.points[1-b]};
  }
  if(!match)return null;
  const {shared,start,end}=match;
  const ux=(shared.x-start.x)/first.length,uy=(shared.y-start.y)/first.length;
  const vx=(end.x-shared.x)/second.length,vy=(end.y-shared.y)/second.length;
  // RDKit rounds SVG coordinates to tenths. Admit that small angular error,
  // while rejecting bent, doubled-back or otherwise ambiguous pairs.
  if(ux*vx+uy*vy<=0||Math.abs(ux*vy-uy*vx)>0.01)return null;
  const length=Math.hypot(end.x-start.x,end.y-start.y);
  if(!Number.isFinite(length)||length===0)return null;
  const ax=(end.x-start.x)/length,ay=(end.y-start.y)/length;
  const center=(shared.x-start.x)*ax+(shared.y-start.y)*ay;
  if(!Number.isFinite(center)||center<=0||center>=length)return null;
  const half=Math.min(first.strokeWidth/2,center,length-center);
  return {start,end,low:(center-half)/length,high:(center+half)/length};
}
const midpoint=(a,b)=>({x:(a.x+b.x)/2,y:(a.y+b.y)/2});
const distance=(a,b)=>Math.hypot(b.x-a.x,b.y-a.y);
function wedge(first,second,bondWidth){
  if(!first||!second||first.classes!==second.classes||first.style!==second.style||first.color===second.color)return null;
  // The wedge outline is a reduced RDKit width. Use the validated full style
  // width explicitly; never estimate it from an ordinary-bond reference image.
  if(!Number.isFinite(bondWidth)||bondWidth<.5||bondWidth>5)return null;
  const triangle=first.points.length===3?first:second,quad=triangle===first?second:first;
  if(triangle.points.length!==3||quad.points.length!==4)return null;
  const shared=triangle.points.filter(point=>quad.points.some(other=>samePoint(point,other)));
  if(shared.length!==2)return null;
  const start=triangle.points.find(point=>!shared.some(other=>samePoint(point,other)));
  const far=quad.points.filter(point=>!shared.some(other=>samePoint(point,other)));
  const adjacent=(points,a,b)=>{const i=points.indexOf(a),j=points.indexOf(b);return Math.abs(i-j)===1||Math.abs(i-j)===points.length-1;};
  const near=shared.map(point=>quad.points.find(other=>samePoint(point,other)));
  if(!adjacent(quad.points,...near)||!adjacent(quad.points,...far))return null;
  const end=midpoint(...far),center=midpoint(...shared),length=distance(start,end);
  if(!Number.isFinite(length)||length===0)return null;
  const ux=(end.x-start.x)/length,uy=(end.y-start.y)/length;
  const along=point=>(point.x-start.x)*ux+(point.y-start.y)*uy;
  const across=point=>(point.x-start.x)*uy-(point.y-start.y)*ux;
  const middle=along(center),nearWidth=distance(...shared),farWidth=distance(...far);
  // Admit only a straight, widening triangle split at a transverse edge.
  // Tenths-rounded SVG vertices permit a small absolute axis error.
  if(middle<=0||middle>=length||Math.abs(across(center))>.15||Math.abs(along(shared[0])-along(shared[1]))>.15||Math.abs(along(far[0])-along(far[1]))>.15||nearWidth<=0||farWidth<=nearWidth)return null;
  for(const point of shared){
    const outer=far.find(other=>Math.sign(across(other))===Math.sign(across(point)));
    if(!outer||Math.abs(across(point)-across(outer)*middle/length)>.15)return null;
  }
  const half=Math.min(bondWidth/2,middle,length-middle);
  return {start,end,low:(middle-half)/length,high:(middle+half)/length,startColor:triangle.color,endColor:quad.color};
}
export function applyBondPaint(fragment,paintStyle={}){
  const children=[...fragment.children],reserved=existingIds(fragment),doc=fragment.ownerDocument;
  const classCounts=new Map();
  for(const child of children){const key=child.getAttribute('class');if(child.localName==='path'&&key)classCounts.set(key,(classCounts.get(key)??0)+1);}
  let definitions=null,count=0;
  for(let index=0;index+1<children.length&&count<MAX_GRADIENTS;index++){
    let first=ordinary(children[index]),second=ordinary(children[index+1]),geometry=pair(first,second),properties=['stroke'];
    if(!geometry){first=filled(children[index]);second=filled(children[index+1]);geometry=classCounts.get(first?.classes)===2?wedge(first,second,paintStyle?.bondWidth):null;properties=['fill','stroke'];}
    if(!geometry)continue;
    if(!definitions){definitions=doc.createElementNS(SVG_NS,'defs');fragment.prepend(definitions);}
    const gradient=doc.createElementNS(SVG_NS,'linearGradient'),id=freshId(fragment,reserved);
    gradient.id=id;gradient.setAttribute(MARKER,'1');gradient.setAttribute('gradientUnits','userSpaceOnUse');
    for(const [key,value] of Object.entries({x1:geometry.start.x,y1:geometry.start.y,x2:geometry.end.x,y2:geometry.end.y}))gradient.setAttribute(key,String(value));
    const startColor=geometry.startColor??first.color,endColor=geometry.endColor??second.color;
    for(const [offset,color] of [[0,startColor],[geometry.low,startColor],[geometry.high,endColor],[1,endColor]]){
      const stop=doc.createElementNS(SVG_NS,'stop');stop.setAttribute('offset',String(offset));stop.setAttribute('stop-color',color);gradient.append(stop);
    }
    definitions.append(gradient);
    for(const item of [first,second])for(const property of properties)setPaint(item.node,property,id);
    count++;index++;
  }
  return fragment;
}

/** A connected export clone must not resolve paint against its live original. */
export function rebaseBondPaint(root){
  const reserved=existingIds(root),ids=new Map();
  for(const gradient of root.querySelectorAll(`linearGradient[${MARKER}="1"]`)){
    if(ids.has(gradient.id))throw new Error('The generated drawing contains duplicate paint identifiers.');
    const id=freshId(root,reserved);ids.set(gradient.id,id);gradient.id=id;
  }
  if(ids.size)for(const node of root.querySelectorAll('path')){
    for(const property of ['fill','stroke']){
      const reference=/^url\(["']?#([^"')]+)["']?\)$/.exec(node.style[property]);
      if(reference&&ids.has(reference[1]))setPaint(node,property,ids.get(reference[1]));
    }
  }
  return root;
}
