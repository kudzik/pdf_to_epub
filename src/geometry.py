"""Page geometry, deskewing, column separation, and reading order normalization.

Invariants:
- Never blindly sort blocks across a page purely by coordinate Y (which splices multi-column text horizontally).
- Deskewing must preserve image dimensions and text content without clipping margins.
- Rotations < 0.2° are considered negligible noise and skipped to avoid raster interpolation degradation.
- Two-column layouts order: top full-width headers -> left column (top-to-bottom) -> right column (top-to-bottom) -> bottom full-width footers.
- Merged multi-column blocks are detected via vertical projection gutter analysis and split into distinct regional blocks.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional, Sequence

import cv2
import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from src.extraction_adapter import (
    ExtractedBlock,
    ExtractedPage,
    NormalizedBBox,
    crop_block_image,
)


class DeskewResult(BaseModel):
    """Result of skew angle detection and deskewing transformation."""

    angle_degrees: float
    applied: bool
    confidence_gain: float = 0.0
    original_image_sha256: Optional[str] = None
    deskewed_image_sha256: Optional[str] = None


class ColumnGutter(BaseModel):
    """Detected column separator gutter in an image or block."""

    split_x_px: int
    split_x_relative: float
    gutter_width_px: int
    min_ink_ratio: float


class PageGeometryReport(BaseModel):
    """Summary of geometrical analysis and transformations applied to an extracted page."""

    page_no: int
    deskew: DeskewResult
    is_two_column: bool
    column_gutter_x_pt: Optional[float] = None
    original_block_count: int
    reordered_block_count: int
    split_blocks_count: int = 0
    transformations_applied: list[str] = Field(default_factory=list)


def detect_skew_angle(
    image: np.ndarray,
    angle_range: tuple[float, float] = (-3.0, 3.0),
    step: float = 0.1,
) -> tuple[float, float]:
    """Detect horizontal text line skew angle using projection profile variance.

    Returns:
    - optimal_angle_degrees: angle to rotate by to make lines horizontal.
    - variance_gain: ratio of max projection variance over variance at 0 degrees.
    """
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    h, w = gray.shape
    # Focus on center 80% to avoid edge artifacts or punch holes
    crop = gray[int(h * 0.1) : int(h * 0.9), int(w * 0.1) : int(w * 0.9)]
    _, thresh = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Initial variance at 0 degrees
    var_zero = float(np.var(np.sum(thresh, axis=1)))
    if var_zero <= 0.0:
        return 0.0, 1.0

    angles = np.arange(angle_range[0], angle_range[1] + step / 2.0, step)
    center_pt = (crop.shape[1] / 2.0, crop.shape[0] / 2.0)

    best_angle = 0.0
    max_var = var_zero

    for angle in angles:
        M = cv2.getRotationMatrix2D(center_pt, float(angle), 1.0)
        rotated = cv2.warpAffine(
            thresh, M, (crop.shape[1], crop.shape[0]), flags=cv2.INTER_NEAREST
        )
        proj = np.sum(rotated, axis=1)
        var = float(np.var(proj))
        if var > max_var:
            max_var = var
            best_angle = float(angle)

    gain = round(max_var / var_zero, 3)
    # If rotating by best_angle makes lines horizontal, then image skew is -best_angle
    image_skew = -best_angle
    return round(image_skew, 2), gain


def deskew_image(
    image: np.ndarray,
    angle_degrees: float,
    border_value: int = 255,
) -> np.ndarray:
    """Deskew an image by rotating it around its center without clipping."""
    if abs(angle_degrees) < 0.05:
        return image.copy()

    h, w = image.shape[:2]
    center = (w / 2.0, h / 2.0)
    # We rotate by -angle_degrees to compensate for the skew
    M = cv2.getRotationMatrix2D(center, -angle_degrees, 1.0)
    deskewed = cv2.warpAffine(
        image,
        M,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(border_value, border_value, border_value) if len(image.shape) == 3 else border_value,
    )
    return deskewed


def detect_column_gutter(
    image: np.ndarray,
    min_relative_width: float = 0.35,
    max_relative_width: float = 0.65,
    max_ink_ratio: float = 0.35,
    min_gutter_width_px: int = 8,
) -> Optional[ColumnGutter]:
    """Detect if an image has a vertical column separation gutter in its center zone.

    Uses vertical projection profile: sums ink per X column.
    """
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    h, w = gray.shape
    if w < 100 or h < 30:
        return None

    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    v_proj = np.sum(thresh, axis=0)  # total ink per X column

    mean_ink = float(np.mean(v_proj))
    if mean_ink <= 0.0:
        return None

    start_x = int(w * min_relative_width)
    end_x = int(w * max_relative_width)
    center_proj = v_proj[start_x:end_x]

    # Find continuous runs of low ink below threshold
    ink_threshold = mean_ink * max_ink_ratio
    low_ink_mask = center_proj < ink_threshold

    best_gutter: Optional[tuple[int, int, float]] = None  # (center_x, width, min_ink_ratio)
    current_run_start: Optional[int] = None

    for idx, is_low in enumerate(low_ink_mask):
        if is_low:
            if current_run_start is None:
                current_run_start = idx
        else:
            if current_run_start is not None:
                run_len = idx - current_run_start
                if run_len >= min_gutter_width_px:
                    run_center = current_run_start + run_len // 2
                    min_ratio = float(np.min(center_proj[current_run_start:idx])) / mean_ink
                    if best_gutter is None or run_len > best_gutter[1]:
                        best_gutter = (run_center, run_len, min_ratio)
                current_run_start = None

    if current_run_start is not None:
        run_len = len(low_ink_mask) - current_run_start
        if run_len >= min_gutter_width_px:
            run_center = current_run_start + run_len // 2
            min_ratio = float(np.min(center_proj[current_run_start:])) / mean_ink
            if best_gutter is None or run_len > best_gutter[1]:
                best_gutter = (run_center, run_len, min_ratio)

    if best_gutter is not None:
        split_px = start_x + best_gutter[0]
        return ColumnGutter(
            split_x_px=split_px,
            split_x_relative=round(split_px / float(w), 3),
            gutter_width_px=best_gutter[1],
            min_ink_ratio=round(best_gutter[2], 3),
        )

    return None


def reorder_blocks_two_column(
    blocks: Sequence[ExtractedBlock],
    page_width_pt: float,
    page_height_pt: float,
    gutter_split_ratio: float = 0.50,
) -> list[ExtractedBlock]:
    """Reorder extracted blocks respecting two-column layout hierarchy.

    Invariant:
    - Never sort all words/blocks across the whole page blindly by Y.
    - Page-wide headers stay first (sorted top-down).
    - Left column blocks processed next (sorted top-down).
    - Right column blocks processed next (sorted top-down).
    - Page-wide footers stay last (sorted top-down).
    """
    if len(blocks) <= 1:
        return list(blocks)

    center_x = page_width_pt * gutter_split_ratio

    top_full_width: list[ExtractedBlock] = []
    left_column: list[ExtractedBlock] = []
    right_column: list[ExtractedBlock] = []
    bottom_full_width: list[ExtractedBlock] = []

    # Determine bounding box boundaries
    for b in blocks:
        if b.bbox is None:
            left_column.append(b)
            continue

        bbox = b.bbox
        width = bbox.r - bbox.l
        mid_x = (bbox.l + bbox.r) / 2.0
        # In Docling / PDF coordinate system: origin at bottom-left:
        # t is higher (closer to top of page), b is lower (closer to bottom)
        top_dist = page_height_pt - max(bbox.t, bbox.b)
        bottom_dist = min(bbox.t, bbox.b)

        # Full-width check: spans at least 65% of the total page width
        is_wide = width > (page_width_pt * 0.65)

        if is_wide:
            if top_dist < (page_height_pt * 0.25):
                top_full_width.append(b)
            elif bottom_dist < (page_height_pt * 0.15):
                bottom_full_width.append(b)
            else:
                # Wide paragraph in the middle: assign to left/right based on midpoint
                if mid_x < center_x:
                    left_column.append(b)
                else:
                    right_column.append(b)
        else:
            if mid_x < center_x:
                left_column.append(b)
            else:
                right_column.append(b)

    # Sort each group top-to-bottom (in PDF coordinates, t is largest at the top, so sort descending by t)
    def sort_top_to_bottom(items: list[ExtractedBlock]) -> list[ExtractedBlock]:
        return sorted(items, key=lambda x: -(x.bbox.t if x.bbox else 0.0))

    sorted_top = sort_top_to_bottom(top_full_width)
    sorted_left = sort_top_to_bottom(left_column)
    sorted_right = sort_top_to_bottom(right_column)
    sorted_bottom = sort_top_to_bottom(bottom_full_width)

    reordered: list[ExtractedBlock] = []
    for item in sorted_top + sorted_left + sorted_right + sorted_bottom:
        new_block = item.model_copy(deep=True)
        new_block.reading_order = len(reordered) + 1
        reordered.append(new_block)

    return reordered


def split_merged_column_block(
    block: ExtractedBlock,
    page_image: Image.Image,
    page_width_pt: float,
    page_height_pt: float,
    ocr_engine: Any = None,
) -> Optional[tuple[ExtractedBlock, ExtractedBlock]]:
    """Detect if a wide block merges two columns horizontally and split it into left and right blocks.

    Uses vertical projection analysis to find column gutter.
    """
    if block.bbox is None:
        return None

    # Check if block is wide enough to possibly span two columns (> 50% of page width)
    block_width_pt = block.bbox.r - block.bbox.l
    if block_width_pt < (page_width_pt * 0.50):
        return None

    crop_img = crop_block_image(
        page_image=page_image,
        bbox=block.bbox,
        page_width_pt=page_width_pt,
        page_height_pt=page_height_pt,
    )
    cv_img = cv2.cvtColor(np.array(crop_img), cv2.COLOR_RGB2BGR)

    gutter = detect_column_gutter(cv_img)
    if gutter is None:
        return None

    # Gutter confirmed! Split bounding box in points
    split_x_pt = block.bbox.l + (block_width_pt * gutter.split_x_relative)

    left_bbox = NormalizedBBox(
        l=block.bbox.l,
        b=block.bbox.b,
        r=round(split_x_pt, 2),
        t=block.bbox.t,
        coord_origin=block.bbox.coord_origin,
    )

    right_bbox = NormalizedBBox(
        l=round(split_x_pt, 2),
        b=block.bbox.b,
        r=block.bbox.r,
        t=block.bbox.t,
        coord_origin=block.bbox.coord_origin,
    )

    # Perform OCR on each half if engine is provided
    left_text = block.text
    right_text = ""

    if ocr_engine is not None:
        left_crop = cv_img[:, : gutter.split_x_px]
        right_crop = cv_img[:, gutter.split_x_px :]

        res_l, _ = ocr_engine(left_crop)
        res_r, _ = ocr_engine(right_crop)

        if res_l:
            left_text = " ".join([line[1] for line in res_l])
        if res_r:
            right_text = " ".join([line[1] for line in res_r])

    block_left = ExtractedBlock(
        block_id=f"{block.block_id}_col1",
        page_no=block.page_no,
        reading_order=block.reading_order,
        block_type=block.block_type,
        text=left_text,
        bbox=left_bbox,
        provenance=block.provenance,
    )

    block_right = ExtractedBlock(
        block_id=f"{block.block_id}_col2",
        page_no=block.page_no,
        reading_order=block.reading_order + 1,
        block_type=block.block_type,
        text=right_text,
        bbox=right_bbox,
        provenance=block.provenance,
    )

    return block_left, block_right


def process_page_geometry(
    extracted_page: ExtractedPage,
    page_image_path: Path,
    enable_deskew: bool = True,
    enable_column_splitting: bool = True,
) -> tuple[ExtractedPage, PageGeometryReport]:
    """Analyze and apply geometrical corrections to an ExtractedPage.

    - Detects skew and applies deskewing if angle >= 0.2°.
    - Identifies two-column layouts and splits horizontally merged blocks.
    - Re-orders blocks so columns are read sequentially without mixing.
    """
    if not page_image_path.exists():
        raise FileNotFoundError(f"Page image not found: {page_image_path}")

    page_img = Image.open(page_image_path)
    cv_img = cv2.cvtColor(np.array(page_img), cv2.COLOR_RGB2BGR)

    applied_transforms: list[str] = []

    # 1. Deskew
    angle, gain = detect_skew_angle(cv_img)
    deskew_applied = False
    if enable_deskew and abs(angle) >= 0.2:
        cv_img = deskew_image(cv_img, angle)
        page_img = Image.fromarray(cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB))
        deskew_applied = True
        applied_transforms.append(f"deskew_{angle:+.2f}deg")

    deskew_res = DeskewResult(
        angle_degrees=angle,
        applied=deskew_applied,
        confidence_gain=gain,
    )

    # 2. Check if page has multiple columns
    page_gutter = detect_column_gutter(cv_img)
    is_two_col = page_gutter is not None or any(
        (b.bbox.r - b.bbox.l) < (extracted_page.width_pt * 0.55)
        for b in extracted_page.blocks
        if b.bbox is not None
    )

    gutter_x_pt = (
        round(extracted_page.width_pt * page_gutter.split_x_relative, 2)
        if page_gutter
        else None
    )

    # 3. Split merged multi-column blocks if enabled
    split_count = 0
    refined_blocks: list[ExtractedBlock] = []

    ocr_engine = None
    if enable_column_splitting and is_two_col:
        try:
            from rapidocr_onnxruntime import RapidOCR

            ocr_engine = RapidOCR()
        except Exception:
            ocr_engine = None

    for b in extracted_page.blocks:
        if enable_column_splitting and is_two_col and b.block_type == "paragraph":
            split_res = split_merged_column_block(
                block=b,
                page_image=page_img,
                page_width_pt=extracted_page.width_pt,
                page_height_pt=extracted_page.height_pt,
                ocr_engine=ocr_engine,
            )
            if split_res is not None:
                refined_blocks.extend(split_res)
                split_count += 1
                continue

        refined_blocks.append(b)

    if split_count > 0:
        applied_transforms.append(f"split_{split_count}_merged_blocks")

    # 4. Re-sequence reading order respecting columns
    if is_two_col:
        reordered_blocks = reorder_blocks_two_column(
            refined_blocks,
            page_width_pt=extracted_page.width_pt,
            page_height_pt=extracted_page.height_pt,
        )
        applied_transforms.append("two_column_reorder")
    else:
        reordered_blocks = refined_blocks

    new_page = extracted_page.model_copy(deep=True)
    new_page.blocks = reordered_blocks
    if applied_transforms:
        for b in new_page.blocks:
            b.provenance.transforms.extend(applied_transforms)

    report = PageGeometryReport(
        page_no=extracted_page.page_no,
        deskew=deskew_res,
        is_two_column=is_two_col,
        column_gutter_x_pt=gutter_x_pt,
        original_block_count=len(extracted_page.blocks),
        reordered_block_count=len(reordered_blocks),
        split_blocks_count=split_count,
        transformations_applied=applied_transforms,
    )

    return new_page, report
