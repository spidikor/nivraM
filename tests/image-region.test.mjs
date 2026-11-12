import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {validateCrop,validateRegionReceipt,regionImageData,overviewPoint,cropBetween} from '../public/modules/image-region.mjs';

const expected={jobId:'png_job_1',requestId:'png_request_1',sourceSha256:'a'.repeat(64),sourceWidth:11559,sourceHeight:9999,mode:'crop',rect:{x:11549,y:9989,width:10,height:10},selected:['component-1']};
const receipt=()=>({schemaVersion:1,...expected,width:10,height:10,resampling:'none',pngBase64:'YWJjZA==',pngSha256:'b'.repeat(64),navigationOnly:false,reviewToken:'opaque_native_token'});
test('source crops preserve boundary pixels and reject invalid or oversized rectangles',()=>{
  assert.deepEqual(validateCrop(expected.rect,11559,9999),expected.rect);
  for(const bad of [{...expected.rect,x:11550},{...expected.rect,y:9990},{...expected.rect,width:0},{...expected.rect,x:true},{...expected.rect,x:.1},{...expected.rect,width:NaN},{x:0,y:0,width:2049,height:1},{x:0,y:0,width:1001,height:1000}])assert.throws(()=>validateCrop(bad,11559,9999));
});
test('receipt validation refuses wrong source, request, rectangle, scale and authority',()=>{
  assert.equal(validateRegionReceipt(receipt(),expected).reviewToken,'opaque_native_token');
  for(const change of [r=>r.sourceSha256='c'.repeat(64),r=>r.requestId='other',r=>r.rect={...r.rect,x:0},r=>r.width=9,r=>r.navigationOnly=true,r=>r.reviewToken=null,r=>r.pngBase64+='!',r=>r.pngSha256='not-a-hash']){const value=receipt();change(value);assert.throws(()=>validateRegionReceipt(value,expected));}
  const navigation={...expected,mode:'overview',rect:null,selected:[]},value={...receipt(),mode:'overview',rect:{x:0,y:0,width:11559,height:9999},width:960,height:830,resampling:'nearest-center',navigationOnly:true};delete value.reviewToken;
  assert.equal(validateRegionReceipt(value,navigation).navigationOnly,true);value.reviewToken='forbidden_token';assert.throws(()=>validateRegionReceipt(value,navigation));
});
test('PNG header and content hash must agree with the bounded receipt before display',async()=>{
  // Header checks are followed by real browser decoding in the UI; this unit
  // fixture deliberately establishes only header/hash validation boundaries.
  const png=Buffer.alloc(33);Buffer.from([137,80,78,71,13,10,26,10]).copy(png);png.writeUInt32BE(13,8);png.write('IHDR',12);png.writeUInt32BE(10,16);png.writeUInt32BE(10,20);
  const value={...receipt(),pngBase64:png.toString('base64'),pngSha256:createHash('sha256').update(png).digest('hex')};
  assert.equal(await regionImageData(value),'data:image/png;base64,'+value.pngBase64);
  await assert.rejects(regionImageData({...value,pngSha256:'c'.repeat(64)}),/hash/);
  await assert.rejects(regionImageData({...value,width:9}),/dimensions/);
  await assert.rejects(regionImageData({...value,pngBase64:'YWJjZA=='}),/signature/);
});
test('overview coordinates exclude letterboxing and round crop boundaries outwards',()=>{
  const box={left:10,top:20,width:400,height:200};
  assert.equal(overviewPoint(20,100,box,1000,1000,200,200),null);
  assert.deepEqual(overviewPoint(110,20,box,1000,1000,200,200),{x:0,y:0});
  assert.deepEqual(overviewPoint(310,220,box,1000,1000,200,200),{x:1000,y:1000});
  assert.deepEqual(cropBetween({x:10.8,y:20.2},{x:30.1,y:40.9},1000,1000),{x:10,y:20,width:21,height:21});
  assert.deepEqual(cropBetween({x:30.1,y:40.9},{x:10.8,y:20.2},1000,1000),{x:10,y:20,width:21,height:21});
});
