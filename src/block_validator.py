"""Strict block validation and correction policy module.

Implements invariant verification for block-level OCR repairs and candidate text:
- Preserves images and assets (forbids adding, removing, or altering image links).
- Preserves culinary quantities, fractions, units, and oven temperatures (375°F vs 3758, T. vs t.).
- Detects omissions and drastically truncated text.
- Detects repetition loops and token degenerations.
- Detects truncated generations (unterminated sentences).
- Enforces dynamic response length limits tied directly to the source block size.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class BlockValidationErrorType(str, Enum):
    """Categorized violation types for block repair validation."""

    EMPTY_OR_NO_CHANGE = "empty_or_no_change"
    ASSET_TAMPERING = "asset_tampering"
    MEASUREMENT_CORRUPTED = "measurement_corrupted"
    UNIT_CORRUPTED = "unit_corrupted"
    OMISSION_DETECTED = "omission_detected"
    REPETITION_LOOP = "repetition_loop"
    TRUNCATED_GENERATION = "truncated_generation"
    LENGTH_DISPROPORTION = "length_disproportion"
    OCR_DEBRIS_REMAINING = "ocr_debris_remaining"


class BlockValidationViolation(BaseModel):
    """Details of a single validation rule violation."""

    error_type: BlockValidationErrorType
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class BlockValidationResult(BaseModel):
    """Result of validating a proposed text repair against the original/source block."""

    is_valid: bool
    violations: list[BlockValidationViolation] = Field(default_factory=list)
    prose_similarity: float = 100.0
    length_ratio: float = 1.0
    original_measurements: list[str] = Field(default_factory=list)
    candidate_measurements: list[str] = Field(default_factory=list)
    computed_token_limit: int = 100


def extract_assets(text: str) -> list[str]:
    """Extract all markdown image references ![alt](url) from text."""
    pattern = r"!\[.*?\]\([^\)]+\)"
    return re.findall(pattern, text)


def strip_assets(text: str) -> str:
    """Remove markdown image references from text to isolate pure prose."""
    pattern = r"!\[.*?\]\([^\)]+\)"
    return re.sub(pattern, " ", text).strip()


def compute_block_token_limit(original_text: str) -> int:
    """Dynamically calculate maximum allowed response tokens based on original block size."""
    words = strip_assets(original_text).split()
    word_count = len(words)
    # Proportional limit: 1.35x input words plus safety margin of 20 tokens, min 40
    return max(40, int(word_count * 1.35) + 20)


def extract_culinary_measurements(text: str) -> list[str]:
    """Extract and normalize culinary quantities, fractions, units, and temperatures.

    Preserves case distinction between 'T.' (tablespoon) and 't.' (teaspoon).
    Normalizes unicode fractions (½ -> 1/2) and space-broken fractions (1 2 tsp -> 1/2 tsp).
    """
    cleaned = strip_assets(text)

    # 1. Normalize unicode fractions
    cleaned = cleaned.replace("½", "1/2").replace("¼", "1/4").replace("¾", "3/4").replace("⅛", "1/8")

    # 2. Fix spaced fractions before known units
    cleaned = re.sub(
        r"\b([1357])\s+([248])(?=\s*(?:tsp|tbsp|cups?|t\.|T\.|g|kg|ml|oz|lb)\b)",
        r"\1/\2",
        cleaned,
    )

    # 3. Normalize common word units while keeping short abbreviation case (T. vs t.)
    # We replace full words with standard tokens
    cleaned = re.sub(r"\bteaspoons?\b", "t.", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\btablespoons?\b", "T.", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bcups?\b", "cup", cleaned, flags=re.IGNORECASE)

    # 4. Extract regex tokens for numbers + units and temperatures
    # Note: case sensitive for T. vs t.
    pattern = (
        r"(?<![\w\d])"
        r"(\d+(?:[.,/]\d+)?)"
        r"\s*"
        r"(°\s*[CFcf]?|T\.|t\.|tsp|tbsp|cup|kg|mg|ml|g|l|oz|lbs?|eggs?|cloves?|minutes?|hours?)"
        r"(?![\w])"
    )

    matches = re.findall(pattern, cleaned)
    tokens: list[str] = []
    for num, unit in matches:
        norm_num = num.replace(",", ".")
        norm_unit = re.sub(r"\s+", "", unit)
        # Normalize °c / °f case
        if "°" in norm_unit:
            norm_unit = norm_unit.upper()
        tokens.append(f"{norm_num}{norm_unit}")

    # Also capture standalone numbers (e.g. "Add 2 eggs", counts, or numbered lists)
    standalone_pattern = r"(?<![\w\d/.,])(\d+(?:[.,]\d+)?)(?=[.,]?(?:\s|$))"
    standalone_nums = re.findall(standalone_pattern, cleaned)

    # We return sorted combined representation
    all_tokens = sorted(tokens + [f"NUM:{n.replace(',', '.')}" for n in standalone_nums])
    return all_tokens


def detect_repetitions(text: str) -> tuple[bool, Optional[str]]:
    """Detect repetitive looping degenerations common in LLM failure modes."""
    cleaned = strip_assets(text)
    # Remove punctuation for word matching
    words = re.findall(r"\b[\w'-]+\b", cleaned.lower())
    if len(words) < 6:
        return False, None

    # Check 1-gram to 5-gram repetitions
    for n in range(1, 6):
        for i in range(len(words) - 2 * n + 1):
            chunk1 = words[i : i + n]
            chunk2 = words[i + n : i + 2 * n]
            if chunk1 == chunk2:
                # Check if repeated at least 3 times (or 4 times for single words)
                chunk3 = words[i + 2 * n : i + 3 * n]
                if (n > 1 and chunk1 == chunk3) or (n == 1 and chunk1 == chunk3 and words[i + 3 * n : i + 4 * n] == chunk1):
                    repeated_phrase = " ".join(chunk1)
                    return True, f"Wykryto zapętlenie frazy: '{repeated_phrase}'"

    # Check sentence repetition
    sentences = [s.strip().lower() for s in re.split(r"[.!?]\s+", cleaned) if len(s.strip()) > 15]
    for s in sentences:
        if sentences.count(s) >= 2:
            return True, f"Wykryto powtórzenie całego zdania: '{s[:40]}...'"

    return False, None


def detect_truncated_generation(candidate: str, original: str) -> tuple[bool, Optional[str]]:
    """Detect if candidate generation was abruptly truncated mid-sentence or mid-word."""
    cand_stripped = strip_assets(candidate).strip()
    orig_stripped = strip_assets(original).strip()

    if not cand_stripped:
        return True, "Tekst kandydata jest pusty."

    # If original ends in terminal punctuation, candidate should also end cleanly
    terminal_punct = (".", "!", "?", '”', '"', "')", ":", ";", "*")
    if orig_stripped and orig_stripped.endswith(terminal_punct):
        if not cand_stripped.endswith(terminal_punct):
            # Check if it ends with hyphen (broken word) or comma
            if cand_stripped.endswith(("-", ",", "(")):
                return True, f"Generacja przerwana znakiem '{cand_stripped[-1]}'."
            return True, "Ucięta generacja: brak końcowego znaku interpunkcyjnego."

    return False, None


def compute_bigram_similarity(s1: str, s2: str) -> float:
    """Compute character bigram Dice similarity percentage (0-100)."""
    s1_clean = re.sub(r"\s+", " ", s1).strip().lower()
    s2_clean = re.sub(r"\s+", " ", s2).strip().lower()

    if s1_clean == s2_clean:
        return 100.0
    if len(s1_clean) < 2 or len(s2_clean) < 2:
        return 0.0

    b1 = [s1_clean[i : i + 2] for i in range(len(s1_clean) - 1)]
    b2 = [s2_clean[i : i + 2] for i in range(len(s2_clean) - 1)]

    counts: dict[str, int] = {}
    for b in b1:
        counts[b] = counts.get(b, 0) + 1

    intersection = 0
    for b in b2:
        if counts.get(b, 0) > 0:
            intersection += 1
            counts[b] -= 1

    return (2.0 * intersection / (len(b1) + len(b2))) * 100.0


def validate_block_repair(
    original: str,
    candidate: str,
    source_ocr: Optional[str] = None,
    allow_unchanged: bool = False,
) -> BlockValidationResult:
    """Comprehensive validation of proposed candidate text against the original paragraph/block.

    Checks:
    1. Empty response or zero change
    2. Asset link preservation
    3. Culinary measurements and temperature invariants
    4. Truncation and omission detection
    5. Repetition loops
    6. Length disproportion / rambling
    7. Lingering OCR debris
    """
    violations: list[BlockValidationViolation] = []
    token_limit = compute_block_token_limit(original)

    orig_clean = original.strip()
    cand_clean = candidate.strip()

    # 1. Empty or No Change
    if not cand_clean:
        violations.append(
            BlockValidationViolation(
                error_type=BlockValidationErrorType.EMPTY_OR_NO_CHANGE,
                message="Kandydat jest pusty.",
            )
        )
        return BlockValidationResult(
            is_valid=False,
            violations=violations,
            prose_similarity=0.0,
            length_ratio=0.0,
            computed_token_limit=token_limit,
        )

    if not allow_unchanged and cand_clean == orig_clean:
        violations.append(
            BlockValidationViolation(
                error_type=BlockValidationErrorType.EMPTY_OR_NO_CHANGE,
                message="Brak zmiany: zaproponowany tekst jest identyczny z oryginałem.",
            )
        )

    # 2. Asset Integrity
    orig_assets = extract_assets(original)
    cand_assets = extract_assets(candidate)
    if orig_assets != cand_assets:
        violations.append(
            BlockValidationViolation(
                error_type=BlockValidationErrorType.ASSET_TAMPERING,
                message="Zmieniono, usunięto lub dodano odnośniki do grafik (assets).",
                details={"original_assets": orig_assets, "candidate_assets": cand_assets},
            )
        )

    orig_prose = strip_assets(original)
    cand_prose = strip_assets(candidate)

    # 3. Measurements and Quantities
    orig_measurements = extract_culinary_measurements(original)
    cand_measurements = extract_culinary_measurements(candidate)

    if orig_measurements != cand_measurements:
        missing = [m for m in orig_measurements if m not in cand_measurements]
        added = [m for m in cand_measurements if m not in orig_measurements]

        # Check if units specifically changed (e.g. T. -> t.)
        unit_violation = False
        for m in missing:
            if "T." in m and any("t." in a for a in added):
                unit_violation = True
                break
            if "°" in m and not any("°" in a for a in added):
                unit_violation = True
                break

        err_type = BlockValidationErrorType.UNIT_CORRUPTED if unit_violation else BlockValidationErrorType.MEASUREMENT_CORRUPTED
        violations.append(
            BlockValidationViolation(
                error_type=err_type,
                message=f"Zmieniono lub zgubiono ilości, miary lub temperatury (brakujące: {missing or 'brak'}, dodane: {added or 'brak'}).",
                details={"missing": missing, "added": added},
            )
        )

    # 4. Length disproportion (rambling or drastic omission)
    orig_len = max(1, len(orig_prose))
    cand_len = len(cand_prose)
    ratio = cand_len / orig_len

    if ratio < 0.70 and len(orig_prose) > 30:
        violations.append(
            BlockValidationViolation(
                error_type=BlockValidationErrorType.OMISSION_DETECTED,
                message=f"Wykryto drastyczne pominięcie treści: długość kandydata to zaledwie {ratio*100:.1f}% oryginału.",
                details={"ratio": ratio, "orig_len": orig_len, "cand_len": cand_len},
            )
        )
    elif ratio > 1.40 and len(orig_prose) > 30:
        violations.append(
            BlockValidationViolation(
                error_type=BlockValidationErrorType.LENGTH_DISPROPORTION,
                message=f"Wykryto nadmierne rozszerzenie / halucynację treści: długość kandydata to {ratio*100:.1f}% oryginału.",
                details={"ratio": ratio, "orig_len": orig_len, "cand_len": cand_len},
            )
        )

    # 5. Repetition Loops
    has_loop, loop_msg = detect_repetitions(candidate)
    if has_loop and loop_msg:
        violations.append(
            BlockValidationViolation(
                error_type=BlockValidationErrorType.REPETITION_LOOP,
                message=loop_msg,
            )
        )

    # 6. Truncated Generation
    is_truncated, trunc_msg = detect_truncated_generation(candidate, original)
    if is_truncated and trunc_msg:
        violations.append(
            BlockValidationViolation(
                error_type=BlockValidationErrorType.TRUNCATED_GENERATION,
                message=trunc_msg,
            )
        )

    # 7. Remaining OCR debris / artifacts
    debris_pattern = r"[a-zA-Z0-9\u0080-\uFFFF][\^«»£~|][a-zA-Z0-9\u0080-\uFFFF]|\b[■\^]\s*p-|\bFlraS\s*-\*"
    if re.search(debris_pattern, cand_prose) or "\ufffd" in cand_prose:
        violations.append(
            BlockValidationViolation(
                error_type=BlockValidationErrorType.OCR_DEBRIS_REMAINING,
                message="W tekście pozostały uszkodzone artefakty skanera (np. znaki specjalne wewnątrz słów).",
            )
        )

    similarity = compute_bigram_similarity(orig_prose, cand_prose)

    return BlockValidationResult(
        is_valid=len(violations) == 0,
        violations=violations,
        prose_similarity=similarity,
        length_ratio=ratio,
        original_measurements=orig_measurements,
        candidate_measurements=cand_measurements,
        computed_token_limit=token_limit,
    )
