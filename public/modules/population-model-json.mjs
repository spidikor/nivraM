/** Bounded JSON transport reader retaining only model/condition number tokens.
 * Python integers and binary64 floats have distinct model meanings. The UI still
 * uses numbers; checked source tokens provide the lossless export authority.
 */
const pointer = key => String(key).replaceAll('~', '~0').replaceAll('/', '~1');
const whitespace = /[\x20\x09\x0a\x0d]/;
const digit = ch => ch>='0'&&ch<='9';
const now = () => performance.now();
function* parsePopulationJson(text, modelPath, collectConditions) {
  if (typeof text !== 'string' || text.length > 16 * 1024 * 1024) throw new TypeError('Population JSON exceeds its transport limit.');
  let offset=0, nodes=0, model=null, nextCheck=0, sliceStart=now();
  const conditions=new Map();
  const bad=()=>{throw new TypeError('Population JSON is malformed, duplicated or exceeds its size/depth limit.');};
  function* checkpoint(){
    if(offset>=nextCheck){nextCheck=offset+4096;if(now()-sliceStart>=4){yield;sliceStart=now();}}
  }
  function* space(){while(offset<text.length&&whitespace.test(text[offset])){offset++;if(offset>=nextCheck)yield* checkpoint();}}
  function* string(){
    const start=offset++;
    while(offset<text.length){
      if(offset>=nextCheck)yield* checkpoint();
      // Every population-model/response string is already bounded to 8192
      // UTF-16 units by presentation validation, at most six JSON characters each.
      if(offset-start>49153)bad();
      const ch=text[offset++];
      if(ch==='"'){
        const value=JSON.parse(text.slice(start,offset));if(value.length>8192)bad();return value;
      }
      if(ch==='\\')offset++;
    }
    return bad();
  }
  function* digits(){if(!digit(text[offset]))bad();while(digit(text[offset])){offset++;if(offset>=nextCheck)yield* checkpoint();}}
  function* value(path,depth,numbers=null,relative=''){
    if(++nodes>5000000||depth>20)bad();
    yield* space();yield* checkpoint();const start=offset;
    const selected=path.length===modelPath.length&&path.every((key,index)=>key===modelPath[index]);
    const condition=collectConditions&&path.length===5&&path[0]==='envelope'&&path[1]==='samples'
      && /^(0|[1-9]\d*)$/.test(path[2])&&path[3]==='result'&&path[4]==='conditions';
    if(selected||condition){numbers=new Map();relative='';}
    const ch=text[offset];let result;
    if(ch==='"')result=yield* string();
    else if(ch==='{'||ch==='['){
      const array=ch==='[';
      // Small flat computed vectors contain no source-model tokens. Native JSON
      // parsing avoids per-component paths; larger or nested data stays cooperative.
      if(array&&numbers===null&&path.length===5&&path[0]==='envelope'&&path[1]==='samples'
          &&path[3]==='result'&&['fractions','percentages','log_fractions'].includes(path[4])){
        const close=text.indexOf(']',offset+1);
        if(close>=0&&close-offset<=131072){
          const body=text.slice(offset+1,close);
          if(!/[\[{}"]/.test(body)){
            let components=body.trim()?1:0,comma=-1;
            while((comma=body.indexOf(',',comma+1))!==-1)if(++components>4096)bad();
            const vector=JSON.parse('['+body+']');
            if(vector.every(item=>typeof item==='number')){
              nodes+=vector.length;if(nodes>5000000||depth+1>20)bad();
              offset=close+1;yield* checkpoint();return vector;
            }
          }
        }
      }
      result=array?[]:Object.create(null);offset++;yield* space();
      const closing=array?']':'}';
      if(text[offset]!==closing){
        let index=0;
        while(true){
          yield* space();let key;
          if(array)key=String(index++);
          else{if(text[offset]!=='"')bad();key=yield* string();if(Object.hasOwn(result,key))bad();yield* space();if(text[offset++]!==':')bad();}
          result[key]=yield* value([...path,key],depth+1,numbers,relative+'/'+pointer(key));
          yield* space();if(text[offset]===closing)break;if(text[offset++]!==',')bad();
        }
      }
      offset++;
    }else if(ch==='-'||digit(ch)){
      if(ch==='-')offset++;
      if(text[offset]==='0')offset++;else yield* digits();
      if(text[offset]==='.'){offset++;yield* digits();}
      if(text[offset]==='e'||text[offset]==='E'){offset++;if(text[offset]==='+'||text[offset]==='-')offset++;yield* digits();}
      const raw=text.slice(start,offset);result=Number(raw);numbers?.set(relative,raw);
    }else if(text.startsWith('true',offset)){offset+=4;result=true;}
    else if(text.startsWith('false',offset)){offset+=5;result=false;}
    else if(text.startsWith('null',offset)){offset+=4;result=null;}
    else bad();
    if(selected)model={text:text.slice(start,offset),numbers};
    if(condition)conditions.set(Number(path[2]),{numbers});
    return result;
  }
  const parsed=yield* value([],0);yield* space();if(offset!==text.length)bad();
  return {value:parsed,model,conditions};
}

/** Synchronous bounded reader for the at-most-2 MiB original model and tests. */
export function readPopulationJson(text,modelPath=[],collectConditions=false){
  const parser=parsePopulationJson(text,modelPath,collectConditions);
  let step;do{step=parser.next();}while(!step.done);return step.value;
}

/** Response parsing yields between bounded scans so owned cancellation can run. */
export async function readPopulationJsonAsync(text,modelPath=[],collectConditions=false,{signal,deadline=Infinity}={}){
  const parser=parsePopulationJson(text,modelPath,collectConditions);
  const check=()=>{
    if(signal?.aborted)throw signal.reason;
    if(now()>=deadline)throw new DOMException('The operation timed out. The preceding document is retained.','TimeoutError');
  };
  try{
    while(true){
      check();const step=parser.next();check();if(step.done)return step.value;
      await new Promise(resolve=>setTimeout(resolve,0));
    }
  }finally{parser.return();}
}

/** Match evaluator numeric meaning, not spelling or JS's rounded integers. */
export function sameModelNumbers(left,right){
  if(!left||!right||left.numbers.size!==right.numbers.size)return false;
  for(const [path,raw] of left.numbers){
    const other=right.numbers.get(path);if(other===undefined)return false;
    const integral=!/[.eE]/.test(raw),otherIntegral=!/[.eE]/.test(other);
    if(integral!==otherIntegral)return false;
    if(integral){if((raw==='-0'?'0':raw)!==(other==='-0'?'0':other))return false;}
    else if(!Number.isFinite(Number(raw))||!Object.is(Number(raw),Number(other)))return false;
  }
  return true;
}

/** Compact after typed binding, retaining integer/float and signed floating zero. */
export function compactModelNumber(raw){
  if(!/[.eE]/.test(raw))return raw==='-0'?'0':raw;
  const value=Number(raw);if(!Number.isFinite(value))throw new TypeError('Model number must be finite.');
  if(Object.is(value,-0))return '-0.0';
  const encoded=String(value);return /[.eE]/.test(encoded)?encoded:encoded+'.0';
}
