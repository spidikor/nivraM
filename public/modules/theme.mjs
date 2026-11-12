/** Presentation stays separate from the document; native storage survives WebView cleanup. */
export function initializeThemeToggle(root,button,{warn=()=>{}}={}){
  let theme='light',revision=0,generation=null,native=null,connected=false,loaded=false;
  let queue=Promise.resolve(),savedRevision=0,pending=0,error=null,suspended=false;
  let resolveReady;
  const ready=new Promise(resolve=>{resolveReady=resolve;});
  const apply=()=>{
    root.setAttribute('data-theme',theme);
    button.setAttribute('aria-pressed',String(theme==='dark'));
  };
  const failed=()=>{
    error='Your light/dark choice could not be saved for the next session.';
    warn(error);
  };
  function save(){
    const choice=theme,sequence=revision;
    pending++;
    queue=queue.then(async()=>{
      try{
        const result=await native.set_appearance(choice,generation,sequence);
        if(result?.status!=='saved'||result.theme!==choice||result.revision!==sequence)throw new Error('Appearance save unconfirmed');
        savedRevision=Math.max(savedRevision,sequence);
        if(sequence===revision)error=null;
      }catch{if(sequence===revision)failed();}
      finally{pending--;}
    });
  }
  button.addEventListener('click',()=>{
    if(suspended)return;
    theme=theme==='dark'?'light':'dark';revision++;apply();
    if(loaded&&generation)save();
  });
  apply();
  return Object.freeze({
    ready,
    getState:()=>({theme,ready:loaded,pending:pending>0,error}),
    async connect(bridge){
      if(connected)return ready;
      connected=true;native=bridge;
      try{
        const result=await native.get_appearance();
        if(!['light','dark'].includes(result?.theme)||!['loaded','default','unavailable'].includes(result?.status)||
           typeof result?.generation!=='string'||!/^[0-9a-f]{32}$/.test(result.generation))throw new Error('Appearance read unconfirmed');
        generation=result.generation;
        if(revision===0){theme=result.theme;apply();}
        if(result.status==='unavailable'){
          error='The saved light/dark choice could not be read. Choose a mode to save it again.';warn(error);
        }
        loaded=true;
        if(revision>0)save();
      }catch{
        loaded=true;error='The saved light/dark choice could not be loaded. Restart the app to try again.';warn(error);
      }finally{resolveReady();}
    },
    async flush(){await ready;await queue;return savedRevision>=revision&&!error;},
    beginCloseReview(){
      suspended=true;button.disabled=true;
      return {theme,revision,generation};
    },
    endCloseReview(){suspended=false;button.disabled=false;}
  });
}
