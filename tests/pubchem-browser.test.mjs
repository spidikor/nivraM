import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,ready,state} from './editor-browser-fixture.mjs';

const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
async function typeInto(page,locator,text){await locator.click();await page.keyboard.press('Control+A');await page.keyboard.type(text);}
async function drawing(page,smiles='CCO'){
  await page.locator('#import-button').click();await typeInto(page,page.locator('#structure-input'),smiles);
  await page.locator('#load-structure').click();await ready(page);
}
async function naming(page){const details=page.locator('#calculate-name').locator('xpath=ancestor::details');if(!await details.evaluate(el=>el.open))await details.locator('summary').click();}
async function fixture(t,{lookup=null,prepareGate=null,clock=false}={}){
  const page=await productionEditor(t),calls=[],proposals=new Map(),pending=new Set();let sequence=0;
  const clockStart=Date.UTC(2030,0,1);
  if(clock)await page.clock.install({time:clockStart});
  await page.route('**/api/pubchem/*',async route=>{
    const finished=deferred();pending.add(finished.promise);
    try{
    const request=route.request(),action=new URL(request.url()).pathname.split('/').at(-1),body=request.postDataJSON();
    calls.push({action,body,token:request.headers()['x-nivram-token']});
    let result;
    if(action==='prepare'){
      const proposalId=(++sequence).toString(16).padStart(64,'0');
      const now=clock?await page.evaluate(()=>Date.now()):Date.now();
      result={proposalId,expiresAt:now+60000,inputIdentity:{documentRevision:body.documentRevision,graphHash:'a'.repeat(64),structureHash:'b'.repeat(64),smiles:'CCO'},
        disclosure:{version:1,provider:'PubChem (NCBI/NLM)',endpoint:'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/property/IUPACName,SMILES/JSON',
          purpose:'Retrieve a database IUPAC name and compare the returned structure.',cost:'No fee is charged by this application.',submittedSmiles:'CCO',
          warnings:['PubChem may standardize structures. A returned record must match before a name is accepted.']}};
      proposals.set(proposalId,result);if(prepareGate){prepareGate.entered?.resolve();await prepareGate.promise;}
    }else if(action==='cancel')result={status:'cancelled'};
    else if(action==='lookup'){
      const proposal=proposals.get(body.proposalId);assert.ok(proposal,'Lookup must bind a prepared proposal.');
      result={status:'matched',name:'ethanol',nameKind:'retrieved_database_iupac',preferredIUPACName:false,inputIdentity:proposal.inputIdentity,
        provider:'PubChem (NCBI/NLM)',warnings:[],errorCode:null,records:[{CID:702,retrievedAt:'2026-09-22T00:00:00Z',submittedSmiles:'CCO',returnedSmiles:'CCO',returnedName:'ethanol',match:{status:'matched',comparison:'Exact submitted structure comparison.',warnings:[]}}]};
      if(lookup)result=await lookup(result,body);
      if(result.status==='matched')result.records[0].returnedName=result.name;
      if(result.status==='mismatch')result.records[0].match.status='mismatch';
      if(result.status==='unavailable')result.records=[];
    }else throw new Error('Unexpected local route');
    try{await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(result)});}catch(error){if(!/closed|cancel|abort/i.test(error.message))throw error;}
    }finally{pending.delete(finished.promise);finished.resolve();}
  });
  await page.reload();await ready(page);await drawing(page);await naming(page);
  // Install before the fresh navigation; allow normal loading before pausing.
  if(clock)await page.clock.pauseAt(clockStart+3600000);
  async function drainRoutes(){
    let timer;
    try{await Promise.race([Promise.all([...pending]),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Held PubChem fixture route did not settle.')),5000);})]);}
    finally{clearTimeout(timer);}
  }
  return {page,calls,proposals,drainRoutes};
}
const actions=(calls,name)=>calls.filter(row=>row.action===name);
async function finishTimerFixture({page,drainRoutes},gate=null){
  gate?.resolve();
  try{await drainRoutes();}
  finally{await page.clock.resume();await page.unroute('**/api/pubchem/*');}
}
const waitForReview=page=>page.waitForFunction(()=>!document.getElementById('pubchem-send').disabled,null,{polling:50});

test('PubChem stays off by default and review cancellation sends no lookup', {timeout:60000},async t=>{
  const {page,calls}=await fixture(t),before=await state(page);
  assert.equal(await page.locator('#pubchem-lookup').count(),1,'A separate optional PubChem action is required.');
  assert.deepEqual(calls,[]);
  await page.getByRole('button',{name:'Lookup in PubChem',exact:true}).click();
  await page.getByRole('button',{name:'Send to PubChem',exact:true}).waitFor();
  await page.waitForFunction(()=>!document.getElementById('pubchem-send').disabled);
  assert.equal(actions(calls,'prepare').length,1);assert.equal(actions(calls,'lookup').length,0);
  assert.deepEqual(actions(calls,'prepare')[0].body,{graph:before.document.graph,documentRevision:before.revision});
  const disclosure=await page.locator('#pubchem-dialog').innerText();
  for(const text of ['PubChem (NCBI/NLM)','CCO','Retrieve a database IUPAC name','No fee','standardize'])assert.ok(disclosure.includes(text));
  const cancelled=page.waitForResponse(response=>response.url().endsWith('/api/pubchem/cancel'));
  await page.locator('#pubchem-review-cancel').click();
  await page.waitForFunction(()=>!document.getElementById('pubchem-dialog').open);
  await cancelled;
  assert.equal(actions(calls,'lookup').length,0);assert.equal(actions(calls,'cancel').length,1);
  assert.equal(actions(calls,'cancel')[0].body.proposalId,'1'.padStart(64,'0'));
  assert.deepEqual(await state(page),before);
});

test('PubChem explicit consent submits exactly one bound request and treats provider text as text', {timeout:60000},async t=>{
  const dangerous='<img src=x onerror="window.pubchemInjected=true">',gate=deferred(),entered=deferred();
  const {page,calls}=await fixture(t,{lookup:async result=>{entered.resolve();await gate.promise;return {...result,name:dangerous};}});
  t.after(()=>gate.resolve());const before=await state(page);
  await page.locator('#pubchem-lookup').click();await page.waitForFunction(()=>!document.getElementById('pubchem-send').disabled);
  await page.locator('#pubchem-send').click();await entered.promise;
  assert.equal(await page.locator('#pubchem-lookup').isDisabled(),true);
  assert.deepEqual(actions(calls,'lookup').map(row=>row.body),[{proposalId:'1'.padStart(64,'0'),consent:true,disclosureVersion:1}]);
  assert.ok(calls.every(row=>typeof row.token==='string'&&row.token.length>0));
  gate.resolve();await page.waitForFunction(text=>document.getElementById('pubchem-result').textContent===text,dangerous);
  assert.equal(await page.locator('#pubchem-result img').count(),0);
  assert.equal(await page.evaluate(()=>window.pubchemInjected),undefined);
  assert.match(await page.locator('#pubchem-details').innerText(),/database|retrieved/i);
  assert.match(await page.locator('#pubchem-details').innerText(),/Preferred IUPAC Name/);
  for(const expected of ['CID 702','2026-09-22T00:00:00Z','Submitted SMILES: CCO','Returned SMILES: CCO'])assert.ok((await page.locator('#pubchem-details').innerText()).includes(expected));
  assert.equal(actions(calls,'lookup').length,1);assert.deepEqual(await state(page),before);
});

test('PubChem cancellation and a later edit revoke the old request and discard its delayed result', {timeout:60000},async t=>{
  const gate=deferred(),entered=deferred();let lookupCount=0;
  const {page,calls}=await fixture(t,{lookup:async result=>{if(++lookupCount===1){entered.resolve();await gate.promise;return {...result,name:'STALE NAME'};}return {...result,status:'mismatch',name:null};}});
  t.after(()=>gate.resolve());
  await page.locator('#pubchem-lookup').click();await page.waitForFunction(()=>!document.getElementById('pubchem-send').disabled);
  await page.locator('#pubchem-send').click();await entered.promise;
  await drawing(page,'CCN');const edited=await state(page);
  await page.waitForFunction(()=>/changed/i.test(document.getElementById('pubchem-result').textContent));
  assert.equal(actions(calls,'cancel').length,1);
  assert.equal(actions(calls,'cancel')[0].body.proposalId,'1'.padStart(64,'0'));
  await naming(page);await page.locator('#pubchem-lookup').click();await page.waitForFunction(()=>!document.getElementById('pubchem-send').disabled);
  await page.locator('#pubchem-send').click();await page.waitForFunction(()=>/match/i.test(document.getElementById('pubchem-result').textContent));
  gate.resolve();await page.waitForFunction(()=>!document.getElementById('pubchem-lookup').disabled);
  assert.doesNotMatch(await page.locator('#pubchem-result').innerText(),/STALE NAME/);
  assert.deepEqual(await state(page),edited);assert.equal(actions(calls,'lookup').length,2);
});

test('PubChem cancellation during local preparation revokes a later proposal without sending it', {timeout:60000},async t=>{
  const gate=deferred(),{page,calls}=await fixture(t,{prepareGate:gate});t.after(()=>gate.resolve());
  await page.locator('#pubchem-lookup').click();await page.locator('#pubchem-dialog[open]').waitFor();
  await page.locator('#pubchem-review-cancel').click();assert.equal(actions(calls,'lookup').length,0);
  gate.resolve();await page.waitForFunction(()=>!document.getElementById('pubchem-lookup').disabled);
  assert.equal(actions(calls,'cancel').length,1);assert.equal(actions(calls,'lookup').length,0);
  assert.equal(await page.locator('#pubchem-dialog').evaluate(el=>el.open),false);
});

test('PubChem provider unavailability leaves offline editing and undo usable', {timeout:60000},async t=>{
  const {page,calls}=await fixture(t,{lookup:async result=>({...result,status:'unavailable',name:null,warnings:['PRIVATE UPSTREAM DETAIL']})});
  const before=await state(page);
  await page.locator('#pubchem-lookup').click();await page.waitForFunction(()=>!document.getElementById('pubchem-send').disabled);
  await page.locator('#pubchem-send').click();await page.waitForFunction(()=>/unavailable/i.test(document.getElementById('pubchem-result').textContent));
  assert.doesNotMatch(await page.locator('#pubchem-result').innerText(),/PRIVATE/);assert.deepEqual(await state(page),before);
  await drawing(page,'CCN');await page.locator('#undo-button').click();await ready(page);
  assert.deepEqual((await state(page)).document,before.document);assert.equal(actions(calls,'lookup').length,1);
});

test('PubChem refuses a result bound to another input or provider', {timeout:60000},async t=>{
  let count=0;
  const {page,calls}=await fixture(t,{lookup:async result=>++count===1
    ?{...result,name:'WRONG INPUT NAME',inputIdentity:{...result.inputIdentity,structureHash:'c'.repeat(64)}}
    :{...result,name:'WRONG PROVIDER NAME',provider:'Other provider'}});
  const before=await state(page);
  for(let i=0;i<2;i++){
    await page.locator('#pubchem-lookup').click();await page.waitForFunction(()=>!document.getElementById('pubchem-send').disabled);
    await page.locator('#pubchem-send').click();await page.waitForFunction(()=>/could not be completed or verified/.test(document.getElementById('pubchem-result').textContent));
    assert.doesNotMatch(await page.locator('#pubchem-result').innerText(),/WRONG/);
    await page.waitForFunction(()=>!document.getElementById('pubchem-lookup').disabled);
  }
  assert.equal(actions(calls,'lookup').length,2);assert.deepEqual(await state(page),before);
});

test('PubChem displays failed structure comparison records and accepts the provider name bound', {timeout:60000},async t=>{
  let count=0;const longName='n'.repeat(9000);
  const {page}=await fixture(t,{lookup:async result=>{
    if(++count===1){
      result.status='failed';result.name=null;
      result.records[0].match={status:'failed',warnings:['Returned SMILES could not be validated.']};
      return result;
    }
    return {...result,name:longName};
  }});
  const before=await state(page);
  await page.locator('#pubchem-lookup').click();await page.waitForFunction(()=>!document.getElementById('pubchem-send').disabled);
  await page.locator('#pubchem-send').click();await page.waitForFunction(()=>!document.getElementById('pubchem-lookup').disabled);
  assert.match(await page.locator('#pubchem-details').innerText(),/CID 702/);
  assert.match(await page.locator('#pubchem-details').innerText(),/Returned structure could not be compared/);
  assert.match(await page.locator('#pubchem-result').innerText(),/lookup failed/);
  await page.locator('#pubchem-lookup').click();await page.waitForFunction(()=>!document.getElementById('pubchem-send').disabled);
  await page.locator('#pubchem-send').click();await page.waitForFunction(()=>!document.getElementById('pubchem-lookup').disabled);
  assert.equal(await page.locator('#pubchem-result').innerText(),longName);
  assert.deepEqual(await state(page),before);
});

test('PubChem close review and pagehide revoke pending consent without a provider lookup', {timeout:60000},async t=>{
  const {page,calls}=await fixture(t);const before=await state(page);
  await page.locator('#pubchem-lookup').click();await page.waitForFunction(()=>!document.getElementById('pubchem-send').disabled);
  const closeCancelled=page.waitForResponse(response=>response.url().endsWith('/api/pubchem/cancel'));
  // The actual native-close review entrypoint is used; no fabricated DOM event.
  await page.evaluate(()=>window.nivramDocument.beginCloseReview());await closeCancelled;
  assert.equal(await page.locator('#pubchem-dialog').evaluate(el=>el.open),false);
  assert.equal(await page.locator('#pubchem-lookup').isDisabled(),true);
  await page.evaluate(()=>window.nivramDocument.endCloseReview());await ready(page);
  assert.deepEqual(await state(page),before);
  assert.equal(actions(calls,'cancel').length,1);assert.equal(actions(calls,'lookup').length,0);
  // Keep external lookup impossible while checking real local revocation. A
  // departing page's keepalive request is not reliably visible to page routes.
  let forbiddenLookups=0;
  await page.route('**/api/pubchem/lookup',async route=>{forbiddenLookups++;await route.abort();});
  await page.unroute('**/api/pubchem/*');
  const preparedResponse=page.waitForResponse(response=>response.url().endsWith('/api/pubchem/prepare'));
  await page.locator('#pubchem-lookup').click();await page.waitForFunction(()=>!document.getElementById('pubchem-send').disabled);
  const prepared=await (await preparedResponse).json();
  assert.match(prepared.proposalId,/^[0-9a-f]{64}$/);assert.ok(prepared.expiresAt>Date.now());
  // Accept the real unsaved-drawing navigation prompt so pagehide occurs.
  page.once('dialog',async dialog=>{assert.equal(dialog.type(),'beforeunload');await dialog.accept();});
  await page.reload();await ready(page);
  const verified=await page.evaluate(async proposalId=>{
    const response=await fetch('/api/pubchem/cancel',{method:'POST',headers:{'Content-Type':'application/json',
      'X-Nivram-Token':document.querySelector('meta[name="nivram-token"]').content},body:JSON.stringify({proposalId})});
    return {ok:response.ok,body:await response.json()};
  },prepared.proposalId);
  assert.ok(prepared.expiresAt>Date.now(),'Revocation must be verified before automatic expiry.');
  assert.equal(verified.ok,true);assert.equal(verified.body.status,'not_active','A pending unrevoked review would return cancelled.');
  assert.equal(forbiddenLookups,0);assert.equal(actions(calls,'lookup').length,0);
  assert.equal(await page.locator('#pubchem-details').innerText(),'');
  assert.equal(await page.locator('#pubchem-dialog').evaluate(el=>el.open),false);
  assert.equal(await page.locator('#pubchem-lookup').isDisabled(),false);
  assert.equal(before.document.graph.atoms.length,3);
});

test('PubChem timer: expired review is revoked and cannot authorize a lookup', {timeout:60000},async t=>{
  const current=await fixture(t,{clock:true}),{page,calls}=current,before=await state(page);
  try{
    await page.locator('#pubchem-lookup').click();await waitForReview(page);
    await page.clock.fastForward(59999);
    assert.equal(await page.locator('#pubchem-dialog').evaluate(el=>el.open),true);
    assert.equal(await page.locator('#pubchem-send').isDisabled(),false);
    assert.equal(actions(calls,'cancel').length,0);assert.equal(actions(calls,'lookup').length,0);
    const revoked=page.waitForResponse(response=>response.url().endsWith('/api/pubchem/cancel'));
    await page.clock.runFor(1);await revoked;
    assert.equal(await page.locator('#pubchem-dialog').evaluate(el=>el.open),false);
    assert.equal(await page.locator('#pubchem-send').isDisabled(),true);
    assert.equal(await page.locator('#pubchem-lookup').isDisabled(),false);
    assert.match(await page.locator('#pubchem-result').innerText(),/expired/i);
    assert.deepEqual(actions(calls,'cancel').map(row=>row.body),[{proposalId:'1'.padStart(64,'0')}]);
    assert.equal(actions(calls,'lookup').length,0);assert.deepEqual(await state(page),before);
    await page.locator('#pubchem-lookup').click();await waitForReview(page);
    assert.equal(actions(calls,'prepare').length,2);
    const cancelled=page.waitForResponse(response=>response.url().endsWith('/api/pubchem/cancel'));
    await page.locator('#pubchem-review-cancel').click();await cancelled;
    assert.deepEqual(actions(calls,'cancel').map(row=>row.body.proposalId),['1'.padStart(64,'0'),'2'.padStart(64,'0')]);
    assert.equal(actions(calls,'lookup').length,0);assert.deepEqual(await state(page),before);
  }finally{await finishTimerFixture(current);}
});

test('PubChem timer: lookup times out at 25 seconds, revokes consent, and ignores a late result', {timeout:60000},async t=>{
  const gate=deferred(),entered=deferred();let count=0;
  const current=await fixture(t,{clock:true,lookup:async result=>{
    if(++count===1){entered.resolve();await gate.promise;return {...result,name:'LATE TIMED OUT NAME'};}
    return result;
  }}),{page,calls}=current,before=await state(page);
  try{
    await page.locator('#pubchem-lookup').click();await waitForReview(page);
    await page.locator('#pubchem-send').click();await entered.promise;
    await page.clock.fastForward(24999);
    assert.equal(await page.locator('#pubchem-lookup').isDisabled(),true);
    assert.equal(await page.locator('#pubchem-cancel').isVisible(),true);
    assert.equal(actions(calls,'cancel').length,0);assert.equal(actions(calls,'lookup').length,1);
    const revoked=page.waitForResponse(response=>response.url().endsWith('/api/pubchem/cancel'));
    await page.clock.runFor(1);await revoked;
    assert.equal(await page.locator('#pubchem-lookup').isDisabled(),false);
    assert.equal(await page.locator('#pubchem-cancel').isVisible(),false);
    assert.match(await page.locator('#pubchem-result').innerText(),/timed out/i);
    assert.match(await page.locator('#pubchem-result').innerText(),/cannot be recalled/i);
    assert.equal(await page.locator('#pubchem-details').innerText(),'');
    assert.deepEqual(actions(calls,'cancel').map(row=>row.body),[{proposalId:'1'.padStart(64,'0')}]);
    assert.equal(actions(calls,'lookup').length,1);assert.deepEqual(await state(page),before);
    // A fresh, separately confirmed lookup must survive the old response.
    await page.locator('#pubchem-lookup').click();await waitForReview(page);
    await page.locator('#pubchem-send').click();
    await page.waitForFunction(()=>document.getElementById('pubchem-result').textContent==='ethanol',null,{polling:50});
    gate.resolve();await current.drainRoutes();await page.clock.runFor(1);
    assert.equal(await page.locator('#pubchem-result').innerText(),'ethanol');
    assert.doesNotMatch(await page.locator('#pubchem-details').innerText(),/LATE TIMED OUT NAME/);
    assert.deepEqual(actions(calls,'lookup').map(row=>row.body),[
      {proposalId:'1'.padStart(64,'0'),consent:true,disclosureVersion:1},
      {proposalId:'2'.padStart(64,'0'),consent:true,disclosureVersion:1},
    ]);
    assert.equal(actions(calls,'cancel').length,1);assert.deepEqual(await state(page),before);
  }finally{await finishTimerFixture(current,gate);}
});

test('PubChem timer: local preparation times out at 25 seconds without sending a lookup', {timeout:60000},async t=>{
  const gate={...deferred(),entered:deferred()};
  const current=await fixture(t,{clock:true,prepareGate:gate}),{page,calls}=current,before=await state(page);
  try{
    await page.locator('#pubchem-lookup').click();await gate.entered.promise;
    await page.clock.fastForward(24999);
    assert.equal(await page.locator('#pubchem-dialog').evaluate(el=>el.open),true);
    assert.equal(await page.locator('#pubchem-send').isDisabled(),true);
    assert.equal(await page.locator('#pubchem-lookup').isDisabled(),true);
    assert.equal(actions(calls,'lookup').length,0);
    await page.clock.runFor(1);
    assert.equal(await page.locator('#pubchem-dialog').evaluate(el=>el.open),false);
    assert.equal(await page.locator('#pubchem-lookup').isDisabled(),false);
    assert.match(await page.locator('#pubchem-result').innerText(),/preparation timed out/i);
    gate.resolve();await current.drainRoutes();await page.clock.runFor(1);
    assert.equal(await page.locator('#pubchem-dialog').evaluate(el=>el.open),false);
    assert.equal(await page.locator('#pubchem-send').isDisabled(),true);
    assert.equal(await page.locator('#pubchem-details').innerText(),'');
    assert.match(await page.locator('#pubchem-result').innerText(),/preparation timed out/i);
    assert.equal(actions(calls,'prepare').length,1);assert.equal(actions(calls,'lookup').length,0);
    assert.deepEqual(await state(page),before);
  }finally{await finishTimerFixture(current,gate);}
});
