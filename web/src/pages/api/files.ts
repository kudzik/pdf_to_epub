import type { APIRoute } from 'astro';
import fs from 'fs';
import { readBookState } from '../../utils/book-state';
import path from 'path';
import { getProjectRootDir } from '../../utils/paths';

export const GET: APIRoute = async () => {
  const rootDir = getProjectRootDir();
  const mdDir = path.join(rootDir, 'output', 'markdown');
  const epubDir = path.join(rootDir, 'output', 'epub');
  const inputDir = path.join(rootDir, 'input');

  let mdFiles: string[] = [];
  let pdfFiles: string[] = [];
  let epubFiles: string[] = [];

  if (fs.existsSync(mdDir)) {
    mdFiles = fs.readdirSync(mdDir).filter(f => f.endsWith('.md'));
  }
  if (fs.existsSync(inputDir)) {
    pdfFiles = fs.readdirSync(inputDir).filter(f => f.endsWith('.pdf'));
  }
  if (fs.existsSync(epubDir)) {
    epubFiles = fs.readdirSync(epubDir).filter(f => f.endsWith('.epub'));
  }

  // Agregacja unikalnych książek (stems) ze wszystkich katalogów
  const bookStems = new Set<string>();
  pdfFiles.forEach(f => bookStems.add(f.replace(/\.pdf$/, '')));
  mdFiles.forEach(f => bookStems.add(f.replace(/\.md$/, '')));
  epubFiles.forEach(f => bookStems.add(f.replace(/\.epub$/, '')));

  const books = Array.from(bookStems).sort().map(stem => {
    const hasPdf = fs.existsSync(path.join(inputDir, `${stem}.pdf`));
    const hasMd = fs.existsSync(path.join(mdDir, `${stem}.md`));
    const hasEpub = fs.existsSync(path.join(epubDir, `${stem}.epub`));

    const state = readBookState(rootDir, stem);
    let status = 'Wymaga ekstrakcji PDF';
    if (state.workflow.ready) {
      status = 'Gotowy EPUB 3';
    } else if (hasMd) {
      status = 'Znaleziono istniejący Markdown';
    }

    return {
      stem,
      hasPdf,
      hasMd,
      hasEpub,
      ready: state.workflow.ready,
      status,
    };
  });

  // Sprawdzenie dostępności Windows Downloads w WSL
  let winDownloadsPath: string | null = null;
  const wslUsersDir = '/mnt/c/Users';
  if (fs.existsSync(wslUsersDir)) {
    try {
      const userFolders = fs.readdirSync(wslUsersDir);
      for (const u of userFolders) {
        if (!['Public', 'Default', 'Default User', 'All Users'].includes(u)) {
          const dl = path.join(wslUsersDir, u, 'Downloads');
          if (fs.existsSync(dl)) {
            winDownloadsPath = dl;
            break;
          }
        }
      }
    } catch (e) {}
  }

  return new Response(JSON.stringify({
    books,
    mdFiles,
    pdfFiles,
    epubFiles,
    windowsDownloads: winDownloadsPath,
  }), {
    headers: { 'Content-Type': 'application/json' },
  });
};
