import type { APIRoute } from 'astro';
import fs from 'fs';
import path from 'path';
import { getProjectRootDir } from '../../utils/paths';
import { probeOllama, getWindowsHostIp } from '../../utils/ollama';

export const GET: APIRoute = async ({ url }) => {
  const rootDir = getProjectRootDir();
  const fileParam = path.basename(url.searchParams.get('file') || url.searchParams.get('book') || 'ksiazka');
  const baseName = fileParam.replace(/\.(md|pdf|epub)$/, '');

  let reportPath = path.join(rootDir, 'output', `audit_${baseName}.json`);
  if (!fs.existsSync(reportPath)) {
    reportPath = path.join(rootDir, 'output', 'audit_report.json');
  }

  const epubPath = path.join(rootDir, 'output', 'epub', `${baseName}.epub`);
  const mdPath = path.join(rootDir, 'output', 'markdown', `${baseName}.md`);
  const pdfPath = path.join(rootDir, 'input', `${baseName}.pdf`);

  let report = null;
  if (fs.existsSync(reportPath)) {
    try {
      report = JSON.parse(fs.readFileSync(reportPath, 'utf-8'));
    } catch (e) {}
  }

  let pdfInfo = { exists: false, filename: `${baseName}.pdf`, size: '0 MB' };
  if (fs.existsSync(pdfPath)) {
    const stat = fs.statSync(pdfPath);
    pdfInfo = {
      exists: true,
      filename: `${baseName}.pdf`,
      size: (stat.size / (1024 * 1024)).toFixed(1) + ' MB',
    };
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

  let mdInfo = { exists: false, size: '0 MB', lines: 0, filename: `${baseName}.md` };
  if (fs.existsSync(mdPath)) {
    const stat = fs.statSync(mdPath);
    const content = fs.readFileSync(mdPath, 'utf-8');
    mdInfo = {
      exists: true,
      size: (stat.size / (1024 * 1024)).toFixed(1) + ' MB',
      lines: content.split('\n').length,
      filename: `${baseName}.md`,
    };
  }

  // Sprawdzenie dostępności lokalnego silnika Ollama (localhost oraz Windows Host IP)
  const ollama = await probeOllama();
  const windowsHostIp = getWindowsHostIp();

  return new Response(JSON.stringify({
    book: baseName,
    pdfInfo,
    epubInfo,
    mdInfo,
    report,
    ollama: {
      available: ollama.available,
      endpoint: ollama.endpoint,
      models: ollama.models,
      windowsHostIp,
    },
  }), {
    headers: { 'Content-Type': 'application/json' },
  });
};
