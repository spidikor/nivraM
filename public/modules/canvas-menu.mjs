/** Resolve a context target without changing chemistry or the current selection. */
export function reflectionSelection(graph,selection,hit){
  const chosen=new Set(selection.atoms);
  for(const bond of graph.bonds)if(selection.bonds.includes(bond.id)){chosen.add(bond.begin);chosen.add(bond.end);}
  const target=hit?.atomId?[hit.atomId]:hit?.bondId?graph.bonds.filter(b=>b.id===hit.bondId).flatMap(b=>[b.begin,b.end]):[];
  if(target.length&&!target.every(id=>chosen.has(id))){
    chosen.clear();for(const id of target)chosen.add(id);
    const queue=[...chosen];
    for(let i=0;i<queue.length;i++)for(const bond of graph.bonds){
      const neighbor=bond.begin===queue[i]?bond.end:bond.end===queue[i]?bond.begin:null;
      if(neighbor!==null&&!chosen.has(neighbor)){chosen.add(neighbor);queue.push(neighbor);}
    }
  }
  return graph.atoms.filter(atom=>chosen.has(atom.id)).map(atom=>atom.id);
}

export function initializeCanvasMenu({document:doc,canvas,getState,getSelection,hitTest,isBusy,reflect,rotate,notify}){
  const menu=doc.createElement('div');menu.id='canvas-context-menu';menu.className='canvas-context-menu';
  menu.setAttribute('role','menu');menu.setAttribute('aria-label','Rotate, flip or mirror drawing');menu.hidden=true;
  const scope=doc.createElement('p');scope.className='canvas-context-scope';scope.id='canvas-context-scope';
  menu.setAttribute('aria-describedby',scope.id);menu.append(scope);
  const items=[];let opened=null,returnFocus=canvas;
  function close(restore=false){
    const wasOpen=!menu.hidden;menu.hidden=true;opened=null;
    if(wasOpen&&restore&&returnFocus?.isConnected)returnFocus.focus();
  }
  for(const [mode,verb,meaning] of [['flip','Flip','Preserve stereochemistry'],['mirror','Mirror','Reflect stereochemistry']]){
    for(const [axis,direction] of [['horizontal','horizontally'],['vertical','vertically']]){
      const item=doc.createElement('button');item.id=`canvas-${mode}-${axis}`;item.type='button';item.setAttribute('role','menuitem');item.tabIndex=-1;
      const label=doc.createElement('span'),detail=doc.createElement('small');label.textContent=`${verb} ${direction}`;detail.textContent=meaning;
      item.append(label,detail);menu.append(item);items.push(item);
      item.addEventListener('click',async()=>{
        const request=opened;close(true);if(!request)return;
        if(isBusy()||doc.querySelector('dialog[open]'))return;
        if(getState().revision!==request.revision){notify('The drawing changed. Open the menu again before reflecting it.',true);return;}
        await reflect({mode,axis,selected:request.selected,expectedRevision:request.revision});
      });
    }
  }
  for(const [degrees,direction,id] of [[30,'counterclockwise','ccw'],[-30,'clockwise','cw']]){
    const item=doc.createElement('button');item.id=`canvas-rotate-${id}`;item.type='button';item.setAttribute('role','menuitem');item.tabIndex=-1;item.textContent=`Rotate 30° ${direction}`;menu.append(item);items.push(item);
    item.addEventListener('click',async()=>{const request=opened;close(true);if(!request||isBusy()||doc.querySelector('dialog[open]'))return;if(getState().revision!==request.revision){notify('The drawing changed. Open the menu again.',true);return;}await rotate?.({degrees,selected:request.selected,expectedRevision:request.revision});});
  }
  doc.body.append(menu);
  function open(event,keyboard=false){
    event.preventDefault();close();
    if(isBusy()||doc.querySelector('dialog[open]'))return;
    const state=getState(),selected=reflectionSelection(state.document.graph,getSelection(),keyboard?null:hitTest(event));
    opened={revision:state.revision,selected};returnFocus=canvas;
    scope.textContent=selected.length?`${selected.length} selected atom${selected.length===1?'':'s'}`:'Whole drawing';
    for(const [index,item] of items.entries()){item.disabled=state.document.graph.atoms.length===0;item.tabIndex=index===0?0:-1;}
    const rect=canvas.getBoundingClientRect();
    const x=keyboard?rect.left+Math.min(30,rect.width/2):event.clientX,y=keyboard?rect.top+Math.min(30,rect.height/2):event.clientY;
    menu.hidden=false;menu.style.left='0px';menu.style.top='0px';
    const bounds=menu.getBoundingClientRect(),view=doc.defaultView;
    menu.style.left=Math.max(8,Math.min(x,view.innerWidth-bounds.width-8))+'px';
    menu.style.top=Math.max(8,Math.min(y,view.innerHeight-bounds.height-8))+'px';
    if(!items[0].disabled)items[0].focus();else{menu.tabIndex=-1;menu.focus();}
  }
  canvas.addEventListener('contextmenu',event=>open(event));
  canvas.addEventListener('keydown',event=>{
    if(event.key==='ContextMenu'||event.key==='F10'&&event.shiftKey){event.stopPropagation();open(event,true);}
  });
  menu.addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close(true);return;}
    if(event.key==='Tab'){close(true);return;}
    if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
    event.preventDefault();event.stopPropagation();const enabled=items.filter(item=>!item.disabled);if(!enabled.length)return;
    const current=enabled.indexOf(doc.activeElement);
    const next=event.key==='Home'?0:event.key==='End'?enabled.length-1:(current+(event.key==='ArrowUp'?-1:1)+enabled.length)%enabled.length;
    for(const item of items)item.tabIndex=-1;enabled[next].tabIndex=0;enabled[next].focus();
  });
  doc.addEventListener('pointerdown',event=>{if(!menu.contains(event.target))close();},true);
  doc.addEventListener('focusin',event=>{if(!menu.hidden&&!menu.contains(event.target))close();});
  doc.defaultView.addEventListener('resize',()=>close());
  doc.defaultView.addEventListener('blur',()=>close());
  doc.defaultView.addEventListener('nivram-close-review',()=>close());
  return Object.freeze({close:()=>close(),element:menu});
}
