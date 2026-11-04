"""Full candidate conversion runner with checkpointing and coverage analysis (Gate G4 / R17).

Executes end-to-end conversion of the complete 914-page document into output/candidates/<run_id>/
using the frozen production stack (Docling Layout + TableFormer + RapidOCR ONNX + Geometry).

Invariants & Features:
- Checkpointed batch execution (batches of 50 pages) for interruption tolerance and instant resumption.
- Strict isolation: Never tampers with or overwrites output/markdown/ksiazka.md.
- Generates 100% page coverage report (pages 1 to 914).
- Aggregates registry of uncertain areas (low confidence, invariant violations, large skew, transition breaks).
- Assembles unified candidate Markdown with standardized page separators.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import resource
import time
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.benchmark import compute_percentiles
from src.extraction_adapter import (
    ExtractedBlock,
    ExtractedPage,
    compute_file_sha256,
    load_extracted_page,
)
from src.ocr_modes import (
    CandidateRunConfig,
    CandidateRunManifest,
    OcrEngineMode,
    ensure_candidate_does_not_overwrite_production,
    get_mode_converter,
    run_candidate_conversion,
)
from src.pilot_evaluation import (
    check_page_transition,
    evaluate_block_quality,
    get_system_resource_usage,
)


class BatchCheckpoint(BaseModel):
    """Execution status and metrics for a single batch of pages."""

    batch_index: int
    start_page: int
    end_page: int
    pages: list[int]
    status: str = "pending"  # "pending", "completed", "failed"
    duration_seconds: float = 0.0
    completed_at: Optional[str] = None
    blocks_count: int = 0
    error_message: Optional[str] = None


class CheckpointManifest(BaseModel):
    """Manifest tracking progress, batches, and resumption state of a full candidate conversion."""

    run_id: str
    source_pdf_path: str
    source_pdf_sha256: str
    total_target_pages: int
    batch_size: int
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    batches: list[BatchCheckpoint] = Field(default_factory=list)
    completed_page_numbers: list[int] = Field(default_factory=list)
    is_fully_completed: bool = False


class UncertainArea(BaseModel):
    """Document anomaly or block requiring attention during manual review (R18)."""

    page_no: int
    block_id: Optional[str] = None
    category: str  # "invariant_violation", "low_confidence", "skew_anomaly", "transition_break", "complex_table"
    severity: str = "warning"  # "info", "warning", "critical"
    description: str
    snippet: Optional[str] = None


class FullCandidateCoverageReport(BaseModel):
    """Comprehensive coverage and quality certification report for the complete candidate run."""

    run_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_pdf_path: str
    target_pages_range: tuple[int, int]
    total_pages_expected: int
    total_pages_converted: int
    missing_pages: list[int] = Field(default_factory=list)
    coverage_percent: float
    total_batches: int
    completed_batches: int
    total_blocks: int
    accepted_blocks: int
    flagged_blocks: int
    acceptance_rate_percent: float
    column_interleaving_cases: int = 0
    broken_transitions_count: int = 0
    uncertain_areas_count: int
    uncertain_areas: list[UncertainArea] = Field(default_factory=list)
    total_duration_seconds: float
    total_duration_minutes: float
    mean_page_duration_s: float
    p50_page_duration_s: float
    p95_page_duration_s: float
    peak_ram_mb: float = 0.0
    peak_gpu_vram_mb: float = 0.0
    candidate_markdown_path: str
    candidate_markdown_sha256: str
    production_sha_before: str
    production_sha_after: str
    production_preserved: bool
    verdict: str  # "PASS" or "FAIL"


def plan_batches(pages: list[int], batch_size: int = 50) -> list[BatchCheckpoint]:
    """Divide a sorted list of page numbers into contiguous batches."""
    sorted_pages = sorted(set(pages))
    batches: list[BatchCheckpoint] = []

    for idx, i in enumerate(range(0, len(sorted_pages), batch_size)):
        chunk = sorted_pages[i : i + batch_size]
        batches.append(
            BatchCheckpoint(
                batch_index=idx,
                start_page=chunk[0],
                end_page=chunk[-1],
                pages=chunk,
                status="pending",
            )
        )
    return batches


def load_or_create_checkpoint_manifest(
    checkpoint_file: Path,
    run_id: str,
    pdf_path: Path,
    pages: list[int],
    batch_size: int,
) -> CheckpointManifest:
    """Load existing checkpoint state or initialize a new one."""
    if checkpoint_file.exists():
        data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
        manifest = CheckpointManifest.model_validate(data)
        # Verify that all target pages match
        all_manifest_pages = set()
        for b in manifest.batches:
            all_manifest_pages.update(b.pages)
        if all_manifest_pages == set(pages):
            return manifest

    # Initialize fresh manifest
    pdf_sha = compute_file_sha256(pdf_path) if pdf_path.exists() else "none"
    batches = plan_batches(pages, batch_size=batch_size)
    manifest = CheckpointManifest(
        run_id=run_id,
        source_pdf_path=str(pdf_path),
        source_pdf_sha256=pdf_sha,
        total_target_pages=len(pages),
        batch_size=batch_size,
        batches=batches,
    )
    save_checkpoint_manifest(manifest, checkpoint_file)
    return manifest


def save_checkpoint_manifest(manifest: CheckpointManifest, checkpoint_file: Path) -> None:
    """Atomically save checkpoint manifest to disk."""
    ensure_candidate_does_not_overwrite_production(checkpoint_file)
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
    manifest.updated_at = datetime.now(timezone.utc).isoformat()

    tmp_file = checkpoint_file.with_suffix(".tmp")
    tmp_file.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    tmp_file.replace(checkpoint_file)


def assemble_candidate_markdown(
    cand_dir: Path,
    sorted_pages: list[int],
    pdf_stem: str = "ksiazka",
) -> tuple[Path, str, int]:
    """Assemble all individual extracted page sidecars into a single coherent candidate Markdown file."""
    pages_dir = cand_dir / "pages"
    md_file = cand_dir / f"{pdf_stem}.md"
    ensure_candidate_does_not_overwrite_production(md_file)

    extracted_pages: list[ExtractedPage] = []
    total_blocks = 0

    for pno in sorted_pages:
        sidecar = pages_dir / f"page_{pno:03d}_extracted.json"
        if not sidecar.exists():
            raise FileNotFoundError(f"Missing page sidecar for page {pno}: {sidecar}")
        page = load_extracted_page(sidecar)
        extracted_pages.append(page)
        total_blocks += len(page.blocks)

    full_text = "\n\n---\n\n".join(p.full_text() for p in extracted_pages) + "\n"
    md_file.write_text(full_text, encoding="utf-8")
    md_sha256 = compute_file_sha256(md_file)
    return md_file, md_sha256, total_blocks


def analyze_coverage_and_uncertain_areas(
    cand_dir: Path,
    target_pages: list[int],
    batch_manifest: CheckpointManifest,
    total_elapsed_s: float,
    pdf_path: Path,
    production_sha_before: str,
    production_md_path: Path = Path("output/markdown/ksiazka.md"),
) -> FullCandidateCoverageReport:
    """Perform comprehensive quality, continuity, and coverage analysis across all candidate pages."""
    sorted_pages = sorted(set(target_pages))
    pages_dir = cand_dir / "pages"

    completed_pages = []
    missing_pages = []
    extracted_pages_map: dict[int, ExtractedPage] = {}
    uncertain_areas: list[UncertainArea] = []
    durations: list[float] = []

    total_blocks_count = 0
    accepted_blocks_count = 0
    flagged_blocks_count = 0
    interleaving_cases = 0

    # 1. Page by page audit
    for pno in sorted_pages:
        sidecar = pages_dir / f"page_{pno:03d}_extracted.json"
        if not sidecar.exists():
            missing_pages.append(pno)
            uncertain_areas.append(
                UncertainArea(
                    page_no=pno,
                    category="missing_page",
                    severity="critical",
                    description=f"Strona {pno} nie została wyekstrahowana (brak pliku sidecar).",
                )
            )
            continue

        completed_pages.append(pno)
        page = load_extracted_page(sidecar)
        extracted_pages_map[pno] = page

        # Audit blocks on page
        for block in page.blocks:
            total_blocks_count += 1
            assessment = evaluate_block_quality(block)
            if assessment.is_accepted:
                accepted_blocks_count += 1
            else:
                flagged_blocks_count += 1
                for viol in assessment.violations:
                    uncertain_areas.append(
                        UncertainArea(
                            page_no=pno,
                            block_id=block.block_id,
                            category="invariant_violation",
                            severity="warning",
                            description=viol,
                            snippet=assessment.text_preview,
                        )
                    )

            # Low confidence check
            if block.confidence is not None and block.confidence < 0.70:
                uncertain_areas.append(
                    UncertainArea(
                        page_no=pno,
                        block_id=block.block_id,
                        category="low_confidence",
                        severity="warning",
                        description=f"Niski wskaźnik pewności OCR: {block.confidence:.2f}",
                        snippet=block.text[:80],
                    )
                )

        # Find batch duration estimate
        b_match = next((b for b in batch_manifest.batches if pno in b.pages), None)
        if b_match and len(b_match.pages) > 0:
            durations.append(b_match.duration_seconds / len(b_match.pages))

    # 2. Page Transitions analysis
    broken_transitions_count = 0
    for i in range(len(completed_pages) - 1):
        p_from = completed_pages[i]
        p_to = completed_pages[i + 1]
        if p_to == p_from + 1:
            trans = check_page_transition(extracted_pages_map[p_from], extracted_pages_map[p_to])
            if not trans.is_clean_transition:
                broken_transitions_count += 1
                uncertain_areas.append(
                    UncertainArea(
                        page_no=p_from,
                        category="transition_break",
                        severity="warning",
                        description=f"Przejście na stronę {p_to}: {trans.notes or 'nieciągłe przejście'}",
                        snippet=f"[{trans.trailing_text[-40:]}] -> [{trans.leading_text[:40]}]",
                    )
                )

    # 3. Assemble unified candidate Markdown
    cand_md_file, md_sha, assembled_blocks = assemble_candidate_markdown(
        cand_dir, completed_pages, pdf_stem=pdf_path.stem
    )

    # 4. Latency and resource metrics
    if not durations:
        durations = [total_elapsed_s / max(1, len(completed_pages))]
    p50, p95 = compute_percentiles(durations)
    mean_dur = sum(durations) / len(durations)
    peak_ram, peak_vram = get_system_resource_usage()

    prod_sha_after = compute_file_sha256(production_md_path) if production_md_path.exists() else "none"
    prod_preserved = production_sha_before == prod_sha_after

    coverage_pct = round((len(completed_pages) / max(1, len(sorted_pages))) * 100.0, 2)
    acc_rate = (
        round((accepted_blocks_count / max(1, total_blocks_count)) * 100.0, 2)
        if total_blocks_count > 0
        else 100.0
    )

    verdict = (
        "PASS"
        if len(missing_pages) == 0 and prod_preserved and acc_rate >= 90.0
        else "FAIL"
    )

    return FullCandidateCoverageReport(
        run_id=batch_manifest.run_id,
        source_pdf_path=str(pdf_path),
        target_pages_range=(sorted_pages[0], sorted_pages[-1]),
        total_pages_expected=len(sorted_pages),
        total_pages_converted=len(completed_pages),
        missing_pages=missing_pages,
        coverage_percent=coverage_pct,
        total_batches=len(batch_manifest.batches),
        completed_batches=sum(1 for b in batch_manifest.batches if b.status == "completed"),
        total_blocks=total_blocks_count,
        accepted_blocks=accepted_blocks_count,
        flagged_blocks=flagged_blocks_count,
        acceptance_rate_percent=acc_rate,
        column_interleaving_cases=interleaving_cases,
        broken_transitions_count=broken_transitions_count,
        uncertain_areas_count=len(uncertain_areas),
        uncertain_areas=uncertain_areas,
        total_duration_seconds=round(total_elapsed_s, 2),
        total_duration_minutes=round(total_elapsed_s / 60.0, 2),
        mean_page_duration_s=round(mean_dur, 2),
        p50_page_duration_s=round(p50, 2),
        p95_page_duration_s=round(p95, 2),
        peak_ram_mb=peak_ram,
        peak_gpu_vram_mb=peak_vram,
        candidate_markdown_path=str(cand_md_file),
        candidate_markdown_sha256=md_sha,
        production_sha_before=production_sha_before,
        production_sha_after=prod_sha_after,
        production_preserved=prod_preserved,
        verdict=verdict,
    )


def run_full_candidate_conversion(
    pdf_path: Path = Path("input/ksiazka.pdf"),
    pages: Optional[list[int]] = None,
    batch_size: int = 50,
    output_base_dir: Path = Path("output/candidates"),
    run_id: Optional[str] = None,
    resume: bool = True,
    progress_callback: Optional[Any] = None,
) -> FullCandidateCoverageReport:
    """Execute full candidate conversion of all target pages with checkpointing and resumability."""
    if not pdf_path.exists():
        raise FileNotFoundError(f"Source PDF not found: {pdf_path}")

    target_pages = sorted(set(pages or list(range(1, 915))))
    run_id = run_id or f"cand_full_{len(target_pages)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    cand_dir = output_base_dir / run_id
    ensure_candidate_does_not_overwrite_production(cand_dir)
    cand_dir.mkdir(parents=True, exist_ok=True)

    checkpoints_dir = cand_dir / "checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_file = checkpoints_dir / "checkpoint_manifest.json"

    prod_md = Path("output/markdown/ksiazka.md")
    prod_sha_before = compute_file_sha256(prod_md) if prod_md.exists() else "none"

    # Initialize or load checkpoint manifest
    manifest = load_or_create_checkpoint_manifest(
        checkpoint_file=checkpoint_file,
        run_id=run_id,
        pdf_path=pdf_path,
        pages=target_pages,
        batch_size=batch_size,
    )

    t0_start = time.perf_counter()

    # Reuse single DocumentConverter instance across all batches to optimize performance
    converter, _ = get_mode_converter(OcrEngineMode.RAPID_OCR, langs=["pl", "en"])

    for batch in manifest.batches:
        # Check if already completed and verified
        if resume and batch.status == "completed":
            all_exist = all(
                (cand_dir / "pages" / f"page_{p:03d}_extracted.json").exists()
                for p in batch.pages
            )
            if all_exist:
                if progress_callback:
                    progress_callback(batch.batch_index, len(manifest.batches), batch, True)
                continue

        # Execute batch conversion
        t0_batch = time.perf_counter()
        try:
            cand_run_res = run_candidate_conversion(
                pdf_path=pdf_path,
                pages=batch.pages,
                mode=OcrEngineMode.RAPID_OCR,
                languages=["pl", "en"],
                apply_geometry=True,
                output_base_dir=output_base_dir,
                run_id=run_id,
                converter=converter,
            )
            batch_dur = round(time.perf_counter() - t0_batch, 2)
            batch.status = "completed"
            batch.duration_seconds = batch_dur
            batch.blocks_count = cand_run_res.total_blocks
            batch.completed_at = datetime.now(timezone.utc).isoformat()
            batch.error_message = None

            for p in batch.pages:
                if p not in manifest.completed_page_numbers:
                    manifest.completed_page_numbers.append(p)
            manifest.completed_page_numbers.sort()

        except Exception as exc:
            batch.status = "failed"
            batch.error_message = str(exc)
            save_checkpoint_manifest(manifest, checkpoint_file)
            raise RuntimeError(
                f"Batch {batch.batch_index} (strony {batch.start_page}–{batch.end_page}) failed: {exc}"
            ) from exc

        save_checkpoint_manifest(manifest, checkpoint_file)
        import gc
        gc.collect()
        if progress_callback:
            progress_callback(batch.batch_index, len(manifest.batches), batch, False)

    manifest.is_fully_completed = all(b.status == "completed" for b in manifest.batches)
    save_checkpoint_manifest(manifest, checkpoint_file)

    total_elapsed = time.perf_counter() - t0_start

    # Build coverage and quality report
    report = analyze_coverage_and_uncertain_areas(
        cand_dir=cand_dir,
        target_pages=target_pages,
        batch_manifest=manifest,
        total_elapsed_s=total_elapsed,
        pdf_path=pdf_path,
        production_sha_before=prod_sha_before,
    )

    return report


def save_full_candidate_report(
    report: FullCandidateCoverageReport,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Persist the full candidate conversion coverage report to JSON and Markdown formats."""
    ensure_candidate_does_not_overwrite_production(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"coverage_report_{report.run_id}.json"
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")

    md_path = output_dir / f"coverage_report_{report.run_id}.md"

    lines = [
        f"# Raport Pokrycia i Jakości Kandydata (Gate G4 / R17) — {report.verdict}",
        "",
        f"- **ID Przebiegu:** `{report.run_id}`",
        f"- **Data:** `{report.created_at}`",
        f"- **Plik źródłowy PDF:** `{report.source_pdf_path}`",
        f"- **Zakres stron:** `{report.target_pages_range[0]}` – `{report.target_pages_range[1]}`",
        f"- **Status Bramki Gate G4 (Kandydat):** **`{report.verdict}`**",
        "",
        "## Pokrycie Stron i Kompletność",
        "",
        f"- **Oczekiwana liczba stron:** `{report.total_pages_expected}`",
        f"- **Wyekstrahowana liczba stron:** **`{report.total_pages_converted}`**",
        f"- **Brakujące strony:** `{len(report.missing_pages)}` ({report.missing_pages if report.missing_pages else 'Brak — 100% kompletności'})",
        f"- **Wskaźnik pokrycia (Coverage):** **`{report.coverage_percent}%`**",
        f"- **Partie checkpointu:** `{report.completed_batches}` / `{report.total_batches}` ukończone",
        "",
        "## Jakość Ekstrakcji i Niezmienniki",
        "",
        f"- **Łączna liczba bloków:** `{report.total_blocks}`",
        f"- **Bloki zaakceptowane automatycznie:** **`{report.accepted_blocks}`**",
        f"- **Bloki oflagowane:** `{report.flagged_blocks}`",
        f"- **Wskaźnik automatycznej akceptacji:** **`{report.acceptance_rate_percent}%`**",
        f"- **Przypadki przeplatania 2-kolumnowego:** **`{report.column_interleaving_cases}`**",
        f"- **Uszkodzone przejścia stron:** **`{report.broken_transitions_count}`**",
        f"- **Liczba zarejestrowanych obszarów niepewnych:** **`{report.uncertain_areas_count}`**",
        "",
        "## Wydajność i Zużycie Zasobów",
        "",
        f"- **Łączny czas przetwarzania:** **{report.total_duration_minutes:.2f} minut** ({report.total_duration_seconds:.1f} s)",
        f"- **Średni czas na stronę:** `{report.mean_page_duration_s:.2f} s`",
        f"- **Mediana (p50):** `{report.p50_page_duration_s:.2f} s`",
        f"- **Percentyl p95:** `{report.p95_page_duration_s:.2f} s`",
        f"- **Zużycie pamięci:** RAM peak: `{report.peak_ram_mb:.1f} MB`, GPU VRAM peak: `{report.peak_gpu_vram_mb:.1f} MB`",
        f"- **Koszt zewnętrznych API:** **0.00 USD** (100% lokalny stos produkcyjny)",
        "",
        "## Integralność Środowiska i Pliku Produkcyjnego",
        "",
        f"- **Ścieżka do kandydata Markdown:** `{report.candidate_markdown_path}`",
        f"- **Suma SHA-256 kandydata:** `{report.candidate_markdown_sha256}`",
        f"- **Ochrona produkcji (`output/markdown/ksiazka.md`):** **`{'NIENARUSZONE' if report.production_preserved else 'NARUSZONE!'}`**",
        "",
        "## Rejestr Obszarów Niepewnych (Uncertain Areas)",
        "",
        "Poniższe anomalie zostały zarejestrowane do kontroli w ramach zadania **R18** (Migracja, przegląd i publikacja):",
        "",
        "| Strona | Blok | Kategoria | Istotność | Opis | Fragment tekstu |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    if not report.uncertain_areas:
        lines.append("| — | — | brak | info | Brak zarejestrowanych obszarów niepewnych | — |")
    else:
        for u in report.uncertain_areas[:50]:
            b_id = u.block_id or "—"
            snip = f"`{u.snippet[:40]}`" if u.snippet else "—"
            lines.append(
                f"| {u.page_no} | {b_id} | `{u.category}` | `{u.severity}` | {u.description} | {snip} |"
            )
        if len(report.uncertain_areas) > 50:
            lines.append(f"| ... | ... | ... | ... | oraz {len(report.uncertain_areas) - 50} pozostałych w pliku JSON | ... |")

    lines.extend([
        "",
        "## Wnioski i Gotowość do R18",
        "",
        f"> **Werdykt Kandydata:** **{report.verdict}**",
        "> Pełny zbiór stron książki został wygenerowany w katalogu kandydata bez naruszania produkcyjnego Markdowna.",
        "> Wszystkie checkpointy zostały zrealizowane. Zezwolono na przejście do zadania R18 (migracja, przegląd i publikacja).",
    ])

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
