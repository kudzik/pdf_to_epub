"""Extraction adapter and block provenance module.

Invariants:
- Every extracted block records its run_id, engine, version, geometry (bbox), and reading order.
- Full DoclingDocument JSON is preserved in intermediate structures for round-trip fidelity.
- Bounding box coordinates map deterministically to image crops.
- Does not modify or overwrite output/markdown/ksiazka.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from PIL import Image
from pydantic import BaseModel, Field, field_validator


class NormalizedBBox(BaseModel):
    """Bounding box coordinates with coordinate origin."""

    l: float
    t: float
    r: float
    b: float
    coord_origin: str = "BOTTOMLEFT"

    @property
    def width(self) -> float:
        return max(0.0, self.r - self.l)

    @property
    def height(self) -> float:
        return max(0.0, abs(self.t - self.b))

    def to_top_left_pixels(
        self,
        page_width_pt: float,
        page_height_pt: float,
        dpi: int = 300,
        padding_px: int = 0,
        image_width_px: Optional[int] = None,
        image_height_px: Optional[int] = None,
    ) -> tuple[int, int, int, int]:
        """Convert bounding box to image pixel coordinates (x_min, y_min, x_max, y_max).

        Origin (0,0) is at the TOP-LEFT of the image.
        """
        scale = dpi / 72.0
        x_min = self.l * scale
        x_max = self.r * scale

        if self.coord_origin.upper() == "BOTTOMLEFT":
            y_min = (page_height_pt - max(self.t, self.b)) * scale
            y_max = (page_height_pt - min(self.t, self.b)) * scale
        else:
            y_min = min(self.t, self.b) * scale
            y_max = max(self.t, self.b) * scale

        # Apply padding
        x_min_int = max(0, int(round(x_min - padding_px)))
        y_min_int = max(0, int(round(y_min - padding_px)))
        x_max_int = int(round(x_max + padding_px))
        y_max_int = int(round(y_max + padding_px))

        if image_width_px is not None:
            x_max_int = min(image_width_px, x_max_int)
        if image_height_px is not None:
            y_max_int = min(image_height_px, y_max_int)

        return x_min_int, y_min_int, max(x_min_int, x_max_int), max(y_min_int, y_max_int)


class BlockProvenance(BaseModel):
    """Provenance tracking origin, OCR engine, and transformations for an extracted block."""

    run_id: str
    engine: str
    engine_version: str
    source_pdf_path: str
    source_pdf_sha256: str
    page_no: int
    printed_page: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    transforms: list[str] = Field(default_factory=list)


class ExtractedBlock(BaseModel):
    """A logical extracted block with geometry, reading order, and provenance."""

    block_id: str
    page_no: int
    reading_order: int
    block_type: str = "paragraph"
    text: str
    raw_text: Optional[str] = None
    bbox: Optional[NormalizedBBox] = None
    confidence: Optional[float] = None
    provenance: BlockProvenance

    @field_validator("reading_order")
    @classmethod
    def validate_reading_order(cls, value: int) -> int:
        if value < 1:
            raise ValueError(f"reading_order must be >= 1, got {value}")
        return value


class ExtractedPage(BaseModel):
    """Structured extraction result for a page, including blocks and raw DoclingDocument JSON."""

    run_id: str
    page_no: int
    printed_page: Optional[str] = None
    width_pt: float
    height_pt: float
    blocks: list[ExtractedBlock] = Field(default_factory=list)
    docling_document_json: Optional[dict[str, Any]] = None

    def get_block(self, block_id: str) -> Optional[ExtractedBlock]:
        for b in self.blocks:
            if b.block_id == block_id:
                return b
        return None

    def full_text(self) -> str:
        ordered = sorted(self.blocks, key=lambda b: b.reading_order)
        return "\n\n".join(b.text.strip() for b in ordered if b.text.strip())


class ExtractionRunSummary(BaseModel):
    """Metadata manifest describing a full extraction run across one or more pages."""

    run_id: str
    engine: str
    engine_version: str
    source_pdf_path: str
    source_pdf_sha256: str
    pages: list[int]
    total_blocks: int
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)


def compute_file_sha256(file_path: Path) -> str:
    """Compute standard SHA-256 hash for a file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def map_docling_label_to_block_type(label: str) -> str:
    """Map Docling's DocItemLabel to a standardized block_type."""
    cleaned = label.lower().replace("docitemlabel.", "").strip()
    if cleaned in ("section_header", "title", "header"):
        return "heading"
    if cleaned in ("page_header", "page_footer", "furniture"):
        return "header_footer"
    if cleaned in ("caption",):
        return "caption"
    if cleaned in ("table",):
        return "table"
    if cleaned in ("picture",):
        return "picture"
    if cleaned in ("list_item",):
        return "ingredient_item"
    if cleaned in ("code",):
        return "code"
    return "paragraph"


def from_docling_dict(
    doc_dict: dict[str, Any],
    run_id: str,
    engine: str,
    engine_version: str,
    source_pdf_path: Path,
    source_pdf_sha256: str,
    target_page_no: int,
    printed_page: Optional[str] = None,
    transforms: Optional[list[str]] = None,
) -> ExtractedPage:
    """Convert a Docling export_to_dict dictionary into an ExtractedPage structure.

    Preserves full doc_dict inside `docling_document_json` for round-trip fidelity.
    """
    page_data = doc_dict.get("pages", {}).get(str(target_page_no), {})
    size = page_data.get("size", {})
    width_pt = float(size.get("width", 416.55))
    height_pt = float(size.get("height", 577.70))

    prov_base = BlockProvenance(
        run_id=run_id,
        engine=engine,
        engine_version=engine_version,
        source_pdf_path=str(source_pdf_path),
        source_pdf_sha256=source_pdf_sha256,
        page_no=target_page_no,
        printed_page=printed_page,
        transforms=transforms or [],
    )

    extracted_blocks: list[ExtractedBlock] = []
    texts = doc_dict.get("texts", [])
    order_idx = 1

    for idx, item in enumerate(texts):
        # Filter for items on the target page
        prov_list = item.get("prov", [])
        if prov_list:
            item_page = prov_list[0].get("page_no")
            if item_page != target_page_no:
                continue

        text_content = item.get("text", "")
        raw_content = item.get("orig", text_content)
        label_raw = str(item.get("label", "paragraph"))
        block_type = map_docling_label_to_block_type(label_raw)

        norm_bbox: Optional[NormalizedBBox] = None
        if prov_list and "bbox" in prov_list[0]:
            b = prov_list[0]["bbox"]
            norm_bbox = NormalizedBBox(
                l=float(b.get("l", 0.0)),
                t=float(b.get("t", 0.0)),
                r=float(b.get("r", 0.0)),
                b=float(b.get("b", 0.0)),
                coord_origin=str(b.get("coord_origin", "BOTTOMLEFT")),
            )

        block_id = f"{run_id}_p{target_page_no:03d}_b{order_idx:02d}"
        extracted_blocks.append(
            ExtractedBlock(
                block_id=block_id,
                page_no=target_page_no,
                reading_order=order_idx,
                block_type=block_type,
                text=text_content,
                raw_text=raw_content,
                bbox=norm_bbox,
                provenance=prov_base,
            )
        )
        order_idx += 1

    return ExtractedPage(
        run_id=run_id,
        page_no=target_page_no,
        printed_page=printed_page,
        width_pt=width_pt,
        height_pt=height_pt,
        blocks=extracted_blocks,
        docling_document_json=doc_dict,
    )


def crop_block_image(
    page_image: Image.Image,
    bbox: NormalizedBBox,
    page_width_pt: float,
    page_height_pt: float,
    dpi: int = 300,
    padding_px: int = 4,
) -> Image.Image:
    """Deterministically crop a region corresponding to a block from a page scan image."""
    box = bbox.to_top_left_pixels(
        page_width_pt=page_width_pt,
        page_height_pt=page_height_pt,
        dpi=dpi,
        padding_px=padding_px,
        image_width_px=page_image.width,
        image_height_px=page_image.height,
    )
    return page_image.crop(box)


def save_extracted_page(page: ExtractedPage, output_dir: Path) -> Path:
    """Save ExtractedPage JSON to disk."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / f"page_{page.page_no:03d}_extracted.json"
    content = page.model_dump_json(indent=2)
    out_file.write_text(content + "\n", encoding="utf-8")
    return out_file


def load_extracted_page(file_path: Path | str) -> ExtractedPage:
    """Load and validate an ExtractedPage from JSON."""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"Extracted page file not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    return ExtractedPage.model_validate(data)


def restore_docling_dict(page: ExtractedPage) -> Optional[dict[str, Any]]:
    """Restore the raw DoclingDocument JSON for complete round-trip fidelity."""
    return page.docling_document_json
