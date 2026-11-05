// Synthetic microbenchmark: no production writes, conversion or network calls.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {performance} from 'node:perf_hooks';
import {readBookState,hashFile} from '../web/src/utils/book-state.ts';
const root=fs.mkdtempSync(path.join(os.tmpdir(),'epub-status-bench-'));
const rows=[];
try {
 for(const [name,mdMB,assetsMB] of [['small',0.1,0],['book',1,16],['large',8,64]]){
  const dir=path.join(root,name),out=path.join(dir,'output'),md=path.join(out,'markdown','fixture.md');
  fs.mkdirSync(path.dirname(md),{recursive:true});fs.mkdirSync(path.join(out,'epub'));
  fs.mkdirSync(path.join(out,'markdown','assets'));
  const paragraph='A paragraph of ordinary recipe text, with salt and flour.\n\n';
  fs.writeFileSync(md,paragraph.repeat(Math.ceil(mdMB*1024*1024/paragraph.length)));
  const deps={[md]:hashFile(md)};
  for(let i=0;i<assetsMB;i++){
   const file=path.join(out,'markdown','assets',`${i}.png`);fs.writeFileSync(file,Buffer.alloc(1024*1024,i));deps[file]=hashFile(file);
  }
  const epub=path.join(out,'epub','fixture.epub');fs.writeFileSync(epub,Buffer.alloc(1024*1024));
  fs.writeFileSync(path.join(out,'build_fixture.json'),JSON.stringify({validated:true,epub_sha256:hashFile(epub),dependencies:deps}));
  const before=process.cpuUsage();const times=[];
  for(let i=0;i<25;i++){const start=performance.now();readBookState(dir,'fixture');times.push(performance.now()-start);}
  times.sort((a,b)=>a-b);const cpu=process.cpuUsage(before);
  const timerStart=performance.now();const timer=new Promise(resolve=>setTimeout(()=>resolve(performance.now()-timerStart),0));readBookState(dir,'fixture');const timerDelay=await timer;
  rows.push({profile:name,markdownMB:mdMB,assetsMB,iterations:25,p50_ms:times[12],p95_ms:times[23],max_ms:times[24],cpu_ms:(cpu.user+cpu.system)/1000,timer_delay_during_one_read_ms:timerDelay});
 }
 const report={date:'2026-11-05',method:'25 sequential warm-cache function calls per synthetic profile; not HTTP latency, not concurrent load',node:process.version,cpu:os.cpus()[0]?.model,rows};
 fs.writeFileSync('docs/measurements/status-baseline-2026-11-05.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
} finally {fs.rmSync(root,{recursive:true,force:true});}
