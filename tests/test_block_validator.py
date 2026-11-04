"""Unit tests for block-level validation and correction invariants (R13).

Covers:
- Asset preservation and tampering prevention.
- Culinary quantities, fractions, units, and temperature invariants.
- Omissions and truncated generation detection.
- Repetition loops and LLM degeneration detection.
- Dynamic token limit calculation tied to block size.
- Integration with patch_paragraph and patch_batch_from_file.
"""

from pathlib import Path
import pytest

from src.block_validator import (
    BlockValidationErrorType,
    compute_block_token_limit,
    detect_repetitions,
    detect_truncated_generation,
    extract_assets,
    extract_culinary_measurements,
    validate_block_repair,
)
from src.repair import patch_paragraph, patch_batch_from_file
from src.write_control import InvalidPatchError


def test_asset_preservation_and_tampering():
    orig = "Preheat oven. ![Diagram](assets/oven_chart.png) Bake for 25 minutes."

    # Valid: asset retained exactly
    cand_valid = "Preheat the oven. ![Diagram](assets/oven_chart.png) Bake for 25 minutes."
    res = validate_block_repair(orig, cand_valid)
    assert res.is_valid is True

    # Invalid: asset removed
    cand_removed = "Preheat the oven. Bake for 25 minutes."
    res = validate_block_repair(orig, cand_removed)
    assert res.is_valid is False
    assert any(v.error_type == BlockValidationErrorType.ASSET_TAMPERING for v in res.violations)

    # Invalid: asset link altered
    cand_altered = "Preheat oven. ![Diagram](assets/other.png) Bake for 25 minutes."
    res = validate_block_repair(orig, cand_altered)
    assert res.is_valid is False
    assert any(v.error_type == BlockValidationErrorType.ASSET_TAMPERING for v in res.violations)


def test_culinary_measurements_and_temperatures():
    orig = "Bake at 375° for 45 minutes with 2 T. butter and 1 2 tsp salt."

    # Valid: equivalent normalized fractions and units
    cand_valid = "Bake at 375° for 45 minutes with 2 T. butter and 1/2 tsp salt."
    res = validate_block_repair(orig, cand_valid)
    assert res.is_valid is True

    # Invalid: temperature altered to 3758 (common OCR corruption)
    cand_temp_corrupt = "Bake at 3758 for 45 minutes with 2 T. butter and 1/2 tsp salt."
    res = validate_block_repair(orig, cand_temp_corrupt)
    assert res.is_valid is False
    assert any(v.error_type in (BlockValidationErrorType.MEASUREMENT_CORRUPTED, BlockValidationErrorType.UNIT_CORRUPTED) for v in res.violations)

    # Invalid: Unit tablespoon (T.) demoted to teaspoon (t.)
    cand_unit_demoted = "Bake at 375° for 45 minutes with 2 t. butter and 1/2 tsp salt."
    res = validate_block_repair(orig, cand_unit_demoted)
    assert res.is_valid is False
    assert any(v.error_type == BlockValidationErrorType.UNIT_CORRUPTED for v in res.violations)

    # Invalid: Fraction dropped
    cand_frac_dropped = "Bake at 375° for 45 minutes with 2 T. butter and salt."
    res = validate_block_repair(orig, cand_frac_dropped)
    assert res.is_valid is False
    assert any(v.error_type == BlockValidationErrorType.MEASUREMENT_CORRUPTED for v in res.violations)


def test_omission_and_length_disproportion():
    orig = (
        "An exception to this rule is Polish hunter's seasonings: Even if you never serve venison or hare, "
        "this delightful seasoning can be used to give an interesting twist of flavor to chicken, pork, beef, "
        "and other meats as well as soups and gravies. For variety, use it instead of Italian seasoning in such dishes."
    )

    # Invalid: drastically dropped content (<70% length)
    cand_omitted = "Polish hunter's seasonings can be used in chicken and pork."
    res = validate_block_repair(orig, cand_omitted)
    assert res.is_valid is False
    assert any(v.error_type == BlockValidationErrorType.OMISSION_DETECTED for v in res.violations)

    # Invalid: rambling length (>140% length)
    cand_rambling = (
        orig + " Furthermore, you should always consult culinary history when cooking Polish dishes. "
        "In addition, remember to keep your kitchen clean and wash your hands thoroughly before preparing any meal. "
        "A good chef always prepares ingredients in advance."
    )
    res = validate_block_repair(orig, cand_rambling)
    assert res.is_valid is False
    assert any(v.error_type == BlockValidationErrorType.LENGTH_DISPROPORTION for v in res.violations)


def test_repetition_loops():
    cand_loop = "Combine savory, marjoram, coriander and sift together and sift together and sift together."
    has_loop, msg = detect_repetitions(cand_loop)
    assert has_loop is True
    assert "zapętlenie" in msg

    res = validate_block_repair("Combine savory, marjoram, coriander and sift together.", cand_loop)
    assert res.is_valid is False
    assert any(v.error_type == BlockValidationErrorType.REPETITION_LOOP for v in res.violations)


def test_truncated_generation():
    orig = "Following the instructions above, crush or grind and sift together all ingredients."

    # Abrupt end mid-sentence
    cand_trunc = "Following the instructions above, crush or grind and sift"
    is_trunc, msg = detect_truncated_generation(cand_trunc, orig)
    assert is_trunc is True

    res = validate_block_repair(orig, cand_trunc)
    assert res.is_valid is False
    assert any(v.error_type == BlockValidationErrorType.TRUNCATED_GENERATION for v in res.violations)


def test_dynamic_token_limit():
    short_text = "POLISH MILD HERB PEPPER"
    assert compute_block_token_limit(short_text) >= 40

    long_text = "word " * 100
    limit = compute_block_token_limit(long_text)
    assert limit >= 150
    assert limit <= 200


def test_patch_paragraph_enforces_invariants(tmp_path: Path):
    md_file = tmp_path / "book.md"
    initial_text = "Combine 2 t. savory and 1 t. caraway."
    md_file.write_text(initial_text, encoding="utf-8")

    # Attempt to apply a corrupted patch (dropping 1 t.)
    with pytest.raises(InvalidPatchError, match="naruszenie niezmienników"):
        patch_paragraph(
            markdown_path=md_file,
            paragraph_index=0,
            replacement_text="Combine 2 t. savory and caraway.",
            validate_invariants=True,
        )

    # Ensure file was not modified
    assert md_file.read_text(encoding="utf-8") == initial_text

    # Valid patch succeeds
    valid_replacement = "Combine 2 t. savory and 1 t. caraway."
    # With typo fixed
    valid_cleaned = "Combine: 2 t. savory and 1 t. caraway."
    ok = patch_paragraph(
        markdown_path=md_file,
        paragraph_index=0,
        replacement_text=valid_cleaned,
        validate_invariants=True,
    )
    assert ok is True
    assert md_file.read_text(encoding="utf-8") == valid_cleaned


def test_patch_batch_enforces_invariants(tmp_path: Path):
    import json
    md_file = tmp_path / "book.md"
    initial_text = "Paragraph 0: 2 cups milk.\n\nParagraph 1: 350° for 30 minutes."
    md_file.write_text(initial_text, encoding="utf-8")

    # Batch with 1 valid patch and 1 corrupted patch (altered temperature)
    batch_file = tmp_path / "fixes.json"
    batch_data = [
        {"index": 0, "text": "Paragraph 0: 2 cups fresh milk."},
        {"index": 1, "text": "Paragraph 1: 3508 for 30 minutes."},  # corrupt: 350° -> 3508
    ]
    batch_file.write_text(json.dumps(batch_data), encoding="utf-8")

    # Entire batch must be rejected without any partial modification
    with pytest.raises(InvalidPatchError, match="naruszenie niezmienników w akapicie #1"):
        patch_batch_from_file(
            markdown_path=md_file,
            fixes_file=batch_file,
            validate_invariants=True,
        )

    # Document remains strictly unchanged
    assert md_file.read_text(encoding="utf-8") == initial_text

