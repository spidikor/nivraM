/** Production readers/exporters/controllers with a controlled DOM/Chart double.
 * Canvas call assertions are not browser, raster layout, or Windows validation.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
const root=process.env.NIVRAM_PHYSIOLOGY_SOURCE??new URL('../',import.meta.url).pathname;
const moduleUrl=name=>pathToFileURL(`${root}/public/modules/${name}.mjs`);
const {validateCapabilities,referenceView,serializeReferenceJson,referenceCsv,validatePopulationEnvelope,serializePopulationJson,populationCsv}=await import(moduleUrl('population-view'));
const {describePhysiologyContext,mountPhysiologyExplorer}=await import(moduleUrl('physiology'));
const {mountReferenceChart,drawReferenceOverlay}=await import(moduleUrl('population-chart'));
const {mountPopulationChart}=await import(moduleUrl('supplied-population-chart'));
const dataset=JSON.parse(await readFile(`${root}/nivram/data/physiology.json`,'utf8'));
const fixtures=JSON.parse(await readFile(new URL('./fixtures/population-analytical.json',import.meta.url),'utf8'));
const ids=['neuronal-cytosol-bonnet-2019','rat-late-proximal-dubose-1983','rat-deep-loop-bend-dubose-1983','rat-late-distal-dubose-1983','rat-papillary-base-dubose-1983','rat-papillary-tip-dubose-1983'];
const pairs=[[7.09,.14],[6.78,.03],[7.39,.04],[6.59,.04],[6.24,.07],[6.12,.03]];
const source={name:'synthetic-model.json',modelInputHash:'a'.repeat(64)};
const tiny=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC','base64');
const clone=value=>structuredClone(value),walk=node=>[node,...node.children.flatMap(walk)];
class Element extends EventTarget{
  constructor(tag,dom){super();this.localName=tag;this.ownerDocument=dom;this.children=[];this.parentNode=null;this.dataset={};this.style={};this.attrs={};this._text='';this.value='';}
  get textContent(){return this._text+this.children.map(node=>node.textContent).join('');}
  set textContent(value){this._text=String(value);this.replaceChildren();}
  set innerHTML(_){assert.fail('Source text must not enter HTML.');}
  append(...nodes){for(const node of nodes){node.remove();node.parentNode=this;this.children.push(node);}}
  after(node){this.parentNode.append(node);}
  replaceChildren(...nodes){for(const node of this.children)node.parentNode=null;this.children=[];this.append(...nodes);}
  remove(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(node=>node!==this);this.parentNode=null;}}
  setAttribute(key,value){this.attrs[key]=String(value);}
  focus(){}
  toBlob(done){done(new Blob([tiny],{type:'image/png'}));}
}
function controlledDom(){
  const dom={createElement:tag=>new Element(tag,dom),charts:[]};
  dom.Chart=class{
    static version='4.5.1';
    constructor(canvas,config){this.canvas=canvas;this.config=config;this.options=config.options;this.data=config.data;this.width=1000;this.height=720;this.chartArea={left:60,right:980,top:400,bottom:580};this.scales={x:{getPixelForValue:v=>60+v*920/14,getValueForPixel:v=>(v-60)*14/920}};this.texts=[];
      this.ctx=new Proxy({fillText:text=>this.texts.push(String(text)),measureText:text=>({width:String(text).length*5})},{get:(target,key)=>key in target?target[key]:()=>{}});dom.charts.push(this);}
    draw(){this.texts=[];for(const plugin of this.config.plugins){plugin.beforeDraw?.(this);plugin.afterDraw?.(this);}}
    update(){this.draw();}
    setDatasetVisibility(index,visible){this.data.datasets[index].hidden=!visible;}
    destroy(){this.destroyed=true;}
  };return dom;
}

test('all six reported values carry uncertainty into real reference and population JSON/CSV exports',()=>{
  const envelope=validatePopulationEnvelope(fixtures.complete.envelope,fixtures.complete.binding),payloads=[];
  for(const [index,id] of ids.entries()){
    const ref=referenceView(dataset,id),p=ref.context,overlay=ref.overlays[0];
    assert.equal(p.statistic,'reported_value');assert.equal(overlay.statistic,'reported_value');assert.equal(overlay.kind,'study_marker');
    assert.deepEqual(p.ph,{value:pairs[index][0],lower:null,upper:null,confidence_level:null});
    assert.deepEqual(p.reported_uncertainty,{value:pairs[index][1],kind:'unspecified_in_abstract',plotted:false});
    assert.match(p.evidence_limitations.join(' '),/central-statistic definition/);assert.doesNotMatch(p.label,/mean/i);
    assert.match(describePhysiologyContext(dataset,p).split('\n')[0],/reported value; central statistic unverified/);
    for(const [scope,json,csv,field] of [
      ['reference',serializeReferenceJson(ref),referenceCsv(ref),'reference'],
      ['population',serializePopulationJson(envelope,{source,reference:ref}),populationCsv(envelope,{source,reference:ref}),'referenceContext'],
    ]){
      assert.deepEqual(JSON.parse(json)[field],ref);
      assert.ok(csv.includes(`"/${field}/context/statistic","string","reported_value","false"`));
      assert.ok(csv.includes('central-statistic definition'));
      payloads.push({scope,kind:'json',payload:json},{scope,kind:'csv',payload:csv});
    }
  }
  const code=`import json,sys\nfrom nivram.reference_export import validate_payload as ref, _csv_document\nfrom nivram.population_export import validate_payload as pop\nfor item in json.load(sys.stdin):\n raw=(ref if item['scope']=='reference' else pop)(item['kind'],item['payload'])\n assert raw==item['payload'].encode()\n d=json.loads(raw) if item['kind']=='json' else _csv_document(raw.decode(),max_rows=50000,max_depth=20,max_items=4096)\n r=d['reference' if item['scope']=='reference' else 'referenceContext']\n assert r['context']['statistic']==r['overlays'][0]['statistic']=='reported_value'\n assert 'central-statistic definition' in ' '.join(r['context']['evidence_limitations'])\nprint('24 payloads round-tripped unchanged')`;
  const result=spawnSync(process.env.PYTHON??'python',['-c',code],{input:JSON.stringify(payloads),cwd:root,env:{...process.env,PYTHONPATH:root,PYTHONDONTWRITEBYTECODE:'1'},encoding:'utf8',maxBuffer:4*1024*1024});
  assert.equal(result.status,0,result.stderr);assert.match(result.stdout,/24 payloads/);
});

test('new and legacy point enums accept exact points and reject malformed additions without mutation',()=>{
  for(const statistic of ['reported_value','study_mean','single_condition']){
    const data=clone(dataset);data.presets.find(p=>p.id===ids[0]).statistic=statistic;const before=clone(data);
    assert.equal(referenceView(data,ids[0]).context.statistic,statistic);assert.deepEqual(data,before);
  }
  const edits=[p=>p.statistic='reported_value ',p=>p.statistic='reported_mean',p=>p.statistic='__proto__',p=>p.statistic='<script>x</script>',p=>p.statistic=null,p=>p.statistic=true,p=>p.statistic={},p=>p.display_kind='reference_band',p=>p.ph.value=null,p=>p.ph.value=true,p=>p.ph.value='7.09',p=>p.ph.value=Infinity,p=>p.ph.lower=6.95,p=>p.ph.upper=7.23,p=>p.ph.confidence_level=.95,p=>p.ph.standard_error=.14,p=>p.reported_uncertainty.plotted=true,p=>p.reported_uncertainty.kind='confidence_interval',p=>p.reported_uncertainty.value=-1,p=>p.reported_uncertainty.value=true,p=>p.evidence_status='verified',p=>p.join_trajectory=true,p=>p.ph=null];
  for(const edit of edits){const data=clone(dataset),p=data.presets.find(p=>p.id===ids[0]);p.statistic='reported_value';edit(p);const before=clone(data);assert.throws(()=>referenceView(data,ids[0]));assert.deepEqual(data,before);}
});

test('both charts and explorer expose qualifications, including export-canvas calls, with unchanged point geometry',async()=>{
  const oldFetch=globalThis.fetch;globalThis.fetch=async()=>Response.json({version:'wording-test'});
  try{for(const id of ids){
    const dom=controlledDom(),host=dom.createElement('div'),suppliedHost=dom.createElement('div'),select=dom.createElement('select'),detail=dom.createElement('p');host.append(select,detail);
    const explorer=mountPhysiologyExplorer({dataset,select,detail});explorer.selectContext(id);
    assert.match(detail.textContent,/central statistic unverified/);
    const chart=mountReferenceChart(host,{Chart:dom.Chart,dataset,capabilities:validateCapabilities(fixtures.capabilities),contextId:id});
    const supplied=mountPopulationChart(suppliedHost,{Chart:dom.Chart,dataset,envelope:validatePopulationEnvelope(fixtures.complete.envelope,fixtures.complete.binding),contextId:id});
    for(const node of [host,suppliedHost])assert.match(node.textContent,/Reported value \(central statistic unverified\)/);
    for(const instance of dom.charts)assert.ok(instance.texts.some(text=>text.includes('Reported value (central statistic unverified)')));
    for(const instance of [chart,supplied]){await instance.exportPng();assert.ok(dom.charts.at(-1).texts.some(text=>text.includes('Reported value (central statistic unverified)')));}
    const overlay=referenceView(dataset,id).overlays[0],geometry=statistic=>{const calls=[],ctx=new Proxy({},{get:(_,key)=>key==='fillText'?()=>{}:(...args)=>calls.push([key,...args])});drawReferenceOverlay(ctx,{getPixelForValue:v=>v*10},{left:0,right:140,top:0,bottom:100},{...overlay,statistic},50,{text:'#000'});return calls;};
    assert.deepEqual(geometry('reported_value'),geometry('study_mean'));
    assert.equal(geometry('reported_value').filter(([name])=>name==='arc').length,1);
    chart.destroy();supplied.destroy();explorer.destroy();
  }}finally{globalThis.fetch=oldFetch;}
});

test('gap/interval/default contracts and literal malicious labels remain unchanged',()=>{
  assert.equal(referenceView(dataset).context,null);assert.deepEqual(referenceView(dataset).overlays,[]);
  assert.equal(dataset.presets.length,19);assert.equal(dataset.presets.filter(p=>p.ph===null).length,11);
  for(const p of dataset.presets.filter(p=>p.ph===null)){const ref=referenceView(dataset,p.id);assert.deepEqual(ref.overlays,[]);assert.equal(ref.gap.label,'No verified numerical interval available');}
  assert.deepEqual(referenceView(dataset,'arterial-blood-uvm').context.ph,{value:null,lower:7.35,upper:7.45,confidence_level:null});
  assert.equal(referenceView(dataset,'csf-merril-1961').context.statistic,'mean_confidence_interval');
  const data=clone(dataset);data.presets.find(p=>p.id===ids[0]).label='<img src=x onerror=alert(1)>';
  const dom=controlledDom(),host=dom.createElement('div'),chart=mountReferenceChart(host,{Chart:dom.Chart,dataset:data,capabilities:validateCapabilities(fixtures.capabilities),contextId:ids[0]});
  assert.ok(host.textContent.includes('<img src=x onerror=alert(1)>'));assert.equal(walk(host).filter(node=>node.localName==='img').length,0);chart.destroy();
});
