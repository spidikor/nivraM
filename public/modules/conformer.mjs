import {validateStereoDenial,STEREO_MESSAGE} from './stereo-guidance.mjs';
import {validateGeneratedHydrogens,generatedDisplayModel} from './generated-hydrogens.mjs';
import {sourceBinding,sameSource,sameJson} from './conformer-source.mjs';
/** Local generated geometry has a distinct, validated transport from supplied MOL. */
const fail=message=>{throw new Error(message);};
export function validateGeneratedResult(result,request){
  if(result?.sourceHash!==request.sourceHash||result?.sourceRevision!==request.sourceRevision||result?.sourceContextHash!==request.sourceContextHash)fail('Generated result does not match the current source.');
  const scene=result.scene;
  if(scene?.kind!=='generated_coordinates'||typeof scene.sourceText!=='string'||scene.sourceText.length>262144||!scene.sourceText.includes('M  END'))fail('Invalid generated MOL result.');
  if(!Array.isArray(scene.atoms)||scene.atoms.length<1||scene.atoms.length>256||!Array.isArray(scene.bonds)||scene.bonds.length>512)fail('Invalid generated scene bounds.');
  for(const [i,a] of scene.atoms.entries())if(a.id!==i+1||typeof a.element!=='string'||!/^([A-Z][a-z]?)$/.test(a.element)||['x','y','z'].some(k=>!Number.isFinite(a[k])||Math.abs(a[k])>10000))fail('Invalid generated atom.');
  const pairs=new Set();
  for(const [i,b] of scene.bonds.entries()){
    if(b.id!==i+1||![b.a,b.b].every(n=>Number.isInteger(n)&&n>=1&&n<=scene.atoms.length)||b.a===b.b||![1,1.5,2,3,4].includes(b.order))fail('Invalid generated bond.');
    const pair=[b.a,b.b].sort((a,b)=>a-b).join(':');if(pairs.has(pair))fail('Duplicate generated bond.');pairs.add(pair);
  }
  if(result.provenance?.generated!==true||result.provenance?.origin!=='generated_coordinates'||result.provenance?.sourceRevision!==request.sourceRevision||result.provenance?.sourceSha256!==request.sourceHash)fail('Generated provenance is missing its source binding.');
  if(result.provenance.sourceContextSha256!==request.sourceContextHash||!sameJson(result.provenance.sourceContext,JSON.parse(request.sourceContextText)))fail('Generated source context does not match the copied document.');
  if(scene.generatedHydrogens){validateGeneratedHydrogens(scene.generatedHydrogens,scene.atoms.length,result.provenance.outputSha256);generatedDisplayModel(scene);}
  return result;
}
export function createConformerController({getDocument,request,cryptoApi=globalThis.crypto,onState=()=>{},onResult=()=>{},onInvalidate=()=>{},onDenial=()=>{},onClearGuidance=()=>{},timeoutMs=25000}){
  let sequence=0,pending=null,acceptedBinding=null,unsubscribe=null,destroyed=false;
  const hex=bytes=>Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
  const id=()=>hex(cryptoApi.getRandomValues(new Uint8Array(16)));
  const clientId=id();
  function cancel(reason='Generation cancelled.'){
    sequence++;
    const job=pending;pending=null;
    if(job){clearTimeout(job.timer);job.controller.abort();if(job.payload)request('conformer/cancel',{clientId,jobId:job.jobId,expiresAt:job.expiresAt}).catch(()=>{});onState('cancelled',reason);}
  }
  function invalidate(reason){cancel(reason);if(acceptedBinding!==null){acceptedBinding=null;onInvalidate(reason);}}
  async function generate({expectedBinding=null,expectedDocument=null}={}){
    onClearGuidance();cancel();const ticket=sequence,job={controller:new AbortController(),jobId:id(),expiresAt:Date.now()+timeoutMs,payload:null};pending=job;
    job.timer=setTimeout(()=>cancel('Local generation timed out.'),timeoutMs);onState('pending','Generating a bounded local conformer…');
    try{
      const doc=getDocument();if(!doc)fail('The 2D document is not ready.');
      // Continue carries its accepted receipt across readiness, rather than
      // recapturing whichever drawing happens to exist after the await.
      const expected=expectedBinding?Object.freeze({revision:expectedBinding.revision,graphText:expectedBinding.graphText,sourceContextText:expectedBinding.sourceContextText}):null;
      if(expectedDocument&&expectedDocument!==doc||expected&&!sameSource(doc.getSnapshot(),expected))fail('The accepted image drawing changed. Use Generate from current drawing instead.');
      if(expected)job.binding=expected;
      await doc.ready;
      if(destroyed||pending!==job||ticket!==sequence)return false;
      if(!unsubscribe)unsubscribe=doc.subscribe(snapshot=>{if((pending?.binding&&!sameSource(snapshot,pending.binding))||(acceptedBinding&&!sameSource(snapshot,acceptedBinding)))invalidate('The 2D graph or image provenance changed. Generate again from the current drawing.');});
      if(expected&&getDocument()!==doc||expected&&!sameSource(doc.getSnapshot(),expected))fail('The accepted image drawing changed while becoming ready. Use Generate from current drawing instead.');
      const binding=expected||sourceBinding(doc.getSnapshot());job.binding=binding;job.revision=binding.revision;
      const {graphText,sourceContextText}=binding;
      const sourceHash=hex(new Uint8Array(await cryptoApi.subtle.digest('SHA-256',new TextEncoder().encode(graphText))));
      if(destroyed||pending!==job||ticket!==sequence)return false;
      if(expected&&getDocument()!==doc||!sameSource(doc.getSnapshot(),binding)){invalidate('The 2D graph or image provenance changed. Generate again.');return false;}
      const sourceContextHash=hex(new Uint8Array(await cryptoApi.subtle.digest('SHA-256',new TextEncoder().encode(sourceContextText))));
      if(destroyed||pending!==job||ticket!==sequence)return false;
      if(expected&&getDocument()!==doc||!sameSource(doc.getSnapshot(),binding)){invalidate('The 2D graph or image provenance changed. Generate again.');return false;}
      job.payload={graphText,sourceContextText,sourceContextHash,sourceRevision:job.revision,sourceHash,clientId,jobId:job.jobId,expiresAt:job.expiresAt};
      const result=await request('conformer',job.payload,{signal:job.controller.signal});
      if(destroyed||pending!==job||ticket!==sequence)return false;
      if(expected&&getDocument()!==doc||!sameSource(doc.getSnapshot(),binding)){invalidate('The 2D graph or image provenance changed. Generate again.');return false;}
      if(Object.hasOwn(result||{},'denial')){
        validateStereoDenial(result,job.payload);
        if(getDocument()!==doc||!sameSource(doc.getSnapshot(),binding))return false;
        onDenial({result,request:job.payload,binding});onState('error',STEREO_MESSAGE+' Unspecified sites must be resolved before generation. Previous valid view is retained.');return false;
      }
      validateGeneratedResult(result,job.payload);
      const outputHash=hex(new Uint8Array(await cryptoApi.subtle.digest('SHA-256',new TextEncoder().encode(result.scene.sourceText))));
      if(destroyed||pending!==job||ticket!==sequence)return false;
      if(expected&&getDocument()!==doc||!sameSource(doc.getSnapshot(),binding)){invalidate('The 2D graph or image provenance changed. Generate again.');return false;}
      if(outputHash!==result.provenance.outputSha256)fail('Generated MOL does not match its output provenance.');
      acceptedBinding=binding;onResult(result);onState('ready','Generated local conformer. Not an experimental structure, bound pose or global energy minimum. The 2D drawing is unchanged.');return true;
    }catch(error){if(pending===job&&!destroyed)onState('error',`Generation failed: ${error.message} Previous valid view is retained.`);return false;}
    finally{clearTimeout(job.timer);if(pending===job)pending=null;}
  }
  function assertCurrent(){if(acceptedBinding&&!sameSource(getDocument().getSnapshot(),acceptedBinding)){invalidate('The 2D graph or image provenance changed. Generate again.');return false;}return true;}
  return {generate,cancel,invalidate,assertCurrent,exportContext(){if(!acceptedBinding||!assertCurrent())return null;return Object.freeze({sourceContextText:acceptedBinding.sourceContextText});},destroy(){destroyed=true;invalidate('3D view closed.');unsubscribe?.();}};
}
