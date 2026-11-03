/**
 * Moduł pomiaru i ewaluacji jakości poprawek OCR.
 * Mierzy wskaźnik podobieństwa leksykalnego, redukcję szumów OCR
 * oraz zachowanie niezmienników (liczb, miar kulinarnych i temperatur).
 */

export interface RepairEvaluation {
  qualityScore: number;       // 0 - 100%
  similarity: number;         // 0 - 100%
  noiseRemoved: number;       // Liczba usuniętych artefaktów OCR
  unitsPreserved: boolean;
  invariantsPreserved: boolean; // Czy liczby i proporcje nie zostały sfałszowane
  addedNumbers: string[];
  missingNumbers: string[];   // Liczby obecne przed, a brakujące po
  verdict: 'EXCELLENT' | 'GOOD' | 'SUSPICIOUS' | 'UNSAFE';
  verdictLabel: string;
}

/**
 * Prosty i szybki algorytm podobieństwa Bigramów (Sørensen-Dice Coefficient).
 */
export function calculateSimilarity(s1: string, s2: string): number {
  if (s1 === s2) return 100;
  if (!s1 || !s2) return 0;

  const clean1 = s1.trim().toLowerCase();
  const clean2 = s2.trim().toLowerCase();

  if (clean1 === clean2) return 100;
  if (clean1.length < 2 || clean2.length < 2) return 0;

  const getBigrams = (str: string) => {
    const bigrams = new Map<string, number>();
    for (let i = 0; i < str.length - 1; i++) {
      const bigram = str.substring(i, i + 2);
      bigrams.set(bigram, (bigrams.get(bigram) || 0) + 1);
    }
    return bigrams;
  };

  const b1 = getBigrams(clean1);
  const b2 = getBigrams(clean2);

  let intersection = 0;
  for (const [bigram, count] of b1.entries()) {
    if (b2.has(bigram)) {
      intersection += Math.min(count, b2.get(bigram)!);
    }
  }

  const total = (clean1.length - 1) + (clean2.length - 1);
  return Math.round(((2.0 * intersection) / total) * 1000) / 10;
}

/**
 * Wykrywa liczby całkowite, dziesiętne i ułamki w tekście.
 */
function extractNumbers(text: string): string[] {
  const fractions: Record<string, string> = { '½': '1/2', '¼': '1/4', '¾': '3/4', '⅓': '1/3', '⅔': '2/3' };
  text = text.replace(/[½¼¾⅓⅔]/g, ch => ` ${fractions[ch]} `);
  text = text.replace(/\b([13])\s+([24])(?=\s*(?:tsp|tbsp|cups?|teaspoons?|tablespoons?|lbs?|oz|g|kg|ml|l)\b)/gi, '$1/$2');
  return (text.match(/(?<![\p{L}\d])\d+(?:[.,/]\d+)?/gu) || []).map(n => n.replace(',', '.'));
}

function extractUnits(text: string): string[] {
  const aliases: Record<string, string> = { teaspoon: 'tsp', teaspoons: 'tsp', tablespoon: 'tbsp', tablespoons: 'tbsp', cups: 'cup', lbs: 'lb', ounces: 'oz', ounce: 'oz' };
  return (text.toLowerCase().match(/(?<![\p{L}])(?:tsp|tbsp|teaspoons?|tablespoons?|cups?|lbs?|ounces?|oz|kg|mg|ml|g|l)(?![\p{L}])/gu) || []).map(unit => aliases[unit] || unit).sort();
}

/**
 * Liczy znaki typowego szumu OCR.
 */
function countNoiseChars(text: string): number {
  const noisePattern = /[\^■£¥¬�]|(?<=[\p{L}])°(?=[\p{L}])/gu;
  const matches = text.match(noisePattern);
  return matches ? matches.length : 0;
}

/**
 * Ewaluacja jakości naprawy akapitu:
 * - Weryfikuje, czy model nie wyhalucynował nowej treści (Similarity >= 75%)
 * - Sprawdza, czy kluczowe liczby/miary zostały zachowane
 * - Liczy usunięte usterki OCR
 */
export function evaluateRepairQuality(original: string, repaired: string): RepairEvaluation {
  const similarity = calculateSimilarity(original, repaired);
  const origNoise = countNoiseChars(original);
  const repNoise = countNoiseChars(repaired);
  const noiseRemoved = Math.max(0, origNoise - repNoise);

  const origNums = extractNumbers(original);
  const repNums = extractNumbers(repaired);

  const remaining = [...repNums];
  const missingNumbers: string[] = [];
  for (const num of origNums) {
    const index = remaining.indexOf(num);
    if (index < 0) missingNumbers.push(num);
    else remaining.splice(index, 1);
  }
  const addedNumbers = remaining;
  const unitsPreserved = JSON.stringify(extractUnits(original)) === JSON.stringify(extractUnits(repaired));
  const invariantsPreserved = missingNumbers.length === 0 && addedNumbers.length === 0 && unitsPreserved;

  // Obliczenie wskaźnika Quality Score (0-100)
  let qualityScore = similarity;

  // Premia za usunięcie szumów OCR
  if (noiseRemoved > 0) {
    qualityScore = Math.min(100, qualityScore + 2);
  }

  // Kara za naruszenie liczb (potencjalna halucynacja)
  if (!invariantsPreserved) {
    qualityScore = Math.max(20, qualityScore - ((missingNumbers.length + addedNumbers.length) * 20));
  }

  // Kara za zbyt dużą odchyłkę długości
  const lengthRatio = original.length > 0 ? repaired.length / original.length : 1;
  if (lengthRatio < 0.7 || lengthRatio > 1.3) {
    qualityScore = Math.max(10, qualityScore - 25);
  }

  qualityScore = Math.round(qualityScore);

  let verdict: 'EXCELLENT' | 'GOOD' | 'SUSPICIOUS' | 'UNSAFE' = 'GOOD';
  let verdictLabel = 'Prawidłowa rekonstrukcja';

  if (qualityScore >= 95 && invariantsPreserved) {
    verdict = 'EXCELLENT';
    verdictLabel = 'Wysokie podobieństwo; liczby zachowane (ocena heurystyczna)';
  } else if (qualityScore >= 80 && invariantsPreserved) {
    verdict = 'GOOD';
    verdictLabel = 'Dobra rekonstrukcja (niezmienniki zachowane)';
  } else if (qualityScore >= 60) {
    verdict = 'SUSPICIOUS';
    verdictLabel = 'Wymaga uwagi (wykryto zmianę parametrów/liczb)';
  } else {
    verdict = 'UNSAFE';
    verdictLabel = 'Podejrzenie halucynacji (znaczna zmiana semantyki)';
  }

  return {
    qualityScore,
    similarity,
    noiseRemoved,
    invariantsPreserved,
    unitsPreserved,
    missingNumbers,
    addedNumbers,
    verdict,
    verdictLabel,
  };
}

