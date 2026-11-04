import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';
import { GET as scanImageGet } from '../src/pages/api/scan-image.ts';
import { POST as patchPost } from '../src/pages/api/patch.ts';
import { GET as historyGet, POST as historyPost } from '../src/pages/api/history.ts';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '..', '..');

test('scan-image API: validates parameters, serves full page PNG and supports cropping', async () => {
  // 1. Missing page returns 400
  const reqMissing = await scanImageGet({ url: new URL('http://localhost/api/scan-image?book=ksiazka') });
  assert.equal(reqMissing.status, 400);

  // 2. Non-existent book returns 404
  const reqNotFound = await scanImageGet({ url: new URL('http://localhost/api/scan-image?book=non_existent_book_123&page=1') });
  assert.equal(reqNotFound.status, 404);

  // 3. Valid page returns 200 with image/png
  const reqOk = await scanImageGet({ url: new URL('http://localhost/api/scan-image?book=ksiazka&page=25') });
  assert.equal(reqOk.status, 200);
  assert.equal(reqOk.headers.get('Content-Type'), 'image/png');
  const buf = Buffer.from(await reqOk.arrayBuffer());
  assert.ok(buf.length > 5000, 'Page image buffer should be substantial');

  const metaFull = await sharp(buf).metadata();
  assert.ok(metaFull.width > 200 && metaFull.height > 200);

  // 4. Normalized crop parameter returns cropped PNG with proportional dimensions
  const reqCrop = await scanImageGet({ url: new URL('http://localhost/api/scan-image?book=ksiazka&page=25&crop=0.1,0.2,0.5,0.4') });
  assert.equal(reqCrop.status, 200);
  assert.equal(reqCrop.headers.get('Content-Type'), 'image/png');
  const cropBuf = Buffer.from(await reqCrop.arrayBuffer());
  const metaCrop = await sharp(cropBuf).metadata();
  assert.ok(metaCrop.width < metaFull.width, 'Cropped width should be smaller than full page width');
  assert.ok(metaCrop.height < metaFull.height, 'Cropped height should be smaller than full page height');
});

test('patch and history APIs: logs applied patches with decision reason into decision history ledger', async () => {
  const testBook = `test_r15_${Date.now()}`;
  const mdDir = path.join(rootDir, 'output', 'markdown');
  const histDir = path.join(rootDir, 'output', 'history');
  fs.mkdirSync(mdDir, { recursive: true });
  fs.mkdirSync(histDir, { recursive: true });

  const mdPath = path.join(mdDir, `${testBook}.md`);
  const histPath = path.join(histDir, `${testBook}_history.json`);

  const initialContent = '# Tytuł\n\nAkapit zero.\n\nAkapit do naprawy 1/2 tsp soli.\n\nAkapit trzeci.';
  fs.writeFileSync(mdPath, initialContent, 'utf8');

  try {
    // 1. POST patch with reason
    const patchReq = new Request('http://localhost/api/patch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        index: 2,
        original: 'Akapit do naprawy 1/2 tsp soli.',
        text: 'Akapit do naprawy 1/2 łyżeczki soli.',
        file: `${testBook}.md`,
        reason: 'Normalizacja jednostki kulinarnej tsp -> łyżeczka'
      })
    });

    const patchRes = await patchPost({ request: patchReq });
    const patchData = await patchRes.json();
    assert.equal(patchData.success, true);
    assert.equal(patchData.changed, true);

    // Verify history ledger file was created and contains the decision record
    assert.ok(fs.existsSync(histPath), 'History file must be written');
    const ledger = JSON.parse(fs.readFileSync(histPath, 'utf8'));
    assert.equal(ledger.book_name, testBook);
    assert.ok(ledger.records.length >= 1);

    const record = ledger.records[ledger.records.length - 1];
    assert.equal(record.paragraph_index, 2);
    assert.equal(record.decision, 'applied');
    assert.equal(record.decision_reason, 'Normalizacja jednostki kulinarnej tsp -> łyżeczka');
    assert.equal(record.source_engine, 'web:patch');

    // 2. POST history verification decision
    const verifyReq = new Request('http://localhost/api/history', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        book: testBook,
        index: 2,
        original: 'Akapit do naprawy 1/2 tsp soli.',
        text: 'Akapit do naprawy 1/2 łyżeczki soli.',
        decision: 'verified',
        reason: 'Zweryfikowano manualnie przez operatora (OK)',
        engine: 'operator'
      })
    });
    const verifyRes = await historyPost({ request: verifyReq });
    const verifyData = await verifyRes.json();
    assert.equal(verifyData.success, true);
    assert.equal(verifyData.record.decision, 'verified');

    // 3. GET history checks anchors
    const getRes = await historyGet({ url: new URL(`http://localhost/api/history?book=${testBook}`) });
    const getData = await getRes.json();
    assert.equal(getData.success, true);
    assert.equal(getData.book, testBook);
    assert.ok(getData.ledger.records.length >= 2);
    assert.ok(getData.anchors[record.record_id].status === 'valid' || getData.anchors[record.record_id].status === 'shifted');
  } finally {
    // Cleanup temporary test book files
    if (fs.existsSync(mdPath)) fs.unlinkSync(mdPath);
    const bakPath = `${mdPath}.bak`;
    if (fs.existsSync(bakPath)) fs.unlinkSync(bakPath);
    if (fs.existsSync(histPath)) fs.unlinkSync(histPath);
  }
});

test('autopilot: supports abort without saving in-flight changes and clean resume from paused index', async () => {
  const source = fs.readFileSync(path.join(rootDir, 'web/src/pages/index.astro'), 'utf8');
  const helpers = source.slice(source.indexOf('function displayRepairState'), source.indexOf('function highlightIssues'));
  const autopilot = source.slice(source.indexOf('async function startOllamaAutopilot'), source.indexOf('// Szybka Rekonstrukcja'));

  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', innerHTML: '', value: 'qwen2.5:14b', style: {},
      classList: { values: new Set(), add(...names) { names.forEach(n => this.values.add(n)); }, remove(...names) { names.forEach(n => this.values.delete(n)); } },
      attrs: {}, setAttribute(k,v) { this.attrs[k] = v; }, scrollIntoView() {},
    });
    return elements.get(id);
  };

  const anomalies = [
    { paragraph_index: 10, raw_paragraph: 'Broken paragraph 10.', reason: 'OCR' },
    { paragraph_index: 20, raw_paragraph: 'Broken paragraph 20.', reason: 'OCR' },
    { paragraph_index: 30, raw_paragraph: 'Broken paragraph 30.', reason: 'OCR' },
  ];

  const patchCalls = [];
  const responses = [
    { success: true, applied: true, repaired: 'Fixed paragraph 10.', method: 'source-ocr', metrics: { qualityScore: 90, similarity: 90, noiseRemoved: 1, invariantsPreserved: true } },
  ];

  let abortTriggered = false;
  const context = {
    isAutopilotRunning: false,
    activeRepairController: null,
    stopRequested: false,
    autopilotResumeIndex: 0,
    globalReport: { anomalies },
    globalOllama: { available: true },
    bookSelector: { value: 'iso_book', disabled: false },
    document: { getElementById: element },
    repairStates: new Map(),
    saveStatuses: new Map(),
    verifyStatuses: new Map(),
    AbortController,
    setInterval: () => 1,
    clearInterval() {},
    showStatus() {},
    triggerAction: async () => true,
    reconstructParagraphHeuristic: t => t,
    fetch: async (url, options) => {
      const body = JSON.parse(options.body);
      patchCalls.push(body);
      if (body.index === 20 && !abortTriggered) {
        // Simulate abort signal arriving during paragraph 20 on first run
        abortTriggered = true;
        context.stopRequested = true;
        const err = new Error('The operation was aborted');
        err.name = 'AbortError';
        throw err;
      }
      return { json: async () => responses.shift() };
    }
  };

  for (const name of ['autopilotProgressBox', 'btnStartOllamaAutopilot', 'btnStartOllamaAutopilotText', 'autopilotStepLabel', 'autopilotProgressPercent', 'autopilotProgressBar', 'autopilotCurrentDetails']) {
    context[name] = element(name);
  }

  vm.runInNewContext(helpers + autopilot, context);

  // 1. First run: repairs index 10, aborts at index 20
  await context.startOllamaAutopilot();

  assert.equal(abortTriggered, true, 'Abort was triggered at paragraph 20');
  assert.equal(context.isAutopilotRunning, false, 'Autopilot is stopped');
  assert.equal(context.autopilotResumeIndex, 1, 'Paused index is recorded at index 1 (paragraph 20)');
  assert.match(element('autopilotStepLabel').textContent, /zatrzymany/);

  // Verify paragraph 20 was NOT saved
  assert.equal(context.saveStatuses.has('iso_book:20'), false, 'Aborted paragraph must not be marked saved');

  // 2. Resume run: continues from index 1 (paragraph 20 and 30)
  context.stopRequested = false;
  responses.push(
    { success: true, applied: true, repaired: 'Fixed paragraph 20.', method: 'source-ocr', metrics: { qualityScore: 88, similarity: 88, noiseRemoved: 1, invariantsPreserved: true } },
    { success: true, applied: true, repaired: 'Fixed paragraph 30.', method: 'source-ocr', metrics: { qualityScore: 92, similarity: 92, noiseRemoved: 1, invariantsPreserved: true } }
  );

  await context.startOllamaAutopilot();

  assert.equal(context.autopilotResumeIndex, 0, 'Autopilot reset resume index to 0 upon completion');
  assert.equal(context.saveStatuses.get('iso_book:20'), 'saved');
  assert.equal(context.saveStatuses.get('iso_book:30'), 'saved');
  assert.match(element('autopilotStepLabel').textContent, /Zakończono/);
});

test('index.astro: includes all UI elements for scan preview, history ledger, and distinct save vs verify statuses', () => {
  const indexContent = fs.readFileSync(path.join(rootDir, 'web/src/pages/index.astro'), 'utf8');

  // 1. Scan preview elements
  assert.ok(indexContent.includes('toggleScan('), 'Must provide toggleScan handler');
  assert.ok(indexContent.includes('reloadScan('), 'Must provide reloadScan handler');
  assert.ok(indexContent.includes('scan-box-'), 'Must provide scan-box container');
  assert.ok(indexContent.includes('scan-img-'), 'Must provide scan-img image element');
  assert.ok(indexContent.includes('🔍 Skan PDF'), 'Must provide scan preview button label');

  // 2. Decision history elements
  assert.ok(indexContent.includes('toggleHistory('), 'Must provide toggleHistory handler');
  assert.ok(indexContent.includes('history-box-'), 'Must provide history-box container');
  assert.ok(indexContent.includes('history-content-'), 'Must provide history-content container');
  assert.ok(indexContent.includes('📜 Historia'), 'Must provide history button label');

  // 3. Distinct save and verification status badges
  assert.ok(indexContent.includes('save-status-'), 'Must provide separate save status element');
  assert.ok(indexContent.includes('verify-status-'), 'Must provide separate verification status element');
  assert.ok(indexContent.includes('markVerified('), 'Must provide markVerified action');
  assert.ok(indexContent.includes('markRejected('), 'Must provide markRejected action');
  assert.ok(indexContent.includes('✓ Zweryfikuj'), 'Must provide verification button');
  assert.ok(indexContent.includes('✗ Odrzuć'), 'Must provide rejection button');
});
