import json
import shutil
from pathlib import Path
from typing import Optional


def backup_markdown(markdown_path: Path) -> Path:
    """Tworzy kopię zapasową pliku Markdown przed wprowadzeniem zmian."""
    backup_path = markdown_path.with_suffix(markdown_path.suffix + ".bak")
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
    (poprzedzający i następujący akapit), aby ułatwić Agentowi AI rekonstrukcję.
    """
    if not report_path.exists():
        raise FileNotFoundError(f"Raport {report_path} nie istnieje. Uruchom najpierw: uv run python -m src.pipeline audit")

    report_data = json.loads(report_path.read_text(encoding="utf-8"))
    anomalies = {a["paragraph_index"]: a for a in report_data.get("anomalies", [])}

    if paragraph_index not in anomalies:
        raise ValueError(f"Nie znaleziono anomalii dla akapitu o indeksie {paragraph_index}.")

    anomaly = anomalies[paragraph_index]
    content = markdown_path.read_text(encoding="utf-8")
    paragraphs = content.split("\n\n")

    if paragraph_index >= len(paragraphs):
        raise IndexError(f"Indeks akapitu {paragraph_index} wykracza poza plik (łączna liczba akapitów: {len(paragraphs)}).")

    raw_text = paragraphs[paragraph_index].strip()
    before = paragraphs[paragraph_index - 1].strip() if paragraph_index > 0 else ""
    after = paragraphs[paragraph_index + 1].strip() if paragraph_index + 1 < len(paragraphs) else ""

    return {
        "paragraph_index": paragraph_index,
        "severity": anomaly.get("severity", "medium"),
        "score": anomaly.get("score", 0.0),
        "categories": anomaly.get("categories", []),
        "sample_tokens": anomaly.get("sample_tokens", []),
        "raw_paragraph": raw_text,
        "context_before": before,
        "context_after": after,
    }


def patch_paragraph(
    markdown_path: Path,
    paragraph_index: int,
    replacement_text: str,
    create_backup: bool = True,
) -> bool:
    """
    Bezpiecznie podmienia treść konkretnego akapitu na podstawie jego indeksu.
    Automatycznie wykonuje kopię zapasową .bak.
    """
    if not markdown_path.exists():
        raise FileNotFoundError(f"Plik {markdown_path} nie istnieje.")

    if create_backup:
        backup_markdown(markdown_path)

    content = markdown_path.read_text(encoding="utf-8")
    paragraphs = content.split("\n\n")

    if paragraph_index < 0 or paragraph_index >= len(paragraphs):
        raise IndexError(f"Indeks {paragraph_index} poza zakresem (0..{len(paragraphs)-1}).")

    paragraphs[paragraph_index] = replacement_text.strip()
    new_content = "\n\n".join(paragraphs)
    markdown_path.write_text(new_content, encoding="utf-8")
    return True


def patch_batch_from_file(
    markdown_path: Path,
    fixes_file: Path,
    create_backup: bool = True,
) -> int:
    """
    Wczytuje plik JSON z poprawkami { "numer_akapitu": "nowy_tekst" }
    i aplikuje wszystkie poprawki w jednym bezpiecznym przejściu.
    """
    if not fixes_file.exists():
        raise FileNotFoundError(f"Plik poprawek {fixes_file} nie istnieje.")

    fixes_data = json.loads(fixes_file.read_text(encoding="utf-8"))
    if not isinstance(fixes_data, dict):
        raise ValueError("Plik poprawek musi być obiektem JSON w formacie: { '51': 'Nowy tekst...' }.")

    if create_backup:
        backup_markdown(markdown_path)

    content = markdown_path.read_text(encoding="utf-8")
    paragraphs = content.split("\n\n")

    applied_count = 0
    for idx_str, new_text in fixes_data.items():
        idx = int(idx_str)
        if 0 <= idx < len(paragraphs):
            paragraphs[idx] = new_text.strip()
            applied_count += 1

    new_content = "\n\n".join(paragraphs)
    markdown_path.write_text(new_content, encoding="utf-8")
    return applied_count

