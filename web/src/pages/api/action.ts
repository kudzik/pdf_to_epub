import type { APIRoute } from 'astro';
import { exec, type ChildProcess } from 'child_process';
import fs from 'fs';
import path from 'path';
import { getProjectRootDir } from '../../utils/paths';

// Rejestr aktywnych procesów w tle (zapobieganie wyścigom i umożliwienie anulowania)
const runningJobs = new Map<string, { pid: number; child: ChildProcess; startTime: number; action: string }>();

export const POST: APIRoute = async ({ request }) => {
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
    } = await request.json();
    const rootDir = getProjectRootDir();
    
    // Ustalanie nazwy książki (stem)
    const rawName = book || file || 'ksiazka';
    const bookStem = path.basename(rawName).replace(/\.(pdf|md|epub)$/, '');

    // Obsługa anulowania aktywnego zadania
    if (action === 'cancel') {
      const activeJob = runningJobs.get(bookStem);
      if (activeJob) {
        try {
          // Wysłanie SIGTERM, a jeśli proces żyje po 1s, SIGKILL
          process.kill(-activeJob.pid, 'SIGTERM');
        } catch {
          try {
            activeJob.child.kill('SIGTERM');
          } catch {}
        }
        runningJobs.delete(bookStem);

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

    let command = '';

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

      const pagesFlag = (pages && typeof pages === 'string' && pages.trim()) ? ` --pages "${pages.trim()}"` : '';
      let ocrFlag = '';
      if (ocrMode === 'native' || ocrMode === 'no-ocr') {
        ocrFlag = ' --no-ocr';
      } else if (ocrMode === 'ocr' || ocrMode === 'force') {
        ocrFlag = ' --ocr';
      }
      const engineFlag = (ocrEngine && ['rapidocr', 'easyocr'].includes(ocrEngine)) ? ` --ocr-engine ${ocrEngine}` : ' --ocr-engine rapidocr';
      command = `uv run python -m src.pipeline extract --book "${bookStem}"${pagesFlag}${ocrFlag}${engineFlag}`;
    } else if (action === 'clean') {
      command = `uv run python -m src.pipeline clean --book "${bookStem}"`;
    } else if (action === 'audit') {
      command = `uv run python -m src.pipeline audit --book "${bookStem}" --report "output/audit_${bookStem}.json"`;
    } else if (action === 'build') {
      command = `uv run python -m src.pipeline build --book "${bookStem}"`;
    } else if (action === 'purge') {
      const epubFlag = (keepEpub === true) ? '--keep-epub' : '--with-epub';
      command = `uv run python -m src.pipeline purge --book "${bookStem}" ${epubFlag}`;
    } else if (action === 'purge-epub') {
      command = `uv run python -m src.pipeline purge --book "${bookStem}" --only-epub`;
    } else if (action === 'purge-all') {
      command = `uv run python -m src.pipeline purge --all --with-epub`;
    } else if (action === 'purge-temp') {
      command = `uv run python -m src.pipeline purge --temp-only`;
    } else if (action === 'copy-windows') {
      // Skopiuj EPUB bezpośrednio do Windows Downloads
      if (!fs.existsSync(epubPath)) {
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
      command = `uv run python -m src.pipeline open-windows --folder "${targetFolder}"`;
    } else {
      return new Response(JSON.stringify({ error: `Nieobsługiwana akcja: ${action}` }), { status: 400 });
    }

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

          const child = exec(command, {
            cwd: rootDir,
            timeout: 600_000,
            maxBuffer: 16 * 1024 * 1024,
            env: { ...process.env, UV_CACHE_DIR: process.env.UV_CACHE_DIR || path.join(rootDir, ".uv-cache") }
          });

          if (child.pid) {
            runningJobs.set(bookStem, {
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
            send({ type: 'stdout', text });
          });

          child.stderr?.on('data', (chunk) => {
            const text = chunk.toString();
            stderrBuf += text;
            send({ type: 'stderr', text });
          });

          child.on('close', (code) => {
            runningJobs.delete(bookStem);
            const durationMs = Date.now() - startTime;
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
            runningJobs.delete(bookStem);
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
    const execResult = await new Promise<{ stdout: string; stderr: string }>((resolve, reject) => {
      const child = exec(command, {
        cwd: rootDir,
        timeout: 600_000,
        maxBuffer: 16 * 1024 * 1024,
        env: { ...process.env, UV_CACHE_DIR: process.env.UV_CACHE_DIR || path.join(rootDir, ".uv-cache") }
      }, (error, stdout, stderr) => {
        runningJobs.delete(bookStem);
        if (error) {
          reject(error);
        } else {
          resolve({ stdout, stderr });
        }
      });

      if (child.pid) {
        runningJobs.set(bookStem, {
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
    return new Response(JSON.stringify({ success: false, error: error.message }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    });
  }
};
