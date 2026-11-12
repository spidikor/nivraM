import {validateGeneratedHydrogens} from './generated-hydrogens.mjs';
/** Untrusted historical records: hashes prove consistency, never generation trust. */
import {parseSuppliedMol} from './supplied-3d.mjs';
import {validateSuppliedEvidence} from './supplied-validation.mjs';
import {projectSourceContext,sameJson} from './conformer-source.mjs';
const enc=new TextEncoder(), bad=()=>{throw new Error('Invalid or excessive 3D coordinate metadata.');};
const need=ok=>{if(!ok)bad();};
const fields=(v,list)=>need(v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===list.length&&list.every(k=>Object.hasOwn(v,k)));
const keys='schema producer version dimension recordedOrigin format units molText molSha256 molBytes provenanceJson provenanceSha256 sourceContextText sourceContextSha256 sourceAtomIds'.split(' ');
const supplied='schema origin format sourceSha256 sourceBytes atomCount bondCount chemistryVerified coordinatesAltered generated experimentalStructureClaim units limitations'.split(' ');
const generated='schema origin generated method engineVersion seed maxIterations threads hydrogens minimization sourceRevision sourceSha256 outputSha256 format canonicalIsomericSmiles chemicalIdentityRoundtripVerified experimentalStructureClaim limitations sourceContext sourceContextSha256'.split(' ');
const hash=async text=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',enc.encode(text))),b=>b.toString(16).padStart(2,'0')).join('');
const validUnicode=s=>!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(s);
// JSON.parse alone silently accepts duplicate object keys. Bound depth before recursion.
export function parseRecordJson(text,bound=1048576){
 need(typeof text==='string'&&validUnicode(text)&&enc.encode(text).length<=bound);let i=0;
 const ws=()=>{while(/[\t\n\r ]/.test(text[i]||'!'))i++;};
 function string(){const start=i++;while(i<text.length){const c=text[i++];if(c==='"'){const s=JSON.parse(text.slice(start,i));need(validUnicode(s));return s;}if(c==='\\')i++;}bad();}
 function value(depth){need(depth<=12);ws();const c=text[i];if(c==='"')return string();
  if(c==='{'||c==='['){i++;const obj=c==='{'?{}:[],seen=new Set(),end=c==='{'?'}':']';ws();if(text[i]===end){i++;return obj;}
   while(true){ws();if(c==='{'){need(text[i]==='"');const key=string();need(!seen.has(key)&&!['__proto__','prototype','constructor'].includes(key));seen.add(key);ws();need(text[i++]===':');obj[key]=value(depth+1);}else obj.push(value(depth+1));ws();const token=text[i++];if(token===end)return obj;need(token===',');}
  }
  const m=/^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(text.slice(i));need(m);i+=m[0].length;const out=JSON.parse(m[0]);need(typeof out!=='number'||Number.isFinite(out));return out;
 }
 const result=value(0);ws();need(i===text.length);return result;
}
export async function validateThreeDRecord(input){
 const r=typeof input==='string'?parseRecordJson(input):parseRecordJson(JSON.stringify(input));const withH=r.schema==='nivram-png-coordinates/6';fields(r,withH?[...keys,'generatedHydrogensJson','generatedHydrogensSha256']:keys);
 need(['nivram-png-coordinates/2','nivram-png-coordinates/3','nivram-png-coordinates/4','nivram-png-coordinates/5','nivram-png-coordinates/6'].includes(r.schema)&&r.producer==='nivraM'&&r.dimension===3&&typeof r.version==='string'&&/^[A-Za-z0-9][A-Za-z0-9.+_-]{0,79}$/.test(r.version));
 async function check(text,digest,bound){need(typeof text==='string'&&validUnicode(text)&&enc.encode(text).length>0&&enc.encode(text).length<=bound&&typeof digest==='string'&&/^[a-f0-9]{64}$/.test(digest));need(await hash(text)===digest);}
 await check(r.molText,r.molSha256,262144);need(r.molBytes===enc.encode(r.molText).length);
 await check(r.provenanceJson,r.provenanceSha256,131072);const p=parseRecordJson(r.provenanceJson,131072),gen=r.recordedOrigin==='generated_coordinates';
 const native=r.schema==='nivram-png-coordinates/4',radical=r.schema==='nivram-png-coordinates/5';need(!radical||!gen);need(!withH||gen);
 need(gen||r.recordedOrigin==='supplied_coordinates');need(!native||!gen&&r.format==='MOL V3000');fields(p,gen?generated:native?[...supplied,'validation']:supplied);need(p.origin===r.recordedOrigin&&p.format===r.format&&p.generated===gen&&p.experimentalStructureClaim===false);
 need(r.schema===(withH?'nivram-png-coordinates/6':radical?'nivram-png-coordinates/5':native?'nivram-png-coordinates/4':r.recordedOrigin==='supplied_coordinates'&&r.format==='MOL V3000'?'nivram-png-coordinates/3':'nivram-png-coordinates/2'));
 const clean=(s,max)=>typeof s==='string'&&s.length<=max&&!/[\x00-\x1f\x7f]/.test(s);
 need(Array.isArray(p.limitations)&&p.limitations.length>=1&&p.limitations.length<=32&&p.limitations.every(s=>clean(s,2048)));
 if(gen){
  need(r.format==='MOL V3000'&&r.units==='angstrom'&&p.schema==='nivram-generated-3d/1'&&typeof p.chemicalIdentityRoundtripVerified==='boolean');
  for(const k of ['method','engineVersion','hydrogens','minimization','canonicalIsomericSmiles'])need(clean(p[k],k==='canonicalIsomericSmiles'?16384:512)&&p[k].length>0);
  for(const k of ['seed','maxIterations','threads','sourceRevision'])need(Number.isInteger(p[k])&&p[k]>=0&&p[k]<=2147483647);
  need(typeof p.sourceSha256==='string'&&/^[a-f0-9]{64}$/.test(p.sourceSha256)&&p.outputSha256===r.molSha256&&p.sourceContextSha256===r.sourceContextSha256);
  await check(r.sourceContextText,r.sourceContextSha256,49152);const context=parseRecordJson(r.sourceContextText,49152);
  need(sameJson(context,p.sourceContext));need(context===null||sameJson(context,projectSourceContext({analysis:{imageImport:context.imageImport}})));
  need(Array.isArray(r.sourceAtomIds)&&r.sourceAtomIds.length>=1&&r.sourceAtomIds.length<=128&&new Set(r.sourceAtomIds).size===r.sourceAtomIds.length&&r.sourceAtomIds.every(s=>clean(s,80)&&s.length>0));
  if(withH){await check(r.generatedHydrogensJson,r.generatedHydrogensSha256,65536);validateGeneratedHydrogens(parseRecordJson(r.generatedHydrogensJson,65536),r.sourceAtomIds.length,r.molSha256);}
 }else{
  need(['MOL V2000','MOL V3000'].includes(r.format)&&r.units==='MOL-convention/unverified'&&r.sourceContextText===null&&r.sourceContextSha256===null&&r.sourceAtomIds===null);
  const model=parseSuppliedMol(enc.encode(r.molText),{allowNativeDeclarations:native});need(model.atoms.some(a=>Object.hasOwn(a,'radicalCode'))===radical);if(native)validateSuppliedEvidence(p.validation);need(r.format===(model.format||'MOL V2000'));need(p.schema===(radical?'nivram-supplied-3d-view/3':native?'nivram-supplied-3d-view/2':'nivram-supplied-3d-view/1')&&p.sourceSha256===r.molSha256&&p.sourceBytes===r.molBytes&&p.atomCount===model.atoms.length&&p.bondCount===model.bonds.length&&p.chemistryVerified===false&&p.coordinatesAltered===false&&p.units==='MOL convention: angstrom; caller declaration, not independently verified');
 }
 return {record:r,provenance:p};
}
export async function createThreeDRecord({model,provenance,sourceContextText=null},version){
 const gen=provenance.origin==='generated_coordinates',molText=model.sourceText,provenanceJson=JSON.stringify(provenance);
 const record={schema:gen&&model.generatedHydrogens?'nivram-png-coordinates/6':!gen&&provenance.schema==='nivram-supplied-3d-view/3'?'nivram-png-coordinates/5':!gen&&provenance.schema==='nivram-supplied-3d-view/2'?'nivram-png-coordinates/4':!gen&&provenance.format==='MOL V3000'?'nivram-png-coordinates/3':'nivram-png-coordinates/2',producer:'nivraM',version,dimension:3,recordedOrigin:provenance.origin,format:provenance.format,units:gen?'angstrom':'MOL-convention/unverified',molText,molSha256:await hash(molText),molBytes:enc.encode(molText).length,provenanceJson,provenanceSha256:await hash(provenanceJson),sourceContextText:gen?sourceContextText:null,sourceContextSha256:gen?await hash(sourceContextText):null,sourceAtomIds:gen?model.atoms.map(a=>a.sourceId):null};
 if(gen&&model.generatedHydrogens){record.generatedHydrogensJson=JSON.stringify(model.generatedHydrogens);record.generatedHydrogensSha256=await hash(record.generatedHydrogensJson);}
 return (await validateThreeDRecord(record)).record;
}
