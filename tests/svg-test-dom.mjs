/** Controlled DOM seam for SVG logic tests, not browser rendering qualification.
 * Parse/serialize through Python's XML implementation; geometry/CSS are fixtures.
 * No third-party dependency, browser, chemistry engine or network is used. */
import {spawnSync} from 'node:child_process';
const NS='http://www.w3.org/2000/svg';
const parser=`import sys,json,xml.etree.ElementTree as E
try:
 def node(e):
  ns,local=e.tag[1:].split('}',1) if e.tag.startswith('{') else ('',e.tag)
  return {'name':local,'ns':ns,'attrs':list(e.attrib.items()),'text':e.text or '', 'children':[node(c) for c in e]}
 print(json.dumps(node(E.fromstring(sys.stdin.read()))))
except Exception: print('{}')`;
const escape=s=>String(s).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;').replaceAll('>','&gt;');
export function controlledDocument({bounds={x:0,y:0,width:100,height:50},measureFailure=false,serializeFailure=false}={}){
  const calls={parsed:0,measured:0,serialized:0};
  class Node{
    constructor(name,attrs={},ns=NS){this.localName=name;this.namespaceURI=ns;this.nodeType=name==='#fragment'?11:1;this.ownerDocument=doc;this.parentNode=null;this.childNodes=[];this.map=new Map(Object.entries(attrs));this.style=new Proxy({}, {get:(_t,k)=>this.declarations()[k]??'',set:(t,k,v)=>(t[k]=v,true)});}
    get children(){return this.childNodes.filter(n=>n.nodeType===1);}
    get id(){return this.getAttribute('id')??'';}set id(v){this.setAttribute('id',v);}
    get attributes(){return [...this.map].map(([name,value])=>({name,localName:name,namespaceURI:name==='xmlns'?'http://www.w3.org/2000/xmlns/':null,value}));}
    getAttribute(k){return this.map.get(k)??null;}setAttribute(k,v){this.map.set(k,String(v));}hasAttribute(k){return this.map.has(k);}removeAttribute(k){this.map.delete(k);}
    declarations(){return Object.fromEntries((this.getAttribute('style')??'').split(';').filter(Boolean).map(x=>{let i=x.indexOf(':');return [x.slice(0,i).trim(),x.slice(i+1).trim()];}));}
    append(...nodes){for(const n of nodes){if(n.nodeType===11){this.append(...[...n.childNodes]);continue;}n.remove();n.parentNode=this;this.childNodes.push(n);}}
    prepend(n){n.remove();n.parentNode=this;this.childNodes.unshift(n);}
    remove(){if(this.parentNode){const siblings=this.parentNode.childNodes;siblings.splice(siblings.indexOf(this),1);this.parentNode=null;}}
    querySelectorAll(selector){const matches=n=>selector==='*'||selector.split(',').some(s=>s==='[id]'?n.hasAttribute('id'):s.startsWith('linearGradient[')?n.localName==='linearGradient'&&n.getAttribute('data-nivram-bond-paint')==='1':n.localName===s);return this.children.flatMap(n=>[...(matches(n)?[n]:[]),...n.querySelectorAll(selector)]);}
    querySelector(s){return this.querySelectorAll(s)[0]??null;}
    getBBox(){calls.measured++;if(measureFailure)throw new Error('measure failure');return bounds;}
    cloneNode(deep){const n=new Node(this.localName,Object.fromEntries(this.map),this.namespaceURI);if(deep)n.append(...this.children.map(c=>c.cloneNode(true)));return n;}
  }
  function convert(o){const n=new Node(o.name,Object.fromEntries(o.attrs??[]),o.ns);n.append(...(o.children??[]).map(convert));return n;}
  function serialize(n){return `<${n.localName}${[...n.map].map(([k,v])=>` ${k}="${escape(v)}"`).join('')}>${n.children.map(serialize).join('')}</${n.localName}>`;}
  const doc={calls,createElementNS:(_ns,name)=>new Node(name),createDocumentFragment:()=>new Node('#fragment'),importNode:(n,deep)=>n.cloneNode(deep),getElementById:()=>null,
    defaultView:{DOMParser:class{parseFromString(source){calls.parsed++;const r=spawnSync('python',['-c',parser],{input:source,encoding:'utf8',maxBuffer:8_000_000});if(r.status!==0)throw new Error(r.stderr);const data=JSON.parse(r.stdout);const root=convert(data);return {documentElement:root,querySelector:()=>data.name?null:{}};}},
      XMLSerializer:class{serializeToString(n){calls.serialized++;if(serializeFailure)throw new Error('serialize failure');return serialize(n);}},
      getComputedStyle(n){const css=n.declarations();return {stroke:css.stroke??n.getAttribute('stroke')??'none',strokeWidth:css['stroke-width']??n.getAttribute('stroke-width')??'1',strokeLinejoin:css['stroke-linejoin']??'miter',strokeLinecap:css['stroke-linecap']??'butt',strokeMiterlimit:css['stroke-miterlimit']??'4'};}}};
  doc.body=new Node('body');return doc;
}
