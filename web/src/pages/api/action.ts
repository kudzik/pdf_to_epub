import type { APIRoute } from 'astro';
import { execFile, type ChildProcess } from 'child_process';
import fs from 'fs';
import path from 'path';
import { getProjectRootDir } from '../../utils/paths.ts';
import { acquireMutationLock } from '../../utils/mutation-lock.ts';
import { readBookState } from '../../utils/book-state.ts';
import { startJob, appendJobLog, finishJob, readJobs, type Job } from '../../utils/job-store.ts';
import { logger } from '../../lib/logger.ts';

// Rejestr aktywnych procesów w tle (zapobieganie wyścigom i umożliwienie anulowania)
const runningJobs = new Map<string, { pid: number; child: ChildProcess; startTime: number; action: string }>();

export const POST: APIRoute = async ({ request }) => {
  let releaseMutation: (() => void) | undefined;
  try {
    const {
      action,
      file,
      book,
      keepEpub = false,
      targetFolder = 'output',
      pages,
      ocrMode,
      ocrEngine = 'rapidocr',
      stream = false,
      overwrite = false,
    } = await request.json();
    const rootDir = getProjectRootDir();
    
    // Ustalanie nazwy książki (stem)
    const rawName = book || file || 'ksiazka';
    if (typeof rawName !== 'string' || path.basename(rawName) !== rawName || /[\x00-\x1f]/.test(rawName) || rawName.startsWith('-')) return Response.json({ error: 'Nieprawidłowa nazwa książki' }, { status: 400 });
    const bookStem = path.basename(rawName).replace(/\.(pdf|md|epub)$/, '');
    if (!bookStem || bookStem === '.' || bookStem === '..') return Response.json({ error: 'Pusta nazwa książki' }, { status: 400 });
    logger.info({ action, bookStem, stream }, 'Otrzymano żądanie wykonania akcji API');

    // Obsługa anulowania aktywnego zadania
    if (action === 'cancel') {
      const activeJob = runningJobs.get(bookStem) || runningJobs.get('*');
      if (!activeJob) {
        const stored = readJobs(rootDir, bookStem).activeJob;
        if (stored && (stored.book === bookStem || stored.action === 'purge-all')) {
          try { process.kill(-stored.pid, 'SIGTERM'); } catch { try { process.kill(stored.pid, 'SIGTERM'); } catch {} }
          await new Promise(resolve => setTimeout(resolve, 3000));
          try { process.kill(-stored.pid, 'SIGKILL'); } catch {}
          finishJob(rootDir,stored,false,'Zatrzymano na żądanie użytkownika');
          return Response.json({success:true});
        }
      }
      if (activeJob) {
        try {
          // Wysłanie SIGTERM, a jeśli proces żyje po 3s, SIGKILL
          process.kill(-activeJob.pid, 'SIGTERM');
        } catch {
          try {
            activeJob.child.kill('SIGTERM');
          } catch {}
        }
        const killTimer = setTimeout(() => {
          if (runningJobs.get(bookStem) === activeJob || runningJobs.get('*') === activeJob) {
            try { process.kill(-activeJob.pid, 'SIGKILL'); } catch { activeJob.child.kill('SIGKILL'); }
          }
        }, 3000);
        killTimer.unref();

        // Zapisanie statusu aborted do pliku postępu
        const progressPath = path.join(rootDir, 'output', `extract_progress_${bookStem}.json`);
        if (fs.existsSync(progressPath)) {
          try {
            fs.writeFileSync(progressPath, JSON.stringify({
              status: 'aborted',
              percent: 0,
              message: 'Operacja przerwana przez użytkownika',
              updated_at: new Date().toISOString(),
            }, null, 2));
          } catch {}
        }

        await new Promise<void>(resolve => {
          if (activeJob.child.exitCode !== null || activeJob.child.signalCode !== null) return resolve();
          activeJob.child.once('close', () => resolve());
        });
        return new Response(JSON.stringify({
          success: true,
          message: `Operacja "${activeJob.action}" dla ${bookStem} została przerwana.`
        }), { headers: { 'Content-Type': 'application/json' } });
      }

      return new Response(JSON.stringify({
        success: true,
        message: `Brak aktywnego zadania do przerwania dla ${bookStem}.`
      }), { headers: { 'Content-Type': 'application/json' } });
    }

    const mdPath = path.join(rootDir, 'output', 'markdown', `${bookStem}.md`);
    const epubPath = path.join(rootDir, 'output', 'epub', `${bookStem}.epub`);
    const pdfPath = path.join(rootDir, 'input', `${bookStem}.pdf`);

    if (runningJobs.has(bookStem) || runningJobs.has('*') || (action === 'purge-all' && runningJobs.size)) {
      return Response.json({ error: 'Inna operacja jest w toku' }, { status: 409 });
    }
    if (['clean', 'audit', 'build'].includes(action) && !fs.existsSync(mdPath)) {
      return Response.json({ error: 'Najpierw uruchom ekstrakcję' }, { status: 409 });
    }
    if (action === 'build' && !readBookState(rootDir, bookStem).report) return Response.json({ error: 'Najpierw wykonaj aktualny audyt' }, { status: 409 });
    if (action === 'extract' && !pages && fs.existsSync(mdPath) && overwrite !== true) {
      return Response.json({ error: 'Wymagane potwierdzenie zastąpienia Markdowna' }, { status: 409 });
    }
    let command: string[] = [];

    if (action === 'extract') {
      // Jeśli już trwa ekstrakcja tej samej książki, zasygnalizuj to
      if (runningJobs.has(bookStem)) {
        return new Response(JSON.stringify({
          success: false,
          error: `Ekstrakcja dla "${bookStem}" jest już w toku. Użyj przycisku Anuluj, jeśli chcesz ją przerwać.`
        }), { status: 409, headers: { 'Content-Type': 'application/json' } });
      }

      // Wyczyść stary plik postępu przed nowym uruchomieniem
      const progressPath = path.join(rootDir, 'output', `extract_progress_${bookStem}.json`);
      if (fs.existsSync(progressPath)) {
        try { fs.unlinkSync(progressPath); } catch {}
      }

      if (pages && (typeof pages !== 'string' || !/^\d+(?:-\d+)?$/.test(pages.trim()))) {
        return Response.json({ error: 'Nieprawidłowy zakres stron' }, { status: 400 });
      }
      if (pages) {
        const [first, last = first] = pages.trim().split('-').map(Number);
        if (first < 1 || last < first) return Response.json({ error: 'Nieprawidłowy zakres stron' }, { status: 400 });
      }
      command = ['run', 'python', '-m', 'src.pipeline', 'extract', '--book', bookStem];
      if (pages) command.push('--pages', pages.trim());
      if (['rapidocr', 'easyocr'].includes(ocrEngine)) ocrMode === undefined && command.push('--ocr');
      if (['native', 'no-ocr'].includes(ocrMode)) command.push('--no-ocr');
      if (['ocr', 'force'].includes(ocrMode)) command.push('--ocr');
      command.push('--ocr-engine', ['rapidocr', 'easyocr'].includes(ocrEngine) ? ocrEngine : 'rapidocr');
    } else if (['clean', 'audit', 'build'].includes(action)) {
      command = ['run', 'python', '-m', 'src.pipeline', action, '--book', bookStem];
      if (action === 'audit') command.push('--report', `output/audit_${bookStem}.json`);
      if (action === 'build') command.push('--no-copy-windows');
    } else if (action === 'purge') {
      command = ['run', 'python', '-m', 'src.pipeline', 'purge', '--book', bookStem, keepEpub === true ? '--keep-epub' : '--with-epub'];
    } else if (action === 'purge-epub') {
      command = ['run', 'python', '-m', 'src.pipeline', 'purge', '--book', bookStem, '--only-epub'];
    } else if (action === 'purge-all') {
      command = ['run', 'python', '-m', 'src.pipeline', 'purge', '--all', '--with-epub'];
    } else if (action === 'purge-temp') {
      command = ['run', 'python', '-m', 'src.pipeline', 'purge', '--temp-only'];
    } else if (action === 'copy-windows') {
      // Skopiuj EPUB bezpośrednio do Windows Downloads
      if (!readBookState(rootDir, bookStem).epubInfo.current) {
        return new Response(JSON.stringify({
          success: false,
          error: `Plik EPUB dla ${bookStem} nie został jeszcze wygenerowany.`
        }), { status: 404, headers: { 'Content-Type': 'application/json' } });
      }

      // Wyszukaj folder Downloads użytkownika Windows
      let winDownloads: string | null = null;
      const wslUsers = '/mnt/c/Users';
      if (fs.existsSync(wslUsers)) {
        for (const u of fs.readdirSync(wslUsers)) {
          if (!['Public', 'Default', 'Default User', 'All Users'].includes(u)) {
            const dl = path.join(wslUsers, u, 'Downloads');
            if (fs.existsSync(dl)) {
              winDownloads = dl;
              break;
            }
          }
        }
      }

      if (!winDownloads) {
        return new Response(JSON.stringify({
          success: false,
          error: 'Nie znaleziono folderu Pobrane w Windows (/mnt/c/Users/*/Downloads).'
        }), { status: 404, headers: { 'Content-Type': 'application/json' } });
      }

      const destPath = path.join(winDownloads, `${bookStem}.epub`);
      fs.copyFileSync(epubPath, destPath);

      return new Response(JSON.stringify({
        success: true,
        destPath,
        message: `Pomyślnie skopiowano do Windows Downloads: ${destPath}`
      }), { headers: { 'Content-Type': 'application/json' } });

    } else if (action === 'open-windows') {
      if (!['input', 'output', 'epub', 'markdown'].includes(targetFolder)) return Response.json({ error: 'Nieprawidłowy folder' }, { status: 400 });
      command = ['run', 'python', '-m', 'src.pipeline', 'open-windows', '--folder', targetFolder];
    } else {
      return new Response(JSON.stringify({ error: `Nieobsługiwana akcja: ${action}` }), { status: 400 });
    }

    const jobKey = action === 'purge-all' ? '*' : bookStem;
    releaseMutation = acquireMutationLock(rootDir);
    const startTime = Date.now();

    // Tryb strumieniowania wyjścia CLI w czasie rzeczywistym (NDJSON)
    if (stream === true) {
      const responseStream = new ReadableStream({
        start(controller) {
          const encoder = new TextEncoder();
          const send = (obj: any) => {
            try {
              controller.enqueue(encoder.encode(JSON.stringify(obj) + '\n'));
            } catch {}
          };

          const child = execFile('uv', command, {
            cwd: rootDir,
            timeout: 7_200_000,
            detached: process.platform !== 'win32',
            maxBuffer: 16 * 1024 * 1024,
            env: {
              ...process.env,
              PYTHONUNBUFFERED: "1",
              UV_CACHE_DIR: process.env.UV_CACHE_DIR || path.join(rootDir, ".uv-cache")
            }
          });

          const persistedJob = child.pid ? startJob(rootDir, bookStem, action, child.pid, startTime) : null;
          if (child.pid) {
            runningJobs.set(jobKey, {
              pid: child.pid,
              child,
              startTime,
              action,
            });
          }

          let stdoutBuf = '';
          let stderrBuf = '';

          child.stdout?.on('data', (chunk) => {
            const text = chunk.toString();
            stdoutBuf += text;
            if (persistedJob) appendJobLog(rootDir, persistedJob, text);
            send({ type: 'stdout', text });
          });

          child.stderr?.on('data', (chunk) => {
            const text = chunk.toString();
            stderrBuf += text;
            if (persistedJob) appendJobLog(rootDir, persistedJob, text);
            send({ type: 'stderr', text });
          });

          child.on('close', (code) => {
            if (persistedJob) finishJob(rootDir, persistedJob, code === 0, code === 0 ? '' : stderrBuf.slice(-2000));
            runningJobs.delete(jobKey);
            releaseMutation?.();
            const durationMs = Date.now() - startTime;
            logger.info({ action, bookStem, code, durationMs }, 'Zakończono proces potomny (stream)');
            send({
              type: 'done',
              success: code === 0,
              code,
              durationMs,
              stdout: stdoutBuf,
              stderr: stderrBuf,
              error: code === 0 ? null : (stderrBuf.trim() || `Proces zakończony z kodem błędu ${code}`)
            });
            try { controller.close(); } catch {}
          });

          child.on('error', (err) => {
            if (persistedJob) finishJob(rootDir, persistedJob, false, err.message);
            runningJobs.delete(jobKey);
            releaseMutation?.();
            send({ type: 'error', error: err.message });
            try { controller.close(); } catch {}
          });
        }
      });

      return new Response(responseStream, {
        headers: {
          'Content-Type': 'application/x-ndjson; charset=utf-8',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
        }
      });
    }

    // Wykonanie procesu z rejestracją w runningJobs i obsługą anulowania (fallback JSON)
    let persistedJob: Job | null = null;
    const execResult = await new Promise<{ stdout: string; stderr: string }>((resolve, reject) => {
      const child = execFile('uv', command, {
        cwd: rootDir,
        timeout: 7_200_000,
            detached: process.platform !== 'win32',
        maxBuffer: 16 * 1024 * 1024,
        env: {
          ...process.env,
          PYTHONUNBUFFERED: "1",
          UV_CACHE_DIR: process.env.UV_CACHE_DIR || path.join(rootDir, ".uv-cache")
        }
      }, (error, stdout, stderr) => {
        if (persistedJob) finishJob(rootDir,persistedJob,!error,error?.message || '');
        runningJobs.delete(jobKey);
        releaseMutation?.();
        if (error) {
          reject(error);
        } else {
          resolve({ stdout, stderr });
        }
      });

      if (child.pid) persistedJob = startJob(rootDir,bookStem,action,child.pid,startTime);
      child.stdout?.on('data', text => { if(persistedJob) appendJobLog(rootDir,persistedJob,String(text)); });
      child.stderr?.on('data', text => { if(persistedJob) appendJobLog(rootDir,persistedJob,String(text)); });
      if (child.pid) {
        runningJobs.set(jobKey, {
          pid: child.pid,
          child,
          startTime,
          action,
        });
      }
    });

    const durationMs = Date.now() - startTime;
    return new Response(JSON.stringify({ success: true, stdout: execResult.stdout, stderr: execResult.stderr, durationMs }), {
      headers: { 'Content-Type': 'application/json' },
    });
  } catch (error: any) {
    releaseMutation?.();
    logger.error({ error: error.message, stack: error.stack }, 'Nieobsługiwany błąd w akcji API');
    return new Response(JSON.stringify({ success: false, error: error.message }), {
      status: error.status || 500,
      headers: { 'Content-Type': 'application/json' },
    });
  }
};
