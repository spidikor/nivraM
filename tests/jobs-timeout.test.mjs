import test from 'node:test';
import assert from 'node:assert/strict';
import {createJobController} from '../public/modules/jobs.mjs';
test('timeouts have an explicit reason even if the operation does not cooperate',async()=>{
  const jobs=createJobController(()=>1);
  const job=jobs.begin('name',{input:{},timeout:20});
  await assert.rejects(job.wait(new Promise(()=>{})),error=>error.name==='TimeoutError');
  assert.equal(job.signal.reason.name,'TimeoutError');
  const retry=jobs.begin('name',{input:{}});assert.equal(await retry.wait(Promise.resolve('usable')),'usable');retry.finish();
});
test('manual and edit cancellation remain distinct from timeout',async()=>{
  const jobs=createJobController(()=>1),job=jobs.begin('name',{input:{}});
  const result=job.wait(new Promise(()=>{}));jobs.cancelAll('The drawing changed.');
  await assert.rejects(result,error=>error.name==='AbortError');
});
