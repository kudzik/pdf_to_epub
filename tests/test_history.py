from pathlib import Path

from typer.testing import CliRunner

from src.history import (
    HistoryLedger,
    HistoryRecord,
    append_history_record,
    compute_paragraph_sha256,
    load_history_ledger,
    verify_index_anchors,
)
from src.pipeline import app

runner = CliRunner()


def test_compute_paragraph_sha256_normalization():
    p1 = "Delicious soup with potatoes.\r\n"
    p2 = "Delicious soup with potatoes.\n  "
    assert compute_paragraph_sha256(p1) == compute_paragraph_sha256(p2)


def test_history_ledger_append_and_supersede(tmp_path: Path):
    rec1 = HistoryRecord(
        book_name="test_book",
        paragraph_index=1,
        paragraph_sha256="abc123hash",
        document_sha256_before="doc_sha_1",
        source_engine="ollama:qwen2.5:14b",
        original_text="Uszkodzony tekst",
        proposed_text="Poprawny tekst 1",
        decision="proposed",
        decision_reason="Model suggestion",
    )
    append_history_record(rec1, root_dir=tmp_path)

    ledger = load_history_ledger("test_book", root_dir=tmp_path)
    assert len(ledger.records) == 1
    assert ledger.records[0].decision == "proposed"

    # Zastosowanie poprawki powinno oznaczyć poprzednią propozycję jako superseded
    rec2 = HistoryRecord(
        book_name="test_book",
        paragraph_index=1,
        paragraph_sha256="abc123hash",
        document_sha256_before="doc_sha_1",
        document_sha256_after="doc_sha_2",
        source_engine="manual",
        original_text="Uszkodzony tekst",
        proposed_text="Ostateczny tekst",
        decision="applied",
        decision_reason="Ręczna akceptacja",
    )
    append_history_record(rec2, root_dir=tmp_path)

    ledger_after = load_history_ledger("test_book", root_dir=tmp_path)
    assert len(ledger_after.records) == 2
    assert ledger_after.records[0].decision == "superseded"
    assert ledger_after.records[1].decision == "applied"


def test_verify_index_anchors_and_shifts(tmp_path: Path):
    # Tworzymy plik początkowy z 3 akapitami
    p0 = "Introduction paragraph."
    p1 = "Target culinary recipe paragraph."
    p2 = "Conclusion paragraph."

    md_file = tmp_path / "book.md"
    md_file.write_text(f"{p0}\n\n{p1}\n\n{p2}\n", encoding="utf-8")

    rec = HistoryRecord(
        book_name="book",
        paragraph_index=1,
        paragraph_sha256=compute_paragraph_sha256(p1),
        document_sha256_before="doc_sha",
        source_engine="heuristic",
        original_text=p1,
        proposed_text=p1,
        decision="proposed",
        decision_reason="Test anchor",
    )
    ledger = HistoryLedger(book_name="book", records=[rec])

    # 1. Stan początkowy: brak przesunięcia
    status_map = verify_index_anchors(md_file, ledger)
    assert status_map[rec.record_id]["status"] == "valid"
    assert status_map[rec.record_id]["is_anchored"] is True
    assert status_map[rec.record_id]["current_index"] == 1
    assert status_map[rec.record_id]["shift_offset"] == 0

    # 2. Przesunięcie indeksu (dodanie nowego akapitu na początku)
    inserted = "Newly inserted paragraph at the beginning."
    md_file.write_text(f"{inserted}\n\n{p0}\n\n{p1}\n\n{p2}\n", encoding="utf-8")

    shifted_map = verify_index_anchors(md_file, ledger)
    assert shifted_map[rec.record_id]["status"] == "shifted"
    assert shifted_map[rec.record_id]["is_anchored"] is False
    assert shifted_map[rec.record_id]["current_index"] == 2  # Przesunięty z #1 na #2
    assert shifted_map[rec.record_id]["shift_offset"] == 1

    # 3. Zmiana lub usunięcie akapitu ze źródła
    md_file.write_text(f"{inserted}\n\n{p0}\n\nCompletely replaced text.\n\n{p2}\n", encoding="utf-8")
    displaced_map = verify_index_anchors(md_file, ledger)
    assert displaced_map[rec.record_id]["status"] == "displaced"
    assert displaced_map[rec.record_id]["is_anchored"] is False
    assert displaced_map[rec.record_id]["current_index"] is None


def test_cli_history_command(tmp_path: Path):
    result = runner.invoke(app, ["history", "--help"])
    assert result.exit_code == 0
    assert "Przegląda historię decyzji" in result.output
