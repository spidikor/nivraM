/** Actual source contracts, not browser layout or accessibility qualification. */
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const read=p=>readFileSync(new URL('../'+p,import.meta.url),'utf8');
const html=read('public/index.html'),css=read('public/style.css'),module=read('public/modules/stereo-guidance.mjs'),app=read('public/app.mjs');
const stack=[],voidTags=new Set(['meta','link','input','br','hr','img','area','base','col','embed','param','source','track','wbr']);let region=null,status=null;
for(const match of html.replace(/<!--[\s\S]*?-->/g,'').matchAll(/<\/?([A-Za-z][\w:-]*)\b[^>]*>/g)){
 const tag=match[1].toLowerCase(),text=match[0];
 if(text.startsWith('</')){assert.equal(stack.pop()?.tag,tag);continue;}
 if(text.includes('id="structure-status"'))status={text,ancestors:[...stack]};
 if(text.includes('id="stereo-guidance-region"')){region={text,ancestors:[...stack]};break;}
 if(!voidTags.has(tag)&&!text.endsWith('/>'))stack.push({tag,text});
}
assert(region&&status);assert(region.ancestors.some(a=>a.text.includes('id="inspector"')));assert(!region.ancestors.some(a=>a.text.includes('class="editor-area"')));assert.match(region.text,/<div\b/);assert.match(region.text,/\bhidden\b/);
assert(status.ancestors.some(a=>a.text.includes('class="editor-area"')));
for(const host of [region,status])assert(!host.ancestors.some(a=>a.tag==='svg'||a.text.includes('id="canvas-wrap"')));
assert.equal((html.match(/id="stereo-guidance-region"/g)||[]).length,1);
const originalStatus=html.match(/<div id="structure-status" class="structure-status">([\s\S]*?)<\/div>/)[1];
for(const id of ['counts','formula','molecular-weight','request-status'])assert(originalStatus.includes(`id="${id}"`));
const rules=selector=>[...css.replace(/\/\*[\s\S]*?\*\//g,'').matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(m=>m[1].trim()===selector).map(m=>m[2]);
const [r]=rules('.stereo-guidance-region');assert(r.includes('overflow:auto'));assert(r.includes('overflow-wrap:anywhere'));assert(r.includes('max-height:10rem'));assert(r.includes('min-width:0'));assert(!r.includes('7rem'));assert(!/position:(absolute|fixed)/.test(r));assert.equal(rules('.stereo-guidance-region[hidden]')[0],'display:none');
assert(rules('.structure-status').some(r=>r==='position:relative'));
assert.equal(rules('.structure-status[data-stereo-guidance="active"]>span')[0],'visibility:hidden');
const [button]=rules('.stereo-guidance-dismiss');for(const contract of ['position:absolute','inset:0','width:100%','height:100%','min-width:0','min-height:0','white-space:normal'])assert(button.includes(contract));
assert.equal(rules('.stereo-guidance-dismiss[hidden]')[0],'display:none');assert.match(css,/@media\(max-width:560px\)\{\.stereo-guidance-dismiss\{font-size:0;line-height:0\}\.stereo-guidance-dismiss::after\{content:"Dismiss flags";font-size:11px;line-height:1.1\}\}/);
assert(module.includes("dismissAll.setAttribute('aria-label','Dismiss stereochemical flags')"));assert(module.includes('region.append(note)'));assert(module.includes('status.append(dismissAll)'));assert(!module.includes('canvas.parentNode.append'));
assert(module.includes("status.removeAttribute('data-stereo-guidance')"));assert(!/status\.(textContent|innerHTML|style)\s*=|status\.replaceChildren\(/.test(module));assert(!/scrollIntoView|scrollTop\s*=|scrollTo\(/.test(module));
// Existing responsive host sizing and source/view invalidation remain in force.
assert.match(css,/\.inspector\{[^}]*overflow:auto/);assert.match(css,/\.editor-area,\.tools\{height:420px;min-height:0\}/);assert.match(css,/\.inspector\{grid-column:1\/-1;display:grid/);assert(!/\.inspector\{[^}]*display:none/.test(css));
assert.match(app,/function setView\(v\)\{stereoGuidance\?\.clear\(\)/);assert.match(app,/new ResizeObserver\(/);assert.match(app,/else fit\(\)/);assert.match(app,/\}\)\.observe\(svg\)/);
console.log('PASS V3 actual source contracts: transient inspector outside canvas; status footprint overlay preserves span layout/content; full accessible label with compact narrow caption; no forced scrolling; existing responsive sizing/resize invalidation unchanged. No browser visual acceptance.');
