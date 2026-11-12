import test from 'node:test';
import assert from 'node:assert/strict';
import {exportGeometry,preflightExport,estimatePeakBytes,MAX_MEMORY_BYTES} from '../public/modules/export.mjs';

test('a large canvas within the old pixel cap is rejected when concurrent buffers exceed the memory budget',()=>{
  assert.throws(()=>preflightExport({width:100,height:100},{width:9999,marginMode:'percent',margin:2}),/memory.*budget/i);
});
test('the maximum required width remains available for a sufficiently shallow drawing',()=>{
  const geometry=exportGeometry({width:1000,height:50},{width:9999,marginMode:'percent',margin:2});
  assert.equal(geometry.width,9999);assert.equal(geometry.padding,200);assert.equal(geometry.height,880);
});
test('geometry remains an exact mathematical result even when its allocation is rejected',()=>{
  const options={width:9999,marginMode:'percent',margin:2},bounds={width:300,height:150};
  assert.equal(exportGeometry(bounds,options).height,5200);
  assert.throws(()=>preflightExport(bounds,options),/memory.*budget/i);
});
test('the estimate includes retained canvas and serialized vector storage',()=>{
  assert.equal(estimatePeakBytes(100,200,{retainedPixels:300,sourceCharacters:50}),321600);
  assert.equal(MAX_MEMORY_BYTES,536870912);
});
