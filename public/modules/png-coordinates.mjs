import {validateProject} from './document.mjs';
const enc=new TextEncoder(),MAX_RECORD=1024*1024;
export function snapshotCoordinates(graph){
  // Deliberately omit the project, analysis, filenames, source image and viewer.
  const clean=validateProject({schema:'nivram/1',graph}).graph;
  if(clean.atoms.length===0)throw new Error('Coordinate metadata requires a nonempty molecular graph.');
  // JSON permits escaped lone surrogates; strict UTF-8 recovery does not.
  function unicode(value){
    if(typeof value==='string'){
      for(const character of value){const code=character.codePointAt(0);if(code>=0xd800&&code<=0xdfff)throw new Error('Coordinate metadata requires valid Unicode strings.');}
    }else if(Array.isArray(value)){for(const item of value)unicode(item);}
    else if(value&&typeof value==='object'){for(const [key,item] of Object.entries(value)){unicode(key);unicode(item);}}
  }
  unicode(clean);
  const text=JSON.stringify(clean);
  if(enc.encode(text).length>MAX_RECORD/2)throw new Error('Coordinate metadata exceeds its bound.');
  return text;
}
