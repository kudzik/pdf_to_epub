// Keep image markup outside the language model: repair only the intervening prose.
const imagePattern = /!\[(?:\\.|[^\]\\])*\]\((?:\\.|[^()\\]|\([^()]*\))*\)/g;

export function imageReferences(text: string): string[] {
  return text.match(imagePattern) || [];
}

export function assetsPreserved(original: string, repaired: string): boolean {
  return JSON.stringify(imageReferences(original)) === JSON.stringify(imageReferences(repaired));
}

export async function repairPreservingAssets(
  original: string,
  repair: (text: string) => Promise<string>,
): Promise<string> {
  const chunks = original.split(new RegExp(`(${imagePattern.source})`, 'g'));
  const result: string[] = [];
  for (let i = 0; i < chunks.length; i++) {
    const chunk = chunks[i];
    if (i % 2 || !chunk.trim()) {
      result.push(chunk);
    } else {
      const repaired = (await repair(chunk.trim())).trim();
      if (!repaired) throw new Error('Model zwrócił pustą odpowiedź dla fragmentu tekstu');
      result.push((chunk.match(/^\s*/)?.[0] || '') + repaired + (chunk.match(/\s*$/)?.[0] || ''));
    }
  }
  const text = result.join('');
  if (!assetsPreserved(original, text)) throw new Error('Model zmienił odnośniki do grafik');
  return text;
}
