"""Explicit OCR engine modes, candidate run isolation, and production write protection.

Invariants:
- Candidate conversions NEVER write directly to output/markdown/ksiazka.md or production paths.
- All candidate exports reside in output/candidates/<run_id>/.
- Modes: 'native' (PDF text layer baseline), 'full_page' (Docling + EasyOCR full page mode replacing broken layer),
  'layout_regions' (Docling OCR on segmented regions).
- CandidateRunManifest records run configuration, parameters, SHA-256 of output markdown, and sets is_production_truth=False.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time
from typing import Any, Optional

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    EasyOcrOptions,
    RapidOcrOptions,
    OcrMode,
)
from docling_core.types.doc.base import ImageRefMode
from pydantic import BaseModel, Field

from src.extraction_adapter import (
    ExtractedPage,
    from_docling_dict,
    save_extracted_page,
    compute_file_sha256,
)


class ProductionOverwriteError(Exception):
    """Raised when an operation attempts to write candidate files directly to production truth."""


class OcrEngineMode(str, Enum):
    """Supported OCR operating modes."""

    NATIVE = "native"
    FULL_PAGE = "full_page"
    LAYOUT_REGIONS = "layout_regions"
    RAPID_OCR = "rapidocr"


class CandidateRunConfig(BaseModel):
    """Configuration used for an isolated candidate conversion run."""

    run_id: str
    mode: OcrEngineMode
    languages: list[str] = Field(default_factory=lambda: ["pl", "en"])
    pages: list[int]
    source_pdf_path: str
    source_pdf_sha256: str
    output_dir: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    docling_version: str
    docling_pipeline_options: dict[str, Any] = Field(default_factory=dict)
    apply_geometry: bool = False


class CandidateRunManifest(BaseModel):
    """Manifest describing a candidate export run and certifying it is NOT production truth."""

    config: CandidateRunConfig
    pages_converted: list[int]
    total_pages: int
    total_blocks: int
    candidate_markdown_path: Optional[str] = None
    candidate_markdown_sha256: Optional[str] = None
    duration_seconds: Optional[float] = None
    page_durations: dict[int, float] = Field(default_factory=dict)
    geometry_reports: dict[int, Any] = Field(default_factory=dict)
    completed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_production_truth: bool = False


def ensure_candidate_does_not_overwrite_production(target_path: Path) -> None:
    """Security guard verifying that a candidate export never targets production paths."""
    resolved = target_path.resolve()
    production_md = Path("output/markdown/ksiazka.md").resolve()
    production_dir = Path("output/markdown").resolve()

    if resolved == production_md:
        raise ProductionOverwriteError(
            f"Zablokowano próbę bezpośredniego zapisu kandydata do produkcyjnego pliku: {target_path}"
        )

    # If inside output/markdown without candidates/ or benchmarks/ in the path, reject
    if "candidates" not in str(resolved) and "benchmarks" not in str(resolved):
        if resolved == production_dir or production_dir in resolved.parents:
            raise ProductionOverwriteError(
                f"Zablokowano próbę zapisu kandydata do katalogu produkcyjnego {target_path}. "
                "Wszystkie przebiegi kandydackie muszą być zapisywane w output/candidates/<run_id>/."
            )


def get_mode_converter(mode: OcrEngineMode, langs: list[str]) -> tuple[DocumentConverter, dict[str, Any]]:
    """Build a DocumentConverter with explicit OCR mode settings."""
    pipeline_options = PdfPipelineOptions()
    pipeline_options.generate_page_images = False
    pipeline_options.generate_picture_images = True

    options_summary: dict[str, Any] = {
        "mode": mode.value,
        "languages": langs,
    }

    if mode == OcrEngineMode.NATIVE:
        pipeline_options.do_ocr = False
        options_summary["do_ocr"] = False
    elif mode == OcrEngineMode.FULL_PAGE:
        pipeline_options.do_ocr = True
        pipeline_options.ocr_options = EasyOcrOptions(
            lang=langs,
            mode=OcrMode.FULL_PAGE,
        )
        options_summary["do_ocr"] = True
        options_summary["ocr_mode"] = "FULL_PAGE"
    elif mode == OcrEngineMode.LAYOUT_REGIONS:
        pipeline_options.do_ocr = True
        pipeline_options.ocr_options = EasyOcrOptions(
            lang=langs,
            mode=OcrMode.LAYOUT_REGIONS,
        )
        options_summary["do_ocr"] = True
        options_summary["ocr_mode"] = "LAYOUT_REGIONS"
    elif mode == OcrEngineMode.RAPID_OCR:
        pipeline_options.do_ocr = True
        rapid_lang = "pl" if "pl" in langs else (langs[0] if langs else "latin")
        pipeline_options.ocr_options = RapidOcrOptions(
            mode=OcrMode.FULL_PAGE,
            lang=[rapid_lang],
        )
        options_summary["do_ocr"] = True
        options_summary["ocr_engine"] = "RapidOCR"
        options_summary["ocr_mode"] = "FULL_PAGE"
        options_summary["rapid_lang"] = rapid_lang

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )
    return converter, options_summary


def run_candidate_conversion(
    pdf_path: Path,
    pages: list[int],
    mode: OcrEngineMode = OcrEngineMode.NATIVE,
    languages: Optional[list[str]] = None,
    output_base_dir: Path = Path("output/candidates"),
    run_id: Optional[str] = None,
    apply_geometry: bool = False,
    converter: Optional[DocumentConverter] = None,
) -> CandidateRunManifest:
    """Execute an isolated candidate conversion run across specified pages.

    Invariants:
    - Never overwrites output/markdown/ksiazka.md.
    - Exports Markdown, asset files, JSON sidecars per page, and manifest.json.
    - Sets is_production_truth = False.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"Source PDF not found: {pdf_path}")
    if not pages:
        raise ValueError("pages list cannot be empty")

    sorted_pages = sorted(set(pages))
    langs = languages or ["pl", "en"]

    if run_id is None:
        run_id = f"cand_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{mode.value}"

    cand_dir = output_base_dir / run_id
    ensure_candidate_does_not_overwrite_production(cand_dir)

    pages_dir = cand_dir / "pages"
    assets_dir = cand_dir / "assets"
    cand_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    pdf_sha = compute_file_sha256(pdf_path)
    t0_total = time.perf_counter()
    docling_ver = importlib.metadata.version("docling")

    if converter is None:
        converter, pipeline_summary = get_mode_converter(mode, langs)
    else:
        pipeline_summary = {"mode": mode.value, "languages": langs, "reused_converter": True}
    config = CandidateRunConfig(
        run_id=run_id,
        mode=mode,
        languages=langs,
        pages=sorted_pages,
        source_pdf_path=str(pdf_path),
        source_pdf_sha256=pdf_sha,
        output_dir=str(cand_dir),
        docling_version=docling_ver,
        docling_pipeline_options=pipeline_summary,
        apply_geometry=apply_geometry,
    )

    # Determine contiguous page range if possible, else process per page
    min_page, max_page = sorted_pages[0], sorted_pages[-1]
    is_contiguous = sorted_pages == list(range(min_page, max_page + 1))

    total_blocks = 0
    pages_converted: list[int] = []
    page_durations: dict[int, float] = {}
    geometry_reports: dict[int, Any] = {}

    if is_contiguous:
        t0_conv = time.perf_counter()
        conv_res = converter.convert(pdf_path, page_range=(min_page, max_page))
        conv_time = time.perf_counter() - t0_conv
        doc = conv_res.document
        doc_dict = doc.export_to_dict()

        avg_page_time = round(conv_time / max(1, len(sorted_pages)), 3)
        processed_pages: list[ExtractedPage] = []

        for pno in sorted_pages:
            page_durations[pno] = avg_page_time
            extracted_page = from_docling_dict(
                doc_dict=doc_dict,
                run_id=run_id,
                engine=f"docling_{mode.value}",
                engine_version=docling_ver,
                source_pdf_path=pdf_path,
                source_pdf_sha256=pdf_sha,
                target_page_no=pno,
                transforms=[mode.value],
            )
            if apply_geometry:
                from src.ground_truth import render_page_image
                from src.geometry import process_page_geometry

                img_path, _ = render_page_image(pdf_path, pno, cand_dir / "images")
                extracted_page, geo_rep = process_page_geometry(extracted_page, img_path)
                geometry_reports[pno] = geo_rep.model_dump()

            save_extracted_page(extracted_page, pages_dir)
            total_blocks += len(extracted_page.blocks)
            pages_converted.append(pno)
            processed_pages.append(extracted_page)

        # Export candidate Markdown
        cand_md_file = cand_dir / f"{pdf_path.stem}.md"
        ensure_candidate_does_not_overwrite_production(cand_md_file)
        if apply_geometry:
            md_text = "\n\n---\n\n".join(p.full_text() for p in processed_pages) + "\n"
            cand_md_file.write_text(md_text, encoding="utf-8")
        else:
            doc.save_as_markdown(
                cand_md_file,
                image_mode=ImageRefMode.REFERENCED,
                artifacts_dir=assets_dir,
            )
    else:
        # Non-contiguous pages: convert each page individually
        md_pieces: list[str] = []
        for pno in sorted_pages:
            t0_page = time.perf_counter()
            conv_res = converter.convert(pdf_path, page_range=(pno, pno))
            page_durations[pno] = round(time.perf_counter() - t0_page, 3)

            doc = conv_res.document
            doc_dict = doc.export_to_dict()

            extracted_page = from_docling_dict(
                doc_dict=doc_dict,
                run_id=run_id,
                engine=f"docling_{mode.value}",
                engine_version=docling_ver,
                source_pdf_path=pdf_path,
                source_pdf_sha256=pdf_sha,
                target_page_no=pno,
                transforms=[mode.value],
            )
            if apply_geometry:
                from src.ground_truth import render_page_image
                from src.geometry import process_page_geometry

                img_path, _ = render_page_image(pdf_path, pno, cand_dir / "images")
                extracted_page, geo_rep = process_page_geometry(extracted_page, img_path)
                geometry_reports[pno] = geo_rep.model_dump()

            save_extracted_page(extracted_page, pages_dir)
            total_blocks += len(extracted_page.blocks)
            pages_converted.append(pno)

            if apply_geometry:
                single_md = extracted_page.full_text()
            else:
                single_md = doc.export_to_markdown(
                    image_mode=ImageRefMode.REFERENCED,
                    image_dir=assets_dir,
                )
            md_pieces.append(single_md)

        cand_md_file = cand_dir / f"{pdf_path.stem}.md"
        ensure_candidate_does_not_overwrite_production(cand_md_file)
        cand_md_file.write_text("\n\n---\n\n".join(md_pieces) + "\n", encoding="utf-8")

    total_duration = round(time.perf_counter() - t0_total, 3)
    md_sha256 = compute_file_sha256(cand_md_file)

    manifest = CandidateRunManifest(
        config=config,
        pages_converted=pages_converted,
        total_pages=len(pages_converted),
        total_blocks=total_blocks,
        candidate_markdown_path=str(cand_md_file.relative_to(cand_dir)),
        candidate_markdown_sha256=md_sha256,
        duration_seconds=total_duration,
        page_durations=page_durations,
        geometry_reports=geometry_reports,
        is_production_truth=False,
    )

    manifest_file = cand_dir / "manifest.json"
    manifest_file.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return manifest


def load_candidate_manifest(manifest_path: Path) -> CandidateRunManifest:
    """Load and validate a CandidateRunManifest from disk."""
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return CandidateRunManifest.model_validate(data)
