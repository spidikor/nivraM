import assert from 'node:assert/strict';
import {webcrypto,createHash} from 'node:crypto';
import {projectSourceContext,sourceBinding,sameSource} from '../public/modules/conformer-source.mjs';
import {createConformerController,validateGeneratedResult} from '../public/modules/conformer.mjs';
import {createDocumentStore} from '../public/modules/document.mjs';
const sha=s=>createHash('sha256').update(s).digest('hex');
const input={kind:'png-optical-reconstruction',sourceSha256:'a'.repeat(64),sourceName:'/private/person/image.png',sourceStorage:'/private/profile',warnings:['Optical recognition can misread stereochemistry.','The score is uncalibrated.'],imageCorrespondence:'user-reviewed; not independently verified',recognition:{method:'MolScribe',modelSha256:'b'.repeat(64),inputSha256:'c'.repeat(64),confidence:.8,confidenceMeaning:'uncalibrated model score',coordinates:'preserved-from-recognized-molfile',secretPath:'/private/model'},reviewedRegion:{kind:'crop',requestId:'private-session',sourceSha256:'a'.repeat(64),pngSha256:'c'.repeat(64),sourceWidth:100,sourceHeight:100,width:80,height:80,rect:{x:10,y:10,width:80,height:80},scale:'one-source-pixel-per-image-pixel',metadataCorrespondence:'unverified optical recognition of this crop'},correction:{kind:'user-edited-graph',baselineGraph:{private:'graph'},reviewedGraph:{private:'graph'},imageCorrespondence:'user-reviewed; not independently verified'},suppliedGraph:{private:'graph'}};
const project={schema:'nivram/1',graph:{atoms:[{id:'a',symbol:'C',x:0,y:0}],bonds:[]},analysis:{imageImport:input}};
const context=projectSourceContext(project),text=JSON.stringify(context);
for(const word of ['sourceName','sourceStorage','secretPath','requestId','baselineGraph','reviewedGraph','suppliedGraph','/private'])assert(!text.includes(word));
assert.deepEqual(context.imageImport.warnings,input.warnings);assert.equal(context.imageImport.recognition.confidence,.8);assert.deepEqual(context.imageImport.reviewedRegion.rect,input.reviewedRegion.rect);assert.equal(context.imageImport.correction.kind,'user-edited-graph');assert.match(context.currentGraphRelationship,/does not verify the current graph/);
assert.equal(projectSourceContext({}),null);assert.equal(projectSourceContext({analysis:{imageImport:null}}),null);
assert.deepEqual(Object.keys(projectSourceContext({analysis:{imageImport:{kind:input.kind,sourceSha256:input.sourceSha256}}}).imageImport),['kind','sourceSha256']);
for(const alter of [x=>x.sourceSha256='bad',x=>x.kind='arbitrary',x=>x.warnings=Array(33).fill('x'),x=>x.warnings=['x'.repeat(1025)],x=>x.warnings=[{nested:true}],x=>x.recognition.confidence=NaN,x=>x.reviewedRegion.rect.width=0,x=>x.correction.kind='invented',x=>x.warnings=Array(32).fill('😀'.repeat(512))]){const copy=structuredClone(input);alter(copy);assert.throws(()=>projectSourceContext({analysis:{imageImport:copy}}));}
const store=createDocumentStore(project),initial=store.getSnapshot(),binding=sourceBinding(initial);assert(sameSource(initial,binding));const mutated=structuredClone(initial);mutated.document.analysis.imageImport.warnings.push('changed');assert(!sameSource(mutated,binding));mutated.document.analysis.imageImport=structuredClone(input);mutated.document.graph.atoms[0].charge=1;assert(!sameSource(mutated,binding));
let result,submitted;const mol='generated\nM  END\n';
const response=p=>({sourceHash:p.sourceHash,sourceRevision:p.sourceRevision,sourceContextHash:p.sourceContextHash,scene:{kind:'generated_coordinates',atoms:[{id:1,element:'C',x:0,y:0,z:0}],bonds:[],sourceText:mol},provenance:{origin:'generated_coordinates',generated:true,sourceRevision:p.sourceRevision,sourceSha256:p.sourceHash,sourceContext:JSON.parse(p.sourceContextText),sourceContextSha256:p.sourceContextHash,outputSha256:sha(mol)}});
const controller=createConformerController({getDocument:()=>({...store,ready:Promise.resolve()}),cryptoApi:webcrypto,request:async(_path,p)=>{submitted=p;return response(p);},onResult:r=>result=r});
assert(await controller.generate());assert.equal(submitted.sourceContextText,text);assert.equal(submitted.sourceContextHash,sha(text));assert.deepEqual(result.provenance.sourceContext,context);assert.deepEqual(JSON.parse(JSON.stringify(result.provenance)).sourceContext,context);
for(const alter of [r=>r.sourceContextHash='0'.repeat(64),r=>r.provenance.sourceContextSha256='0'.repeat(64),r=>r.provenance.sourceContext.imageImport.warnings.push('forged')]){const r=response(submitted);alter(r);assert.throws(()=>validateGeneratedResult(r,submitted));}controller.destroy();
// A source mutation with unchanged revision, during transport and after completion.
let snapshot=structuredClone(initial),subscriber,release,outputs=0,invalidated=0;
const doc={ready:Promise.resolve(),getSnapshot:()=>snapshot,subscribe:f=>{subscriber=f;return ()=>{};}};
const ctl=createConformerController({getDocument:()=>doc,cryptoApi:webcrypto,request:async(path,p)=>path.endsWith('/cancel')?{}:new Promise(resolve=>release=()=>resolve(response(p))),onResult:()=>outputs++,onInvalidate:()=>invalidated++});
const tick=()=>new Promise(r=>setTimeout(r,0));async function wait(){for(let i=0;i<100&&!release;i++)await tick();assert(release);}
let pending=ctl.generate();await wait();snapshot.document.analysis.imageImport.warnings.push('context changed without revision');release();assert.equal(await pending,false);assert.equal(outputs,0);
release=null;pending=ctl.generate();await wait();release();assert(await pending);assert.equal(outputs,1);snapshot.document.analysis.imageImport.recognition.confidence=.2;subscriber(snapshot);assert.equal(invalidated,1);
release=null;pending=ctl.generate();await wait();release();assert(await pending);snapshot.document.graph.atoms[0].charge=1;assert.equal(ctl.assertCurrent(),false);assert.equal(invalidated,2);ctl.destroy();
console.log('PASS safe scientific PNG projection, strict bounds/privacy exclusions, actual document snapshot consumer, provenance export readback, context binding and same-revision context/graph fencing.');
for(const pauseAt of [1,2,3]){
  let local=structuredClone(initial),digests=0,unblock,delivered=0;
  const cryptoApi={getRandomValues:b=>webcrypto.getRandomValues(b),subtle:{digest:async(...args)=>{digests++;if(digests===pauseAt)await new Promise(r=>unblock=r);return webcrypto.subtle.digest(...args);}}};
  const c=createConformerController({getDocument:()=>({ready:Promise.resolve(),getSnapshot:()=>local,subscribe:()=>()=>{}}),cryptoApi,request:async(path,p)=>path.endsWith('/cancel')?{}:response(p),onResult:()=>delivered++});
  const running=c.generate();for(let i=0;i<100&&!unblock;i++)await tick();assert(unblock);local.document.analysis.imageImport.warnings.push('Changed during digest without revision');unblock();assert.equal(await running,false);assert.equal(delivered,0);c.destroy();
}
console.log('PASS source-context mutation fenced after graph hash, context hash and output hash asynchronous completions.');
