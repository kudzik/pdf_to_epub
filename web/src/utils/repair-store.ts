import fs from 'node:fs';
import { randomUUID } from 'node:crypto';
import { assetsPreserved } from './repair-assets.ts';

interface Patch { index: number; text: string; original?: string }

export function writeRepairs(mdPath: string, patches: Patch[]) {
  const paragraphs = fs.readFileSync(mdPath, 'utf8').split('\n\n');
  const indices = new Set<number>();
  let changed = 0;
  for (const patch of patches) {
    const { index, text, original } = patch;
    if (!Number.isInteger(index) || index < 0 || index >= paragraphs.length || indices.has(index)) throw new Error('Nieprawidłowy lub powtórzony indeks akapitu');
    indices.add(index);
    if (original !== undefined && paragraphs[index].trim() !== original.trim()) {
      const error = new Error('Konflikt zapisu. Odśwież raport.');
      Object.assign(error, { status: 409 });
      throw error;
    }
    if (typeof text !== 'string' || !text.trim()) throw new Error('Nie można zapisać pustej rekonstrukcji');
    if (!assetsPreserved(paragraphs[index], text)) throw new Error('Zapis zablokowany: zmieniono grafiki');
    const replacement = text.trim().replace(/\r\n/g, '\n').replace(/\n\s*\n/g, '\n');
    if (paragraphs[index].trim() !== replacement) changed++;
    paragraphs[index] = replacement;
  }
  if (!changed) return 0;
  if (!fs.existsSync(mdPath + '.bak')) fs.copyFileSync(mdPath, mdPath + '.bak');
  const temporary = mdPath + `.${randomUUID()}.tmp`;
  try {
    fs.writeFileSync(temporary, paragraphs.join('\n\n'), 'utf8');
    fs.renameSync(temporary, mdPath);
  } finally {
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
  return changed;
}

export function writeRepair(mdPath: string, index: number, original: string, replacement: string) {
  return writeRepairs(mdPath, [{ index, text: replacement, original }]);
}
