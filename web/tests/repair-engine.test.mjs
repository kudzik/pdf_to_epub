import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { reconstruct } from '../src/utils/repair-engine.ts';
import { assessRepair } from '../src/utils/repair-policy.ts';
import { evaluateRepairQuality } from '../src/utils/metrics.ts';
import { writeRepair } from '../src/utils/repair-store.ts';

for (const [original, candidate] of [
  ['Add 250g flour.', 'Add 250g flour and 5g salt.'],
  ['Add 2 eggs and 2 cups milk.', 'Add 2 eggs and cups milk.'],
  ['Heat to 180°C with 250g flour.', 'Heat to 190°C with 250g flour.'],
]) test(`number invariants reject ${candidate}`, () => {
  assert.equal(evaluateRepairQuality(original, candidate).invariantsPreserved, false);
  assert.equal(assessRepair(original, candidate).accepted, false);
});

test('valid culinary fractions preserve quantity and temperature is not noise', () => {
  assert.equal(evaluateRepairQuality('Use ½ cup and bake at 180°C.', 'Use 1/2 cup and bake at 180°C.').invariantsPreserved, true);
  assert.equal(evaluateRepairQuality('Use 1 4 cup.', 'Use 1/4 cup.').invariantsPreserved, true);
  assert.equal(evaluateRepairQuality('Heat to 180°C.', 'Heat to 180 C.').noiseRemoved, 0);
});

test('source evidence can explain false OCR digits but is never a blanket waiver', async () => {
  const corrupted = 'Food preparation is a frame of minH 3 5 and a pleasant pastime.';
  const faithful = 'Food preparation is a frame of mind and a pleasant pastime.';
  const calls = [];
  const result = await reconstruct(corrupted, {
    source: async fragment => { assert.equal(fragment, corrupted); return { text: faithful, pages: [26], confidence: .95, coverage: .6, method: 'easyocr' }; },
    complete: async (fragment, evidence) => { calls.push(!!evidence); return { text: faithful, uncertain: false }; },
  });
  assert.deepEqual(calls, [false, true]);
  assert.equal(result.accepted, true);
  assert.equal(result.method, 'source-ocr');
  assert.deepEqual(result.sources[0].pages, [26]);
  assert.equal(result.metrics.invariantsPreserved, false);
  assert.equal(assessRepair(corrupted, faithful + ' Use 5 cups sugar.', { text: faithful, confidence: .95, coverage: .6 }).accepted, false);
});

test('missing or uncertain source leaves the candidate rejected', async () => {
  const input = 'Food preparation is a frame of minH 3 5 and a pleasant pastime.';
  const result = await reconstruct(input, {
    complete: async () => ({ text: 'Food preparation is a frame of mind and a pleasant pastime.', uncertain: true }),
    source: async () => { throw Error('PDF not found'); },
  });
  assert.equal(result.accepted, false);
  assert.match(result.issues.join(' '), /PDF not found/);
  assert.equal(assessRepair(input, 'Food preparation is a frame of mind and a pleasant pastime.', { text: 'Food preparation is a frame of mind and a pleasant pastime.', confidence: .4, coverage: .6 }).accepted, false);
});

test('unchanged answer cannot be counted as a repair', async () => {
  const result = await reconstruct('This text still has corruption.', {
    complete: async text => ({ text, uncertain: false }),
    source: async () => { throw Error('source unavailable'); },
  });
  assert.equal(result.accepted, false);
});

test('write is atomic, preserves assets and paragraph indexes, rejects stale writes', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'epub-repair-'));
  const md = path.join(dir, 'book.md');
  const image = '![Image](assets/image_8.png)';
  const before = `First paragraph.\n\nBad text.${image}\n\nLast paragraph.`;
  fs.writeFileSync(md, before);
  try {
    assert.throws(() => writeRepair(md, 1, `Bad text.${image}`, 'No image'), /grafiki/);
    assert.equal(fs.readFileSync(md, 'utf8'), before);
    writeRepair(md, 1, `Bad text.${image}`, `Clean text.\n\n${image}`);
    assert.equal(fs.readFileSync(md, 'utf8').split('\n\n').length, 3);
    assert.equal(fs.readFileSync(md + '.bak', 'utf8'), before);
    const current = fs.readFileSync(md, 'utf8');
    assert.throws(() => writeRepair(md, 1, `Bad text.${image}`, `Other text.${image}`), /Konflikt/);
    assert.equal(fs.readFileSync(md, 'utf8'), current);
    assert.deepEqual(fs.readdirSync(dir).sort(), ['book.md', 'book.md.bak']);
  } finally { fs.rmSync(dir, { recursive: true }); }
});

test('culinary units cannot silently change', () => {
  assert.equal(evaluateRepairQuality('Add 1 tsp salt.', 'Add 1 tbsp salt.').invariantsPreserved, false);
  assert.equal(evaluateRepairQuality('Add 1 teaspoon salt.', 'Add 1 tsp salt.').invariantsPreserved, true);
});

test('invalid batch is rejected before any partial write', async () => {
  const { writeRepairs } = await import('../src/utils/repair-store.ts');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'epub-batch-'));
  const md = path.join(dir, 'book.md');
  const original = 'First.\n\nSecond.';
  fs.writeFileSync(md, original);
  try {
    assert.throws(() => writeRepairs(md, [{index: 0, text: 'Changed.'}, {index: 9, text: 'Bad index.'}]), /indeks/);
    assert.equal(fs.readFileSync(md, 'utf8'), original);
    assert.equal(fs.existsSync(md + '.bak'), false);
    assert.equal(writeRepairs(md, [{index: 0, text: 'First.'}]), 0);
    assert.equal(fs.existsSync(md + '.bak'), false);
  } finally { fs.rmSync(dir, { recursive: true }); }
});

test('source evidence cannot waive loss of a real measured quantity or a changed temperature scale', () => {
  const original = 'Add 250g flour and bake at 180°C.';
  const lost = 'Add flour and bake at 180°C.';
  assert.equal(assessRepair(original, lost, {text: lost, confidence: .99, coverage: .9}).accepted, false);
  assert.equal(assessRepair(original, 'Add 250g flour and bake at 180°F.').accepted, false);
});

test('short bilingual heading always checks the scan and retains its heading level', async () => {
  const original = '## pros NJLSHR00M POWDER Srzybowy/m$czka grzybowa';
  let sourceCalls = 0;
  const result = await reconstruct(original, {
    source: async () => { sourceCalls++; return {text:'MUSHROOM POWDER\nproszek grzybowy/mączka grzybowa',pages:[54],confidence:.98,coverage:.78,method:'easyocr'}; },
    complete: async (fragment, evidence) => ({text: evidence ? 'MUSHROOM POWDER proszek grzybowy/mączka grzybowa' : original.replace('m$', 'm('),uncertain:false}),
  });
  assert.equal(sourceCalls, 1);
  assert.equal(result.accepted, true);
  assert.match(result.repaired, /^## MUSHROOM POWDER/);
  assert.match(result.repaired, /proszek grzybowy\/mączka grzybowa/);
});
