import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, '..', '..');

test('index.astro: includes Step 1 range controls, OCR engine selector, and Scan Viewer Modal', () => {
  const indexPath = path.join(rootDir, 'web', 'src', 'pages', 'index.astro');
  const content = fs.readFileSync(indexPath, 'utf-8');

  // Step 1 controls
  assert.ok(content.includes('id="extractRangeSelect"'), 'Must contain page range selector');
  assert.ok(content.includes('value="1-10"'), 'Must have 1-10 quick test range option');
  assert.ok(content.includes('id="extractCustomPages"'), 'Must have custom page range input');
  assert.ok(content.includes('id="extractOcrEngine"'), 'Must have OCR engine selector');
  assert.ok(content.includes('RapidOCR'), 'Must offer RapidOCR engine option');

  // High-res Scan Viewer Modal
  assert.ok(content.includes('id="scanViewerModal"'), 'Must include scanViewerModal');
  assert.ok(content.includes('id="scanModalViewport"'), 'Must include interactive viewport');
  assert.ok(content.includes('id="btnModalZoomIn"'), 'Must include zoom in button');
  assert.ok(content.includes('id="btnModalZoomOut"'), 'Must include zoom out button');
  assert.ok(content.includes('id="btnModalClose"'), 'Must include close button');
  assert.ok(content.includes('id="scanModalPageIndicator"'), 'Must include modal page indicator');

  // Script functions
  assert.ok(content.includes('window.openScanModal ='), 'Must expose openScanModal function');
  assert.ok(content.includes('window.stepScanPage ='), 'Must expose stepScanPage function');
  assert.ok(content.includes('window.requestScanOcrRepair ='), 'Must expose requestScanOcrRepair function');
  assert.ok(content.includes('/api/locate-page'), 'Must query /api/locate-page for accurate page indexing');
});

test('locate-page API endpoint: exists and verifies query structure', async () => {
  const locatePath = path.join(rootDir, 'web', 'src', 'pages', 'api', 'locate-page.ts');
  assert.ok(fs.existsSync(locatePath), 'locate-page.ts must exist');

  const content = fs.readFileSync(locatePath, 'utf-8');
  assert.ok(content.includes('pdfium.PdfDocument'), 'Must use pypdfium2 for high-speed page search');
  assert.ok(content.includes('q_words'), 'Must perform tokenized word search');
});

test('action.ts: supports extract action with pages and ocrEngine flags', () => {
  const actionPath = path.join(rootDir, 'web', 'src', 'pages', 'api', 'action.ts');
  const content = fs.readFileSync(actionPath, 'utf-8');

  assert.ok(content.includes('pages'), 'Must accept pages parameter');
  assert.ok(content.includes('ocrEngine'), 'Must accept ocrEngine parameter');
  assert.ok(content.includes('--pages'), 'Must construct CLI command with --pages');
  assert.ok(content.includes('--ocr-engine'), 'Must construct CLI command with --ocr-engine');
});

test('status.ts: checks and reports extractProgress', () => {
  const statusPath = path.join(rootDir, 'web', 'src', 'pages', 'api', 'status.ts');
  const content = fs.readFileSync(statusPath, 'utf-8');

  assert.ok(content.includes('extract_progress_'), 'Must look for extract_progress_*.json');
  assert.ok(content.includes('extractProgress'), 'Must expose extractProgress in response JSON');
});
