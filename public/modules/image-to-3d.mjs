import {sameSource} from './conformer-source.mjs';
/** Offer only after the review owner reports successful acceptance. Never accepts or generates on its own. */
export function mountImageTo3d({button,events,getDocument,open,generate,canOpen=()=>true}){
  if(!button||!events?.addEventListener)return {destroy(){}};
  let binding=null,document=null,unsubscribe=null,closing=false;
  function clear(){binding=null;button.hidden=true;unsubscribe?.();unsubscribe=null;}
  function offer(event){clear();if(closing)return;document=getDocument();if(!document||!event.detail||!sameSource(document.getSnapshot(),event.detail))return;binding={...event.detail};button.hidden=false;unsubscribe=document.subscribe(snapshot=>{if(binding&&!sameSource(snapshot,binding))clear();});}
  async function continueTo3d(){if(closing||!binding||getDocument()!==document||!sameSource(document.getSnapshot(),binding)){clear();return false;}if(!canOpen())return false;const expectedBinding=Object.freeze({...binding}),expectedDocument=document;clear();open();return generate({expectedBinding,expectedDocument});}
  const close=()=>{closing=true;clear();},end=()=>{closing=false;};
  const handlers=[[events,'nivram-image-accepted',offer],[events,'nivram-close-review',close],[events,'nivram-close-review-ended',end],[events,'pagehide',clear],[button,'click',continueTo3d]];
  for(const [node,type,fn] of handlers)node.addEventListener(type,fn);
  clear();return {destroy(){clear();for(const [node,type,fn] of handlers)node.removeEventListener(type,fn);}};
}
