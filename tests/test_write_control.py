import hashlib
import json
import os
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from src.auditor import audit_markdown_file, check_report_freshness
from src.pipeline import app
from src.repair import patch_paragraph
from src.write_control import (
    AssetIntegrityError,
    InvalidPatchError,
    LockTimeoutError,
    PatchRequest,
    VersionConflictError,
    book_write_lock,
    compute_file_sha256,
    write_patches_atomic,
)

runner = CliRunner()


def test_lock_mutual_exclusion_and_timeout(tmp_path: Path):
    md_file = tmp_path / "test.md"
    md_file.write_text("Paragraph 1\n\nParagraph 2\n", encoding="utf-8")

    with book_write_lock(md_file, timeout_seconds=1.0, owner="proc1"):
        # Próba równoczesnego pobrania blokady przez drugi proces
        with pytest.raises(LockTimeoutError):
            with book_write_lock(md_file, timeout_seconds=0.1, owner="proc2"):
                pass

    # Po zwolnieniu blokady drugi proces może ją swobodnie pobrać
    with book_write_lock(md_file, timeout_seconds=0.5, owner="proc2"):
        pass


def test_stale_lock_cleanup(tmp_path: Path):
    md_file = tmp_path / "test.md"
    md_file.write_text("Content\n", encoding="utf-8")
    lock_file = tmp_path / "test.md.lock"

    # Tworzymy sztuczny, przestarzały plik blokady
    lock_file.write_text(json.dumps({"pid": 999999, "timestamp": "2020-01-01T00:00:00Z"}), encoding="utf-8")
    past_time = time.time() - 100
    os.utime(lock_file, (past_time, past_time))

    # book_write_lock powinien wykryć stary plik i przejąć blokadę
    with book_write_lock(md_file, timeout_seconds=0.5, owner="new_proc"):
        assert lock_file.exists()

    assert not lock_file.exists()


def test_version_conflict_rejection(tmp_path: Path):
    md_file = tmp_path / "test.md"
    initial_text = "Original p0\n\nOriginal p1\n"
    md_file.write_text(initial_text, encoding="utf-8")

    correct_sha = compute_file_sha256(md_file)
    bogus_sha = "0000000000000000000000000000000000000000000000000000000000000000"

    # 1. Próba zapisu ze złym expected_sha256
    with pytest.raises(VersionConflictError):
        write_patches_atomic(
            markdown_path=md_file,
            patches=[PatchRequest(index=0, text="New text")],
            expected_sha256=bogus_sha,
        )
    assert md_file.read_text(encoding="utf-8") == initial_text

    # 2. Próba zapisu ze złym original tekst akapitu
    with pytest.raises(VersionConflictError):
        write_patches_atomic(
            markdown_path=md_file,
            patches=[PatchRequest(index=0, text="New text", original="Mismatch text")],
            expected_sha256=correct_sha,
        )
    assert md_file.read_text(encoding="utf-8") == initial_text

    # 3. Poprawny zapis ze zgodnym sha i original
    res = write_patches_atomic(
        markdown_path=md_file,
        patches=[PatchRequest(index=0, text="New text", original="Original p0")],
        expected_sha256=correct_sha,
    )
    assert res.is_success is True
    assert res.changed_count == 1
    assert "New text\n\nOriginal p1" in md_file.read_text(encoding="utf-8")


def test_asset_preservation_enforcement(tmp_path: Path):
    md_file = tmp_path / "test.md"
    image_tag = "![Plate](assets/dish_1.png)"
    p0 = f"Delicious dish with photo {image_tag}."
    p1 = "Second paragraph."
    md_file.write_text(f"{p0}\n\n{p1}\n", encoding="utf-8")

    # Próba usunięcia grafiki
    with pytest.raises(AssetIntegrityError):
        write_patches_atomic(
            markdown_path=md_file,
            patches=[PatchRequest(index=0, text="Delicious dish without photo.")],
        )

    # Poprawka zachowująca grafikę
    res = write_patches_atomic(
        markdown_path=md_file,
        patches=[PatchRequest(index=0, text=f"Delicious reconstructed dish with {image_tag}.")],
    )
    assert res.is_success is True
    assert image_tag in md_file.read_text(encoding="utf-8")


def test_invalid_patch_bounds_and_empty(tmp_path: Path):
    md_file = tmp_path / "test.md"
    md_file.write_text("Only one paragraph.", encoding="utf-8")

    # Zły indeks
    with pytest.raises(InvalidPatchError):
        write_patches_atomic(
            markdown_path=md_file,
            patches=[PatchRequest(index=5, text="Out of bounds")],
        )

    # Pusty tekst
    with pytest.raises(InvalidPatchError):
        write_patches_atomic(
            markdown_path=md_file,
            patches=[PatchRequest(index=0, text="   \n\n  ")],
        )

    # Duplikat indeksu w paczce
    with pytest.raises(InvalidPatchError):
        write_patches_atomic(
            markdown_path=md_file,
            patches=[
                PatchRequest(index=0, text="First"),
                PatchRequest(index=0, text="Second"),
            ],
        )


def test_audit_report_sha256_and_staleness(tmp_path: Path):
    md_file = tmp_path / "book.md"
    md_file.write_text("# Chapter One\n\nSome introductory content.\n", encoding="utf-8")
    report_file = tmp_path / "audit_book.json"

    report = audit_markdown_file(md_file, report_file)
    assert report.markdown_sha256 != ""

    freshness = check_report_freshness(report_file, md_file)
    assert freshness["is_stale"] is False
    assert freshness["report_sha256"] == compute_file_sha256(md_file)

    # Modyfikacja Markdowna
    patch_paragraph(md_file, paragraph_index=1, replacement_text="Modified content.")
    stale_check = check_report_freshness(report_file, md_file)
    assert stale_check["is_stale"] is True


def test_cli_patch_version_flags(tmp_path: Path):
    output_dir = tmp_path / "output"
    markdown_dir = output_dir / "markdown"
    markdown_dir.mkdir(parents=True)
    md_file = markdown_dir / "ksiazka.md"
    md_file.write_text("Old content.\n\nSecond paragraph.\n", encoding="utf-8")

    correct_sha = compute_file_sha256(md_file)

    # Zła suma SHA w CLI
    bad_result = runner.invoke(app, [
        "patch", "0", "--text", "Updated",
        "--expected-sha", "wrong_hash",
        "--output-dir", str(output_dir),
    ])
    assert bad_result.exit_code != 0
    assert "Konflikt wersji" in bad_result.output

    # Dobra suma SHA w CLI
    good_result = runner.invoke(app, [
        "patch", "0", "--text", "Updated content.",
        "--expected-sha", correct_sha,
        "--output-dir", str(output_dir),
    ])
    assert good_result.exit_code == 0
    assert "Pomyślnie podmieniono" in good_result.output
