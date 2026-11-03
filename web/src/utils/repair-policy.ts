import { assetsPreserved, imageReferences } from './repair-assets.ts';
import { evaluateRepairQuality } from './metrics.ts';

export function prose(text: string): string {
  for (const image of imageReferences(text)) text = text.replace(image, ' ');
  return text.replace(/^#{1,6}\s+/gm, '');
}

function measurements(text: string): string[] {
  text = prose(text).toLowerCase().replace(/½/g, '1/2').replace(/¼/g, '1/4').replace(/¾/g, '3/4');
  text = text.replace(/\b([13])\s+([24])(?=\s*(?:tsp|tbsp|cups?|g|kg|ml|oz|lb)\b)/g, '$1/$2');
  text = text.replace(/\bteaspoons?\b/g, 'tsp').replace(/\btablespoons?\b/g, 'tbsp').replace(/\bcups\b/g, 'cup');
  return (text.match(/(?<![\p{L}\d])\d+(?:[.,/]\d+)?\s*(?:°\s*[cf]|tsp|tbsp|cup|kg|mg|ml|g|l|oz|lbs?|eggs?|cloves?|minutes?|hours?)(?![\p{L}])/gu) || []).map(value => value.replace(/\s+/g, '').replace(',', '.')).sort();
}

// A similarity score is a heuristic, never proof of semantic fidelity.
export function assessRepair(original: string, candidate: string, source?: { text: string; confidence: number; coverage: number }, uncertain = false) {
  const metrics = evaluateRepairQuality(prose(original), prose(candidate));
  const issues: string[] = [];
  if (JSON.stringify(measurements(original)) !== JSON.stringify(measurements(candidate))) issues.push('Zmieniono ilości, miary lub temperatury — wymagana kontrola ze skanem');
  if (!candidate.trim()) issues.push('Model zwrócił pusty tekst');
  if (!assetsPreserved(original, candidate)) issues.push('Zmieniono odnośniki do grafik');
  if (uncertain) issues.push('Model zgłosił nieczytelny lub niepewny fragment');
  if (candidate.trim() === original.trim()) issues.push('Brak zmiany — usterka nie została naprawiona');
  if (/[\p{L}][\^«»£][\p{L}]|[\p{L}]°[\p{L}]|�/u.test(prose(candidate))) issues.push('Pozostały uszkodzone znaki OCR');
  if (source) {
    const evidence = evaluateRepairQuality(source.text, prose(candidate));
    if (source.confidence < .7 || source.coverage < .28) issues.push('Odczyt źródłowy ma zbyt małą pewność');
    if (!evidence.invariantsPreserved) issues.push('Liczby lub jednostki nie zgadzają się z ponownym odczytem skanu');
    if (evidence.similarity < 85) issues.push('Tekst zbyt mocno odbiega od ponownego odczytu skanu');
    const ratio = prose(candidate).length / Math.max(1, source.text.length);
    if (ratio < .85 || ratio > 1.15) issues.push('Zmieniono długość fragmentu względem skanu');
    return { accepted: !issues.length, issues, metrics, sourceMetrics: evidence };
  }
  if (!metrics.invariantsPreserved) issues.push(`Zmieniono liczby lub jednostki (brakujące: ${metrics.missingNumbers.join(', ') || '—'}; dodane: ${metrics.addedNumbers.join(', ') || '—'})`);
  if (metrics.similarity < 90) issues.push('Zbyt duża rekonstrukcja bez potwierdzenia w skanie');
  return { accepted: !issues.length, issues, metrics, sourceMetrics: null };
}
