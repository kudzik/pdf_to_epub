"""Revision metadata shared by CLI and the web interface."""
import hashlib
import json
import os
import uuid
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_dependencies(md: Path, metadata: Path | None, css: Path | None, cover: Path | None) -> dict:
    files = [md]
    files.extend(p for p in (metadata, css, cover) if p is not None)
    # All assets are included conservatively, also covering referenced CSS assets.
    assets = md.parent / 'assets'
    if assets.is_dir():
        files.extend(sorted(p for p in assets.rglob('*') if p.is_file()))
    return {str(p.resolve()): sha256(p) if p.is_file() else None for p in files}
