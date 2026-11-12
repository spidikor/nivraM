/** Production entrypoint and real job/store behavior with an exporter seam. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createDocumentStore} from '../public/modules/document.mjs';
import {createJobController} from '../public/modules/jobs.mjs';
import {validateOptions} from '../public/modules/export.mjs';
const app=readFileSync(new URL('../public/app.mjs',import.meta.url),'utf8');
const entry=app.slice(app.indexOf('async function svgExport(){'),app.indexOf('\nasync function mol(){'));
function harness(mode='ok'){
  const store=createDocumentStore({schema:'nivram/1',graph:{atoms:[{id:'a',symbol:'C',x:0,y:0}],bonds:[]}}),jobs=createJobController(()=>store.getSnapshot().revision),saved=store.getSnapshot();
  const exports=[],downloads=[],notices=[],node={textContent:''};
  const context={jobs,validateOptions,snapshot:()=>store.getSnapshot().document,exportOptions:()=>({...saved.document.export,...(mode==='invalid'?{width:0}:{})}),name:()=> 'chosen-name',notify:(...v)=>notices.push(v),$:()=>node,
    exportSvg:(source,options,extra)=>{exports.push({source,options,extra});if(mode==='cancel')jobs.cancelAll();if(mode==='stale')store.markUnsaved();if(mode==='failed')throw new Error('export failed');return {blob:new Blob(['test'],{type:'image/svg+xml'}),width:1000,height:500,transparentBackground:false};},
    downloadBlob:(...v)=>{if(mode==='download-failed')throw new Error('download failed');downloads.push(v);}};
  const instance=new Function(...Object.keys(context),`let busy=${mode==='busy'},activeJob=null,scene=${mode==='empty'?'null':JSON.stringify({svg:'engine svg'})},graph=snapshot().graph;const setBusy=v=>busy=v;${entry};return {run:svgExport,busy:()=>busy};`)(...Object.values(context));
  return {instance,store,saved,jobs,exports,downloads,notices,node};
}
test('SVG entry exports frozen style/source and selected filename without document or history changes',async()=>{
  const f=harness();await f.instance.run();assert.equal(f.exports.length,1);assert.equal(f.exports[0].source,'engine svg');assert.deepEqual(f.exports[0].extra.paintStyle,f.saved.document.style);assert.equal(f.downloads.length,1);assert.equal(f.downloads[0][1],'chosen-name.svg');assert.deepEqual(f.store.getSnapshot(),f.saved);assert.equal(f.store.historyState().canUndo,false);assert.equal(f.instance.busy(),false);assert.match(f.node.textContent,/Complete the save\/download dialog/);
});
test('busy/close-review blocks entry; empty/invalid inputs never invoke export or download',async()=>{
  for(const mode of ['busy','empty','invalid']){const f=harness(mode);await f.instance.run();assert.equal(f.exports.length,0);assert.equal(f.downloads.length,0);assert.deepEqual(f.store.getSnapshot(),f.saved);assert.equal(f.instance.busy(),mode==='busy');}
});
test('cancelled/stale/failed exports cannot dispatch and failure clears owned busy state',async()=>{
  for(const mode of ['cancel','stale','failed','download-failed']){const f=harness(mode);await f.instance.run();assert.equal(f.downloads.length,0);assert.equal(f.instance.busy(),false);assert.match(f.node.textContent,/No SVG created/);assert.deepEqual(f.store.getSnapshot().document,f.saved.document);assert.equal(f.store.historyState().canUndo,false);}
});
test('SVG button routes to the production entry with accessible help',()=>{
  const html=readFileSync(new URL('../public/index.html',import.meta.url),'utf8');assert.match(app,/\$\('export-svg-button'\)\.onclick=svgExport/);assert.match(html,/id="export-svg-button"[^>]+aria-describedby="svg-export-help"/);assert.match(html,/id="svg-export-help"/);
});
