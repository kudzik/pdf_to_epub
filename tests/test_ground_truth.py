"""Unit tests for ground truth data models, validation, and fixture consistency."""

import json
from pathlib import Path
import pytest
from pydantic import ValidationError

from src.ground_truth import (
    BBox,
    CriticalQuantity,
    UnresolvedGlyph,
    GroundTruthBlock,
    GroundTruthTable,
    GroundTruthTableCell,
    PageGroundTruth,
    save_page_ground_truth,
    load_page_ground_truth,
    load_all_ground_truths,
    validate_ground_truth_against_manifest,
    compute_file_sha256,
    render_page_image,
)


def test_bbox_dimensions():
    bbox = BBox(left=10.0, top=20.0, right=110.0, bottom=170.0)
    assert bbox.width == 100.0
    assert bbox.height == 150.0


def test_critical_quantity_validation():
    cq = CriticalQuantity(
        value="1/2",
        unit="tsp",
        ingredient_or_context="baking soda",
        raw_token="1/2 tsp",
    )
    assert cq.value == "1/2"
    assert cq.unit == "tsp"
    data = cq.model_dump()
    assert data["value"] == "1/2"


def test_unresolved_glyph_validation():
    ug = UnresolvedGlyph(
        glyph_id="g01",
        best_reading="°",
        notes="superscript circle",
        confidence_assessment="uncertain",
    )
    assert ug.best_reading == "°"


def test_ground_truth_block_invalid_reading_order():
    with pytest.raises(ValidationError):
        GroundTruthBlock(
            block_id="b01",
            reading_order=0,  # invalid, must be >= 1
            block_type="paragraph",
            text="sample text",
        )


def test_page_ground_truth_integrity_duplicate_ids():
    gt = PageGroundTruth(
        page_no=1,
        layout_type="recipe_single_column",
        source_image_path="dummy.png",
        source_image_sha256="abc",
        blocks=[
            GroundTruthBlock(block_id="b01", reading_order=1, block_type="paragraph", text="p1"),
            GroundTruthBlock(block_id="b01", reading_order=2, block_type="paragraph", text="p2"),
        ],
    )
    with pytest.raises(ValueError, match="Duplicate block_id 'b01'"):
        gt.validate_integrity()


def test_page_ground_truth_integrity_duplicate_order():
    gt = PageGroundTruth(
        page_no=1,
        layout_type="recipe_single_column",
        source_image_path="dummy.png",
        source_image_sha256="abc",
        blocks=[
            GroundTruthBlock(block_id="b01", reading_order=1, block_type="paragraph", text="p1"),
            GroundTruthBlock(block_id="b02", reading_order=1, block_type="paragraph", text="p2"),
        ],
    )
    with pytest.raises(ValueError, match="Duplicate reading_order 1"):
        gt.validate_integrity()


def test_page_ground_truth_full_text():
    gt = PageGroundTruth(
        page_no=1,
        layout_type="recipe_single_column",
        source_image_path="dummy.png",
        source_image_sha256="abc",
        blocks=[
            GroundTruthBlock(block_id="h00", reading_order=1, block_type="header_footer", text="Page Header 1"),
            GroundTruthBlock(block_id="h01", reading_order=2, block_type="heading", text="TITLE"),
            GroundTruthBlock(block_id="p01", reading_order=3, block_type="paragraph", text="First line.\nSecond line."),
        ],
    )

    # By default, exclude header_footer
    text = gt.full_text()
    assert "Page Header 1" not in text
    assert "TITLE" in text
    assert "First line.\nSecond line." in text

    # With header_footer included
    text_with_hf = gt.full_text(include_header_footer=True)
    assert "Page Header 1" in text_with_hf

    # Normalized text
    norm_text = gt.full_text(normalize=True)
    assert "First line. Second line." in norm_text


def test_round_trip_save_and_load(tmp_path: Path):
    gt = PageGroundTruth(
        page_no=58,
        printed_page_no=56,
        layout_type="recipe_single_column",
        source_image_path="benchmarks/fixtures/images/page_058.png",
        source_image_sha256="abc123sha",
        blocks=[
            GroundTruthBlock(
                block_id="p58_h01",
                reading_order=1,
                block_type="heading",
                text="POLISH MILD HERB PEPPER",
            ),
            GroundTruthBlock(
                block_id="p58_p01",
                reading_order=2,
                block_type="ingredient_item",
                text="2 t. savory",
                critical_quantities=[
                    CriticalQuantity(value="2", unit="t.", ingredient_or_context="savory", raw_token="2 t. savory")
                ],
            ),
        ],
        tables=[
            GroundTruthTable(
                table_id="p58_t01",
                reading_order=3,
                num_rows=1,
                num_cols=1,
                cells=[GroundTruthTableCell(row=1, col=1, text="cell")],
            )
        ],
    )

    file_path = tmp_path / "page_058.json"
    save_page_ground_truth(gt, file_path)

    loaded = load_page_ground_truth(file_path)
    assert loaded.page_no == 58
    assert loaded.printed_page_no == 56
    assert len(loaded.blocks) == 2
    assert len(loaded.tables) == 1
    assert loaded.blocks[1].critical_quantities[0].value == "2"


def test_validate_ground_truth_against_manifest(tmp_path: Path):
    manifest_path = Path("benchmarks/fixtures/test_pages_manifest.json")
    if not manifest_path.exists():
        pytest.skip("Canonical manifest file not present.")

    # Valid GT for page 58
    gt_valid = PageGroundTruth(
        page_no=58,
        printed_page_no=56,
        layout_type="recipe_single_column",
        source_image_path="benchmarks/fixtures/images/page_058.png",
        source_image_sha256="abc",
    )
    validate_ground_truth_against_manifest(gt_valid, manifest_path)

    # Missing page in manifest
    gt_missing = PageGroundTruth(
        page_no=999,
        layout_type="recipe_single_column",
        source_image_path="dummy.png",
        source_image_sha256="abc",
    )
    with pytest.raises(ValueError, match="not present in manifest"):
        validate_ground_truth_against_manifest(gt_missing, manifest_path)

    # Layout type mismatch
    gt_wrong_layout = PageGroundTruth(
        page_no=58,
        printed_page_no=56,
        layout_type="index_multi_column",
        source_image_path="dummy.png",
        source_image_sha256="abc",
    )
    with pytest.raises(ValueError, match="Layout type mismatch"):
        validate_ground_truth_against_manifest(gt_wrong_layout, manifest_path)

    # Printed page mismatch
    gt_wrong_printed = PageGroundTruth(
        page_no=58,
        printed_page_no=99,
        layout_type="recipe_single_column",
        source_image_path="dummy.png",
        source_image_sha256="abc",
    )
    with pytest.raises(ValueError, match="Printed page mismatch"):
        validate_ground_truth_against_manifest(gt_wrong_printed, manifest_path)


def test_existing_ground_truth_fixtures_valid():
    fixtures_dir = Path("benchmarks/fixtures/ground_truth")
    manifest_path = Path("benchmarks/fixtures/test_pages_manifest.json")

    fixtures = load_all_ground_truths(fixtures_dir)
    assert len(fixtures) >= 5, f"Expected at least 5 ground truth fixtures, found {len(fixtures)}"

    for pno, gt in fixtures.items():
        gt.validate_integrity()
        validate_ground_truth_against_manifest(gt, manifest_path)
        assert gt.source_image_sha256, f"Missing sha256 for page {pno}"
        assert len(gt.blocks) > 0, f"Page {pno} has no blocks"


def test_render_page_image_negative():
    with pytest.raises(FileNotFoundError):
        render_page_image(Path("non_existent.pdf"), 1, Path("/tmp"))

    with pytest.raises(ValueError, match="page_no must be >= 1"):
        render_page_image(Path("input/ksiazka.pdf"), 0, Path("/tmp"))
