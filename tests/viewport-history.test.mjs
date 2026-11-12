import test from 'node:test';
import assert from 'node:assert/strict';
import {restoreHistoryView} from '../public/modules/viewport.mjs';

const previousTransform={x0:400,y0:250,xScale:60,yScale:-60};
const nextTransform={x0:800,y0:500,xScale:120,yScale:-120};
const input={view:{x:100,y:150,w:800,h:500},previousTransform,nextTransform,
  previousViewport:{width:800,height:500},viewportWidth:800,viewportHeight:500};
const close=(a,b)=>assert.ok(Math.abs(a-b)<1e-9,`${a} != ${b}`);

test('history restores exact world edges at the original viewport size',()=>{
  const before=JSON.stringify(input),view=restoreHistoryView(input);
  assert.deepEqual(view,{x:200,y:300,w:1600,h:1000});
  assert.equal(JSON.stringify(input),before);
});

test('resized history restores the molecular center and display scale with current aspect',()=>{
  for(const [width,height] of [[1200,500],[600,900]]){
    const view=restoreHistoryView({...input,viewportWidth:width,viewportHeight:height});
    close((view.x+view.w/2-nextTransform.x0)/nextTransform.xScale,5/3);
    close((view.y+view.h/2-nextTransform.y0)/nextTransform.yScale,-2.5);
    close(view.w/width,2);close(view.h/height,2);close(view.w/view.h,width/height);
  }
});

test('letterboxed saved views preserve displayed scale when the viewport changes',()=>{
  const view=restoreHistoryView({...input,previousViewport:{width:1000,height:500},viewportWidth:600,viewportHeight:400});
  close(view.w,1200);close(view.h,800);close(view.x+view.w/2,1000);close(view.y+view.h/2,800);
});

test('hidden viewport keeps valid world edges and invalid view data cannot publish nonfinite geometry',()=>{
  assert.deepEqual(restoreHistoryView({...input,viewportWidth:0}),{x:200,y:300,w:1600,h:1000});
  assert.throws(()=>restoreHistoryView({...input,view:{...input.view,w:-1}}));
  assert.throws(()=>restoreHistoryView({...input,nextTransform:{...nextTransform,xScale:NaN}}));
});
