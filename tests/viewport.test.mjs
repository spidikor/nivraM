import test from 'node:test';
import assert from 'node:assert/strict';
import {DEFAULT_TARGET_BOND_LENGTH,DEFAULT_SVG_BOND_LENGTH,fitInkView,drawingView,
  measureMedianBondLength,preserveWorldView} from '../public/modules/viewport.mjs';

const close=(actual,expected,tolerance=1e-9)=>assert.ok(Math.abs(actual-expected)<=tolerance,`${actual} != ${expected}`);
const box={x:-240,y:-160,width:2400,height:1200};
function checkFit(view,bounds,width,height,padding=24){
  close(view.w/view.h,width/height);
  const scale=width/view.w;
  assert.ok((bounds.x-view.x)*scale>=padding-1e-8);
  assert.ok((bounds.y-view.y)*scale>=padding-1e-8);
  assert.ok((view.x+view.w-bounds.x-bounds.width)*scale>=padding-1e-8);
  assert.ok((view.y+view.h-bounds.y-bounds.height)*scale>=padding-1e-8);
}

test('wide and tall imports retain full ink and 24 CSS-pixel padding at real viewport aspect',()=>{
  for(const [viewportWidth,viewportHeight] of [[800,600],[1366,768],[1920,1080],[320,900]]){
    for(const bounds of [box,{x:25,y:-400,width:500,height:3000}]){
      const view=fitInkView({bounds,viewportWidth,viewportHeight,medianBondLength:97.5});
      checkFit(view,bounds,viewportWidth,viewportHeight);
    }
  }
});

test('long label and disconnected-fragment ink determine fitting rather than atom-only bounds',()=>{
  const bounds={x:-850,y:12,width:3900,height:125};
  const view=fitInkView({bounds,viewportWidth:800,viewportHeight:600,medianBondLength:97.5});
  checkFit(view,bounds,800,600);
  close((bounds.x-view.x)*800/view.w,24);
  close((view.x+view.w-bounds.x-bounds.width)*800/view.w,24);
});

test('tiny imports are centered and capped at comfortable displayed bond length',()=>{
  const bounds={x:120,y:130,width:90,height:60};
  const view=fitInkView({bounds,viewportWidth:1366,viewportHeight:768,medianBondLength:97.5});
  close(97.5*1366/view.w,DEFAULT_TARGET_BOND_LENGTH);
  close(view.x+view.w/2,165);close(view.y+view.h/2,160);
  checkFit(view,bounds,1366,768);
});

test('large imports shrink below the comfortable cap as necessary',()=>{
  const view=fitInkView({bounds:box,viewportWidth:800,viewportHeight:600,medianBondLength:97.5});
  assert.ok(97.5*800/view.w<DEFAULT_TARGET_BOND_LENGTH);
  checkFit(view,box,800,600);
});

test('bondless labels use a stable scale reference instead of filling the window',()=>{
  const bounds={x:390,y:230,width:20,height:40};
  const view=fitInkView({bounds,viewportWidth:800,viewportHeight:600,medianBondLength:null});
  close(DEFAULT_SVG_BOND_LENGTH*800/view.w,DEFAULT_TARGET_BOND_LENGTH);
  checkFit(view,bounds,800,600);
});

test('point and line ink produce finite aspect-correct views',()=>{
  for(const bounds of [{x:0,y:0,width:0,height:0},{x:10,y:20,width:0,height:1000},{x:-20,y:-10,width:1000,height:0}]){
    const view=fitInkView({bounds,viewportWidth:800,viewportHeight:600});
    checkFit(view,bounds,800,600);
    assert.ok(Object.values(view).every(Number.isFinite));
  }
});

test('new drawings use the calibrated scale at every CSS viewport size',()=>{
  for(const [viewportWidth,viewportHeight] of [[800,600],[1366,768],[1920,1080]]){
    const view=drawingView({viewportWidth,viewportHeight});
    close(view.w/view.h,viewportWidth/viewportHeight);
    close(DEFAULT_SVG_BOND_LENGTH*viewportWidth/view.w,70.47475694468609);
    close(view.x+view.w/2,400);close(view.y+view.h/2,250);
  }
});

test('drawing scale accepts the real scene bond length and a chosen molecular center',()=>{
  const view=drawingView({viewportWidth:800,viewportHeight:600,svgBondLength:90,centerX:-75,centerY:140});
  close(90*800/view.w,DEFAULT_TARGET_BOND_LENGTH);
  close(view.x+view.w/2,-75);close(view.y+view.h/2,140);
});

test('geometry depends only on CSS size, not device-pixel ratio',()=>{
  const input={bounds:box,viewportWidth:1366,viewportHeight:768,medianBondLength:97.5};
  const low=fitInkView({...input,devicePixelRatio:1});
  const high=fitInkView({...input,devicePixelRatio:2});
  assert.deepEqual(low,high);
  assert.deepEqual(drawingView({...input,devicePixelRatio:1}),drawingView({...input,devicePixelRatio:2}));
});

test('hidden, nonfinite, and padding-constrained viewports request no view update',()=>{
  for(const [viewportWidth,viewportHeight] of [[0,600],[800,0],[-1,500],[NaN,500],[800,Infinity]]){
    assert.equal(fitInkView({bounds:box,viewportWidth,viewportHeight}),null);
    assert.equal(drawingView({viewportWidth,viewportHeight}),null);
  }
  assert.equal(fitInkView({bounds:box,viewportWidth:48,viewportHeight:600}),null);
  assert.equal(fitInkView({bounds:box,viewportWidth:600,viewportHeight:40}),null);
});

test('bad geometry cannot produce an invalid or inverted viewBox',()=>{
  const valid={bounds:box,viewportWidth:800,viewportHeight:600};
  for(const bounds of [null,{...box,width:-1},{...box,height:-2},{...box,x:NaN},{...box,y:Infinity}]){
    assert.throws(()=>fitInkView({...valid,bounds}));
  }
  for(const medianBondLength of [0,-1,NaN,Infinity])assert.throws(()=>fitInkView({...valid,medianBondLength}));
  for(const targetBondLength of [0,-1,NaN,Infinity]){
    assert.throws(()=>fitInkView({...valid,targetBondLength}));
    assert.throws(()=>drawingView({...valid,targetBondLength}));
  }
  assert.throws(()=>fitInkView({...valid,padding:-1}));
  assert.throws(()=>drawingView({...valid,centerX:NaN}));
});

test('median follows real scene.positions and graph begin/end connectivity, counting each bond once',()=>{
  const scene={positions:[{id:'a',x:0,y:0},{id:'b',x:3,y:4},{id:'c',x:3,y:16},{id:'d',x:30,y:16}]};
  const graph={bonds:[{begin:'a',end:'b',order:3},{begin:'b',end:'c',order:1},{begin:'c',end:'d',order:2}]};
  const before=JSON.stringify({scene,graph});
  close(measureMedianBondLength(scene,graph),12);
  close(measureMedianBondLength(scene,{bonds:graph.bonds.slice(0,2)}),8.5);
  assert.equal(JSON.stringify({scene,graph}),before);
});

test('median ignores coincident endpoints, avoids unrelated fragment distances, and supports no bonds',()=>{
  const scene={positions:[{id:'a',x:0,y:0},{id:'b',x:0,y:0},{id:'c',x:0,y:100},{id:'d',x:10000,y:10000}]};
  assert.equal(measureMedianBondLength(scene,{bonds:[{begin:'a',end:'b'}]}),null);
  assert.equal(measureMedianBondLength(scene,{bonds:[]}),null);
  assert.equal(measureMedianBondLength(null,{bonds:[]}),null);
  assert.equal(measureMedianBondLength(scene,null),null);
  close(measureMedianBondLength(scene,{bonds:[{begin:'a',end:'b'},{begin:'b',end:'c'}]}),100);
});

test('inconsistent atom positions and missing bond endpoints are rejected',()=>{
  const scene={positions:[{id:'a',x:0,y:0}]};
  assert.throws(()=>measureMedianBondLength(scene,{bonds:[{begin:'a',end:'missing'}]}));
  assert.throws(()=>measureMedianBondLength({positions:[...scene.positions,...scene.positions]},{bonds:[]}));
  assert.throws(()=>measureMedianBondLength({positions:[{id:'a',x:NaN,y:0}]},{bonds:[]}));
  assert.throws(()=>measureMedianBondLength({atoms:[]},{bonds:[]}));
});

test('preserving a manually adjusted view retains identical world edges through scene translation and scale',()=>{
  const old={x0:400,y0:250,xScale:60,yScale:-60},next={x0:800,y0:500,xScale:65,yScale:-65};
  const input={x:225,y:150,w:350,h:240};
  const output=preserveWorldView({view:input,previousTransform:old,nextTransform:next});
  for(const [axis,size,origin,scale] of [['x','w','x0','xScale'],['y','h','y0','yScale']]){
    close((input[axis]-old[origin])/old[scale],(output[axis]-next[origin])/next[scale]);
    close((input[axis]+input[size]-old[origin])/old[scale],(output[axis]+output[size]-next[origin])/next[scale]);
  }
  const restored=preserveWorldView({view:output,previousTransform:next,nextTransform:old});
  for(const key of ['x','y','w','h'])close(restored[key],input[key]);
  assert.deepEqual(input,{x:225,y:150,w:350,h:240});
});

test('world-view preservation has no comfortable-scale cap or implicit recentering',()=>{
  const transform={x0:400,y0:250,xScale:65,yScale:-65},view={x:1500,y:-750,w:20,h:12};
  assert.deepEqual(preserveWorldView({view,previousTransform:transform,nextTransform:transform}),view);
});

test('world-view preservation rejects invalid transforms and view dimensions',()=>{
  const valid={view:{x:0,y:0,w:800,h:500},previousTransform:{x0:400,y0:250,xScale:60,yScale:-60},nextTransform:{x0:500,y0:300,xScale:65,yScale:-65}};
  assert.throws(()=>preserveWorldView({...valid,view:{...valid.view,w:0}}));
  assert.throws(()=>preserveWorldView({...valid,previousTransform:{...valid.previousTransform,xScale:0}}));
  assert.throws(()=>preserveWorldView({...valid,nextTransform:{...valid.nextTransform,yScale:65}}));
  assert.throws(()=>preserveWorldView({...valid,nextTransform:{...valid.nextTransform,x0:Infinity}}));
});
