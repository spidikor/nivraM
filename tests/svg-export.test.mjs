import test from 'node:test';
import assert from 'node:assert/strict';
import {svgGeometry,exportSvg} from '../public/modules/svg-export.mjs';
import {controlledDocument} from './svg-test-dom.mjs';
const NS='http://www.w3.org/2000/svg';
const options={width:1000,marginMode:'percent',margin:2};
const wrap=s=>`<svg xmlns="${NS}">${s}</svg>`;
// Source bond-0 is forbidden, but a generated 32-hex gradient ID may start in 0.
const FORBIDDEN_SOURCE_CONTENT=/<(?:image|text|script|metadata|foreignObject)|data-nivram|class=|graph|atom-|\bbond-0\b|selection|canvas/;
test('source-token leak assertion admits randomized local gradients but rejects source IDs',()=>{
  assert.doesNotMatch('<linearGradient id="nivram-bond-00000000000000000000000000000000-1">',FORBIDDEN_SOURCE_CONTENT);
  assert.match('<path id="bond-0">',FORBIDDEN_SOURCE_CONTENT);
  assert.match('<path class="bond-0 atom-0">',FORBIDDEN_SOURCE_CONTENT);
});
const path=(d='M 0,0 L 50,0',color='#000000',classes='bond-0 atom-0 atom-1')=>`<path class="${classes}" d="${d}" style="fill:none;stroke:${color};stroke-width:4;stroke-linecap:round;stroke-linejoin:round"/>`;
const source=wrap(path()+path('M 50,0 L 100,0','#FF0000')+'<path class="atom-1" d="M 99,2 L 101,2 L 101,4 Z" fill="#FF0000"/>');
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-9,`${a} != ${b}`);
test('nominal SVG dimensions preserve aspect ratio and symmetric bounded margins',()=>{
  for(const width of [1,333,1000,9999])for(const ratio of [.01,.5,1,2]){
    const b={x:-123,y:456,width:100,height:100*ratio},o={width,marginMode:'percent',margin:width===1?0:2};const g=svgGeometry(b,o),v=g.viewBox;
    assert.equal(g.width,width);assert.equal(g.height,Math.ceil(b.height*g.scale)+2*g.padding);
    near((b.x-v.x)*g.scale,g.padding);near(v.width/v.height,g.width/g.height);
    near(b.y+b.height/2,v.y+v.height/2);assert.ok((b.y-v.y)*g.scale>=g.padding-1e-9);
  }
});
test('SVG does not inherit raster memory/pixel caps',()=>assert.equal(svgGeometry({x:0,y:0,width:1,height:2},{...options,width:9999}).height,19598));
test('invalid bounds/options and unsupported initial heights reject',()=>{
  for(const key of ['x','y','width','height'])for(const value of [NaN,Infinity,'1'])assert.throws(()=>svgGeometry({...{x:0,y:0,width:1,height:1},[key]:value},options));
  for(const value of [0,-1])assert.throws(()=>svgGeometry({x:0,y:0,width:value,height:1},options));
  assert.throws(()=>svgGeometry({x:0,y:0,width:1,height:1000},options),/height/);
  assert.throws(()=>svgGeometry({x:0,y:0,width:1,height:1},{...options,margin:50}),/margins/);
});
test('production sanitizer and paint produce standalone vector SVG with no embedded graph or UI IDs',async()=>{
  const doc=controlledDocument(),result=exportSvg(source,options,{ownerDocument:doc,paintStyle:{bondWidth:4}}),text=await result.blob.text();
  assert.equal(result.blob.type,'image/svg+xml');assert.equal(result.width,1000);assert.equal(result.padding,20);
  assert.match(text,/<svg[^>]+xmlns="http:\/\/www.w3.org\/2000\/svg"/);assert.match(text,/<rect[^>]+fill="#FFFFFF"/);
  assert.match(text,/<linearGradient /);assert.equal((text.match(/<stop /g)||[]).length,4);
  const id=/linearGradient id="([^"]+)"/.exec(text)[1];assert.equal((text.match(new RegExp(`url\\(#${id}\\)`,'g'))||[]).length,2);
  assert.match(text,/d="M 0,0 L 50,0"/);assert.match(text,/d="M 50,0 L 100,0"/);assert.match(text,/stop-color="#000000"/);assert.match(text,/stop-color="#FF0000"/);
  assert.doesNotMatch(text,FORBIDDEN_SOURCE_CONTENT);
  assert.equal(doc.body.children.length,0);assert.deepEqual(doc.calls,{parsed:1,measured:1,serialized:1});
});
test('transparent output has no background rect and no source geometry is changed',async()=>{
  const doc=controlledDocument(),result=exportSvg(source,{...options,transparentBackground:true},{ownerDocument:doc,paintStyle:{bondWidth:4}});
  const text=await result.blob.text();assert.doesNotMatch(text,/<rect/);assert.equal(result.transparentBackground,true);assert.match(text,/d="M 99,2 L 101,2 L 101,4 Z"/);
});
test('standalone serialization rejects active content, external paint, text and unsupported transforms',()=>{
  for(const content of ['<script/>','<foreignObject/>','<image href="https://evil.invalid/a"/>','<path onload="x"/>','<path style="fill:url(https://evil.invalid/a)"/>','<path style="fill:var(--secret)"/>','<path fill="currentColor"/>','<text>O</text>','<g transform="scale(2)">'+path()+'</g>','<svg>'+path()+'</svg>','<defs/>']){
    const doc=controlledDocument();assert.throws(()=>exportSvg(wrap(content),options,{ownerDocument:doc}));assert.equal(doc.body.children.length,0);
  }
  for(const raw of ['<!DOCTYPE svg><svg/>','<svg><unclosed>','not xml','x'.repeat(4_000_001)])assert.throws(()=>exportSvg(raw,options,{ownerDocument:controlledDocument()}));
});
test('empty source rejects and stage is removed on measurement, bounds or serialization failure',()=>{
  assert.throws(()=>exportSvg(wrap(''),options,{ownerDocument:controlledDocument()}),/Draw or import/);
  for(const config of [{measureFailure:true},{serializeFailure:true},{bounds:{x:NaN,y:0,width:1,height:1}},{bounds:{x:0,y:0,width:1,height:1e9}}]){
    const doc=controlledDocument(config);assert.throws(()=>exportSvg(source,options,{ownerDocument:doc}));assert.equal(doc.body.children.length,0);
  }
});
test('cancelled export does not parse, measure or serialize',()=>{
  const controller=new AbortController();controller.abort();const doc=controlledDocument();assert.throws(()=>exportSvg(source,options,{signal:controller.signal,ownerDocument:doc}),{name:'AbortError'});assert.deepEqual(doc.calls,{parsed:0,measured:0,serialized:0});
});
test('filled wedge gradient, hash bars, curved outline and radical ellipse retain exact geometry',async()=>{
  const a='M 0,0 L 20,-2 L 20,2 Z',b='M 20,-2 L 40,-4 L 40,4 L 20,2 Z',hash='M 45,-5 L 45,5',glyph='M 50,0 Q 51,2 52,0 Z';
  const filled=(d,c)=>`<path class="bond-0 atom-0 atom-1" d="${d}" style="fill:${c};stroke:${c};stroke-width:1;stroke-linejoin:round"/>`;
  const raw=wrap(filled(a,'#000000')+filled(b,'#FF0000')+path(hash,'#333399','bond-1 atom-1 atom-2')+`<path class="atom-2" d="${glyph}" fill="#333399"/><ellipse cx="54" cy="4" rx="1" ry="1" fill="#333399"/>`);
  const text=await exportSvg(raw,options,{ownerDocument:controlledDocument(),paintStyle:{bondWidth:4}}).blob.text();
  for(const d of [a,b,hash,glyph])assert.ok(text.includes(`d="${d}"`));
  assert.equal((text.match(/<linearGradient /g)||[]).length,1);assert.equal((text.match(/url\(#/g)||[]).length,4);assert.match(text,/<ellipse cx="54" cy="4" rx="1" ry="1"/);
});
test('miter strokes reserve their extension without modifying paths',async()=>{
  const raw=wrap('<path d="M 0,0 L 100,50" style="fill:none;stroke:#000000;stroke-width:4;stroke-linejoin:miter;stroke-miterlimit:10"/>');
  const result=exportSvg(raw,{width:1000,margin:0},{ownerDocument:controlledDocument()});const text=await result.blob.text();
  assert.match(text,/viewBox="-21 -21/);assert.match(text,/d="M 0,0 L 100,50"/);
});

test('diagonal square caps fit inside the output even at zero margins',async()=>{
  const raw=wrap('<path d="M 0,0 L 100,100" style="fill:none;stroke:#000000;stroke-width:5;stroke-linejoin:round;stroke-linecap:square"/>');
  const text=await exportSvg(raw,{width:9999,margin:0,transparentBackground:true},{ownerDocument:controlledDocument({bounds:{x:0,y:0,width:100,height:100}})}).blob.text();
  const [x,y,w,h]=/viewBox="([^"]+)"/.exec(text)[1].split(' ').map(Number),extension=5/Math.sqrt(2);
  assert.ok(x<=-extension&&y<=-extension);assert.ok(x+w>=100+extension&&y+h>=100+extension);
});
test('empty or negative raw geometry cannot become a valid box by adding stroke guard',()=>{
  for(const bounds of [{x:0,y:0,width:0,height:0},{x:0,y:0,width:-1,height:10},{x:0,y:0,width:10,height:-1}]){
    const doc=controlledDocument({bounds});assert.throws(()=>exportSvg(source,options,{ownerDocument:doc}),/geometry/);assert.equal(doc.body.children.length,0);
  }
  for(const bounds of [{x:0,y:0,width:0,height:100},{x:0,y:0,width:100,height:0}])assert.ok(exportSvg(source,options,{ownerDocument:controlledDocument({bounds})}).blob.size>0);
});
