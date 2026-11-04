import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, '../..');
const indexAstroPath = path.join(rootDir, 'web/src/pages/index.astro');

test('UX Stepper and Interaction Lock: verifies comprehensive UI improvements', () => {
  const content = fs.readFileSync(indexAstroPath, 'utf8');

  // 1. Workflow Stepper elements
  assert.ok(content.includes('id="nextActionHeroBanner"'), 'Must include Next Best Action Hero banner');
  assert.ok(content.includes('id="btnNextActionCta"'), 'Must include 1-click CTA button for next recommended action');
  assert.ok(content.includes('id="stepCard1"'), 'Must include Step 1 card (PDF)');
  assert.ok(content.includes('id="stepCard2"'), 'Must include Step 2 card (Extraction)');
  assert.ok(content.includes('id="stepCard3"'), 'Must include Step 3 card (Clean)');
  assert.ok(content.includes('id="stepCard4"'), 'Must include Step 4 card (Audit & Repair)');
  assert.ok(content.includes('id="stepCard5"'), 'Must include Step 5 card (EPUB 3)');
  assert.ok(content.includes('id="stepperGlobalStatus"'), 'Must include stepper global status label');

  // 2. Live Execution Console & Drawer
  assert.ok(content.includes('id="liveProgressCard"'), 'Must include Live Progress Console card');
  assert.ok(content.includes('id="liveProgressTimer"'), 'Must include stopwatch timer element');
  assert.ok(content.includes('id="liveConsoleOutput"'), 'Must include live stdout/stderr console');
  assert.ok(content.includes('id="sessionActivityList"'), 'Must include Session Activity log feed');

  // 3. Concurrency Lock & Anti-collision functions
  assert.ok(content.includes('function setSystemBusy('), 'Must provide setSystemBusy controller');
  assert.ok(content.includes('aria-busy'), 'Must set accessibility aria-busy on buttons during execution');
  assert.ok(content.includes('pointer-events-none'), 'Must lock click interactions during execution');

  // 4. Overwrite Guardrail
  assert.ok(content.includes('Ostrzeżenie przed nadpisaniem Markdowna'), 'Must provide guardrail warning before overwriting existing Markdown');
  assert.ok(content.includes('function updateWorkflowStepper('), 'Must provide dynamic stepper update logic');
});

