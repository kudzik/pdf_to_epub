from pathlib import Path

import pytest
from typer.testing import CliRunner

from src.benchmark_manifest import (
    BenchmarkPagesManifest,
    PageLayoutType,
    TestPageEntry,
    build_canonical_manifest,
    load_test_pages_manifest,
    save_test_pages_manifest,
    validate_manifest_consistency,
)
from src.pipeline import app

runner = CliRunner()


def test_canonical_manifest_validity():
    manifest = build_canonical_manifest()
    validation = validate_manifest_consistency(manifest)

    assert validation["is_valid"] is True
    assert validation["dev_count"] == 20
    assert validation["holdout_count"] == 10
    assert validation["total_test_pages"] == 30
    assert len(validation["errors"]) == 0

    dev_nums = {p.pdf_page for p in manifest.dev_pages}
    holdout_nums = {p.pdf_page for p in manifest.holdout_pages}

    # Zbiory muszą być całkowicie rozłączne
    assert len(dev_nums.intersection(holdout_nums)) == 0

    # Wymagane znane trudne strony muszą znajdować się w dev
    required_cases = {25, 26, 33, 34, 40, 54, 58}
    assert required_cases.issubset(dev_nums)


def test_validate_manifest_detects_overlap_and_missing_required():
    entry = TestPageEntry(
        pdf_page=25,
        chapter="Test",
        layout_type=PageLayoutType.PROSE_SINGLE_COLUMN,
        description="Overlap page",
    )
    # Manifest z nakładającą się stroną 25 w obu zbiorach
    manifest = BenchmarkPagesManifest(
        dev_pages=[entry] * 20,
        holdout_pages=[entry] * 10,
    )
    validation = validate_manifest_consistency(manifest)
    assert validation["is_valid"] is False
    assert any("nakładające" in err.lower() or "duplikaty" in err.lower() for err in validation["errors"])


def test_validate_manifest_detects_out_of_bounds_page():
    manifest = build_canonical_manifest()
    # Wstawiamy nieprawidłowy numer strony 9999
    manifest.dev_pages[0].pdf_page = 9999
    validation = validate_manifest_consistency(manifest)
    assert validation["is_valid"] is False
    assert any("wykracza poza zakres" in err for err in validation["errors"])


def test_save_and_load_roundtrip(tmp_path: Path):
    manifest = build_canonical_manifest()
    target_file = tmp_path / "custom_manifest.json"

    saved_path = save_test_pages_manifest(manifest, target_path=target_file)
    assert saved_path.is_file()

    loaded = load_test_pages_manifest(saved_path)
    assert loaded.total_pages_in_pdf == 914
    assert len(loaded.dev_pages) == 20
    assert len(loaded.holdout_pages) == 10
    assert loaded.dev_pages[0].pdf_page == 25


def test_committed_fixture_manifest_exists_and_valid():
    fixture_path = Path("benchmarks/fixtures/test_pages_manifest.json")
    assert fixture_path.is_file(), "Plik benchmarks/fixtures/test_pages_manifest.json musi istnieć w repozytorium"

    manifest = load_test_pages_manifest(fixture_path)
    validation = validate_manifest_consistency(manifest)
    assert validation["is_valid"] is True


def test_cli_test_manifest_command():
    result = runner.invoke(app, ["test-manifest", "--help"])
    assert result.exit_code == 0
    assert "Wyświetla lub weryfikuje manifest stron testowych" in result.output
