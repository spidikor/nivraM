import test from 'node:test';
import assert from 'node:assert/strict';
import {createOpticalSetup,validateOpticalAvailability,attachOpticalSetup} from '../public/modules/optical-setup.mjs';

const modelSha256='32821fa4ba8d17f9a92e374601438c5e757947dbf1141e9ad0ef51cccfa1c501';
const available={status:'configured',message:'Runtime compatibility is checked on recognition.',modelSha256};
const absent={status:'not-configured',message:'Local recognition has not been configured.'};
const id='optical_setup_1';
const deferred=()=>{let resolve;return {promise:new Promise(r=>{resolve=r;}),resolve:value=>resolve(value)};};
function fixture(){
  const calls=[],saved=[],states=[];
  let selection={setupId:id,runtimeName:'',modelName:'',opticalRecognition:absent};
  const native={
    async begin_optical_setup(...args){calls.push(['begin',...args]);return {status:'editing',...selection};},
    async choose_optical_setup_file(...args){calls.push(['choose',...args]);selection={...selection,[args[1]==='python'?'runtimeName':'modelName']:args[1]==='python'?'python.exe':'swin_base_char_aux_1m.pth'};return {status:'selected',...selection};},
    async save_optical_setup(...args){calls.push(['save',...args]);return {status:'saved',setupId:id,opticalRecognition:available,modelVerified:true,runtimeCompatibility:'checked-on-recognition'};},
    async cancel_optical_setup(...args){calls.push(['cancel',...args]);return {status:'cancelled'};}
  };
  const options={native,sessionToken:'local-token',onState:s=>states.push(s),onSaved:(...args)=>saved.push(args)};
  return {native,options,calls,saved,states,controller:createOpticalSetup(options)};
}
async function selected(f){assert.equal(await f.controller.open('current_png_job'),true);await f.controller.choose('python');await f.controller.choose('model');}

test('local setup selects through native dialogs and publishes only a verified save with captured review identity',async()=>{
  const f=fixture();await selected(f);assert.equal(f.saved.length,0);assert.equal(await f.controller.save(),true);
  assert.deepEqual(f.calls,[['begin','local-token'],['choose',id,'python','local-token'],['choose',id,'model','local-token'],['save',id,'local-token']]);
  assert.deepEqual(f.saved,[[available,'current_png_job']]);assert.equal(f.controller.state.open,false);
  assert.ok(f.states.some(s=>s.busy&&s.message.startsWith('Verifying')));
});

test('missing native methods, empty selection and arbitrary chooser kinds cannot dispatch setup work',async()=>{
  const f=fixture();assert.equal(await f.controller.save(),false);await f.controller.open();
  assert.equal(await f.controller.save(),false);assert.equal(await f.controller.choose('C:/private/file.exe'),false);assert.equal(f.calls.length,1);
  const unsupported=createOpticalSetup({native:{begin_optical_setup(){throw new Error('Must not run');}},sessionToken:'token'});
  assert.equal(unsupported.supported,false);assert.equal(await unsupported.open(),false);
});

test('chooser cancellation preserves staged selections and never saves them',async()=>{
  const f=fixture();await selected(f);const before=f.controller.state;
  f.native.choose_optical_setup_file=async()=>({status:'cancelled',setupId:id,runtimeName:before.runtimeName,modelName:before.modelName,opticalRecognition:absent});
  assert.equal(await f.controller.choose('model'),true);assert.equal(f.controller.state.modelName,before.modelName);
  f.controller.cancel();assert.equal(f.saved.length,0);assert.deepEqual(f.calls.at(-1),['cancel',id,'local-token']);
});

test('verification failure and busy response retain selections without publishing configuration',async()=>{
  for(const response of [()=>{throw new Error('The selected model failed its integrity check.');},()=>({status:'busy'})]){
    const f=fixture();await selected(f);f.native.save_optical_setup=response;
    assert.equal(await f.controller.save(),false);assert.equal(f.controller.state.open,true);assert.equal(f.controller.state.busy,false);
    assert.equal(f.controller.state.error,true);assert.equal(f.controller.state.modelName,'swin_base_char_aux_1m.pth');assert.equal(f.saved.length,0);f.controller.cancel();
  }
});

test('cancelled pending begin releases its late native transaction without reopening the dialog',async()=>{
  const f=fixture(),wait=deferred();f.native.begin_optical_setup=()=>wait.promise;
  const pending=f.controller.open();f.controller.cancel();wait.resolve({status:'editing',setupId:id,runtimeName:'',modelName:'',opticalRecognition:absent});
  assert.equal(await pending,false);assert.equal(f.controller.state.open,false);assert.deepEqual(f.calls,[['cancel',id,'local-token']]);assert.equal(f.saved.length,0);
});

test('cancelled pending chooser and save cannot overwrite or publish into a newer setup',async()=>{
  for(const method of ['choose_optical_setup_file','save_optical_setup']){
    const f=fixture(),wait=deferred();await selected(f);const original=f.native[method];f.native[method]=()=>wait.promise;
    const pending=method==='save_optical_setup'?f.controller.save():f.controller.choose('model');
    assert.equal(f.controller.state.busy,true);f.controller.cancel();f.native[method]=original;await f.controller.open('replacement_png_job');
    const before=f.controller.state;wait.resolve(method==='save_optical_setup'?{status:'saved',setupId:id,opticalRecognition:available,modelVerified:true,runtimeCompatibility:'checked-on-recognition'}:{status:'selected',setupId:id,runtimeName:'late.exe',modelName:'late.pth',opticalRecognition:available});
    assert.equal(await pending,false);assert.deepEqual(f.controller.state,before);assert.equal(f.saved.length,0);f.controller.cancel();
  }
});

test('mismatched, incomplete or wrong-model save receipts cannot publish availability',async()=>{
  for(const patch of [{setupId:'another_setup'},{modelVerified:false},{runtimeCompatibility:'verified'},{opticalRecognition:{...available,modelSha256:'a'.repeat(64)}},{opticalRecognition:{status:'configured',message:'Ready'}}]){
    const f=fixture();await selected(f);f.native.save_optical_setup=async()=>({status:'saved',setupId:id,opticalRecognition:available,modelVerified:true,runtimeCompatibility:'checked-on-recognition',...patch});
    assert.equal(await f.controller.save(),false);assert.equal(f.saved.length,0);assert.equal(f.controller.state.error,true);f.controller.cancel();
  }
  assert.throws(()=>validateOpticalAvailability({status:'invented',message:'Ready'}));
});

test('mismatched chooser receipts leave the previous selections active and report an error',async()=>{
  const f=fixture();await selected(f);f.native.choose_optical_setup_file=async()=>({status:'selected',setupId:'another_setup',runtimeName:'bad.exe',modelName:'bad.pth',opticalRecognition:available});
  assert.equal(await f.controller.choose('model'),false);assert.equal(f.controller.state.modelName,'swin_base_char_aux_1m.pth');assert.equal(f.controller.state.error,true);f.controller.cancel();
});

// A minimal DOM boundary checks Escape/close lifecycle without pretending to
// validate layout, real native file dialogs, filesystem checks or inference.
function dom(){
  const elements=new Map();
  for(const id of ['optical-setup-dialog','optical-setup-button','image-import-setup','optical-setup-runtime','optical-setup-model','optical-setup-availability','optical-setup-status','optical-setup-progress','optical-setup-runtime-choose','optical-setup-model-choose','optical-setup-save','optical-setup-cancel','optical-setup-close']){
    const element=new EventTarget();Object.assign(element,{hidden:true,disabled:false,open:false,textContent:'',classList:{toggle(){}},setAttribute(){},showModal(){this.open=true;},close(){this.open=false;this.dispatchEvent(new Event('close'));}});elements.set(id,element);
  }
  return {document:{getElementById:id=>elements.get(id)},events:new EventTarget(),get:id=>elements.get(id)};
}

test('Escape closes setup only; global close fences pending setup until close review ends',async()=>{
  const f=fixture(),view=dom(),controller=attachOpticalSetup({...view,...f.options,reviewIdentity:()=> 'png_review'});
  await controller.open('png_review');assert.equal(view.get('optical-setup-dialog').open,true);
  const escape=new Event('cancel',{cancelable:true});view.get('optical-setup-dialog').dispatchEvent(escape);
  assert.equal(escape.defaultPrevented,true);assert.equal(controller.state.open,false);assert.equal(view.get('optical-setup-dialog').open,false);
  view.events.dispatchEvent(new Event('nivram-close-review'));view.get('optical-setup-button').dispatchEvent(new Event('click'));assert.equal(controller.state.open,false);
  view.events.dispatchEvent(new Event('nivram-close-review-ended'));view.get('optical-setup-button').dispatchEvent(new Event('click'));
  await new Promise(r=>setImmediate(r));assert.equal(controller.state.open,true);
  view.get('optical-setup-dialog').dispatchEvent(new Event('close'));assert.equal(controller.state.open,true,'A delayed close event cannot cancel a reopened dialog.');
  controller.cancel();assert.equal(f.saved.length,0);
});
