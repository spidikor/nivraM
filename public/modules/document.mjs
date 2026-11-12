import {validateProperties} from './property-contract.mjs';
import {validateOptions} from './export.mjs';
import {isElementSymbol} from './elements.mjs';
import {applyBondPaint} from './bond-paint.mjs';
import {validMassUnavailable} from './mass-contract.mjs';

export const PROJECT_SCHEMA='nivram/1';
export const DEFAULT_STYLE=Object.freeze({bondWidth:4.2,labelSize:36,bondSpacing:0.14,labelPadding:0.04,color:true,hashBondStyle:'engine'});
export const DEFAULT_EXPORT=Object.freeze({width:2000,marginMode:'percent',margin:2,transparentBackground:false});
const SVG_NS='http://www.w3.org/2000/svg';
const plain=value=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&[Object.prototype,null].includes(Object.getPrototypeOf(value));
const known=(value,keys,label)=>{
  if(!plain(value))throw new Error(`${label} must be an object.`);
  for(const key of Object.keys(value))if(!keys.includes(key))throw new Error(`Unsupported ${label} field: ${key}. Nothing was changed.`);
};
const finite=(value,label)=>{if(typeof value!=='number'||!Number.isFinite(value))throw new Error(`${label} must be a finite number.`);};
const id=value=>typeof value==='string'&&value.length>0&&value.length<=80;
export function cloneData(value){
  let count=0;
  function check(item,depth){
    if(++count>100000||depth>32)throw new Error('The project data exceeds the supported size or nesting limit.');
    if(item===null||typeof item==='boolean'||typeof item==='string')return;
    if(typeof item==='number'){finite(item,'Project numbers');return;}
    if(Array.isArray(item)){for(const child of item)check(child,depth+1);return;}
    if(!plain(item))throw new Error('Project data must contain only JSON values.');
    for(const [key,child] of Object.entries(item)){
      if(['__proto__','constructor','prototype'].includes(key))throw new Error('Unsupported project data key.');
      check(child,depth+1);
    }
  }
  check(value,0);return JSON.parse(JSON.stringify(value));
}
const freeze=value=>{if(value&&typeof value==='object'){for(const child of Object.values(value))freeze(child);Object.freeze(value);}return value;};
const signature=value=>JSON.stringify(value,(_key,item)=>plain(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);

export function validateProject(input){
  // Check this user-facing fraction before the generic JSON walk so empty
  // numeric inputs (NaN) receive the same actionable message as range errors.
  const labelPadding=input?.style?.labelPadding;
  if(labelPadding!==undefined&&(typeof labelPadding!=='number'||!Number.isFinite(labelPadding)||labelPadding<0||labelPadding>.20))
    throw new Error('Extra label clearance must be a number from 0% to 20%.');
  const project=cloneData(input);
  known(project,['schema','version','graph','style','export','analysis'],'project');
  if(project.schema!==PROJECT_SCHEMA)throw new Error('This project version is not supported. The original file was not changed.');
  if(project.version!==undefined&&(typeof project.version!=='string'||project.version.length>80))throw new Error('Invalid project application version.');
  known(project.graph,['atoms','bonds'],'chemical graph');
  const {atoms,bonds}=project.graph;
  if(!Array.isArray(atoms)||!Array.isArray(bonds)||atoms.length>512||bonds.length>1024)throw new Error('The project exceeds the supported atom/bond limits.');
  const atomsById=new Set(),bondIds=new Set(),edges=new Set();
  for(const atom of atoms){
    known(atom,['id','symbol','x','y','charge','isotope','explicitHs','noImplicit','aromatic','chiralTag','radicals','mapNum'],'atom');
    if(!id(atom.id)||atomsById.has(atom.id))throw new Error('Every atom requires a unique valid identifier.');
    atomsById.add(atom.id);
    if(!isElementSymbol(atom.symbol))throw new Error('Invalid chemical element.');
    for(const axis of ['x','y']){finite(atom[axis],'Atom coordinates');if(Math.abs(atom[axis])>10000)throw new Error('Atom coordinates exceed the supported range.');}
    for(const [key,lo,hi] of [['charge',-8,8],['isotope',0,400],['explicitHs',0,8],['chiralTag',0,2],['radicals',0,8],['mapNum',0,999999]]){
      if(atom[key]!==undefined&&(!Number.isInteger(atom[key])||atom[key]<lo||atom[key]>hi))throw new Error(`Invalid atom ${key}.`);
    }
    for(const flag of ['aromatic','noImplicit'])if(atom[flag]!==undefined&&typeof atom[flag]!=='boolean')throw new Error(`Invalid atom ${flag}.`);
  }
  for(const bond of bonds){
    known(bond,['id','begin','end','order','direction','stereo','stereoAtoms'],'bond');
    if(!id(bond.id)||bondIds.has(bond.id))throw new Error('Every bond requires a unique valid identifier.');
    bondIds.add(bond.id);
    if(!atomsById.has(bond.begin)||!atomsById.has(bond.end)||bond.begin===bond.end)throw new Error('A bond has an invalid atom reference.');
    const edge=JSON.stringify([bond.begin,bond.end].sort());if(edges.has(edge))throw new Error('Duplicate chemical bond.');edges.add(edge);
    if(![1,1.5,2,3].includes(bond.order))throw new Error('Unsupported bond order.');
    for(const [key,max] of [['direction',6],['stereo',5]])if(bond[key]!==undefined&&(!Number.isInteger(bond[key])||bond[key]<0||bond[key]>max))throw new Error(`Invalid bond ${key}.`);
    const refs=bond.stereoAtoms??[];
    if(!Array.isArray(refs)||![0,2].includes(refs.length)||refs.some(ref=>!atomsById.has(ref)))throw new Error('Invalid bond stereochemistry references.');
  }
  const style={...DEFAULT_STYLE,...(project.style??{})};
  if(project.style!==undefined)known(project.style,Object.keys(DEFAULT_STYLE),'drawing style');
  for(const [key,lo,hi] of [['bondWidth',.5,5],['labelSize',10,40],['bondSpacing',.06,.28]]){finite(style[key],key);if(style[key]<lo||style[key]>hi)throw new Error(`Unsupported ${key}.`);}
  if(!Number.isInteger(style.labelSize)||typeof style.color!=='boolean')throw new Error('Invalid drawing style.');
  if(!['engine','spaced'].includes(style.hashBondStyle))throw new Error('Choose Engine spacing or Thicker, spaced bars for hashed stereobonds.');
  if(project.export!==undefined)known(project.export,Object.keys(DEFAULT_EXPORT),'export settings');
  const options={...DEFAULT_EXPORT,...(project.export??{})};validateOptions(options);
  if(project.analysis!==undefined&&!Array.isArray(project.analysis)&&!plain(project.analysis))throw new Error('Analysis references must be a JSON object or array.');
  return {...project,version:project.version??'0.2.0-dev.1',style,export:options};
}

export function prepareRender(result,baseProject,ownerDocument=globalThis.document){
  if(!plain(result)||!plain(result.scene))throw new Error('The chemistry engine returned an invalid scene.');
  const scene=cloneData(result.scene);
  const project=validateProject({...baseProject,graph:result.graph,style:scene.style});
  if(!Array.isArray(scene.positions)||scene.positions.length!==project.graph.atoms.length)throw new Error('The scene does not match its molecular graph.');
  const expected=new Set(project.graph.atoms.map(atom=>atom.id));
  for(const position of scene.positions){
    if(!plain(position)||!expected.delete(position.id))throw new Error('The scene contains invalid atom positions.');
    finite(position.x,'Scene coordinates');finite(position.y,'Scene coordinates');
  }
  if(!plain(scene.transform))throw new Error('The scene has no valid coordinate transform.');
  for(const key of ['x0','y0','xScale','yScale'])finite(scene.transform[key],'Scene transform');
  if(scene.transform.xScale<=0||scene.transform.yScale>=0)throw new Error('The scene transform has an unsupported orientation.');
  for(const key of ['width','height']){finite(scene[key],'Scene size');if(scene[key]<=0)throw new Error('Invalid scene size.');}
  for(const key of ['formula','smiles'])if(typeof scene[key]!=='string')throw new Error('Invalid scene properties.');
  if(scene.mass===null){
    if(!validMassUnavailable(scene.properties?.massUnavailable,project.graph)||scene.properties?.molecularWeight?.value!==null||scene.properties?.exactMass?.value!==null)throw new Error('Invalid unavailable molecular mass.');
  }else{
    finite(scene.mass,'Molecular mass');
    if(scene.properties&&Object.hasOwn(scene.properties,'massUnavailable'))throw new Error('Inconsistent molecular mass availability.');
  }
  if(Object.hasOwn(scene,'properties')){
    validateProperties(scene.properties,project.graph);
    if(scene.formula!==scene.properties.formula||scene.smiles!==scene.properties.smiles)throw new Error('The scene does not match its property report.');
    const mass=scene.properties.molecularWeight.value;
    if(project.graph.atoms.length===0?scene.mass!==0:mass===null?scene.mass!==null:Math.abs(scene.mass-mass)>.000500001)throw new Error('The scene mass does not match its property report.');
  }
  const fragment=sanitizeSvg(scene.svg,ownerDocument,project.style);
  return {project,scene:freeze(scene),fragment};
}

export function sanitizeSvg(source,ownerDocument=globalThis.document,paintStyle={}){
  if(typeof source!=='string'||source.length>4_000_000||/<!\s*(?:DOCTYPE|ENTITY)/i.test(source))throw new Error('The chemistry engine returned unsupported SVG.');
  const parsed=new ownerDocument.defaultView.DOMParser().parseFromString(source,'image/svg+xml');
  const root=parsed.documentElement;
  if(parsed.querySelector('parsererror')||root.localName!=='svg'||root.namespaceURI!==SVG_NS)throw new Error('The chemistry engine returned invalid SVG.');
  const elements=new Set(['svg','g','path','line','text','tspan','circle','ellipse','polygon','polyline','rect']);
  const attributes=new Set(['id','class','d','x','y','x1','y1','x2','y2','cx','cy','r','rx','ry','width','height','points','transform','viewBox','version','baseProfile','style','fill','stroke','stroke-width','stroke-linecap','stroke-linejoin','stroke-miterlimit','stroke-dasharray','stroke-dashoffset','fill-rule','opacity','fill-opacity','stroke-opacity','font-size','font-family','font-style','font-weight','text-anchor','dominant-baseline','dx','dy','rotate','textLength','lengthAdjust','space']);
  const properties=new Set(['fill','stroke','stroke-width','stroke-linecap','stroke-linejoin','stroke-miterlimit','stroke-dasharray','stroke-dashoffset','fill-rule','opacity','fill-opacity','stroke-opacity','font-size','font-family','font-style','font-weight','text-anchor','dominant-baseline']);
  for(const element of [root,...root.querySelectorAll('*')]){
    if(element.namespaceURI!==SVG_NS||!elements.has(element.localName))throw new Error('Unsupported or unsafe SVG content was rejected.');
    for(const attribute of element.attributes){
      if(attribute.namespaceURI==='http://www.w3.org/2000/xmlns/')continue;
      if(attribute.namespaceURI&&!(attribute.namespaceURI==='http://www.w3.org/XML/1998/namespace'&&attribute.localName==='space'))throw new Error('Unsupported SVG attribute namespace.');
      if(!attributes.has(attribute.localName)||/url\s*\(|javascript:|expression\s*\(|@import|[\\<>]/i.test(attribute.value))throw new Error('Unsupported or unsafe SVG attribute was rejected.');
      if(attribute.localName==='style')for(const declaration of attribute.value.split(';').filter(part=>part.trim())){
        const colon=declaration.indexOf(':');if(colon<0||!properties.has(declaration.slice(0,colon).trim().toLowerCase()))throw new Error('Unsupported SVG styling was rejected.');
      }
    }
  }
  const fragment=ownerDocument.createDocumentFragment();
  for(const child of root.children)fragment.append(ownerDocument.importNode(child,true));
  return applyBondPaint(fragment,paintStyle);
}

export function createDocumentStore(initial,{historyLimit=80}={}){
  let current=freeze(validateProject(initial)),revision=0,past=[],future=[],suspended=false;
  let historyGroup=null;
  let recoveryGeneration;
  let saved=signature(current);
  const revisions=new Map([[0,saved]]),listeners=new Set();
  const snapshot=()=>({document:cloneData(current),revision,dirty:signature(current)!==saved,
    ...(recoveryGeneration===undefined?{}:{recoveryGeneration})});
  const emit=()=>{for(const listener of listeners){try{listener(snapshot());}catch{ /* Observers cannot invalidate a committed document. */ }}};
  return Object.freeze({
    getSnapshot:snapshot,
    setRecoveryGeneration(generation){
      if(typeof generation!=='string'||!/^[0-9a-f]{32}$/.test(generation)||
          (recoveryGeneration!==undefined&&generation!==recoveryGeneration)){
        throw new Error('The recovery identity does not belong to this editor.');
      }
      recoveryGeneration=generation;
      return snapshot();
    },
    historyState:()=>({canUndo:past.length>0,canRedo:future.length>0}),
    historyTarget:(redo=false)=>{const stack=redo?future:past;return stack.length?cloneData(stack.at(-1).document):null;},
    // Transient view context is separate from projects, saving and recovery.
    historyContext:(redo=false)=>{const stack=redo?future:past;return stack.length?cloneData(stack.at(-1).context):null;},
    beginHistoryGroup(){
      if(suspended)throw new Error('Document changes are paused while closing is reviewed.');
      const token=Object.freeze({});historyGroup={token,revision,recorded:false};return token;
    },
    endHistoryGroup(token){if(historyGroup?.token===token)historyGroup=null;},
    commit(candidate,{expectedRevision=revision,mode='record',historyGroup:groupToken=null,historyContext:context=null,apply=()=>{}}={}){
      if(suspended)throw new Error('Document changes are paused while closing is reviewed.');
      if(expectedRevision!==revision)throw new Error('The document changed while this operation was running. Its result was discarded.');
      if(!['record','undo','redo','none'].includes(mode))throw new Error('Invalid document history operation.');
      const group=groupToken===null?null:historyGroup;
      if(groupToken!==null&&(mode!=='record'||group?.token!==groupToken||group.revision!==revision))throw new Error('This editing gesture is no longer current.');
      const next=freeze(validateProject(candidate));
      if((mode==='undo'&&!past.length)||(mode==='redo'&&!future.length))throw new Error('There is no history entry to restore.');
      const changed=signature(next)!==signature(current);
      const entry=Object.freeze({document:current,context:freeze(cloneData(context))});
      // The view is prepared first; no document/history state changes if it fails.
      apply(next,{changed});
      if(groupToken===null)historyGroup=null;
      if(changed||mode==='undo'||mode==='redo'){
        if(mode==='record'){if(!group?.recorded)past=[...past,entry].slice(-historyLimit);future=[];}
        if(mode==='undo'){past=past.slice(0,-1);future=[...future,entry].slice(-historyLimit);}
        if(mode==='redo'){future=future.slice(0,-1);past=[...past,entry].slice(-historyLimit);}
        current=next;revision++;revisions.set(revision,signature(current));
        if(group){group.revision=revision;group.recorded=true;}
        while(revisions.size>historyLimit*2+2)revisions.delete(revisions.keys().next().value);
        emit();
      }
      return snapshot();
    },
    markSaved(savedRevision,savedDocument){
      if(!Number.isInteger(savedRevision)||!revisions.has(savedRevision))return false;
      if(savedDocument===undefined&&savedRevision!==revision)return false;
      const acknowledged=savedDocument===undefined?signature(current):signature(validateProject(savedDocument));
      if(revisions.get(savedRevision)!==acknowledged)return false;
      saved=acknowledged;emit();return true;
    },
    markUnsaved(){
      if(suspended)throw new Error('Document changes are paused while closing is reviewed.');
      // Recovery is a new unsaved file context. An older destination's receipt
      // cannot certify it, even if both files contain identical chemistry.
      historyGroup=null;saved=null;revision++;revisions.clear();revisions.set(revision,signature(current));emit();
    },
    suspendMutations(){historyGroup=null;suspended=true;return snapshot();},
    resumeMutations(){suspended=false;},
    subscribe(listener){if(typeof listener!=='function')throw new Error('A document listener must be a function.');listeners.add(listener);return ()=>listeners.delete(listener);}
  });
}
