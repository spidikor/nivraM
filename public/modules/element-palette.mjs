import {COMMON_ELEMENTS,MORE_ELEMENTS,ELEMENT_NAMES} from './elements.mjs';

/** A disclosure of native buttons; chemistry and selected tool belong to the caller. */
export function initializeElementPalette({document:doc,parent,onSelect,getSelected}){
  if(!doc||!parent||typeof onSelect!=='function'||typeof getSelected!=='function')
    throw new TypeError('Element palette requires its document, parent and selection callbacks.');
  const buttons=new Map();
  const disclosure=doc.createElement('span');disclosure.className='element-disclosure';
  const trigger=doc.createElement('button');trigger.type='button';trigger.id='more-elements-button';
  trigger.className='more-elements-button';trigger.setAttribute('aria-controls','more-elements-options');
  const options=doc.createElement('div');options.id='more-elements-options';options.className='more-elements-options';
  options.setAttribute('role','group');options.setAttribute('aria-label','More elements');options.hidden=true;

  function close(restoreFocus=false){
    const wasOpen=!options.hidden;
    options.hidden=true;trigger.textContent='More ▾';trigger.setAttribute('aria-expanded','false');
    if(wasOpen&&restoreFocus&&trigger.isConnected)trigger.focus();
  }
  function refresh(){
    const selected=getSelected();
    for(const [symbol,button] of buttons){
      const active=symbol===selected;
      button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));
    }
    trigger.classList.toggle('active',MORE_ELEMENTS.includes(selected));
  }
  function buttonFor(symbol){
    const button=doc.createElement('button');button.type='button';button.className='element';
    button.dataset.element=symbol;button.textContent=symbol;
    button.title=`Choose ${ELEMENT_NAMES[symbol]} (${symbol})`;
    button.setAttribute('aria-label',button.title);
    button.addEventListener('click',()=>{
      if(MORE_ELEMENTS.includes(symbol))close(true);
      onSelect(symbol);refresh();
    });
    buttons.set(symbol,button);return button;
  }
  for(const symbol of COMMON_ELEMENTS)parent.append(buttonFor(symbol));
  for(const symbol of MORE_ELEMENTS)options.append(buttonFor(symbol));
  disclosure.append(trigger,options);parent.append(disclosure);

  function open(){
    refresh();options.hidden=false;trigger.textContent='More ▴';trigger.setAttribute('aria-expanded','true');
    const selected=getSelected();
    buttons.get(MORE_ELEMENTS.includes(selected)?selected:MORE_ELEMENTS[0]).focus();
  }
  trigger.addEventListener('click',()=>options.hidden?open():close(true));
  trigger.addEventListener('keydown',event=>{
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){
      event.preventDefault();event.stopPropagation();open();
    }
  });
  disclosure.addEventListener('keydown',event=>{
    if(options.hidden)return;
    if(event.key==='Escape'){
      event.preventDefault();event.stopPropagation();close(true);return;
    }
    if(event.key==='Tab'){
      // Let the browser perform normal Tab/Shift+Tab from the disclosure trigger.
      // Hidden options cannot trap focus or become invisible tab stops.
      close(true);return;
    }
    if(!['ArrowDown','ArrowUp','ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();event.stopPropagation();
    const entries=MORE_ELEMENTS.map(symbol=>buttons.get(symbol));
    const current=entries.indexOf(doc.activeElement);
    const backwards=['ArrowUp','ArrowLeft'].includes(event.key);
    const next=event.key==='Home'?0:event.key==='End'?entries.length-1:
      (current+(backwards?-1:1)+entries.length)%entries.length;
    entries[next].focus();
  });
  doc.addEventListener('pointerdown',event=>{if(!disclosure.contains(event.target))close();},true);
  doc.addEventListener('focusin',event=>{if(!disclosure.contains(event.target))close();});
  doc.defaultView.addEventListener('blur',()=>close());
  doc.defaultView.addEventListener('nivram-close-review',()=>close());
  close();refresh();
  return Object.freeze({refresh,close,isOpen:()=>!options.hidden});
}
