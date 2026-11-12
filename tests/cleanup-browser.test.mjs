import {boundRenderFixture} from './bound-render-fixture.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {testBrowser} from './browser-session.mjs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

// Real editor integration; the local HTTP boundary supplies deterministic layouts.
const publicPath=fileURLToPath(new URL('../public/',import.meta.url));
const style={bondWidth:4.2,labelSize:36,bondSpacing:0.14,color:true};
const initial={schema:'nivram/1',graph:{atoms:[
  {id:'a0',symbol:'C',x:0,y:0,isotope:13},
  {id:'a1',symbol:'O',x:1.5,y:0},
  {id:'a2',symbol:'N',x:3,y:1},
],bonds:[{id:'b0',begin:'a0',end:'a1',order:1},{id:'b1',begin:'a1',end:'a2',order:1}]},style,export:{width:1234,marginMode:'pixels',margin:17},analysis:[{kind:'test',status:'unavailable'}]};
function resultFor(graph,appearance){
  const positions=graph.atoms.map(a=>({id:a.id,x:100+a.x*50,y:150-a.y*50}));
  const byId=new Map(positions.map(p=>[p.id,p]));
  const artwork=graph.bonds.map(b=>{const a=byId.get(b.begin),c=byId.get(b.end);return `<path d="M${a.x} ${a.y}L${c.x} ${c.y}" stroke="#000" stroke-width="3" fill="none"/>`;}).join('');
  return {graph,scene:{svg:`<svg xmlns="http://www.w3.org/2000/svg">${artwork}</svg>`,positions,transform:{x0:100,y0:150,xScale:50,yScale:-50},width:800,height:500,formula:'fixture',smiles:graph.atoms.length?'[13CH3]ON':'',mass:graph.atoms.length?48:0,style:appearance}};
}
async function fixture(t){
  const fault={alter:null,defer:false,started:null,release:null},requests=[];
  const server=createServer(async(req,res)=>{
    try{
      res.setHeader('Content-Type','application/json');
      if(req.url==='/api/status')return res.end(JSON.stringify({version:'test',rdkit:'fixture-version'}));
      if(req.url.startsWith('/api/')){
        if(!['/api/render','/api/clean'].includes(req.url)){res.statusCode=503;return res.end(JSON.stringify({error:'Unavailable in cleanup fixture.'}));}
        const chunks=[];for await(const chunk of req)chunks.push(chunk);
        const data=JSON.parse(Buffer.concat(chunks).toString()),graph=structuredClone(data.graph);
        if(req.url==='/api/clean'){
          requests.push(structuredClone(data));
          const chosen=new Set(data.selected.length?data.selected:graph.atoms.map(a=>a.id));
          for(const a of graph.atoms)if(chosen.has(a.id)){a.x+=.4;a.y+=.25;}
          fault.alter?.(graph);fault.alter=null;
          if(fault.defer){fault.defer=false;await new Promise(resolve=>{fault.release=resolve;fault.started?.();});}
        }
        return res.end(JSON.stringify(boundRenderFixture(req.url.slice(5),data,resultFor(graph,data.style))));
      }
      const relative=req.url==='/'?'index.html':req.url.slice(1),filename=path.resolve(publicPath,relative);
      if(!filename.startsWith(publicPath)){res.statusCode=404;return res.end();}
      res.setHeader('Content-Type',relative.endsWith('.mjs')?'text/javascript':relative.endsWith('.css')?'text/css':'text/html');
      res.end(await readFile(filename));
    }catch(error){res.statusCode=500;res.end(JSON.stringify({error:error.message}));}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{fault.release?.();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
  const browser=await testBrowser(t);
  const page=await browser.newPage({viewport:{width:1366,height:768}}),url=`http://127.0.0.1:${server.address().port}/`,external=new Set();
  await page.route('**/*',route=>{
    const target=route.request().url();
    if(target.startsWith(url)||target.startsWith('blob:')||target.startsWith('data:'))return route.continue();
    external.add(new URL(target).origin);return route.abort();
  });
  t.after(()=>{if(external.size)t.diagnostic(`Focused fixture blocked external browser injection: ${[...external].join(', ')}. This is not a privacy acceptance check.`);});
  await page.goto(url);await page.evaluate(()=>window.nivramDocument.ready);
  assert.equal(await page.evaluate(project=>window.nivramDocument.openProject(project),initial),true);
  return {page,fault,requests};
}
const ready=page=>page.waitForFunction(()=>document.querySelector('#request-status').textContent==='Ready');
const live=page=>page.evaluate(()=>({snapshot:window.nivramDocument.getSnapshot(),artwork:document.querySelector('#drawing').innerHTML,selection:document.querySelector('#selection').innerHTML,view:document.querySelector('#canvas').getAttribute('viewBox'),undo:document.querySelector('#undo-button').disabled,redo:document.querySelector('#redo-button').disabled}));
async function selectAtom(page,atom){
  const screen=await page.evaluate(atom=>{const svg=document.querySelector('#canvas'),point=svg.createSVGPoint();point.x=100+atom.x*50;point.y=150-atom.y*50;const out=point.matrixTransform(svg.getScreenCTM());return {x:out.x,y:out.y};},atom);
  await page.mouse.click(screen.x,screen.y);
}

test('cleanup previews preserve live state, apply once, cancel safely, and reject stale or unsafe candidates',async t=>{
  const {page,fault,requests}=await fixture(t);let before=await live(page);
  await page.click('#clean-button');await ready(page);
  assert.deepEqual(await live(page),before,'Cleanup must not commit before the user selects Apply.');
  assert.equal(await page.locator('#cleanup-dialog').evaluate(d=>d.open),true);
  assert.match(await page.textContent('#cleanup-backend'),/RDKit.*fixture-version/);
  assert.match(await page.textContent('#cleanup-limitations'),/Marvin.*not.*established/i);
  assert.equal(await page.locator('#cleanup-current path').count(),2);assert.equal(await page.locator('#cleanup-proposed path').count(),2);
  assert.notEqual(await page.locator('#cleanup-current').innerHTML(),await page.locator('#cleanup-proposed').innerHTML());
  await page.click('#cancel-cleanup');assert.deepEqual(await live(page),before);
  await page.click('#clean-button');await ready(page);await page.click('#apply-cleanup');
  const applied=await live(page);assert.equal(applied.snapshot.revision,before.snapshot.revision+1);
  assert.deepEqual(applied.snapshot.document.analysis,before.snapshot.document.analysis);assert.deepEqual(applied.snapshot.document.export,before.snapshot.document.export);
  await page.click('#undo-button');await ready(page);assert.deepEqual((await live(page)).snapshot.document,before.snapshot.document);
  await page.click('#redo-button');await ready(page);assert.deepEqual((await live(page)).snapshot.document,applied.snapshot.document);
  await selectAtom(page,applied.snapshot.document.graph.atoms[1]);before=await live(page);
  await page.keyboard.press('Control+2');await ready(page);assert.deepEqual(requests.at(-1).selected,['a1']);
  await page.keyboard.press('Escape');assert.deepEqual(await live(page),before,'Escape from the preview must retain selection, view and document.');
  await page.click('#clean-button');await ready(page);await page.click('#apply-cleanup');
  const selectedResult=await live(page);
  for(const index of [0,2])assert.deepEqual(selectedResult.snapshot.document.graph.atoms[index],before.snapshot.document.graph.atoms[index]);
  assert.notDeepEqual(selectedResult.snapshot.document.graph.atoms[1],before.snapshot.document.graph.atoms[1]);
  for(const alter of [g=>{g.atoms[0].x+=1;},g=>{g.atoms[1].charge=1;}]){
    before=await live(page);fault.alter=alter;
    await page.click('#clean-button');await ready(page);
    assert.equal(await page.locator('#cleanup-dialog').evaluate(d=>d.open),false);
    assert.deepEqual(await live(page),before);
  }
  const endpoints=selectedResult.snapshot.document.graph.atoms;
  await selectAtom(page,{x:(endpoints[0].x+endpoints[1].x)/2,y:(endpoints[0].y+endpoints[1].y)/2});before=await live(page);
  await page.click('#clean-button');await ready(page);assert.deepEqual(requests.at(-1).selected,['a0','a1']);
  await page.click('#cancel-cleanup');assert.deepEqual(await live(page),before);
  fault.defer=true;let started=new Promise(resolve=>{fault.started=resolve;});
  before=await live(page);await page.click('#clean-button');await started;
  await page.keyboard.press('Escape');await ready(page);fault.release();assert.deepEqual(await live(page),before);
  await page.click('#clean-button');await ready(page);
  assert.equal(await page.evaluate(project=>window.nivramDocument.openProject(project),initial),true);
  const replacement=await live(page);
  assert.equal(await page.locator('#cleanup-dialog').evaluate(d=>d.open),false);
  await page.evaluate(()=>document.querySelector('#apply-cleanup').click());assert.deepEqual(await live(page),replacement);
  t.diagnostic('Cleanup UI assertions completed: whole/selected preview, cancellation, undo/redo, preservation, late result and stale Apply.');
});
