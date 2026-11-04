import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class FileInventoryEntry(BaseModel):
    rel_path: str
    sha256: str
    size_bytes: int
    modified_at: str
    category: str
    copied_to_snapshot: bool = False


class EnvironmentInfo(BaseModel):
    python_version: str
    platform_system: str
    platform_release: str
    packages: dict[str, str] = Field(default_factory=dict)
    cuda_available: bool = False
    cuda_device_name: Optional[str] = None
    ollama_available: bool = False
    ollama_endpoint: Optional[str] = None
    ollama_models: list[str] = Field(default_factory=list)


class BookStateSummary(BaseModel):
    book_name: str
    pdf_exists: bool = False
    pdf_sha256: Optional[str] = None
    pdf_size_bytes: Optional[int] = None
    markdown_exists: bool = False
    markdown_sha256: Optional[str] = None
    markdown_lines: int = 0
    markdown_paragraphs: int = 0
    backup_exists: bool = False
    backup_sha256: Optional[str] = None
    assets_count: int = 0
    audit_report_exists: bool = False
    total_anomalies: Optional[int] = None
    high_severity_anomalies: Optional[int] = None


class SnapshotOptions(BaseModel):
    book_name: str = "ksiazka"
    description: str = "Baseline snapshot"
    output_dir: Path = Path("output/snapshots")
    include_pdf_copy: bool = False
    include_assets_copy: bool = False


class SnapshotManifest(BaseModel):
    manifest_version: str = "1.0"
    snapshot_id: str
    created_at: str
    description: str
    book_name: str
    environment: EnvironmentInfo
    book_state: BookStateSummary
    files: list[FileInventoryEntry] = Field(default_factory=list)


def compute_sha256(file_path: Path) -> str:
    """Oblicza sumę kontrolną SHA-256 dla podanego pliku z obsługą dużych plików."""
    if not file_path.is_file():
        raise FileNotFoundError(f"Plik {file_path} nie istnieje lub nie jest plikiem regularnym.")

    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def get_windows_host_ip() -> str:
    """Zwraca adres IP hosta Windows w środowisku WSL."""
    try:
        route = subprocess.check_output(
            "ip route show default | awk '{print $3}'",
            shell=True,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if route:
            return route
    except Exception:
        pass
    return "127.0.0.1"


def probe_ollama_status() -> tuple[bool, Optional[str], list[str]]:
    """Sprawdza dostępność instancji Ollama (lokalnie lub na hoście Windows)."""
    candidates = ["http://127.0.0.1:11434"]
    win_ip = get_windows_host_ip()
    if win_ip and win_ip != "127.0.0.1":
        candidates.append(f"http://{win_ip}:11434")

    for url in candidates:
        try:
            req = urllib.request.Request(f"{url}/api/tags", headers={"User-Agent": "pdf-to-epub"})
            with urllib.request.urlopen(req, timeout=0.6) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
                    return True, url, models
        except Exception:
            continue

    return False, None, []


def collect_environment_info() -> EnvironmentInfo:
    """Zbiera informacje o środowisku uruchomieniowym, bibliotekach, CUDA i Ollama."""
    key_packages = [
        "docling",
        "docling-core",
        "pypdfium2",
        "easyocr",
        "torch",
        "pydantic",
        "typer",
        "rich",
        "pytest",
    ]
    pkg_versions: dict[str, str] = {}
    for pkg in key_packages:
        try:
            pkg_versions[pkg] = importlib.metadata.version(pkg)
        except Exception:
            pkg_versions[pkg] = "not_installed"

    cuda_available = False
    cuda_name: Optional[str] = None
    try:
        import torch
        cuda_available = bool(torch.cuda.is_available())
        if cuda_available and torch.cuda.device_count() > 0:
            cuda_name = torch.cuda.get_device_name(0)
    except Exception:
        pass

    ollama_ok, ollama_url, ollama_models = probe_ollama_status()

    return EnvironmentInfo(
        python_version=sys.version.split()[0],
        platform_system=platform.system(),
        platform_release=platform.release(),
        packages=pkg_versions,
        cuda_available=cuda_available,
        cuda_device_name=cuda_name,
        ollama_available=ollama_ok,
        ollama_endpoint=ollama_url,
        ollama_models=ollama_models,
    )


def summarize_book_state(
    book_name: str,
    root_dir: Path = Path("."),
) -> tuple[BookStateSummary, list[FileInventoryEntry]]:
    """Przeprowadza inwentaryzację plików powiązanych ze wskazaną książką."""
    summary = BookStateSummary(book_name=book_name)
    files: list[FileInventoryEntry] = []

    def register_file(path: Path, category: str) -> Optional[FileInventoryEntry]:
        if not path.is_file():
            return None
        sha = compute_sha256(path)
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        rel_str = str(path.relative_to(root_dir)) if path.is_relative_to(root_dir) else str(path)
        entry = FileInventoryEntry(
            rel_path=rel_str,
            sha256=sha,
            size_bytes=stat.st_size,
            modified_at=mtime,
            category=category,
        )
        files.append(entry)
        return entry

    # 1. Źródłowy PDF
    pdf_path = root_dir / "input" / f"{book_name}.pdf"
    if pdf_entry := register_file(pdf_path, "source_pdf"):
        summary.pdf_exists = True
        summary.pdf_sha256 = pdf_entry.sha256
        summary.pdf_size_bytes = pdf_entry.size_bytes

    # 2. Markdown i Backup
    md_path = root_dir / "output" / "markdown" / f"{book_name}.md"
    if md_entry := register_file(md_path, "markdown"):
        summary.markdown_exists = True
        summary.markdown_sha256 = md_entry.sha256
        content = md_path.read_text(encoding="utf-8", errors="replace")
        summary.markdown_lines = len(content.splitlines())
        summary.markdown_paragraphs = len([p for p in content.split("\n\n") if p.strip()])

    bak_path = root_dir / "output" / "markdown" / f"{book_name}.md.bak"
    if bak_entry := register_file(bak_path, "backup"):
        summary.backup_exists = True
        summary.backup_sha256 = bak_entry.sha256

    # 3. Metadane i CSS
    register_file(root_dir / "metadata.yaml", "metadata")
    register_file(root_dir / "templates" / "epub.css", "css")

    # 4. Raport audytu
    audit_path = root_dir / "output" / f"audit_{book_name}.json"
    if audit_entry := register_file(audit_path, "audit"):
        summary.audit_report_exists = True
        try:
            audit_data = json.loads(audit_path.read_text(encoding="utf-8"))
            summary.total_anomalies = audit_data.get("total_anomalies")
            summary.high_severity_anomalies = audit_data.get("high_severity_count")
        except Exception:
            pass

    # 5. Assety
    assets_dir = root_dir / "output" / "markdown" / "assets"
    if assets_dir.is_dir():
        asset_files = [f for f in sorted(assets_dir.iterdir()) if f.is_file() and f.name != ".gitkeep"]
        summary.assets_count = len(asset_files)
        for a_file in asset_files:
            register_file(a_file, "asset")

    return summary, files


def create_snapshot(
    options: SnapshotOptions,
    root_dir: Path = Path("."),
) -> SnapshotManifest:
    """Tworzy punkt odniesienia (snapshot) stanu książki, środowiska i metadanych."""
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snapshot_id = f"{timestamp_str}_{options.book_name}"
    target_snapshot_dir = options.output_dir / snapshot_id
    files_backup_dir = target_snapshot_dir / "files"
    files_backup_dir.mkdir(parents=True, exist_ok=True)

    summary, files_inventory = summarize_book_state(options.book_name, root_dir=root_dir)
    env_info = collect_environment_info()

    # Kopiujemy kluczowe pliki tekstowe/metadanych do katalogu snapshotu
    files_to_copy = [
        (root_dir / "output" / "markdown" / f"{options.book_name}.md", "markdown"),
        (root_dir / "output" / "markdown" / f"{options.book_name}.md.bak", "backup"),
        (root_dir / "output" / f"audit_{options.book_name}.json", "audit"),
        (root_dir / "metadata.yaml", "metadata"),
        (root_dir / "templates" / "epub.css", "css"),
    ]

    copied_paths: set[str] = set()
    for src_file, _ in files_to_copy:
        if src_file.is_file():
            dest = files_backup_dir / src_file.name
            shutil.copy2(src_file, dest)
            rel_str = str(src_file.relative_to(root_dir)) if src_file.is_relative_to(root_dir) else str(src_file)
            copied_paths.add(rel_str)

    # Opcjonalne kopiowanie PDF i assetów
    if options.include_pdf_copy:
        pdf_file = root_dir / "input" / f"{options.book_name}.pdf"
        if pdf_file.is_file():
            shutil.copy2(pdf_file, files_backup_dir / pdf_file.name)
            copied_paths.add(str(pdf_file.relative_to(root_dir)))

    if options.include_assets_copy:
        assets_src = root_dir / "output" / "markdown" / "assets"
        if assets_src.is_dir():
            assets_dest = files_backup_dir / "assets"
            shutil.copytree(assets_src, assets_dest, dirs_exist_ok=True)
            for a_file in assets_src.glob("*"):
                if a_file.is_file():
                    copied_paths.add(str(a_file.relative_to(root_dir)))

    for f_entry in files_inventory:
        if f_entry.rel_path in copied_paths:
            f_entry.copied_to_snapshot = True

    manifest = SnapshotManifest(
        snapshot_id=snapshot_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        description=options.description,
        book_name=options.book_name,
        environment=env_info,
        book_state=summary,
        files=files_inventory,
    )

    manifest_path = target_snapshot_dir / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    return manifest


def verify_snapshot_integrity(snapshot_dir: Path) -> dict:
    """Weryfikuje spójność danych i sum kontrolnych w danym katalogu snapshotu."""
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.is_file():
        return {
            "is_valid": False,
            "error": f"Brak pliku manifestu w {snapshot_dir}",
            "files_checked": 0,
            "mismatches": [],
        }

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = SnapshotManifest.model_validate(data)
    except Exception as exc:
        return {
            "is_valid": False,
            "error": f"Błąd walidacji manifestu: {exc}",
            "files_checked": 0,
            "mismatches": [],
        }

    files_dir = snapshot_dir / "files"
    files_checked = 0
    mismatches: list[dict] = []

    for entry in manifest.files:
        if not entry.copied_to_snapshot:
            continue

        file_name = Path(entry.rel_path).name
        target_path = files_dir / file_name
        if not target_path.is_file():
            mismatches.append({
                "rel_path": entry.rel_path,
                "expected_sha256": entry.sha256,
                "error": "Plik nie został znaleziony w katalogu snapshotu",
            })
            continue

        current_sha = compute_sha256(target_path)
        files_checked += 1
        if current_sha != entry.sha256:
            mismatches.append({
                "rel_path": entry.rel_path,
                "expected_sha256": entry.sha256,
                "actual_sha256": current_sha,
                "error": "Niezgodność sumy kontrolnej SHA-256",
            })

    return {
        "is_valid": len(mismatches) == 0,
        "snapshot_id": manifest.snapshot_id,
        "files_checked": files_checked,
        "mismatches": mismatches,
    }
