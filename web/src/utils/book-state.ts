import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

export const hashFile = (file: string) => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const readJson = (file: string): any => { try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; } };
export function readBookState(root: string, book: string) {
  root = path.resolve(root);
  const output = path.join(root, 'output');
  const md = path.join(output, 'markdown', `${book}.md`);
  const pdf = path.join(root, 'input', `${book}.pdf`);
  const epub = path.join(output, 'epub', `${book}.epub`);
  const info = (file: string) => ({ exists: fs.existsSync(file), filename: path.basename(file),
    size: fs.existsSync(file) ? `${(fs.statSync(file).size / 1024).toFixed(1)} KB` : '0 KB' });
  const content = fs.existsSync(md) ? fs.readFileSync(md, 'utf8') : '';
  const mdHash = fs.existsSync(md) ? hashFile(md) : '';
  const extraction = readJson(path.join(output, `extraction_${book}.json`));
  const clean = readJson(path.join(output, `clean_status_${book}.json`));
  // Edits after cleaning preserve the milestone; a new extraction invalidates it.
  const cleaned = !!content.trim() && !!clean?.cleaned && (!extraction?.timestamp || Date.parse(clean.timestamp) >= Date.parse(extraction.timestamp));
  const reportFile = path.join(output, book === 'ksiazka' && !fs.existsSync(path.join(output, `audit_${book}.json`)) ? 'audit_report.json' : `audit_${book}.json`);
  const rawReport = readJson(reportFile);
  const paragraphs = content.split('\n\n');
  const reportValid = rawReport && Array.isArray(rawReport.anomalies) && rawReport.anomalies.every((a: any) => Number.isInteger(a?.paragraph_index) && a.paragraph_index >= 0 && a.paragraph_index < paragraphs.length);
  const reportState = !fs.existsSync(reportFile) ? 'missing' : !reportValid ? 'invalid' : !mdHash || rawReport.markdown_sha256 !== mdHash ? 'stale' : 'current';
  const report = reportState === 'current' ? rawReport : null;
  if (report?.anomalies) {
    report.anomalies = report.anomalies.map((a: any) => ({ ...a, raw_paragraph: paragraphs[a.paragraph_index]?.trim() || '' }));
  }
  const build = readJson(path.join(output, `build_${book}.json`));
  let current = false;
  try {
    current = !!build?.validated && fs.existsSync(epub) && build.epub_sha256 === hashFile(epub)
      && build.dependencies?.[md] === mdHash
      && Object.entries(build.dependencies).every(([file, hash]) => hash === null ? !fs.existsSync(file) : fs.existsSync(file) && hashFile(file) === hash);
    if (current && build.metadata_candidate) {
      const candidate = build.metadata_candidate;
      current = (fs.existsSync(candidate) ? hashFile(candidate) : null) === build.metadata_candidate_hash;
    }
    if (current) {
      const assetDir = path.join(output, 'markdown', 'assets');
      const walk = (dir: string): string[] => fs.existsSync(dir) ? fs.readdirSync(dir, {withFileTypes:true}).flatMap(e => e.isDirectory() ? walk(path.join(dir,e.name)) : [path.join(dir,e.name)]) : [];
      current = walk(assetDir).every(file => Object.hasOwn(build.dependencies, file));
    }
  } catch { current = false; }
  const usable = !!content.trim();
  const missing = report?.missing_chapters?.length || 0;
  const anomalies = report?.anomalies?.length || 0;
  const scope = extraction?.scope || 'unknown';
  const step = !usable ? 1 : !cleaned ? 2 : !report || anomalies || missing ? 3 : 4;
  const pdfMatches = scope === 'full' && fs.existsSync(pdf)
    && extraction.pdf_size === fs.statSync(pdf).size
    && Math.abs(extraction.pdf_mtime_ms - fs.statSync(pdf).mtimeMs) < 1;
  const ready = usable && cleaned && !!report && !anomalies && !missing && current && pdfMatches;
  return {
    book, pdfInfo: info(pdf), mdInfo: { ...info(md), usable, lines: content ? content.split('\n').length : 0 },
    epubInfo: { ...info(epub), current, validated: current, mtime: fs.existsSync(epub) ? fs.statSync(epub).mtime.toLocaleTimeString('pl-PL', { timeZone: 'Europe/Warsaw', hour: '2-digit', minute: '2-digit' }) : '' },
    cleanInfo: { cleaned, timestamp: clean?.timestamp || '' }, report, reportState,
    extractionInfo: { scope, pages: extraction?.pages || null },
    extractProgress: readJson(path.join(output, `extract_progress_${book}.json`)),
    workflow: { step, ready, anomalies, missingChapters: missing, coverage: scope, coverageVerified: pdfMatches, completenessChecked: report?.completeness_checked === true },
  };
}
