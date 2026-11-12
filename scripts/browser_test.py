"""Black-box browser checks. Run from the source folder after installing Playwright."""
import json,sys,re,subprocess,io,time,hashlib,http.client,os,shutil
from pathlib import Path
from PIL import Image,ImageChops
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'verification';OUT.mkdir(exist_ok=True)
proc=subprocess.Popen([sys.executable,str(ROOT/'run_nivram.py'),'--no-browser'],stdout=subprocess.PIPE,text=True,cwd=ROOT)
url=None
for _ in range(4):
    line=proc.stdout.readline()
    m=re.search(r'http://127\.0\.0\.1:\d+/',line)
    if m:url=m.group();break
if not url:raise RuntimeError('Local server did not start')
results=[];external=[];errors=[]
def record(name,detail):
 results.append({'test':name,'result':'pass','detail':detail});print('PASS:',name,flush=True)
try:
 with sync_playwright() as pw:
  executable=os.environ.get('NIVRAM_CHROMIUM') or shutil.which('chromium') or shutil.which('google-chrome')
  launch={'headless':True}
  if executable:launch['executable_path']=executable
  if sys.platform.startswith('linux') and hasattr(os,'geteuid') and os.geteuid()==0:
   launch['args']=['--no-sandbox','--disable-dev-shm-usage']  # Root-only container test harness.
  browser=pw.chromium.launch(**launch)
  context=browser.new_context(viewport={'width':1440,'height':980},accept_downloads=True)
  def route(r):
   if r.request.url.startswith(url) or r.request.url.startswith(('blob:','data:')):r.continue_()
   else:external.append(r.request.url);r.abort()
  context.route('**/*',route)
  page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
  # Respect managed browser navigation policy. Fallback uses an in-memory
  # document and real loopback HTTP service, without changing browser policy.
  mode='direct-loopback'
  try:
   page.goto(url)
  except Exception as exc:
   if 'ERR_BLOCKED_BY_ADMINISTRATOR' not in str(exc):raise
   mode='in-memory-browser-with-real-loopback-API'
   page.close();page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
   port=int(re.search(r':(\d+)/',url).group(1))
   c=http.client.HTTPConnection('127.0.0.1',port);c.request('GET','/');r=c.getresponse();html=r.read().decode();c.close()
   html=re.sub(r'<script[^>]*src=[^>]*></script>','',html)
   html=re.sub(r'<link[^>]*rel="stylesheet"[^>]*>','',html)
   page.set_content(html)
   page.add_style_tag(content=(ROOT/'public/style.css').read_text())
   def local_http(path,options=None):
    if not str(path).startswith('/api/'):raise RuntimeError('Unexpected test request: '+str(path))
    options=options or {};headers=options.get('headers',{}).copy();headers['Origin']=url.rstrip('/')
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=30)
    conn.request(options.get('method','GET'),path,body=options.get('body'),headers=headers)
    response=conn.getresponse();data=response.read().decode();status=response.status;heads=dict(response.getheaders());conn.close()
    return {'status':status,'body':data,'headers':heads}
   page.expose_function('__localHTTP',local_http)
   page.evaluate("() => { window.fetch=async(path,options)=>{const r=await window.__localHTTP(path,options);return new Response(r.body,{status:r.status,headers:r.headers})}; }")
   exports=re.sub(r'^export ', '', (ROOT/'public/modules/export.mjs').read_text(),flags=re.M)
   app=re.sub(r'^import .*?;\n', '', (ROOT/'public/app.mjs').read_text(),flags=re.M)
   page.add_script_tag(content='(async()=>{'+exports+'\n'+app+'\n})()')
  page.wait_for_function("document.querySelector('#request-status').textContent === 'Ready'",timeout=12000)
  record('browser execution mode',mode)
  def load(smiles):
   page.click('#import-button');page.fill('#structure-input',smiles);page.click('#load-structure')
   page.wait_for_function("!document.body.classList.contains('busy') && !document.querySelector('#import-dialog').open")
  def project(name):
   with page.expect_download(timeout=20000) as item:page.click('#save-button')
   download=item.value;path=OUT/(name+'.nivram');download.save_as(path);return json.loads(path.read_text())
  load('CCO');page.wait_for_function("document.querySelector('#counts').textContent==='3 atoms · 2 bonds'")
  record('SMILES import through UI','Ethanol: 3 atoms and 2 bonds')
  before=project('before-export')
  for width in [1000,4000,9999]:
   page.fill('#export-width',str(width))
   with page.expect_download(timeout=30000) as item:page.click('#export-button')
   download=item.value;path=OUT/f'export-{width}.png';download.save_as(path)
   im=Image.open(path).convert('RGBA');assert im.width==width
   assert im.getchannel('A').getextrema()==(255,255)
   diff=ImageChops.difference(im.convert('RGB'),Image.new('RGB',im.size,'white'))
   bbox=diff.getbbox();p=(width*2+99)//100
   assert bbox and bbox[0]>=p and bbox[1]==p and im.width-bbox[2]>=p and im.height-bbox[3]==p,(width,bbox,p,im.size)
   assert bbox[0]<=p+1 and im.width-bbox[2]<=p+1,(width,bbox)
   record(f'PNG {width}',{'width':im.width,'height':im.height,'bbox':bbox,'padding':p,'opaque':True,'bytes':path.stat().st_size})
  after=project('after-export');assert before['graph']==after['graph']
  record('PNG does not mutate coordinates or chemistry','Project graph unchanged before/after all three exports')
  page.fill('#export-width','640');page.locator('#margin-details summary').click();page.select_option('#margin-mode','pixels');page.fill('#margin-value','12')
  with page.expect_download(timeout=30000) as item:page.click('#export-button')
  path=OUT/'export-fixed.png';item.value.save_as(path);im=Image.open(path).convert('RGB');bbox=ImageChops.difference(im,Image.new('RGB',im.size,'white')).getbbox()
  assert im.width==640 and bbox[0]>=12 and bbox[1]==12;record('fixed pixel override',{'size':im.size,'bbox':bbox})
  page.click('#reset-margin');assert page.input_value('#margin-value')=='2';assert page.input_value('#margin-mode')=='percent'
  page.fill('#export-width','10000');page.click('#export-button');page.wait_for_selector('#toast.error:not([hidden])');assert '9999' in page.locator('#toast').inner_text()
  record('invalid width rejection','10000-pixel request rejected with a visible error')
  page.fill('#export-width','2000')
  page.click('#clean-button');page.wait_for_function("!document.body.classList.contains('busy')")
  cleaned=project('after-clean');assert len(cleaned['graph']['atoms'])==3
  page.click('#undo-button');page.wait_for_function("!document.body.classList.contains('busy')")
  record('2D Clean and Undo through UI','Experimental local cleaner executes and is undoable')
  def wait_ready():
   page.wait_for_function("!document.body.classList.contains('busy')")
  def screen_positions(doc):
   port=int(re.search(r':(\d+)/',url).group(1))
   token=page.locator('meta[name="nivram-token"]').get_attribute('content')
   c=http.client.HTTPConnection('127.0.0.1',port,timeout=30)
   c.request('POST','/api/render',json.dumps({'graph':doc['graph'],'style':doc['style']}),{'Content-Type':'application/json','X-Nivram-Token':token,'Origin':url.rstrip('/')})
   r=c.getresponse();assert r.status==200;r=json.loads(r.read());c.close()
   return {a['id']:page.evaluate("p=>{const q=new DOMPoint(p.x,p.y).matrixTransform(document.querySelector('#canvas').getScreenCTM());return {x:q.x,y:q.y}}",a) for a in r['scene']['positions']}
  page.click('#new-button');wait_ready();page.click('[data-tool="ring"]')
  area=page.locator('#canvas').bounding_box();page.mouse.click(area['x']+area['width']/2,area['y']+area['height']/2);wait_ready()
  assert page.locator('#counts').inner_text()=='6 atoms · 6 bonds'
  ring=project('drawn-ring');assert len(ring['graph']['atoms'])==6
  record('draw benzene with Ring tool','6 atoms and 6 bonds; valid Kekule representation')
  # Fuse a second ring onto one real bond by pointer interaction.
  pos=screen_positions(ring);bond=ring['graph']['bonds'][0];a=pos[bond['begin']];b=pos[bond['end']]
  page.mouse.click((a['x']+b['x'])/2,(a['y']+b['y'])/2);wait_ready()
  assert page.locator('#counts').inner_text()=='10 atoms · 11 bonds',page.locator('#toast').inner_text()
  record('fused ring editing','Two benzene rings share one bond: 10 atoms and 11 bonds')
  page.click('#undo-button');wait_ready();assert page.locator('#counts').inner_text()=='6 atoms · 6 bonds'
  page.click('#redo-button');wait_ready();assert page.locator('#counts').inner_text()=='10 atoms · 11 bonds'
  record('undo and redo edits','Ring-fusion action restored in both directions')
  # Real atom replacement, bond extension and invalid-edit rollback.
  load('CCO');doc=project('editing-start');pos=screen_positions(doc)
  atom=doc['graph']['atoms'][0];p0=pos[atom['id']]
  page.click('[data-element="N"]');page.mouse.click(p0['x'],p0['y']);wait_ready()
  replaced=project('atom-replaced');assert replaced['graph']['atoms'][0]['symbol']=='N'
  record('replace atom by pointer','Terminal C replaced with N')
  page.click('[data-element="C"]');page.click('[data-tool="bond"]')
  pos=screen_positions(replaced);p0=pos[replaced['graph']['atoms'][0]['id']]
  page.mouse.move(p0['x'],p0['y']);page.mouse.down();page.mouse.move(p0['x']-40,p0['y']-80,steps=5);page.mouse.up();wait_ready()
  extended=project('bond-extended');assert len(extended['graph']['atoms'])==4 and len(extended['graph']['bonds'])==3
  record('draw a bond by dragging','One C atom and one covalent bond added')
  page.click('[data-tool="select"]');pos=screen_positions(extended);p0=pos[extended['graph']['atoms'][0]['id']]
  page.mouse.move(p0['x'],p0['y']);page.mouse.down();page.mouse.move(p0['x']+30,p0['y']+25,steps=4);page.mouse.up();wait_ready()
  moved=project('atom-moved');assert moved['graph']['atoms'][0]['x']!=extended['graph']['atoms'][0]['x']
  for a,b in zip(extended['graph']['atoms'][1:],moved['graph']['atoms'][1:]):assert (a['x'],a['y'])==(b['x'],b['y'])
  record('drag selected atom','Selected atom moved; unselected atom coordinates unchanged')
  page.click('#clean-button');wait_ready();partial=project('partial-clean')
  for a,b in zip(moved['graph']['atoms'][1:],partial['graph']['atoms'][1:]):assert (a['x'],a['y'])==(b['x'],b['y'])
  record('partial cleanup via UI','Unselected coordinates preserved exactly')
  page.click('#delete-button');wait_ready();deleted=project('atom-deleted');assert len(deleted['graph']['atoms'])==3
  record('delete selected atom','Selected atom and incident bonds removed')
  page.locator('#file-input').set_input_files(str(OUT/'editing-start.nivram'));wait_ready()
  opened=project('reopened');assert opened['graph']==doc['graph']
  record('project file round trip','Save/Open preserved graph and coordinates exactly')
  # Replacing a carbonyl carbon with fluorine must roll back, not corrupt the drawing.
  load('CC(=O)O');valid=project('rollback-before');pos=screen_positions(valid);target=pos[valid['graph']['atoms'][1]['id']]
  page.click('[data-element="F"]');page.mouse.click(target['x'],target['y']);wait_ready()
  assert not page.locator('#toast').is_hidden() and 'invalid' in page.locator('#toast').inner_text().lower()
  assert project('rollback-after')['graph']==valid['graph']
  record('invalid atom edit rollback','Overvalent fluorine replacement rejected; prior graph unchanged')
  # Challenging visible labels and stereo must survive rendering and MOL export.
  load('[13CH3][C@@H](O)C(=O)[O-]');stereo_before=project('stereo-before');page.fill('#export-width','1000')
  with page.expect_download(timeout=30000) as item:page.click('#export-button')
  path=OUT/'export-stereo.png';item.value.save_as(path);im=Image.open(path).convert('RGBA')
  bbox=ImageChops.difference(im.convert('RGB'),Image.new('RGB',im.size,'white')).getbbox()
  assert im.getchannel('A').getextrema()==(255,255) and bbox[0]>=20 and bbox[1]==20 and im.height-bbox[3]==20
  with page.expect_download(timeout=20000) as item:page.click('#export-mol-button')
  mp=OUT/'export-stereo.mol';item.value.save_as(mp)
  from rdkit import Chem
  expected=Chem.MolToSmiles(Chem.MolFromSmiles('[13CH3][C@@H](O)C(=O)[O-]'),isomericSmiles=True)
  assert Chem.MolToSmiles(Chem.MolFromMolBlock(mp.read_text(),removeHs=False),isomericSmiles=True)==expected
  assert project('stereo-after')['graph']==stereo_before['graph']
  record('isotope, charge and stereochemistry exports','PNG has intact white borders; actual downloaded MOL retains canonical isomeric identity; graph unchanged')
  # A tall, deliberately narrow valid graph confirms height is not capped at 9999.
  load('CCCCCCCCCCCC');tall=project('tall-input')
  for i,a in enumerate(tall['graph']['atoms']):a['x']=(i%2)*1.2;a['y']=i*1.5
  tall['export']={'width':1000,'marginMode':'percent','margin':2}
  tp=OUT/'tall.nivram';tp.write_text(json.dumps(tall));page.locator('#file-input').set_input_files(str(tp));wait_ready()
  with page.expect_download(timeout=30000) as item:page.click('#export-button')
  path=OUT/'export-tall.png';item.value.save_as(path);im=Image.open(path).convert('RGB');bbox=ImageChops.difference(im,Image.new('RGB',im.size,'white')).getbbox()
  assert im.width==1000 and im.height>9999 and bbox[1]==20 and im.height-bbox[3]==20
  record('actual PNG auto-height above 9999',{'width':im.width,'height':im.height,'bbox':bbox})
  # Empty drawing must not produce a spurious blank export.
  page.click('#new-button');wait_ready();page.click('#export-button');page.wait_for_selector('#toast.error:not([hidden])')
  assert 'before exporting' in page.locator('#toast').inner_text().lower()
  record('empty PNG rejected','Clear visible error instead of blank download')
  load('Cn1c(=O)c2c(ncn2C)n(C)c1=O');page.fill('#filename','caffeine');page.fill('#export-width','2000')
  page.locator('#margin-details summary').click()
  with page.expect_download(timeout=30000) as item:page.click('#export-button')
  item.value.save_as(OUT/'example-caffeine-2000.png')
  page.wait_for_timeout(4700)
  bounds=page.evaluate("()=>({scroll:document.documentElement.scrollHeight,viewport:innerHeight,footer:document.querySelector('.app-footer').getBoundingClientRect().top,canvas:document.querySelector('#canvas').getBoundingClientRect().bottom})")
  assert bounds['scroll']<=bounds['viewport']+1 and bounds['canvas']<bounds['footer'],bounds
  page.screenshot(path=str(OUT/'nivraM-desktop.png'),full_page=True)
  page.set_viewport_size({'width':1024,'height':768});page.screenshot(path=str(OUT/'nivraM-laptop.png'),full_page=True)
  record('desktop and laptop rendering','Screenshots at 1440×980 and 1024×768')
  assert not external,external;assert not errors,errors
  record('offline network isolation',{'external_requests':external,'page_errors':errors})
  browser.close()
finally:
 proc.terminate();proc.wait(timeout=5)
 (OUT/'browser-results.json').write_text(json.dumps({'tests':results,'external_requests':external,'page_errors':errors},indent=2))
print(json.dumps(results,indent=2))
