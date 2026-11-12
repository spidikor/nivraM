import {sameSource} from './conformer-source.mjs';
export const STEREO_MESSAGE='Invalid stereochemistry! Please specify';
const fail=()=>{throw new Error('Invalid or stale stereochemistry diagnostic.');};
const exact=(o,keys)=>o&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).sort().join('|')===[...keys].sort().join('|');
/** Validate transport and actual source IDs, not chemistry. RDKit owns classification. */
export function validateStereoDenial(result,request){
  if(!exact(result,['sourceHash','sourceRevision','sourceContextHash','denial'])||result.sourceHash!==request.sourceHash||result.sourceRevision!==request.sourceRevision||result.sourceContextHash!==request.sourceContextHash)fail();
  const d=result.denial,graph=JSON.parse(request.graphText);
  if(!exact(d,['schema','targets','unsupportedCount'])||d.schema!=='nivram-stereo-denial/1'||!Array.isArray(d.targets)||d.targets.length>384||!Number.isInteger(d.unsupportedCount)||d.unsupportedCount<0||d.unsupportedCount>384||d.targets.length+d.unsupportedCount<1||d.targets.length+d.unsupportedCount>384)fail();
  const atoms=new Map(graph.atoms.map(a=>[a.id,a])),bonds=new Map(graph.bonds.map(b=>[b.id,b])),seen=new Set();
  for(const t of d.targets){
    if(!exact(t,['kind','id','notation'])||typeof t.id!=='string'||t.id.length>128||!['atom','bond'].includes(t.kind)||seen.has(t.kind+':'+t.id))fail();
    seen.add(t.kind+':'+t.id);
    if(t.kind==='atom'){if(!atoms.has(t.id)||t.notation!=='R/S?')fail();}
    else {const b=bonds.get(t.id);if(!b||b.order!==2||!atoms.has(b.begin)||!atoms.has(b.end)||t.notation!=='E/Z?')fail();}
  }
  return structuredClone(d);
}
const finite=p=>Number.isFinite(p?.x)&&Number.isFinite(p?.y);
const boxValid=b=>finite(b)&&Number.isFinite(b.width)&&Number.isFinite(b.height)&&b.width>=0&&b.height>=0;
const inflate=(b,n)=>({x:b.x-n,y:b.y-n,width:b.width+2*n,height:b.height+2*n});
export const boxesOverlap=(a,b)=>a.x<=b.x+b.width&&a.x+a.width>=b.x&&a.y<=b.y+b.height&&a.y+a.height>=b.y;
/** Closed segment / rectangle intersection, including boundary contact. */
export function segmentHitsBox(a,b,r){
  let lo=0,hi=1;
  for(const [start,delta,min,max] of [[a.x,b.x-a.x,r.x,r.x+r.width],[a.y,b.y-a.y,r.y,r.y+r.height]]){
    if(Math.abs(delta)<1e-10){if(start<min||start>max)return false;continue;}
    const t1=(min-start)/delta,t2=(max-start)/delta;lo=Math.max(lo,Math.min(t1,t2));hi=Math.min(hi,Math.max(t1,t2));if(lo>hi)return false;
  }return true;
}
function segmentsCross(a,b,c,d){
  const cross=(p,q,r)=>(q.x-p.x)*(r.y-p.y)-(q.y-p.y)*(r.x-p.x);
  const ab1=cross(a,b,c),ab2=cross(a,b,d),cd1=cross(c,d,a),cd2=cross(c,d,b);
  return ab1*ab2<=0&&cd1*cd2<=0&&Math.max(a.x,b.x)>=Math.min(c.x,d.x)&&Math.max(c.x,d.x)>=Math.min(a.x,b.x)&&Math.max(a.y,b.y)>=Math.min(c.y,d.y)&&Math.max(c.y,d.y)>=Math.min(a.y,b.y);
}
/** Conservative bounded placement. A target may be omitted rather than overlap.
 * Arrow ends before the target's ink exclusion disk; it never pierces a label.
 * Bounding boxes overestimate curves/diagonal strokes, intentionally preferring fallback.
 */
export function placeStereoFlags(targets,obstacles,bounds){
  if(!Array.isArray(targets)||targets.length>384||!Array.isArray(obstacles)||obstacles.length>4096||!boxValid(bounds)||obstacles.some(b=>!boxValid(b)))return {placed:[],hidden:[...(targets||[])]};
  const placed=[],hidden=[],ink=obstacles.map(b=>inflate(b,4));
  for(const target of targets){
    if(!finite(target)||!boxValid({x:0,y:0,width:target.width,height:target.height})||!Number.isFinite(target.radius)||target.radius<8){hidden.push(target);continue;}
    let found=null;
    for(const distance of [48,72,104,144,200,280,380]){
      for(let step=0;step<24;step++){
        const angle=-Math.PI/2+step*Math.PI/12,ux=Math.cos(angle),uy=Math.sin(angle),x=target.x+ux*distance,y=target.y+uy*distance;
        const box={x:x-target.width/2-5,y:y-target.height/2-5,width:target.width+10,height:target.height+10};
        if(box.x<bounds.x||box.y<bounds.y||box.x+box.width>bounds.x+bounds.width||box.y+box.height>bounds.y+bounds.height||ink.some(b=>boxesOverlap(b,box))||placed.some(p=>boxesOverlap(inflate(p.box,5),box)||segmentHitsBox(p.start,p.end,inflate(box,4))))continue;
        const edge=Math.min((box.width/2)/Math.max(Math.abs(ux),1e-9),(box.height/2)/Math.max(Math.abs(uy),1e-9));
        const start={x:x-ux*(edge+3),y:y-uy*(edge+3)},end={x:target.x+ux*target.radius,y:target.y+uy*target.radius};
        if(distance-edge-target.radius<12||ink.some(b=>segmentHitsBox(start,end,b))||placed.some(p=>segmentHitsBox(start,end,inflate(p.box,5))||segmentsCross(start,end,p.start,p.end)||segmentHitsBox(start,end,inflate({x:Math.min(p.start.x,p.end.x),y:Math.min(p.start.y,p.end.y),width:Math.abs(p.end.x-p.start.x),height:Math.abs(p.end.y-p.start.y)},3))))continue;
        // Arrowhead footprint is included in the collision test.
        const head={x:end.x-9,y:end.y-9,width:18,height:18};
        if(ink.some(b=>boxesOverlap(b,head))||placed.some(p=>boxesOverlap(p.box,head)||segmentHitsBox(p.start,p.end,head)))continue;
        found={target,box,start,end,x,y};break;
      }if(found)break;
    }
    if(found)placed.push(found);else hidden.push(target);
  }return {placed,hidden};
}
const NS='http://www.w3.org/2000/svg';
/** Ephemeral UI only: never part of document, history, scene or export. */
export function mountStereoGuidance({dom,events,canvas,drawing,getDocument,getScene,getView,getSelection=()=>({atoms:[],bonds:[]}),setTimer=setTimeout,clearTimer=clearTimeout,now=Date.now}){
  const region=dom.getElementById('stereo-guidance-region'),status=dom.getElementById('structure-status');
  if(!region||!status)throw new Error('The stereochemistry guidance hosts are unavailable.');
  const svg=(tag,attrs,parent)=>{const n=dom.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));parent?.append(n);return n;};
  const layer=svg('g',{'pointer-events':'none','aria-label':'Unspecified stereochemistry guidance'},canvas);
  const note=dom.createElement('p');note.setAttribute('role','status');note.hidden=true;region.append(note);
  const dismissAll=dom.createElement('button');dismissAll.textContent='Dismiss stereochemical flags';dismissAll.hidden=true;dismissAll.setAttribute('class','stereo-guidance-dismiss');dismissAll.setAttribute('aria-label','Dismiss stereochemical flags');dismissAll.setAttribute('title','Dismiss stereochemical flags');status.append(dismissAll);
  const popup=dom.createElement('dialog'),heading=dom.createElement('h2'),explanation=dom.createElement('p'),list=dom.createElement('p'),review=dom.createElement('button'),dismiss=dom.createElement('button');
  popup.style.cssText='position:fixed;inset:1rem 1rem auto auto;max-width:32rem;z-index:1000;margin:0;padding:1rem';
  heading.textContent=STEREO_MESSAGE;heading.id='stereo-guidance-title';popup.setAttribute('aria-labelledby',heading.id);
  explanation.textContent='These sites have unspecified stereochemistry. The drawing may be chemically valid, but generation cannot choose a stereoisomer for you. No stereochemistry has been assigned.';
  review.textContent='Review drawing';dismiss.textContent='Dismiss stereochemical flags';popup.append(heading,explanation,list,review,dismiss);dom.body.append(popup);
  let binding=null,document=null,unsubscribe=null,timer=null,epoch=0,deadline=0,active=[],hidden=[],unsupported=0;
  const seenReceipts=new Set();
  const groups=new Map(),key=t=>t.kind+':'+t.id,describe=t=>`${t.kind} ${t.id} (${t.notation})`;
  function refresh(){
    list.textContent='Unspecified sites: '+active.map(describe).join(', ')+(unsupported?`; ${unsupported} additional site(s) cannot be localized by the supported notation.`:'.');
    note.textContent=hidden.length?'No safe visible placement for: '+hidden.map(describe).join(', ')+'. Zoom or reposition the drawing, then generate again.':'Grey italic flags mark unspecified stereochemistry; arrows stop before the target ink.';
    if(unsupported)note.textContent+=` ${unsupported} additional unresolved site(s) have no supported localized flag.`;
    note.hidden=false;dismissAll.hidden=false;region.hidden=false;status.setAttribute('data-stereo-guidance','active');
  }
  function checkDeadline(){if(binding&&now()>=deadline){clear();return false;}return !!binding;}
  function select(){
    if(!checkDeadline())return;const selection=getSelection(),atoms=new Set(selection.atoms),bonds=new Set(selection.bonds);
    const removed=active.filter(t=>(t.kind==='atom'?atoms:bonds).has(t.id));
    for(const t of removed){groups.get(key(t))?.remove();groups.delete(key(t));}
    const dismissed=new Set(removed.map(key));active=active.filter(t=>!dismissed.has(key(t)));hidden=hidden.filter(t=>!dismissed.has(key(t)));
    if(removed.length&&popup.open)popup.close();
    if(!active.length&&!unsupported){clear();return;}refresh();
  }
  function clear(){epoch++;if(timer!==null)clearTimer(timer);timer=null;deadline=0;active=[];hidden=[];unsupported=0;groups.clear();binding=null;layer.replaceChildren();note.hidden=true;note.textContent='';if(dom.activeElement===dismissAll||dom.activeElement===dismiss||popup.contains?.(dom.activeElement))canvas.focus?.();dismissAll.hidden=true;region.hidden=true;status.removeAttribute('data-stereo-guidance');unsubscribe?.();unsubscribe=null;if(popup.open)popup.close();}
  function show(event){
    const detail=event.detail;const receipt=detail?.request&&[detail.request.clientId,detail.request.jobId,detail.request.sourceHash,detail.request.sourceRevision,detail.request.sourceContextHash].join(':');
    if(seenReceipts.has(receipt))return;
    clear();document=getDocument();if(!document||!detail?.binding||!sameSource(document.getSnapshot(),detail.binding))return;
    let denial;try{denial=validateStereoDenial(detail.result,detail.request);}catch{return;}
    // The request and displayed source must be the same copied graph/context.
    if(detail.request.graphText!==detail.binding.graphText||detail.request.sourceContextText!==detail.binding.sourceContextText||detail.request.sourceRevision!==detail.binding.revision)return;
    seenReceipts.add(receipt);if(seenReceipts.size>64)seenReceipts.delete(seenReceipts.values().next().value);
    binding=detail.binding;deadline=now()+30000;const ticket=epoch;timer=setTimer(()=>{if(ticket===epoch)clear();},30000);unsubscribe=document.subscribe(()=>clear());
    const snapshot=document.getSnapshot(),graph=snapshot.document.graph,scene=getScene(),positions=new Map((scene?.positions||[]).map(p=>[p.id,p]));
    const targets=denial.targets.map(t=>{const b=t.kind==='bond'?graph.bonds.find(b=>b.id===t.id):null,a=positions.get(b?b.begin:t.id),z=b?positions.get(b.end):a;return {...t,x:a&&z?(a.x+z.x)/2:NaN,y:a&&z?(a.y+z.y)/2:NaN,radius:Math.max(14,(snapshot.document.style?.labelSize||36)*.8)};});
    let result={placed:[],hidden:targets};
    try{
      const nodes=[...drawing.querySelectorAll('path,line,polygon,polyline,circle,ellipse,rect,text')];if(nodes.length>4096)throw new Error('Too many obstacles');
      // Convert each rendered primitive box to canvas coordinates, including transforms.
      const inverse=canvas.getCTM().inverse(),obstacles=nodes.map(n=>{const b=n.getBBox(),m=inverse.multiply(n.getCTM());const ps=[[b.x,b.y],[b.x+b.width,b.y],[b.x,b.y+b.height],[b.x+b.width,b.y+b.height]].map(([x,y])=>({x:m.a*x+m.c*y+m.e,y:m.b*x+m.d*y+m.f}));const xs=ps.map(p=>p.x),ys=ps.map(p=>p.y);const stroke=Number.parseFloat(n.style?.strokeWidth||n.getAttribute('stroke-width')||snapshot.document.style?.bondWidth||4.2)*Math.max(Math.hypot(m.a,m.b),Math.hypot(m.c,m.d));return inflate({x:Math.min(...xs),y:Math.min(...ys),width:Math.max(...xs)-Math.min(...xs),height:Math.max(...ys)-Math.min(...ys)},Math.max(stroke/2,2));});
      for(const t of targets){const text=svg('text',{'font-size':22,'font-family':'Arial, sans-serif','font-style':'italic'},layer);text.textContent=t.notation;const b=text.getBBox();t.width=b.width;t.height=b.height;t.textX=b.x;t.textY=b.y;text.remove();if(!(t.width>0&&t.height>0))throw new Error('Text measurement unavailable');}
      result=placeStereoFlags(targets,obstacles,getView());
      for(const p of result.placed){
        const group=svg('g',{'data-stereo-target':key(p.target)},layer);groups.set(key(p.target),group);
        const attrs={stroke:'#78818d',opacity:.7,'stroke-width':1.8,fill:'none'};svg('line',{...attrs,x1:p.start.x,y1:p.start.y,x2:p.end.x,y2:p.end.y},group);
        const dx=p.start.x-p.end.x,dy=p.start.y-p.end.y,len=Math.hypot(dx,dy),ux=dx/len,uy=dy/len;
        svg('path',{...attrs,d:`M ${p.end.x+ux*7-uy*3} ${p.end.y+uy*7+ux*3} L ${p.end.x} ${p.end.y} L ${p.end.x+ux*7+uy*3} ${p.end.y+uy*7-ux*3}`},group);
        const text=svg('text',{x:p.x-p.target.textX-p.target.width/2,y:p.y-p.target.textY-p.target.height/2,'font-size':22,'font-family':'Arial, sans-serif','font-style':'italic',fill:'#78818d',opacity:.7},group);text.textContent=p.target.notation;
      }
    }catch{layer.replaceChildren();result={placed:[],hidden:targets};}
    active=targets;hidden=result.hidden;unsupported=denial.unsupportedCount;select();if(!binding)return;
    // The 3D owner returns to the editor while preserving its previous valid model.
    // This timed notice is nonmodal so target selection remains available.
    try{popup.show();}catch{note.textContent=STEREO_MESSAGE+'. '+list.textContent+' '+note.textContent;}

  }
  const close=()=>clear(),reviewDrawing=()=>{if(!checkDeadline())return;popup.close();const viewer=dom.getElementById('supplied-3d-dialog');if(viewer?.open)viewer.close();canvas.focus?.();};
  dismissAll.addEventListener('click',close);review.addEventListener('click',reviewDrawing);dismiss.addEventListener('click',close);events.addEventListener('nivram-stereo-denial',show);events.addEventListener('nivram-stereo-clear',clear);events.addEventListener('pagehide',clear);events.addEventListener('nivram-stereo-selection',select);events.addEventListener('visibilitychange',checkDeadline);dom.addEventListener?.('visibilitychange',checkDeadline);popup.addEventListener('cancel',clear);
  // View changes invalidate placement instead of leaving clipped/colliding hints.
  return {clear,destroy(){clear();events.removeEventListener('nivram-stereo-denial',show);events.removeEventListener('nivram-stereo-clear',clear);events.removeEventListener('pagehide',clear);events.removeEventListener('nivram-stereo-selection',select);events.removeEventListener('visibilitychange',checkDeadline);dom.removeEventListener?.('visibilitychange',checkDeadline);popup.removeEventListener('cancel',clear);dismissAll.removeEventListener('click',close);review.removeEventListener('click',reviewDrawing);dismiss.removeEventListener('click',close);layer.remove();note.remove();dismissAll.remove();popup.remove();}};
}
