import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../src/pages/index.astro',import.meta.url),'utf8');
test('history request respects explicit book even when selector has changed',async()=>{
  const start=source.indexOf('    async function fetchHistoryStatuses(');
  const end=source.indexOf('    let observedJob',start);
  const urls=[];
  const context={bookSelector:{value:'beta'},fetch:async url=>{urls.push(url);return {json:async()=>({success:true,ledger:{records:[]}})};}};
  vm.createContext(context);vm.runInContext(source.slice(start,end),context);
  await context.fetchHistoryStatuses('alpha');
  assert.deepEqual(urls,['/api/history?book=alpha']);
});
