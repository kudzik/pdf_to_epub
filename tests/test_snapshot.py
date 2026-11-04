import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from src.pipeline import app
from src.snapshot import (
    EnvironmentInfo,
    SnapshotManifest,
    SnapshotOptions,
    collect_environment_info,
    compute_sha256,
    create_snapshot,
    summarize_book_state,
    verify_snapshot_integrity,
)

runner = CliRunner()


def test_compute_sha256_valid_and_missing(tmp_path: Path):
    sample_file = tmp_path / "sample.txt"
    content = b"Hello, PDF to EPUB world!"
    sample_file.write_bytes(content)

    expected_sha = hashlib.sha256(content).hexdigest()
    assert compute_sha256(sample_file) == expected_sha

    missing_file = tmp_path / "missing.txt"
    with pytest.raises(FileNotFoundError):
        compute_sha256(missing_file)


def test_collect_environment_info():
    env = collect_environment_info()
    assert isinstance(env, EnvironmentInfo)
    assert env.python_version.startswith("3.")
    assert "docling" in env.packages
    assert "pydantic" in env.packages
    assert isinstance(env.cuda_available, bool)
    assert isinstance(env.ollama_available, bool)


def test_summarize_book_state(tmp_path: Path):
    # Tworzymy atrapę struktury książki
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf_file = input_dir / "test_book.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 mock content")

    output_dir = tmp_path / "output"
    markdown_dir = output_dir / "markdown"
    assets_dir = markdown_dir / "assets"
    assets_dir.mkdir(parents=True)

    md_file = markdown_dir / "test_book.md"
    md_file.write_text("# Chapter 1\n\nParagraph 1\n\nParagraph 2\n", encoding="utf-8")

    bak_file = markdown_dir / "test_book.md.bak"
    bak_file.write_text("# Chapter 1\n\nOriginal\n", encoding="utf-8")

    asset_file = assets_dir / "img1.png"
    asset_file.write_bytes(b"mock image png")

    meta_file = tmp_path / "metadata.yaml"
    meta_file.write_text("title: Test Book\n", encoding="utf-8")

    css_dir = tmp_path / "templates"
    css_dir.mkdir()
    css_file = css_dir / "epub.css"
    css_file.write_text("body { font-size: 1em; }\n", encoding="utf-8")

    audit_file = output_dir / "audit_test_book.json"
    audit_file.write_text(
        json.dumps({"total_anomalies": 10, "high_severity_count": 2}),
        encoding="utf-8",
    )

    summary, files = summarize_book_state("test_book", root_dir=tmp_path)

    assert summary.pdf_exists is True
    assert summary.pdf_size_bytes == len(b"%PDF-1.4 mock content")
    assert summary.markdown_exists is True
    assert summary.markdown_paragraphs == 3
    assert summary.backup_exists is True
    assert summary.assets_count == 1
    assert summary.audit_report_exists is True
    assert summary.total_anomalies == 10
    assert summary.high_severity_anomalies == 2

    categories = {f.category for f in files}
    assert "source_pdf" in categories
    assert "markdown" in categories
    assert "backup" in categories
    assert "metadata" in categories
    assert "css" in categories
    assert "audit" in categories
    assert "asset" in categories


def test_create_and_verify_snapshot(tmp_path: Path):
    # Przygotowanie struktury źródłowej
    output_dir = tmp_path / "output"
    markdown_dir = output_dir / "markdown"
    markdown_dir.mkdir(parents=True)

    md_file = markdown_dir / "sample.md"
    md_file.write_text("Test content for snapshot baseline.", encoding="utf-8")

    meta_file = tmp_path / "metadata.yaml"
    meta_file.write_text("title: Sample\n", encoding="utf-8")

    css_dir = tmp_path / "templates"
    css_dir.mkdir()
    css_file = css_dir / "epub.css"
    css_file.write_text("body {}", encoding="utf-8")

    snapshots_dir = output_dir / "snapshots"

    opts = SnapshotOptions(
        book_name="sample",
        description="Unit test snapshot",
        output_dir=snapshots_dir,
    )

    manifest = create_snapshot(opts, root_dir=tmp_path)
    assert isinstance(manifest, SnapshotManifest)
    snapshot_dir = snapshots_dir / manifest.snapshot_id
    assert snapshot_dir.is_dir()
    assert (snapshot_dir / "manifest.json").is_file()

    # Sprawdzenie weryfikacji sum
    res = verify_snapshot_integrity(snapshot_dir)
    assert res["is_valid"] is True
    assert res["files_checked"] >= 3
    assert len(res["mismatches"]) == 0

    # Test modyfikacji pliku (tampering test)
    copied_md = snapshot_dir / "files" / "sample.md"
    copied_md.write_text("Corrupted content!", encoding="utf-8")

    tampered_res = verify_snapshot_integrity(snapshot_dir)
    assert tampered_res["is_valid"] is False
    assert len(tampered_res["mismatches"]) == 1
    assert tampered_res["mismatches"][0]["error"] == "Niezgodność sumy kontrolnej SHA-256"


def test_cli_snapshot_command(tmp_path: Path):
    result = runner.invoke(app, ["snapshot", "--help"])
    assert result.exit_code == 0
    assert "Tworzy lub weryfikuje punkt odniesienia" in result.output
