import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import {parseSuppliedMol,suppliedProvenance,projectSupplied} from '../public/modules/supplied-3d.mjs';
import {createThreeDRecord,validateThreeDRecord} from '../public/modules/three-d-record.mjs';
globalThis.crypto??=webcrypto;
const fixtures=JSON.parse(readFileSync(new URL('./fixtures/supplied-v2000-properties.json',import.meta.url)));
for(const f of fixtures.valid)for(const newline of ['\n','\r\n']){
 const text=f.text.replaceAll('\n',newline),m=parseSuppliedMol(new TextEncoder().encode(text));
 assert.deepEqual(m.atoms.map(a=>a.charge),f.charges,f.name);assert.deepEqual(m.atoms.map(a=>a.isotope),f.isotopes,f.name);
 assert(m.atoms.every(Object.isFrozen));assert.equal(m.requiresNativeValidation,undefined);assert.equal(m.chemistryVerified,false);
 projectSupplied(m,{yaw:1,pitch:.3,zoom:2});assert.equal(m.sourceText,text);
 const p=await suppliedProvenance(m),record=await createThreeDRecord({model:m,provenance:p},'test');await validateThreeDRecord(record);assert.equal(record.molText,text);
}
const invalid=fixtures.invalid.filter(f=>f.name!=='radical');
for(const f of invalid)assert.throws(()=>parseSuppliedMol(new TextEncoder().encode(f.text)),undefined,f.name);
console.log(`PASS V2000 ${fixtures.valid.length} fixtures × LF/CRLF; ${invalid.length} negative fixtures; immutable effective declarations, projection, exact metadata recovery.`);
