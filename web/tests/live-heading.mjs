// Real model test of the short heading; no hand-corrected text goes into the request.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
const root = path.resolve(import.meta.dirname, '../..');
const fixture = JSON.parse(fs.readFileSync(new URL('./fixtures/mushroom-heading.json', import.meta.url), 'utf8'));
const book = path.join(root, 'output/markdown/ksiazka.md');
const hash = () => createHash('sha256').update(fs.readFileSync(book)).digest('hex');
const before = hash();
const stem = `__heading_test_${randomUUID()}`;
const md = path.join(root, 'output/markdown', stem + '.md');
const pdf = path.join(root, 'input', stem + '.pdf');
const base = process.env.STUDIO_TEST_URL || 'http://localhost:4343';
try {
  fs.writeFileSync(md, [fixture.context[0], fixture.original, fixture.context[1]].join('\n\n'));
  fs.symlinkSync(path.join(root, 'input/ksiazka.pdf'), pdf);
  const response = await fetch(`${base}/api/ollama-repair`, {
    method:'POST',headers:{'Content-Type':'application/json'},signal:AbortSignal.timeout(250000),
    body:JSON.stringify({file:stem+'.md',index:1,paragraph:fixture.original,model:process.env.OLLAMA_TEST_MODEL || 'qwen2.5:14b',apply:false}),
  });
  const result = await response.json();
  const dir = path.join(import.meta.dirname, 'artifacts');
  fs.mkdirSync(dir, {recursive:true});
  fs.writeFileSync(path.join(dir, 'live-heading.json'),JSON.stringify({status:response.status,bookUnchanged:hash()===before,result},null,2));
  assert.equal(response.status,200,result.error);
  assert.equal(result.applied,false);
  assert.equal(result.method,'source-ocr');
  assert.ok(result.sources.some(s=>s.pages.includes(54)));
  assert.equal(result.repaired.replace(/\s+/g,' ').trim(),'## MUSHROOM POWDER proszek grzybowy/mączka grzybowa');
  assert.equal(hash(),before);
  console.log('PASS: actual model reconstructed the bilingual heading with neighbouring context and PDF page 54; original book unchanged.');
} finally {
  for (const file of [md,pdf]) if (fs.existsSync(file)) fs.unlinkSync(file);
  assert.equal(hash(),before);
}
