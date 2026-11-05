import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../src/pages/index.astro', import.meta.url), 'utf8');
const helpers = source.slice(source.indexOf('function displayRepairState'), source.indexOf('function highlightIssues'));
const autopilot = source.slice(source.indexOf('async function startOllamaAutopilot'), source.indexOf('// Szybka Rekonstrukcja'));
function setup(responses) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', innerHTML: '', value: 'qwen2.5:14b', style: {},
      classList: { values: new Set(), add(...names) { names.forEach(n => this.values.add(n)); }, remove(...names) { names.forEach(n => this.values.delete(n)); } },
      attrs: {}, setAttribute(k,v) { this.attrs[k] = v; }, scrollIntoView() {},
    });
    return elements.get(id);
  };
  const actions = [], calls = [];
  const context = { localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    statusLoading: false, isSystemBusy: false, globalStatusData: {}, stageAvailability() {}, isAutopilotRunning: false, activeRepairController: null, stopRequested: false,
    globalReport: { anomalies: responses.map((_, index) => ({paragraph_index:index, raw_paragraph:`Broken paragraph ${index}.`, reason:'OCR'})) },
    globalOllama: { available:true }, bookSelector: {value:'test', disabled:false},
    document: {getElementById:element}, repairStates: new Map(), AbortController,
    setInterval: () => 1, clearInterval() {}, showStatus() {},
    fetch: async (url, options) => { calls.push(JSON.parse(options.body)); return {json:async () => responses.shift()}; },
    triggerAction: async action => { actions.push(action); return true; },
    reconstructParagraphHeuristic: text => text,
  };
  for (const name of ['autopilotProgressBox','btnStartOllamaAutopilot','autopilotStepLabel','autopilotProgressPercent','autopilotProgressBar','autopilotCurrentDetails']) context[name] = element(name);
  vm.runInNewContext(helpers + autopilot, context);
  return {context, element, actions, calls};
}

test('autopilot retains rejected proposal, clears busy state and never builds when no repair was saved', async () => {
  const {context,element,actions} = setup([{success:false, applied:false, repaired:'Proposed paragraph.', error:'Source uncertain'}]);
  await context.startOllamaAutopilot();
  assert.deepEqual(actions, []);
  assert.equal(context.bookSelector.disabled, false);
  assert.equal(context.isAutopilotRunning, false);
  assert.equal(context.activeRepairController, null);
  assert.equal(element('card-0').attrs['aria-busy'], 'false');
  assert.equal(element('card-0').classList.values.has('animate-pulse'), false);
  assert.equal(element('liveDiffAfter').textContent, 'Proposed paragraph.');
  assert.equal(element('repaired-preview-0').textContent, 'Proposed paragraph.');
  assert.match(element('metrics-footer-0').textContent, /Source uncertain/);
});

test('autopilot continues after rejection and audits saved changes and leaves compilation to the user', async () => {
  const {context,element,actions,calls} = setup([
    {success:false, applied:false, repaired:'Rejected.', error:'Source uncertain'},
    {success:true, applied:true, repaired:'Fixed paragraph 1.', method:'source-ocr', sources:[{pages:[26]}], metrics:{qualityScore:85,similarity:85,noiseRemoved:1,invariantsPreserved:true}},
  ]);
  await context.startOllamaAutopilot();
  assert.deepEqual(calls.map(c => c.index), [0,1]);
  assert.deepEqual(actions, ['audit']);
  assert.equal(element('repaired-preview-1').textContent, 'Fixed paragraph 1.');
  assert.match(element('quality-badge-1').textContent, /ponowny OCR/);
  assert.match(element('metrics-footer-1').textContent, /26/);
  assert.equal(context.bookSelector.disabled, false);
});

test('stop request interrupts the run and prevents final build', async () => {
  const {context,element,actions,calls} = setup([{success:false}, {success:false}]);
  context.fetch = async () => { context.stopRequested = true; throw new Error('Cancelled'); };
  await context.startOllamaAutopilot();
  assert.deepEqual(actions, []);
  assert.equal(context.activeRepairController, null);
  assert.equal(context.isAutopilotRunning, false);
  assert.equal(context.bookSelector.disabled, false);
  assert.equal(element('card-0').attrs['aria-busy'], 'false');
  assert.match(element('autopilotStepLabel').textContent, /zatrzymany/);
});

test('filtering keeps the active record visible and re-rendering preserves completed proposals', () => {
  const {context,element} = setup([{success:false}]);
  context.activeFilter = 'HIGH';
  context.searchQuery = 'no match';
  context.globalReport.anomalies[0].severity = 'low';
  context.anomaliesList = element('anomaliesList');
  context.escapeHtml = s => s;
  context.highlightIssues = s => s;
  context.repairStates.set('test:0', {working:true, text:'Working...', label:'Active record', details:'In progress'});
  const render = source.slice(source.indexOf('function renderAnomalies'), source.indexOf('function displayRepairState'));
  vm.runInNewContext(render, context);
  context.renderAnomalies();
  assert.match(context.anomaliesList.innerHTML, /id="card-0"/);
  assert.equal(element('card-0').attrs['aria-busy'], 'true');
  context.rememberRepair(0, {success:false, repaired:'Rejected proposal', error:'Review source'});
  context.searchQuery = '';
  context.activeFilter = 'ALL';
  context.renderAnomalies();
  assert.equal(element('repaired-preview-0').textContent, 'Rejected proposal');
  assert.equal(element('card-0').attrs['aria-busy'], 'false');
});
