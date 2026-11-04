"""Unit tests for migration, reconciliation, and atomic publication (Gate G4 / R18)."""

from pathlib import Path
import pytest

from src.extraction_adapter import compute_file_sha256
from src.migration import (
    AssetManifestReport,
    DocumentDiffSummary,
    MigrationResult,
    build_asset_manifest,
    compare_documents,
    publish_candidate_document,
    reconcile_candidate_document,
    rollback_production_document,
    save_migration_report,
)


def test_build_asset_manifest(tmp_path: Path):
    """Verify scanning disk assets and detecting valid vs missing markdown image references."""
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    img1 = assets_dir / "img1.png"
    img1.write_bytes(b"\x89PNG\r\n\x1a\nmock_image1")

    img2 = assets_dir / "img2.png"
    img2.write_bytes(b"\x89PNG\r\n\x1a\nmock_image2")

    # Production references img1 and missing img3
    prod_md = tmp_path / "prod.md"
    prod_md.write_text(
        "Here is first image: ![First](assets/img1.png)\n"
        "Here is missing image: ![Missing](assets/img3.png)\n",
        encoding="utf-8",
    )

    # Candidate references img1 and img2
    cand_md = tmp_path / "cand.md"
    cand_md.write_text(
        "Candidate has ![First](assets/img1.png) and ![Second](assets/img2.png)\n",
        encoding="utf-8",
    )

    manifest = build_asset_manifest(assets_dir, prod_md, cand_md)

    assert manifest.total_disk_assets == 2
    assert manifest.all_references_valid is False
    assert "assets/img3.png" in manifest.missing_files

    item_img1 = next((item for item in manifest.assets if item.rel_path == "assets/img1.png"), None)
    assert item_img1 is not None
    assert item_img1.file_exists is True
    assert item_img1.referenced_in_production is True
    assert item_img1.referenced_in_candidate is True


def test_reconcile_candidate_document(tmp_path: Path):
    """Verify that illustrations in production are properly mapped and injected into candidate."""
    prod_md = tmp_path / "prod.md"
    prod_md.write_text(
        "# Introduction\n\n"
        "The history of Polish cuisine is rich and diverse.![Image](assets/intro.png)\n\n"
        "## Soup Recipes\n\n"
        "Rosol is traditional Sunday broth.![Image](assets/soup.png)\n",
        encoding="utf-8",
    )

    cand_md = tmp_path / "cand.md"
    cand_md.write_text(
        "# Introduction\n\n"
        "The history of Polish cuisine is rich and diverse.\n\n"
        "## Soup Recipes\n\n"
        "Rosol is traditional Sunday broth.\n",
        encoding="utf-8",
    )

    reconciled = reconcile_candidate_document(cand_md, prod_md)

    assert "![Image](assets/intro.png)" in reconciled
    assert "![Image](assets/soup.png)" in reconciled


def test_compare_documents(tmp_path: Path):
    """Verify diff calculation between production and candidate Markdown."""
    prod_md = tmp_path / "prod.md"
    prod_md.write_text(
        "# Title\n\n"
        "Mix 1 2 tsp salt and ½ cup water. ■ ^p- Debris.\n",
        encoding="utf-8",
    )

    cand_md = tmp_path / "cand.md"
    cand_md.write_text(
        "# Title\n\n"
        "Mix 1/2 tsp salt and 1/2 cup water. Clean prose.\n",
        encoding="utf-8",
    )

    diff = compare_documents(prod_md, cand_md)

    assert diff.production_lines > 0
    assert diff.candidate_lines > 0
    assert diff.word_similarity_pct > 50.0
    assert diff.improvements["unicode_fractions_normalized"] == 1
    assert diff.improvements["broken_fractions_repaired"] == 1
    assert diff.improvements["scanner_debris_eliminated"] == 1


def test_publish_candidate_document_dry_run(tmp_path: Path):
    """Verify dry_run simulation does not alter the production Markdown file."""
    prod_md = tmp_path / "output" / "markdown" / "ksiazka.md"
    prod_md.parent.mkdir(parents=True, exist_ok=True)
    prod_md.write_text("# Original Production Content\n", encoding="utf-8")
    original_sha = compute_file_sha256(prod_md)

    cand_md = tmp_path / "output" / "candidates" / "ksiazka.md"
    cand_md.parent.mkdir(parents=True, exist_ok=True)
    cand_md.write_text("# New Candidate Content\n", encoding="utf-8")

    assets_dir = tmp_path / "output" / "markdown" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    result = publish_candidate_document(
        candidate_md_path=cand_md,
        production_md_path=prod_md,
        assets_dir=assets_dir,
        dry_run=True,
    )

    assert result.status == "dry_run"
    assert compute_file_sha256(prod_md) == original_sha
    assert prod_md.read_text(encoding="utf-8") == "# Original Production Content\n"


def test_publish_and_rollback_cycle(tmp_path: Path):
    """Verify atomic publish updates production and rollback restores exact original file."""
    prod_md = tmp_path / "output" / "markdown" / "ksiazka.md"
    prod_md.parent.mkdir(parents=True, exist_ok=True)
    original_text = "# Original Book Content Before Publish\n\nSome paragraph text.\n"
    prod_md.write_text(original_text, encoding="utf-8")
    original_sha = compute_file_sha256(prod_md)

    cand_md = tmp_path / "output" / "candidates" / "ksiazka.md"
    cand_md.parent.mkdir(parents=True, exist_ok=True)
    new_text = "# New Published Candidate Content\n\nUpgraded paragraph text.\n"
    cand_md.write_text(new_text, encoding="utf-8")

    assets_dir = tmp_path / "output" / "markdown" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # 1. Publish candidate
    pub_res = publish_candidate_document(
        candidate_md_path=cand_md,
        production_md_path=prod_md,
        assets_dir=assets_dir,
        dry_run=False,
    )

    assert pub_res.status == "published"
    assert pub_res.verdict == "PASS"
    assert compute_file_sha256(prod_md) != original_sha
    assert "New Published Candidate Content" in prod_md.read_text(encoding="utf-8")

    # Verify backup exists
    backup_file = prod_md.with_name("ksiazka.md.pre_r18_bak")
    assert backup_file.exists()
    assert compute_file_sha256(backup_file) == original_sha

    # 2. Rollback to original
    roll_res = rollback_production_document(
        production_md_path=prod_md,
        backup_path=backup_file,
    )

    assert roll_res.status == "rolled_back"
    assert roll_res.verdict == "PASS"
    assert compute_file_sha256(prod_md) == original_sha
    assert prod_md.read_text(encoding="utf-8") == original_text


def test_save_migration_report(tmp_path: Path):
    """Verify persisting migration report to JSON and Markdown."""
    diff = DocumentDiffSummary(
        production_path="output/markdown/ksiazka.md",
        production_sha256="abc",
        production_lines=100,
        production_words=1000,
        production_paragraphs=50,
        candidate_path="output/candidates/ksiazka.md",
        candidate_sha256="def",
        candidate_lines=110,
        candidate_words=1050,
        candidate_paragraphs=52,
        word_similarity_pct=95.5,
        total_assets_preserved=113,
        culinary_measurements_prod=200,
        culinary_measurements_cand=200,
    )
    manifest = AssetManifestReport(
        assets_dir="output/markdown/assets",
        total_disk_assets=113,
        total_production_references=113,
        total_candidate_references=113,
        all_references_valid=True,
    )
    result = MigrationResult(
        run_id="test_mig_run",
        status="published",
        snapshot_id="snap_123",
        backup_path="output/markdown/ksiazka.md.pre_r18_bak",
        production_path="output/markdown/ksiazka.md",
        production_sha_before="abc",
        production_sha_after="def",
        diff_summary=diff,
        asset_manifest=manifest,
        verdict="PASS",
    )

    out_dir = tmp_path / "benchmarks" / "migration"
    json_path, md_path = save_migration_report(result, out_dir)

    assert json_path.exists()
    assert md_path.exists()

    md_content = md_path.read_text(encoding="utf-8")
    assert "Raport Migracji i Publikacji Dokumentu" in md_content
    assert "95.5%" in md_content
    assert "113" in md_content
    assert "snap_123" in md_content
