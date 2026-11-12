/** Optional, per-request PubChem lookup. Consent and results stay in this session. */
const ENDPOINT='https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/property/IUPACName,SMILES/JSON';
const ID=/^[0-9a-f]{64}$/;
const STATUSES=new Set(['matched','mismatch','ambiguous','not_found','unavailable','failed']);
const messages={mismatch:'No accepted name: the returned structure did not match.',
  ambiguous:'No accepted name: PubChem returned ambiguous records.',not_found:'No matching PubChem record was found.',
  unavailable:'PubChem is unavailable. You can continue editing offline.',failed:'The PubChem lookup failed. You can continue editing offline.'};
const text=(value,max=4096)=>typeof value==='string'&&value.length>0&&value.length<=max;
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
function require(value){if(!value)throw new Error('Invalid local PubChem response.');}
function freeze(value){if(value&&typeof value==='object'){for(const child of Object.values(value))freeze(child);Object.freeze(value);}return value;}
function identity(value,revision){
  require(object(value)&&value.documentRevision===revision&&ID.test(value.graphHash)&&ID.test(value.structureHash)&&text(value.smiles,32768));
  return {documentRevision:value.documentRevision,graphHash:value.graphHash,structureHash:value.structureHash,smiles:value.smiles};
}
function proposal(value,revision){
  require(object(value)&&typeof value.proposalId==='string'&&ID.test(value.proposalId));
  const inputIdentity=identity(value.inputIdentity,revision),d=value.disclosure;
  require(Number.isSafeInteger(value.expiresAt)&&value.expiresAt>Date.now()&&value.expiresAt<=Date.now()+125000);
  require(object(d)&&d.version===1&&text(d.provider,200)&&d.endpoint===ENDPOINT&&text(d.purpose)&&text(d.cost)
    &&d.submittedSmiles===inputIdentity.smiles&&Array.isArray(d.warnings)&&d.warnings.length<=20&&d.warnings.every(item=>text(item)));
  return freeze({proposalId:value.proposalId,expiresAt:value.expiresAt,inputIdentity,
    disclosure:{version:1,provider:d.provider,endpoint:d.endpoint,purpose:d.purpose,cost:d.cost,submittedSmiles:d.submittedSmiles,warnings:[...d.warnings]}});
}
function acceptedResult(value,review){
  require(object(value)&&STATUSES.has(value.status)&&value.nameKind==='retrieved_database_iupac'&&value.preferredIUPACName===false
    &&value.provider===review.disclosure.provider&&Array.isArray(value.warnings)&&value.warnings.length<=20&&value.warnings.every(item=>text(item)));
  const received=identity(value.inputIdentity,review.inputIdentity.documentRevision);
  require(Object.keys(received).every(key=>received[key]===review.inputIdentity[key]));
  require(value.status==='matched'?text(value.name,16384):value.name===null);
  require(Array.isArray(value.records)&&value.records.length<=20);
  for(const record of value.records){
    require(object(record)&&Number.isSafeInteger(record.CID)&&record.CID>0&&text(record.retrievedAt,64)
      &&record.submittedSmiles===review.inputIdentity.smiles&&text(record.returnedSmiles,32768)&&text(record.returnedName,16384)
      &&object(record.match)&&['matched','mismatch','failed'].includes(record.match.status)
      &&(text(record.match.comparison)||(record.match.status==='failed'&&record.match.comparison===undefined))
      &&Array.isArray(record.match.warnings)&&record.match.warnings.length<=20&&record.match.warnings.every(item=>text(item)));
  }
  if(value.status==='matched')require(value.records.length===1&&value.records[0].match.status==='matched'&&value.records[0].returnedName===value.name);
  return value;
}

export function mountPubChem({editor,dom=document,fetcher=globalThis.fetch,initialCloseReview=false}={}){
  const find=id=>dom.getElementById(id),button=find('pubchem-lookup'),cancelButton=find('pubchem-cancel');
  const dialog=find('pubchem-dialog'),send=find('pubchem-send'),result=find('pubchem-result'),details=find('pubchem-details');
  const token=dom.querySelector('meta[name="nivram-token"]').content;
  for(const id of ['pubchem-smiles','pubchem-endpoint'])find(id).style.overflowWrap='anywhere';
  details.style.whiteSpace='pre-wrap';details.style.overflowWrap='anywhere';
  let active=null,stopped=false,closeReview=initialCloseReview,lastRevision=editor.getSnapshot().revision;
  const listeners=[];
  function listen(node,event,callback){node.addEventListener(event,callback);listeners.push(()=>node.removeEventListener(event,callback));}
  const current=op=>active===op&&!op.cancelled&&!stopped&&!closeReview&&editor.getSnapshot().revision===op.revision;
  function buttons(){
    button.disabled=stopped||closeReview||active!==null;
    cancelButton.hidden=!active||active.cancelled;
    send.disabled=!active||!current(active)||active.phase!=='review'||active.review.expiresAt<=Date.now();
  }
  async function post(action,body,signal,keepalive=false){
    const response=await fetcher(`/api/pubchem/${action}`,{method:'POST',signal,keepalive,credentials:'same-origin',redirect:'error',cache:'no-store',
      headers:{'Content-Type':'application/json','X-Nivram-Token':token},body:JSON.stringify(body)});
    if(!response.ok)throw new Error('Local PubChem request failed.');
    const raw=await response.text();require(raw.length<=262144);
    return JSON.parse(raw);
  }
  function revoke(op,id=op.review?.proposalId??op.proposalId){
    if(!id||op.revoked)return;
    op.revoked=true;const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),3000);
    void post('cancel',{proposalId:id},controller.signal,true).catch(()=>{}).finally(()=>clearTimeout(timer));
  }
  function settle(op){
    clearTimeout(op.timer);clearTimeout(op.expiry);
    if(active===op){active=null;buttons();}
  }
  function closeDialog(){if(dialog.open)dialog.close();}
  function cancel(message='Cancelled. No PubChem lookup was sent.'){
    const op=active;
    if(!op)return;
    op.cancelled=true;clearTimeout(op.expiry);revoke(op);
    result.textContent=op.phase==='lookup'?'Cancelled. A transmitted request cannot be recalled; any later result will be ignored.':message;
    details.textContent='';closeDialog();
    // A local preparation has no ID yet. Let its bounded response arrive so it
    // can be revoked; keep another preparation disabled until it settles.
    if(op.phase!=='prepare'){op.controller.abort();settle(op);}else buttons();
  }
  function request(op,action,body){
    clearTimeout(op.timer);op.controller=new AbortController();
    op.timer=setTimeout(()=>{
      if(active!==op)return;
      op.cancelled=true;revoke(op);op.controller.abort();closeDialog();
      result.textContent=action==='lookup'?'PubChem lookup timed out. A transmitted request cannot be recalled; any later result will be ignored.':'Local PubChem preparation timed out. No lookup was sent.';
      details.textContent='';settle(op);
    },25000);
    return post(action,body,op.controller.signal);
  }
  function disclose(review){
    const d=review.disclosure;
    for(const [id,value] of [['pubchem-provider',d.provider],['pubchem-endpoint',d.endpoint],['pubchem-purpose',d.purpose],['pubchem-cost',d.cost],['pubchem-smiles',d.submittedSmiles]])find(id).textContent=value;
    const warnings=find('pubchem-warnings');warnings.replaceChildren();
    for(const warning of d.warnings){const item=dom.createElement('li');item.textContent=warning;warnings.append(item);}
  }
  async function prepare(){
    if(active||stopped||closeReview)return;
    const snapshot=editor.getSnapshot(),op={phase:'prepare',revision:snapshot.revision,input:freeze(structuredClone(snapshot.document.graph)),cancelled:false,revoked:false,review:null};
    active=op;result.textContent='Preparing a local review. Nothing has been sent to PubChem.';details.textContent='';
    find('pubchem-review-status').textContent='Preparing the exact structure and disclosure locally.';
    for(const id of ['pubchem-provider','pubchem-endpoint','pubchem-purpose','pubchem-cost','pubchem-smiles'])find(id).textContent='';
    find('pubchem-warnings').replaceChildren();buttons();dialog.showModal();
    try{
      const value=await request(op,'prepare',{graph:op.input,documentRevision:op.revision});
      if(typeof value?.proposalId==='string'&&ID.test(value.proposalId))op.proposalId=value.proposalId;
      // Even a cancelled preparation must revoke a usable returned proposal ID.
      if(!current(op)){if(typeof value?.proposalId==='string'&&ID.test(value.proposalId))revoke(op,value.proposalId);settle(op);return;}
      op.review=proposal(value,op.revision);clearTimeout(op.timer);op.phase='review';disclose(op.review);
      result.textContent='Review the proposed PubChem request. Nothing has been sent.';
      find('pubchem-review-status').textContent='This consent applies only to this request. Review expires within two minutes.';
      op.expiry=setTimeout(()=>{if(active===op)cancel('Review expired. No PubChem lookup was sent. Start a new review.');},Math.max(0,op.review.expiresAt-Date.now()));
      buttons();
    }catch{
      if(current(op)){revoke(op);result.textContent='The local PubChem review could not be prepared. Nothing was sent. You can continue editing offline.';details.textContent='';op.cancelled=true;closeDialog();}
      settle(op);
    }
  }
  async function lookup(){
    const op=active;if(!op||!current(op)||op.phase!=='review')return;
    if(op.review.expiresAt<=Date.now()){cancel('Review expired. No PubChem lookup was sent. Start a new review.');return;}
    op.phase='lookup';clearTimeout(op.expiry);closeDialog();buttons();
    result.textContent='Looking up the reviewed structure in PubChem. You can continue editing.';details.textContent='';
    try{
      const value=await request(op,'lookup',{proposalId:op.review.proposalId,consent:true,disclosureVersion:1});
      if(!current(op))return;
      const accepted=acceptedResult(value,op.review);
      if(accepted.status==='matched'){
        result.textContent=accepted.name;
        details.textContent=`Retrieved database IUPAC name from ${op.review.disclosure.provider}. The returned structure matched under the comparison below. This is not a verified Preferred IUPAC Name. The drawing is unchanged.`;
      }else{result.textContent=messages[accepted.status];details.textContent='No database name was associated with this drawing. The drawing is unchanged.';}
      if(!['unavailable','failed'].includes(accepted.status)&&accepted.warnings.length)details.textContent+='\n'+accepted.warnings.join('\n');
      for(const record of accepted.records){
        const comparison=record.match.comparison??'Returned structure could not be compared.';
        details.textContent+=`\n\nPubChem CID ${record.CID}; retrieved ${record.retrievedAt}.\nSubmitted SMILES: ${record.submittedSmiles}\nReturned SMILES: ${record.returnedSmiles}\nRetrieved record name: ${record.returnedName}\nStructure comparison: ${record.match.status}. ${comparison}`;
        if(record.match.warnings.length)details.textContent+='\n'+record.match.warnings.join('\n');
      }
    }catch{
      if(current(op)){revoke(op);result.textContent='The PubChem lookup could not be completed or verified. You can continue editing offline.';details.textContent='No database name was associated with this drawing.';}
    }finally{settle(op);}
  }
  listen(button,'click',()=>{void prepare();});listen(send,'click',()=>{void lookup();});
  listen(cancelButton,'click',()=>cancel());listen(find('pubchem-review-cancel'),'click',()=>cancel());
  listen(dialog,'cancel',event=>{event.preventDefault();cancel();});
  listen(dialog,'close',()=>{if(active&&active.phase==='review'&&!active.cancelled)cancel();});
  const unsubscribe=editor.subscribe(snapshot=>{
    if(snapshot.revision===lastRevision)return;lastRevision=snapshot.revision;
    cancel('The drawing changed. The previous review was cancelled.');
    result.textContent='The drawing changed. Review a new PubChem request for the current structure.';details.textContent='';
  });
  buttons();
  return Object.freeze({
    setCloseReview(value){closeReview=Boolean(value);if(closeReview)cancel('PubChem review cancelled while closing is reviewed.');buttons();},
    destroy(){if(stopped)return;cancel('PubChem review cancelled.');stopped=true;unsubscribe();for(const remove of listeners.splice(0))remove();buttons();},
  });
}

if(typeof window!=='undefined'){
  let started=false,closed=false,closeReview=false,mounted=null;
  async function start(){
    if(started||!window.nivramDocument)return;started=true;
    try{await window.nivramDocument.ready;if(!closed)mounted=mountPubChem({editor:window.nivramDocument,initialCloseReview:closeReview});}
    catch{const result=document.getElementById('pubchem-result');if(result)result.textContent='Optional PubChem lookup is unavailable. You can continue editing offline.';}
  }
  window.addEventListener('nivram-document-ready',start);
  window.addEventListener('nivram-close-review',()=>{closeReview=true;mounted?.setCloseReview(true);});
  window.addEventListener('nivram-close-review-ended',()=>{closeReview=false;mounted?.setCloseReview(false);});
  window.addEventListener('pagehide',()=>{closed=true;mounted?.destroy();},{once:true});
  void start();
}
