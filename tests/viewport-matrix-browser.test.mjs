import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor,ready,state} from './editor-browser-fixture.mjs';

// Acceptance oracle already recorded in editor-refinement.test.mjs:201-202.
// Keep this independent of the fit implementation and its exported constant.
const comfortableBondCss=70.47475694468609;
const bondTolerance=comfortableBondCss*.05;
// Half a CSS pixel allows subpixel SVG/layout rounding, not a DPR-scaled margin.
// These are outlined-ink geometry bounds, not raster antialias/filter extents.
const paddingCss=24,paddingTolerance=.5;
const viewports=[{width:800,height:600},{width:1366,height:768},{width:1920,height:1080}];
const cases=[
  {name:'tiny ethane',source:'CC',tiny:true},
  {name:'large disconnected salt',source:'C'.repeat(40)+'.[NH4+].[Cl-]',large:true,fragments:3},
  {name:'charged isotope labels',source:'[13CH3][15NH2+]'+'C'.repeat(38)+'C(=O)[18O-].[23Na+].[37Cl-]',labels:true,large:true,fragments:3},
];
async function frames(page){
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
}
async function load(page,source){
  await page.locator('#import-button').click();await page.locator('#structure-input').click();
  await page.keyboard.press('Control+A');await page.keyboard.type(source);
  // Observe the response to the real input. No extra render call or token read.
  const [response]=await Promise.all([
    page.waitForResponse(r=>new URL(r.url()).pathname==='/api/import'&&r.request().method()==='POST'),
    page.locator('#load-structure').click(),
  ]);
  assert.equal(response.ok(),true,`Import failed: HTTP ${response.status()}`);
  const result=await response.json();assert.ok(result.scene?.positions?.length>0);
  await page.locator('#import-dialog').waitFor({state:'hidden'});await ready(page);await frames(page);
  return result.scene;
}
function fragmentCount(graph){
  const remaining=new Set(graph.atoms.map(a=>a.id));let count=0;
  while(remaining.size){
    const queue=[remaining.values().next().value];remaining.delete(queue[0]);count++;
    while(queue.length){const id=queue.pop();for(const b of graph.bonds){
      const next=b.begin===id?b.end:b.end===id?b.begin:null;
      if(remaining.delete(next))queue.push(next);
    }}
  }
  return count;
}
async function measure(page,scene){
  return page.evaluate(scene=>{
    const canvas=document.getElementById('canvas'),drawing=document.getElementById('drawing');
    const rect=canvas.getBoundingClientRect(),matrix=canvas.getScreenCTM(),inkMatrix=drawing.getScreenCTM();
    if(!matrix||!inkMatrix)throw new Error('Missing live SVG screen transform.');
    const project=window.nivramDocument.getSnapshot().document;
    function bounds(node){
      const b=node.getBBox(),m=node.getScreenCTM();
      if(!m)throw new Error('Missing rendered outline transform.');
      const p=[[b.x,b.y],[b.x+b.width,b.y],[b.x,b.y+b.height],[b.x+b.width,b.y+b.height]]
        .map(([x,y])=>new DOMPoint(x,y).matrixTransform(m));
      return {left:Math.min(...p.map(p=>p.x)),right:Math.max(...p.map(p=>p.x)),
        top:Math.min(...p.map(p=>p.y)),bottom:Math.max(...p.map(p=>p.y))};
    }
    const ink=bounds(drawing);
    const atoms=scene.positions.map(a=>{const p=new DOMPoint(a.x,a.y).matrixTransform(matrix);return {id:a.id,x:p.x,y:p.y};});
    const byId=new Map(atoms.map(a=>[a.id,a]));
    const bonds=project.graph.bonds.map(b=>{
      const a=byId.get(b.begin),c=byId.get(b.end);
      if(!a||!c)throw new Error('Imported bond has no rendered endpoints.');
      return Math.hypot(a.x-c.x,a.y-c.y);
    }).sort((a,b)=>a-b);
    const middle=Math.floor(bonds.length/2);
    const median=bonds.length%2?bonds[middle]:(bonds[middle-1]+bonds[middle])/2;
    const labels=project.graph.atoms.flatMap((a,index)=>{
      if(!a.isotope&&!a.charge)return [];
      // Bond paths also carry atom classes: count actual atom glyph outlines only.
      const paths=[...drawing.querySelectorAll(`path.atom-${index}`)]
        .filter(p=>![...p.classList].some(c=>c.startsWith('bond-')));
      return [{id:a.id,isotope:a.isotope,charge:a.charge,outlines:paths.map(bounds)}];
    });
    const vb=canvas.viewBox.baseVal;
    return {viewport:{width:innerWidth,height:innerHeight},dpr:devicePixelRatio,
      canvas:{width:rect.width,height:rect.height},view:{width:vb.width,height:vb.height},
      ink:{...ink,width:ink.right-ink.left,height:ink.bottom-ink.top},
      padding:{left:ink.left-rect.left,right:rect.right-ink.right,top:ink.top-rect.top,bottom:rect.bottom-ink.bottom},
      atoms,bondCount:bonds.length,bondInkCount:drawing.querySelectorAll('path[class*="bond-"]').length,median,labels};
  },scene);
}
function assertFit(m,label){
  for(const [name,values] of Object.entries({canvas:m.canvas,view:m.view}))
    for(const [axis,value] of Object.entries(values))assert.ok(Number.isFinite(value)&&value>0,`${label}: empty/nonfinite ${name} ${axis}`);
  // getBBox() excludes stroke expansion: a visible horizontal/vertical bond
  // can have zero geometry height/width. Reject empty drawings separately.
  for(const axis of ['width','height'])assert.ok(Number.isFinite(m.ink[axis])&&m.ink[axis]>=0,`${label}: invalid ink ${axis}`);
  assert.ok(m.bondInkCount>0&&(m.ink.width>0||m.ink.height>0),`${label}: no rendered bonded drawing`);
  assert.ok(m.canvas.width>2*paddingCss&&m.canvas.height>2*paddingCss,`${label}: canvas cannot accommodate padding`);
  assert.ok(Math.abs(m.view.width/m.view.height-m.canvas.width/m.canvas.height)<1e-6,`${label}: view aspect must match measured canvas`);
  for(const [side,value] of Object.entries(m.padding))
    assert.ok(Number.isFinite(value)&&value>=paddingCss-paddingTolerance,`${label}: ${side} ink padding ${value} CSS px; expected 24 +/- 0.5 minimum`);
  assert.ok(m.bondCount>0&&Number.isFinite(m.median)&&m.median>0,`${label}: no measurable chemical bonds`);
  assert.ok(m.median<=comfortableBondCss+bondTolerance,`${label}: displayed median bond ${m.median} exceeds comfortable cap`);
}

for(const viewport of viewports)for(const deviceScaleFactor of [1,2]){
  test(`rendered fit matrix ${viewport.width}x${viewport.height} DPR ${deviceScaleFactor}`,{timeout:150000},async t=>{
    const page=await productionEditor(t,{viewport,deviceScaleFactor});
    for(const fixture of cases){
      const scene=await load(page,fixture.source),before=await state(page),m=await measure(page,scene);
      t.diagnostic(JSON.stringify({fixture:fixture.name,requested:{viewport,deviceScaleFactor},measured:m}));
      assert.deepEqual(m.viewport,viewport,'Use a real context viewport, not invented canvas dimensions.');
      assert.equal(m.dpr,deviceScaleFactor,'The browser must actually use the requested DPR.');
      assert.equal(m.atoms.length,before.document.graph.atoms.length);assertFit(m,fixture.name);
      if(fixture.fragments)assert.equal(fragmentCount(before.document.graph),fixture.fragments,'Keep disconnected components in the test input.');
      if(fixture.tiny)assert.ok(Math.abs(m.median-comfortableBondCss)<=bondTolerance,`Tiny import scale ${m.median} must stay within the recorded 5% calibration band.`);
      if(fixture.large){
        assert.ok(m.median<comfortableBondCss-bondTolerance,'Large import must shrink below the comfortable scale.');
        assert.ok(Math.min(...Object.values(m.padding))<=paddingCss+paddingTolerance,'A large drawing must use the available canvas, not hide under excessive zoom-out.');
      }
      if(fixture.labels){
        assert.ok(before.document.graph.atoms.some(a=>a.isotope&&a.charge),'Input must contain combined charge/isotope labels.');
        assert.ok(m.labels.length>=5,'Input must retain all isotope/charged label sites.');
        assert.ok(m.labels.some(a=>a.isotope===15&&a.charge===1&&a.outlines.length>=3),'The multi-glyph isotope/charge label must actually be rendered.');
        for(const label of m.labels){
          assert.ok(label.outlines.length>0,`Missing outlined label ${label.id}`);
          for(const b of label.outlines){
            assert.ok(Object.values(b).every(Number.isFinite)&&b.right>b.left&&b.bottom>b.top,'Label outline must have positive measured area.');
            assert.ok(b.left>=m.ink.left-.01&&b.right<=m.ink.right+.01&&b.top>=m.ink.top-.01&&b.bottom<=m.ink.bottom+.01,'Fit bounds must include every charged/isotope glyph.');
          }
        }
        const atomLeft=Math.min(...m.atoms.map(a=>a.x)),atomRight=Math.max(...m.atoms.map(a=>a.x));
        const atomTop=Math.min(...m.atoms.map(a=>a.y)),atomBottom=Math.max(...m.atoms.map(a=>a.y));
        assert.ok(m.ink.left<atomLeft-1||m.ink.right>atomRight+1||m.ink.top<atomTop-1||m.ink.bottom>atomBottom+1,'Labels must exercise ink extending beyond atom-center bounds.');
      }
      await page.locator('#fit-button').click();await frames(page);
      const explicit=await measure(page,scene);assertFit(explicit,`${fixture.name}, explicit Fit`);
      assert.ok(Math.abs(explicit.median-m.median)<=paddingTolerance,'Accepted-import fit and explicit Fit must agree in displayed scale.');
      assert.deepEqual(await state(page),before,'Fitting and geometry inspection must not change document or history revision.');
    }
  });
}
