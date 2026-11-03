import { test } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { calculateSimilarity, evaluateRepairQuality } from '../src/utils/metrics.ts';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, '..', '..');

test('calculateSimilarity: computes accurate bigram similarity', () => {
  const s1 = 'Heat the oil in a large skillet over medium-high heat.';
  const s2 = 'Heat the oil in a large skillet over medium-high heat.';
  assert.strictEqual(calculateSimilarity(s1, s2), 100);

  const s3 = 'Heat the oil in a large pan over medium-high heat.';
  const sim = calculateSimilarity(s1, s3);
  assert.ok(sim > 80 && sim < 100, `Expected similarity between 80 and 100, got ${sim}`);

  const s4 = 'Completely different recipe for baking chocolate cookies.';
  const lowSim = calculateSimilarity(s1, s4);
  assert.ok(lowSim < 40, `Expected low similarity, got ${lowSim}`);
});

test('evaluateRepairQuality: evaluates OCR repairs and detects invariants', () => {
  const original = 'Add 1 2 tsp of salt and heat to 180°C with 250g flour and kapust^.';
  const goodRepair = 'Add 1/2 tsp of salt and heat to 180°C with 250g flour and kapustą.';

  const evalGood = evaluateRepairQuality(original, goodRepair);
  assert.ok(evalGood.qualityScore >= 90, `Expected quality >= 90, got ${evalGood.qualityScore}`);
  assert.strictEqual(evalGood.invariantsPreserved, true, 'Numbers like 180 and 250 should be preserved');
  assert.strictEqual(evalGood.verdict, 'EXCELLENT');

  // Test: Hallucinated / corrupt change (modified ingredients / changed numbers)
  const badRepair = 'Add 5 tsp of sugar and heat to 300°C with 500g chocolate.';
  const evalBad = evaluateRepairQuality(original, badRepair);
  assert.strictEqual(evalBad.invariantsPreserved, false, 'Should flag missing original numbers');
  assert.ok(evalBad.qualityScore < 60, `Quality score should be low for hallucinated rewrite, got ${evalBad.qualityScore}`);
  assert.strictEqual(evalBad.verdict, 'UNSAFE');
});

test('index.astro: verifies UI standards and eliminates antipatterns', () => {
  const indexPath = path.join(rootDir, 'web', 'src', 'pages', 'index.astro');
  const indexContent = fs.readFileSync(indexPath, 'utf-8');

  // 1. Zero browser alert() or confirm()
  assert.ok(!indexContent.includes('alert('), 'index.astro must not contain browser alert()');
  assert.ok(!indexContent.includes('confirm('), 'index.astro must not contain browser confirm()');

  // 2. Toast container must have z-[9999] so it is not hidden or blurred behind modals
  assert.ok(
    indexContent.includes('id="toastContainer"') && indexContent.includes('z-[9999]'),
    'toastContainer must have z-[9999] to stay above modals'
  );

  // 3. No ugly scrollbar in Ollama PowerShell command snippet
  assert.ok(
    !indexContent.includes('[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0", "User")</span>') ||
    !indexContent.includes('whitespace-nowrap overflow-x-auto px-1">[Environment]'),
    'PowerShell snippet must not have horizontal scrollbar class whitespace-nowrap overflow-x-auto'
  );

  // 4. Must target output folder for Windows Explorer
  assert.ok(
    indexContent.includes("openWindowsExplorer('output')"),
    "openWindowsExplorer must target 'output' folder"
  );
});

test('pipeline.py & action.ts: verifies target output folder resolution', () => {
  const pipelinePath = path.join(rootDir, 'src', 'pipeline.py');
  const pipelineContent = fs.readFileSync(pipelinePath, 'utf-8');
  assert.ok(
    pipelineContent.includes('target = Path("output").resolve()'),
    'pipeline.py must resolve output folder for Windows explorer'
  );

  const actionPath = path.join(rootDir, 'web', 'src', 'pages', 'api', 'action.ts');
  const actionContent = fs.readFileSync(actionPath, 'utf-8');
  assert.ok(
    actionContent.includes("targetFolder = 'output'"),
    "action.ts must default targetFolder to 'output'"
  );
});
