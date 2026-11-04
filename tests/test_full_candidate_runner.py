"""Unit tests for full candidate runner with checkpointing and coverage analysis (Gate G4 / R17)."""

from datetime import datetime, timezone
import json
from pathlib import Path
import pytest

from src.extraction_adapter import (
    BlockProvenance,
    ExtractedBlock,
    ExtractedPage,
    NormalizedBBox,
    compute_file_sha256,
    save_extracted_page,
)
from src.full_candidate_runner import (
    BatchCheckpoint,
    CheckpointManifest,
    FullCandidateCoverageReport,
    UncertainArea,
    analyze_coverage_and_uncertain_areas,
    assemble_candidate_markdown,
    load_or_create_checkpoint_manifest,
    plan_batches,
    save_checkpoint_manifest,
    save_full_candidate_report,
)
from src.ocr_modes import ProductionOverwriteError


def _create_mock_extracted_page(run_id: str, page_no: int, text: str) -> ExtractedPage:
    prov = BlockProvenance(
        run_id=run_id,
        engine="rapidocr",
        engine_version="1.0",
        source_pdf_path="input/ksiazka.pdf",
        source_pdf_sha256="dummy_sha",
        page_no=page_no,
    )
    block = ExtractedBlock(
        block_id=f"p{page_no}_b1",
        page_no=page_no,
        reading_order=1,
        block_type="paragraph",
        text=text,
        confidence=0.98,
        bbox=NormalizedBBox(l=50.0, t=100.0, r=250.0, b=150.0),
        provenance=prov,
    )
    return ExtractedPage(
        run_id=run_id,
        page_no=page_no,
        width_pt=612.0,
        height_pt=792.0,
        blocks=[block],
    )


def test_plan_batches_914_pages():
    """Verify that 914 pages are divided into exactly 19 batches with batch size 50."""
    pages = list(range(1, 915))
    batches = plan_batches(pages, batch_size=50)

    assert len(batches) == 19
    assert batches[0].batch_index == 0
    assert batches[0].start_page == 1
    assert batches[0].end_page == 50
    assert len(batches[0].pages) == 50

    assert batches[18].batch_index == 18
    assert batches[18].start_page == 901
    assert batches[18].end_page == 914
    assert len(batches[18].pages) == 14

    # Check continuity: no gaps or overlapping pages
    all_batched_pages = []
    for b in batches:
        all_batched_pages.extend(b.pages)
    assert all_batched_pages == pages


def test_checkpoint_manifest_save_and_load(tmp_path: Path):
    """Verify atomic persistence and reload of CheckpointManifest."""
    ckpt_file = tmp_path / "checkpoints" / "manifest.json"
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 mock")

    pages = list(range(1, 101))
    manifest = load_or_create_checkpoint_manifest(
        checkpoint_file=ckpt_file,
        run_id="test_run",
        pdf_path=pdf_path,
        pages=pages,
        batch_size=50,
    )

    assert len(manifest.batches) == 2
    assert manifest.batches[0].status == "pending"

    # Update batch 0 status
    manifest.batches[0].status = "completed"
    manifest.batches[0].duration_seconds = 45.2
    manifest.batches[0].blocks_count = 620
    manifest.completed_page_numbers = list(range(1, 51))
    save_checkpoint_manifest(manifest, ckpt_file)

    # Reload and verify
    reloaded = load_or_create_checkpoint_manifest(
        checkpoint_file=ckpt_file,
        run_id="test_run",
        pdf_path=pdf_path,
        pages=pages,
        batch_size=50,
    )
    assert reloaded.batches[0].status == "completed"
    assert reloaded.batches[0].duration_seconds == 45.2
    assert reloaded.batches[1].status == "pending"
    assert len(reloaded.completed_page_numbers) == 50


def test_assemble_candidate_markdown(tmp_path: Path):
    """Verify that assemble_candidate_markdown reads all page sidecars and joins them with page breaks."""
    cand_dir = tmp_path / "candidates" / "run_test"
    pages_dir = cand_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    for pno in (1, 2, 3):
        page = _create_mock_extracted_page("run_test", pno, f"Content of page {pno}.")
        save_extracted_page(page, pages_dir)

    md_file, sha, total_blocks = assemble_candidate_markdown(
        cand_dir=cand_dir,
        sorted_pages=[1, 2, 3],
        pdf_stem="ksiazka",
    )

    assert md_file.exists()
    assert md_file.name == "ksiazka.md"
    assert total_blocks == 3
    assert len(sha) == 64

    content = md_file.read_text(encoding="utf-8")
    assert "Content of page 1." in content
    assert "Content of page 2." in content
    assert "Content of page 3." in content
    assert "\n\n---\n\n" in content


def test_analyze_coverage_and_uncertain_areas_clean(tmp_path: Path):
    """Verify 100% clean coverage report when all pages exist without anomalies."""
    cand_dir = tmp_path / "candidates" / "run_clean"
    pages_dir = cand_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = tmp_path / "ksiazka.pdf"
    pdf_path.write_bytes(b"%PDF mock")

    # Pages 1 to 5
    for pno in range(1, 6):
        page = _create_mock_extracted_page("run_clean", pno, f"Clean paragraph text on page {pno}.")
        save_extracted_page(page, pages_dir)

    manifest = CheckpointManifest(
        run_id="run_clean",
        source_pdf_path=str(pdf_path),
        source_pdf_sha256="sha",
        total_target_pages=5,
        batch_size=5,
        batches=[
            BatchCheckpoint(
                batch_index=0,
                start_page=1,
                end_page=5,
                pages=list(range(1, 6)),
                status="completed",
                duration_seconds=5.0,
                blocks_count=5,
            )
        ],
        completed_page_numbers=list(range(1, 6)),
        is_fully_completed=True,
    )

    prod_md = tmp_path / "mock_prod.md"
    prod_md.write_text("# Mock Production MD\n", encoding="utf-8")
    from src.extraction_adapter import compute_file_sha256
    prod_sha = compute_file_sha256(prod_md)

    report = analyze_coverage_and_uncertain_areas(
        cand_dir=cand_dir,
        target_pages=list(range(1, 6)),
        batch_manifest=manifest,
        total_elapsed_s=5.0,
        pdf_path=pdf_path,
        production_sha_before=prod_sha,
        production_md_path=prod_md,
    )

    assert report.coverage_percent == 100.0
    assert len(report.missing_pages) == 0
    assert report.total_blocks == 5
    assert report.accepted_blocks == 5
    assert report.flagged_blocks == 0
    assert report.acceptance_rate_percent == 100.0
    assert report.uncertain_areas_count == 0
    assert report.verdict == "PASS"


def test_analyze_coverage_detects_missing_pages_and_anomalies(tmp_path: Path):
    """Verify that missing pages and invariant violations are registered into uncertain_areas."""
    cand_dir = tmp_path / "candidates" / "run_anomalies"
    pages_dir = cand_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = tmp_path / "ksiazka.pdf"
    pdf_path.write_bytes(b"%PDF mock")

    # Page 1: normal
    save_extracted_page(_create_mock_extracted_page("run_anomalies", 1, "Normal text on page 1."), pages_dir)
    # Page 2: omitted (missing page)
    # Page 3: contains scanner debris
    save_extracted_page(_create_mock_extracted_page("run_anomalies", 3, "Add 1 cup sugar ■ ^p- and mix."), pages_dir)

    manifest = CheckpointManifest(
        run_id="run_anomalies",
        source_pdf_path=str(pdf_path),
        source_pdf_sha256="sha",
        total_target_pages=3,
        batch_size=3,
        batches=[
            BatchCheckpoint(
                batch_index=0,
                start_page=1,
                end_page=3,
                pages=[1, 2, 3],
                status="completed",
                duration_seconds=3.0,
            )
        ],
    )

    prod_md = tmp_path / "mock_prod2.md"
    prod_md.write_text("# Mock Production MD\n", encoding="utf-8")
    prod_sha = compute_file_sha256(prod_md)

    report = analyze_coverage_and_uncertain_areas(
        cand_dir=cand_dir,
        target_pages=[1, 2, 3],
        batch_manifest=manifest,
        total_elapsed_s=3.0,
        pdf_path=pdf_path,
        production_sha_before=prod_sha,
        production_md_path=prod_md,
    )

    assert report.missing_pages == [2]
    assert report.coverage_percent == pytest.approx(66.67, rel=0.01)
    assert report.verdict == "FAIL"  # Failed because page 2 is missing

    # Verify uncertain areas registry
    categories = [u.category for u in report.uncertain_areas]
    assert "missing_page" in categories
    assert "invariant_violation" in categories


def test_save_full_candidate_report_and_guard(tmp_path: Path):
    """Verify that save_full_candidate_report persists JSON and MD, and forbids saving to production paths."""
    out_dir = tmp_path / "benchmarks" / "full_candidate"
    report = FullCandidateCoverageReport(
        run_id="test_run_914",
        source_pdf_path="input/ksiazka.pdf",
        target_pages_range=(1, 914),
        total_pages_expected=914,
        total_pages_converted=914,
        missing_pages=[],
        coverage_percent=100.0,
        total_batches=19,
        completed_batches=19,
        total_blocks=12000,
        accepted_blocks=11980,
        flagged_blocks=20,
        acceptance_rate_percent=99.83,
        column_interleaving_cases=0,
        broken_transitions_count=0,
        uncertain_areas_count=20,
        uncertain_areas=[
            UncertainArea(
                page_no=208,
                block_id="p208_b4",
                category="invariant_violation",
                severity="warning",
                description="Wykryto zapętlenie frazy: 's'",
                snippet="ray S s s S Ss gi extract",
            )
        ],
        total_duration_seconds=950.0,
        total_duration_minutes=15.83,
        mean_page_duration_s=1.04,
        p50_page_duration_s=1.03,
        p95_page_duration_s=1.05,
        peak_ram_mb=3800.0,
        peak_gpu_vram_mb=850.0,
        candidate_markdown_path="output/candidates/test_run_914/ksiazka.md",
        candidate_markdown_sha256="abcd" * 16,
        production_sha_before="ef01" * 16,
        production_sha_after="ef01" * 16,
        production_preserved=True,
        verdict="PASS",
    )

    json_path, md_path = save_full_candidate_report(report, out_dir)
    assert json_path.exists()
    assert md_path.exists()

    md_content = md_path.read_text(encoding="utf-8")
    assert "Raport Pokrycia i Jakości Kandydata" in md_content
    assert "100.0%" in md_content
    assert "12000" in md_content
    assert "Rejestr Obszarów Niepewnych" in md_content
    assert "p208_b4" in md_content

    # Verify production path write protection
    with pytest.raises(ProductionOverwriteError):
        save_full_candidate_report(report, Path("output/markdown"))
