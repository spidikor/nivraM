import assert from 'node:assert/strict';
import {readFileSync,writeFileSync} from 'node:fs';
import {parseSuppliedMol,suppliedProvenance} from '../public/modules/supplied-3d.mjs';
import {createThreeDRecord,validateThreeDRecord} from '../public/modules/three-d-record.mjs';
import {hydrogenDisplay} from '../public/modules/three-d-display.mjs';
import {radicalLabel} from '../public/modules/supplied-3d-viewer.mjs';
const atom=(e,code=0)=>'    0.0000    0.0000    0.0000 '+e.padEnd(3)+' 0'+String(code).padStart(3)+'  0  0  0  0  0  0  0  0  0  0';
const prop=(k,id,v)=>`M  ${k}  1${String(id).padStart(4)}${String(v).padStart(4)}`;
const v2=(ps=[])=>['x',' '.repeat(20)+'3D','','  2  1  0  0  0  0            999 V2000',atom('C',3),atom('H'), '  1  2  1  0  0  0  0',...ps,'M  END',''].join('\n');
const v3=(p)=>['x',' '.repeat(20)+'3D','','  0  0  0  0  0  0  0  0  0  0999 V3000','M  V30 BEGIN CTAB','M  V30 COUNTS 2 1 0 0 0','M  V30 BEGIN ATOM','M  V30 1 C 0 0 0 7 CHG=1 MASS=13',`M  V30 2 H 1 0 0 0 ${p}`,'M  V30 END ATOM','M  V30 BEGIN BOND','M  V30 1 1 1 2','M  V30 END BOND','M  V30 END CTAB','M  END'].join('\n');
const parse=t=>parseSuppliedMol(new TextEncoder().encode(t),{allowNativeDeclarations:true});
const valid=[],invalid=[],records=[];
for(const code of [0,1,2,3])for(const source of [v2([prop('RAD',2,code),prop('ISO',1,13)]),v3(`RAD=${code}`)])for(const nl of ['\n','\r\n']){
 const text=source.replaceAll('\n',nl),m=parse(text);valid.push(text);assert.equal(m.atoms[1].radicalCode,code);assert.equal(m.atoms[0].isotope,13);assert.match(radicalLabel(m.atoms[1]),new RegExp(`RAD${code} .* declaration`));assert.equal(hydrogenDisplay(m,'implicit').hidden.size,0);
 const p=await suppliedProvenance(m),r=await createThreeDRecord({model:m,provenance:p},'test');assert.equal(r.schema,'nivram-png-coordinates/5');assert.equal((await validateThreeDRecord(r)).record.molText,text);records.push(r);
 for(const schema of ['nivram-png-coordinates/2','nivram-png-coordinates/3','nivram-png-coordinates/4'])await assert.rejects(()=>validateThreeDRecord({...r,schema}));await assert.rejects(()=>validateThreeDRecord({...r,molText:text+' '}));
}
for(const ps of [[prop('RAD',2,2)],[prop('RAD',2,2),prop('CHG',1,-2)],[prop('CHG',1,-2),prop('RAD',2,2)]]){const m=parse(v2(ps));assert.equal(m.atoms[0].charge,ps.length===1?0:-2);valid.push(v2(ps));}
for(const code of [-1,4,9])invalid.push(v2([prop('RAD',2,code)]),v3(`RAD=${code}`));
invalid.push(v2([prop('RAD',2,1),prop('RAD',2,2)]),v2([prop('RAD',0,2)]),v2([prop('RAD',3,2)]),v2([prop('RAD',2,2)]).replace('M  RAD  1','M  RAD  9'),v3('RAD=2 RAD=2'),v3('RAD=1.0'),v3('RAD=2 CFG=1'),v3('RAD=0 VAL=0'),v3('RAD=2').replace('M  V30 1 1 1 2','M  V30 1 1 1 2 CFG=1'),v2().replace(atom('C',3),atom('C',4)));
for(const text of invalid)assert.throws(()=>parse(text));
assert.equal(hydrogenDisplay(parse(v2([prop('CHG',1,0)])),'implicit').hidden.size,1);
if(process.env.RADICAL_FIXTURES)writeFileSync(process.env.RADICAL_FIXTURES,JSON.stringify({valid,invalid,records}));
console.log(`PASS supplied RAD: ${valid.length} positive sources, ${invalid.length} negatives, ${records.length} schema5 records, precedence, labels, special H and tampering`);

const presenceCases=JSON.parse(readFileSync(new URL('./fixtures/supplied-radical-presence-records.json',import.meta.url),'utf8'));
for(const {name,record} of presenceCases){
 await validateThreeDRecord(record);
 assert.equal(parse(record.molText).atoms.some(a=>Object.hasOwn(a,'radicalCode')),name==='leading_space_atom');
}
console.log('PASS parsed RAD presence: leading-space V3000 atom and legacy V2000 title do not diverge from schema semantics.');
