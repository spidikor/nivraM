import test from 'node:test';
import assert from 'node:assert/strict';
import {projectWithProfile} from '../public/modules/profiles.mjs';
import {DEFAULT_STYLE,DEFAULT_EXPORT} from '../public/modules/document.mjs';
const document={schema:'nivram/1',graph:{atoms:[{id:'a',symbol:'C',x:1.2345,y:-6.789,isotope:13,charge:0}],bonds:[]},
  style:{...DEFAULT_STYLE},export:{...DEFAULT_EXPORT},analysis:{source:'retained'}};
const profile={schema:'nivram-profile/1',id:'a'.repeat(32),revision:3,name:'Paper',style:{...DEFAULT_STYLE,bondWidth:1.5},export:{...DEFAULT_EXPORT,width:9999}};
test('applying a profile preserves exact geometry, chemistry, provenance and source objects',()=>{
  const before=structuredClone(document),result=projectWithProfile(document,profile);
  assert.deepEqual(result.graph,before.graph);assert.deepEqual(result.analysis,before.analysis);
  assert.deepEqual(document,before);assert.equal(result.style.bondWidth,1.5);assert.equal(result.export.width,9999);
  result.graph.atoms[0].x=5;assert.equal(document.graph.atoms[0].x,1.2345);
});
test('future profiles and invalid export settings are rejected without document mutation',()=>{
  const before=structuredClone(document);
  assert.throws(()=>projectWithProfile(document,{...profile,schema:'nivram-profile/99'}));
  assert.throws(()=>projectWithProfile(document,{...profile,export:{...profile.export,width:10000}}));
  assert.deepEqual(document,before);
});
