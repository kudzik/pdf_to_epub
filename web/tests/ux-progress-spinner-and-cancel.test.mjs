import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, '..', '..');

test('index.astro: includes btnCancelActiveJob and stops spinner on outcome', () => {
  const indexPath = path.join(rootDir, 'web', 'src', 'pages', 'index.astro');
  const content = fs.readFileSync(indexPath, 'utf-8');

  // Cancel Button
  assert.ok(content.includes('id="btnCancelActiveJob"'), 'Must include btnCancelActiveJob in liveProgressCard');
  assert.ok(content.includes('cancelActiveJob'), 'Must define cancelActiveJob handler');
  assert.ok(content.includes("addEventListener('click', cancelActiveJob)"), 'Must bind cancel listener');

  // Spinner Lifecycle Management
  assert.ok(content.includes("spinnerEl.classList.remove('animate-spin')"), 'Must remove animate-spin on completion');
  assert.ok(content.includes("outcome === 'success'"), 'Must handle success outcome with checkmark icon');
  assert.ok(content.includes("outcome === 'error'"), 'Must handle error outcome with alert icon');
  assert.ok(content.includes("liveProgressCard.classList.add('hidden')"), 'Must auto-hide liveProgressCard when completed');

  // Full-book safeguard
  assert.ok(content.includes("rangeSelect.value === 'all'"), 'Must warn before extracting 914 pages');
});

test('action.ts: supports action=cancel and deletes stale progress before extraction', () => {
  const actionPath = path.join(rootDir, 'web', 'src', 'pages', 'api', 'action.ts');
  const content = fs.readFileSync(actionPath, 'utf-8');

  assert.ok(content.includes("action === 'cancel'"), 'Must support cancel action in action.ts');
  assert.ok(content.includes('runningJobs'), 'Must maintain runningJobs registry to track background tasks');
  assert.ok(content.includes('SIGTERM'), 'Must terminate process cleanly with SIGTERM');
  assert.ok(content.includes('extract_progress_'), 'Must clear extract_progress before new extraction');
});
