import json
import shutil
from pathlib import Path
from typing import Optional

from src.history import (
    HistoryRecord,
    append_history_record,
    compute_paragraph_sha256,
)
from src.write_control import (
    AssetIntegrityError,
    InvalidPatchError,
    LockTimeoutError,
    PatchRequest,
    VersionConflictError,
    compute_file_sha256,
    write_patches_atomic,
)


def backup_markdown(markdown_path: Path) -> Path:
    """Tworzy kopię zapasową pliku Markdown przed wprowadzeniem zmian."""
    backup_path = markdown_path.with_name(f"{markdown_path.name}.bak")
    if not backup_path.exists():
        shutil.copy2(markdown_path, backup_path)
    return backup_path


def get_anomaly_details(
    report_path: Path,
    paragraph_index: int,
    markdown_path: Path,
) -> dict:
    """
    Zwraca szczegółowe informacje o anomalii wraz z kontekstem
    (poprzedzający i następujący akapit) oraz informacją o aktualności raportu.
    """
    if not report_path.exists():
        raise FileNotFoundError(f"Raport {report_path} nie istnieje. Uruchom najpierw: uv run python -m src.pipeline audit")

    if not markdown_path.exists():
        raise FileNotFoundError(f"Plik Markdown {markdown_path} nie istnieje.")

    report_data = json.loads(report_path.read_text(encoding="utf-8"))
    anomalies = {a["paragraph_index"]: a for a in report_data.get("anomalies", [])}

    if paragraph_index not in anomalies:
        raise ValueError(f"Nie znaleziono anomalii dla akapitu o indeksie {paragraph_index}.")

    content = markdown_path.read_text(encoding="utf-8")
    paragraphs = content.split("\n\n")

    if paragraph_index >= len(paragraphs):
        raise IndexError(f"Indeks akapitu {paragraph_index} wykracza poza plik (łączna liczba akapitów: {len(paragraphs)}).")

    raw_text = paragraphs[paragraph_index].strip()
    before = paragraphs[paragraph_index - 1].strip() if paragraph_index > 0 else ""
    after = paragraphs[paragraph_index + 1].strip() if paragraph_index + 1 < len(paragraphs) else ""

    # Sprawdzenie aktualności względem SHA-256 Markdowna
    current_sha = compute_file_sha256(markdown_path)
    report_sha = report_data.get("markdown_sha256")
    is_stale = bool(report_sha and report_sha.lower() != current_sha.lower())

    anomaly = anomalies[paragraph_index]
    return {
        "paragraph_index": paragraph_index,
        "severity": anomaly.get("severity", "medium"),
        "score": anomaly.get("score", 0.0),
        "categories": anomaly.get("categories", []),
        "sample_tokens": anomaly.get("sample_tokens", []),
        "raw_paragraph": raw_text,
        "context_before": before,
        "context_after": after,
        "markdown_sha256": current_sha,
        "report_sha256": report_sha,
        "is_stale": is_stale,
    }


def patch_paragraph(
    markdown_path: Path,
    paragraph_index: int,
    replacement_text: str,
    expected_original: Optional[str] = None,
    expected_sha256: Optional[str] = None,
    create_backup: bool = True,
    owner: str = "cli:patch",
    timeout_seconds: float = 5.0,
) -> bool:
    """
    Bezpiecznie i atomowo podmienia treść konkretnego akapitu na podstawie jego indeksu.
    Stosuje blokadę plikową, kontrolę wersji SHA-256 oraz ochronę linków do grafik.
    """
    res = write_patches_atomic(
        markdown_path=markdown_path,
        patches=[
            PatchRequest(
                index=paragraph_index,
                text=replacement_text,
                original=expected_original,
            )
        ],
        expected_sha256=expected_sha256,
        create_backup=create_backup,
        owner=owner,
        timeout_seconds=timeout_seconds,
    )
    if res.is_success and res.changed_count > 0:
        stem = markdown_path.stem
        append_history_record(
            HistoryRecord(
                book_name=stem,
                paragraph_index=paragraph_index,
                paragraph_sha256=compute_paragraph_sha256(expected_original or replacement_text),
                document_sha256_before=res.sha256_before,
                document_sha256_after=res.sha256_after,
                source_engine=owner,
                original_text=expected_original or "",
                proposed_text=replacement_text,
                decision="applied",
                decision_reason="Akapit zmodyfikowany przez patch_paragraph",
            )
        )
    return res.is_success


def patch_batch_from_file(
    markdown_path: Path,
    fixes_file: Path,
    expected_sha256: Optional[str] = None,
    create_backup: bool = True,
    owner: str = "cli:patch-batch",
    timeout_seconds: float = 5.0,
) -> int:
    """
    Wczytuje plik JSON z poprawkami { "numer_akapitu": "nowy_tekst" } lub listą obiektów
    i aplikuje wszystkie poprawki w jednym atomowym przejściu pod blokadą zapisu.
    """
    if not fixes_file.exists():
        raise FileNotFoundError(f"Plik poprawek {fixes_file} nie istnieje.")

    fixes_data = json.loads(fixes_file.read_text(encoding="utf-8"))
    patches: list[PatchRequest] = []

    if isinstance(fixes_data, dict):
        for idx_str, val in fixes_data.items():
            if isinstance(val, dict):
                patches.append(
                    PatchRequest(
                        index=int(idx_str),
                        text=val.get("text", ""),
                        original=val.get("original"),
                    )
                )
            elif isinstance(val, str):
                patches.append(PatchRequest(index=int(idx_str), text=val))
            else:
                raise ValueError(f"Nieobsługiwany format wartości dla klucza {idx_str}.")
    elif isinstance(fixes_data, list):
        for item in fixes_data:
            if isinstance(item, dict) and "index" in item and "text" in item:
                patches.append(
                    PatchRequest(
                        index=int(item["index"]),
                        text=str(item["text"]),
                        original=item.get("original"),
                    )
                )
            else:
                raise ValueError("Element listy poprawek musi zawierać klucze 'index' i 'text'.")
    else:
        raise ValueError("Plik poprawek musi być obiektem JSON lub listą poprawek.")

    res = write_patches_atomic(
        markdown_path=markdown_path,
        patches=patches,
        expected_sha256=expected_sha256,
        create_backup=create_backup,
        owner=owner,
        timeout_seconds=timeout_seconds,
    )
    if res.is_success and res.changed_count > 0:
        stem = markdown_path.stem
        for p in patches:
            if p.index in res.modified_indices:
                append_history_record(
                    HistoryRecord(
                        book_name=stem,
                        paragraph_index=p.index,
                        paragraph_sha256=compute_paragraph_sha256(p.original or p.text),
                        document_sha256_before=res.sha256_before,
                        document_sha256_after=res.sha256_after,
                        source_engine=owner,
                        original_text=p.original or "",
                        proposed_text=p.text,
                        decision="applied",
                        decision_reason="Akapit zmodyfikowany przez patch_batch_from_file",
                    )
                )
    return res.changed_count
