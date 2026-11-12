/** Session-only, content-free observations; these never grant download permission. */
const LIMIT=1_000_000_000, RETAINED=4;
const FORMATS=new Set(['json','csv','png']);
function booleanOrUnavailable(read){
  try{const value=read();return typeof value==='boolean'?value:null;}catch{return null;}
}
function dispatchObserver(row,readActivation){
  return ()=>{
    if(row.dispatch_observed)return;
    row.dispatch_observed=true;
    row.dispatch_active=booleanOrUnavailable(readActivation);
  };
}
export function createReferenceExportTrace(readActivation=()=>globalThis.navigator?.userActivation?.isActive){
  let seen=0,dropped=0;
  const events=[];
  return Object.freeze({
    begin(format,event){
      if(!FORMATS.has(format))return ()=>{};
      seen=Math.min(LIMIT,seen+1);
      const row={sequence:seen,format,trusted:booleanOrUnavailable(()=>event?.isTrusted),
        handler_active:booleanOrUnavailable(readActivation),dispatch_active:null,dispatch_observed:false};
      events.push(row);
      if(events.length>RETAINED){events.shift();dropped=Math.min(LIMIT,dropped+1);}
      return dispatchObserver(row,readActivation);
    },
    snapshot(){return {schema:1,seen,dropped,events:events.map(row=>({...row}))};}
  });
}
export const referenceExportTrace=createReferenceExportTrace();
export const referenceExportDiagnostics=()=>referenceExportTrace.snapshot();
