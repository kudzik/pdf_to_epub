"""Unit tests for geometry analysis, deskewing, column separation, and reading order."""

from pathlib import Path
import cv2
import numpy as np
from PIL import Image
import pytest

from src.extraction_adapter import (
    BlockProvenance,
    ExtractedBlock,
    ExtractedPage,
    NormalizedBBox,
)
from src.geometry import (
    DeskewResult,
    ColumnGutter,
    PageGeometryReport,
    detect_skew_angle,
    deskew_image,
    detect_column_gutter,
    reorder_blocks_two_column,
    split_merged_column_block,
    process_page_geometry,
)
from src.ground_truth import render_page_image


def test_detect_skew_angle_and_deskew():
    # Create synthetic image with horizontal stripes
    h, w = 600, 800
    img = np.ones((h, w), dtype=np.uint8) * 255
    for y in range(60, h - 60, 40):
        img[y : y + 10, 60 : w - 60] = 0

    # Deliberately rotate by +1.6 degrees
    true_angle = 1.6
    M = cv2.getRotationMatrix2D((w / 2, h / 2), true_angle, 1.0)
    rotated = cv2.warpAffine(img, M, (w, h), borderValue=255)

    detected_angle, gain = detect_skew_angle(rotated)
    assert abs(detected_angle - true_angle) <= 0.2
    assert gain > 1.0

    # Deskew should correct it
    corrected = deskew_image(rotated, detected_angle)
    angle_after, _ = detect_skew_angle(corrected)
    assert abs(angle_after) <= 0.2


def test_detect_column_gutter_synthetic_two_columns():
    h, w = 400, 800
    img = np.ones((h, w), dtype=np.uint8) * 255

    # Left column: x from 50 to 350
    for y in range(40, h - 40, 30):
        img[y : y + 10, 50:350] = 0

    # Right column: x from 450 to 750
    for y in range(40, h - 40, 30):
        img[y : y + 10, 450:750] = 0

    # White space / gutter is between x=350 and x=450 (center 400)
    gutter = detect_column_gutter(img)
    assert gutter is not None
    assert 380 <= gutter.split_x_px <= 420
    assert 0.45 <= gutter.split_x_relative <= 0.55
    assert gutter.min_ink_ratio == 0.0


def test_detect_column_gutter_single_column_no_split():
    h, w = 400, 800
    img = np.ones((h, w), dtype=np.uint8) * 255

    # Continuous single-column prose spanning across full width (50 to 750)
    for y in range(40, h - 40, 25):
        img[y : y + 10, 50:750] = 0

    gutter = detect_column_gutter(img)
    assert gutter is None


def test_reorder_blocks_two_column():
    """Verify that multi-column reordering prevents horizontal line interleaving."""
    page_w = 400.0
    page_h = 600.0

    # Synthetic block set mimicking a 2-column recipe page:
    # 1. Header (full width at top)
    # 2. Left col block 1 (top of left col)
    # 3. Left col block 2 (bottom of left col)
    # 4. Right col block 1 (top of right col, Y between left 1 and left 2)
    # 5. Right col block 2 (bottom of right col)
    # 6. Footer (full width at bottom)

    prov = BlockProvenance(
        run_id="test_run",
        page_no=1,
        source_pdf_path="dummy.pdf",
        source_pdf_sha256="abc",
        engine="test",
        engine_version="1.0",
    )

    header = ExtractedBlock(
        block_id="b_hdr",
        page_no=1,
        reading_order=1,
        block_type="header_footer",
        text="Chapter 1: Polish Recipes",
        bbox=NormalizedBBox(l=40, b=550, r=360, t=580),
        provenance=prov,
    )
    left1 = ExtractedBlock(
        block_id="b_l1",
        page_no=1,
        reading_order=2,
        block_type="paragraph",
        text="Left column recipe ingredient 1",
        bbox=NormalizedBBox(l=40, b=400, r=180, t=500),
        provenance=prov,
    )
    right1 = ExtractedBlock(
        block_id="b_r1",
        page_no=1,
        reading_order=3,
        block_type="paragraph",
        text="Right column recipe ingredient 1",
        bbox=NormalizedBBox(l=220, b=410, r=360, t=490),
        provenance=prov,
    )
    left2 = ExtractedBlock(
        block_id="b_l2",
        page_no=1,
        reading_order=4,
        block_type="paragraph",
        text="Left column recipe ingredient 2",
        bbox=NormalizedBBox(l=40, b=250, r=180, t=350),
        provenance=prov,
    )
    right2 = ExtractedBlock(
        block_id="b_r2",
        page_no=1,
        reading_order=5,
        block_type="paragraph",
        text="Right column recipe ingredient 2",
        bbox=NormalizedBBox(l=220, b=240, r=360, t=340),
        provenance=prov,
    )
    footer = ExtractedBlock(
        block_id="b_ftr",
        page_no=1,
        reading_order=6,
        block_type="header_footer",
        text="Page 42",
        bbox=NormalizedBBox(l=40, b=20, r=360, t=45),
        provenance=prov,
    )

    # In bad Y-based ordering: header -> left1 -> right1 -> left2 -> right2 -> footer (mixed!)
    # In proper 2-column ordering: header -> left1 -> left2 -> right1 -> right2 -> footer
    input_blocks = [header, left1, right1, left2, right2, footer]

    reordered = reorder_blocks_two_column(input_blocks, page_w, page_h)

    block_ids = [b.block_id for b in reordered]
    assert block_ids == ["b_hdr", "b_l1", "b_l2", "b_r1", "b_r2", "b_ftr"]
    # Check reading order numbering is 1..6
    assert [b.reading_order for b in reordered] == [1, 2, 3, 4, 5, 6]


def test_split_merged_column_block():
    page_w = 400.0
    page_h = 500.0

    # Create synthetic page image with 2 columns
    img_h, img_w = int(page_h * 300 / 72), int(page_w * 300 / 72)
    img = np.ones((img_h, img_w, 3), dtype=np.uint8) * 255

    # Draw ink in left and right sections
    img[400:800, 200:600] = 0
    img[400:800, 1000:1400] = 0
    pil_img = Image.fromarray(img)

    prov = BlockProvenance(
        run_id="test_run",
        page_no=1,
        source_pdf_path="dummy.pdf",
        source_pdf_sha256="abc",
        engine="test",
        engine_version="1.0",
    )

    merged_block = ExtractedBlock(
        block_id="b_merged",
        page_no=1,
        reading_order=1,
        block_type="paragraph",
        text="Merged text across two columns",
        bbox=NormalizedBBox(l=40, b=200, r=360, t=350),
        provenance=prov,
    )

    split_res = split_merged_column_block(
        block=merged_block,
        page_image=pil_img,
        page_width_pt=page_w,
        page_height_pt=page_h,
    )

    assert split_res is not None
    left_b, right_b = split_res
    assert left_b.block_id == "b_merged_col1"
    assert right_b.block_id == "b_merged_col2"
    assert left_b.bbox.l == merged_block.bbox.l
    assert right_b.bbox.r == merged_block.bbox.r
    assert left_b.bbox.r < right_b.bbox.l or left_b.bbox.r == right_b.bbox.l


def test_process_page_geometry_real_pdf_page(tmp_path: Path, text_pdf):
    pdf_path = text_pdf

    img_path, sha = render_page_image(pdf_path, 1, tmp_path / "images", dpi=300)

    # Construct ExtractedPage for page 58
    prov = BlockProvenance(
        run_id="test_geo",
        page_no=1,
        source_pdf_path=str(pdf_path),
        source_pdf_sha256=sha,
        engine="test",
        engine_version="1.0",
    )
    dummy_block = ExtractedBlock(
        block_id="p58_b01",
        page_no=1,
        reading_order=1,
        block_type="paragraph",
        text="Test block on page 58",
        bbox=NormalizedBBox(l=43.0, b=416.0, r=377.0, t=503.0),
        provenance=prov,
    )
    ext_page = ExtractedPage(
        run_id="test_geo",
        page_no=1,
        width_pt=420.0,
        height_pt=595.0,
        blocks=[dummy_block],
        provenance=prov,
    )

    new_page, report = process_page_geometry(ext_page, img_path)

    assert report.page_no == 1
    assert report.deskew is not None
    assert isinstance(report.deskew.angle_degrees, float)
    assert report.original_block_count == 1
    assert len(new_page.blocks) >= 1
