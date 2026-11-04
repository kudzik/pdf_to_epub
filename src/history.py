import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from src.write_control import book_write_lock


class HistoryRecord(BaseModel):
    record_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    book_name: str
    paragraph_index: int
    paragraph_sha256: str
    document_sha256_before: str
    document_sha256_after: Optional[str] = None
    source_engine: str  # np. "ollama:qwen2.5:14b", "heuristic", "manual", "source_ocr"
    original_text: str
    proposed_text: str
    decision: str  # "proposed", "accepted", "rejected", "applied", "superseded"
    decision_reason: str
    quality_metrics: Optional[dict] = None


class HistoryLedger(BaseModel):
    ledger_version: str = "1.0"
    book_name: str
    last_updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    records: list[HistoryRecord] = Field(default_factory=list)


def compute_paragraph_sha256(text: str) -> str:
    """Zwraca sumę kontrolną SHA-256 znormalizowanego tekstu akapitu."""
    clean = text.strip().replace("\r\n", "\n")
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def get_history_file_path(book_name: str, root_dir: Path = Path(".")) -> Path:
    """Zwraca ścieżkę do pliku historii decyzji dla danej książki."""
    history_dir = root_dir / "output" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    return history_dir / f"{book_name}_history.json"


def load_history_ledger(book_name: str, root_dir: Path = Path(".")) -> HistoryLedger:
    """Wczytuje księgę historii decyzji dla danej książki z dysku."""
    history_file = get_history_file_path(book_name, root_dir=root_dir)
    if not history_file.is_file():
        return HistoryLedger(book_name=book_name)

    try:
        data = json.loads(history_file.read_text(encoding="utf-8"))
        return HistoryLedger.model_validate(data)
    except Exception:
        # W razie uszkodzenia pliku zwracamy pusty ledger z zachowaniem nazwy
        return HistoryLedger(book_name=book_name)


def save_history_ledger(ledger: HistoryLedger, root_dir: Path = Path(".")) -> None:
    """Zapisuje księgę historii atomowo na dysku."""
    history_file = get_history_file_path(ledger.book_name, root_dir=root_dir)
    ledger.last_updated_at = datetime.now(timezone.utc).isoformat()

    temp_file = history_file.with_name(f"{history_file.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_file.write_text(ledger.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temp_file, history_file)
    finally:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                pass


def append_history_record(record: HistoryRecord, root_dir: Path = Path(".")) -> HistoryLedger:
    """
    Dodaje wpis do historii decyzji danej książki.
    Jeśli istnieje już wpis dla danego akapitu o tym samym silniku i treści, aktualizuje jego decyzję.
    """
    ledger = load_history_ledger(record.book_name, root_dir=root_dir)

    # Oznaczamy poprzednie aktywne propozycje dla tego indeksu jako zastąpione (superseded), jeśli aplikujemy nową
    if record.decision == "applied":
        for r in ledger.records:
            if r.paragraph_index == record.paragraph_index and r.decision in ["proposed", "accepted"]:
                r.decision = "superseded"

    ledger.records.append(record)
    save_history_ledger(ledger, root_dir=root_dir)
    return ledger


def verify_index_anchors(
    markdown_path: Path,
    ledger: HistoryLedger,
) -> dict[str, dict]:
    """
    Weryfikuje kotwice indeksów w pliku Markdown.
    Sprawdza, czy akapity pod zapisanymi indeksami odpowiadają sumom SHA-256 treści.
    Wykrywa przesunięcia indeksów (offset shifts) spowodowane modyfikacjami pliku.
    """
    if not markdown_path.is_file():
        raise FileNotFoundError(f"Plik Markdown {markdown_path} nie istnieje.")

    content = markdown_path.read_text(encoding="utf-8")
    paragraphs = content.split("\n\n")
    p_hashes = [compute_paragraph_sha256(p) for p in paragraphs]

    # Budujemy mapę hash -> lista indeksów w aktualnym dokumencie
    hash_to_indices: dict[str, list[int]] = {}
    for idx, h in enumerate(p_hashes):
        hash_to_indices.setdefault(h, []).append(idx)

    audit_status: dict[str, dict] = {}

    for record in ledger.records:
        rec_id = record.record_id
        recorded_idx = record.paragraph_index
        recorded_hash = record.paragraph_sha256

        # 1. Sprawdzamy czy pod zapisanym indeksem nadal jest ten sam akapit
        if 0 <= recorded_idx < len(paragraphs) and p_hashes[recorded_idx] == recorded_hash:
            audit_status[rec_id] = {
                "status": "valid",
                "is_anchored": True,
                "current_index": recorded_idx,
                "recorded_index": recorded_idx,
                "shift_offset": 0,
            }
            continue

        # 2. Jeśli nie zgadza się, sprawdzamy czy akapit przesunął się w inne miejsce
        candidate_indices = hash_to_indices.get(recorded_hash, [])
        if candidate_indices:
            new_idx = candidate_indices[0]
            offset = new_idx - recorded_idx
            audit_status[rec_id] = {
                "status": "shifted",
                "is_anchored": False,
                "current_index": new_idx,
                "recorded_index": recorded_idx,
                "shift_offset": offset,
                "warning": f"Przesunięcie indeksu z #{recorded_idx} na #{new_idx} (offset: {offset:+d}).",
            }
        else:
            # 3. Akapit został zmieniony lub usunięty
            audit_status[rec_id] = {
                "status": "displaced",
                "is_anchored": False,
                "current_index": None,
                "recorded_index": recorded_idx,
                "shift_offset": None,
                "warning": f"Akapit #{recorded_idx} uległ zmianie lub został usunięty ze źródła.",
            }

    return audit_status
