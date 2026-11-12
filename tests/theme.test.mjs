import test from 'node:test';
import assert from 'node:assert/strict';
import {initializeThemeToggle} from '../public/modules/theme.mjs';

// Only the DOM boundary used by the controller is represented here. These are
// controller tests, not a substitute for native button or CSS rendering tests.
function controls(){
  const rootAttributes=new Map(),buttonAttributes=new Map();
  const root={setAttribute(name,value){rootAttributes.set(name,value);}};
  const button=new EventTarget();
  button.setAttribute=(name,value)=>buttonAttributes.set(name,value);
  return {root,button,rootAttributes,buttonAttributes};
}

test('activation keeps the displayed theme and accessible pressed state in sync',()=>{
  const view=controls();initializeThemeToggle(view.root,view.button);
  assert.equal(view.rootAttributes.get('data-theme'),'light');
  assert.equal(view.buttonAttributes.get('aria-pressed'),'false');
  view.button.dispatchEvent(new Event('click'));
  assert.equal(view.rootAttributes.get('data-theme'),'dark');
  assert.equal(view.buttonAttributes.get('aria-pressed'),'true');
  view.button.dispatchEvent(new Event('click'));
  assert.equal(view.rootAttributes.get('data-theme'),'light');
  assert.equal(view.buttonAttributes.get('aria-pressed'),'false');
});

test('a second window starts independently and cannot reset the first window',()=>{
  const first=controls();initializeThemeToggle(first.root,first.button);
  first.button.dispatchEvent(new Event('click'));
  const second=controls();initializeThemeToggle(second.root,second.button);
  assert.equal(first.rootAttributes.get('data-theme'),'dark');
  assert.equal(first.buttonAttributes.get('aria-pressed'),'true');
  assert.equal(second.rootAttributes.get('data-theme'),'light');
  second.button.dispatchEvent(new Event('click'));
  first.button.dispatchEvent(new Event('click'));
  assert.equal(first.rootAttributes.get('data-theme'),'light');
  assert.equal(second.rootAttributes.get('data-theme'),'dark');
  assert.equal(second.buttonAttributes.get('aria-pressed'),'true');
});

test('activation writes only presentation and accessibility attributes',()=>{
  const view=controls();
  view.rootAttributes.set('lang','en');
  view.buttonAttributes.set('aria-label','Dark mode');
  view.buttonAttributes.set('title','PNG output remains on white.');
  initializeThemeToggle(view.root,view.button);
  for(let i=0;i<5;i++)view.button.dispatchEvent(new Event('click'));
  assert.deepEqual(Object.fromEntries(view.rootAttributes),{lang:'en','data-theme':'dark'});
  assert.deepEqual(Object.fromEntries(view.buttonAttributes),{
    'aria-label':'Dark mode',title:'PNG output remains on white.','aria-pressed':'true'
  });
});

const generation='a'.repeat(32);
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('new theme controllers restore both choices from a shared native preference store',async()=>{
  let saved='light';
  const bridge={get_appearance:async()=>({theme:saved,status:'loaded',generation}),
    set_appearance:async(theme,_generation,revision)=>{saved=theme;return {status:'saved',theme,revision};}};
  for(const expected of ['light','dark','light']){
    const view=controls(),controller=initializeThemeToggle(view.root,view.button);
    await controller.connect(bridge);
    assert.equal(controller.getState().theme,expected);
    view.button.dispatchEvent(new Event('click'));
    assert.equal(await controller.flush(),true);
  }
});

test('a slow preference read never overwrites a newer user choice',async()=>{
  const waiting=deferred(),writes=[],view=controls(),controller=initializeThemeToggle(view.root,view.button);
  const attached=controller.connect({get_appearance:()=>waiting.promise,
    set_appearance:async(theme,_generation,revision)=>{writes.push(theme);return {status:'saved',theme,revision};}});
  view.button.dispatchEvent(new Event('click'));
  waiting.resolve({theme:'light',status:'loaded',generation});await attached;await controller.flush();
  assert.equal(controller.getState().theme,'dark');assert.deepEqual(writes,['dark']);
});

test('fast switches stay immediate while writes remain ordered and close review freezes the last choice',async()=>{
  const first=deferred(),writes=[],view=controls(),controller=initializeThemeToggle(view.root,view.button);
  await controller.connect({get_appearance:async()=>({theme:'light',status:'default',generation}),
    set_appearance:async(theme,_generation,revision)=>{writes.push(theme);if(revision===1)await first.promise;return {status:'saved',theme,revision};}});
  view.button.dispatchEvent(new Event('click'));await tick();
  view.button.dispatchEvent(new Event('click'));view.button.dispatchEvent(new Event('click'));
  assert.equal(controller.getState().theme,'dark');assert.deepEqual(writes,['dark']);
  assert.deepEqual(controller.beginCloseReview(),{theme:'dark',revision:3,generation});
  view.button.dispatchEvent(new Event('click'));assert.equal(controller.getState().theme,'dark');
  assert.equal(view.button.disabled,true);first.resolve();assert.equal(await controller.flush(),true);
  assert.deepEqual(writes,['dark','light','dark']);controller.endCloseReview();
  assert.equal(view.button.disabled,false);view.button.dispatchEvent(new Event('click'));
  assert.equal(controller.getState().theme,'light');await controller.flush();
});

test('failed reads and writes report loss of persistence without reverting the chosen display',async()=>{
  const warnings=[],view=controls(),controller=initializeThemeToggle(view.root,view.button,{warn:m=>warnings.push(m)});
  await controller.connect({get_appearance:async()=>({theme:'light',status:'unavailable',generation}),
    set_appearance:async()=>{throw new Error('private path');}});
  view.button.dispatchEvent(new Event('click'));assert.equal(await controller.flush(),false);
  assert.equal(controller.getState().theme,'dark');assert.equal(warnings.length,2);
  assert.ok(!warnings.join('').includes('private path'));
});
