/**
 * Moduł pomiaru i ewaluacji jakości poprawek OCR.
 * Mierzy wskaźnik podobieństwa leksykalnego, redukcję szumów OCR
 * oraz zachowanie niezmienników (liczb, miar kulinarnych i temperatur).
 */

export interface RepairEvaluation {
  qualityScore: number;       // 0 - 100%
  similarity: number;         // 0 - 100%
  noiseRemoved: number;       // Liczba usuniętych artefaktów OCR
  invariantsPreserved: boolean; // Czy liczby i proporcje nie zostały sfałszowane
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
  // Dopasowuje liczby, w tym ułamki (np. 1/2, 1/4) oraz temperatury
  const matches = text.match(/\b\d+(?:[.,/]\d+)?\b/g);
  return matches || [];
}

/**
 * Liczy znaki typowego szumu OCR.
 */
function countNoiseChars(text: string): number {
  const noisePattern = /[\^°■|£«»¥~¬]/g;
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

  // Sprawdzamy czy liczby z oryginału znajdują się w poprawionym tekście
  // (z uwzględnieniem, że ułamek '1 2' mógł zostać scalony w '1/2')
  const missingNumbers: string[] = [];
  for (const num of origNums) {
    if (!repNums.includes(num)) {
      // Wyjątek: jeśli w oryginale było osobno '1' i '2', a w poprawionym jest '1/2'
      if ((num === '1' || num === '2') && repNums.includes('1/2')) {
        continue;
      }
      if ((num === '1' || num === '4') && repNums.includes('1/4')) {
        continue;
      }
      if ((num === '3' || num === '4') && repNums.includes('3/4')) {
        continue;
      }
      missingNumbers.push(num);
    }
  }

  const invariantsPreserved = missingNumbers.length === 0;

  // Obliczenie wskaźnika Quality Score (0-100)
  let qualityScore = similarity;

  // Premia za usunięcie szumów OCR
  if (noiseRemoved > 0) {
    qualityScore = Math.min(100, qualityScore + 2);
  }

  // Kara za naruszenie liczb (potencjalna halucynacja)
  if (!invariantsPreserved) {
    qualityScore = Math.max(20, qualityScore - (missingNumbers.length * 20));
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
    verdictLabel = 'Wzorowa jakość (brak halucynacji, liczby zachowane)';
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
    missingNumbers,
    verdict,
    verdictLabel,
  };
}

