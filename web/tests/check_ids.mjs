import { chromium } from 'playwright';
import fs from 'fs';

const content = fs.readFileSync(new URL('../src/pages/index.astro', import.meta.url), 'utf-8');
const idMatches = [...content.matchAll(/document\.getElementById\(['"]([^'"]+)['"]\)/g)].map(m => m[1]);
const uniqueIds = [...new Set(idMatches)];

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.goto('http://localhost:4343', { waitUntil: 'networkidle' });
await page.waitForTimeout(500);

console.log('Sprawdzanie', uniqueIds.length, 'unikalnych ID z getElementById...');
const missing = [];
for (const id of uniqueIds) {
  try {
    const el = await page.$(`[id="${id}"]`);
    if (!el) {
      missing.push(id);
    }
  } catch (e) {}
}
console.log('Brakujące elementy w HTML:', missing);
await browser.close();
