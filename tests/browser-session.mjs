import assert from 'node:assert/strict';
import {createRequire} from 'node:module';

const {chromium}=createRequire(import.meta.url)('playwright');

// These are isolated editor fixtures, not native application shutdown tests.
// Direct launch owns the browser locally; a remote connection only owns a client.
// The check runner separately requires the entire assigned Windows job to exit.
export async function testBrowser(t){
  const browser=await chromium.launch({headless:true,...(process.env.NIVRAM_BROWSER_EXECUTABLE?{executablePath:process.env.NIVRAM_BROWSER_EXECUTABLE}:{})});
  async function bounded(promise,milliseconds,message){
    let timer;try{return await Promise.race([promise,new Promise((_resolve,reject)=>{timer=setTimeout(()=>reject(new Error(message)),milliseconds);})]);}finally{clearTimeout(timer);}
  }
  t.after(async()=>{
    let phase='starting',contextIndex=0;
    const started=performance.now();
    try{
      await bounded((async()=>{
        for(const context of browser.contexts()){
          phase=`closing context ${++contextIndex} (${context.pages().length} pages)`;
          await context.close();
        }
        // Local Browser.close waits for its process and temporary-profile cleanup.
        phase='closing directly launched browser';
        await browser.close();
        phase='checking closed browser';
        assert.equal(browser.isConnected(),false,'The owned local browser must disconnect.');
        assert.equal(browser.contexts().length,0,'The owned local browser must close every context.');
      })(),15000,'The directly launched test browser did not close within 15 seconds.');
      t.diagnostic('The isolated local browser closed; the outer Windows job receipt separately verifies process-tree exit. Native app shutdown is a separate check.');
    }catch(error){
      t.diagnostic(`Local browser cleanup failed at ${phase}; elapsed ${Math.round(performance.now()-started)} ms; connection open: ${browser.isConnected()}; contexts: ${browser.contexts().length}.`);
      // Failure remains a failed check. The supervisor can stop only this newly
      // assigned job; no browser is selected by name or a user-profile wildcard.
      throw error;
    }
  });
  return browser;
}
