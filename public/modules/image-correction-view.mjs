import {fitInkView,measureMedianBondLength,preserveWorldView} from './viewport.mjs';

/** View-only navigation. Renderer origins/scales may change after any edit. */
export function createImageCorrectionView(){
  let view=null,source=null,automatic=true;
  const copy=()=>view?{...view}:null;
  const finite=(n)=>typeof n==='number'&&Number.isFinite(n);
  function fit(){
    if(!source)return null;
    const next=fitInkView({bounds:source.bounds,viewportWidth:source.width,viewportHeight:source.height,
      medianBondLength:measureMedianBondLength(source.scene,source.graph)});
    if(next){view=next;automatic=true;}
    return copy();
  }
  function load(next){
    if(!next||!finite(next.width)||!finite(next.height)||next.width<=0||next.height<=0)return copy();
    if(view&&source&&!automatic){
      const mapped=preserveWorldView({view,previousTransform:source.scene.transform,nextTransform:next.scene.transform});
      const w=mapped.w*next.width/source.width,h=mapped.h*next.height/source.height;
      view={x:mapped.x+(mapped.w-w)/2,y:mapped.y+(mapped.h-h)/2,w,h};
      source=next;
    }else{source=next;fit();}
    return copy();
  }
  function zoom(factor,anchor){
    if(!finite(factor)||factor<=0)throw new TypeError('A positive zoom factor is required.');
    if(!view||!source)return copy();
    const point=anchor??{x:view.x+view.w/2,y:view.y+view.h/2};
    if(!finite(point.x)||!finite(point.y))throw new TypeError('A finite zoom anchor is required.');
    // Bound the molecular-world units per CSS pixel, independent of rendering
    // origin/scale or the size of the imported drawing.
    const units=source.scene.transform.xScale*source.width;
    // Fit can require a view outside the normal navigation range. Include the
    // current width so a bounded step never reverses direction or jumps inward.
    const w=Math.min(Math.max(units*10,view.w),Math.max(Math.min(units/800,view.w),view.w*factor)),ratio=w/view.w;
    if(ratio===1)return copy();
    view={x:point.x+(view.x-point.x)*ratio,y:point.y+(view.y-point.y)*ratio,w,h:view.h*ratio};
    automatic=false;return copy();
  }
  function pan(dx,dy){
    if(!finite(dx)||!finite(dy))throw new TypeError('Finite pan distances are required.');
    if(view&&(dx||dy)){
      const x=view.x+dx,y=view.y+dy;
      if(!finite(x)||!finite(y))throw new RangeError('Pan exceeds the supported view range.');
      view={...view,x,y};automatic=false;
    }
    return copy();
  }
  return Object.freeze({load,fit,zoom,pan,snapshot:copy,isAutomatic:()=>automatic,
    resize(width,height){return source?load({...source,width,height}):copy();},
    clear(){view=null;source=null;automatic=true;}});
}
