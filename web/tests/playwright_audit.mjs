import { chromium } from 'playwright';

async function run() {
  console.log('🚀 Uruchamianie Playwright Chromium...');
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  const logs = [];
  const errors = [];
  const network = [];

  page.on('console', msg => {
    logs.push(`[BROWSER CONSOLE ${msg.type().toUpperCase()}]: ${msg.text()}`);
    console.log(`[BROWSER CONSOLE ${msg.type().toUpperCase()}]: ${msg.text()}`);
  });

  page.on('pageerror', err => {
    errors.push(`[BROWSER PAGEERROR]: ${err.message}\n${err.stack}`);
    console.error(`[BROWSER PAGEERROR]: ${err.message}`);
  });

  page.on('request', req => {
    network.push(`--> ${req.method()} ${req.url()}`);
  });

  page.on('response', res => {
    network.push(`<-- ${res.status()} ${res.url()}`);
  });

  page.on('requestfailed', req => {
    errors.push(`[NETWORK FAILED]: ${req.method()} ${req.url()} - ${req.failure()?.errorText}`);
    console.error(`[NETWORK FAILED]: ${req.method()} ${req.url()}`);
  });

  console.log('🌐 Nawigacja do http://localhost:4343...');
  await page.goto('http://localhost:4343', { waitUntil: 'networkidle' });

  console.log('🔍 Sprawdzanie stanu początkowego strony...');
  const title = await page.title();
  console.log(`Tytuł strony: ${title}`);

  const liveConsole = await page.$eval('#liveConsoleOutput', el => el.textContent);
  console.log(`Początkowa zawartość #liveConsoleOutput:\n"${liveConsole}"`);

  // Sprawdź czy są błędy JS na starcie
  console.log(`Liczba błędów na starcie: ${errors.length}`);

  // 1. TEST: Kliknięcie "Ekstrahuj z PDF"
  console.log('\n--- TEST 1: Kliknięcie "Ekstrahuj z PDF" (#btnRunExtract) ---');
  await page.click('#btnRunExtract');
  await page.waitForTimeout(1000);

  // Sprawdź czy pojawił się modal potwierdzenia (np. ostrzeżenie o nadpisaniu)
  const isModalVisible = await page.$eval('#confirmModal', el => !el.classList.contains('hidden'));
  console.log(`Czy modal potwierdzenia jest widoczny? ${isModalVisible}`);

  if (isModalVisible) {
    const modalTitle = await page.$eval('#confirmModalTitle', el => el.textContent);
    const modalMsg = await page.$eval('#confirmModalMessage', el => el.textContent);
    console.log(`Modal Title: "${modalTitle}"`);
    console.log(`Modal Message:\n${modalMsg}`);
    console.log('Klikam przycisk potwierdzenia (#btnAcceptConfirm)...');
    await page.click('#btnAcceptConfirm');
  }

  // Obserwuj przez 5 sekund terminal i postęp
  for (let i = 1; i <= 5; i++) {
    await page.waitForTimeout(1000);
    const termVisible = await page.$eval('#liveTerminalDrawer', el => !el.classList.contains('hidden'));
    const termText = await page.$eval('#liveConsoleOutput', el => el.textContent);
    const progressTitle = await page.$eval('#liveProgressTitle', el => el.textContent);
    const progressTimer = await page.$eval('#liveProgressTimer', el => el.textContent);
    console.log(`[T+${i}s] Drawer visible: ${termVisible} | Timer: ${progressTimer} | Title: "${progressTitle}"`);
    console.log(`[T+${i}s] Console output snippet:\n${termText.slice(-300)}`);
  }

  // 2. TEST: Kliknięcie Anuluj
  console.log('\n--- TEST 2: Kliknięcie "⏹ Anuluj" (#btnCancelActiveJob) ---');
  const cancelVisible = await page.$eval('#btnCancelActiveJob', el => !el.classList.contains('hidden'));
  console.log(`Czy przycisk Anuluj jest widoczny? ${cancelVisible}`);
  if (cancelVisible) {
    await page.click('#btnCancelActiveJob');
    await page.waitForTimeout(2000);
    const termTextAfterCancel = await page.$eval('#liveConsoleOutput', el => el.textContent);
    const statusText = await page.$eval('#pipelineStatusBanner', el => el.textContent);
    console.log(`Status po anulowaniu: "${statusText.trim()}"`);
    console.log(`Console output po anulowaniu:\n${termTextAfterCancel.slice(-200)}`);
  }

  // 3. TEST: Kliknięcie "Wyczyść pliki (Reset)"
  console.log('\n--- TEST 3: Kliknięcie "Wyczyść pliki (Reset)" (#btnPurgeWorkfiles) ---');
  await page.click('#btnPurgeWorkfiles');
  await page.waitForTimeout(500);

  const isPurgeModalVisible = await page.$eval('#confirmModal', el => !el.classList.contains('hidden'));
  console.log(`Czy modal czyszczenia jest widoczny? ${isPurgeModalVisible}`);
  if (isPurgeModalVisible) {
    const purgeTitle = await page.$eval('#confirmModalTitle', el => el.textContent);
    console.log(`Modal Title: "${purgeTitle}"`);
    console.log('Potwierdzam czyszczenie...');
    await page.click('#btnAcceptConfirm');
  }

  for (let i = 1; i <= 4; i++) {
    await page.waitForTimeout(1000);
    const termText = await page.$eval('#liveConsoleOutput', el => el.textContent);
    const progressTitle = await page.$eval('#liveProgressTitle', el => el.textContent);
    const progressTimer = await page.$eval('#liveProgressTimer', el => el.textContent);
    console.log(`[Purge T+${i}s] Timer: ${progressTimer} | Title: "${progressTitle}"`);
    console.log(`[Purge T+${i}s] Console snippet:\n${termText.slice(-250)}`);
  }

  console.log('\n--- PODSUMOWANIE BŁĘDÓW PRZEGLĄDARKI ---');
  console.log(`Błędy JS (${errors.length}):`, errors);

  await browser.close();
  console.log('✅ Zakończono audyt Playwright.');
}

run().catch(err => {
  console.error('❌ Błąd krytyczny audytu Playwright:', err);
  process.exit(1);
});

