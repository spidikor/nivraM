import test from 'node:test';
import assert from 'node:assert/strict';
import {reflectionSelection} from '../public/modules/canvas-menu.mjs';

const graph={atoms:['a','b','c','d','e'].map(id=>({id})),bonds:[
  {id:'ab',begin:'a',end:'b'},{id:'bc',begin:'b',end:'c'},{id:'de',begin:'d',end:'e'}]};
test('empty-space menu uses explicit selection or the whole drawing',()=>{
  assert.deepEqual(reflectionSelection(graph,{atoms:[],bonds:[]},null),[]);
  assert.deepEqual(reflectionSelection(graph,{atoms:['c'],bonds:['ab']},null),['a','b','c']);
});
test('right-clicking a selected target keeps the complete existing selection',()=>{
  assert.deepEqual(reflectionSelection(graph,{atoms:['a','d'],bonds:[]},{atomId:'a'}),['a','d']);
  assert.deepEqual(reflectionSelection(graph,{atoms:['d'],bonds:['ab']},{bondId:'ab'}),['a','b','d']);
  assert.deepEqual(reflectionSelection(graph,{atoms:['a','b'],bonds:[]},{bondId:'ab'}),['a','b']);
});
test('right-clicking an unselected atom or bond targets its connected molecule only',()=>{
  assert.deepEqual(reflectionSelection(graph,{atoms:['d'],bonds:[]},{atomId:'b'}),['a','b','c']);
  assert.deepEqual(reflectionSelection(graph,{atoms:[],bonds:[]},{bondId:'de'}),['d','e']);
});
test('selection resolution does not mutate the graph or caller selection',()=>{
  const before=JSON.stringify(graph),selection={atoms:['c','a'],bonds:['ab']};
  reflectionSelection(graph,selection,null);
  assert.equal(JSON.stringify(graph),before);assert.deepEqual(selection,{atoms:['c','a'],bonds:['ab']});
});
