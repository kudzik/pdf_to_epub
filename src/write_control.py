import fcntl
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from pydantic import BaseModel, Field


class LockTimeoutError(Exception):
    """Zgłaszany, gdy nie uda się uzyskać blokady pliku przed upływem limitu czasu."""
    pass


class VersionConflictError(Exception):
    """Zgłaszany, gdy suma SHA-256 lub oryginalna treść akapitu nie zgadza się ze stanem na dysku."""
    pass


class AssetIntegrityError(Exception):
    """Zgłaszany, gdy próba modyfikacji akapitu usuwa lub zmienia odnośniki do grafik (assets)."""
    pass


class InvalidPatchError(IndexError, ValueError):
    """Zgłaszany, gdy podany indeks lub treść poprawki są nieprawidłowe."""
    pass


class PatchRequest(BaseModel):
    index: int
    text: str
    original: Optional[str] = None


class WriteResult(BaseModel):
    is_success: bool
    sha256_before: str
    sha256_after: str
    changed_count: int
    total_paragraphs: int
    modified_indices: list[int] = Field(default_factory=list)


def compute_file_sha256(file_path: Path) -> str:
    """Zwraca sumę SHA-256 dla wskazanego pliku."""
    if not file_path.is_file():
        raise FileNotFoundError(f"Plik {file_path} nie istnieje.")
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def extract_asset_references(text: str) -> list[str]:
    """Wyszukuje wszystkie relatywne odnośniki do grafik w formacie Markdown: ![alt](assets/...)"""
    matches = re.findall(r"!\[.*?\]\((assets/[^\)]+)\)", text)
    return sorted(matches)


def check_assets_preserved(original_text: str, replacement_text: str) -> bool:
    """Weryfikuje, czy w zrekonstruowanym tekście zachowano wszystkie grafiki z oryginału."""
    orig_assets = extract_asset_references(original_text)
    if not orig_assets:
        return True
    repl_assets = extract_asset_references(replacement_text)
    return orig_assets == repl_assets


def is_lock_stale(lock_path: Path, max_age_seconds: float = 30.0) -> bool:
    """Sprawdza, czy istniejący plik blokady jest przestarzały (np. proces uległ awarii)."""
    if not lock_path.is_file():
        return False
    try:
        mtime = lock_path.stat().st_mtime
        if (time.time() - mtime) > max_age_seconds:
            return True
    except OSError:
        pass
    return False


@contextmanager
def book_write_lock(
    markdown_path: Path,
    timeout_seconds: float = 5.0,
    owner: str = "cli",
) -> Generator[Path, None, None]:
    """
    Menedżer kontekstu zapewniający blokadę pojedynczego zapisującego (single-writer lock)
    za pomocą pliku .lock oraz fcntl.flock z detekcją przestarzałych blokad.
    """
    lock_path = markdown_path.with_name(f"{markdown_path.name}.lock")
    start_time = time.monotonic()
    fd: Optional[int] = None

    while True:
        try:
            # Tworzymy / otwieramy plik blokady
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            # Próbujemy uzyskać wyłączną blokadę bez blokowania (non-blocking)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            # Zapisujemy metadane właściciela blokady
            payload = json.dumps({
                "pid": os.getpid(),
                "owner": owner,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }).encode("utf-8")
            os.ftruncate(fd, 0)
            os.write(fd, payload)
            break
        except (BlockingIOError, OSError):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None

            # Sprawdzamy, czy blokada nie jest martwa
            if is_lock_stale(lock_path):
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError:
                    pass

            if (time.monotonic() - start_time) >= timeout_seconds:
                raise LockTimeoutError(
                    f"Nie udało się uzyskać blokady pliku {markdown_path} w ciągu {timeout_seconds}s. "
                    "Inny proces (API lub CLI) jest w trakcie zapisu."
                )
            time.sleep(0.05)

    try:
        yield lock_path
    finally:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            except OSError:
                pass
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


def write_patches_atomic(
    markdown_path: Path,
    patches: list[PatchRequest],
    expected_sha256: Optional[str] = None,
    create_backup: bool = True,
    owner: str = "cli",
    timeout_seconds: float = 5.0,
) -> WriteResult:
    """
    Atomowo aplikuje paczkę poprawek do pliku Markdown z kontrolą wersji i blokadą.
    Kroki:
    1. Pobiera blokadę wyłączną book_write_lock.
    2. Sprawdza expected_sha256 (jeśli podano).
    3. Weryfikuje indeksy, zawartość oryginału i integralność grafik.
    4. Zapisuje do pliku tymczasowego z fsync i podmienia atomowo przez os.replace.
    """
    if not markdown_path.is_file():
        raise FileNotFoundError(f"Plik Markdown {markdown_path} nie istnieje.")

    if not patches:
        raise InvalidPatchError("Brak poprawek do zaaplikowania.")

    with book_write_lock(markdown_path, timeout_seconds=timeout_seconds, owner=owner):
        current_content = markdown_path.read_text(encoding="utf-8")
        sha_before = hashlib.sha256(current_content.encode("utf-8")).hexdigest()

        # 1. Kontrola wersji pliku
        if expected_sha256 and expected_sha256.lower() != sha_before.lower():
            raise VersionConflictError(
                f"Konflikt wersji dokumentu: oczekiwano SHA-256 {expected_sha256[:12]}..., "
                f"lecz aktualna suma pliku to {sha_before[:12]}... Odśwież dane przed zapisem."
            )

        paragraphs = current_content.split("\n\n")
        total_p = len(paragraphs)
        indices_seen: set[int] = set()
        modified_indices: list[int] = []

        # 2. Weryfikacja wszystkich poprawek przed jakąkolwiek modyfikacją
        for p in patches:
            if not isinstance(p.index, int) or p.index < 0 or p.index >= total_p:
                raise InvalidPatchError(f"Nieprawidłowy indeks akapitu: {p.index} (zakres: 0..{total_p-1}).")

            if p.index in indices_seen:
                raise InvalidPatchError(f"Zduplikowany indeks akapitu w jednej paczce: {p.index}.")
            indices_seen.add(p.index)

            # Weryfikacja czy treść nie jest pusta
            clean_text = p.text.strip().replace("\r\n", "\n")
            if not clean_text:
                raise InvalidPatchError(f"Próba zapisu pustego tekstu dla akapitu #{p.index}.")

            current_paragraph = paragraphs[p.index].strip()

            # Weryfikacja konfliktu na poziomie akapitu
            if p.original is not None:
                expected_orig = p.original.strip()
                if current_paragraph != expected_orig:
                    raise VersionConflictError(
                        f"Konflikt w akapicie #{p.index}: treść uległa zmianie od czasu przygotowania poprawki. "
                        f"Oczekiwano: '{expected_orig[:40]}...', zastano: '{current_paragraph[:40]}...'."
                    )

            # Weryfikacja integralności grafik
            if not check_assets_preserved(current_paragraph, clean_text):
                raise AssetIntegrityError(
                    f"Zablokowano zapis w akapicie #{p.index}: modyfikacja usunęłaby lub zmieniła link do grafiki."
                )

        # 3. Zastosowanie poprawek w pamięci
        for p in patches:
            replacement = re.sub(r"\n\s*\n", "\n", p.text.strip().replace("\r\n", "\n"))
            if paragraphs[p.index].strip() != replacement:
                paragraphs[p.index] = replacement
                modified_indices.append(p.index)

        # Jeśli brak zmian
        if not modified_indices:
            return WriteResult(
                is_success=True,
                sha256_before=sha_before,
                sha256_after=sha_before,
                changed_count=0,
                total_paragraphs=total_p,
                modified_indices=[],
            )

        # 4. Kopia zapasowa .bak (jeśli jeszcze nie istnieje)
        if create_backup:
            bak_path = markdown_path.with_name(f"{markdown_path.name}.bak")
            if not bak_path.exists():
                shutil.copy2(markdown_path, bak_path)

        # 5. Atomowy zapis do pliku tymczasowego
        new_text = "\n\n".join(paragraphs)
        temp_file = markdown_path.with_name(f"{markdown_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                f.write(new_text)
                f.flush()
                os.fsync(f.fileno())

            # Atomowa zamiana
            os.replace(temp_file, markdown_path)
        finally:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass

        sha_after = hashlib.sha256(new_text.encode("utf-8")).hexdigest()

        # Zapisujemy stan ostatniej modyfikacji
        state_path = markdown_path.with_name(f"{markdown_path.name}.state.json")
        try:
            state_data = {
                "last_modified_at": datetime.now(timezone.utc).isoformat(),
                "sha256": sha_after,
                "owner": owner,
                "modified_paragraphs_count": len(modified_indices),
            }
            state_path.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
        except OSError:
            pass

        return WriteResult(
            is_success=True,
            sha256_before=sha_before,
            sha256_after=sha_after,
            changed_count=len(modified_indices),
            total_paragraphs=total_p,
            modified_indices=modified_indices,
        )
