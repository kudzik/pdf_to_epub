"""Unit tests for quality metrics, CER/WER, critical quantities, and negative corruption tests."""

from pathlib import Path
import pytest

from src.benchmark import (
    normalize_text_for_metrics,
    levenshtein_distance,
    compute_cer,
    compute_wer,
    detect_omissions_and_repetitions,
    evaluate_critical_quantities,
    evaluate_page_quality,
    evaluate_candidate_run,
    compute_percentiles,
    compare_candidate_manifests,
    format_comparison_markdown,
)
from src.ground_truth import (
    PageGroundTruth,
    GroundTruthBlock,
    CriticalQuantity,
)
from src.ocr_modes import (
    CandidateRunConfig,
    CandidateRunManifest,
    OcrEngineMode,
)


def test_normalize_text_for_metrics():
    raw = '  “Hello”  world — this is a   test…\nNew line.  '
    norm = normalize_text_for_metrics(raw)
    assert norm == '"Hello" world - this is a test… New line.'


def test_levenshtein_distance():
    assert levenshtein_distance("", "") == 0
    assert levenshtein_distance("kitten", "sitting") == 3
    assert levenshtein_distance(["apple", "banana"], ["apple", "banana"]) == 0
    assert levenshtein_distance(["apple", "banana"], ["apple", "orange"]) == 1


def test_cer_and_wer_computation():
    ref = "The quick brown fox jumps over the lazy dog."
    hyp_exact = "The quick brown fox jumps over the lazy dog."
    assert compute_cer(ref, hyp_exact) == 0.0
    assert compute_wer(ref, hyp_exact) == 0.0

    hyp_one_word_diff = "The quick brown fox jumps over a lazy dog."
    assert compute_wer(ref, hyp_one_word_diff) > 0.0
    assert compute_cer(ref, hyp_one_word_diff) > 0.0


def test_detect_omissions_and_repetitions():
    ref = "One two three four five six seven eight nine ten."
    # Missing words 4-8 (five words)
    hyp_omitted = "One two three nine ten."
    omissions, reps = detect_omissions_and_repetitions(ref, hyp_omitted, min_omission_words=3)
    assert len(omissions) == 1
    assert "four five six seven eight" in omissions[0]
    assert reps == []

    # Repetition loop
    hyp_repeated = "Alpha beta gamma delta epsilon Alpha beta gamma delta epsilon end."
    _, reps_found = detect_omissions_and_repetitions("Alpha beta gamma delta epsilon end.", hyp_repeated, min_repetition_words=4)
    assert len(reps_found) >= 1
    assert "Alpha beta gamma delta" in reps_found[0]


def test_evaluate_critical_quantities_exact():
    cq1 = CriticalQuantity(value="375", unit="°", ingredient_or_context="oven temperature", raw_token="375°")
    cq2 = CriticalQuantity(value="2", unit="T.", ingredient_or_context="marjoram", raw_token="2 T. marjoram")

    text = "Preheat oven to 375° and mix in 2 T. marjoram thoroughly."
    matches = evaluate_critical_quantities([cq1, cq2], text)
    assert len(matches) == 2
    assert matches[0].status == "exact"
    assert matches[1].status == "exact"


def test_evaluate_critical_quantities_temperature_corruption():
    # Known test case from PLAN_REKONSTRUKCJI.md: 375° corrupted to 3758
    cq = CriticalQuantity(value="375", unit="°", ingredient_or_context="oven temperature", raw_token="375°")
    text_corrupted = "When you set your oven at 3758 does it produce that temperature?"

    matches = evaluate_critical_quantities([cq], text_corrupted)
    assert len(matches) == 1
    assert matches[0].status == "corrupted_value"
    assert "3758" in matches[0].candidate_token


def test_evaluate_critical_quantities_unit_corruption():
    # Unit swap: 2 T. (tablespoon) changed to 2 t. (teaspoon)
    cq = CriticalQuantity(value="2", unit="T.", ingredient_or_context="marjoram", raw_token="2 T. marjoram")
    text_corrupted = "Combine: 2 t. marjoram and spices."

    matches = evaluate_critical_quantities([cq], text_corrupted)
    assert len(matches) == 1
    assert matches[0].status == "corrupted_unit"
    assert "tablespoon" in matches[0].error_details


def test_negative_deliberately_corrupted_input_fails_quality_gates():
    """Obligatory requirement from PLAN_REKONSTRUKCJI.md (R08):

    'test celowo błędnego wyniku wykrywa pominięcia i przekręcone wartości'.
    """
    gt = PageGroundTruth(
        page_no=40,
        printed_page_no=38,
        layout_type="prose_single_column",
        source_image_path="dummy.png",
        source_image_sha256="abc",
        blocks=[
            GroundTruthBlock(
                block_id="p40_p01",
                reading_order=1,
                block_type="paragraph",
                text="When you set your oven at 375° does it actually produce that temperature?",
                critical_quantities=[
                    CriticalQuantity(value="375", unit="°", ingredient_or_context="oven temperature", raw_token="375°")
                ],
            ),
            GroundTruthBlock(
                block_id="p40_p02",
                reading_order=2,
                block_type="paragraph",
                text="Combine 2 T. marjoram and 1 t. caraway with the seasonings.",
                critical_quantities=[
                    CriticalQuantity(value="2", unit="T.", ingredient_or_context="marjoram", raw_token="2 T. marjoram"),
                    CriticalQuantity(value="1", unit="t.", ingredient_or_context="caraway", raw_token="1 t. caraway"),
                ],
            ),
            GroundTruthBlock(
                block_id="p40_p03",
                reading_order=3,
                block_type="paragraph",
                text="This is an important third paragraph that must not be omitted from the page output.",
            ),
        ],
    )

    # Deliberately corrupted candidate:
    # 1) 375° -> 3758 (temperature corrupted)
    # 2) 2 T. -> 2 t. (unit corrupted)
    # 3) Third paragraph completely dropped (omission)
    deliberately_corrupted_candidate = (
        "When you set your oven at 3758 does it actually produce that temperature?\n\n"
        "Combine 2 t. marjoram and 1 t. caraway with the seasonings."
    )

    report = evaluate_page_quality(gt, deliberately_corrupted_candidate)

    # 1. Non-zero CER and WER
    assert report.cer > 0.05
    assert report.wer > 0.05

    # 2. Both corruptions detected
    assert report.corrupted_quantities >= 2
    corrupted_statuses = [q.status for q in report.quantity_matches if q.status in ("corrupted_value", "corrupted_unit")]
    assert "corrupted_value" in corrupted_statuses
    assert "corrupted_unit" in corrupted_statuses

    # 3. Omission detected
    assert len(report.omissions) >= 1
    assert any("important third paragraph" in o for o in report.omissions)

    # 4. Critical violations flagged
    assert len(report.critical_violations) >= 2
    assert any("Corrupted quantities" in v for v in report.critical_violations)
    assert any("Omissions detected" in v for v in report.critical_violations)


def test_evaluate_candidate_run_against_fixtures(tmp_path: Path):
    # Setup candidate directory with page 58
    cand_dir = tmp_path / "cand_run_eval"
    pages_dir = cand_dir / "pages"
    pages_dir.mkdir(parents=True)

    config = CandidateRunConfig(
        run_id="cand_run_eval",
        mode=OcrEngineMode.NATIVE,
        languages=["pl", "en"],
        pages=[58],
        source_pdf_path="input/ksiazka.pdf",
        source_pdf_sha256="abc",
        output_dir=str(cand_dir),
        docling_version="2.146.0",
    )
    manifest = CandidateRunManifest(
        config=config,
        pages_converted=[58],
        total_pages=1,
        total_blocks=14,
        candidate_markdown_path="ksiazka.md",
        candidate_markdown_sha256="123",
        is_production_truth=False,
    )
    manifest_file = cand_dir / "manifest.json"
    manifest_file.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    # Write candidate markdown with matching recipe text
    cand_md = cand_dir / "ksiazka.md"
    cand_md.write_text(
        "POLISH MILD HERB PEPPER\n\n"
        "pieprz ziołowy łagodny\n\n"
        "Following the instructions above, crush or grind and sift together: "
        "2 t. savory, 2 t. marjoram, 1 t. caraway, 1 t. coriander, and 1 t. (not freshly grated) dry horseradish.\n\n"
        "POLISH SHARP HERB PEPPER\n\n"
        "pieprz ziołowy ostry\n\n"
        "Combine as above: 1 t. savory, 1 t. dry (pale yellow) mustard, 1 t. dry black mustard, 1 t. coriander, and 1 t. cayenne pepper.\n\n"
        "POLISH PORK SEASONING\n\n"
        "przyprawa do mięs wieprzowych\n\n"
        "Combine: 2 t. black pepper, 2 T. marjoram, 1 t. paprika, 1 t. caraway, 1 t. sage (optional), 1 t. onion powder, and 1 t. garlic powder.",
        encoding="utf-8",
    )

    report = evaluate_candidate_run(
        cand_manifest_path=manifest_file,
        fixtures_dir=Path("benchmarks/fixtures/ground_truth"),
    )

    assert report.run_id == "cand_run_eval"
    assert report.pages_evaluated == [58]
    assert report.exact_critical_quantities > 10
    assert report.quantity_accuracy_pct > 70.0


def test_compute_percentiles():
    assert compute_percentiles([]) == (0.0, 0.0)
    assert compute_percentiles([10.0]) == (10.0, 10.0)
    vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    p50, p95 = compute_percentiles(vals)
    assert p50 == 5.0
    assert p95 == 9.0


def test_compare_candidate_manifests(tmp_path: Path):
    cand_base = tmp_path / "candidates"
    cand_base.mkdir(parents=True)

    # Variant A (Native)
    dir_a = cand_base / "cand_a"
    dir_a.mkdir()
    (dir_a / "ksiazka.md").write_text("POLISH MILD HERB PEPPER pieprz ziołowy łagodny", encoding="utf-8")
    cfg_a = CandidateRunConfig(
        run_id="cand_a",
        mode=OcrEngineMode.NATIVE,
        languages=["pl", "en"],
        pages=[58],
        source_pdf_path="input/ksiazka.pdf",
        source_pdf_sha256="abc",
        output_dir=str(dir_a),
        docling_version="2.146.0",
    )
    man_a = CandidateRunManifest(
        config=cfg_a,
        pages_converted=[58],
        total_pages=1,
        total_blocks=5,
        candidate_markdown_path="ksiazka.md",
        candidate_markdown_sha256="123",
        duration_seconds=2.5,
        page_durations={58: 2.5},
        is_production_truth=False,
    )
    man_a_path = dir_a / "manifest.json"
    man_a_path.write_text(man_a.model_dump_json(indent=2), encoding="utf-8")

    # Variant B (Full Page EasyOCR)
    dir_b = cand_base / "cand_b"
    dir_b.mkdir()
    (dir_b / "ksiazka.md").write_text(
        "POLISH MILD HERB PEPPER pieprz ziołowy łagodny\nFollowing the instructions above, crush or grind and sift together: 2 t. savory, 2 t. marjoram",
        encoding="utf-8",
    )
    cfg_b = CandidateRunConfig(
        run_id="cand_b",
        mode=OcrEngineMode.FULL_PAGE,
        languages=["pl", "en"],
        pages=[58],
        source_pdf_path="input/ksiazka.pdf",
        source_pdf_sha256="abc",
        output_dir=str(dir_b),
        docling_version="2.146.0",
    )
    man_b = CandidateRunManifest(
        config=cfg_b,
        pages_converted=[58],
        total_pages=1,
        total_blocks=12,
        candidate_markdown_path="ksiazka.md",
        candidate_markdown_sha256="456",
        duration_seconds=8.0,
        page_durations={58: 8.0},
        is_production_truth=False,
    )
    man_b_path = dir_b / "manifest.json"
    man_b_path.write_text(man_b.model_dump_json(indent=2), encoding="utf-8")

    comparison = compare_candidate_manifests(
        manifest_paths=[man_a_path, man_b_path],
        fixtures_dir=Path("benchmarks/fixtures/ground_truth"),
    )

    assert comparison.pages_evaluated == [58]
    assert len(comparison.summary_table) == 2
    assert "native" in comparison.engine_reports
    assert "full_page" in comparison.engine_reports
    assert comparison.fastest_engine == "native"
    assert comparison.best_overall_engine in ("native", "full_page")

    md = format_comparison_markdown(comparison)
    assert "# Raport z Benchmarku OCR" in md
    assert "`native`" in md
    assert "`full_page`" in md
