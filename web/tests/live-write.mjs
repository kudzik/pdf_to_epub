// Actual API + Ollama write test in a temporary book. The user's book is read-only.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { imageReferences } from '../src/utils/repair-assets.ts';
const root = path.resolve(import.meta.dirname, '../..');
const fixture = JSON.parse(fs.readFileSync(path.join(import.meta.dirname, 'fixtures/foreword.json'), 'utf8'));
const hash = () => createHash('sha256').update(fs.readFileSync(path.join(root, 'output/markdown/ksiazka.md'))).digest('hex');
const before = hash();
const stem = `__repair_test_${randomUUID()}`;
const md = path.join(root, 'output/markdown', stem + '.md');
const pdf = path.join(root, 'input', stem + '.pdf');
const original = `Unchanged first paragraph.\n\n${fixture.original}\n\nUnchanged last paragraph.`;
const base = process.env.STUDIO_TEST_URL || 'http://localhost:4343';
const payload = { file: stem + '.md', index: 1, paragraph: fixture.original, model: process.env.OLLAMA_TEST_MODEL || 'qwen2.5:14b', apply: true };
async function post(body) {
  const response = await fetch(`${base}/api/ollama-repair`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.timeout(250000) });
  return { status: response.status, data: await response.json() };
}
try {
  const status = await (await fetch(`${base}/api/status?book=ksiazka`)).json();
  assert.equal(status.repairVersion, 'source-ocr-v1', 'Restart the server before testing');
  fs.writeFileSync(md, original);
  fs.symlinkSync(path.join(root, 'input/ksiazka.pdf'), pdf);
  const { status: code, data } = await post(payload);
  assert.equal(code, 200, data.error);
  assert.equal(data.applied, true);
  const paragraphs = fs.readFileSync(md, 'utf8').split('\n\n');
  assert.equal(paragraphs.length, 3);
  assert.equal(paragraphs[0], 'Unchanged first paragraph.');
  assert.equal(paragraphs[2], 'Unchanged last paragraph.');
  assert.equal(paragraphs[1], data.repaired.trim().replace(/\n\s*\n/g, '\n'));
  assert.deepEqual(imageReferences(paragraphs[1]), imageReferences(fixture.original));
  assert.equal(fs.readFileSync(md + '.bak', 'utf8'), original);
  const content = fs.readFileSync(md, 'utf8');
  const stale = await post(payload);
  assert.equal(stale.status, 409);
  assert.equal(fs.readFileSync(md, 'utf8'), content);
  assert.equal(hash(), before);
  const dir = path.join(import.meta.dirname, 'artifacts');
  fs.mkdirSync(dir, {recursive: true});
  fs.writeFileSync(path.join(dir, 'live-write.json'), JSON.stringify({ status: code, staleStatus: stale.status, originalBookUnchanged: hash() === before, result: data }, null, 2));
  console.log('PASS: actual model result saved to isolated book; images, backup, paragraph indexes and stale-write rejection verified; original book unchanged.');
} finally {
  for (const file of [md, md + '.bak', pdf]) if (fs.existsSync(file)) fs.unlinkSync(file);
  assert.equal(hash(), before, 'The original book changed during the test');
}
