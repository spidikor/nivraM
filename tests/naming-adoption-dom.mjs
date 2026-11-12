// Pure Node controlled DOM/fetch integration, not browser or rendering evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
class Node extends EventTarget {
  constructor(){super();this.textContent='';this.value='';this.children=[];this.open=false;}
  replaceChildren(...children){this.children=children;} setAttribute(){} removeAttribute(){} close(){this.open=false;}
}
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
const syntheticRecords=()=>['[13CH3]CO','[CH3]','CCO'].flatMap((smiles,index)=>['unsupported','round_trip_matched','generated_unverified','mismatch'].filter(status=>index===2||status!=='generated_unverified').map(status=>{
  const mandatory=index!==2,candidate=index===1?'carbon':'ethanol',accepted=status==='round_trip_matched'||status==='generated_unverified';
  return {smiles,mandatory,candidate,mode:status,graph:{atoms:[{id:'a',symbol:'C',isotope:index===0?13:0,radicalElectrons:index===1?1:0,x:0,y:0}],bonds:[]},result:{status,name:accepted?candidate:null,generatedName:accepted||!mandatory?candidate:null,warnings:['Controlled verification diagnostic.'],roundTrip:{status:accepted?'matched':'unavailable',warnings:[]},engine:{name:'controlled'}}};
}));

test('actual naming entrypoint adopts HTTP receipts without changing document and rejects late results',async()=>{
  // Optional receipts come from test_naming_adoption_routes.py, not model inference.
  const records=process.env.NIVRAM_NAMING_RECEIPTS?JSON.parse(readFileSync(process.env.NIVRAM_NAMING_RECEIPTS,'utf8')):syntheticRecords();
  assert.ok(records.length>=9);
  const old={window:globalThis.window,document:globalThis.document,fetch:globalThis.fetch};
  const nodes=new Map(),find=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
  const view=new EventTarget();view.MutationObserver=class{observe(){}disconnect(){}};
  let revision=17,documentState={graph:structuredClone(records[0].graph)},history=[{label:'original drawing'}];
  const listeners=new Set(),pending=[],cancels=[];
  view.nivramDocument={ready:Promise.resolve(),getSnapshot:()=>({revision,document:documentState}),subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn);}};
  globalThis.window=view;
  globalThis.document={getElementById:find,defaultView:view,documentElement:{dataset:{}},querySelector:()=>({content:'controlled-session'})};
  globalThis.fetch=(url,options)=>{
    if(url==='/api/name')return new Promise(resolve=>pending.push({resolve,body:JSON.parse(options.body)}));
    if(url==='/api/cancel-job'){cancels.push(JSON.parse(options.body));return Promise.resolve({ok:true,json:async()=>({status:'finished'})});}
    return Promise.resolve({ok:false,json:async()=>({error:'Unrelated fixture endpoint unavailable.'})});
  };
  const reply=(index,result,changes={})=>{
    const request=pending[index];request.resolve({ok:true,json:async()=>({...result,clientId:request.body.clientId,jobId:request.body.jobId,inputIdentity:{documentRevision:request.body.documentRevision},...changes})});
  };
  try{
    await import('../public/modules/analysis.mjs?naming-adoption');await tick();
    for(const record of records){
      documentState={graph:structuredClone(record.graph)};
      const before=JSON.stringify({revision,documentState,history});
      const i=pending.length,promise=find('calculate-name').onclick();
      assert.deepEqual(pending[i].body.graph,documentState.graph);
      reply(i,record.result);await promise;
      const expected=record.result.name||'No accepted name';
      assert.equal(find('name-result').textContent,expected,`${record.smiles}/${record.mode}`);
      if(record.mandatory&&record.mode!=='matched'&&record.result.status!=='round_trip_matched'){
        assert.equal(find('name-result').textContent,'No accepted name');
        assert.equal(find('name-result').textContent.includes(record.candidate),false);
        assert.equal(find('name-details').textContent.includes(record.candidate),false);
      }
      for(const warning of record.result.warnings)assert.ok(find('name-details').textContent.includes(warning));
      assert.equal(JSON.stringify({revision,documentState,history}),before);
    }
    const success={status:'round_trip_matched',name:'current accepted',generatedName:'current accepted',warnings:[],roundTrip:{status:'matched'},engine:{name:'controlled'}};
    let i=pending.length,promise=find('calculate-name').onclick();
    find('cancel-name').onclick();reply(i,success);await promise;await tick();
    assert.equal(find('name-result').textContent,'Cancelled');
    i=pending.length;const older=find('calculate-name').onclick(),newer=find('calculate-name').onclick();
    reply(i+1,success);await newer;reply(i,{...success,name:'late obsolete'});await older;await tick();
    assert.equal(find('name-result').textContent,'current accepted');
    i=pending.length;promise=find('calculate-name').onclick();revision++;
    for(const fn of listeners)fn({revision,document:documentState});
    reply(i,{...success,name:'late obsolete'});await promise;await tick();
    assert.equal(find('name-result').textContent,'Calculate a name for the current structure.');
    for(const difference of ['jobId','revision']){
      i=pending.length;promise=find('calculate-name').onclick();
      reply(i,success,difference==='jobId'?{jobId:'wrong-job'}:{inputIdentity:{documentRevision:revision-1}});await promise;
      assert.match(find('name-result').textContent,/does not match/);
    }
    assert.ok(cancels.length>=3);
    assert.deepEqual(history,[{label:'original drawing'}]);
    view.dispatchEvent(new Event('nivram-close-review'));view.dispatchEvent(new Event('pagehide'));
  }finally{Object.assign(globalThis,old);}
});
