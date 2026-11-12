import {propertyEnvelope} from './property-response-fixtures.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {requestAnalysis} from '../public/modules/analysis.mjs';
import {createJobController} from '../public/modules/jobs.mjs';

const task=()=>createJobController(()=>7).begin('naming',{input:{graph:{atoms:[],bonds:[]}}});
const response=body=>({ok:true,json:async()=>body});

test('naming requests use unique bounded IDs and validate response identity',async()=>{
  const identities=[];
  const fetcher=async(url,options)=>{
    const data=JSON.parse(options.body);identities.push(data);
    return response({...data,inputIdentity:{documentRevision:7},name:'synthetic'});
  };
  for(let i=0;i<2;i++){const job=task();await requestAnalysis('name',job,{token:'session',fetcher});job.finish();}
  assert.match(identities[0].clientId,/^[0-9a-f]{32}$/);assert.match(identities[0].jobId,/^[0-9a-f]{32}$/);
  assert.equal(identities[0].clientId,identities[1].clientId);assert.notEqual(identities[0].jobId,identities[1].jobId);
  assert.ok(identities[0].expiresAt>Date.now());assert.ok(identities[0].expiresAt<Date.now()+60000);
});

test('abort sends a fresh authenticated keepalive cancellation matching the job',async()=>{
  const job=task(),calls=[];
  const fetcher=(url,options)=>{
    calls.push({url,options});
    if(url==='/api/cancel-job')return Promise.resolve(response({status:'cancellation_requested'}));
    return new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(options.signal.reason),{once:true}));
  };
  const pending=requestAnalysis('name',job,{token:'session-secret',fetcher});
  job.cancel();await assert.rejects(pending,/cancelled/i);await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls.length,2);
  const [calculation,cancel]=calls;
  assert.equal(cancel.url,'/api/cancel-job');assert.equal(cancel.options.keepalive,true);
  assert.notEqual(cancel.options.signal,calculation.options.signal);assert.equal(cancel.options.signal.aborted,false);
  assert.equal(cancel.options.headers['X-Nivram-Token'],'session-secret');
  const original=JSON.parse(calculation.options.body),identity=JSON.parse(cancel.options.body);
  assert.deepEqual(identity,{clientId:original.clientId,jobId:original.jobId,expiresAt:original.expiresAt});
  assert.equal('graph' in identity,false);
});

test('a response for another job or document cannot be adopted',async()=>{
  for(const difference of ['jobId','documentRevision']){
    const job=task();
    const fetcher=async(url,options)=>{
      const data=JSON.parse(options.body);
      return response({...data,jobId:difference==='jobId'?'f'.repeat(32):data.jobId,inputIdentity:{documentRevision:difference==='documentRevision'?6:7}});
    };
    await assert.rejects(requestAnalysis('name',job,{token:'session',fetcher}),/does not match/);job.finish();
  }
});

test('late success after cancellation remains rejected even if fetch ignores abort',async()=>{
  const job=task();let resolve,body;
  const fetcher=(url,options)=>{
    if(url==='/api/cancel-job')return Promise.resolve(response({status:'finished'}));
    body=JSON.parse(options.body);return new Promise(done=>{resolve=done;});
  };
  const pending=requestAnalysis('name',job,{token:'session',fetcher});job.cancel();
  resolve(response({...body,inputIdentity:{documentRevision:7}}));await assert.rejects(pending,/cancelled/i);
});

test('property requests retain compatibility without creating naming job IDs',async()=>{
  const job=task();let body;
  await requestAnalysis('properties',job,{token:'session',fetcher:async(url,options)=>{
    body=JSON.parse(options.body);return response(propertyEnvelope(body,'empty'));
  }});job.finish();assert.equal('clientId' in body,false);assert.equal('jobId' in body,false);
});
