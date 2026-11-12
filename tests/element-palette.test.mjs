import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {once} from 'node:events';
import {testBrowser} from './browser-session.mjs';
import {COMMON_ELEMENTS,MORE_ELEMENTS} from '../public/modules/elements.mjs';

const files=new Map([
  ['/modules/element-palette.mjs',new URL('../public/modules/element-palette.mjs',import.meta.url)],
  ['/modules/elements.mjs',new URL('../public/modules/elements.mjs',import.meta.url)],
  ['/style.css',new URL('../public/style.css',import.meta.url)],
  ['/canvas-menu.css',new URL('../public/modules/canvas-menu.css',import.meta.url)],
]);
const html=`<!doctype html><html><head><link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/canvas-menu.css"></head><body>
<main><button id="before">Before palette</button><div id="elements"></div><button id="after">After palette</button>
<input id="text" aria-label="Text input"><div class="canvas-wrap" style="height:180px"><svg id="canvas" viewBox="0 0 300 180"><g id="drawing">
<path class="bond-0 atom-0 atom-1" style="fill:none;stroke:#000000;stroke-width:2" d="M 20 50 L 80 50"/>
<text class="atom-0" x="15" y="50" style="fill:#000000;font-size:20px">C</text>
<text class="atom-1" x="80" y="50" style="fill:#333399;font-size:20px">N</text>
</g></svg></div><div class="canvas-context-menu" hidden><button>Context action</button></div></main>
<script type="module">
import {initializeElementPalette} from '/modules/element-palette.mjs';
window.fixture={selected:'C',chosen:[]};
window.palette=initializeElementPalette({document,parent:document.getElementById('elements'),
getSelected:()=>fixture.selected,onSelect:symbol=>{fixture.selected=symbol;fixture.chosen.push(symbol);}});
</script></body></html>`;

async function fixture(t){
  const server=createServer(async(request,response)=>{
    if(request.url==='/'){response.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});response.end(html);return;}
    const file=files.get(request.url);
    if(!file){response.writeHead(404);response.end();return;}
    try{response.writeHead(200,{'Content-Type':request.url.endsWith('.css')?'text/css':'text/javascript'});response.end(await readFile(file));}
    catch{response.writeHead(500);response.end();}
  });
  server.listen(0,'127.0.0.1');await once(server,'listening');
  t.after(async()=>{server.closeAllConnections();await new Promise((resolve,reject)=>server.close(error=>error?reject(error):resolve()));});
  const browser=await testBrowser(t),page=await browser.newPage({viewport:{width:1366,height:768}});
  const origin=`http://127.0.0.1:${server.address().port}`,errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>route.request().url().startsWith(origin)?route.continue():route.abort());
  await page.goto(origin);await page.waitForFunction(()=>window.palette);
  t.after(()=>assert.deepEqual(errors,[]));return page;
}

test('every curated element has a medicinal example and authoritative source in the evidence table',async()=>{
  const doc=await readFile(new URL('../docs/medicinal-elements.md',import.meta.url),'utf8');
  const supported=[];
  for(const line of doc.split('\n')){
    const row=line.match(/^\| ([A-Z][a-z]?(?:, [A-Z][a-z]?)*) \| ([^|]+) \| (.+) \|$/);
    if(!row)continue;
    assert.ok(row[2].trim().length>5);
    assert.match(row[3],/https:\/\/(?:www\.)?dailymed\.nlm\.nih\.gov\//);
    supported.push(...row[1].split(', '));
  }
  assert.deepEqual(supported.sort(),[...COMMON_ELEMENTS,...MORE_ELEMENTS].sort());
  assert.match(doc,/metastable nuclear state in Tc-99m/);
});

test('curated palette disclosure supports selection, focus, dismissal and external refresh',{timeout:60000},async t=>{
  const page=await fixture(t);
  const active=()=>page.evaluate(()=>document.activeElement?.id||document.activeElement?.dataset.element);
  const expanded=()=>page.getAttribute('#more-elements-button','aria-expanded');
  assert.equal(await page.locator('[data-element]').count(),32);
  assert.equal(await page.locator('[data-element]:visible').count(),10);
  assert.equal(await page.textContent('#more-elements-button'),'More ▾');
  assert.equal(await expanded(),'false');
  await page.click('[data-element="H"]');assert.equal(await page.getAttribute('[data-element="H"]','aria-pressed'),'true');
  await page.click('#more-elements-button');assert.equal(await expanded(),'true');
  assert.equal(await page.textContent('#more-elements-button'),'More ▴');assert.equal(await active(),'Li');
  await page.keyboard.press('ArrowRight');assert.equal(await active(),'B');
  await page.keyboard.press('ArrowDown');assert.equal(await active(),'Na');
  await page.keyboard.press('ArrowUp');assert.equal(await active(),'B');
  await page.keyboard.press('End');assert.equal(await active(),'Lu');
  await page.keyboard.press('Home');assert.equal(await active(),'Li');
  await page.keyboard.press('ArrowLeft');assert.equal(await active(),'Lu');
  await page.keyboard.press('Enter');assert.equal(await expanded(),'false');assert.equal(await active(),'more-elements-button');
  assert.deepEqual(await page.evaluate(()=>fixture.chosen),['H','Lu']);
  assert.equal(await page.getAttribute('[data-element="Lu"]','aria-pressed'),'true');
  await page.keyboard.press('ArrowDown');assert.equal(await active(),'Lu');assert.equal(await expanded(),'true');
  await page.keyboard.press('Escape');assert.equal(await expanded(),'false');assert.equal(await active(),'more-elements-button');
  await page.keyboard.press('Enter');await page.keyboard.press('Tab');
  assert.equal(await active(),'after');assert.equal(await expanded(),'false');
  await page.click('#more-elements-button');await page.keyboard.press('Shift+Tab');
  assert.equal(await active(),'I');assert.equal(await expanded(),'false');
  await page.click('#more-elements-button');await page.click('#after');
  assert.equal(await active(),'after');assert.equal(await expanded(),'false');
  await page.click('#more-elements-button');await page.locator('#text').focus();
  assert.equal(await expanded(),'false');assert.equal(await active(),'text');
  await page.evaluate(()=>{fixture.selected='Se';palette.refresh();});
  await page.click('#more-elements-button');assert.equal(await active(),'Se');
  assert.equal(await page.getAttribute('[data-element="Se"]','aria-pressed'),'true');
  await page.evaluate(()=>{fixture.selected='U';palette.refresh();palette.close();});
  assert.equal(await page.locator('[data-element][aria-pressed="true"]').count(),0);
  assert.equal(await page.locator('[data-element="U"]').count(),0);
  await page.click('#more-elements-button');assert.equal(await active(),'Li');
  await page.evaluate(()=>window.dispatchEvent(new Event('nivram-close-review')));
  assert.equal(await expanded(),'false');assert.equal(await page.evaluate(()=>palette.isOpen()),false);
});

test('dark surfaces are neutral and darker, glyph paint is retained, halo is display-only',{timeout:60000},async t=>{
  const page=await fixture(t);
  const before=await page.locator('#drawing').evaluate(node=>node.outerHTML);
  await page.evaluate(()=>document.documentElement.dataset.theme='dark');
  await page.click('#more-elements-button');
  const styles=await page.evaluate(()=>{
    const read=selector=>{const s=getComputedStyle(document.querySelector(selector));return {background:s.backgroundColor,color:s.color,stroke:s.stroke,fill:s.fill,filter:s.filter};};
    const drawing=document.querySelector('#drawing'),clone=drawing.cloneNode(true);
    const stage=document.createElementNS('http://www.w3.org/2000/svg','svg');stage.append(clone);document.body.append(stage);
    const exportClone={bond:getComputedStyle(clone.querySelector('.bond-0')).stroke,glyphFilter:getComputedStyle(clone.querySelector('text')).filter};stage.remove();
    return {page:read(':root'),canvas:read('.canvas-wrap'),options:read('#more-elements-options'),button:read('[data-element="Li"]'),
      carbon:read('text.atom-0'),nitrogen:read('text.atom-1'),bond:read('.bond-0'),exportClone};
  });
  assert.equal(styles.page.background,'rgb(13, 13, 13)');assert.equal(styles.canvas.background,'rgb(16, 16, 16)');
  assert.equal(styles.options.background,'rgb(32, 32, 32)');assert.equal(styles.button.background,'rgb(38, 38, 38)');
  assert.equal(styles.bond.stroke,'rgb(214, 214, 214)');
  assert.equal(styles.carbon.fill,'rgb(0, 0, 0)');assert.equal(styles.nitrogen.fill,'rgb(51, 51, 153)');
  assert.match(styles.carbon.filter,/drop-shadow/);assert.match(styles.nitrogen.filter,/drop-shadow/);
  assert.deepEqual(styles.exportClone,{bond:'rgb(0, 0, 0)',glyphFilter:'none'});
  assert.equal(await page.locator('#drawing').evaluate(node=>node.outerHTML),before);
  const luminance=value=>value.match(/\d+/g).slice(0,3).map(Number).map(n=>{n/=255;return n<=.04045?n/12.92:((n+.055)/1.055)**2.4;}).reduce((sum,n,i)=>sum+n*[.2126,.7152,.0722][i],0);
  const contrast=(a,b)=>(Math.max(luminance(a),luminance(b))+.05)/(Math.min(luminance(a),luminance(b))+.05);
  assert.ok(contrast(styles.button.color,styles.button.background)>=4.5);
  await page.keyboard.press('Escape');await page.keyboard.press('Tab');
  assert.equal(await page.locator('#after').evaluate(node=>getComputedStyle(node).outlineColor),'rgb(242, 242, 242)');
});
