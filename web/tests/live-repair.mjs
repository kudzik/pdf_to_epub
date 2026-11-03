// Explicit live check: requires Web Studio, local Ollama and input/ksiazka.pdf.
// Never sends the human reference to the endpoint and never writes book content.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { imageReferences } from '../src/utils/repair-assets.ts';

const root = path.resolve(import.meta.dirname, '../..');
const fixture = JSON.parse(fs.readFileSync(path.join(import.meta.dirname, 'fixtures/foreword.json'), 'utf8'));
const md = path.join(root, 'output/markdown/ksiazka.md');
const hash = () => createHash('sha256').update(fs.readFileSync(md)).digest('hex');
const before = hash();
const base = process.env.STUDIO_TEST_URL || 'http://localhost:4343';
const status = await (await fetch(`${base}/api/status?book=ksiazka`)).json();
assert.equal(status.repairVersion, 'source-ocr-v1', 'Stary serwer: zbuduj i zrestartuj Web Studio przed testem');
const start = Date.now();
const response = await fetch(`${base}/api/ollama-repair`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ paragraph: fixture.original, file: `${fixture.book}.md`, model: process.env.OLLAMA_TEST_MODEL || 'qwen2.5:14b', apply: false }),
  signal: AbortSignal.timeout(250_000),
});
const result = await response.json();
assert.equal(hash(), before, 'Live test modified the book');
function tokens(text) {
  for (const ref of imageReferences(text)) text = text.replace(ref, ' ');
  return text.toLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
}
function wordErrorRate(reference, candidate) {
  const a = tokens(reference), b = tokens(candidate);
  let previous = Array.from({length: b.length + 1}, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const next = [i];
    for (let j = 1; j <= b.length; j++) next[j] = Math.min(previous[j] + 1, next[j-1] + 1, previous[j-1] + (a[i-1] === b[j-1] ? 0 : 1));
    previous = next;
  }
  return previous[b.length] / a.length;
}
const wer = wordErrorRate(fixture.reference, result.repaired || '');
const summary = { checkedAt: new Date().toISOString(), endpoint: base, status: response.status, seconds: (Date.now()-start)/1000, bookUnchanged: hash() === before, wordErrorRate: wer, result };
const dir = path.join(import.meta.dirname, 'artifacts');
fs.mkdirSync(dir, {recursive: true});
fs.writeFileSync(path.join(dir, 'live-repair.json'), JSON.stringify(summary, null, 2));
console.log(JSON.stringify({ status: response.status, seconds: summary.seconds, wordErrorRate: wer, method: result.method, issues: result.issues, bookUnchanged: summary.bookUnchanged }, null, 2));
assert.equal(response.status, 200, result.error);
assert.equal(result.success, true);
assert.equal(result.applied, false);
assert.equal(result.method, 'source-ocr');
assert.deepEqual(imageReferences(result.repaired), imageReferences(fixture.original));
assert.ok(result.sources.some(source => source.pages.includes(26)), 'Missing source provenance');
assert.ok(wer <= .035, `Word error rate ${(wer*100).toFixed(2)}% exceeds 3.5%`);
console.log('Live reconstruction passed; no reference text was sent to the model.');
