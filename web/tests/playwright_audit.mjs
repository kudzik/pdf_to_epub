// All API traffic is intercepted: this test never extracts, cleans or deletes real books.
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({headless:true});
const page = await browser.newPage();
const errors = [];
page.on('pageerror', e => errors.push(e.message));
const states = new Map(['alpha', 'beta'].map(book => [book, {
  book, pdfInfo:{exists:true,filename:`${book}.pdf`,size:'100 KB'},
  mdInfo:{exists:book === 'alpha',usable:book === 'alpha',filename:`${book}.md`,lines:12,size:'1 KB'},
  cleanInfo:{cleaned:false},report:null,reportState:'missing',epubInfo:{exists:false,current:false},
  extractionInfo:{scope:'unknown'},workflow:{step:book==='alpha'?2:1,coverageVerified:false},
  ollama:{available:false,models:[]}
}]));
const actions=[];
let slowAlpha = false;
let failBeta = false;
let activeJob = null;
let latestJob = null;
await page.route('**/api/**', async route => {
  const url=new URL(route.request().url());
  if(url.pathname==='/api/files') return route.fulfill({json:{books:[{stem:'alpha',hasPdf:true,hasMd:true},{stem:'beta',hasPdf:true,hasMd:false}]}});
  if(url.pathname==='/api/jobs') return route.fulfill({json:{activeJob,job:latestJob,logTail:'fixture progress log',extractProgress:{status:'running',percent:35,completed_pages:7,total_pages:20}}});
  if(url.pathname==='/api/history') return route.fulfill({json:{success:true,ledger:{records:[]}}});
  if(url.pathname==='/api/status') {
    const book=url.searchParams.get('book');
    if (slowAlpha && book==='alpha') await new Promise(resolve=>setTimeout(resolve,400));
    if (failBeta && book==='beta') return route.fulfill({status:503,json:{error:'Fixture status unavailable'}});
    return route.fulfill({json:states.get(book)});
  }
  if(url.pathname==='/api/action') {
    const body=route.request().postDataJSON(); actions.push(body);
    const state=states.get(body.book);
    if(body.action==='clean') { state.cleanInfo.cleaned=true; await new Promise(resolve=>setTimeout(resolve,350)); }
    if(body.action==='audit') {state.report={anomalies:[],missing_chapters:[],total_anomalies:0};state.reportState='current';}
    if(body.action==='build') state.epubInfo={exists:true,current:true,validated:true,filename:'alpha.epub',size:'2 KB',mtime:'12:00'};
    return route.fulfill({json:{success:true,stdout:'Fixture action completed',durationMs:1}});
  }
  return route.fulfill({status:404,json:{error:'Unexpected API call blocked by test'}});
});
try {
  await page.goto(process.env.STUDIO_TEST_URL || 'http://localhost:4344', {waitUntil:'networkidle'});
  assert.equal(await page.locator('#bookSelector').inputValue(),'');
  assert.equal(await page.locator('[data-stage-action="extract"]').isDisabled(),true);
  await page.selectOption('#bookSelector','alpha');
  await page.waitForFunction(() => document.querySelector('#nextActionStepTag').textContent.includes('2 z 4'));
  assert.match(await page.locator('#stepStatus2').innerText(),/Znaleziono istniejący Markdown/);
  assert.equal(actions.length,0,'Selection must never execute a pipeline action');
  await page.click('[data-stage-action="clean"]');
  assert.equal(await page.locator('[data-stage-action="audit"]').isDisabled(),true);
  assert.equal(await page.locator('#btnRunAudit').isDisabled(),true);
  await page.waitForFunction(() => document.querySelector('#nextActionStepTag').textContent.includes('3 z 4'));
  await page.click('[data-stage-action="audit"]');
  await page.waitForFunction(() => document.querySelector('#nextActionStepTag').textContent.includes('4 z 4'));
  await page.click('[data-stage-action="build"]');
  await page.waitForFunction(() => document.querySelector('#nextActionStepTag').textContent.includes('pokrycie nieznane'));
  await page.selectOption('#bookSelector','beta');
  await page.waitForFunction(() => document.querySelector('#nextActionStepTag').textContent.includes('1 z 4'));
  assert.equal(await page.locator('[data-stage-action="clean"]').isDisabled(),true);
  assert.deepEqual(actions.map(a=>a.action),['clean','audit','build']);
  await page.selectOption('#extractOcrEngine','easyocr');
  await page.selectOption('#extractRangeSelect','1-10');
  assert.match(await page.locator('#extractSettingsSummary').innerText(),/EasyOCR/);
  activeJob=latestJob={id:'fixture-job',book:'beta',action:'extract',pid:123,startTime:Date.now()-120000,status:'running'};
  await page.reload({waitUntil:'networkidle'});
  await page.waitForFunction(()=>document.querySelector('#liveProgressTitle').textContent.includes('35%'));
  assert.equal(await page.locator('#bookSelector').inputValue(),'beta');
  assert.equal(await page.locator('[data-stage-action="extract"]').isDisabled(),true);
  assert.match(await page.locator('#liveConsoleOutput').innerText(),/fixture progress log/);
  assert.match(await page.locator('#liveProgressTimer').innerText(),/02:/);
  activeJob=null;
  latestJob={...latestJob,status:'succeeded'};
  await page.waitForFunction(()=>!document.querySelector('#bookSelector').disabled);
  slowAlpha=true;
  await page.selectOption('#bookSelector','alpha');
  await page.selectOption('#bookSelector','beta');
  await page.waitForFunction(() => document.querySelector('#stepTitle2').textContent==='beta.md');
  await page.waitForTimeout(600);
  assert.equal(await page.locator('#stepTitle2').innerText(),'beta.md','A late response must not overwrite book B');
  failBeta=true;
  await page.selectOption('#bookSelector','alpha');
  await page.selectOption('#bookSelector','beta');
  await page.waitForFunction(() => document.querySelector('#nextActionTitle').textContent.includes('Nie udało'));
  assert.equal(await page.locator('[data-stage-action="extract"]').isDisabled(),true);
  assert.deepEqual(errors,[]);
  console.log('PASS: selection, card actions, four stages, unknown coverage, book switch, delayed response race, API failure; no real API writes.');
} finally { await browser.close(); }
