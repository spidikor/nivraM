/** Bounded supplied-coordinate reader. No chemistry inference or document mutation. */
export const MAX_MOL_BYTES=262144;
export const MAX_ATOMS=256, MAX_BONDS=512;
const ELEMENTS=new Set('H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og'.split(' '));
function fail(message){throw new Error(message);}
function integer(field){if(!/^\s*\d+\s*$/.test(field))fail('Unsupported MOL integer field.');return Number(field);}
function zeroFields(text){return text.trim()===''||text.trim().split(/\s+/).every(value=>/^0+$/.test(value));}
function coordinate(field){if(!/^\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*$/.test(field))fail('Coordinates must be finite decimal fields.');const n=Number(field);if(!Number.isFinite(n)||Math.abs(n)>10000)fail('Coordinate magnitude exceeds this viewer’s 10,000-unit bound.');return n;}
// Declaration-only subset, not chemistry validation. Stereo and valence remain
// native-reader-only; the explicit opt-in is preflight for a fresh bounded worker,
// never chemistry validation or permission to adopt its output directly.
function parseDeclaredV3000(text,lines,allowNativeDeclarations){
  const bad=()=>fail('Only bounded V3000 CHG, MASS, RAD and atom-map declarations are supported here; stereo, valence and other properties require a chemistry-qualified reader.');
  const need=ok=>{if(!ok)bad();};
  if(lines.at(-1)==='')lines=lines.slice(0,-1);
  need(lines.every(line=>line.length<=512&&!/[\x00-\x1f]/.test(line)));
  need(lines[3]==='  0  0  0  0  0  0  0  0  0  0999 V3000'&&lines[4]==='M  V30 BEGIN CTAB');
  const counts=/^M  V30 COUNTS (\d+) (\d+) 0 0 0$/.exec(lines[5]||'');need(counts);
  const na=Number(counts[1]),nb=Number(counts[2]);need(na>=1&&na<=128&&nb<=256);
  need(lines.length===(nb?12+na+nb:10+na)&&lines[6]==='M  V30 BEGIN ATOM'&&lines[7+na]==='M  V30 END ATOM');
  need(lines.at(-2)==='M  V30 END CTAB'&&lines.at(-1)==='M  END');
  if(nb)need(lines[8+na]==='M  V30 BEGIN BOND'&&lines.at(-3)==='M  V30 END BOND');
  const elements=new Set('H B C N O F Si P S Cl Br I'.split(' ')),atoms=[],bonds=[],pairs=new Set();
  for(let i=0;i<na;i++){
    const f=lines[7+i].trim().split(/\s+/);
    need(f.length>=8&&f[0]==='M'&&f[1]==='V30'&&f[2]===String(i+1)&&elements.has(f[3])&&/^(0|[1-9]\d{0,5})$/.test(f[7]));
    const declarations={};
    for(const field of f.slice(8)){
      const match=(allowNativeDeclarations?/^(CHG|MASS|RAD|CFG|VAL)=(-?\d+)$/:/^(CHG|MASS|RAD)=(-?\d+)$/).exec(field);need(match);
      const [,key,raw]=match,value=Number(raw);
      need(!Object.hasOwn(declarations,key)&&(key==='CHG'?value>=-8&&value<=8:key==='MASS'?value>=1&&value<=400:key==='RAD'?value>=0&&value<=3:key==='CFG'?value>=1&&value<=2:value>=-1&&value<=8));
      declarations[key]=value;
    }
    need(f.slice(4,7).every(v=>/^-?\d+(?:\.\d+)?$/.test(v)));
    atoms.push(Object.freeze({id:i+1,element:f[3],x:coordinate(f[4]),y:coordinate(f[5]),z:coordinate(f[6]),charge:declarations.CHG??0,isotope:declarations.MASS??0,atomMap:Number(f[7]),...(Object.hasOwn(declarations,'RAD')?{radicalCode:declarations.RAD}:{}),declarations:Object.freeze(declarations)}));
  }
  for(let i=0;i<nb;i++){
    const f=lines[9+na+i].trim().split(/\s+/);
    need((allowNativeDeclarations?f.length>=6:f.length===6)&&f[0]==='M'&&f[1]==='V30'&&f[2]===String(i+1)&&/^[123]$/.test(f[3])&&f.slice(4,6).every(v=>/^\d+$/.test(v)));
    const a=Number(f[4]),b=Number(f[5]),pair=[a,b].sort((x,y)=>x-y).join(':');
    need(a>=1&&a<=na&&b>=1&&b<=na&&a!==b&&!pairs.has(pair));pairs.add(pair);
    const declarations={};
    for(const field of f.slice(6)){const m=/^CFG=(-?\d+)$/.exec(field);need(m&&[1,3].includes(Number(m[1]))&&f[3]==='1'&&!Object.hasOwn(declarations,'CFG'));declarations.CFG=Number(m[1]);}
    bonds.push(Object.freeze({id:i+1,a,b,order:Number(f[3]),...(Object.keys(declarations).length?{declarations:Object.freeze(declarations)}:{})}));
  }
  need(!atoms.some(a=>Object.hasOwn(a.declarations,'RAD'))||!atoms.some(a=>Object.hasOwn(a.declarations,'CFG')||Object.hasOwn(a.declarations,'VAL'))&&!bonds.some(b=>b.declarations));
  return Object.freeze({kind:'supplied_coordinates',format:'MOL V3000',chemistryVerified:false,...(atoms.some(a=>Object.hasOwn(a.declarations,'CFG')||Object.hasOwn(a.declarations,'VAL'))||bonds.some(b=>b.declarations)?{requiresNativeValidation:true}:{}),sourceText:text,atoms:Object.freeze(atoms),bonds:Object.freeze(bonds)});
}
export function parseSuppliedMol(bytes,{allowNativeDeclarations=false}={}){
  if(!(bytes instanceof Uint8Array)||!bytes.length||bytes.length>MAX_MOL_BYTES)fail('Choose a MOL file between 1 byte and 256 KiB.');
  if(bytes.some(b=>b!==9&&b!==10&&b!==13&&(b<32||b>126)))fail('This viewer supports ASCII MOL text only.');
  const text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);
  if(/\r(?!\n)/.test(text))fail('Use LF or CRLF line endings.');
  const lines=text.split(/\r?\n/);
  if(lines.length<5||lines[1].slice(20,22)!=='3D')fail('The MOL header must explicitly declare 3D coordinates. 2D files are not converted here.');
  if(lines.some(line=>line.includes('$$$$')))fail('SDF records are not supported here; supply one supported MOL V2000 or supported V3000 record.');
  const count=lines[3];
  if(count.endsWith('V3000'))return parseDeclaredV3000(text,lines,allowNativeDeclarations);
  if(count.length<39||count.slice(33).trim()!=='V2000')fail('Only a single supported MOL V2000 or supported V3000 record is supported; PDB and XYZ require separate readers.');
  const na=integer(count.slice(0,3)), nb=integer(count.slice(3,6));
  if(na<1||na>MAX_ATOMS||nb>MAX_BONDS)fail('This viewer supports 1–256 atoms and at most 512 bonds.');
  if(!zeroFields(count.slice(6,30))||integer(count.slice(30,33))!==999)fail('Counts flags, query records and declared chirality flags are not supported by this reader.');
  if(lines.length<5+na+nb)fail('The MOL atom or bond block is truncated.');
  const atoms=[];
  for(let i=0;i<na;i++){
    const line=lines[4+i];
    if(line.length<34||line[30]!==' ')fail('Unsupported or truncated MOL atom record.');
    const element=line.slice(31,34).trim();
    if(!ELEMENTS.has(element))fail('Query, wildcard and unknown atom labels are not supported by this reader.');
    // Absolute M ISO only: legacy mass differences depend on a reference table,
    // and mixed inline/M ISO interpretation differs across native readers.
    const chargeField=line.slice(36,39),code=chargeField.trim()===''?0:integer(chargeField);
    if(!zeroFields(line.slice(34,36))||!zeroFields(line.slice(39))||![0,1,2,3,5,6,7].includes(code)||(code!==0&&line.length<39))fail('Only inline charge codes and absolute M CHG/M ISO/M RAD declarations are supported; mass differences, inline radicals, parity, maps and other atom properties reject.');
    atoms.push({id:i+1,element,x:coordinate(line.slice(0,10)),y:coordinate(line.slice(10,20)),z:coordinate(line.slice(20,30)),charge:code===0?0:4-code,isotope:0,atomMap:0});
  }
  const bonds=[],pairs=new Set();
  for(let i=0;i<nb;i++){
    const line=lines[4+na+i];
    if(line.length<9)fail('The MOL bond block is truncated.');
    const a=integer(line.slice(0,3)),b=integer(line.slice(3,6)),order=integer(line.slice(6,9));
    if(a<1||a>na||b<1||b>na||a===b)fail('Bond endpoints do not identify two supported atoms.');
    if(![1,2,3,4].includes(order)||!zeroFields(line.slice(9)))fail('Query/stereo/topology bond fields are not supported here; only plain orders 1, 2, 3 or aromatic 4 are displayed.');
    const pair=[a,b].sort((x,y)=>x-y).join(':');if(pairs.has(pair))fail('Duplicate bond records are not supported.');pairs.add(pair);
    bonds.push(Object.freeze({id:i+1,a,b,order}));
  }
  let cursor=4+na+nb;
  const assigned={CHG:new Set(),ISO:new Set(),RAD:new Set()};let hasCharges=false;
  while(lines[cursor]!==undefined&&lines[cursor]!=='M  END'){
    const line=lines[cursor++],kind=line.slice(3,6);
    if(!/^M  (CHG|ISO|RAD)/.test(line)||line.length<9)fail('Unsupported V2000 property record.');
    const n=integer(line.slice(6,9));
    if(n<1||n>8||line.length!==9+8*n)fail('V2000 properties require 1–8 fixed-width pairs.');
    if((kind==='CHG'||kind==='RAD')&&!hasCharges){atoms.forEach(a=>a.charge=0);hasCharges=true;}
    for(let j=0;j<n;j++){
      const id=integer(line.slice(9+8*j,13+8*j)),raw=line.slice(13+8*j,17+8*j);
      if(!/^ *-?\d+ *$/.test(raw))fail('Unsupported V2000 property integer.');
      const value=Number(raw);
      if(id<1||id>na||assigned[kind].has(id)||(kind==='CHG'?value< -8||value>8:kind==='RAD'?value<0||value>3:value<1||value>400))fail('Out-of-range or duplicate V2000 property declaration.');
      assigned[kind].add(id);atoms[id-1][kind==='CHG'?'charge':kind==='RAD'?'radicalCode':'isotope']=value;
    }
  }
  if(lines[cursor]!=='M  END'||lines.slice(cursor+1).some(line=>line.trim()!==''))fail('Missing M END or unsupported trailing content.');
  atoms.forEach(Object.freeze);
  return Object.freeze({kind:'supplied_coordinates',chemistryVerified:false,sourceText:text,atoms:Object.freeze(atoms),bonds:Object.freeze(bonds)});
}
export async function suppliedProvenance(model,cryptoApi=globalThis.crypto){
  const bytes=new TextEncoder().encode(model.sourceText);
  const hash=Array.from(new Uint8Array(await cryptoApi.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
  return Object.freeze({schema:model.atoms.some(a=>Object.hasOwn(a,'radicalCode'))?'nivram-supplied-3d-view/3':'nivram-supplied-3d-view/1',origin:'supplied_coordinates',format:model.format||'MOL V2000',sourceSha256:hash,sourceBytes:bytes.length,atomCount:model.atoms.length,bondCount:model.bonds.length,chemistryVerified:false,coordinatesAltered:false,generated:false,experimentalStructureClaim:false,units:'MOL convention: angstrom; caller declaration, not independently verified',limitations:[model.atoms.some(a=>Object.hasOwn(a,'radicalCode'))?'RAD codes are literal none/singlet/doublet/triplet declarations, not electron counts or verified spin. Coordinates and all other properties remain unverified.':'Coordinates, charges, isotopes, atom maps and bonds are declarations, not chemically validated or verified identities.','View rotation and zoom do not change source coordinates.','No conformer generation, recognition, minimization or population prediction is performed.','Export is the exact accepted input bytes, not a converted or optimized structure.']});
}
export function projectSupplied(model,{yaw=0,pitch=0,zoom=1}={},width=640,height=400){
  if(![yaw,pitch,zoom,width,height].every(Number.isFinite)||zoom<0.2||zoom>5||width<=0||height<=0)fail('Invalid viewer transform.');
  const xs=model.atoms.map(a=>a.x),ys=model.atoms.map(a=>a.y),zs=model.atoms.map(a=>a.z);
  const center=[(Math.min(...xs)+Math.max(...xs))/2,(Math.min(...ys)+Math.max(...ys))/2,(Math.min(...zs)+Math.max(...zs))/2];
  const radius=Math.max(0.5,...model.atoms.map(a=>Math.hypot(a.x-center[0],a.y-center[1],a.z-center[2])));
  const scale=Math.max(1,Math.min(width,height)-64)/(2*radius)*zoom,cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
  return model.atoms.map(a=>{const x=a.x-center[0],y=a.y-center[1],z=a.z-center[2],xx=cy*x+sy*z,zz=-sy*x+cy*z;return {id:a.id,element:a.element,charge:a.charge,isotope:a.isotope,atomMap:a.atomMap,...(Object.hasOwn(a,'radicalCode')?{radicalCode:a.radicalCode}:{}),x:width/2+xx*scale,y:height/2-(cp*y-sp*zz)*scale,z:sp*y+cp*zz};});
}
