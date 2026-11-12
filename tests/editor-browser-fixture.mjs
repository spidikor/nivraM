import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import {testBrowser} from './browser-session.mjs';

const repository=fileURLToPath(new URL('../',import.meta.url));
const python=process.env.NIVRAM_PYTHON??path.resolve(repository,'../venv/Scripts/python.exe');
const runtimeProbe=spawnSync(python,['-B','-c',"import json,sys,sysconfig;from pathlib import Path;print(json.dumps({'executable':str(Path(sys.base_prefix)/'python.exe'),'packages':sysconfig.get_paths()['purelib']}))"],{cwd:repository,encoding:'utf8',windowsHide:true,timeout:10000});
assert.ifError(runtimeProbe.error);assert.equal(runtimeProbe.status,0,runtimeProbe.stderr);
const runtime=JSON.parse(runtimeProbe.stdout);
async function bounded(promise,ms,label){let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(label)),ms);})]);}finally{clearTimeout(timer);}}
export async function productionEditor(t,{externalRequests=null,viewport={width:1366,height:768},deviceScaleFactor=1}={}){
  const browserCleanups=[];
  const browserContext={after(callback){browserCleanups.push(callback);},diagnostic(...args){return t.diagnostic(...args);}};
  const launch="import sys,threading\nfrom nivram.server import make_server\ns=make_server(0)\nt=threading.Thread(target=s.serve_forever)\nt.start()\nprint(f'http://127.0.0.1:{s.server_port}/',flush=True)\ntry: sys.stdin.readline()\nfinally:\n s.shutdown()\n s.server_close()\n t.join(5)\n";
  const bootstrap=`import sys\nsys.path[:0]=${JSON.stringify([repository,runtime.packages])}\n${launch}`;
  const child=spawn(runtime.executable,['-I','-S','-B','-u','-c',bootstrap],{cwd:repository,env:{...process.env,PYTHONPATH:[repository,runtime.packages].join(path.delimiter),PYTHONNOUSERSITE:'1'},windowsHide:true,stdio:['pipe','pipe','pipe']});
  let stderr='';child.stderr.on('data',b=>{stderr=(stderr+b).slice(-4000);});
  t.after(async()=>{
    // Node runs after hooks in registration order. Keep the page's server alive
    // until browser cleanup finishes, and still stop it if that cleanup fails.
    try{
      for(const cleanup of browserCleanups)await cleanup();
    }finally{
      const exited=child.exitCode!==null||child.signalCode!==null?Promise.resolve():once(child,'exit');
      try{child.stdin.end('stop\n');await bounded(exited,15000,'Owned chemistry server did not close.');assert.equal(child.exitCode,0,stderr);}
      catch(error){if(child.exitCode===null&&child.signalCode===null){child.kill();await bounded(exited,5000,'Owned server fallback did not exit.');}throw error;}
    }
  });
  const origin=await bounded(new Promise((resolve,reject)=>{let out='';child.once('error',reject);child.once('exit',c=>reject(new Error(`Chemistry server exited ${c}: ${stderr}`)));child.stdout.on('data',b=>{out+=b;const m=out.match(/http:\/\/127\.0\.0\.1:\d+\//);if(m)resolve(m[0]);});}),20000,'Chemistry server startup timed out.');
  const browser=await testBrowser(browserContext);
  const context=await browser.newContext({viewport,deviceScaleFactor});
  const page=await context.newPage(),errors=[];
  page.on('crash',()=>t.diagnostic('The owned editor test page reported a browser crash.'));
  page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>{const url=route.request().url();if(url.startsWith(origin)||/^(data|blob):/.test(url))return route.continue();externalRequests?.push(url);return route.abort();});
  await page.goto(origin);await ready(page);
  t.after(()=>assert.deepEqual(errors,[]));
  return page;
}
export const ready=page=>page.waitForFunction(()=>window.nivramDocument&&document.getElementById('request-status').textContent==='Ready');
export const state=page=>page.evaluate(()=>window.nivramDocument.getSnapshot());
// All editing/focus actions use real keyboard events. DOM reads only locate and
// inspect controls; no click(), fill(), focus(), DOM mutation or injected edit.
export async function tabTo(page,id){
  for(let i=0;i<220;i++){
    if(await page.evaluate(()=>document.activeElement?.id)===id)return;
    await page.keyboard.press('Tab');
  }
  throw new Error(`Keyboard could not reach ${id}.`);
}
export async function press(page,id){await tabTo(page,id);await page.keyboard.press('Enter');await ready(page);}
export async function number(page,id,value){await tabTo(page,id);await page.keyboard.press('Control+A');await page.keyboard.type(String(value));await page.keyboard.press('Tab');}
export async function choose(page,id,value){
  await tabTo(page,id);
  const index=await page.locator('#'+id).evaluate((el,value)=>[...el.options].findIndex(o=>o.value===value),value);
  assert.ok(index>=0,`Missing option ${value}`);await page.keyboard.press('Home');
  for(let i=0;i<index;i++)await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Tab');assert.equal(await page.inputValue('#'+id),value);
}
