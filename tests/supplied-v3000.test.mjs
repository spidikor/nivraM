import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {parseSuppliedMol,suppliedProvenance} from '../public/modules/supplied-3d.mjs';
import {createThreeDRecord,validateThreeDRecord} from '../public/modules/three-d-record.mjs';
globalThis.crypto??=webcrypto;
const enc=new TextEncoder();
const raw=['plain',' '.repeat(20)+'3D','','  0  0  0  0  0  0  0  0  0  0999 V3000','M  V30 BEGIN CTAB','M  V30 COUNTS 1 0 0 0 0','M  V30 BEGIN ATOM','M  V30 1 O -0.0000 0.1 0.2 0','M  V30 END ATOM','M  V30 END CTAB','M  END'].join('\n');
for(const text of [raw,raw+'\n',raw.replaceAll('\n','\r\n')+'\r\n']){
 const model=parseSuppliedMol(enc.encode(text)),provenance=await suppliedProvenance(model);
 assert.equal(model.sourceText,text);assert.equal(provenance.format,'MOL V3000');assert.equal(provenance.generated,false);
 const record=await createThreeDRecord({model,provenance},'test');assert.equal(record.schema,'nivram-png-coordinates/3');await validateThreeDRecord(record);
 await assert.rejects(validateThreeDRecord({...record,schema:'nivram-png-coordinates/2'}));
}
for(const bad of [raw+'\n\n',raw.replace('0.2 0','0.2 00'),raw.replace('0.2 0','0.2 0 CFG=1'),raw.replace('0.2 0','0.2 0 VAL=1'),raw.replace('0.2 0','NaN 0'),raw.replace('0.2 0','10001 0'),raw.replace('3D','2D'),raw.replace('COUNTS 1','COUNTS 129'),raw.replace('1 O','1 *'),raw+'\n$$$$',raw.replace('END CTAB','BEGIN SGROUP')])assert.throws(()=>parseSuppliedMol(enc.encode(bad)));
console.log('PASS plain V3000 parser and versioned supplied metadata; unsupported declarations rejected.');

for(const properties of ['7 CHG=-2 MASS=18','999999 MASS=400 CHG=8','0 CHG=-8','0 CHG=0']){
 const text=raw.replace('0.2 0',`0.2 ${properties}`),model=parseSuppliedMol(enc.encode(text));
 assert.equal(model.sourceText,text);assert.equal(model.chemistryVerified,false);
 assert.equal(model.atoms[0].atomMap,Number(properties.split(' ')[0]));
 const record=await createThreeDRecord({model,provenance:await suppliedProvenance(model)},'test');await validateThreeDRecord(record);
}
const atom=parseSuppliedMol(enc.encode(raw.replace('0.2 0','0.2 7 CHG=-2 MASS=18'))).atoms[0];
assert.equal(atom.charge,-2);assert.equal(atom.isotope,18);assert.deepEqual(atom.declarations,{CHG:-2,MASS:18});assert(Object.isFrozen(atom.declarations));
for(const props of ['1000000','-1','01','0 CHG=9','0 CHG=-9','0 MASS=0','0 MASS=401','0 CHG=1 CHG=2','0 MASS=18 MASS=18','0 CFG=2','0 VAL=0','0 CHG=1.2','0 CHG=NaN'])assert.throws(()=>parseSuppliedMol(enc.encode(raw.replace('0.2 0',`0.2 ${props}`))));
console.log('PASS declared charge, isotope mass number and atom-map bounds, retention and PNG metadata.');
