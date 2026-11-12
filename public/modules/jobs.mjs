import {cloneData} from './document.mjs';

function immutable(value){if(value&&typeof value==='object'){for(const child of Object.values(value))immutable(child);Object.freeze(value);}return value;}
export function createJobController(getRevision,{timeoutMs=30000}={}){
  let sequence=0;const active=new Map();
  return Object.freeze({
    begin(channel,{input=null,settings={},timeout=timeoutMs}={}){
      active.get(channel)?.cancel('A newer operation replaced this pending request.');
      const controller=new AbortController(),revision=getRevision(),jobId=++sequence;
      const snapshot=immutable(cloneData(input)),options=immutable(cloneData(settings));
      let finished=false,timer;
      const task={
        id:jobId,channel,revision,input:snapshot,settings:options,signal:controller.signal,
        assertCurrent(){
          if(controller.signal.aborted)throw controller.signal.reason;
          if(finished||active.get(channel)!==task||getRevision()!==revision)throw new Error('The document changed while this operation was running. Its result was discarded.');
        },
        cancel(message='The operation was cancelled.',reason='AbortError'){
          if(!controller.signal.aborted)controller.abort(new DOMException(message,reason));
          clearTimeout(timer);if(active.get(channel)===task)active.delete(channel);
        },
        finish(){finished=true;clearTimeout(timer);if(active.get(channel)===task)active.delete(channel);},
        async wait(promise){
          const pending=Promise.resolve(promise);
          try{task.assertCurrent();}catch(error){pending.catch(()=>{});throw error;}
          let onAbort;
          try{return await Promise.race([pending,new Promise((_resolve,reject)=>{onAbort=()=>reject(controller.signal.reason);controller.signal.addEventListener('abort',onAbort,{once:true});})]);}
          finally{controller.signal.removeEventListener('abort',onAbort);}
        }
      };
      active.set(channel,task);
      timer=setTimeout(()=>task.cancel('The operation timed out. The preceding document is retained.','TimeoutError'),timeout);
      return Object.freeze(task);
    },
    cancelAll(message){for(const task of [...active.values()])task.cancel(message);}
  });
}
