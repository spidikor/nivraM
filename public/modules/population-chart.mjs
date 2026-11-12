import {stampPng} from './png-metadata.mjs';
/** Staged reference-only Chart.js adapter. No prediction, network or project mutation. */
import {createPhSelection, referenceView, referencePresentation} from './population-view.mjs';

const mounts = new WeakMap();
let nextId = 0;
const THEMES = Object.freeze({
  light: Object.freeze({background:'#ffffff', text:'#202632', grid:'#d8dde5'}),
  dark: Object.freeze({background:'#191c23', text:'#edf0f5', grid:'#48505e'}),
});
const TYPES = Object.freeze({
  reference_band:'Reference interval', observed_range:'Observed range',
  study_marker:'Source point', confidence_interval:'Mean and confidence interval',
  sd_interval:'Mean and standard deviation', sem_interval:'Mean and standard error',
  custom_marker:'User point — unverified', custom_band:'User range — unverified',
});
const kindLabel = overlay => overlay.statistic === 'reported_value' ? 'Reported value (central statistic unverified)' : TYPES[overlay.kind] ?? overlay.kind;
const rangeText = overlay => overlay.lower === null
  ? `pH ${overlay.value}` : `pH ${overlay.lower}–${overlay.upper}${overlay.value === null ? '' : `; mean ${overlay.value}`}`;
function checkTheme(theme) {
  if (!Object.hasOwn(THEMES, theme)) throw new TypeError('Theme must be light or dark.');
  return theme;
}
function exportSize(width, height) {
  if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width < 480 || width > 2048
      || height < 240 || height > 1536 || width * height > 3000000) {
    throw new RangeError('Reference PNG must be 480–2048 by 240–1536 pixels and at most 3 million pixels.');
  }
}
function wrap(text, limit=88) {
  const words = text.split(/\s+/), lines=[]; let current='';
  for (const word of words) {
    if (current && current.length + word.length + 1 > limit) { lines.push(current); current=''; }
    current += (current ? ' ' : '') + word;
  }
  if (current) lines.push(current);
  return lines;
}
function strokeLine(ctx, x1, y1, x2, y2) {
  ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke();
}
export function drawReferenceOverlay(ctx, scale, area, overlay, y, theme) {
  const custom = overlay.kind.startsWith('custom_');
  const color = custom ? '#c16b12' : '#287ac7';
  ctx.save(); ctx.beginPath(); ctx.rect(area.left,area.top,area.right-area.left,area.bottom-area.top); ctx.clip();
  ctx.strokeStyle=color; ctx.fillStyle=color; ctx.lineWidth=2; ctx.setLineDash(custom ? [6,4] : []);
  if (overlay.lower !== null && overlay.upper !== null) {
    const left=scale.getPixelForValue(overlay.lower), right=scale.getPixelForValue(overlay.upper);
    if (overlay.kind === 'reference_band' || overlay.kind === 'custom_band') {
      ctx.fillStyle=custom ? 'rgba(193,107,18,0.18)' : 'rgba(40,122,199,0.20)';
      ctx.fillRect(left,y-12,right-left,24); ctx.strokeRect(left,y-12,right-left,24);
      if (custom) {
        ctx.save(); ctx.beginPath(); ctx.rect(left,y-12,right-left,24); ctx.clip();
        ctx.lineWidth=1; ctx.setLineDash([]);
        for(let x=Math.max(left,area.left)-24; x<Math.min(right,area.right)+24; x+=8) strokeLine(ctx,x,y+12,x+24,y-12);
        ctx.restore();
      }
    } else {
      ctx.setLineDash(overlay.kind==='observed_range' ? [6,3] : overlay.kind==='sd_interval' ? [2,3] : overlay.kind==='sem_interval' ? [8,3,2,3] : []);
      strokeLine(ctx,left,y,right,y); ctx.setLineDash([]);
      strokeLine(ctx,left,y-8,left,y+8); strokeLine(ctx,right,y-8,right,y+8);
    }
  }
  if (overlay.value !== null) {
    const x=scale.getPixelForValue(overlay.value);
    ctx.setLineDash([]); ctx.fillStyle=color; ctx.beginPath();
    if(custom) { ctx.moveTo(x,y-7);ctx.lineTo(x+7,y);ctx.lineTo(x,y+7);ctx.lineTo(x-7,y);ctx.closePath(); }
    else ctx.arc(x,y,5,0,2*Math.PI);
    ctx.fill();
  }
  ctx.fillStyle=theme.text; ctx.font='12px sans-serif'; ctx.textAlign='left'; ctx.textBaseline='bottom';
  const confidence=overlay.confidenceLevel === null ? '' : ` (${overlay.confidenceLevel*100}% CI)`;
  ctx.fillText(`${kindLabel(overlay)}${confidence}: ${rangeText(overlay)}`,area.left+4,y-19,area.right-area.left-8);
  ctx.restore();
}
function chartConfig(readState, {responsive=true, exporting=false, transparentBackground=false}={}) {
  const {theme,reference}=readState(), colors=THEMES[theme];
  const plugin={id:'nivram-reference-context',
    beforeDraw(chart) {
      if(exporting&&transparentBackground)return;
      const {ctx,width,height}=chart; ctx.save(); ctx.fillStyle=THEMES[readState().theme].background;
      ctx.fillRect(0,0,width,height); ctx.restore();
    },
    afterDraw(chart) {
      const state=readState(), {ctx,chartArea:area,scales}=chart, colors=THEMES[state.theme];
      if(!area || area.right<=area.left) return;
      ctx.save();
      const overlays=state.reference.overlays;
      overlays.forEach((overlay,index) => drawReferenceOverlay(ctx,scales.x,area,overlay,
        area.top+(area.bottom-area.top)*(index+1)/(overlays.length+1),colors));
      const x=scales.x.getPixelForValue(state.selection.ph);
      ctx.strokeStyle=colors.text; ctx.lineWidth=1; ctx.setLineDash([3,3]);
      strokeLine(ctx,x,area.top,x,area.bottom); ctx.setLineDash([]);
      ctx.fillStyle=colors.text;ctx.font='12px sans-serif';ctx.textAlign='left';ctx.textBaseline='top';
      ctx.fillText(`Selected pH ${state.selection.ph.toFixed(1)} · reference only`,area.left+4,area.top+3,area.right-area.left-8);
      if(!overlays.length) {
        ctx.textAlign='center';
        ctx.fillText(state.reference.gap ? 'No verified numerical interval available' : 'Select a reference context to show its evidence',
          (area.left+area.right)/2,(area.top+area.bottom)/2,area.right-area.left-12);
      }
      ctx.restore();
    }};
  const subtitle = exporting ? [
    ...wrap(reference.context?.label ?? 'No reference selected'),
    ...(reference.context ? wrap(`${reference.context.species}; ${reference.context.preparation}; ${reference.context.condition}`) : []),
    ...(reference.context ? wrap(`Sources: ${reference.context.source_ids.join(', ') || 'none verified'}; ${reference.context.temperature_note}`) : []),
    ...(reference.gap ? ['Evidence gap retained; a custom comparison does not fill that gap.'] : []),
    `Reference dataset ${reference.datasetVersion}; molecular predictions unavailable.`,
  ] : [];
  return {type:'line',data:{datasets:[]},plugins:[plugin],options:{
    animation:false,responsive,maintainAspectRatio:false,devicePixelRatio:exporting?1:undefined,
    events:[],locale:'en-US',color:colors.text,
    layout:{padding:{top:8,right:12,bottom:8,left:12}},
    plugins:{legend:{display:false},tooltip:{enabled:false},
      title:{display:true,text:['pH reference context',...subtitle],color:colors.text,font:{size:exporting?14:16},padding:12}},
    scales:{x:{type:'linear',min:0,max:14,title:{display:true,text:'pH',color:colors.text},
      ticks:{stepSize:1,color:colors.text},grid:{color:colors.grid},border:{color:colors.grid}},
      y:{type:'linear',min:0,max:1,display:false}},
  }};
}

/** Owns only its created DOM, Chart instances and subscription; caller owns the shared selection. */
export function mountReferenceChart(host, {Chart,dataset,capabilities,selection=createPhSelection(),
    contextId=null,comparison=null,theme='light'}={}) {
  if(!host?.ownerDocument || typeof host.append!=='function') throw new TypeError('A DOM host is required.');
  if(mounts.has(host)) throw new Error('Destroy the existing reference chart before mounting again.');
  if(typeof Chart!=='function' || Chart.version!=='4.5.1') throw new TypeError('The verified local Chart.js 4.5.1 is required.');
  for(const name of ['getSnapshot','enterPh','pointerPh','key','subscribe'])
    if(typeof selection?.[name]!=='function') throw new TypeError('A shared pH selection store is required.');
  const ownedDataset=structuredClone(dataset);
  let currentTheme=checkTheme(theme), presentation=referencePresentation(capabilities,referenceView(ownedDataset,contextId,comparison));
  let chart=null, stopped=false, unsubscribe=()=>{};
  const listeners=[], exports=new Set(), dom=host.ownerDocument, id=`nivram-ph-reference-${++nextId}`;
  const element=(tag,text=null)=>{const node=dom.createElement(tag);if(text!==null)node.textContent=text;return node;};
  const section=element('section');section.dataset.phReference='';section.setAttribute('aria-labelledby',`${id}-title`);
  Object.assign(section.style,{minWidth:'0',overflowWrap:'anywhere'});
  const heading=element('h3','pH reference context');heading.id=`${id}-title`;
  const unavailable=element('p');unavailable.textContent=`Molecular predictions unavailable: ${presentation.unavailableClasses.map(c=>c.id).join(', ')}. Reference values are not molecular populations.`;
  const reasonList=element('ul');for(const value of presentation.unavailableClasses) reasonList.append(element('li',`${value.id}: ${value.reason}`));
  const help=element('p','Move the pointer over the plot, use Left/Right, Home/End, or enter an exact 0.1 pH sample. Reference boundaries remain continuous.');help.id=`${id}-help`;
  const frame=element('div');Object.assign(frame.style,{position:'relative',height:'340px',width:'100%',minWidth:'0'});
  const canvas=element('canvas');canvas.tabIndex=0;canvas.setAttribute('role','slider');canvas.setAttribute('aria-label','Selected reference pH');
  canvas.setAttribute('aria-valuemin','0');canvas.setAttribute('aria-valuemax','14');canvas.setAttribute('aria-describedby',help.id);
  canvas.textContent='Use the pH entry and reference data table for the same information.';frame.append(canvas);
  const form=element('form'), label=element('label','Selected pH (0–14, exact 0.1 samples)'),input=element('input');
  label.htmlFor=`${id}-input`;input.id=label.htmlFor;input.type='text';input.inputMode='decimal';input.maxLength=128;
  label.style.display='block';input.style.maxWidth='100%';
  const apply=element('button','Select pH');apply.type='submit';
  const error=element('p');error.setAttribute('role','alert');error.id=`${id}-error`;input.setAttribute('aria-describedby',`${help.id} ${error.id}`);
  form.append(label,input,apply,error);
  const readout=element('p');readout.dataset.phReadout='';readout.setAttribute('role','status');readout.setAttribute('aria-live','polite');readout.setAttribute('aria-atomic','true');
  const detail=element('p'),gap=element('p'),table=element('table'),caption=element('caption','Reference values; no population percentages');
  table.style.width='100%';table.style.tableLayout='fixed';
  const head=element('thead'),row=element('tr');
  for(const text of ['Context','Statistical meaning / shape','Exact pH','Evidence / source IDs']) {const th=element('th',text);th.scope='col';row.append(th);}
  head.append(row);const body=element('tbody');table.append(caption,head,body);
  const limitations=element('ul'),sources=element('ul'),interpretation=element('details'),rules=element('ul');
  interpretation.append(element('summary','Reference interpretation and limitations'),rules);
  section.append(heading,unavailable,reasonList,help,frame,form,readout,detail,gap,table,limitations,sources,interpretation);
  const live=()=>{if(stopped)throw new Error('Reference chart is disposed.');};
  const state=()=>Object.freeze({reference:presentation.reference,selection:selection.getSnapshot(),theme:currentTheme,disposed:stopped});
  function listen(target,name,fn,options) {target.addEventListener(name,fn,options);listeners.push(()=>target.removeEventListener(name,fn,options));}
  function refreshSelection() {
    const selected=selection.getSnapshot();
    input.value=selected.ph.toFixed(1);canvas.setAttribute('aria-valuenow',String(selected.ph));
    canvas.setAttribute('aria-valuetext',`pH ${selected.ph.toFixed(1)}; molecular predictions unavailable`);
    readout.textContent=`Selected pH ${selected.ph.toFixed(1)} (sample ${selected.index} of 0–140). Molecular population values unavailable.`;
  }
  function refreshReference() {
    const ref=presentation.reference;
    gap.textContent=ref.gap ? `${ref.gap.label}. ${ref.gap.reason}` : '';
    const context=ref.context;
    detail.textContent=context ? `${context.label}. Compartment: ${context.compartment}; site: ${context.anatomical_site}. ${context.species}; ${context.population}; ${context.preparation}; ${context.condition}. ${context.temperature_note} Measurement: ${context.measurement_method}. Handling: ${context.specimen_handling}.`
      : 'Reference overlays are off until a context is selected.';
    body.replaceChildren();
    for(const overlay of ref.overlays) {
      const tr=element('tr'), confidence=overlay.confidenceLevel===null?'':`; ${overlay.confidenceLevel*100}% confidence level`;
      for(const text of [overlay.label,kindLabel(overlay)+confidence,rangeText(overlay),`${overlay.evidenceStatus}; ${(overlay.sourceIds??[]).join(', ') || 'user entered; no verified source'}`]) tr.append(element('td',text));
      body.append(tr);
    }
    if(!ref.overlays.length){const tr=element('tr'),td=element('td',ref.gap?.label??'No reference selected');td.colSpan=4;tr.append(td);body.append(tr);}
    limitations.replaceChildren();for(const text of context?.evidence_limitations??[])limitations.append(element('li',text));
    if(context?.reported_uncertainty && !context.reported_uncertainty.plotted)
      limitations.append(element('li',`Reported uncertainty ${context.reported_uncertainty.value} (${context.reported_uncertainty.kind}) is not plotted or reinterpreted as an interval.`));
    sources.replaceChildren();for(const source of ref.sources) sources.append(element('li',`${source.id}: ${source.title}. ${source.url} Inspected: ${source.inspected_scope}. Accessed: ${source.accessed_date}.`));
    rules.replaceChildren();for(const text of ref.interpretationRules)rules.append(element('li',text));
  }
  function refreshTheme() {
    const colors=THEMES[currentTheme];section.style.backgroundColor=colors.background;section.style.color=colors.text;
    chart.options.color=colors.text;chart.options.plugins.title.color=colors.text;
    chart.options.scales.x.ticks.color=colors.text;chart.options.scales.x.title.color=colors.text;
    chart.options.scales.x.grid.color=colors.grid;chart.options.scales.x.border.color=colors.grid;
  }
  function cleanupExport(record) {
    if(!record.chart) record.chart=Chart.getChart?.(record.canvas)??null;
    if(record.chart){record.chart.destroy();record.chart=null;}
    record.canvas.width=0;record.canvas.height=0;exports.delete(record);
  }
  function destroy() {
    if(stopped)return;stopped=true;
    unsubscribe();for(const remove of listeners.splice(0))remove();
    for(const record of [...exports]) {record.cancel?.();cleanupExport(record);}
    if(!chart)chart=Chart.getChart?.(canvas)??null;
    if(chart){chart.destroy();chart=null;}
    section.remove();mounts.delete(host);
  }
  try {
    host.append(section);chart=new Chart(canvas,chartConfig(state));
    if(!chart.ctx)throw new Error('A 2D canvas is required.');
    refreshSelection();refreshReference();refreshTheme();chart.update('none');
    listen(canvas,'keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();error.textContent='';selection.key(event.key);}});
    const pointer=event=>{
      if(!chart || event.isPrimary===false)return;
      const rect=canvas.getBoundingClientRect();if(rect.width<=0 || rect.height<=0)return;
      const x=(event.clientX-rect.left)*chart.width/rect.width,y=(event.clientY-rect.top)*chart.height/rect.height,area=chart.chartArea;
      if(x<area.left||x>area.right||y<area.top||y>area.bottom)return;
      error.textContent='';selection.pointerPh(chart.scales.x.getValueForPixel(x));
    };
    listen(canvas,'pointermove',pointer);listen(canvas,'pointerdown',event=>{canvas.focus({preventScroll:true});pointer(event);});
    listen(form,'submit',event=>{event.preventDefault();try{selection.enterPh(input.value);error.textContent='';refreshSelection();}catch(failure){error.textContent=failure.message;}});
    unsubscribe=selection.subscribe(()=>{if(!stopped){refreshSelection();chart.draw();}});
    const api=Object.freeze({
      getSnapshot:state,
      updateContext({contextId=null,comparison=null}={}) {
        live();const accepted=referencePresentation(capabilities,referenceView(ownedDataset,contextId,comparison));
        presentation=accepted;refreshSelection();refreshReference();chart.update('none');return state();
      },
      setTheme(theme){live();currentTheme=checkTheme(theme);refreshTheme();chart.update('none');return state();},
      async exportPng({width=1000,height=560,transparentBackground=false}={}) {
        if(typeof transparentBackground!=='boolean')throw new TypeError('transparentBackground must be a boolean.');
        live();exportSize(width,height);if(exports.size)throw new Error('A reference PNG export is already in progress.');
        const captured=Object.freeze({...state(),theme:'light'}),output=dom.createElement('canvas');output.width=width;output.height=height;
        const record={canvas:output,chart:null,cancel:null};exports.add(record);
        try {
          record.chart=new Chart(output,chartConfig(()=>captured,{responsive:false,exporting:true,transparentBackground}));
          if(!record.chart.ctx)throw new Error('A 2D export canvas is required.');
          record.chart.update('none');
          const blob=await new Promise((resolve,reject)=>{
            record.cancel=()=>reject(new DOMException('Reference chart was disposed during export.','AbortError'));
            output.toBlob(value=>value?resolve(value):reject(new Error('PNG encoding failed.')),'image/png');
          });
          live();if(blob.type!=='image/png'||blob.size>8388608)throw new Error('Reference PNG encoding exceeds its bound.');
          const stamped=await stampPng(blob);live();
          if(stamped.size>8388608)throw new Error('PNG metadata exceeds output bound.');
          return stamped;
        } finally {cleanupExport(record);}
      },destroy,
    });
    mounts.set(host,api);return api;
  } catch(error) {destroy();throw error;}
}
