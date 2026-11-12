import test from 'node:test';
import assert from 'node:assert/strict';
import {createImageCorrectionView} from '../public/modules/image-correction-view.mjs';

const close=(actual,expected)=>assert.ok(Math.abs(actual-expected)<1e-9,`${actual} != ${expected}`);
function fixture({width=600,height=300,scale=100,x0=200,y0=150}={}){
  return {width,height,bounds:{x:x0-20,y:y0-20,width:scale+40,height:40},
    graph:{atoms:[{id:'a',x:0,y:0},{id:'b',x:1,y:0}],bonds:[{begin:'a',end:'b'}]},
    scene:{transform:{x0,y0,xScale:scale,yScale:-scale},positions:[{id:'a',x:x0,y:y0},{id:'b',x:x0+scale,y:y0}]}};
}
function screen(view,source,point){
  const t=source.scene.transform;
  return {x:(t.x0+point.x*t.xScale-view.x)/view.w*source.width,
    y:(t.y0+point.y*t.yScale-view.y)/view.h*source.height};
}
function worldCenter(view,source){const t=source.scene.transform;return {x:(view.x+view.w/2-t.x0)/t.xScale,y:(view.y+view.h/2-t.y0)/t.yScale};}

test('initial Fit includes ink, uses viewport shape and calibrated scale',()=>{
  const nav=createImageCorrectionView(),source=fixture(),view=nav.load(source);
  assert.equal(nav.isAutomatic(),true);close(view.w/view.h,2);
  assert.ok((source.bounds.x-view.x)/view.w*600>=24);
  close(screen(view,source,{x:1,y:0}).x-screen(view,source,{x:0,y:0}).x,70.47475694468609);
});

test('zoom preserves an off-center screen anchor and does not mutate source',()=>{
  const nav=createImageCorrectionView(),source=fixture(),before=structuredClone(source),view=nav.load(source);
  const anchor={x:view.x+view.w*.27,y:view.y+view.h*.61},next=nav.zoom(.5,anchor);
  close((anchor.x-next.x)/next.w,.27);close((anchor.y-next.y)/next.h,.61);
  close(next.w,view.w/2);assert.equal(nav.isAutomatic(),false);assert.deepEqual(source,before);
});

test('pan changes only view origin and snapshots cannot mutate navigation',()=>{
  const nav=createImageCorrectionView();nav.load(fixture());const before=nav.snapshot(),after=nav.pan(123,-45);
  close(after.x,before.x+123);close(after.y,before.y-45);close(after.w,before.w);close(after.h,before.h);
  after.x=999999;assert.notEqual(nav.snapshot().x,after.x);assert.equal(nav.isAutomatic(),false);
});

test('corrected renderer origin and scale retain molecular-world screen positions',()=>{
  const nav=createImageCorrectionView(),first=fixture();nav.load(first);nav.zoom(.5);const before=nav.pan(37,-21);
  const point={x:.2,y:.4},position=screen(before,first,point);
  const next=fixture({scale:61,x0:61,y0:219}),after=nav.load(next);
  close(screen(after,next,point).x,position.x);close(screen(after,next,point).y,position.y);
  const undone=nav.load(first);for(const key of ['x','y','w','h'])close(undone[key],before[key]);
});

test('manual resize retains world center and units per CSS pixel',()=>{
  const nav=createImageCorrectionView(),source=fixture();nav.load(source);nav.zoom(.8);const before=nav.pan(50,-20);
  const center=worldCenter(before,source),after=nav.resize(900,400),next={...source,width:900,height:400};
  close(worldCenter(after,next).x,center.x);close(worldCenter(after,next).y,center.y);
  close(after.w/900,before.w/600);close(after.h/400,before.h/300);
});

test('Fit restores automatic navigation and includes changed ink',()=>{
  const nav=createImageCorrectionView(),source=fixture();const original=nav.load(source);nav.zoom(.4);nav.pan(99,80);
  assert.deepEqual(nav.fit(),original);assert.equal(nav.isAutomatic(),true);
  const next={...source,bounds:{x:-10000,y:-10000,width:20000,height:20000}};
  const fitted=nav.load(next);assert.ok(fitted.x<-10000);assert.ok(fitted.y<-10000);
  assert.ok(fitted.x+fitted.w>10000);assert.ok(fitted.y+fitted.h>10000);
});

test('hidden viewport resize does not erase a manual view',()=>{
  const nav=createImageCorrectionView();nav.load(fixture());const before=nav.zoom(.5);
  assert.deepEqual(nav.resize(0,0),before);assert.equal(nav.isAutomatic(),false);
  assert.deepEqual(nav.resize(600,300),before);
});

test('clear removes old draft view so next source starts fitted',()=>{
  const nav=createImageCorrectionView(),source=fixture(),original=nav.load(source);nav.zoom(.5);nav.pan(100,50);nav.clear();
  assert.equal(nav.snapshot(),null);assert.equal(nav.isAutomatic(),true);assert.deepEqual(nav.load(source),original);
});

test('extreme zoom remains finite and preserves anchor within explicit bounds',()=>{
  const nav=createImageCorrectionView(),source=fixture();const original=nav.load(source),anchor={x:original.x,y:original.y};
  const near=nav.zoom(Number.MIN_VALUE,anchor);close(near.w,75);close(near.x,anchor.x);close(near.y,anchor.y);
  const far=nav.zoom(Number.MAX_VALUE,anchor);close(far.w,600000);close(far.x,anchor.x);close(far.y,anchor.y);
  for(const value of Object.values(far))assert.ok(Number.isFinite(value));
});

test('invalid navigation inputs fail without changing the chosen view',()=>{
  const nav=createImageCorrectionView();const before=nav.load(fixture());
  for(const value of [0,-1,NaN,Infinity,'1',null])assert.throws(()=>nav.zoom(value));
  assert.throws(()=>nav.zoom(1,{x:NaN,y:0}));assert.throws(()=>nav.pan(Infinity,0));
  assert.deepEqual(nav.snapshot(),before);
});

test('zoom limits never reverse direction or jump when Fit needs a wider view',()=>{
  const nav=createImageCorrectionView(),source=fixture();
  source.bounds={x:-1000000,y:-1000000,width:2000000,height:2000000};
  const fitted=nav.load(source);assert.ok(fitted.w>600000);
  assert.deepEqual(nav.zoom(1.2),fitted,'Zoom out at the limit must not zoom in');
  assert.deepEqual(nav.zoom(1),fitted,'Identity zoom must preserve any fitted view');
  close(nav.zoom(.8).w,fitted.w*.8);
});
