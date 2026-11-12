// Controlled UI transport fixtures only. No native chemistry or identity calculation.
// Descriptor labels come from the pinned schema fixture; every value/identity below
// is synthetic. Graph witness and mock SMILES digests are internally coherent,
// but are NOT independently generated native/canonical chemistry identities.
import {createHash} from 'node:crypto';
import {DEFAULT_STYLE} from '../public/modules/document.mjs';
import {propertyReport} from './property-response-fixtures.mjs';
const hashText=value=>createHash('sha256').update(value,'utf8').digest('hex');
const hash=value=>hashText(JSON.stringify(value));
const domain=new Set(['H','C','N','O','F','P','S','Cl','Br','I']);
function mockIdentity(graph){
  const structure={atoms:graph.atoms.map(({x,y,...atom})=>atom),bonds:graph.bonds};
  const smiles=graph.atoms.length?'mock-structure-'+hash(structure):'';
  return {graphHash:hash(graph),structureHash:hashText(smiles),smiles,documentRevision:null};
}
function mockProperties(graph,identity){
  const report=propertyReport('empty'),links=new Map(graph.atoms.map(a=>[a.id,[]]));
  for(const bond of graph.bonds){links.get(bond.begin).push(bond.end);links.get(bond.end).push(bond.begin);}
  const unseen=new Set(links.keys());let fragments=0;
  while(unseen.size){fragments++;const first=unseen.values().next().value;unseen.delete(first);const pending=[first];while(pending.length)for(const id of links.get(pending.pop()))if(unseen.delete(id))pending.push(id);}
  const empty=!graph.atoms.length,charged=graph.atoms.some(a=>a.charge),radicals=graph.atoms.some(a=>a.radicals),outside=[...new Set(graph.atoms.map(a=>a.symbol).filter(s=>!domain.has(s)))].sort(),warnings=[];
  if(fragments>1)warnings.push('Multiple disconnected fragments are present. Select an analyte explicitly; a whole-mixture LogP is not calculated.');
  if(charged)warnings.push('The as-drawn structure contains charged atoms. Its fragment descriptor is not a validated neutral-species partition coefficient or pH-dependent LogD.');
  if(radicals)warnings.push('Radical LogP is outside the evaluated application domain.');
  if(outside.length)warnings.push('LogP is outside the evaluated element domain: '+outside.join(', ')+'.');
  const inapplicable=empty||fragments!==1||radicals||outside.length>0;
  Object.assign(report,{status:empty?'empty':'calculated',inputIdentity:identity,engine:{name:'RDKit',version:'synthetic-ui-fixture-not-native'},calculatedAt:'synthetic-fixture',formula:empty?'':'mock-formula',smiles:identity.smiles,formalCharge:graph.atoms.reduce((n,a)=>n+(a.charge??0),0),fragmentCount:fragments,warnings:[...warnings]});
  report.molecularWeight.value=empty?null:42;report.exactMass.value=empty?null:42;
  Object.assign(report.logP,{value:inapplicable?null:0,status:inapplicable?'not_applicable':charged?'calculated_with_warning':'calculated',warnings,methodVersion:'synthetic-fixture'});
  return report;
}
export function boundRenderFixture(action,request,result){
  const {sourceBinding,requestId,documentRevision,...sourceRequest}=structuredClone(request);
  if(sourceBinding!=='nivram-render/1'||!Object.hasOwn(sourceRequest,'graph'))throw new Error('Fixture expected a bound graph request.');
  const graph=structuredClone(result.graph),sourceIdentity=mockIdentity(sourceRequest.graph),outputIdentity=mockIdentity(graph),properties=mockProperties(graph,outputIdentity);
  const scene={...structuredClone(result.scene),style:{...DEFAULT_STYLE,...sourceRequest.style},properties,formula:properties.formula,smiles:properties.smiles,mass:graph.atoms.length?properties.molecularWeight.value:0};
  return {schema:'nivram-render/1',requestId,documentRevision,action,sourceRequest,sourceIdentity,outputIdentity,outputGraphJson:JSON.stringify(graph),graph,scene};
}
