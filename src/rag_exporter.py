"""RAG knowledge base generation, semantic chunking, and GPU embedding export (Gate G5 / R19)."""

import datetime
import json
from pathlib import Path
import re
from typing import Any, Optional
import urllib.error
import urllib.request

from pydantic import BaseModel, Field

from src.block_validator import extract_culinary_measurements


class RagChunk(BaseModel):
    chunk_id: str
    chunk_type: str = "prose"  # "recipe", "table", "section_header", "prose"
    title: str = ""
    parent_sections: list[str] = Field(default_factory=list)
    content: str
    word_count: int = 0
    culinary_measurements: list[str] = Field(default_factory=list)
    assets_referenced: list[str] = Field(default_factory=list)
    line_start: int = 1
    line_end: int = 1
    estimated_page: int = 1
    embedding: Optional[list[float]] = None


class RagExportSummary(BaseModel):
    run_id: str
    source_file: str
    total_chunks: int = 0
    total_words: int = 0
    recipe_chunks_count: int = 0
    prose_chunks_count: int = 0
    total_culinary_measurements: int = 0
    total_assets_referenced: int = 0
    embedded_chunks_count: int = 0
    embedding_model: Optional[str] = None
    jsonl_path: str = ""
    json_path: str = ""
    created_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )


def extract_chunk_assets(text: str) -> list[str]:
    """Extract list of referenced asset paths from Markdown text."""
    return re.findall(r"!\[.*?\]\((assets/[^\)]+)\)", text)


def is_recipe_content(text: str) -> bool:
    """Detect whether chunk contains a recipe (ingredients, measurements, instructions)."""
    text_lower = text.lower()
    recipe_keywords = [
        "składniki", "ingredients", "sposób wykonania", "przygotowanie",
        "method", "directions", "instructions", "tbsp", "tsp", "cup",
        "łyżka", "łyżeczka", "szklanka", "gotować", "piec", "smażyć",
        "bake", "cook", "boil", "fry"
    ]
    keyword_hits = sum(1 for kw in recipe_keywords if kw in text_lower)
    measurements = extract_culinary_measurements(text)

    # If it has at least 3 culinary measurements and recipe terminology
    if len(measurements) >= 3 and keyword_hits >= 1:
        return True
    if keyword_hits >= 3:
        return True
    return False


def chunk_markdown_document(
    md_path: Path,
    max_words_per_chunk: int = 400,
    total_estimated_pages: int = 914,
) -> list[RagChunk]:
    """Split Markdown document into semantic chunks honoring headings and recipe structure."""
    if not md_path.exists():
        return []

    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    total_lines = max(1, len(lines))

    chunks: list[RagChunk] = []
    chunk_counter = 1

    current_h1: str = ""
    current_h2: str = ""
    current_h3: str = ""

    current_chunk_lines: list[str] = []
    current_chunk_start: int = 1
    current_title: str = "Wprowadzenie"

    def finalize_chunk(end_line: int):
        nonlocal chunk_counter, current_chunk_lines, current_chunk_start, current_title
        if not current_chunk_lines:
            return

        chunk_text = "\n".join(current_chunk_lines).strip()
        if not chunk_text:
            current_chunk_lines = []
            return

        words = re.findall(r"\b\w+\b", chunk_text)
        word_count = len(words)
        if word_count == 0:
            current_chunk_lines = []
            return

        # Determine parent sections hierarchy
        parents = [h for h in (current_h1, current_h2, current_h3) if h]

        # Detect chunk type
        is_recipe = is_recipe_content(chunk_text)
        is_table = "|---" in chunk_text or "|:--" in chunk_text
        if is_recipe:
            chunk_type = "recipe"
        elif is_table:
            chunk_type = "table"
        else:
            chunk_type = "prose"

        # Measurements and assets
        measurements = extract_culinary_measurements(chunk_text)
        assets = extract_chunk_assets(chunk_text)

        # Estimate source page (1 to total_estimated_pages)
        mid_line = (current_chunk_start + end_line) // 2
        est_page = max(1, min(total_estimated_pages, int(round((mid_line / total_lines) * total_estimated_pages))))

        chunk = RagChunk(
            chunk_id=f"chunk_{chunk_counter:05d}",
            chunk_type=chunk_type,
            title=current_title,
            parent_sections=parents,
            content=chunk_text,
            word_count=word_count,
            culinary_measurements=measurements,
            assets_referenced=assets,
            line_start=current_chunk_start,
            line_end=end_line,
            estimated_page=est_page,
        )
        chunks.append(chunk)
        chunk_counter += 1
        current_chunk_lines = []

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Heading 1
        if stripped.startswith("# ") and not stripped.startswith("## "):
            finalize_chunk(idx - 1)
            current_h1 = stripped.lstrip("# ").strip()
            current_h2 = ""
            current_h3 = ""
            current_title = current_h1
            current_chunk_start = idx
            current_chunk_lines.append(line)
            continue

        # Heading 2
        if stripped.startswith("## ") and not stripped.startswith("### "):
            finalize_chunk(idx - 1)
            current_h2 = stripped.lstrip("# ").strip()
            current_h3 = ""
            current_title = current_h2
            current_chunk_start = idx
            current_chunk_lines.append(line)
            continue

        # Heading 3
        if stripped.startswith("### "):
            finalize_chunk(idx - 1)
            current_h3 = stripped.lstrip("# ").strip()
            current_title = current_h3
            current_chunk_start = idx
            current_chunk_lines.append(line)
            continue

        # Append regular line
        if not current_chunk_lines:
            current_chunk_start = idx

        current_chunk_lines.append(line)

        # Split on paragraph boundary if word limit exceeded
        if stripped == "":
            current_words = sum(len(re.findall(r"\b\w+\b", l)) for l in current_chunk_lines)
            if current_words >= max_words_per_chunk:
                finalize_chunk(idx)

    # Finalize remaining lines
    finalize_chunk(len(lines))

    return chunks


def generate_embedding_ollama(
    text: str,
    endpoint: str = "http://172.22.192.1:11434",
    model: str = "nomic-embed-text",
    timeout_s: float = 10.0,
) -> Optional[list[float]]:
    """Generate vector embedding for text using Ollama model running on GPU."""
    url = f"{endpoint.rstrip('/')}/api/embeddings"
    payload = json.dumps({"model": model, "prompt": text[:2000]}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            if response.status == 200:
                body = json.loads(response.read().decode("utf-8"))
                return body.get("embedding")
    except Exception:
        # Fallback to localhost if host ip fails
        if "172.22." in endpoint:
            try:
                local_url = "http://localhost:11434/api/embeddings"
                req_local = urllib.request.Request(
                    local_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req_local, timeout=timeout_s) as resp:
                    if resp.status == 200:
                        body = json.loads(resp.read().decode("utf-8"))
                        return body.get("embedding")
            except Exception:
                pass
        return None
    return None


def export_rag_knowledge_base(
    md_path: Path,
    output_dir: Path = Path("output/rag"),
    embed: bool = False,
    ollama_endpoint: str = "http://172.22.192.1:11434",
    embedding_model: str = "nomic-embed-text",
    max_words_per_chunk: int = 400,
    max_embed_chunks: Optional[int] = None,
) -> tuple[Path, Path, RagExportSummary]:
    """Export Markdown document to RAG JSONL and JSON formats with optional GPU embedding."""
    output_dir.mkdir(parents=True, exist_ok=True)
    now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"rag_export_{now_str}"

    chunks = chunk_markdown_document(md_path, max_words_per_chunk=max_words_per_chunk)

    total_chunks = len(chunks)
    total_words = sum(c.word_count for c in chunks)
    recipe_chunks_count = sum(1 for c in chunks if c.chunk_type == "recipe")
    prose_chunks_count = sum(1 for c in chunks if c.chunk_type == "prose")
    total_culinary_measurements = sum(len(c.culinary_measurements) for c in chunks)
    total_assets_referenced = sum(len(c.assets_referenced) for c in chunks)
    embedded_chunks_count = 0

    if embed and chunks:
        limit = max_embed_chunks if max_embed_chunks is not None else total_chunks
        for i in range(min(limit, len(chunks))):
            emb = generate_embedding_ollama(
                chunks[i].content,
                endpoint=ollama_endpoint,
                model=embedding_model,
            )
            if emb:
                chunks[i].embedding = emb
                embedded_chunks_count += 1

    stem = md_path.stem if md_path.exists() else "ksiazka"
    jsonl_path = output_dir / f"{stem}_rag_chunks.jsonl"
    json_path = output_dir / f"{stem}_rag_summary.json"

    # Write JSONL
    with jsonl_path.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(chunk.model_dump_json() + "\n")

    summary = RagExportSummary(
        run_id=run_id,
        source_file=str(md_path),
        total_chunks=total_chunks,
        total_words=total_words,
        recipe_chunks_count=recipe_chunks_count,
        prose_chunks_count=prose_chunks_count,
        total_culinary_measurements=total_culinary_measurements,
        total_assets_referenced=total_assets_referenced,
        embedded_chunks_count=embedded_chunks_count,
        embedding_model=embedding_model if embed else None,
        jsonl_path=str(jsonl_path),
        json_path=str(json_path),
    )

    # Write summary JSON
    json_path.write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")

    return jsonl_path, json_path, summary
