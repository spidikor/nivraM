import {readPopulationJsonAsync} from './population-model-json.mjs';
import {createJobController} from './jobs.mjs';
import {validateSuppliedPopulationModel, validatePopulationEnvelope} from './population-view.mjs';

export const MAX_MODEL_BYTES = 2 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
const encoder = new TextEncoder();
const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(',')}]`
  : value && typeof value === 'object' ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
    : JSON.stringify(value);
const digest = async text => [...new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256', encoder.encode(text)))].map(byte => byte.toString(16).padStart(2, '0')).join('');

async function readResponse(response, options) {
  const declared = response.headers.get('content-length');
  if (declared !== null && Number(declared) > MAX_RESPONSE_BYTES) {
    await response.body?.cancel();
    throw new Error('The population response exceeds its size limit.');
  }
  if (!response.body) throw new Error('The population response is empty.');
  const reader = response.body.getReader(), chunks = [];
  let size = 0;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_RESPONSE_BYTES) throw new Error('The population response exceeds its size limit.');
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {bytes.set(chunk, offset); offset += chunk.length;}
    return await readPopulationJsonAsync(new TextDecoder('utf-8', {fatal:true}).decode(bytes), ['envelope','model'], true, options);
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally {reader.releaseLock();}
}

/** Session-only calculation; the drawing digest binds freshness, not chemical correspondence. */
export function createPopulationAnalysis({editor, fetcher=globalThis.fetch, token='', timeoutMs=60000,
  newId=()=>globalThis.crypto.randomUUID(), onInvalidate=()=>{}}) {
  const jobs = createJobController(() => editor.getSnapshot().revision, {timeoutMs});
  let stopped = false, active = null, result = null, status = 'idle', revision = editor.getSnapshot().revision;
  function cancel(message='Population calculation cancelled.') {
    jobs.cancelAll(message); active = null; result = null; status = 'idle';
  }
  const unsubscribe = editor.subscribe(snapshot => {
    if (snapshot.revision === revision) return;
    revision = snapshot.revision;
    cancel('The drawing changed. Reopen the supplied model to calculate again.');
    onInvalidate('The drawing changed. Reopen the supplied model to calculate again.');
  });
  return Object.freeze({
    get status() {return status;}, get result() {return result;},
    cancel,
    dispose() {if (!stopped) {stopped = true; cancel(); unsubscribe();}},
    async loadText(modelText, {name='Supplied model'}={}) {
      if (stopped) throw new Error('Population analysis is closed.');
      cancel();
      if (typeof modelText !== 'string' || encoder.encode(modelText).byteLength > MAX_MODEL_BYTES)
        throw new Error('Choose a model JSON file no larger than 2 MiB.');
      const task = jobs.begin('population', {input:editor.getSnapshot().document});
      active = task; status = 'calculating';
      const deadline = performance.now()+timeoutMs;
      const current = () => {
        if (performance.now()>=deadline) task.cancel('The operation timed out. The preceding document is retained.','TimeoutError');
        task.assertCurrent();
      };
      // Let queued user cancellation and timeout events run before publishing a
      // parsed/validated result. Existing envelope validation remains synchronous.
      const checkpoint = async () => {await task.wait(new Promise(resolve=>setTimeout(resolve,0)));current();};
      const requestId = newId();
      let cancellationSent = false, requestSent = false;
      const abandon = () => {
        if (cancellationSent || !requestSent) return;
        cancellationSent = true;
        const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 3000);
        Promise.resolve().then(() => fetcher('/api/populations/cancel', {method:'POST', keepalive:true, signal:controller.signal,
          headers:{'Content-Type':'application/json', 'X-Nivram-Token':token}, body:JSON.stringify({schemaVersion:1, requestId})}))
          .catch(() => {}).finally(() => clearTimeout(timer));
      };
      task.signal.addEventListener('abort', abandon, {once:true});
      try {
        const parsedModel=await task.wait(readPopulationJsonAsync(modelText,[],false,{signal:task.signal,deadline}));
        current();
        const model=validateSuppliedPopulationModel(parsedModel.value);
        current();
        const [modelInputHash, graphHash] = await task.wait(Promise.all([digest(modelText), digest(JSON.stringify(task.input.graph))]));
        current();
        const body = JSON.stringify({schemaVersion:1, requestId, documentRevision:task.revision, graphHash, modelText, modelInputHash});
        if (encoder.encode(body).byteLength > 8 * 1024 * 1024) throw new Error('The encoded model request exceeds its size limit.');
        requestSent = true;
        const response = await task.wait(fetcher('/api/populations/evaluate', {method:'POST', signal:task.signal,
          headers:{'Content-Type':'application/json', 'X-Nivram-Token':token}, body}));
        const decoded = await task.wait(readResponse(response, {signal:task.signal,deadline})), payload = decoded.value;
        await checkpoint();
        if (!response.ok) throw new Error(typeof payload?.error === 'string' ? payload.error : 'The local population calculation failed.');
        if (!payload || typeof payload !== 'object' || Array.isArray(payload)
          || Object.keys(payload).sort().join(',') !== 'envelope,modelInputHash,requestId,schemaVersion'
          || payload.schemaVersion !== 1 || payload.requestId !== requestId || payload.modelInputHash !== modelInputHash)
          throw new Error('The population response does not match this request.');
        const envelope = validatePopulationEnvelope(payload.envelope, {documentRevision:task.revision, graphHash, modelSignature:payload.envelope?.modelSignature}, {modelText, modelInputHash, responseModelText:decoded.model?.text, responseConditions:decoded.conditions});
        if (canonical(envelope.model) !== canonical(model)) throw new Error('The result changed the supplied model.');
        // This file route sends only the model, not an omitted-mass input.
        // The generic envelope validator also serves explicitly supplied mass
        // callers, so enforce this route's narrower input contract here.
        for (const row of envelope.samples) {
          // This evaluator computes every in-domain sample or fails the whole
          // request. Generic adapters can still represent unavailable samples.
          if (row.status === 'unavailable')
            throw new Error('The model-only result marked an in-domain sample unavailable.');
          if (row.status !== 'computed') continue;
          const expected = model.ensemble_complete ? 0 : null;
          if (row.result.omitted_fraction !== expected || row.result.omitted_mass_source !== null)
            throw new Error('The result claimed omitted mass or a source not supplied by this model-file request.');
        }
        await checkpoint();
        result = Object.freeze({name:String(name).slice(0, 256), modelText, modelInputHash, envelope});
        status = 'ready';
        return result;
      } catch (error) {
        abandon();
        if (active === task) {status = 'idle'; result = null;}
        throw error;
      } finally {
        task.signal.removeEventListener('abort', abandon);
        task.finish();
        if (active === task) active = null;
      }
    }
  });
}
