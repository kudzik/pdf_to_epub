import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { startJob, appendJobLog, finishJob, readJobs } from '../src/utils/job-store.ts';
import { acquireMutationLock } from '../src/utils/mutation-lock.ts';

test('disk state restores job, start time and logs independently of HTTP connection', t => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'epub-jobs-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const job=startJob(root,'book','extract',process.pid,Date.now()-5000);
  appendJobLog(root,job,'page 1\npage 2\n');
  const restored=readJobs(root,'book');
  assert.equal(restored.activeJob.id,job.id);
  assert.equal(restored.job.startTime,job.startTime);
  assert.match(restored.logTail,/page 2/);
  assert.throws(()=>acquireMutationLock(root),/operacja/);
  finishJob(root,job,true);
  assert.equal(readJobs(root,'book').activeJob,null);
  assert.equal(readJobs(root,'book').job.status,'succeeded');
  const release=acquireMutationLock(root); release();
});
test('dead process becomes interrupted; reading an old job cannot replace a newer active job', async t => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'epub-dead-job-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const child=spawn('/bin/sleep',['10']);
  await once(child,'spawn');
  const old=startJob(root,'old','extract',child.pid);
  child.kill(); await once(child,'close');
  const current=startJob(root,'new','clean',process.pid);
  assert.equal(readJobs(root,'old').job.status,'interrupted');
  assert.equal(readJobs(root,'new').activeJob.id,current.id);
  finishJob(root,current,true);
});
