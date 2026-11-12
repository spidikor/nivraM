import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor} from './editor-browser-fixture.mjs';

const style=color=>`fill:none;fill-rule:evenodd;stroke:${color};stroke-width:4px;stroke-linecap:round;stroke-linejoin:round;stroke-opacity:1`;
const path=(d,color,extra='')=>`<path class="bond-0 atom-0 atom-1" d="${d}" style="${style(color)}${extra}"/>`;
const svg=body=>`<svg xmlns="http://www.w3.org/2000/svg">${body}</svg>`;

test('trusted bond gradients preserve geometry, isolate references and keep incoming SVG resource rejection',{timeout:120000},async t=>{
  const page=await productionEditor(t);
  const horizontal=svg(path('M 0,0 L 20,0','#000000')+path('M 20,0 L 40,0','#FF0000'));
  const basic=await page.evaluate(async source=>{
    const {sanitizeSvg}=await import('/modules/document.mjs');
    const fragment=sanitizeSvg(source),gradient=fragment.querySelector('linearGradient');
    return {count:fragment.querySelectorAll('linearGradient').length,paths:[...fragment.querySelectorAll('path')].map(node=>node.getAttribute('d')),
      gradient:gradient?.outerHTML??null,stops:gradient?[...gradient.children].map(node=>({offset:Number(node.getAttribute('offset')),color:node.getAttribute('stop-color')})):[]};
  },horizontal);
  assert.equal(basic.count,1,'Sanitized ordinary two-color bond halves need a programmatically generated vector gradient.');
  assert.deepEqual(basic.paths,['M 0,0 L 20,0','M 20,0 L 40,0']);
  assert.deepEqual(basic.stops,[{offset:0,color:'#000000'},{offset:0.45,color:'#000000'},{offset:0.55,color:'#FF0000'},{offset:1,color:'#FF0000'}]);

  const cases=[
    {name:'backwards vertical',source:svg(path('M 0,20 L 0,40','#FF0000')+path('M 0,20 L 0,0','#000000')),count:1,paint:{x1:0,y1:40,x2:0,y2:0,colors:['#FF0000','#FF0000','#000000','#000000']}},
    {name:'double bond',source:svg(path('M 0,0 L 20,0','#000000')+path('M 20,0 L 40,0','#FF0000')+path('M 0,8 L 20,8','#000000')+path('M 20,8 L 40,8','#FF0000')),count:2},
    {name:'same color',source:svg(path('M 0,0 L 20,0','#FF0000')+path('M 20,0 L 40,0','#FF0000')),count:0},
    {name:'filled wedge',source:svg('<path class="bond-0 atom-0 atom-1" d="M 0,0 L 20,0 L 20,4 Z" style="fill:#000000;stroke:#000000;stroke-width:4px"/>'+path('M 20,0 L 40,0','#FF0000')),count:0},
    {name:'dashed',source:svg(path('M 0,0 L 20,0','#000000',';stroke-dasharray:2,2')+path('M 20,0 L 40,0','#FF0000',';stroke-dasharray:2,2')),count:0},
    {name:'noncollinear',source:svg(path('M 0,0 L 20,0','#000000')+path('M 20,0 L 20,20','#FF0000')),count:0},
    {name:'different classes',source:horizontal.replace('M 20,0 L 40,0','M 20,0 L 40,0').replace('class="bond-0 atom-0 atom-1" d="M 20','class="bond-1 atom-0 atom-1" d="M 20'),count:0},
    {name:'transparent stroke',source:horizontal.replaceAll('stroke-opacity:1','stroke-opacity:0.5'),count:0},
    {name:'inherited transform',source:svg(`<g transform="translate(5 5)">${path('M 0,0 L 20,0','#000000')+path('M 20,0 L 40,0','#FF0000')}</g>`),count:0},
    {name:'zero length',source:svg(path('M 20,0 L 20,0','#000000')+path('M 20,0 L 40,0','#FF0000')),count:0},
    {name:'short unequal halves',source:svg(path('M 0,0 L 0.25,0','#000000')+path('M 0.25,0 L 1,0','#FF0000')),count:1},
  ];
  for(const item of cases){
    const result=await page.evaluate(async source=>{
      const {sanitizeSvg}=await import('/modules/document.mjs');
      const before=new DOMParser().parseFromString(source,'image/svg+xml');
      const fragment=sanitizeSvg(source);
      return {count:fragment.querySelectorAll('linearGradient').length,paths:[...fragment.querySelectorAll('path')].map(node=>node.getAttribute('d')),
        originalPaths:[...before.querySelectorAll('path')].map(node=>node.getAttribute('d')),
        offsets:[...fragment.querySelectorAll('linearGradient')].map(node=>[...node.children].map(stop=>Number(stop.getAttribute('offset')))),
        paints:[...fragment.querySelectorAll('linearGradient')].map(node=>({x1:Number(node.getAttribute('x1')),y1:Number(node.getAttribute('y1')),x2:Number(node.getAttribute('x2')),y2:Number(node.getAttribute('y2')),colors:[...node.children].map(stop=>stop.getAttribute('stop-color'))}))};
    },item.source);
    assert.equal(result.count,item.count,item.name);assert.deepEqual(result.paths,result.originalPaths,item.name);
    if(item.paint)assert.deepEqual(result.paints[0],item.paint,'Reversed paths retain their color at each original outer endpoint.');
    for(const offsets of result.offsets){assert.equal(offsets[0],0);assert.equal(offsets.at(-1),1);assert.ok(offsets.every((offset,i)=>Number.isFinite(offset)&&offset>=0&&offset<=1&&(!i||offset>=offsets[i-1])),item.name);}
  }
  const isolated=await page.evaluate(async source=>{
    const {sanitizeSvg}=await import('/modules/document.mjs'),{rebaseBondPaint}=await import('/modules/bond-paint.mjs');
    const make=()=>{const root=document.createElementNS('http://www.w3.org/2000/svg','svg');root.append(sanitizeSvg(source));document.body.append(root);return root;};
    const first=make(),second=make(),before=first.outerHTML,clone=first.cloneNode(true);
    rebaseBondPaint(clone);document.body.append(clone);
    try{
      const nodes=[first,second,clone],ids=nodes.map(root=>root.querySelector('linearGradient').id);
      const own=nodes.every(root=>[...root.querySelectorAll('path')].every(node=>{
        const match=node.style.stroke.match(/^url\(["']?#([^"')]+)["']?\)$/);return match&&root.querySelector('linearGradient').id===match[1];
      }));
      const reprocessed=sanitizeSvg(source.replace('class="bond-0','id="'+ids[0]+'" class="bond-0'));
      return {ids,own,originalUnchanged:first.outerHTML===before,reservedIdsAvoided:![...reprocessed.querySelectorAll('linearGradient')].some(node=>ids.includes(node.id))};
    }finally{first.remove();second.remove();clone.remove();}
  },horizontal);
  assert.equal(new Set(isolated.ids).size,3);assert.equal(isolated.own,true);assert.equal(isolated.originalUnchanged,true);assert.equal(isolated.reservedIdsAvoided,true);
  const rejected=await page.evaluate(async()=>{
    const {sanitizeSvg}=await import('/modules/document.mjs');
    const bodies=['<defs><linearGradient id="x"><stop offset="0" stop-color="#000000"/></linearGradient></defs>',
      '<linearGradient id="x"/>','<stop offset="0" stop-color="#000000"/>','<path d="M0 0L1 1" style="stroke:url(#x)"/>',
      '<path d="M0 0L1 1" stroke="url(https://example.invalid/paint.svg#x)"/>','<path href="#x" d="M0 0L1 1"/>',
      '<g xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="#x"/>'];
    return bodies.map(body=>{try{sanitizeSvg(`<svg xmlns="http://www.w3.org/2000/svg">${body}</svg>`);return false;}catch{return true;}});
  });
  assert.ok(rejected.every(Boolean),'All incoming gradients and references remain rejected.');
});
