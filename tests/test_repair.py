import json
from pathlib import Path
import pytest
from src.repair import (
    backup_markdown,
    get_anomaly_details,
    patch_paragraph,
    patch_batch_from_file,
)


def test_backup_markdown(tmp_path: Path):
    """Weryfikuje automatyczne tworzenie kopii zapasowej .bak."""
    md_file = tmp_path / "test.md"
    md_file.write_text("Oryginalna treść", encoding="utf-8")

    bak_file = backup_markdown(md_file)
    assert bak_file.exists()
    assert bak_file.name == "test.md.bak"
    assert bak_file.read_text(encoding="utf-8") == "Oryginalna treść"


def test_get_anomaly_details(tmp_path: Path):
    """Weryfikuje pobieranie szczegółów anomalii z kontekstem przed i po."""
    md_file = tmp_path / "book.md"
    md_file.write_text("Akapit 0: Wstęp.\n\nAkapit 1: Uszkodzony b°ynthe.\n\nAkapit 2: Zakończenie.", encoding="utf-8")

    report_file = tmp_path / "report.json"
    report_data = {
        "anomalies": [
            {
                "paragraph_index": 1,
                "severity": "high",
                "score": 10.0,
                "categories": ["corrupted_ocr_symbols"],
                "sample_tokens": ["b°ynthe"],
            }
        ]
    }
    report_file.write_text(json.dumps(report_data), encoding="utf-8")

    details = get_anomaly_details(report_file, 1, md_file)
    assert details["paragraph_index"] == 1
    assert details["raw_paragraph"] == "Akapit 1: Uszkodzony b°ynthe."
    assert details["context_before"] == "Akapit 0: Wstęp."
    assert details["context_after"] == "Akapit 2: Zakończenie."
    assert details["sample_tokens"] == ["b°ynthe"]


def test_patch_paragraph(tmp_path: Path):
    """Weryfikuje bezpieczną podmianę wskazanego akapitu."""
    md_file = tmp_path / "book.md"
    md_file.write_text("Akapit 0.\n\nAkapit 1 do naprawy.\n\nAkapit 2.", encoding="utf-8")

    success = patch_paragraph(md_file, 1, "Akapit 1 naprawiony!")
    assert success is True

    new_content = md_file.read_text(encoding="utf-8")
    assert new_content == "Akapit 0.\n\nAkapit 1 naprawiony!\n\nAkapit 2."
    assert (tmp_path / "book.md.bak").exists()


def test_patch_batch_from_file(tmp_path: Path):
    """Weryfikuje aplikowanie paczki poprawek z pliku JSON."""
    md_file = tmp_path / "book.md"
    md_file.write_text("Akapit 0.\n\nAkapit 1.\n\nAkapit 2.\n\nAkapit 3.", encoding="utf-8")

    fixes_file = tmp_path / "fixes.json"
    fixes_file.write_text(json.dumps({"1": "Akapit 1 poprawiony.", "3": "Akapit 3 poprawiony."}), encoding="utf-8")

    applied = patch_batch_from_file(md_file, fixes_file)
    assert applied == 2

    new_content = md_file.read_text(encoding="utf-8")
    assert "Akapit 1 poprawiony." in new_content
    assert "Akapit 3 poprawiony." in new_content
    assert "Akapit 0." in new_content
    assert "Akapit 2." in new_content


def test_patch_invalid_index_raises(tmp_path: Path):
    """Weryfikuje błąd przy podaniu indeksu spoza zakresu."""
    md_file = tmp_path / "book.md"
    md_file.write_text("Tylko jeden akapit.", encoding="utf-8")

    with pytest.raises(IndexError):
        patch_paragraph(md_file, 99, "Tekst")

