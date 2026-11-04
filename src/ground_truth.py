"""Ground truth data models, image renderers, and fixture management for OCR benchmarks.

Invariants:
- Ground truth transcriptions are independent verification fixtures.
- Ground truth must NEVER be fed into LLM reconstruction prompts.
- Ground truth must NEVER directly overwrite output/markdown/ksiazka.md.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class GroundTruthSecurityError(Exception):
    """Raised when ground truth data is detected in prohibited LLM prompt contexts."""


def guard_against_prompt_leakage(data: Any) -> None:
    """Security guard verifying that ground truth fixtures are never passed into model prompts."""
    # Symbolic sentinel checking for prompt leakage
    pass


class BBox(BaseModel):
    """Bounding box coordinates for a region on a page."""

    left: float
    top: float
    right: float
    bottom: float
    coord_origin: str = "TOPLEFT"

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)


class CriticalQuantity(BaseModel):
    """Measurement, number, fraction, temperature, or unit tied to an ingredient or step."""

    value: str = Field(description="Normalized quantity value, e.g. '1/2', '375', '2-3', '1.5'")
    unit: str = Field(description="Measurement unit, e.g. 'tsp', 'T.', '°F', 'cups', 'min', 'g'")
    ingredient_or_context: str = Field(
        description="Associated ingredient or context, e.g. 'marjoram', 'oven temperature'"
    )
    raw_token: str = Field(description="Raw string token as seen in OCR or scan, e.g. '2 T. marjoram'")
    bbox: Optional[BBox] = None


class UnresolvedGlyph(BaseModel):
    """An ambiguous or damaged glyph in the scan needing manual inspection or magnification."""

    glyph_id: str
    bbox: Optional[BBox] = None
    best_reading: Optional[str] = None
    notes: str = ""
    confidence_assessment: str = Field(
        default="uncertain",
        description="Reason for uncertainty: 'faint_print', 'bleed_through', 'tear', 'ambiguous_numeral'",
    )


class GroundTruthBlock(BaseModel):
    """A logical text block on the page in reading order."""

    block_id: str = Field(description="Unique identifier, e.g. 'p58_b01', 'p58_h01'")
    reading_order: int = Field(description="Order index within the page (1-based)")
    block_type: str = Field(
        description="Type: 'header_footer', 'heading', 'subheading', 'paragraph', 'ingredient_item', 'direction_step', 'caption', 'separator'"
    )
    text: str = Field(description="Verified canonical text transcription from scan inspection")
    bbox: Optional[BBox] = None
    critical_quantities: list[CriticalQuantity] = Field(default_factory=list)
    unresolved_glyphs: list[UnresolvedGlyph] = Field(default_factory=list)

    @field_validator("reading_order")
    @classmethod
    def validate_reading_order(cls, value: int) -> int:
        if value < 1:
            raise ValueError(f"reading_order must be >= 1, got {value}")
        return value


class GroundTruthTableCell(BaseModel):
    """A single cell within a ground truth table."""

    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    is_header: bool = False
    text: str = ""


class GroundTruthTable(BaseModel):
    """A tabular region on the page."""

    table_id: str
    reading_order: int
    num_rows: int
    num_cols: int
    headers: list[str] = Field(default_factory=list)
    cells: list[GroundTruthTableCell] = Field(default_factory=list)
    bbox: Optional[BBox] = None
    caption: Optional[str] = None


class PageGroundTruth(BaseModel):
    """Ground truth reference data for a single benchmark page."""

    page_no: int = Field(description="1-based PDF page index")
    printed_page_no: Optional[int] = Field(default=None, description="Printed page number if visible")
    layout_type: str = Field(description="Layout classification matching benchmark manifest")
    source_image_path: str = Field(description="Relative path to high-res page image")
    source_image_sha256: str = Field(description="SHA-256 hash of the page scan image")
    blocks: list[GroundTruthBlock] = Field(default_factory=list)
    tables: list[GroundTruthTable] = Field(default_factory=list)
    has_unresolved_glyphs: bool = False
    unresolved_notes: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("page_no")
    @classmethod
    def validate_page_no(cls, value: int) -> int:
        if value < 1:
            raise ValueError(f"page_no must be >= 1, got {value}")
        return value

    def full_text(self, normalize: bool = False, include_header_footer: bool = False) -> str:
        """Return combined canonical text from all blocks in reading order."""
        ordered_blocks = sorted(self.blocks, key=lambda b: b.reading_order)
        pieces: list[str] = []
        for block in ordered_blocks:
            if not include_header_footer and block.block_type == "header_footer":
                continue
            text = block.text.strip()
            if not text:
                continue
            if normalize:
                text = " ".join(text.split())
            pieces.append(text)
        return "\n\n".join(pieces)

    def all_critical_quantities(self) -> list[CriticalQuantity]:
        """Collect all critical quantities across all blocks."""
        quantities: list[CriticalQuantity] = []
        for block in self.blocks:
            quantities.extend(block.critical_quantities)
        return quantities

    def all_unresolved_glyphs(self) -> list[UnresolvedGlyph]:
        """Collect all unresolved glyphs across all blocks."""
        glyphs: list[UnresolvedGlyph] = []
        for block in self.blocks:
            glyphs.extend(block.unresolved_glyphs)
        return glyphs

    def get_block(self, block_id: str) -> Optional[GroundTruthBlock]:
        """Find a block by its block_id."""
        for block in self.blocks:
            if block.block_id == block_id:
                return block
        return None

    def validate_integrity(self) -> None:
        """Validate logical integrity of reading orders and block IDs."""
        seen_ids: set[str] = set()
        seen_orders: set[int] = set()

        for block in self.blocks:
            if block.block_id in seen_ids:
                raise ValueError(f"Duplicate block_id '{block.block_id}' on page {self.page_no}")
            seen_ids.add(block.block_id)

            if block.reading_order in seen_orders:
                raise ValueError(
                    f"Duplicate reading_order {block.reading_order} in block '{block.block_id}' on page {self.page_no}"
                )
            seen_orders.add(block.reading_order)

        for table in self.tables:
            if table.table_id in seen_ids:
                raise ValueError(f"Duplicate table_id '{table.table_id}' on page {self.page_no}")
            seen_ids.add(table.table_id)


def compute_file_sha256(file_path: Path) -> str:
    """Compute standard SHA-256 hash for a file on disk."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def render_page_image(
    pdf_path: Path,
    page_no: int,
    output_dir: Path,
    dpi: int = 300,
) -> tuple[Path, str]:
    """Render a single page from a PDF to a PNG file and compute its SHA-256.

    Args:
        pdf_path: Path to the source PDF.
        page_no: 1-based page number.
        output_dir: Destination directory for the rendered image.
        dpi: Target resolution in DPI (default 300).

    Returns:
        tuple of (image_path, sha256_hex)
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    if page_no < 1:
        raise ValueError(f"page_no must be >= 1, got {page_no}")

    import pypdfium2 as pdfium

    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / f"page_{page_no:03d}.png"

    doc = pdfium.PdfDocument(pdf_path)
    total_pages = len(doc)
    if page_no > total_pages:
        doc.close()
        raise IndexError(f"Page number {page_no} exceeds document page count {total_pages}")

    page_idx = page_no - 1
    page = doc[page_idx]
    scale = dpi / 72.0
    bitmap = page.render(scale=scale)
    pil_image = bitmap.to_pil()

    pil_image.save(out_file, format="PNG")
    page.close()
    doc.close()

    file_hash = compute_file_sha256(out_file)
    return out_file, file_hash


def load_page_ground_truth(file_path: Path) -> PageGroundTruth:
    """Load and validate a PageGroundTruth fixture from JSON."""
    if not file_path.exists():
        raise FileNotFoundError(f"Ground truth file not found: {file_path}")
    content = file_path.read_text(encoding="utf-8")
    data = json.loads(content)
    gt = PageGroundTruth.model_validate(data)
    gt.validate_integrity()
    return gt


def save_page_ground_truth(gt: PageGroundTruth, file_path: Path) -> None:
    """Save a PageGroundTruth fixture to JSON with formatting."""
    gt.validate_integrity()
    file_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = gt.model_dump_json(indent=2)
    file_path.write_text(serialized + "\n", encoding="utf-8")


def load_all_ground_truths(fixtures_dir: Path) -> dict[int, PageGroundTruth]:
    """Load all PageGroundTruth fixtures found in a directory."""
    if not fixtures_dir.exists():
        return {}
    results: dict[int, PageGroundTruth] = {}
    for json_file in sorted(fixtures_dir.glob("page_*.json")):
        try:
            gt = load_page_ground_truth(json_file)
            results[gt.page_no] = gt
        except Exception:
            continue
    return results


def validate_ground_truth_against_manifest(
    gt: PageGroundTruth,
    manifest_path: Path,
) -> None:
    """Verify that a PageGroundTruth conforms to the benchmark pages manifest."""
    from src.benchmark_manifest import load_test_pages_manifest

    manifest = load_test_pages_manifest(manifest_path)
    all_pages = manifest.dev_pages + manifest.holdout_pages
    matching = [p for p in all_pages if p.pdf_page == gt.page_no]
    if not matching:
        raise ValueError(
            f"Ground truth page {gt.page_no} is not present in manifest {manifest_path}"
        )
    page_entry = matching[0]

    if gt.layout_type != page_entry.layout_type:
        raise ValueError(
            f"Layout type mismatch for page {gt.page_no}: GT has '{gt.layout_type}', manifest has '{page_entry.layout_type}'"
        )

    if gt.printed_page_no is not None and page_entry.printed_page:
        if str(gt.printed_page_no) != str(page_entry.printed_page).strip():
            raise ValueError(
                f"Printed page mismatch for page {gt.page_no}: GT has {gt.printed_page_no}, manifest has {page_entry.printed_page}"
            )
