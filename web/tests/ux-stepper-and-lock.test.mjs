import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, '../..');
const indexAstroPath = path.join(rootDir, 'web/src/pages/index.astro');
const actionTsPath = path.join(rootDir, 'web/src/pages/api/action.ts');

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

test('Stepper Reset on Purge: verifies complete reset of stages and metrics', () => {
  const indexContent = fs.readFileSync(indexAstroPath, 'utf8');
  const actionContent = fs.readFileSync(actionTsPath, 'utf8');

  // 1. Action API passes --with-epub when purging so no stale EPUB prevents stepper reset
  assert.ok(actionContent.includes("keepEpub === true ? '--keep-epub' : '--with-epub'"), 'Action API must purge EPUB by default to ensure stage reset');
  assert.ok(actionContent.includes("'purge', '--all', '--with-epub'"), 'purge-all must include --with-epub');

  // 2. Purge handler resets client states and reloads books list
  assert.ok(indexContent.includes('repairStates.clear()'), 'Must clear repairStates on purge');
  assert.ok(indexContent.includes('saveStatuses.clear()'), 'Must clear saveStatuses on purge');
  assert.ok(indexContent.includes('verifyStatuses.clear()'), 'Must clear verifyStatuses on purge');
  assert.ok(indexContent.includes('await loadBooks()'), 'Must reload books after purge');

  // 3. DOM metric counters reset to 0 in absence of audit report
  assert.ok(indexContent.includes("statTotal.textContent = '0'"), 'Anomaly total must reset to 0 when report is cleared');
  assert.ok(indexContent.includes("statHigh.textContent = 'High: 0'"), 'High severity badge must reset to 0');
  assert.ok(indexContent.includes("statMed.textContent = 'Med: 0'"), 'Medium severity badge must reset to 0');
  assert.ok(indexContent.includes("statLow.textContent = 'Low: 0'"), 'Low severity badge must reset to 0');

  // 4. Stepper requires Markdown existence for EPUB completion invariant
  assert.ok(indexContent.includes('const isEpubValid = epubExists && mdExists && data.epubInfo?.current !== false;'), 'EPUB step must only be marked complete if source MD exists');
  assert.ok(indexContent.includes('stepBadge2.textContent = mdExists ?'), 'Step 2 badge text must dynamically toggle between MD and completed');
  assert.ok(indexContent.includes('stepBadge3.textContent = mdExists ?'), 'Step 3 badge text must dynamically toggle');
  assert.ok(indexContent.includes('stepCard2.className = mdExists'), 'Step 2 card background must toggle on mdExists');
  assert.ok(indexContent.includes('stepCard5.className = isEpubValid'), 'Step 5 card background must toggle on isEpubValid');

  // 5. Harmonized numbering: Conversion stages start with Krok 1 (Ekstrakcja), not Krok 2
  assert.ok(indexContent.includes('>Źródło</span>'), 'StepCard1 must be labeled Źródło (prerequisite), not Krok 1');
  assert.ok(indexContent.includes('>Krok 1</span>'), 'StepCard2 (Ekstrakcja) must be labeled Krok 1');
  assert.ok(indexContent.includes('>Krok 2</span>'), 'StepCard3 (Czyszczenie) must be labeled Krok 2');
  assert.ok(indexContent.includes('>Krok 3</span>'), 'StepCard4 (Audyt) must be labeled Krok 3');
  assert.ok(indexContent.includes('>Krok 4</span>'), 'StepCard5 (Publikacja) must be labeled Krok 4');
  assert.ok(indexContent.includes('Gotowy do ekstrakcji'), 'StepCard2 must display Gotowy do ekstrakcji when PDF is available');
});

