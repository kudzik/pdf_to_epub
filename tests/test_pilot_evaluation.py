"""Unit tests for the 50-page pilot evaluation module (Gate G3 / R16)."""

from datetime import datetime, timezone
from pathlib import Path
import pytest

from src.extraction_adapter import (
    BlockProvenance,
    ExtractedBlock,
    ExtractedPage,
    NormalizedBBox,
)
from src.ocr_modes import ProductionOverwriteError
from src.pilot_evaluation import (
    BlockQualityAssessment,
    PageTransitionCheck,
    PilotCostExtrapolation,
    PilotEvaluationReport,
    PilotPageSummary,
    check_page_transition,
    evaluate_block_quality,
    extrapolate_cost_and_time,
    get_system_resource_usage,
    save_pilot_report,
)


def _make_block(
    block_id: str,
    page_no: int,
    reading_order: int,
    text: str,
    block_type: str = "paragraph",
) -> ExtractedBlock:
    prov = BlockProvenance(
        run_id="test_run",
        engine="rapidocr",
        engine_version="1.0",
        source_pdf_path="input/test.pdf",
        source_pdf_sha256="test_sha",
        page_no=page_no,
    )
    return ExtractedBlock(
        block_id=block_id,
        page_no=page_no,
        reading_order=reading_order,
        block_type=block_type,
        text=text,
        bbox=NormalizedBBox(l=50.0, t=100.0, r=250.0, b=150.0),
        provenance=prov,
    )


def test_evaluate_block_quality_clean_text():
    """Verify that a well-formed paragraph is accepted without violations."""
    block = _make_block(
        "b1", 201, 1,
        "Combine the flour, salt, and 1/2 tsp baking powder in a medium bowl."
    )
    assessment = evaluate_block_quality(block)
    assert assessment.is_accepted is True
    assert len(assessment.violations) == 0
    assert assessment.block_id == "b1"


def test_evaluate_block_quality_empty_text():
    """Verify that an empty or whitespace block is flagged."""
    block = _make_block("b2", 201, 2, "   \n  ")
    assessment = evaluate_block_quality(block)
    assert assessment.is_accepted is False
    assert any("Pusty blok" in v for v in assessment.violations)


def test_evaluate_block_quality_scanner_debris():
    """Verify that scanner debris and unmapped chars are flagged."""
    block = _make_block("b3", 201, 3, "Add 1 cup sugar ■ ^p- and stir well.")
    assessment = evaluate_block_quality(block)
    assert assessment.is_accepted is False
    assert any("artefakty skanera" in v for v in assessment.violations)


def test_evaluate_block_quality_repetition_loop():
    """Verify that looping repetition degenerations are flagged."""
    block = _make_block(
        "b4", 201, 4,
        "stir thoroughly and stir thoroughly and stir thoroughly and stir thoroughly and stir thoroughly"
    )
    assessment = evaluate_block_quality(block)
    assert assessment.is_accepted is False
    assert any("zapętlenie" in v.lower() for v in assessment.violations)


def test_evaluate_block_quality_mangled_temperature():
    """Verify that mangled temperature digits (e.g. 3758°F) are detected."""
    block = _make_block(
        "b5", 201, 5,
        "Preheat the oven to 3758°F before placing the baking sheet."
    )
    assessment = evaluate_block_quality(block)
    assert assessment.is_accepted is False
    assert any("temperatura" in v.lower() for v in assessment.violations)


def test_check_page_transition_clean_sentence_boundary():
    """Verify clean sentence boundary when page A ends in period and page B starts with uppercase."""
    p_a = ExtractedPage(
        run_id="run1", page_no=201, width_pt=612.0, height_pt=792.0,
        blocks=[
            _make_block("a1", 201, 1, "This concludes the preparatory sauce step."),
        ],
    )
    p_b = ExtractedPage(
        run_id="run1", page_no=202, width_pt=612.0, height_pt=792.0,
        blocks=[
            _make_block("b1", 202, 1, "Next, prepare the pastry crust according to directions."),
        ],
    )
    trans = check_page_transition(p_a, p_b)
    assert trans.is_clean_transition is True
    assert trans.is_hyphenated_break is False
    assert trans.is_lowercase_continuation is False
    assert trans.transition_type == "sentence_boundary"


def test_check_page_transition_hyphenated_break():
    """Verify detection of hyphenated word broken across page boundary."""
    p_a = ExtractedPage(
        run_id="run1", page_no=201, width_pt=612.0, height_pt=792.0,
        blocks=[
            _make_block("a1", 201, 1, "The process requires careful concen-"),
        ],
    )
    p_b = ExtractedPage(
        run_id="run1", page_no=202, width_pt=612.0, height_pt=792.0,
        blocks=[
            _make_block("b1", 202, 1, "tration of the butter and sugar mixture."),
        ],
    )
    trans = check_page_transition(p_a, p_b)
    assert trans.is_clean_transition is True
    assert trans.is_hyphenated_break is True
    assert trans.dehyphenated_word == "concentration"
    assert trans.transition_type == "hyphenated_continuation"


def test_check_page_transition_lowercase_continuation():
    """Verify sentence continuing in lowercase onto the next page."""
    p_a = ExtractedPage(
        run_id="run1", page_no=201, width_pt=612.0, height_pt=792.0,
        blocks=[
            _make_block("a1", 201, 1, "Slowly incorporate the melted chocolate and"),
        ],
    )
    p_b = ExtractedPage(
        run_id="run1", page_no=202, width_pt=612.0, height_pt=792.0,
        blocks=[
            _make_block("b1", 202, 1, "whisk until the texture becomes glossy."),
        ],
    )
    trans = check_page_transition(p_a, p_b)
    assert trans.is_clean_transition is True
    assert trans.is_hyphenated_break is False
    assert trans.is_lowercase_continuation is True
    assert trans.transition_type == "lowercase_sentence_continuation"


def test_check_page_transition_broken_punctuation_start():
    """Verify that leading dangling punctuation on page B flags an issue."""
    p_a = ExtractedPage(
        run_id="run1", page_no=201, width_pt=612.0, height_pt=792.0,
        blocks=[
            _make_block("a1", 201, 1, "An unexpected interruption happened"),
        ],
    )
    p_b = ExtractedPage(
        run_id="run1", page_no=202, width_pt=612.0, height_pt=792.0,
        blocks=[
            _make_block("b1", 202, 1, ", which caused the emulsion to fail."),
        ],
    )
    trans = check_page_transition(p_a, p_b)
    assert trans.is_clean_transition is False
    assert trans.transition_type == "broken_punctuation_start"


def test_extrapolate_cost_and_time():
    """Verify latency percentile calculation and full book extrapolation math."""
    durations = [1.2, 1.4, 1.5, 1.6, 2.0] * 10  # 50 measurements
    extrap = extrapolate_cost_and_time(
        page_durations=durations,
        total_pilot_pages=50,
        total_elapsed_s=77.0,
        full_book_pages=914,
    )
    assert extrap.pilot_pages_count == 50
    assert extrap.full_book_pages_count == 914
    assert extrap.api_cost_usd == 0.0
    assert extrap.mean_page_duration_s > 1.0
    assert extrap.estimated_total_seconds == pytest.approx(extrap.mean_page_duration_s * 914, rel=0.01)
    assert extrap.estimated_hours_for_914_pages < 2.0  # Well under the 2-hour threshold
    assert extrap.recommended_checkpoint_batch_size == 50


def test_system_resource_usage():
    """Verify get_system_resource_usage returns positive numeric values."""
    ram, vram = get_system_resource_usage()
    assert ram >= 0.0
    assert vram >= 0.0


def test_save_pilot_report_and_guard(tmp_path: Path):
    """Verify that pilot report is correctly persisted to JSON and Markdown, and guards production path."""
    extrap = PilotCostExtrapolation(
        pilot_pages_count=50,
        full_book_pages_count=914,
        pilot_total_duration_s=75.0,
        mean_page_duration_s=1.5,
        p50_page_duration_s=1.45,
        p95_page_duration_s=1.85,
        min_page_duration_s=1.1,
        max_page_duration_s=2.1,
        estimated_total_seconds=1371.0,
        estimated_total_minutes=22.9,
        estimated_hours_for_914_pages=0.38,
        api_cost_usd=0.0,
        hardware_mode="Local RapidOCR ONNX",
        peak_ram_mb=450.0,
        peak_gpu_vram_mb=1200.0,
        recommended_checkpoint_batch_size=50,
    )

    report = PilotEvaluationReport(
        run_id="test_pilot_run",
        pilot_pages=list(range(201, 251)),
        total_pages_evaluated=50,
        total_blocks=500,
        total_accepted_blocks=490,
        total_flagged_blocks=10,
        overall_acceptance_rate_percent=98.0,
        column_interleaving_cases=0,
        broken_transitions_count=0,
        cost_extrapolation=extrap,
        production_sha_before="abc123",
        production_sha_after="abc123",
        production_preserved=True,
        checklist={
            "1_completeness_50_pages": True,
            "2_no_column_interleaving": True,
            "3_automated_acceptance_above_90_pct": True,
            "4_page_transitions_clean": True,
            "5_extrapolated_time_under_2h": True,
            "6_zero_api_cost": True,
            "7_production_source_intact": True,
        },
        verdict="PASS",
        candidate_markdown_path="output/candidates/test_pilot_run/ksiazka.md",
        decision_d10="Akceptacja wyników pilota 50 stron i autoryzacja R17.",
    )

    out_dir = tmp_path / "benchmarks" / "pilot_50_pages"
    json_path, md_path = save_pilot_report(report, out_dir)

    assert json_path.exists()
    assert md_path.exists()

    md_content = md_path.read_text(encoding="utf-8")
    assert "Raport z Pilota 50 Stron" in md_content
    assert "Bramka Gate G3" in md_content
    assert "98.0%" in md_content
    assert "Decyzja D10" in md_content

    # Verify security guard prevents saving directly to production markdown
    with pytest.raises(ProductionOverwriteError):
        save_pilot_report(report, Path("output/markdown"))

