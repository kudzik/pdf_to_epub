import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

export interface Job { id: string; book: string; action: string; pid: number; startTime: number; status: string; finishedAt?: number; error?: string; }
const folder = (root: string) => path.join(root, 'output', 'jobs');
const latest = (root: string, book: string) => path.join(folder(root), `book_${encodeURIComponent(book)}.json`);
function write(file: string, value: unknown) {
  fs.mkdirSync(path.dirname(file), {recursive:true});
  const temp = `${file}.${randomUUID()}.tmp`;
  try { fs.writeFileSync(temp,JSON.stringify(value)); fs.renameSync(temp,file); }
  finally { fs.rmSync(temp,{force:true}); }
}
function read(file: string): Job | null { try { return JSON.parse(fs.readFileSync(file,'utf8')); } catch { return null; } }
function save(root: string, job: Job) {
  write(latest(root,job.book), job);
  const activeFile=path.join(folder(root),'active.json');
  if(read(activeFile)?.id===job.id) write(activeFile,job);
}
export function startJob(root: string, book: string, action: string, pid: number, startTime = Date.now()): Job {
  const job: Job = { id:randomUUID(), book, action, pid, startTime, status:'running' };
  save(root,job); write(path.join(folder(root),'active.json'),job); return job;
}
export function appendJobLog(root: string, job: Job, text: string) {
  fs.appendFileSync(path.join(folder(root),`${job.id}.log`),text);
}
export function finishJob(root: string, job: Job, success: boolean, error = '') {
  job.status = success ? 'succeeded' : 'failed'; job.finishedAt=Date.now(); job.error=error;
  save(root,job);
}
function reconcile(root: string, job: Job | null): Job | null {
  if (job?.status === 'running') {
    try { process.kill(job.pid,0); }
    catch (error: any) { if (error.code==='ESRCH') { job.status='interrupted'; job.finishedAt=Date.now(); job.error='Proces zakończył się bez potwierdzenia wyniku'; save(root,job); } }
  }
  return job;
}
export function readJobs(root: string, book: string) {
  const active = reconcile(root,read(path.join(folder(root),'active.json')));
  const job = reconcile(root,read(latest(root,book)));
  let logTail='';
  if(job) {
    const file=path.join(folder(root),`${job.id}.log`);
    if(fs.existsSync(file)) {
      const fd=fs.openSync(file,'r');
      try { const size=fs.fstatSync(fd).size; const data=Buffer.alloc(Math.min(size,32000)); fs.readSync(fd,data,0,data.length,Math.max(0,size-data.length)); logTail=data.toString('utf8'); }
      finally { fs.closeSync(fd); }
    }
  }
  return {activeJob:active?.status==='running'?active:null, job, logTail};
}
