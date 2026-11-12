import {canonicalElement,isElementPrefix} from './elements.mjs';

// The extension window is independent of rendering latency. Only this gesture's
// own continuation can wait behind its pending authoritative update.
export function createElementEntry({getTarget,getRevision,isBusy,beginGroup,endGroup,apply,preview,clearPreview,now=()=>performance.now(),schedule=setTimeout,unschedule=clearTimeout}){
  let current=null,serial=0;
  const same=(a,b)=>a&&b&&a.id===b.id&&a.x===b.x&&a.y===b.y;
  function finish(g){if(!g.pending&&!g.queue.length&&!g.open){endGroup(g.token);if(current===g)current=null;}}
  function close(){const g=current;if(!g)return;g.open=false;unschedule(g.timer);finish(g);}
  async function drain(g){
    if(g.pending)return;g.pending=true;
    try{
      while(g.queue.length&&!g.cancelled){
        const symbol=g.queue.shift();
        if(getRevision()!==g.revision){g.cancelled=true;break;}
        const accepted=await apply(symbol,g.token,g.target.id);
        if(!accepted){g.cancelled=true;g.open=false;g.queue.length=0;break;}
        const nextRevision=getRevision();if(nextRevision!==g.revision)g.accepted++;g.revision=nextRevision;
      }
    }finally{
      g.pending=false;
      if(g.cancelled){g.open=false;g.queue.length=0;}
      clearPreview();finish(g);
    }
  }
  function enqueue(g,symbol){preview(g.target.id,symbol);g.queue.push(symbol);if(!g.pending)g.done=drain(g);}
  function input(key){
    if(!/^[a-z]$/i.test(key))return false;
    const target=getTarget();if(!target)return false;
    const time=now();let g=current;
    if(g&&(!g.open||time-g.started>=500||!same(target,g.target))){close();g=null;}
    if(g){
      const combined=canonicalElement(g.first+key);g.open=false;unschedule(g.timer);
      if(combined)enqueue(g,combined);else finish(g);
      return true;
    }
    if(isBusy()||current?.pending)return false;
    const symbol=canonicalElement(key);
    if(!symbol&&!isElementPrefix(key))return false;
    g={id:++serial,first:key,target:{...target},started:time,revision:getRevision(),token:beginGroup(),open:true,pending:false,queue:[],accepted:0,cancelled:false,done:Promise.resolve()};
    current=g;g.timer=schedule(()=>{g.open=false;finish(g);},500);
    if(symbol)enqueue(g,symbol);
    return true;
  }
  async function cancel(){
    const g=current;if(!g)return 0;
    g.cancelled=true;g.open=false;g.queue.length=0;unschedule(g.timer);endGroup(g.token);clearPreview();
    await g.done;finish(g);return g.accepted;
  }
  return {input,close,cancel,active:()=>!!current,pending:()=>!!current?.pending};
}
