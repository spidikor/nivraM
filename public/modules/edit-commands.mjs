/** Private graph candidates shared by pointer and keyboard entry points.
 * Callers still validate/render/commit through the document transaction path.
 * These helpers never commit, select entities, or mutate the input graph.
 */
import {cloneData} from './document.mjs';
import {setBondKind} from './bond-edit.mjs';

const BOND_KINDS=new Set(['1','2','3','wedge','dash']);
const RING_TYPES=new Set(['3','4','5','6','7','8','benzene']);
function finite(value,label){if(typeof value!=='number'||!Number.isFinite(value))throw new Error(`${label} must be finite.`);return value;}
function position(value,label='Position'){
  if(!value||typeof value!=='object')throw new Error(`${label} is required.`);
  return {x:finite(value.x,`${label} x`),y:finite(value.y,`${label} y`)};
}
function symbol(value){if(typeof value!=='string'||!/^[A-Z][a-z]?$/.test(value))throw new Error('Invalid chemical element.');return value;}
function kind(value){if(!BOND_KINDS.has(value))throw new Error('Unsupported bond tool.');return value;}
function candidate(input){
  const graph=cloneData(input);
  if(!graph||!Array.isArray(graph.atoms)||!Array.isArray(graph.bonds))throw new Error('Invalid chemical graph.');
  return graph;
}
function atomById(graph,id){const atom=graph.atoms.find(a=>a.id===id);if(!atom)throw new Error('The targeted atom no longer exists.');return atom;}
function bondById(graph,id){const bond=graph.bonds.find(b=>b.id===id);if(!bond)throw new Error('The targeted bond no longer exists.');return bond;}
function result(input,graph,details={}){
  return {graph,changed:JSON.stringify(input)!==JSON.stringify(graph),reason:null,createdAtomIds:[],createdBondIds:[],...details};
}
function identityFactory(graph,makeId){
  const used=new Set([...graph.atoms,...graph.bonds].map(item=>item.id));
  return prefix=>{
    if(typeof makeId!=='function')throw new Error('An identity generator is required for new entities.');
    const id=makeId(prefix);
    if(typeof id!=='string'||!id.length||id.length>80||used.has(id))throw new Error('The new entity requires a unique valid identifier.');
    used.add(id);return id;
  };
}
function atomRecord(point,element,id){
  const p=position(point);
  return {id,symbol:element,x:p.x,y:p.y,charge:0,isotope:0,explicitHs:0,noImplicit:false,aromatic:false,chiralTag:0,radicals:0,mapNum:0};
}
function bondRecord(begin,end,value,id){
  kind(value);
  return {id,begin,end,order:['wedge','dash'].includes(value)?1:Number(value),direction:value==='wedge'?1:value==='dash'?2:0,stereo:0,stereoAtoms:[]};
}
function touchStereo(graph,ids){
  const touched=new Set(ids);
  for(const atom of graph.atoms)if(touched.has(atom.id))atom.chiralTag=0;
}
function selectedIds(graph,ids,entity){
  if(!Array.isArray(ids))throw new Error('Selected entity identifiers must be an array.');
  const unique=[...new Set(ids)];
  for(const id of unique)(entity==='atom'?atomById:bondById)(graph,id);
  return unique;
}

export function editAtom(input,{atomId=null,position:point,symbol:element='C',makeId}={}){
  const graph=candidate(input);symbol(element);
  if(atomId!==null){
    const atom=atomById(graph,atomId);
    // Match the atom pointer tool: identity, coordinates, charge, isotope,
    // mapping and existing chiral tag are preserved for backend validation.
    Object.assign(atom,{symbol:element,explicitHs:0,noImplicit:false,aromatic:false,radicals:0});
    return result(input,graph,{atomId});
  }
  const p=position(point),id=identityFactory(graph,makeId)('a');
  graph.atoms.push(atomRecord(p,element,id));
  return result(input,graph,{atomId:id,createdAtomIds:[id]});
}

export function createBond(input,{startAtomId=null,startPosition,endAtomId=null,endPosition,endSymbol='C',kind:value='1',freeAngle=false,makeId}={}){
  const graph=candidate(input);kind(value);symbol(endSymbol);
  if(typeof freeAngle!=='boolean')throw new Error('The free-angle choice must be boolean.');
  const start=startAtomId!==null?atomById(graph,startAtomId):position(startPosition,'Bond start');
  const end=endAtomId!==null?atomById(graph,endAtomId):position(endPosition,'Bond end');
  if(startAtomId!==null&&endAtomId===startAtomId)return result(input,graph,{reason:'same-atom',atomId:endAtomId});
  if(startAtomId!==null&&endAtomId!==null){
    const existing=graph.bonds.find(b=>(b.begin===startAtomId&&b.end===endAtomId)||(b.end===startAtomId&&b.begin===endAtomId));
    if(existing)return result(input,graph,{reason:'already-bonded',atomId:endAtomId,bondId:existing.id});
  }
  const make=identityFactory(graph,makeId),createdAtomIds=[];
  let first=startAtomId,second=endAtomId;
  if(first===null){first=make('a');graph.atoms.push(atomRecord(start,'C',first));createdAtomIds.push(first);}
  if(second===null){
    const dx=end.x-start.x,dy=end.y-start.y,length=Math.hypot(dx,dy);
    let angle=length<.15?Math.PI/6:Math.atan2(dy,dx);
    if(!freeAngle)angle=Math.round(angle/(Math.PI/6))*Math.PI/6;
    const distance=freeAngle?Math.max(.6,length):1.5;
    const point={x:start.x+distance*Math.cos(angle),y:start.y+distance*Math.sin(angle)};
    position(point,'Bond endpoint');second=make('a');
    graph.atoms.push(atomRecord(point,endSymbol,second));createdAtomIds.push(second);
  }
  const bondId=make('b');
  touchStereo(graph,[first,second]);graph.bonds.push(bondRecord(first,second,value,bondId));
  return result(input,graph,{atomId:second,bondId,createdAtomIds,createdBondIds:[bondId]});
}

export function editBond(input,{bondId,kind:value='1'}={}){
  const graph=candidate(input);setBondKind(graph,bondId,value);
  return result(input,graph,{bondId});
}

export function placeRing(input,{position:point,atomId=null,bondId=null,type='benzene',makeId}={}){
  const graph=candidate(input);
  if(!RING_TYPES.has(type))throw new Error('Unsupported ring type.');
  const n=type==='benzene'?6:Number(type),aromatic=type==='benzene';
  const anchor=atomId!==null?atomById(graph,atomId):null;
  const existingBond=bondId!==null&&!anchor?bondById(graph,bondId):null;
  const p=anchor?null:position(point,'Ring position');
  if(existingBond&&existingBond.order>2)throw new Error('Fuse rings to a single or double bond.');
  const make=identityFactory(graph,makeId),vertices=[],createdAtomIds=[],createdBondIds=[];
  const add=point=>{const a=atomRecord(point,'C',make('a'));graph.atoms.push(a);createdAtomIds.push(a.id);return a;};
  if(existingBond){
    const a=atomById(graph,existingBond.begin),b=atomById(graph,existingBond.end);
    vertices.push(a,b);
    let dx=b.x-a.x,dy=b.y-a.y;
    const cross=dx*(p.y-a.y)-dy*(p.x-a.x),turn=(cross>=0?1:-1)*2*Math.PI/n;
    let last=b;
    for(let i=2;i<n;i++){
      const nx=dx*Math.cos(turn)-dy*Math.sin(turn),ny=dx*Math.sin(turn)+dy*Math.cos(turn);
      dx=nx;dy=ny;last=add({x:last.x+dx,y:last.y+dy});vertices.push(last);
    }
  }else{
    const radius=1.5/(2*Math.sin(Math.PI/n));
    // Six-membered rings have top/bottom vertices. Five-membered rings reflect
    // their previous default in x. Bond-fused placement above is unchanged.
    const offsets=Array.from({length:n},(_,i)=>{
      const angle=(n===6?7*Math.PI/6:Math.PI)+i*2*Math.PI/n;
      return {x:(n===5?-1:1)*radius*Math.cos(angle),y:radius*Math.sin(angle)};
    });
    let start=0;
    if(anchor&&(n===5||n===6)){
      for(let i=1;i<n;i++)if(offsets[i].x<offsets[start].x-1e-10||
        (Math.abs(offsets[i].x-offsets[start].x)<=1e-10&&offsets[i].y<offsets[start].y))start=i;
    }
    const center=anchor?(n===5||n===6?{x:anchor.x-offsets[start].x,y:anchor.y-offsets[start].y}:{x:anchor.x+radius,y:anchor.y}):p;
    for(let i=0;i<n;i++){
      const offset=offsets[(start+i)%n];
      vertices.push(i===0&&anchor?anchor:add({x:center.x+offset.x,y:center.y+offset.y}));
    }
  }
  for(let i=0;i<n;i++){
    if(existingBond&&i===0)continue;
    const order=aromatic?((i+(existingBond?.order===2?1:0))%2===0?1:2):1;
    const id=make('b');graph.bonds.push(bondRecord(vertices[i].id,vertices[(i+1)%n].id,String(order),id));createdBondIds.push(id);
  }
  touchStereo(graph,vertices.map(a=>a.id));
  return result(input,graph,{atomId:anchor?.id??createdAtomIds[0],bondId:existingBond?.id??createdBondIds[0],createdAtomIds,createdBondIds});
}

export function deleteEntities(input,{atomIds=[],bondIds=[]}={}){
  const graph=candidate(input),removed=new Set(selectedIds(graph,atomIds,'atom')),bids=new Set(selectedIds(graph,bondIds,'bond'));
  const touched=graph.bonds.filter(b=>removed.has(b.begin)||removed.has(b.end)||bids.has(b.id)).flatMap(b=>[b.begin,b.end]);
  graph.atoms=graph.atoms.filter(a=>!removed.has(a.id));
  graph.bonds=graph.bonds.filter(b=>!removed.has(b.begin)&&!removed.has(b.end)&&!bids.has(b.id));
  touchStereo(graph,touched);
  for(const bond of graph.bonds)if(bond.stereoAtoms?.some(id=>removed.has(id))){bond.stereo=0;bond.stereoAtoms=[];}
  return result(input,graph);
}

export function translateAtoms(input,{atomIds=[],dx=0,dy=0}={}){
  const graph=candidate(input),selected=new Set(selectedIds(graph,atomIds,'atom'));
  finite(dx,'Horizontal displacement');finite(dy,'Vertical displacement');
  for(const atom of graph.atoms)if(selected.has(atom.id)){
    atom.x=finite(atom.x+dx,'Translated x');atom.y=finite(atom.y+dy,'Translated y');
  }
  return result(input,graph);
}
