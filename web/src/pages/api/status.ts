import type { APIRoute } from 'astro';
import fs from 'fs';
import path from 'path';

export const GET: APIRoute = async ({ url }) => {
  const rootDir = path.resolve(process.cwd(), '..');
  const fileParam = url.searchParams.get('file') || 'ksiazka.md';
  const baseName = fileParam.replace(/\.(md|pdf)$/, '');

  const reportPath = path.join(rootDir, 'output', 'audit_report.json');
  const epubPath = path.join(rootDir, 'output', 'epub', `${baseName}.epub`);
  const mdPath = path.join(rootDir, 'output', 'markdown', fileParam.endsWith('.md') ? fileParam : `${baseName}.md`);

  let report = null;
  if (fs.existsSync(reportPath)) {
    try {
      report = JSON.parse(fs.readFileSync(reportPath, 'utf-8'));
    } catch (e) {}
  }

  let epubInfo = { exists: false, size: '0 MB', mtime: '', filename: `${baseName}.epub` };
  if (fs.existsSync(epubPath)) {
    const stat = fs.statSync(epubPath);
    epubInfo = {
      exists: true,
      size: (stat.size / (1024 * 1024)).toFixed(1) + ' MB',
      mtime: stat.mtime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      filename: `${baseName}.epub`,
    };
  }

  let mdInfo = { exists: false, size: '0 MB', lines: 0, filename: path.basename(mdPath) };
  if (fs.existsSync(mdPath)) {
    const stat = fs.statSync(mdPath);
    const content = fs.readFileSync(mdPath, 'utf-8');
    mdInfo = {
      exists: true,
      size: (stat.size / (1024 * 1024)).toFixed(1) + ' MB',
      lines: content.split('\n').length,
      filename: path.basename(mdPath),
    };
  }

  // Sprawdzenie dostępności lokalnego silnika Ollama na porcie 11434
  let ollamaAvailable = false;
  let ollamaModels: string[] = [];
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 600);
    const res = await fetch('http://localhost:11434/api/tags', { signal: controller.signal });
    clearTimeout(timeout);
    if (res.ok) {
      const data = await res.json();
      ollamaAvailable = true;
      ollamaModels = (data.models || []).map((m: any) => m.name);
    }
  } catch (e) {}

  return new Response(JSON.stringify({
    file: mdInfo.filename,
    report,
    epubInfo,
    mdInfo,
    ollama: {
      available: ollamaAvailable,
      models: ollamaModels,
    },
  }), {
    headers: { 'Content-Type': 'application/json' },
  });
};
