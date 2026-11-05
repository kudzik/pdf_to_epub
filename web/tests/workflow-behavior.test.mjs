import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../src/pages/index.astro', import.meta.url), 'utf8');
const start = source.indexOf('    function updateWorkflowStepper(data)');
const end = source.indexOf('    // Wywołanie akcji w CLI', start);
function render(overrides = {}) {
  const element = () => ({ textContent: '', innerHTML: '', className: '', classList: { add() {}, remove() {} } });
  const context = { bookSelector: { value: 'fixture' }, activeCtaHandler: null,
    triggerAction() {}, startOllamaAutopilot() {}, openWindowsExplorer() {}, copyToWindowsDownloads() {} };
  for (const name of source.slice(start, end).matchAll(/\b(?:step(?:Card|Badge|Title|Sub|Status)\d|stepperGlobalStatus|nextAction\w+|btnRunAutopilot(?:Text)?)\b/g)) context[name[0]] ??= element();
  vm.createContext(context);
  vm.runInContext(source.slice(start, end), context);
  context.updateWorkflowStepper({ pdfInfo: { exists: true }, mdInfo: { exists: true, lines: 10 },
    cleanInfo: { cleaned: true }, report: { anomalies: [] }, epubInfo: { exists: false }, workflow: { coverageVerified: true }, ...overrides });
  return context;
}
test('workflow follows four stages with repair inside audit', () => {
  assert.match(render({ mdInfo: { exists: false } }).nextActionStepTag.textContent, /1 z 4/);
  assert.match(render({ cleanInfo: { cleaned: false } }).nextActionStepTag.textContent, /2 z 4/);
  assert.match(render({ report: null }).nextActionStepTag.textContent, /3 z 4/);
  assert.match(render({ report: { anomalies: [{}] } }).nextActionStepTag.textContent, /3 z 4/);
  assert.match(render().nextActionStepTag.textContent, /4 z 4/);
});
test('current EPUB completes workflow; stale EPUB requires rebuild', () => {
  assert.match(render({ epubInfo: { exists: true, current: true } }).nextActionStepTag.textContent, /Sukces/);
  assert.match(render({ epubInfo: { exists: true, current: false } }).nextActionStepTag.textContent, /4 z 4/);
});

test('missing chapters prevent success and absent MD invalidates audit', () => {
  assert.match(render({ report: { anomalies: [], missing_chapters: ['Soups'] }, epubInfo: { exists: true, current: true } }).nextActionStepTag.textContent, /Kontrola kompletności/);
  const absent = render({ mdInfo: { exists: false }, report: { anomalies: [] } });
  assert.doesNotMatch(absent.stepStatus4.innerHTML, /Audyt zakończony/);
});
