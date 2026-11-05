import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { POST } from '../src/pages/api/action.ts';
import { acquireMutationLock } from '../src/utils/mutation-lock.ts';

test('API rejects invalid ranges, implicit extraction and unconfirmed overwrite without launching commands', async t => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'epub-api-'));
  const cwd=process.cwd();
  fs.mkdirSync(path.join(root,'output','markdown'),{recursive:true});
  process.chdir(root);
  t.after(()=>{process.chdir(cwd);fs.rmSync(root,{recursive:true,force:true});});
  const request = body => POST({ request: new Request('http://fixture/api/action', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}) });
  assert.equal((await request({action:'clean',book:'missing'})).status,409);
  assert.equal((await request({action:'extract',book:'../escape'})).status,400);
  assert.equal((await request({action:'extract',book:'fixture',pages:'1; touch file'})).status,400);
  assert.equal((await request({action:'extract',book:'fixture',pages:'8-2'})).status,400);
  fs.writeFileSync(path.join(root,'output','markdown','fixture.md'),'Corrected.');
  assert.equal((await request({action:'extract',book:'fixture'})).status,409);
  assert.equal(fs.readFileSync(path.join(root,'output','markdown','fixture.md'),'utf8'),'Corrected.');
});
test('filesystem mutation lock excludes competing writers and releases exactly once', t => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'epub-lock-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const release=acquireMutationLock(root);
  assert.throws(()=>acquireMutationLock(root),/operacja/);
  release();
  const next=acquireMutationLock(root);
  release(); // must not delete the next owner's lock
  assert.throws(()=>acquireMutationLock(root),/operacja/);
  next();
});

test('streamed jobs survive a disconnected reader and OCR selection has distinct arguments', async t => {
  const { readJobs }=await import('../src/utils/job-store.ts');
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'epub-run-job-'));
  const cwd=process.cwd(), oldPath=process.env.PATH;
  fs.mkdirSync(path.join(root,'output','markdown'),{recursive:true});
  const bin=path.join(root,'bin'); fs.mkdirSync(bin);
  fs.writeFileSync(path.join(bin,'uv'), `#!/bin/sh
printf '%s\\n' "$@"
sleep 0.15
`, {mode:0o755});
  process.chdir(root); process.env.PATH=`${bin}:${oldPath}`;
  t.after(()=>{process.chdir(cwd);process.env.PATH=oldPath;fs.rmSync(root,{recursive:true,force:true});});
  for (const engine of ['auto','rapidocr','easyocr','native']) {
    const body={action:'extract',book:'fixture',pages:'1-2',ocrEngine:engine,stream:true,...(engine==='native'?{ocrMode:'native'}:{})};
    const response=await POST({request:new Request('http://fixture/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    assert.equal(response.status,200);
    const initial=readJobs(root,'fixture');
    assert.equal(initial.activeJob.action,'extract');
    await response.body.cancel(); // simulates closing/reloading the browser
    for(let i=0;i<100 && readJobs(root,'fixture').activeJob;i++) await new Promise(resolve=>setTimeout(resolve,20));
    const restored=readJobs(root,'fixture');
    assert.equal(restored.job.status,'succeeded');
    assert.ok(restored.logTail, JSON.stringify({restored, files:fs.readdirSync(path.join(root,'output','jobs')), script:fs.readFileSync(path.join(bin,'uv'),'utf8')}));
    const args=restored.logTail.trim().split('\n');
    assert.equal(args.includes('--ocr'),['rapidocr','easyocr'].includes(engine));
    assert.equal(args.includes('--no-ocr'),engine==='native');
  }
});
