import type { APIRoute } from 'astro';
import fs from 'fs';
import { createHash } from 'node:crypto';
import path from 'path';
import { getProjectRootDir } from '../../utils/paths';
import { probeOllama, getWindowsHostIp } from '../../utils/ollama';

export const GET: APIRoute = async ({ url }) => {
  const rootDir = getProjectRootDir();
  const fileParam = path.basename(url.searchParams.get('file') || url.searchParams.get('book') || 'ksiazka');
  const baseName = fileParam.replace(/\.(md|pdf|epub)$/, '');

  let reportPath = path.join(rootDir, 'output', `audit_${baseName}.json`);
  if (!fs.existsSync(reportPath) && baseName === 'ksiazka') {
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
    const markdownHash = createHash('sha256').update(content).digest('hex');
    if (report && report.markdown_sha256 !== markdownHash) report = null;
    if (report?.anomalies) {
      const paragraphs = content.split('\n\n');
      report.anomalies = report.anomalies.filter((a: any) => Number.isInteger(a.paragraph_index) && a.paragraph_index >= 0 && a.paragraph_index < paragraphs.length).map((a: any) => ({ ...a, raw_paragraph: paragraphs[a.paragraph_index].trim() }));
    }
    mdInfo = {
      exists: true,
      size: (stat.size / (1024 * 1024)).toFixed(1) + ' MB',
      lines: content.split('\n').length,
      filename: `${baseName}.md`,
    };
  }

  const epubCurrent = fs.existsSync(epubPath) && fs.existsSync(mdPath)
    && fs.statSync(epubPath).mtimeMs >= fs.statSync(mdPath).mtimeMs;

  // Sprawdzenie postępu ekstrakcji
  const progressPath = path.join(rootDir, 'output', `extract_progress_${baseName}.json`);
  let extractProgress = null;
  if (fs.existsSync(progressPath)) {
    try {
      extractProgress = JSON.parse(fs.readFileSync(progressPath, 'utf-8'));
    } catch (e) {}
  }

  // Sprawdzenie statusu czyszczenia Markdowna
  const cleanStatusPath = path.join(rootDir, 'output', `clean_status_${baseName}.json`);
  let cleanInfo = { cleaned: false, timestamp: '' };
  if (fs.existsSync(cleanStatusPath)) {
    try {
      const data = JSON.parse(fs.readFileSync(cleanStatusPath, 'utf-8'));
      cleanInfo = {
        cleaned: fs.existsSync(mdPath) && Boolean(data.cleaned)
          && fs.statSync(mdPath).mtimeMs <= Date.parse(data.timestamp),
        timestamp: data.timestamp || '',
      };
    } catch (e) {}
  }

  // Sprawdzenie dostępności lokalnego silnika Ollama (localhost oraz Windows Host IP)
  const ollama = await probeOllama();
  const windowsHostIp = getWindowsHostIp();

  return new Response(JSON.stringify({
    repairVersion: 'source-ocr-v1',
    book: baseName,
    pdfInfo,
    epubInfo: { ...epubInfo, current: epubCurrent },
    mdInfo,
    cleanInfo,
    report,
    extractProgress,
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
