// Read original bytes only. PNG parsing remains authoritative in the local worker.
export const MAX_PNG_INPUT=64*1024*1024;
const pngSignature=[137,80,78,71,13,10,26,10];
const isPng=file=>file?.type==='image/png'||typeof file?.name==='string'&&/\.png$/i.test(file.name);
const editable=target=>Boolean(target?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"])'));

export async function preparePngIntake(file,task){
  task.assertCurrent();
  if(!file||!Number.isSafeInteger(file.size)||file.size<8||file.size>MAX_PNG_INPUT||typeof file.arrayBuffer!=='function')throw new Error('Select one PNG no larger than 64 MiB.');
  const filename=file.name||'clipboard.png';
  if(!isPng(file)||typeof filename!=='string'||filename.length>255||!/\.png$/i.test(filename)||/[\x00-\x1f\x7f/\\:<>"|?*]/.test(filename))throw new Error('Select a PNG with a filename that does not contain a folder or path.');
  const buffer=await task.wait(file.arrayBuffer());task.assertCurrent();
  if(!(buffer instanceof ArrayBuffer)||buffer.byteLength!==file.size)throw new Error('The PNG size changed while reading it.');
  const bytes=new Uint8Array(buffer);
  if(pngSignature.some((byte,index)=>bytes[index]!==byte))throw new Error('The selected bytes are not a PNG image.');
  const digest=await task.wait(crypto.subtle.digest('SHA-256',buffer));task.assertCurrent();
  const parts=[];
  // Each block is divisible by three, so concatenating its base64 preserves bytes.
  for(let offset=0;offset<bytes.length;offset+=24576){
    task.assertCurrent();parts.push(btoa(String.fromCharCode(...bytes.subarray(offset,offset+24576))));
    if(offset&&offset%(24576*64)===0){await task.wait(new Promise(resolve=>setTimeout(resolve,0)));task.assertCurrent();}
  }
  return {filename,pngBase64:parts.join(''),sha256:[...new Uint8Array(digest)].map(byte=>byte.toString(16).padStart(2,'0')).join('')};
}

export function attachPngIntake({target,onFile,onError,canStart=()=>true}){
  function filesOf(transfer){
    const files=Array.from(transfer?.files||[]);
    if(files.length)return files;
    return Array.from(transfer?.items||[]).filter(item=>item.kind==='file').map(item=>item.getAsFile());
  }
  function submit(event,transfer){
    if(event.defaultPrevented||editable(event.target))return;
    const files=filesOf(transfer);if(!files.length)return;
    event.preventDefault();
    if(!canStart())return;
    if(files.length!==1||!files[0]||!isPng(files[0])){onError(new Error('Import one PNG image at a time.'));return;}
    try{Promise.resolve(onFile(files[0])).catch(onError);}catch(error){onError(error);}
  }
  const drop=event=>submit(event,event.dataTransfer);
  const paste=event=>submit(event,event.clipboardData);
  const dragover=event=>{
    if(event.defaultPrevented||editable(event.target)||!Array.from(event.dataTransfer?.types||[]).includes('Files'))return;
    event.preventDefault();event.dataTransfer.dropEffect=canStart()?'copy':'none';
  };
  const bindings={drop,paste,dragover};
  for(const [name,handler] of Object.entries(bindings))target.addEventListener(name,handler);
  return ()=>{for(const [name,handler] of Object.entries(bindings))target.removeEventListener(name,handler);};
}
