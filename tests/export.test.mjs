import test from 'node:test';
import assert from 'node:assert/strict';
import {exportGeometry,validateOptions} from '../public/modules/export.mjs';
for (const [width,pad] of [[1000,20],[4000,80],[9999,200]]){
 test(`width ${width} has exactly configured padding`,()=>{
  const g=exportGeometry({width:300,height:150},{width,marginMode:'percent',margin:2});
  assert.equal(g.width,width);assert.equal(g.padding,pad);
  assert.equal(g.height,Math.ceil((width-2*pad)*0.5)+2*pad);
 });
}
test('width includes both margins',()=>assert.equal(exportGeometry({width:300,height:100},{width:1000,margin:2,marginMode:'percent'}).contentWidth,960));
test('pixel override',()=>assert.equal(exportGeometry({width:1,height:1},{width:1000,margin:7,marginMode:'pixels'}).padding,7));
test('percentage rounds upward',()=>assert.equal(exportGeometry({width:1,height:1},{width:333,margin:2,marginMode:'percent'}).padding,7));
test('height can exceed 9999',()=>assert.ok(exportGeometry({width:10,height:120},{width:1000,margin:2,marginMode:'percent'}).height>9999));
for(const width of [0,-1,10000,3.4,NaN,Infinity,'1000'])test(`invalid width ${width}`,()=>assert.throws(()=>validateOptions({width,margin:2,marginMode:'percent'})));
for(const margin of [-1,NaN,Infinity,50])test(`invalid margin ${margin}`,()=>assert.throws(()=>exportGeometry({width:1,height:1},{width:1000,margin,marginMode:'percent'})));
test('empty bounds rejected',()=>assert.throws(()=>exportGeometry({width:0,height:20},{width:1000,margin:2,marginMode:'percent'})));
test('excessive height rejected rather than clipped',()=>assert.throws(()=>exportGeometry({width:1,height:5000},{width:9999,margin:2,marginMode:'percent'})));
