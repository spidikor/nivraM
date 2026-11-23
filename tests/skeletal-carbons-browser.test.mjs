import test from 'node:test';
import assert from 'node:assert/strict';
import {productionEditor, ready, state} from './editor-browser-fixture.mjs';

async function downloadBytes(page, button, extension){
  const [download]=await Promise.all([page.waitForEvent('download'), page.click(button)]);
  assert.ok(download.suggestedFilename().endsWith(extension));
  const stream=await download.createReadStream();
  assert.ok(stream);
  const chunks=[];
  for await(const chunk of stream)chunks.push(chunk);
  assert.equal(await download.failure(),null);
  await ready(page);
  return Buffer.concat(chunks);
}

async function artwork(page){
  return page.evaluate(()=>{
    const paths=[...document.querySelectorAll('#drawing path')];
    return {
      paths:paths.map(node=>node.getAttribute('d')),
      outlinedAtoms:paths.map(node=>node.getAttribute('class')).filter(name=>/^atom-\d+$/.test(name))
    };
  });
}

async function exportedArtwork(page, expectedPaths){
  const svg=await downloadBytes(page,'#export-svg-button','.svg');
  const paths=await page.evaluate(source=>{
    const svgDocument=new DOMParser().parseFromString(source,'image/svg+xml');
    if(svgDocument.documentElement.localName!=='svg'||svgDocument.querySelector('parsererror'))
      throw new Error('The downloaded artwork is not SVG.');
    return [...svgDocument.querySelectorAll('path')].map(node=>node.getAttribute('d'));
  },svg.toString('utf8'));
  assert.deepEqual(paths,expectedPaths,'The exported outlines and bond paths must match the editor.');

  const png=await downloadBytes(page,'#export-button','.png');
  const pixels=await page.evaluate(async data=>{
    const image=new Image();
    image.src=`data:image/png;base64,${data}`;
    await image.decode();
    const canvas=document.createElement('canvas');
    const {naturalWidth:width,naturalHeight:height}=image;
    canvas.width=width;
    canvas.height=height;
    const context=canvas.getContext('2d');
    context.drawImage(image,0,0);
    const rgba=context.getImageData(0,0,width,height).data;
    let ink=0;
    for(let i=0;i<rgba.length;i+=4){
      if(rgba[i+3]&&Math.min(rgba[i],rgba[i+1],rgba[i+2])<240)ink++;
    }
    const bytes=new Uint8Array(await crypto.subtle.digest('SHA-256',rgba));
    const sha256=Array.from(bytes,value=>value.toString(16).padStart(2,'0')).join('');
    return {width,height,ink,sha256};
  },png.toString('base64'));
  assert.equal(pixels.width,640);
  assert.ok(pixels.height>0&&pixels.ink>0,'The downloaded PNG must decode to visible artwork.');
  return pixels;
}

async function reopen(page, name, buffer){
  await page.locator('#file-input').setInputFiles({name:`${name}.nivram`,mimeType:'application/json',buffer});
  await page.waitForFunction(name=>document.getElementById('filename').value===name,name);
  await ready(page);
}

test('skeletal carbons follows Apply appearance, history, project readback and downloaded artwork',{timeout:120000},async t=>{
  const page=await productionEditor(t);
  const checkbox=page.getByRole('checkbox',{name:'Skeletal carbons',exact:true});
  assert.equal(await checkbox.getAttribute('id'),'skeletal-carbons');
  assert.equal(await checkbox.isChecked(),true);
  await page.click('#import-button');
  await page.fill('#structure-input','CCO');
  await page.click('#load-structure');
  await page.waitForFunction(()=>!document.getElementById('import-dialog').open);
  await ready(page);
  await page.fill('#export-width','640');
  await page.locator('#export-width').press('Tab');
  await ready(page);

  const baseline=await state(page);
  const skeletal=await artwork(page);
  assert.equal(baseline.document.style.skeletalCarbons,true);
  assert.deepEqual(baseline.document.graph.atoms.map(atom=>atom.symbol),['C','C','O']);
  assert.ok(skeletal.outlinedAtoms.includes('atom-2'),'The oxygen label remains visible.');
  assert.ok(!skeletal.outlinedAtoms.some(name=>name==='atom-0'||name==='atom-1'));
  const skeletalPixels=await exportedArtwork(page,skeletal.paths);
  assert.deepEqual(await state(page),baseline,'Exporting the drawing keeps its document and history state.');

  await checkbox.uncheck();
  assert.deepEqual(await state(page),baseline,'Changing the checkbox does not commit before Apply appearance.');
  assert.deepEqual(await artwork(page),skeletal);
  await page.click('#apply-style');
  await ready(page);
  const applied=await state(page);
  const labeled=await artwork(page);
  assert.equal(applied.revision,baseline.revision+1);
  assert.deepEqual(applied.document,{...baseline.document,style:{...baseline.document.style,skeletalCarbons:false}});
  for(const name of ['atom-0','atom-1','atom-2'])assert.ok(labeled.outlinedAtoms.includes(name),`Missing ${name} label.`);
  assert.notDeepEqual(labeled.paths,skeletal.paths);
  const labeledPixels=await exportedArtwork(page,labeled.paths);
  assert.notEqual(labeledPixels.sha256,skeletalPixels.sha256,'The PNG must render the changed appearance.');
  assert.deepEqual(await state(page),applied);

  await page.click('#undo-button');
  await ready(page);
  assert.equal(await checkbox.isChecked(),true);
  assert.deepEqual((await state(page)).document,baseline.document);
  assert.deepEqual(await artwork(page),skeletal);
  await page.click('#redo-button');
  await ready(page);
  assert.equal(await checkbox.isChecked(),false);
  assert.deepEqual((await state(page)).document,applied.document);
  assert.deepEqual(await artwork(page),labeled);

  const savedBytes=await downloadBytes(page,'#save-button','.nivram');
  const saved=JSON.parse(savedBytes.toString('utf8'));
  assert.deepEqual(saved,applied.document);
  const legacy=structuredClone(saved);
  delete legacy.style.skeletalCarbons;
  await reopen(page,'legacy-skeletal-carbons',Buffer.from(JSON.stringify(legacy)));
  assert.equal(await checkbox.isChecked(),true);
  assert.deepEqual((await state(page)).document,baseline.document);
  assert.deepEqual(await artwork(page),skeletal);
  await reopen(page,'saved-carbon-labels',savedBytes);
  assert.equal(await checkbox.isChecked(),false);
  assert.deepEqual((await state(page)).document,saved);
  assert.deepEqual(await artwork(page),labeled);
});
