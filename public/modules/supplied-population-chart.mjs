import {stampPng} from './png-metadata.mjs';
/** Presentation of a validated supplied result. No model evaluation, network or drawing mutation. */
import {createPhSelection,populationSeries,populationTable,referenceView,populationConditionText} from './population-view.mjs';
import {drawReferenceOverlay} from './population-chart.mjs';

const mounts=new WeakMap();let nextId=0;
const themes={light:{background:'#ffffff',text:'#202632',grid:'#d8dde5'},dark:{background:'#191c23',text:'#edf0f5',grid:'#48505e'}};
const numberFormat=new Intl.NumberFormat('en-US',{maximumSignificantDigits:10});
const percent=value=>value===null?'Unknown':`${numberFormat.format(value)}%`;
const mass=value=>value===null?'Unknown':percent(value*100);
const kindNames={reference_band:'Reference interval',observed_range:'Observed range',study_marker:'Source point',confidence_interval:'Mean and confidence interval',sd_interval:'Mean and standard deviation',sem_interval:'Mean and standard error',custom_marker:'User point — unverified',custom_band:'User range — unverified'};
const checkTheme=value=>{if(!Object.hasOwn(themes,value))throw new TypeError('Theme must be light or dark.');return value;};
const exportWarning='Supplied-model calculation; not a validated molecular prediction.';
const exportWidth=1000,exportHeight=720,exportHeader=198,exportFooter=116,legendLimit=12;

// The same complete curves feed the live and export canvases. Hidden states keep
// their original samples and percentages; no decimation or mass redistribution.
function curveDatasets(series,envelope,visibleStateIds){
  const visible=new Set(visibleStateIds);
  return series.map((state,position)=>({label:state.stateId,hidden:!visible.has(state.stateId),data:state.points.map(point=>({x:point.x,y:point.y===null?null:envelope.samples[point.index].result.percentages[position]})),borderColor:state.style.color,backgroundColor:state.style.color,borderDash:[...state.style.dash],borderWidth:2,spanGaps:false,pointRadius:state.points.map((point,index)=>point.y!==null&&(index===0||state.points[index-1].y===null)&&(index===140||state.points[index+1].y===null)?3:0),pointHitRadius:0,tension:0,fill:false,parsing:false,normalized:true}));
}

function boundedText(ctx,text,x,y,width){
  // The full identity and state labels remain in JSON. Long source-supplied
  // labels must not overflow a fixed publication canvas or hide its warning.
  text=String(text).replace(/[\r\n\t]/g,' ');
  if(ctx.measureText(text).width>width){
    const suffix='... [full text in JSON]';let low=0,high=text.length;
    while(low<high){const middle=Math.ceil((low+high)/2);if(ctx.measureText(text.slice(0,middle)+suffix).width<=width)low=middle;else high=middle-1;}
    text=text.slice(0,low)+suffix;
  }
  ctx.fillText(text,x,y,width);
}

function drawExportLabels(ctx,captured,series){
  const {envelope,selection,table,reference,visibleStateIds}=captured,model=envelope.model,visible=new Set(visibleStateIds),shown=series.filter(state=>visible.has(state.stateId)),legend=shown.slice(0,legendLimit);
  const condition=key=>populationConditionText(envelope,key);
  ctx.save();ctx.fillStyle=themes.light.text;ctx.textAlign='left';ctx.textBaseline='top';
  ctx.font='bold 20px sans-serif';ctx.fillText(model.provenance.source_kind==='synthetic_analytical'?'Synthetic analytical population example':'Supplied-model populations',20,16);
  ctx.font='bold 13px sans-serif';ctx.fillText(exportWarning,20,44);
  ctx.font='12px sans-serif';
  ctx.fillText(`Selected pH ${selection.ph.toFixed(1)}; 141 samples, pH 0-14. Missing values remain gaps.`,20,66);
  boundedText(ctx,`Normalization: ${table.normalizationScope??'No population values at selected pH'}; equilibrium: ${model.equilibrium_scope}.`,20,84,960);
  boundedText(ctx,`Represented: ${mass(table.representedMass)}; plotted: ${mass(table.plottedMass)}; hidden represented: ${mass(table.hiddenRepresentedMass)}; omitted: ${mass(table.omittedMass)}.`,20,102,960);
  boundedText(ctx,`Input identity: ${model.provenance.input_identity}`,20,120,960);
  ctx.fillText(`Model signature: ${envelope.modelSignature}`,20,138);
  boundedText(ctx,`Conditions: ${condition('temperature_kelvin')} K; ${model.conditions.solvent}; model domain pH ${condition('ph_min')}-${condition('ph_max')}.`,20,156,960);
  boundedText(ctx,`Reference: ${reference.context?.label??'none selected'}; cited context only, not in-vivo validation.`,20,174,960);
  ctx.fillText(`Curves shown: ${shown.length} of ${series.length}; hidden: ${series.length-shown.length}. Legend: ${legend.length} of ${shown.length} visible states.`,20,611);
  for(const [index,state] of legend.entries()){
    const x=20+(index%4)*245,y=633+Math.floor(index/4)*19;
    ctx.save();ctx.strokeStyle=state.style.color;ctx.lineWidth=3;ctx.setLineDash([...state.style.dash]);ctx.beginPath();ctx.moveTo(x,y+6);ctx.lineTo(x+24,y+6);ctx.stroke();ctx.restore();
    boundedText(ctx,state.stateId,x+31,y,207);
  }
  ctx.fillText('All visible curves are plotted. Export JSON for every state, full labels, conditions, warnings and results.',20,699);
  ctx.restore();
}

/** Caller owns envelope validation/binding and the optional shared pH selection. */
export function mountPopulationChart(host,{Chart,envelope,selection=createPhSelection(),dataset,contextId=null,comparison=null,theme='light',onStateSelect=()=>{}}={}){
  if(!host?.ownerDocument||typeof host.append!=='function')throw new TypeError('A DOM host is required.');
  if(mounts.has(host))throw new Error('Destroy the existing population chart before mounting again.');
  if(typeof Chart!=='function'||Chart.version!=='4.5.1')throw new TypeError('The verified local Chart.js 4.5.1 is required.');
  if(typeof onStateSelect!=='function')throw new TypeError('The state selection callback must be a function.');
  for(const key of ['getSnapshot','enterPh','pointerPh','key','subscribe'])if(typeof selection?.[key]!=='function')throw new TypeError('A shared pH selection store is required.');
  // These functions require the validator's accepted object identity. A plain
  // object or copied envelope cannot silently bypass the presentation boundary.
  const series=populationSeries(envelope);
  let ownedDataset=dataset==null?null:structuredClone(dataset);
  const resolveReference=(contextId,comparison,source=ownedDataset)=>{
    if(source!==null)return referenceView(source,contextId,comparison);
    if(contextId!==null&&contextId!==''||comparison!==null)throw new Error('Physiological reference data is unavailable; population values remain usable without a reference.');
    return Object.freeze({context:null,comparison:null,overlays:Object.freeze([]),sources:Object.freeze([]),interpretationRules:Object.freeze([]),gap:Object.freeze({label:'Physiological reference data is unavailable',reason:'No reference interval or comparison has been added to the supplied populations.'})});
  };
  let currentTheme=checkTheme(theme),reference=resolveReference(contextId,comparison),chart=null,disposed=false,unsubscribe=()=>{},selectedStateId=envelope.model.reference_state_id;
  const visible=new Set(series.map(state=>state.stateId)),listeners=[],rows=new Map(),exports=new Set(),dom=host.ownerDocument,id=`nivram-populations-${++nextId}`;
  let table=populationTable(envelope,selection.getSnapshot().index);
  const element=(tag,text=null)=>{const value=dom.createElement(tag);if(text!==null)value.textContent=text;return value;};
  const listen=(target,event,handler)=>{target.addEventListener(event,handler);listeners.push(()=>target.removeEventListener(event,handler));};
  const section=element('section');section.dataset.populationChart='';section.setAttribute('aria-labelledby',`${id}-title`);Object.assign(section.style,{minWidth:'0',overflowWrap:'anywhere'});
  const heading=element('h3',envelope.model.provenance.source_kind==='synthetic_analytical'?'Synthetic analytical population example':'Supplied-model populations');heading.id=`${id}-title`;
  const scope=element('p',envelope.model.provenance.source_kind==='synthetic_analytical'?'Synthetic analytical data only. These values are not a molecular prediction or an analysis of the drawing.':'These are mathematical results from a supplied model. Chemical states are not inferred from the drawing; model applicability and molecular identity are not independently validated.');
  const identity=element('p',`Input identity: ${envelope.model.provenance.input_identity}. Engine: ${envelope.model.provenance.engine} ${envelope.model.provenance.engine_version}. Model: ${envelope.model.provenance.model_name} ${envelope.model.provenance.model_version}.`);
  const help=element('p','Move the pointer over the plot, use Left/Right and Home/End, or enter an exact 0.1 pH sample. Curves retain all 141 positions. Gaps remain gaps; hiding curves never changes percentages.');help.id=`${id}-help`;
  const frame=element('div');Object.assign(frame.style,{position:'relative',height:'420px',width:'100%',minWidth:'0'});
  const canvas=element('canvas');canvas.dataset.populationCanvas='';canvas.tabIndex=0;canvas.setAttribute('role','slider');canvas.setAttribute('aria-label','Selected population pH');canvas.setAttribute('aria-valuemin','0');canvas.setAttribute('aria-valuemax','14');canvas.setAttribute('aria-describedby',help.id);canvas.textContent='Use the pH entry and state table for the same supplied values.';frame.append(canvas);
  const form=element('form');form.dataset.populationForm='';const label=element('label','Selected pH (0–14, exact 0.1 samples)'),input=element('input'),submit=element('button','Select pH'),error=element('p');
  label.htmlFor=`${id}-ph`;input.id=label.htmlFor;input.dataset.populationPh='';input.type='text';input.inputMode='decimal';input.maxLength=128;submit.type='submit';label.style.display='block';error.setAttribute('role','alert');form.append(label,input,submit,error);
  const readout=element('p');readout.dataset.populationReadout='';readout.setAttribute('role','status');readout.setAttribute('aria-live','polite');
  const massSummary=element('p');massSummary.dataset.populationMass='';const interpretation=element('p'),warnings=element('ul');warnings.dataset.populationWarnings='';
  const tableFrame=element('div');Object.assign(tableFrame.style,{maxHeight:'420px',overflow:'auto'});const stateTable=element('table'),caption=element('caption');stateTable.style.width='100%';stateTable.dataset.populationTable='';
  const head=element('thead'),headRow=element('tr');for(const text of ['Inspect','Plot','State','Population (%)','Visibility']){const cell=element('th',text);cell.scope='col';headRow.append(cell);}head.append(headRow);
  const body=element('tbody');body.dataset.populationRows='';stateTable.append(caption,head,body);tableFrame.append(stateTable);
  for(const [position,state] of series.entries()){
    const row=element('tr');row.dataset.stateId=state.stateId;
    const inspectCell=element('td'),inspect=element('input');inspect.type='radio';inspect.name=`${id}-inspect`;inspect.value=state.stateId;inspect.checked=selectedStateId===state.stateId;inspect.dataset.populationInspect=state.stateId;inspect.setAttribute('aria-label',`Inspect ${state.stateId} state`);inspectCell.append(inspect);
    const toggleCell=element('td'),toggle=element('input');toggle.type='checkbox';toggle.checked=true;toggle.dataset.populationVisible=state.stateId;toggle.setAttribute('aria-label',`Show ${state.stateId} curve`);toggleCell.append(toggle);
    const stateCell=element('th');stateCell.scope='row';const key=element('span'),name=element('span',state.stateId);key.setAttribute('aria-hidden','true');Object.assign(key.style,{display:'inline-block',width:'24px',marginRight:'8px',borderTop:`3px ${state.style.dash.length?'dashed':'solid'} ${state.style.color}`});stateCell.append(key,name);
    const value=element('td'),visibility=element('td','Shown');value.dataset.populationPercentage='';row.append(inspectCell,toggleCell,stateCell,value,visibility);body.append(row);rows.set(state.stateId,{value,visibility,toggle,inspect});
    listen(inspect,'change',()=>{if(!disposed&&inspect.checked)selectState(state.stateId);});
    listen(toggle,'change',()=>{if(disposed)return;toggle.checked?visible.add(state.stateId):visible.delete(state.stateId);chart.setDatasetVisibility(position,toggle.checked);refreshSelection();chart.update('none');});
  }
  const referenceHeading=element('h4','Physiological reference context'),referenceDetail=element('p'),referenceValues=element('ul'),referenceLimits=element('ul'),referenceSources=element('ul');
  const referenceNotice=element('p','Reference lanes share the pH axis but describe their cited compartments and statistical meanings only. They do not validate these model populations in vivo. Reference boundaries remain continuous, including between sampled pH values.');
  const provenance=element('details');provenance.dataset.populationProvenance='';provenance.append(element('summary','Model conditions, source, scope and provenance'));
  const definitions=element('dl');
  for(const [group,values] of Object.entries({conditions:envelope.model.conditions,provenance:envelope.model.provenance,scope:{supported_classes:envelope.model.supported_classes,equilibrium_scope:envelope.model.equilibrium_scope,equilibrium_justification:envelope.model.equilibrium_justification,ensemble_complete:envelope.model.ensemble_complete},binding:{documentRevision:envelope.documentRevision,graphHash:envelope.graphHash,modelSignature:envelope.modelSignature}})){
    for(const [key,original] of Object.entries(values)){const value=group==='conditions'?populationConditionText(envelope,key):original;definitions.append(element('dt',`${group}.${key}`),element('dd',value===null?'Not supplied / unknown':typeof value==='object'?JSON.stringify(value):String(value)));}
  }
  provenance.append(definitions);section.append(heading,scope,identity,help,frame,form,readout,massSummary,interpretation,warnings,tableFrame,referenceHeading,referenceNotice,referenceDetail,referenceValues,referenceLimits,referenceSources,provenance);
  const live=()=>{if(disposed)throw new Error('Population chart is disposed.');};
  const snapshot=()=>Object.freeze({envelope,reference,selection:selection.getSnapshot(),selectedStateId,table,visibleStateIds:Object.freeze([...visible]),theme:currentTheme,disposed});
  function selectState(stateId){
    live();if(typeof stateId!=='string'||!rows.has(stateId))throw new TypeError('Select a state from the supplied model.');
    if(stateId===selectedStateId)return snapshot();
    selectedStateId=stateId;for(const [id,cells] of rows)cells.inspect.checked=id===stateId;
    onStateSelect(stateId);return snapshot();
  }
  const topPadding=()=>reference.overlays.length?reference.overlays.length*58+12:12;
  function refreshSelection(){
    const selected=selection.getSnapshot();table=populationTable(envelope,selected.index,{visibleStateIds:[...visible]});
    input.value=selected.ph.toFixed(1);canvas.setAttribute('aria-valuenow',String(selected.ph));canvas.setAttribute('aria-valuetext',`pH ${selected.ph.toFixed(1)}; ${table.status==='computed'?'supplied-model values':'population values unavailable'}`);
    readout.textContent=`Selected pH ${selected.ph.toFixed(1)} (sample ${selected.index} of 0–140). ${table.status==='computed'?'Supplied-model values.':table.reason}`;
    caption.textContent=`All ${series.length} supplied states at pH ${selected.ph.toFixed(1)}; percentages retain their declared normalization scope.`;
    const currentRows=new Map(table.rows.map(row=>[row.stateId,row]));
    for(const state of series){const cells=rows.get(state.stateId),value=currentRows.get(state.stateId);cells.value.textContent=value?percent(value.percentage):'Unavailable';cells.value.dataset.percentage=value?String(value.percentage):'';cells.value.title=value?`Supplied percentage: ${value.percentage}; fraction: ${value.fraction}`:table.reason;cells.visibility.textContent=visible.has(state.stateId)?'Shown':'Hidden (mass retained)';cells.toggle.checked=visible.has(state.stateId);}
    massSummary.textContent=`Represented mass: ${mass(table.representedMass)}; plotted: ${mass(table.plottedMass)}; hidden represented: ${mass(table.hiddenRepresentedMass)}; omitted: ${mass(table.omittedMass)}; full-ensemble mass: ${mass(table.fullEnsembleMass)}.${table.omittedMassSource?` Omitted-mass source: ${table.omittedMassSource}.`:''}`;
    interpretation.textContent=table.status==='computed'?`Normalization: ${table.normalizationScope}. Equilibrium scope: ${table.equilibriumScope}. ${table.interpretation}`:`No values are extrapolated or substituted. ${table.reason}`;
    warnings.replaceChildren();for(const text of table.warnings)warnings.append(element('li',text));
  }
  function refreshReference(){
    const context=reference.context;
    referenceDetail.textContent=context?`${context.label}. ${context.species}; ${context.population}; ${context.preparation}; ${context.condition}. Compartment: ${context.compartment}; anatomical site: ${context.anatomical_site}. ${context.temperature_note} Measurement: ${context.measurement_method}. Handling: ${context.specimen_handling}.`:ownedDataset===null?'Physiological reference data is unavailable. Supplied-model curves and table remain available.':'Reference overlays are off until a context is selected.';
    referenceValues.replaceChildren();for(const overlay of reference.overlays){const limits=overlay.lower===null?`pH ${overlay.value}`:`pH ${overlay.lower}–${overlay.upper}${overlay.value===null?'':`; mean ${overlay.value}`}`,confidence=overlay.confidenceLevel===null?'':`; confidence level ${overlay.confidenceLevel*100}%`;referenceValues.append(element('li',`${overlay.label}: ${overlay.statistic==='reported_value'?'Reported value (central statistic unverified)':kindNames[overlay.kind]}; ${limits}${confidence}. ${overlay.evidenceStatus}; source IDs: ${overlay.sourceIds.join(', ')||'user entered; unverified'}.`));}
    if(reference.gap)referenceValues.append(element('li',`${reference.gap.label}. ${reference.gap.reason}`));
    referenceLimits.replaceChildren();for(const text of [...reference.interpretationRules,...(context?.evidence_limitations||[])])referenceLimits.append(element('li',text));
    if(context?.reported_uncertainty&&!context.reported_uncertainty.plotted)referenceLimits.append(element('li',`Reported uncertainty ${context.reported_uncertainty.value} (${context.reported_uncertainty.kind}) is not plotted or reinterpreted as an interval.`));
    referenceSources.replaceChildren();for(const source of reference.sources)referenceSources.append(element('li',`${source.id}: ${source.title}. ${source.url} Inspected: ${source.inspected_scope}. Accessed: ${source.accessed_date}.`));
  }
  function refreshTheme(){
    const colors=themes[currentTheme];section.style.color=colors.text;section.style.backgroundColor=colors.background;chart.options.color=colors.text;chart.options.plugins.title.color=colors.text;
    for(const axis of ['x','y']){const scale=chart.options.scales[axis];scale.title.color=colors.text;scale.ticks.color=colors.text;scale.grid.color=colors.grid;scale.border.color=colors.grid;}
  }
  function updateReference(next,source=ownedDataset){
    const previous=reference,previousDataset=ownedDataset;
    try{reference=next;ownedDataset=source;refreshReference();chart.options.layout.padding.top=topPadding();chart.update('none');return snapshot();}
    catch(error){
      reference=previous;ownedDataset=previousDataset;
      try{refreshReference();chart.options.layout.padding.top=topPadding();chart.update('none');}
      catch(restoreError){throw new AggregateError([error,restoreError],'The reference update and its display restoration failed.');}
      throw error;
    }
  }
  function destroy(){
    if(disposed)return;disposed=true;unsubscribe();for(const remove of listeners.splice(0))remove();
    for(const record of [...exports]){record.cancel?.();cleanupExport(record);}
    if(!chart)chart=Chart.getChart?.(canvas)??null;if(chart){chart.destroy();chart=null;}section.remove();mounts.delete(host);
  }
  function cleanupExport(record){
    if(!record.chart)record.chart=Chart.getChart?.(record.canvas)??null;
    if(record.chart){record.chart.destroy();record.chart=null;}
    record.canvas.width=0;record.canvas.height=0;exports.delete(record);
  }
  function config(readState,{exporting=false,transparentBackground=false}={}){
    const state=readState(),colors=themes[state.theme],padding=()=>readState().reference.overlays.length*58+12;
    const plugin={id:'nivram-supplied-population-context',beforeDraw(value){if(exporting&&transparentBackground)return;const ctx=value.ctx;ctx.save();ctx.fillStyle=themes[readState().theme].background;ctx.fillRect(0,0,value.width,value.height);ctx.restore();},afterDraw(value){
    const state=readState(),colors=themes[state.theme];
    const {ctx,chartArea:area,scales}=value;if(!area||area.right<=area.left)return;
    const lane={left:area.left,right:area.right,top:area.top-padding()+6,bottom:area.top-3};
    state.reference.overlays.forEach((overlay,index)=>drawReferenceOverlay(ctx,scales.x,lane,overlay,lane.top+38+index*58,colors));
    const x=scales.x.getPixelForValue(state.selection.ph);ctx.save();ctx.strokeStyle=colors.text;ctx.lineWidth=1;ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(x,state.reference.overlays.length?lane.top:area.top);ctx.lineTo(x,area.bottom);ctx.stroke();ctx.restore();
    if(exporting)drawExportLabels(ctx,state,series);
  }};
    return {type:'line',plugins:[plugin],data:{datasets:curveDatasets(series,envelope,state.visibleStateIds)},options:{animation:false,responsive:!exporting,maintainAspectRatio:false,devicePixelRatio:exporting?1:undefined,events:[],locale:'en-US',parsing:false,normalized:true,color:colors.text,layout:{padding:{top:padding()+(exporting?exportHeader:0),right:12,bottom:exporting?exportFooter:8,left:12}},plugins:{legend:{display:false},tooltip:{enabled:false},title:{display:false,text:'Supplied populations (%) across pH',color:colors.text}},scales:{x:{type:'linear',min:0,max:14,title:{display:true,text:'pH',color:colors.text},ticks:{stepSize:1,color:colors.text},grid:{color:colors.grid},border:{color:colors.grid}},y:{type:'linear',min:0,max:100,title:{display:true,text:'Population (%)',color:colors.text},ticks:{color:colors.text},grid:{color:colors.grid},border:{color:colors.grid}}}}};
  }
  async function exportPng({transparentBackground=false}={}){
    if(typeof transparentBackground!=='boolean')throw new TypeError('transparentBackground must be a boolean.');
    live();if(exports.size)throw new Error('A population PNG export is already in progress.');
    // Snapshot members are immutable validated values. Copy the visible set and
    // selected sample before rendering; later UI changes cannot rewrite it.
    const captured=Object.freeze({...snapshot(),theme:'light'}),output=dom.createElement('canvas');output.width=exportWidth;output.height=exportHeight;
    const record={canvas:output,chart:null,cancel:null};exports.add(record);
    try{
      record.chart=new Chart(output,config(()=>captured,{exporting:true,transparentBackground}));
      if(!record.chart.ctx)throw new Error('A 2D export canvas is required.');
      record.chart.update('none');
      const blob=await new Promise((resolve,reject)=>{
        record.cancel=()=>reject(new DOMException('Population chart was disposed during export.','AbortError'));
        output.toBlob(value=>value?resolve(value):reject(new Error('PNG encoding failed.')),'image/png');
      });
      live();if(blob.type!=='image/png'||blob.size===0||blob.size>8388608)throw new Error('Population PNG encoding exceeds its bound.');
      const stamped=await stampPng(blob);live();
          if(stamped.size>8388608)throw new Error('PNG metadata exceeds output bound.');
          return stamped;
    }finally{cleanupExport(record);}
  }
  try{
    host.append(section);chart=new Chart(canvas,config(snapshot));
    if(!chart.ctx)throw new Error('A 2D canvas is required.');refreshSelection();refreshReference();refreshTheme();chart.update('none');
    listen(canvas,'keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();error.textContent='';selection.key(event.key);}});
    const pointer=event=>{if(!chart||event.isPrimary===false)return;const rect=canvas.getBoundingClientRect();if(rect.width<=0||rect.height<=0)return;const x=(event.clientX-rect.left)*chart.width/rect.width,y=(event.clientY-rect.top)*chart.height/rect.height,area=chart.chartArea;if(x<area.left||x>area.right||y<area.top||y>area.bottom)return;error.textContent='';selection.pointerPh(chart.scales.x.getValueForPixel(x));};
    listen(canvas,'pointermove',pointer);listen(canvas,'pointerdown',event=>{canvas.focus({preventScroll:true});pointer(event);});
    listen(form,'submit',event=>{event.preventDefault();try{selection.enterPh(input.value);error.textContent='';refreshSelection();}catch(failure){error.textContent=failure.message;}});
    unsubscribe=selection.subscribe(()=>{if(!disposed){refreshSelection();chart.draw();}});
    const api=Object.freeze({getSnapshot:snapshot,selectState,setTheme(value){live();currentTheme=checkTheme(value);refreshTheme();chart.update('none');return snapshot();},
      updateContext({contextId=null,comparison=null}={}){live();return updateReference(resolveReference(contextId,comparison));},
      setReferenceDataset(value,{contextId=null,comparison=null}={}){
        live();const nextDataset=value==null?null:structuredClone(value),next=resolveReference(contextId,comparison,nextDataset);
        // Reference loading must not recreate the chart or its model/state session.
        return updateReference(next,nextDataset);
      },exportPng,destroy});
    mounts.set(host,api);return api;
  }catch(failure){destroy();throw failure;}
}
