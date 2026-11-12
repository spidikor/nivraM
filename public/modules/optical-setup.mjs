const modelHash='32821fa4ba8d17f9a92e374601438c5e757947dbf1141e9ad0ef51cccfa1c501';
const methods=['begin_optical_setup','choose_optical_setup_file','save_optical_setup','cancel_optical_setup'];
const availabilityStates=['configured','not-configured','unavailable','invalid-configuration','missing-runtime','missing-model','invalid-model','closed'];
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);

export function validateOpticalAvailability(value){
  if(!object(value)||!availabilityStates.includes(value.status)||typeof value.message!=='string'||value.message.length>2000||value.status==='configured'&&value.modelSha256!==modelHash||value.modelSha256!==undefined&&value.modelSha256!==modelHash)throw new Error('Local recognition returned invalid availability information.');
  return Object.freeze({status:value.status,message:value.message,...(value.modelSha256===undefined?{}:{modelSha256:value.modelSha256})});
}

function snapshot(raw,expectedId,statuses){
  if(raw?.status==='busy')throw new Error('Local recognition is busy. Finish or cancel recognition before changing setup.');
  if(raw?.status==='stale'||raw?.status==='superseded')throw new Error('This setup has expired. Cancel and open setup again.');
  if(!object(raw)||!statuses.includes(raw.status)||typeof raw.setupId!=='string'||!/^[A-Za-z0-9_-]{8,80}$/.test(raw.setupId)||expectedId&&raw.setupId!==expectedId)throw new Error('Local recognition returned an invalid setup response.');
  const names={};
  for(const key of ['runtimeName','modelName']){
    if(typeof raw[key]!=='string'||raw[key].length>500)throw new Error('Local recognition returned an invalid file selection.');
    names[key]=raw[key];
  }
  return {setupId:raw.setupId,...names,opticalRecognition:validateOpticalAvailability(raw.opticalRecognition)};
}

// The native bridge owns all filesystem paths and pending selections. This
// controller sends only the setup identifier, fixed chooser kind and API token.
export function createOpticalSetup({native,sessionToken,onState=()=>{},onSaved=()=>{}}){
  let generation=0,context=null;
  let state={open:false,busy:false,setupId:null,runtimeName:'',modelName:'',opticalRecognition:null,message:'',error:false};
  const supported=methods.every(name=>typeof native?.[name]==='function')&&typeof sessionToken==='string'&&Boolean(sessionToken);
  const publish=changes=>{state={...state,...changes};onState(Object.freeze({...state}));};
  const release=id=>{if(id)try{Promise.resolve(native.cancel_optical_setup(id,sessionToken)).catch(()=>{});}catch{/* Local callbacks remain fenced even if the bridge closes. */}};
  const fail=error=>publish({busy:false,error:true,message:typeof error?.message==='string'?error.message.slice(0,2000):'Local recognition setup could not complete.'});
  const active=(request,id)=>generation===request&&state.open&&(!id||state.setupId===id);
  return Object.freeze({
    supported,
    get state(){return Object.freeze({...state});},
    async open(reviewIdentity=null){
      if(!supported||state.open)return false;
      const request=++generation;context=reviewIdentity;
      publish({open:true,busy:true,setupId:null,runtimeName:'',modelName:'',opticalRecognition:null,message:'Reading local recognition settings…',error:false});
      try{
        const raw=await native.begin_optical_setup(sessionToken);
        if(!active(request)){if(typeof raw?.setupId==='string'&&/^[A-Za-z0-9_-]{8,80}$/.test(raw.setupId))release(raw.setupId);return false;}
        const selected=snapshot(raw,null,['editing']);
        publish({...selected,busy:false,message:'Choose the existing local runtime and pinned model, then verify and save.',error:false});return true;
      }catch(error){if(active(request))fail(error);return false;}
    },
    async choose(kind){
      if(!['python','model'].includes(kind)||!state.open||state.busy||!state.setupId)return false;
      const request=generation,id=state.setupId;
      publish({busy:true,error:false,message:kind==='python'?'Choose the local recognition Python executable.':'Choose the pinned MolScribe model file.'});
      try{
        const raw=await native.choose_optical_setup_file(id,kind,sessionToken);if(!active(request,id))return false;
        publish({...snapshot(raw,id,['selected','cancelled']),busy:false,error:false,message:raw.status==='cancelled'?'File selection cancelled. Your previous selection is retained.':'Selection updated. Verify the model and save to apply setup.'});return true;
      }catch(error){if(active(request,id))fail(error);return false;}
    },
    async save(){
      if(!state.open||state.busy||!state.setupId||!state.runtimeName||!state.modelName)return false;
      const request=generation,id=state.setupId,savedContext=context;
      publish({busy:true,error:false,message:'Verifying the pinned model and saving local setup…'});
      try{
        const raw=await native.save_optical_setup(id,sessionToken);if(!active(request,id))return false;
        if(raw?.status==='busy')throw new Error('Local recognition is busy. Finish or cancel recognition before changing setup.');
        if(raw?.status==='stale'||raw?.status==='superseded')throw new Error('This setup has expired. Cancel and open setup again.');
        if(raw?.status!=='saved'||raw.setupId!==id||raw.modelVerified!==true||raw.runtimeCompatibility!=='checked-on-recognition')throw new Error('Local recognition did not confirm a verified setup save.');
        const opticalRecognition=validateOpticalAvailability(raw.opticalRecognition);
        if(opticalRecognition.status!=='configured')throw new Error('The saved local recognition setup is unavailable.');
        generation++;context=null;publish({open:false,busy:false,setupId:null,opticalRecognition,message:'Local recognition setup saved. Model verified; runtime compatibility is checked when recognition starts.',error:false});
        onSaved(opticalRecognition,savedContext);return true;
      }catch(error){if(active(request,id))fail(error);return false;}
    },
    cancel(){
      const id=state.setupId;generation++;context=null;
      publish({open:false,busy:false,setupId:null,message:'',error:false});release(id);
    }
  });
}

export function attachOpticalSetup({document,events,native,sessionToken,canOpen=()=>true,reviewIdentity=()=>null,onSaved=()=>{}}){
  const get=id=>document.getElementById(id),dialog=get('optical-setup-dialog');
  const triggers=[get('optical-setup-button'),get('image-import-setup')];
  const controller=createOpticalSetup({native,sessionToken,onSaved,onState:state=>{
    if(state.open&&!dialog.open)dialog.showModal();else if(!state.open&&dialog.open)dialog.close();
    get('optical-setup-runtime').textContent=state.runtimeName||'No runtime selected';
    get('optical-setup-model').textContent=state.modelName||'No model selected';
    get('optical-setup-availability').textContent=state.opticalRecognition?.message||'';
    get('optical-setup-status').textContent=state.message;
    get('optical-setup-status').classList.toggle('image-import-error',state.error);
    get('optical-setup-progress').hidden=!state.busy;
    dialog.setAttribute('aria-busy',String(state.busy));
    get('optical-setup-runtime-choose').disabled=state.busy||!state.setupId;
    get('optical-setup-model-choose').disabled=state.busy||!state.setupId;
    get('optical-setup-save').disabled=state.busy||!state.setupId||!state.runtimeName||!state.modelName;
  }});
  if(!controller.supported)return controller;
  let closing=false;
  for(const trigger of triggers){trigger.hidden=false;trigger.addEventListener('click',()=>{if(!closing&&canOpen())controller.open(reviewIdentity());});}
  get('optical-setup-runtime-choose').addEventListener('click',()=>controller.choose('python'));
  get('optical-setup-model-choose').addEventListener('click',()=>controller.choose('model'));
  get('optical-setup-save').addEventListener('click',()=>controller.save());
  for(const id of ['optical-setup-cancel','optical-setup-close'])get(id).addEventListener('click',()=>controller.cancel());
  dialog.addEventListener('cancel',event=>{event.preventDefault();controller.cancel();});
  dialog.addEventListener('close',()=>{if(controller.state.open&&!dialog.open)controller.cancel();});
  events.addEventListener('nivram-close-review',()=>{closing=true;controller.cancel();});
  events.addEventListener('nivram-close-review-ended',()=>{closing=false;});
  return controller;
}
