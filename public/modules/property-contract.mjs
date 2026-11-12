// Data-only admission. Matching a source and a schema does not verify chemistry.
import {MASS_MESSAGE,validMassUnavailable} from './mass-contract.mjs';
export const PROPERTY_SCHEMA='nivram-properties/1';
const fail=()=>{throw new Error('Invalid or mismatched molecular property report.');};
const require=ok=>{if(!ok)fail();};
const plain=v=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&[Object.prototype,null].includes(Object.getPrototypeOf(v));
const exact=(v,keys)=>plain(v)&&Object.keys(v).sort().join('|')===[...keys].sort().join('|');
const text=(v,n,empty=false)=>typeof v==='string'&&v.length<=n&&(empty||v.length>0);
const finite=v=>typeof v==='number'&&Number.isFinite(v);
const MULTI='Multiple disconnected fragments are present. Select an analyte explicitly; a whole-mixture LogP is not calculated.';
const CHARGED='The as-drawn structure contains charged atoms. Its fragment descriptor is not a validated neutral-species partition coefficient or pH-dependent LogD.';
const RADICAL='Radical LogP is outside the evaluated application domain.';
const NONFINITE='The descriptor engine returned a nonfinite result.';
const DOMAIN=new Set(['H','C','N','O','F','P','S','Cl','Br','I']);
const DESCRIPTORS={
  molecularWeight:{unit:'g/mol',label:'Molecular weight / molar mass',method:'RDKit average atomic weights, with explicit isotope labels'},
  exactMass:{unit:'Da',label:'Exact mass',method:'RDKit CalcExactMolWt; explicit isotopes and formal charge retained'},
  logP:{unit:'dimensionless',label:'Calculated LogP - RDKit Wildman-Crippen',method:'Wildman-Crippen atom-fragment estimate',applicability:'As-drawn structure only; not experimental LogP or LogD'}
};
export function boundedPropertyData(value){
  const pending=[[value,0]];let count=0;
  while(pending.length){
    const [v,depth]=pending.pop();require(++count<=250000&&depth<=64);
    if(Array.isArray(v)){for(const child of v)pending.push([child,depth+1]);}
    else if(plain(v)){for(const [key,child] of Object.entries(v)){require(!['__proto__','constructor','prototype'].includes(key));pending.push([child,depth+1]);}}
    else require(v===null||typeof v==='boolean'||typeof v==='string'||finite(v));
  }
  require(new TextEncoder().encode(JSON.stringify(value)).length<=8*1024*1024);
  return value;
}
export function sameJson(a,b){
  if(a===b)return true;
  if(Array.isArray(a))return Array.isArray(b)&&a.length===b.length&&a.every((v,i)=>sameJson(v,b[i]));
  if(!plain(a)||!plain(b))return false;
  const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(k=>Object.hasOwn(b,k)&&sameJson(a[k],b[k]));
}
function graphFacts(graph){
  require(plain(graph)&&Array.isArray(graph.atoms)&&Array.isArray(graph.bonds)&&graph.atoms.length<=512&&graph.bonds.length<=1024);
  const links=new Map();let charge=0,charged=false,radicals=false;const outside=new Set();
  for(const a of graph.atoms){
    require(plain(a)&&text(a.id,80)&&!links.has(a.id)&&text(a.symbol,3));
    const c=Object.hasOwn(a,'charge')?a.charge:0,r=Object.hasOwn(a,'radicals')?a.radicals:0;require(Number.isInteger(c)&&Math.abs(c)<=8&&Number.isInteger(r)&&r>=0&&r<=8);
    links.set(a.id,[]);charge+=c;charged||=c!==0;radicals||=r!==0;if(!DOMAIN.has(a.symbol))outside.add(a.symbol);
  }
  for(const b of graph.bonds){require(plain(b)&&links.has(b.begin)&&links.has(b.end));links.get(b.begin).push(b.end);links.get(b.end).push(b.begin);}
  const unseen=new Set(links.keys());let fragments=0;
  while(unseen.size){fragments++;const first=unseen.values().next().value;unseen.delete(first);const todo=[first];while(todo.length)for(const n of links.get(todo.pop()))if(unseen.delete(n))todo.push(n);}
  return {empty:graph.atoms.length===0,fragments,charge,charged,radicals,outside:[...outside].sort()};
}
export function validatePropertyIdentity(identity,revision){
  require(exact(identity,['graphHash','structureHash','smiles','documentRevision']));
  for(const key of ['graphHash','structureHash'])require(typeof identity[key]==='string'&&/^[0-9a-f]{64}$/.test(identity[key]));
  require(text(identity.smiles,65536,true));
  const r=identity.documentRevision;require((r===null||Number.isSafeInteger(r)||typeof r==='string'&&r.length<=120)&&r===revision);
  return identity;
}
function validateSemantics(report,graph){
  const facts=graphFacts(graph),empty=facts.empty,unavailable=Object.hasOwn(report,'massUnavailable');
  require(report.status===(empty?'empty':'calculated'));
  require(text(report.formula,65536,empty)&&Boolean(report.formula)!==empty);
  require(Number.isInteger(report.formalCharge)&&report.formalCharge===facts.charge&&Number.isInteger(report.fragmentCount)&&report.fragmentCount===facts.fragments);
  if(unavailable)require(!empty&&validMassUnavailable(report.massUnavailable,graph));
  for(const key of ['molecularWeight','exactMass'])require(empty||unavailable?report[key]===null:finite(report[key])&&report[key]>=0);
  const l=report.logP;require(exact(l,['value','status','warnings'])&&['calculated','calculated_with_warning','not_applicable'].includes(l.status));
  require(l.status==='not_applicable'?l.value===null:finite(l.value));
  require(Array.isArray(l.warnings)&&l.warnings.length<=32&&l.warnings.every(w=>text(w,2000)));
  const expected=[];if(facts.fragments>1)expected.push(MULTI);
  // Native sanitization may introduce charge separation (e.g. neutral nitro).
  const charged=facts.charged||l.warnings.includes(CHARGED);if(charged)expected.push(CHARGED);
  const radical=facts.radicals;if(radical)expected.push(RADICAL);
  if(facts.outside.length)expected.push('LogP is outside the evaluated element domain: '+facts.outside.join(', ')+'.');
  let inapplicable=empty||facts.fragments!==1||radical||facts.outside.length>0;
  if(l.warnings.includes(NONFINITE)){require(!inapplicable);expected.push(NONFINITE);inapplicable=true;}
  require(sameJson(l.warnings,expected));
  require(l.status===(inapplicable?'not_applicable':charged?'calculated_with_warning':'calculated'));
  require(sameJson(report.warnings,[...l.warnings,...(unavailable?[MASS_MESSAGE]:[])]));
}
export function validatePropertySummary(report,graph){
  boundedPropertyData(report);validateSemantics(report,graph);return report;
}
export function validateProperties(report,graph,{revision=null,expectedIdentity=null}={}){
  boundedPropertyData(report);
  const unavailable=plain(report)&&Object.hasOwn(report,'massUnavailable');
  require(exact(report,['status','inputIdentity','engine','calculatedAt','transformations','formula','smiles','formalCharge','fragmentCount','molecularWeight','exactMass','logP','warnings',...(unavailable?['massUnavailable']:[])]));
  validatePropertyIdentity(report.inputIdentity,revision);
  if(expectedIdentity!==null){validatePropertyIdentity(expectedIdentity,revision);require(sameJson(report.inputIdentity,expectedIdentity));}
  require(report.smiles===report.inputIdentity.smiles&&Boolean(report.smiles)===(graph.atoms.length>0));
  require(Array.isArray(report.transformations)&&report.transformations.length===0);
  require(exact(report.engine,['name','version'])&&report.engine.name==='RDKit'&&text(report.engine.version,80)&&text(report.calculatedAt,80));
  for(const [key,constants] of Object.entries(DESCRIPTORS)){
    const d=report[key];require(exact(d,['value',...Object.keys(constants),...(key==='logP'?['methodVersion','status','warnings']:[])]));
    for(const [field,value] of Object.entries(constants))require(d[field]===value);
    if(key==='logP')require(text(d.methodVersion,80));
  }
  validateSemantics({...report,molecularWeight:report.molecularWeight.value,exactMass:report.exactMass.value,logP:{value:report.logP.value,status:report.logP.status,warnings:report.logP.warnings}},graph);
  return report;
}
export function validatePropertyEnvelope(envelope,request){
  boundedPropertyData(envelope);
  require(exact(envelope,['schema','requestId','sourceGraph','sourceIdentity','properties'])&&envelope.schema===PROPERTY_SCHEMA&&envelope.requestId===request.requestId);
  require(sameJson(envelope.sourceGraph,request.graph));
  validatePropertyIdentity(envelope.sourceIdentity,request.documentRevision);
  return validateProperties(envelope.properties,request.graph,{revision:request.documentRevision,expectedIdentity:envelope.sourceIdentity});
}
