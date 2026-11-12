// Pure SVG-view geometry. Viewport dimensions and padding are CSS pixels;
// coordinates, ink bounds, bond lengths and returned views are SVG units.
export const DEFAULT_TARGET_BOND_LENGTH=70.47475694468609;
export const DEFAULT_SVG_BOND_LENGTH=97.5;

function finite(value,label){
  if(typeof value!=='number'||!Number.isFinite(value))throw new TypeError(`${label} must be finite.`);
  return value;
}
function positive(value,label){
  finite(value,label);if(value<=0)throw new RangeError(`${label} must be positive.`);return value;
}
function measurable(width,height){
  return typeof width==='number'&&Number.isFinite(width)&&width>0
    &&typeof height==='number'&&Number.isFinite(height)&&height>0;
}
function viewAt(centerX,centerY,w,h){
  positive(w,'View width');positive(h,'View height');
  const x=finite(centerX-w/2,'View x'),y=finite(centerY-h/2,'View y');
  return {x,y,w,h};
}

/**
 * Fit complete rendered ink, including labels and disconnected fragments.
 * A null/absent median (an atom-only drawing) uses the normal SVG bond length
 * as a scale reference, so a lone label is not enlarged to fill the window.
 * Return null while the CSS viewport is hidden or cannot accommodate padding.
 */
export function fitInkView({bounds,viewportWidth,viewportHeight,medianBondLength=null,
  padding=24,targetBondLength=DEFAULT_TARGET_BOND_LENGTH}){
  if(!measurable(viewportWidth,viewportHeight))return null;
  positive(targetBondLength,'Target bond length');finite(padding,'Padding');
  if(padding<0)throw new RangeError('Padding must be nonnegative.');
  const availableWidth=viewportWidth-2*padding,availableHeight=viewportHeight-2*padding;
  if(availableWidth<=0||availableHeight<=0)return null;
  if(!bounds||typeof bounds!=='object')throw new TypeError('Ink bounds are required.');
  const {x,y,width,height}=bounds;
  finite(x,'Ink x');finite(y,'Ink y');finite(width,'Ink width');finite(height,'Ink height');
  if(width<0||height<0)throw new RangeError('Ink dimensions must be nonnegative.');
  const reference=medianBondLength??DEFAULT_SVG_BOND_LENGTH;
  positive(reference,'Median bond length');
  const scale=Math.min(width>0?availableWidth/width:Infinity,
    height>0?availableHeight/height:Infinity,targetBondLength/reference);
  positive(scale,'Drawing scale');
  return viewAt(x+width/2,y+height/2,viewportWidth/scale,viewportHeight/scale);
}

/** A new blank document's view at the calibrated comfortable drawing scale. */
export function drawingView({viewportWidth,viewportHeight,svgBondLength=DEFAULT_SVG_BOND_LENGTH,
  centerX=400,centerY=250,targetBondLength=DEFAULT_TARGET_BOND_LENGTH}){
  if(!measurable(viewportWidth,viewportHeight))return null;
  positive(svgBondLength,'SVG bond length');positive(targetBondLength,'Target bond length');
  finite(centerX,'Drawing center x');finite(centerY,'Drawing center y');
  const unitsPerCssPixel=svgBondLength/targetBondLength;
  return viewAt(centerX,centerY,viewportWidth*unitsPerCssPixel,viewportHeight*unitsPerCssPixel);
}

/**
 * The real renderer stores SVG positions in scene.positions; connectivity is
 * held separately in graph.bonds with begin/end atom identifiers. Measure each
 * chemical bond once, independent of its order or the number of drawn strokes.
 * Return null for a bondless scene or exclusively coincident endpoints.
 */
export function measureMedianBondLength(scene,graph){
  if(scene==null||graph==null)return null;
  if(!Array.isArray(scene.positions)||!Array.isArray(graph.bonds))throw new TypeError('A scene and its graph are required.');
  const positions=new Map();
  for(const position of scene.positions){
    if(!position||positions.has(position.id))throw new TypeError('Scene atom positions must have unique identifiers.');
    finite(position.x,'Atom x');finite(position.y,'Atom y');positions.set(position.id,position);
  }
  const lengths=[];
  for(const bond of graph.bonds){
    const a=positions.get(bond.begin),b=positions.get(bond.end);
    if(!a||!b)throw new TypeError('A bond endpoint is absent from the scene.');
    const length=finite(Math.hypot(a.x-b.x,a.y-b.y),'Rendered bond length');
    if(length>0)lengths.push(length);
  }
  if(!lengths.length)return null;
  lengths.sort((a,b)=>a-b);
  const middle=Math.floor(lengths.length/2);
  return lengths.length%2?lengths[middle]:lengths[middle-1]+(lengths[middle]-lengths[middle-1])/2;
}

function validTransform(transform){
  if(!transform||typeof transform!=='object')throw new TypeError('A scene transform is required.');
  for(const key of ['x0','y0','xScale','yScale'])finite(transform[key],`Transform ${key}`);
  if(transform.xScale<=0||transform.yScale>=0)throw new RangeError('Unsupported scene transform orientation.');
  return transform;
}

/**
 * Keep the same molecular-world viewport after RDKit changes its SVG origin or
 * scale. This never refits, recenters, or caps a user's zoom. Input and output
 * views use {x,y,w,h}; app.mjs can map its width/height names at this boundary.
 */
export function preserveWorldView({view,previousTransform,nextTransform}){
  if(!view||typeof view!=='object')throw new TypeError('A view is required.');
  finite(view.x,'View x');finite(view.y,'View y');positive(view.w,'View width');positive(view.h,'View height');
  const old=validTransform(previousTransform),next=validTransform(nextTransform);
  const rx=next.xScale/old.xScale,ry=next.yScale/old.yScale;
  const x=finite(next.x0+(view.x-old.x0)*rx,'View x');
  const y=finite(next.y0+(view.y-old.y0)*ry,'View y');
  const w=positive(view.w*rx,'View width'),h=positive(view.h*ry,'View height');
  return {x,y,w,h};
}

/** Restore transient history in the current renderer and viewport. Resize
 * preserves molecular center and displayed scale. Automatic Fit is handled by
 * the caller using the restored drawing's current ink bounds. */
export function restoreHistoryView({view,previousTransform,nextTransform,previousViewport,viewportWidth,viewportHeight}){
  const mapped=preserveWorldView({view,previousTransform,nextTransform}),old=previousViewport;
  if(!old||!measurable(old.width,old.height)||!measurable(viewportWidth,viewportHeight)
      ||(old.width===viewportWidth&&old.height===viewportHeight))return mapped;
  // SVG meet alignment uses the larger units-per-pixel ratio if a retained
  // manual viewBox and its CSS viewport have different aspect ratios.
  const unitsPerPixel=Math.max(mapped.w/old.width,mapped.h/old.height);
  return viewAt(mapped.x+mapped.w/2,mapped.y+mapped.h/2,
    viewportWidth*unitsPerPixel,viewportHeight*unitsPerPixel);
}
