/** Production controllers with a controlled DOM/Chart adapter, not browser or raster acceptance. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
const root=process.env.NIVRAM_CONTEXT_SOURCE;
const moduleUrl=name=>root?pathToFileURL(`${root}/public/modules/${name}.mjs`):new URL(`../public/modules/${name}.mjs`,import.meta.url);
const {mountPhysiologyExplorer}=await import(moduleUrl('physiology'));
const {mountPopulationDialog}=await import(moduleUrl('population-dialog'));
const {mountPopulationChart}=await import(moduleUrl('supplied-population-chart'));
const {createDocumentStore}=await import(moduleUrl('document'));
const {validatePopulationEnvelope,createPhSelection,serializePopulationJson}=await import(moduleUrl('population-view'));
const physiologyBytes=await readFile(new URL('../nivram/data/physiology.json',import.meta.url));
const dataset=JSON.parse(physiologyBytes),fixtures=JSON.parse(await readFile(new URL('./fixtures/population-analytical.json',import.meta.url),'utf8'));
const html=await readFile(root?`${root}/public/index.html`:new URL('../public/index.html',import.meta.url),'utf8');
const modelText=JSON.stringify(fixtures.complete.envelope.model),hash=value=>createHash('sha256').update(value).digest('hex');
const turn=()=>new Promise(resolve=>setImmediate(resolve));
async function until(check){const deadline=Date.now()+5000;while(Date.now()<deadline){if(check())return;await new Promise(resolve=>setTimeout(resolve,1));}assert.fail('Controlled operation did not settle within five seconds.');}
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return{promise,resolve};};
const walk=node=>[node,...node.children.flatMap(walk)];
const dataNode=(host,key,value)=>walk(host).find(node=>Object.hasOwn(node.dataset,key)&&(value===undefined||node.dataset[key]===value));
class Element extends EventTarget{
  constructor(tag,dom){super();this.localName=tag;this.ownerDocument=dom;this.children=[];this.parentNode=null;this.attrs={};this.dataset={};this.style={};this.value='';this.disabled=false;this.hidden=false;this.checked=false;this.files=[];this._text='';}
  get textContent(){return this._text+this.children.map(node=>node.textContent).join('');}
  set textContent(value){this._text=String(value);this.replaceChildren();}
  append(...nodes){for(const node of nodes){node.remove();node.parentNode=this;this.children.push(node);}}
  after(node){const parent=this.parentNode;node.remove();node.parentNode=parent;parent.children.splice(parent.children.indexOf(this)+1,0,node);}
  replaceChildren(...nodes){for(const node of this.children)node.parentNode=null;this.children=[];this.append(...nodes);}
  remove(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(node=>node!==this);this.parentNode=null;}}
  setAttribute(key,value){this.attrs[key]=String(value);}
  removeAttribute(key){delete this.attrs[key];}
  focus(){this.ownerDocument.activeElement=this;}
  click(){if(!this.disabled)this.emit('click');}
  emit(type){this.dispatchEvent(new Event(type,{cancelable:true}));}
  showModal(){this.open=true;}
  close(){this.open=false;this.emit('close');}
  toBlob(callback){this.ownerDocument.encode(this,callback);}
}
function controlledDom(){
  const dom={createElement:tag=>new Element(tag,dom),getElementById:id=>walk(dom.body).find(node=>node.id===id)};
  dom.body=dom.createElement('body');dom.documentElement=dom.createElement('html');dom.documentElement.dataset.theme='light';
  const view=new EventTarget();view.MutationObserver=class{observe(){}disconnect(){}};dom.defaultView=view;dom.charts=[];
  view.Chart=class{
    static version='4.5.1';
    constructor(canvas,config){this.canvas=canvas;this.config=config;this.options=config.options;this.data=config.data;this.width=1000;this.height=720;this.chartArea={left:60,right:980,top:400,bottom:580};this.scales={x:{getPixelForValue:value=>60+value*920/14,getValueForPixel:value=>(value-60)*14/920}};this.texts=[];
      this.ctx=new Proxy({fillText:text=>this.texts.push(String(text)),measureText:text=>({width:String(text).length*5})},{get:(target,key)=>key in target?target[key]:()=>{}});dom.charts.push(this);}
    update(){this.draw();}
    draw(){this.texts=[];for(const plugin of this.config.plugins){plugin.beforeDraw?.(this);plugin.afterDraw?.(this);}}
    setDatasetVisibility(index,visible){this.data.datasets[index].hidden=!visible;}
    destroy(){this.destroyed=true;}
  };
  for(const match of html.matchAll(/<([a-z][a-z0-9-]*)\b([^>]*\bid="([^"]+)"[^>]*)>/gi)){
    const node=dom.createElement(match[1]);node.id=match[3];node.disabled=/\sdisabled(?:\s|$)/.test(match[2]);dom.body.append(node);
  }
  const initial=dom.createElement('option');initial.value='';dom.getElementById('physiology-context').append(initial);
  return dom;
}
function explorerFixture(dom=controlledDom()){
  const select=dom.getElementById('physiology-context'),detail=dom.getElementById('physiology-detail');
  const explorer=mountPhysiologyExplorer({dataset,select,detail});
  const compare=(lower,upper,label='Test comparison')=>{
    dom.getElementById('physiology-custom-lower').value=lower;dom.getElementById('physiology-custom-upper').value=upper;dom.getElementById('physiology-custom-label').value=label;
    walk(dom.body).find(node=>node.localName==='form').emit('submit');
  };
  return{dom,explorer,select,compare};
}
function dialogFixture({references=true,initialCloseReview=false}={}){
  const dom=controlledDom(),n=id=>dom.getElementById(id),store=createDocumentStore({schema:'nivram/1',graph:{atoms:[{id:'a0',symbol:'C',x:0,y:0},{id:'a1',symbol:'O',x:1.5,y:0}],bonds:[{id:'b0',begin:'a0',end:'a1',order:1}]}});
  const before=store.getSnapshot(),history=store.historyState(),calls=[],saves=[],fault={};
  const fetcher=async(url,options)=>{
    const request=JSON.parse(options.body);calls.push({url,options,request});
    if(url.endsWith('/cancel')||url.endsWith('/cancel-state'))return Response.json({});
    if(url.endsWith('/prepare-state'))return new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('Cancelled','AbortError')),{once:true}));
    assert.equal(url,'/api/populations/evaluate');
    if(fault.evaluate)await fault.evaluate.promise;
    const envelope=structuredClone(fixtures.complete.envelope);envelope.documentRevision=request.documentRevision;envelope.graphHash=request.graphHash;
    return Response.json({schemaVersion:1,requestId:request.requestId,modelInputHash:request.modelInputHash,envelope});
  };
  dom.defaultView.pywebview={api:{async save_population_export(kind,payload,token){assert.equal(token,'context-test');saves.push({kind,payload});if(fault.save)await fault.save.promise;return{status:'saved',bytes:Buffer.byteLength(payload),sha256:hash(payload)};}}};
  const dialog=mountPopulationDialog({editor:store,token:'context-test',dom,fetcher,initialCloseReview});
  let refs;
  const attach=()=>{refs??=explorerFixture(dom);dialog.setReferences({dataset,explorer:refs.explorer});return refs;};
  if(references)attach();
  const open=()=>n('open-population-model').click();
  const load=async()=>{open();n('population-model-file').files=[{name:'synthetic.json',size:Buffer.byteLength(modelText),arrayBuffer:async()=>new TextEncoder().encode(modelText).buffer}];n('population-model-file').emit('change');await until(()=>n('population-model-status').textContent.startsWith('Supplied-model calculation ready.'));};
  const choose=id=>{n('population-context').value=id??'';n('population-context').emit('change');};
  const host=n('population-model-chart'),chart=()=>dom.charts.findLast(chart=>!chart.destroyed),evaluations=()=>calls.filter(call=>call.url.endsWith('/evaluate'));
  const customize=()=>{const ph=dataNode(host,'populationPh');ph.value='8.0';dataNode(host,'populationForm').emit('submit');const toggle=dataNode(host,'populationVisible','HA');toggle.checked=false;toggle.emit('change');const inspect=dataNode(host,'populationInspect','A');inspect.checked=true;inspect.emit('change');};
  const retained=()=>({chart:chart(),data:structuredClone(chart().data),ph:dataNode(host,'populationPh').value,selected:dataNode(host,'populationInspect','A').checked,summary:n('population-state-summary').textContent,percentages:walk(host).filter(node=>Object.hasOwn(node.dataset,'populationPercentage')).map(node=>node.textContent),file:n('population-model-name').textContent});
  const exportFile=async kind=>{const count=saves.length;n(`population-export-${kind}`).click();await until(()=>saves.length>count);await until(()=>!n(`population-export-${kind}`).disabled);return saves.at(-1).payload;};
  const destroy=()=>{dialog.destroy();refs?.explorer.destroy();};
  return{dom,n,store,before,history,calls,saves,fault,dialog,attach,open,load,choose,host,chart,evaluations,customize,retained,exportFile,destroy,get refs(){return refs;}};
}

test('shared explorer setter validates first, preserves per-context accepted comparisons and cleans up',()=>{
  const f=explorerFixture(),original=JSON.stringify(dataset),changes=[];const stop=f.explorer.subscribe(value=>changes.push(value));
  try{
    f.explorer.selectContext('bowmans-space-gap');f.compare('7.35','7.45');const accepted=f.explorer.getSnapshot();
    f.dom.getElementById('physiology-custom-lower').value='8';
    for(const invalid of ['missing',undefined,{},false])assert.throws(()=>f.explorer.selectContext(invalid),/available reference context/);
    assert.equal(f.explorer.getSnapshot(),accepted);assert.equal(f.dom.getElementById('physiology-custom-lower').value,'8');
    f.explorer.selectContext('arterial-blood-uvm');f.compare('7.2','7.2','A marker');
    f.explorer.selectContext('bowmans-space-gap');assert.equal(f.explorer.getSnapshot().comparison,accepted.comparison);assert.equal(f.dom.getElementById('physiology-custom-lower').value,'7.35');
    const count=changes.length;f.explorer.selectContext('bowmans-space-gap');assert.equal(changes.length,count);
    f.explorer.selectContext(null);assert.equal(f.select.value,'');assert.equal(f.explorer.getSnapshot().context,null);
    f.explorer.selectContext('arterial-blood-uvm');assert.equal(f.explorer.getSnapshot().comparison.display_kind,'custom_marker');
    assert.equal(JSON.stringify(dataset),original);stop();f.explorer.destroy();assert.throws(()=>f.explorer.selectContext(null),/destroyed/);
  }finally{stop();f.explorer.destroy();}
});

test('modal changes reuse the exact chart/model session, custom comparisons and export current qualified references',async()=>{
  const f=dialogFixture();try{
    f.refs.explorer.selectContext('bowmans-space-gap');f.refs.compare('7.35','7.45');const custom=f.refs.explorer.getSnapshot().comparison;
    await f.load();f.customize();const before=f.retained(),first=JSON.parse(await f.exportFile('json'));
    assert.equal(f.n('population-context').children.length,20);assert.equal(f.n('population-context').disabled,false);
    for(const id of ['arterial-blood-uvm','rat-late-proximal-dubose-1983','bowmans-space-gap',null]){
      f.choose(id);assert.equal(f.n('population-dialog').open,true);assert.deepEqual(f.retained(),before);assert.equal(f.evaluations().length,1);
      const result=JSON.parse(await f.exportFile('json'));assert.deepEqual(result.populationResults,first.populationResults);assert.deepEqual(result.visibleStateIds,first.visibleStateIds);assert.deepEqual(result.selectedSample,first.selectedSample);assert.equal(result.selectedSample.index,80);assert.equal(result.referenceContext.context?.id??null,id);
      if(id==='arterial-blood-uvm')assert.deepEqual([result.referenceContext.overlays[0].lower,result.referenceContext.overlays[0].upper],[7.35,7.45]);
      if(id==='rat-late-proximal-dubose-1983'){assert.equal(result.referenceContext.overlays[0].kind,'study_marker');assert.equal(result.referenceContext.overlays[0].lower,null);assert.match(f.host.textContent,/not plotted or reinterpreted/);}
      if(id==='bowmans-space-gap'){assert.ok(result.referenceContext.gap);assert.deepEqual(result.referenceContext.comparison,custom);assert.equal(result.referenceContext.overlays.length,1);assert.equal(result.referenceContext.overlays[0].kind,'custom_band');}
      const csv=await f.exportFile('csv');assert.ok(csv.includes('referenceContext'));if(id)assert.ok(csv.includes(id));
    }
    assert.equal(f.refs.select.value,'');assert.deepEqual(f.store.getSnapshot(),f.before);assert.deepEqual(f.store.historyState(),f.history);
    assert.equal(hash(await readFile(new URL('../nivram/data/physiology.json',import.meta.url))),hash(physiologyBytes));
  }finally{f.destroy();}
});

test('late reference loading updates the same chart without clearing pH, visibility or selected state',async()=>{
  const f=dialogFixture({references:false});try{
    await f.load();f.customize();const before=f.retained();assert.equal(f.n('population-context').disabled,true);assert.match(f.host.textContent,/reference data is unavailable/i);
    f.attach();assert.deepEqual(f.retained(),before);f.choose('arterial-blood-uvm');assert.deepEqual(f.retained(),before);assert.equal(f.evaluations().length,1);
    assert.equal(f.n('population-context').disabled,false);assert.match(f.host.textContent,/7\.35–7\.45/);
    f.dialog.setReferences({dataset,explorer:f.refs.explorer});assert.equal(f.n('population-context').children.length,20);assert.deepEqual(f.retained(),before);
  }finally{f.destroy();}
});

test('an in-flight export keeps its captured reference; local changes are blocked and later exports use the new shared context',async()=>{
  const f=dialogFixture();try{
    await f.load();f.customize();f.choose('arterial-blood-uvm');const before=f.retained();f.fault.save=deferred();
    f.n('population-export-json').click();assert.equal(f.n('population-context').disabled,true);
    f.choose('bowmans-space-gap');assert.equal(f.n('population-context').value,'arterial-blood-uvm');
    // Another accepted shared-context update can arrive programmatically while saving.
    f.refs.explorer.selectContext('rat-late-proximal-dubose-1983');await until(()=>f.saves.length===1);
    assert.equal(JSON.parse(f.saves[0].payload).referenceContext.context.id,'arterial-blood-uvm');assert.deepEqual(f.retained(),before);
    f.fault.save.resolve();f.fault.save=null;await until(()=>!f.n('population-export-json').disabled);
    assert.equal(f.n('population-context').disabled,false);assert.equal(JSON.parse(await f.exportFile('json')).referenceContext.context.id,'rat-late-proximal-dubose-1983');
  }finally{f.fault.save?.resolve();f.destroy();}
});

test('context switching retains an owned pending state preview, while close still cancels it',async()=>{
  const f=dialogFixture();try{
    await f.load();f.customize();const sidecar={schemaVersion:1,fileKind:'nivram-population-state-structures',modelInputHash:hash(modelText),structures:fixtures.complete.envelope.model.states.map(state=>({stateId:state.state_id,structureId:state.structure_id,format:'smiles',source:state.state_id==='HA'?'[CH3:1][OH:2]':'[CH3:1][O-:2]',coreAtomMaps:[{atomId:'a0',mapNum:1},{atomId:'a1',mapNum:2}]}))};
    const text=JSON.stringify(sidecar);f.n('population-structures-file').files=[{name:'structures.json',size:Buffer.byteLength(text),arrayBuffer:async()=>new TextEncoder().encode(text).buffer}];f.n('population-structures-file').emit('change');
    await until(()=>f.calls.some(call=>call.url.endsWith('/prepare-state')));const request=f.calls.find(call=>call.url.endsWith('/prepare-state')),before=f.retained(),status=f.n('population-state-status').textContent;
    f.choose('arterial-blood-uvm');assert.deepEqual(f.retained(),before);assert.equal(request.options.signal.aborted,false);assert.equal(f.n('population-state-status').textContent,status);assert.equal(f.calls.filter(call=>call.url.endsWith('/cancel-state')).length,0);
    f.n('close-population-model').click();assert.equal(request.options.signal.aborted,true);await until(()=>f.calls.some(call=>call.url.endsWith('/cancel-state')));assert.equal(f.host.children.length,0);assert.deepEqual(f.store.getSnapshot(),f.before);
  }finally{f.destroy();}
});

test('close review, drawing edits and disposal still revoke the session; delayed references cannot unlock controls',async()=>{
  const f=dialogFixture({references:false,initialCloseReview:true});try{
    f.attach();assert.equal(f.n('population-context').disabled,true);f.open();assert.notEqual(f.n('population-dialog').open,true);
    f.dom.defaultView.dispatchEvent(new Event('nivram-close-review-ended'));await f.load();f.choose('arterial-blood-uvm');
    const updated=structuredClone(f.before.document);updated.graph.atoms[0].x=5;f.store.commit(updated);assert.equal(f.host.children.length,0);
    await f.load();f.dom.defaultView.dispatchEvent(new Event('nivram-close-review'));assert.equal(f.n('population-dialog').open,false);assert.equal(f.n('population-context').disabled,true);assert.equal(f.host.children.length,0);
    f.dialog.destroy();const count=f.calls.length;f.dom.defaultView.dispatchEvent(new Event('nivram-close-review-ended'));f.choose('rat-late-proximal-dubose-1983');f.dialog.setReferences({dataset,explorer:f.refs.explorer});assert.equal(f.n('population-context').disabled,true);assert.equal(f.calls.length,count);
  }finally{f.destroy();}
});

test('chart dataset replacement validates before commit and keeps model, selection, visibility and state identity',()=>{
  const dom=controlledDom(),host=dom.createElement('div'),envelope=validatePopulationEnvelope(fixtures.complete.envelope,fixtures.complete.binding),selection=createPhSelection();
  const chart=mountPopulationChart(host,{Chart:dom.defaultView.Chart,envelope,selection,dataset:null});
  try{
    selection.enterPh('8');chart.selectState('A');const toggle=dataNode(host,'populationVisible','HA');toggle.checked=false;toggle.emit('change');const before=chart.getSnapshot();
    assert.throws(()=>chart.setReferenceDataset({...dataset,schema_version:2}),/Unsupported reference dataset/);assert.equal(chart.getSnapshot().reference,before.reference);
    chart.setReferenceDataset(dataset,{contextId:'arterial-blood-uvm'});const after=chart.getSnapshot();for(const key of ['envelope','selection','table','selectedStateId'])assert.equal(after[key],before[key],key);assert.deepEqual(after.visibleStateIds,before.visibleStateIds);
    chart.setReferenceDataset(null);assert.equal(chart.getSnapshot().envelope,envelope);assert.equal(chart.getSnapshot().reference.context,null);
    assert.throws(()=>chart.updateContext({contextId:'arterial-blood-uvm'}),/unavailable/);
  }finally{chart.destroy();}
});

test('PNG export retains captured context during encoding and the next export uses the changed context (adapter, not raster proof)',async()=>{
  const dom=controlledDom(),host=dom.createElement('div'),envelope=validatePopulationEnvelope(fixtures.complete.envelope,fixtures.complete.binding),selection=createPhSelection(),pending=[];
  const tiny=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC','base64');
  dom.encode=(canvas,done)=>pending.push({canvas,done});
  const originalFetch=globalThis.fetch;globalThis.fetch=async url=>{assert.equal(url,'/api/status');return Response.json({version:'context-test'});};
  const chart=mountPopulationChart(host,{Chart:dom.defaultView.Chart,envelope,selection,dataset,contextId:'arterial-blood-uvm'});
  try{
    selection.enterPh('8');chart.selectState('A');const before=chart.getSnapshot(),exporting=chart.exportPng();assert.equal(pending.length,1);
    const exportChart=dom.charts.at(-1);chart.updateContext({contextId:'rat-late-proximal-dubose-1983'});exportChart.draw();assert.ok(exportChart.texts.some(text=>text.startsWith('Reference: '+before.reference.context.label)));assert.ok(exportChart.texts.some(text=>text.includes('7.35–7.45')));
    pending[0].done(new Blob([tiny],{type:'image/png'}));assert.equal((await exporting).type,'image/png');
    const next=chart.exportPng(),nextChart=dom.charts.at(-1);assert.ok(nextChart.texts.some(text=>text.startsWith('Reference: '+chart.getSnapshot().reference.context.label)));assert.ok(nextChart.texts.some(text=>text.includes('6.78')));
    pending[1].done(new Blob([tiny],{type:'image/png'}));await next;assert.equal(chart.getSnapshot().envelope,before.envelope);assert.equal(chart.getSnapshot().selection,before.selection);
    const exported=JSON.parse(serializePopulationJson(envelope,{source:{name:'synthetic.json',modelInputHash:hash(modelText)},index:80,reference:chart.getSnapshot().reference}));assert.equal(exported.referenceContext.context.id,'rat-late-proximal-dubose-1983');
  }finally{chart.destroy();globalThis.fetch=originalFetch;}
});

test('a throwing earlier observer does not prevent the chart subscriber from seeing the accepted selection',async()=>{
  const f=dialogFixture({references:false}),refs=explorerFixture(f.dom);let stop;
  try{
    stop=refs.explorer.subscribe(context=>{if(context.contextId)throw new Error('Injected earlier observer failure');});
    f.dialog.setReferences({dataset,explorer:refs.explorer});await f.load();f.customize();const before=f.retained();
    assert.throws(()=>refs.explorer.selectContext('arterial-blood-uvm'),error=>error instanceof AggregateError&&error.errors[0].message==='Injected earlier observer failure');
    assert.equal(refs.explorer.getSnapshot().contextId,'arterial-blood-uvm');assert.equal(f.n('population-context').value,'arterial-blood-uvm');assert.deepEqual(f.retained(),before);
    assert.equal(JSON.parse(await f.exportFile('json')).referenceContext.context.id,'arterial-blood-uvm');
  }finally{stop?.();f.destroy();refs.explorer.destroy();}
});

test('failing replacement subscriptions retain the old source, options, chart and export authority',async()=>{
  const f=dialogFixture();try{
    await f.load();f.customize();f.choose('arterial-blood-uvm');const before=f.retained(),options=[...f.n('population-context').children],snapshot=f.refs.explorer.getSnapshot();
    const source={getSnapshot:()=>snapshot,subscribe(){throw new Error('Injected subscribe failure');}};
    assert.throws(()=>f.dialog.setReferences({dataset,explorer:source}),/Injected subscribe failure/);
    assert.deepEqual(f.n('population-context').children,options);assert.deepEqual(f.retained(),before);assert.equal(f.n('population-context').value,'arterial-blood-uvm');assert.equal(f.n('population-export-json').disabled,false);
    f.choose('rat-late-proximal-dubose-1983');assert.deepEqual(f.retained(),before);assert.equal(JSON.parse(await f.exportFile('json')).referenceContext.context.id,'rat-late-proximal-dubose-1983');
    assert.throws(()=>f.dialog.setReferences({dataset,explorer:{getSnapshot:()=>snapshot,subscribe(callback){callback({contextId:'missing',comparison:null});return()=>{};}}}),/available reference context/);
    f.choose('bowmans-space-gap');assert.equal(JSON.parse(await f.exportFile('json')).referenceContext.context.id,'bowmans-space-gap');
  }finally{f.destroy();}
});

test('invalid reference replacement is rejected without a chart and before subscribing or enabling controls',()=>{
  const f=dialogFixture({references:false});let subscribed=0;
  try{
    const options=[...f.n('population-context').children];
    assert.throws(()=>f.dialog.setReferences({dataset:{...dataset,schema_version:2},explorer:{getSnapshot:()=>({contextId:null,comparison:null}),subscribe(){subscribed++;return()=>{};}}}),/Unsupported reference dataset/);
    assert.equal(subscribed,0);assert.deepEqual(f.n('population-context').children,options);assert.equal(f.n('population-context').disabled,true);assert.equal(f.n('open-population-model').disabled,false);
  }finally{f.destroy();}
});

test('a chart subscriber failure explicitly blocks new exports until reference synchronization recovers',async()=>{
  const f=dialogFixture();try{
    await f.load();f.customize();f.choose('arterial-blood-uvm');const before=f.retained(),chart=f.chart(),update=chart.update;
    chart.update=()=>{throw new Error('Injected chart update failure');};f.choose('rat-late-proximal-dubose-1983');
    assert.equal(f.refs.explorer.getSnapshot().contextId,'rat-late-proximal-dubose-1983');assert.equal(f.n('population-context').value,'rat-late-proximal-dubose-1983');assert.match(f.n('population-context-status').textContent,/exports are disabled/);assert.deepEqual(f.retained(),before);
    for(const kind of ['json','csv','png']){assert.equal(f.n(`population-export-${kind}`).disabled,true);f.n(`population-export-${kind}`).emit('click');}
    await turn();assert.equal(f.saves.length,0);assert.equal(f.evaluations().length,1);
    chart.update=update;f.choose('rat-late-proximal-dubose-1983');assert.equal(f.n('population-export-json').disabled,false);assert.deepEqual(f.retained(),before);
    assert.equal(JSON.parse(await f.exportFile('json')).referenceContext.context.id,'rat-late-proximal-dubose-1983');assert.deepEqual(f.store.getSnapshot(),f.before);
  }finally{f.destroy();}
});

test('one-shot chart update failure restores prior reference text, padding, snapshot and drawing labels',()=>{
  const dom=controlledDom(),host=dom.createElement('div'),envelope=validatePopulationEnvelope(fixtures.complete.envelope,fixtures.complete.binding);
  const chart=mountPopulationChart(host,{Chart:dom.defaultView.Chart,envelope,dataset,contextId:'arterial-blood-uvm'}),rendered=dom.charts[0];
  try{
    const before=chart.getSnapshot(),text=host.textContent,padding=rendered.options.layout.padding.top,labels=[...rendered.texts],update=rendered.update;let calls=0;
    rendered.update=function(){if(++calls===1)throw new Error('One-shot chart error');return update.call(this);};
    assert.throws(()=>chart.updateContext({contextId:'bowmans-space-gap'}),/One-shot chart error/);
    assert.equal(calls,2);assert.equal(chart.getSnapshot().reference,before.reference);assert.equal(host.textContent,text);assert.equal(rendered.options.layout.padding.top,padding);assert.deepEqual(rendered.texts,labels);
    chart.updateContext({contextId:'bowmans-space-gap'});assert.equal(chart.getSnapshot().reference.context.id,'bowmans-space-gap');assert.match(host.textContent,/No verified numerical interval available/);
  }finally{chart.destroy();}
});
