import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

test('radical entry is separately labelled, bounded, restored and committed through existing transaction',async()=>{
  const html=await readFile(new URL('../public/index.html',import.meta.url),'utf8');
  const app=await readFile(new URL('../public/app.mjs',import.meta.url),'utf8');
  assert.match(html,/<label for="atom-radicals">Unpaired electrons \(radicals\)<\/label>/);
  assert.match(html,/<input id="atom-radicals" type="number" min="0" max="8" step="1" value="0" disabled aria-describedby="atom-hydrogen-help">/);
  assert.equal((app.match(/'atom-charge','atom-isotope','atom-radicals','apply-atom'/g)||[]).length,2);
  assert.ok(app.includes("$('atom-radicals').value=atom?.radicals||0;"));
  assert.ok(app.includes("a.radicals=$('atom-radicals').valueAsNumber;await change(g,{proposedEdit:{before:copy(graph),after:g}})"));
});
