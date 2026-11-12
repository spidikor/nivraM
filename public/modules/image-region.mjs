// Receipt validation and source-pixel coordinates are separate from image display.
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const integer=value=>Number.isSafeInteger(value);
const sameRect=(a,b)=>a===null&&b===null||a&&b&&['x','y','width','height'].every(key=>a[key]===b[key]);
export function validateCrop(rect,sourceWidth,sourceHeight){
  if(!rect||!['x','y','width','height'].every(key=>integer(rect[key]))||rect.x<0||rect.y<0||rect.width<1||rect.height<1||rect.width>2048||rect.height>2048||rect.width*rect.height>1_000_000||rect.x+rect.width>sourceWidth||rect.y+rect.height>sourceHeight)throw new Error('Choose an integer crop inside the source, no more than 2048 pixels per side and 1,000,000 pixels in area.');
  return {x:rect.x,y:rect.y,width:rect.width,height:rect.height};
}
export function validateRegionReceipt(raw,expected){
  const {jobId,requestId,sourceSha256,sourceWidth,sourceHeight,mode,rect,selected}=expected;
  const sourceRect=mode==='overview'?{x:0,y:0,width:sourceWidth,height:sourceHeight}:rect;
  if(!raw||raw.schemaVersion!==1||raw.jobId!==jobId||raw.requestId!==requestId||raw.sourceSha256!==sourceSha256||raw.sourceWidth!==sourceWidth||raw.sourceHeight!==sourceHeight||raw.mode!==mode||!sameRect(raw.rect,sourceRect)||raw.resampling!==(mode==='overview'?'nearest-center':'none'))throw new Error('The image region does not match the current source and request.');
  if(!integer(raw.width)||!integer(raw.height)||raw.width<1||raw.height<1||!hash(raw.pngSha256)||typeof raw.pngBase64!=='string'||raw.pngBase64.length>5_592_408||!raw.pngBase64.length||raw.pngBase64.length%4||!/^[A-Za-z0-9+/]+={0,2}$/.test(raw.pngBase64))throw new Error('The image region returned invalid bounded PNG data.');
  const authority=mode==='crop'&&selected.length>0;
  if(raw.navigationOnly!==(mode==='overview')||(authority?typeof raw.reviewToken!=='string'||!/^[A-Za-z0-9_-]{8,128}$/.test(raw.reviewToken):raw.reviewToken!=null))throw new Error('The image region returned invalid review authority.');
  if(mode==='crop'){
    validateCrop(rect,sourceWidth,sourceHeight);
    if(raw.width!==rect.width||raw.height!==rect.height)throw new Error('The detail crop was resized. Import remains disabled.');
  }else{
    const edge=Math.max(sourceWidth,sourceHeight),width=edge<=960?sourceWidth:Math.max(1,Math.floor(sourceWidth*960/edge)),height=edge<=960?sourceHeight:Math.max(1,Math.floor(sourceHeight*960/edge));
    if(mode!=='overview'||raw.width!==width||raw.height!==height)throw new Error('The overview does not match its bounded source geometry.');
  }
  return {...raw,rect:{...sourceRect}};
}
export async function regionImageData(receipt){
  const bytes=Uint8Array.from(atob(receipt.pngBase64),character=>character.charCodeAt(0));
  if(bytes.length>4*1024*1024||bytes.length<33||[137,80,78,71,13,10,26,10].some((value,i)=>bytes[i]!==value))throw new Error('Invalid bounded PNG signature.');
  const header=new DataView(bytes.buffer);
  if(header.getUint32(8)!==13||header.getUint32(12)!==0x49484452||header.getUint32(16)!==receipt.width||header.getUint32(20)!==receipt.height)throw new Error('The detail PNG dimensions do not match its receipt.');
  const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(value=>value.toString(16).padStart(2,'0')).join('');
  if(digest!==receipt.pngSha256)throw new Error('The image region hash does not match its receipt.');
  return 'data:image/png;base64,'+receipt.pngBase64;
}
export function overviewPoint(clientX,clientY,box,sourceWidth,sourceHeight,overviewWidth=sourceWidth,overviewHeight=sourceHeight){
  const scale=Math.min(box.width/overviewWidth,box.height/overviewHeight),width=overviewWidth*scale,height=overviewHeight*scale;
  const x=clientX-box.left-(box.width-width)/2,y=clientY-box.top-(box.height-height)/2;
  if(!Number.isFinite(scale)||scale<=0||x<0||y<0||x>width||y>height)return null;
  return {x:x/width*sourceWidth,y:y/height*sourceHeight};
}
export function cropBetween(start,end,sourceWidth,sourceHeight){
  const x=Math.floor(Math.min(start.x,end.x)),y=Math.floor(Math.min(start.y,end.y));
  return validateCrop({x,y,width:Math.ceil(Math.max(start.x,end.x))-x,height:Math.ceil(Math.max(start.y,end.y))-y},sourceWidth,sourceHeight);
}
