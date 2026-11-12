/** Real evaluator -> production controller -> controlled DOM/Chart canvas calls.
 * Tiny encoded fixture exercises ownership/stamping only, never raster accuracy.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
const root=process.env.NIVRAM_CONDITION_SOURCE;
const moduleUrl=name=>root?pathToFileURL(`${root}/public/modules/${name}.mjs`):new URL(`../public/modules/${name}.mjs`,import.meta.url);
const {createDocumentStore}=await import(moduleUrl('document'));
const {createPopulationAnalysis}=await import(moduleUrl('population-analysis'));
const {mountPopulationChart}=await import(moduleUrl('supplied-population-chart'));
const {populationConditionText,validatePopulationEnvelope,createPhSelection,serializePopulationJson,populationCsv}=await import(moduleUrl('population-view'));
const fixture=JSON.parse(await readFile(new URL('./fixtures/population-analytical.json',import.meta.url),'utf8')).complete;
const dataset=JSON.parse(await readFile(new URL('../nivram/data/physiology.json',import.meta.url),'utf8'));
const base=JSON.stringify(fixture.envelope.model),hash=value=>createHash('sha256').update(value).digest('hex');
const tiny=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC','base64');
const walk=node=>[node,...node.children.flatMap(walk)];
class Element extends EventTarget{
  constructor(tag,dom){super();this.localName=tag;this.ownerDocument=dom;this.children=[];this.parentNode=null;this.dataset={};this.style={};this.attrs={};this._text='';}
  get textContent(){return this._text+this.children.map(node=>node.textContent).join('');}
  set textContent(value){this._text=String(value);this.replaceChildren();}
  append(...nodes){for(const node of nodes){node.remove();node.parentNode=this;this.children.push(node);}}
  replaceChildren(...nodes){for(const node of this.children)node.parentNode=null;this.children=[];this.append(...nodes);}
  remove(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(node=>node!==this);this.parentNode=null;}}
  setAttribute(key,value){this.attrs[key]=String(value);}
  focus(){}
  toBlob(done){this.ownerDocument.pending.push({canvas:this,done});}
}
function controlledDom(){
  const dom={createElement:tag=>new Element(tag,dom),pending:[],charts:[]};
  dom.Chart=class{
    static version='4.5.1';
    constructor(canvas,config){this.canvas=canvas;this.config=config;this.options=config.options;this.data=config.data;this.width=1000;this.height=720;this.chartArea={left:60,right:980,top:400,bottom:580};this.scales={x:{getPixelForValue:v=>60+v*920/14,getValueForPixel:v=>(v-60)*14/920}};this.texts=[];
      this.ctx=new Proxy({fillText:text=>this.texts.push(String(text)),measureText:text=>({width:String(text).length*5})},{get:(target,key)=>key in target?target[key]:()=>{}});dom.charts.push(this);}
    draw(){this.texts=[];for(const plugin of this.config.plugins){plugin.beforeDraw?.(this);plugin.afterDraw?.(this);}}
    update(){this.draw();}
    setDatasetVisibility(index,visible){this.data.datasets[index].hidden=!visible;}
    destroy(){this.destroyed=true;}
  };
  return dom;
}
function definitions(host){const nodes=walk(host).find(node=>node.localName==='dl').children,out={};for(let index=0;index<nodes.length;index+=2)out[nodes[index].textContent]=nodes[index+1].textContent;return out;}
function python(mode,value){const child=spawnSync(process.env.PYTHON??'python',[new URL('./population-exact-model-helper.py',import.meta.url).pathname,mode],{input:JSON.stringify(value),encoding:'utf8',maxBuffer:40*1024*1024,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});assert.equal(child.status,0,child.stderr);return child.stdout;}
async function evaluated(text){
  const store=createDocumentStore({schema:'nivram/1',graph:{atoms:[],bonds:[]}}),before=store.getSnapshot(),history=store.historyState();
  const analysis=createPopulationAnalysis({editor:store,fetcher:async(url,options)=>{assert.equal(url,'/api/populations/evaluate');return new Response(python('evaluate',JSON.parse(options.body)));}});
  try{return{result:await analysis.loadText(text,{name:'synthetic-condition.json'}),analysis,store,before,history};}catch(error){analysis.dispose();throw error;}
}
const change=(text,field,raw)=>text.replace(new RegExp('("'+field+'":)(?:null|-?(?:0|[1-9]\\d*)(?:\\.\\d+)?(?:[eE][+-]?\\d+)?)'),(_,key)=>key+raw);
const cases=[
  ['normal',base,{temperature_kelvin:'298.15',ionic_strength_mol_l:'Not supplied / unknown',reference_ph:'7',ph_min:'0',ph_max:'14'}],
  ['large integers',change(change(base,'temperature_kelvin','9007199254740993'),'ionic_strength_mol_l','9007199254740995'),{temperature_kelvin:'9007199254740993',ionic_strength_mol_l:'9007199254740995',reference_ph:'7',ph_min:'0',ph_max:'14'}],
  ['float spellings',change(change(change(change(base,'temperature_kelvin','2.9815e2'),'ionic_strength_mol_l','1e-999'),'reference_ph','7.000'),'ph_max','1.4e1'),{temperature_kelvin:'298.15',ionic_strength_mol_l:'0.0',reference_ph:'7.0',ph_min:'0',ph_max:'14.0'}],
  ['signed zero',change(change(change(change(base,'ionic_strength_mol_l','-0.0'),'reference_ph','-0.0'),'ph_min','-0.0'),'ph_max','14.0'),{temperature_kelvin:'298.15',ionic_strength_mol_l:'-0.0',reference_ph:'-0.0',ph_min:'-0.0',ph_max:'14.0'}],
  ['integer negative zero',change(change(change(base,'ionic_strength_mol_l','-0'),'reference_ph','-0'),'ph_min','-0'),{temperature_kelvin:'298.15',ionic_strength_mol_l:'0',reference_ph:'0',ph_min:'0',ph_max:'14'}],
  ['long float spelling',change(base,'temperature_kelvin','2.9815e+'+'0'.repeat(150000)+'2'),{temperature_kelvin:'298.15',ionic_strength_mol_l:'Not supplied / unknown',reference_ph:'7',ph_min:'0',ph_max:'14'}],
  ['large float semantics',change(base,'temperature_kelvin','9007199254740993.0'),{temperature_kelvin:'9007199254740992.0',ionic_strength_mol_l:'Not supplied / unknown',reference_ph:'7',ph_min:'0',ph_max:'14'}],
];

test('real evaluator condition values reach DOM and PNG canvas calls without parsed-number rounding',async()=>{
  const originalFetch=globalThis.fetch;globalThis.fetch=async()=>Response.json({version:'condition-test'});
  try{for(const [name,text,expected] of cases){
    const f=await evaluated(text),dom=controlledDom(),host=dom.createElement('div');
    const chart=mountPopulationChart(host,{Chart:dom.Chart,envelope:f.result.envelope,dataset:null,conditions:{temperature_kelvin:'UNTRUSTED OVERRIDE'}});
    try{
      const displayed=definitions(host);
      for(const [key,value] of Object.entries(expected))assert.equal(displayed['conditions.'+key],value,`${name}: ${key}`);
      assert.equal(Object.hasOwn(displayed,'binding.modelInputHash'),false);assert.equal(f.result.modelInputHash,hash(text));assert.equal(displayed['binding.modelSignature'],f.result.envelope.modelSignature);
      assert.equal(displayed['provenance.input_identity'],fixture.envelope.model.provenance.input_identity);assert.ok(!host.textContent.includes('UNTRUSTED OVERRIDE'));
      const exporting=chart.exportPng(),drawn=dom.charts.at(-1);
      assert.ok(drawn.texts.includes(`Conditions: ${expected.temperature_kelvin} K; water; model domain pH ${expected.ph_min}-${expected.ph_max}.`),name);
      assert.ok(drawn.texts.includes(`Input identity: ${fixture.envelope.model.provenance.input_identity}`));assert.ok(drawn.texts.includes(`Model signature: ${f.result.envelope.modelSignature}`));
      // Ionic strength remains in the full DOM provenance, not a new PNG line.
      assert.ok(!drawn.texts.some(line=>line.includes('ionic_strength')));
      dom.pending[0].done(new Blob([tiny],{type:'image/png'}));assert.equal((await exporting).type,'image/png');
      const options={source:{name:f.result.name,modelInputHash:f.result.modelInputHash}},json=serializePopulationJson(f.result.envelope,options),csv=populationCsv(f.result.envelope,options);
      assert.deepEqual(JSON.parse(python('validate',{modelText:text,modelInputHash:hash(text),json,csv})).validated,['json','csv']);
      assert.deepEqual(f.store.getSnapshot(),f.before);assert.deepEqual(f.store.historyState(),f.history);assert.equal(f.result.modelText,text);
    }finally{chart.destroy();f.analysis.dispose();}
  }}finally{globalThis.fetch=originalFetch;}
});

test('generic validated adapters keep their existing labels and do not invent an original input hash',async()=>{
  const envelope=validatePopulationEnvelope(fixture.envelope,fixture.binding),dom=controlledDom(),host=dom.createElement('div'),chart=mountPopulationChart(host,{Chart:dom.Chart,envelope,dataset:null});
  try{
    const display=definitions(host);
    assert.throws(()=>populationConditionText(structuredClone(envelope),'temperature_kelvin'),/validated population envelope/);
    for(const field of ['__proto__','toString','/conditions/temperature_kelvin',Symbol('temperature_kelvin'),null])
      assert.throws(()=>populationConditionText(envelope,field),/supported model condition/);
    for(const [key,value] of Object.entries(envelope.model.conditions))assert.equal(display['conditions.'+key],value===null?'Not supplied / unknown':String(value));
    assert.equal(populationConditionText(envelope,'ionic_strength_mol_l'),null);assert.equal(Object.hasOwn(display,'binding.modelInputHash'),false);
    const exporting=chart.exportPng(),rejected=assert.rejects(exporting,/encoding failed/);
    assert.ok(dom.charts.at(-1).texts.includes('Conditions: 298.15 K; water; model domain pH 0-14.'));
    dom.pending[0].done(null);await rejected;assert.equal(dom.pending[0].canvas.width,0);
  }finally{chart.destroy();}
});

test('captured PNG condition text and source identity survive context, pH and visibility changes; disposal cancels',async()=>{
  const [_,text]=cases.find(([name])=>name==='large integers'),f=await evaluated(text),dom=controlledDom(),host=dom.createElement('div'),selection=createPhSelection();
  const chart=mountPopulationChart(host,{Chart:dom.Chart,envelope:f.result.envelope,selection,dataset,contextId:'arterial-blood-uvm'}),originalFetch=globalThis.fetch;
  globalThis.fetch=async()=>Response.json({version:'condition-test'});
  try{
    const before=chart.getSnapshot(),display=definitions(host),exporting=chart.exportPng(),drawn=dom.charts.at(-1),captured=[...drawn.texts];
    chart.updateContext({contextId:'rat-late-proximal-dubose-1983'});selection.enterPh('8');chart.selectState('A');
    const toggle=walk(host).find(node=>node.dataset.populationVisible==='HA');toggle.checked=false;toggle.dispatchEvent(new Event('change'));
    drawn.draw();assert.deepEqual(drawn.texts,captured);assert.deepEqual(definitions(host),display);assert.equal(chart.getSnapshot().envelope,before.envelope);
    assert.ok(drawn.texts.includes('Conditions: 9007199254740993 K; water; model domain pH 0-14.'));
    assert.ok(drawn.texts.some(line=>line.startsWith('Selected pH 7.0')));assert.ok(drawn.texts.some(line=>line.startsWith('Reference: '+before.reference.context.label)));
    dom.pending[0].done(new Blob([tiny],{type:'image/png'}));await exporting;
    const next=chart.exportPng(),nextDrawn=dom.charts.at(-1);
    assert.ok(nextDrawn.texts.some(line=>line.startsWith('Selected pH 8.0')));assert.ok(nextDrawn.texts.some(line=>line.startsWith('Reference: '+chart.getSnapshot().reference.context.label)));
    assert.ok(nextDrawn.texts.includes('Conditions: 9007199254740993 K; water; model domain pH 0-14.'));
    const rejected=assert.rejects(next,error=>error.name==='AbortError');chart.destroy();await rejected;
    assert.equal(dom.pending[1].canvas.width,0);assert.equal(dom.pending[1].canvas.height,0);assert.equal(nextDrawn.destroyed,true);
    dom.pending[1].done(new Blob([tiny],{type:'image/png'}));assert.deepEqual(f.store.getSnapshot(),f.before);assert.deepEqual(f.store.historyState(),f.history);
  }finally{chart.destroy();f.analysis.dispose();globalThis.fetch=originalFetch;}
});
