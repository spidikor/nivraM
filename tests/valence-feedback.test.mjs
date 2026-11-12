import test from 'node:test';
import assert from 'node:assert/strict';
import {valenceFeedback,paintValenceFeedback} from '../public/modules/valence-feedback.mjs';
import {createBond,editBond} from '../public/modules/edit-commands.mjs';
const t={x0:400,y0:250,xScale:60,yScale:-60};
const before={atoms:[{id:'center',symbol:'C',x:0,y:0},{id:'a',symbol:'C',x:1.5,y:0},{id:'b',symbol:'C',x:0,y:1.5}],bonds:[{id:'old',begin:'center',end:'a',order:1},{id:'neighbor',begin:'center',end:'b',order:1}]};
const diagnostic={code:'atom-valence',atomIds:['center']};
test('only proposed new bond geometry is red, not accepted neighbors; source is immutable',()=>{
 const saved=JSON.stringify(before);let serial=0;
 const after=createBond(before,{startAtomId:'center',endPosition:{x:0,y:-1.5},kind:'1',freeAngle:true,makeId:p=>p+(++serial)}).graph;
 const feedback=valenceFeedback(diagnostic,before,after,t);
 assert.equal(feedback.lines.length,1);assert.ok(!['old','neighbor'].includes(feedback.lines[0].id));
 assert.deepEqual(feedback.lines[0].start,{x:400,y:250});assert.deepEqual(feedback.lines[0].end,{x:400,y:340});
 assert.equal(feedback.label,'Rejected bond: invalid valence');assert.match(feedback.message,/Edit not applied: invalid valence at C/);assert.equal(JSON.stringify(before),saved);
});
test('shared pointer and keyboard editBond command maps changed bond only',()=>{
 const after=editBond(before,{bondId:'old',kind:'3'}).graph;
 assert.deepEqual(valenceFeedback(diagnostic,before,after,t).lines.map(b=>b.id),['old']);
});
test('atom-only valence change never blames unchanged adjacent bonds',()=>{
 const after=structuredClone(before);after.atoms[0].symbol='O';
 const result=valenceFeedback(diagnostic,before,after,t);assert.deepEqual(result.lines,[]);assert.deepEqual(result.points,[{id:'center',x:400,y:250}]);assert.equal(result.label,'Rejected atom edit: invalid valence');
});
test('nonvalence, malformed and foreign diagnostics do not generate feedback',()=>{
 for(const diagnostic of [undefined,{code:'kekulize',atomIds:['center']},{code:'atom-valence',atomIds:[0]},{code:'atom-valence',atomIds:['unknown']},{code:'atom-valence',atomIds:[]}])assert.equal(valenceFeedback(diagnostic,before,before,t),null);
});
test('no coordinate transform falls back to accessible atom explanation',()=>{
 const after=editBond(before,{bondId:'old',kind:'3'}).graph;
 assert.deepEqual(valenceFeedback(diagnostic,before,after,null).lines,[]);
});
test('overlay uses DOM attributes and only exact proposed bond IDs',()=>{
 const host={children:[],setAttribute(){},replaceChildren(){this.children=[]},append(n){this.children.push(n)}};
 const doc={createElementNS(){return {attrs:{},setAttribute(k,v){this.attrs[k]=v}}}};
 const feedback=valenceFeedback(diagnostic,before,editBond(before,{bondId:'old',kind:'3'}).graph,t);
 paintValenceFeedback(host,feedback,doc);assert.equal(host.children.length,1);
 assert.equal(host.children[0].attrs['data-proposed-bond'],'old');assert.equal(host.children[0].attrs.stroke,'#dc2626');assert.equal(host.children[0].attrs['stroke-dasharray'],'7 4');
 paintValenceFeedback(host,{lines:[]},doc);assert.equal(host.children.length,0);
});
