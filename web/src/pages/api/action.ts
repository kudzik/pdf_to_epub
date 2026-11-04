import type { APIRoute } from 'astro';
import { exec } from 'child_process';
import fs from 'fs';
import path from 'path';
import util from 'util';
import { getProjectRootDir } from '../../utils/paths';

const execPromise = util.promisify(exec);

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
    } = await request.json();
    const rootDir = getProjectRootDir();
    
    // Ustalanie nazwy książki (stem)
    const rawName = book || file || 'ksiazka';
    const bookStem = path.basename(rawName).replace(/\.(pdf|md|epub)$/, '');

    const mdPath = path.join(rootDir, 'output', 'markdown', `${bookStem}.md`);
    const epubPath = path.join(rootDir, 'output', 'epub', `${bookStem}.epub`);
    const pdfPath = path.join(rootDir, 'input', `${bookStem}.pdf`);

    let command = '';

    if (action === 'extract') {
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
    const { stdout, stderr } = await execPromise(command, {
      cwd: rootDir,
      timeout: 600_000,
      maxBuffer: 16 * 1024 * 1024,
      env: { ...process.env, UV_CACHE_DIR: process.env.UV_CACHE_DIR || path.join(rootDir, ".uv-cache") }
    });
    const durationMs = Date.now() - startTime;
    return new Response(JSON.stringify({ success: true, stdout, stderr, durationMs }), {
      headers: { 'Content-Type': 'application/json' },
    });
  } catch (error: any) {
    return new Response(JSON.stringify({ success: false, error: error.message }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    });
  }
};
