import fs from 'node:fs';
import path from 'node:path';
import { readJobs } from './job-store.ts';

// A filesystem gate also covers separate HTTP workers and repair endpoints.
export function acquireMutationLock(root: string, direct = false): () => void {
  if (!direct && readJobs(root, '').activeJob) throw Object.assign(new Error('Inna operacja zapisu jest w toku'), { status: 423 });
  const lock = direct ? path.join(root, '.mutation.lock') : path.join(root, 'output', '.mutation.lock');
  fs.mkdirSync(path.dirname(lock), { recursive: true });
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const fd = fs.openSync(lock, 'wx');
      fs.writeFileSync(fd, String(process.pid));
      fs.closeSync(fd);
      let released = false;
      return () => { if (released) return; released = true; try { fs.unlinkSync(lock); } catch {} };
    } catch (error: any) {
      if (error.code !== 'EEXIST') throw error;
      let alive = true;
      try { process.kill(Number(fs.readFileSync(lock, 'utf8')), 0); }
      catch (e: any) { alive = e.code !== 'ESRCH'; }
      if (!alive) { fs.unlinkSync(lock); continue; }
      throw Object.assign(new Error('Inna operacja zapisu jest w toku'), { status: 423 });
    }
  }
  throw Object.assign(new Error('Nie można uzyskać blokady zapisu'), { status: 423 });
}
