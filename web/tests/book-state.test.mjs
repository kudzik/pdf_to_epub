import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { readBookState, hashFile } from '../src/utils/book-state.ts';
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'epub-state-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const out = path.join(root, 'output');
  fs.mkdirSync(path.join(out, 'markdown'), { recursive: true });
  fs.mkdirSync(path.join(out, 'epub'));
  const md = path.join(out, 'markdown', 'book.md');
  const json = (name, data) => fs.writeFileSync(path.join(out, `${name}_book.json`), JSON.stringify(data));
  return {root, out, md, json, state: () => readBookState(root, 'book')};
}
test('existing and empty Markdown do not imply full extraction', t => {
  const f = fixture(t); fs.writeFileSync(f.md, '');
  assert.equal(f.state().mdInfo.usable, false);
  fs.writeFileSync(f.md, '# Imported');
  assert.equal(f.state().extractionInfo.scope, 'unknown');
  assert.equal(f.state().workflow.coverageVerified, false);
});
test('repair preserves cleaning and invalidates audit; extraction resets cleaning', t => {
  const f = fixture(t); fs.writeFileSync(f.md, 'Before');
  f.json('clean_status', {cleaned:true,timestamp:'2026-11-04T10:00:00Z'});
  f.json('audit', {markdown_sha256:hashFile(f.md),anomalies:[]});
  fs.writeFileSync(f.md, 'After');
  assert.equal(f.state().cleanInfo.cleaned, true);
  assert.equal(f.state().reportState, 'stale');
  assert.equal(f.state().workflow.step, 3);
  f.json('extraction', {scope:'full',timestamp:'2026-11-04T11:00:00Z'});
  assert.equal(f.state().cleanInfo.cleaned, false);
  fs.unlinkSync(f.md); assert.equal(f.state().report, null);
});
test('EPUB requires validated manifest and unchanged dependencies', t => {
  const f = fixture(t); fs.writeFileSync(f.md, 'Text');
  const epub = path.join(f.out,'epub','book.epub'); fs.writeFileSync(epub, 'fixture');
  assert.equal(f.state().epubInfo.current, false);
  f.json('build', {validated:true,epub_sha256:hashFile(epub),dependencies:{[f.md]:hashFile(f.md)}});
  assert.equal(f.state().epubInfo.current, true);
  fs.writeFileSync(f.md,'Changed'); assert.equal(f.state().epubInfo.current, false);
});
