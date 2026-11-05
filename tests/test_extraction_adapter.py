"""Unit tests for extraction adapter, block provenance, and geometry mapping."""

import json
from pathlib import Path
from PIL import Image
import pytest
from pydantic import ValidationError

from src.extraction_adapter import (
    NormalizedBBox,
    BlockProvenance,
    ExtractedBlock,
    ExtractedPage,
    ExtractionRunSummary,
    from_docling_dict,
    crop_block_image,
    save_extracted_page,
    load_extracted_page,
    restore_docling_dict,
    map_docling_label_to_block_type,
)


def test_normalized_bbox_dimensions():
    bbox = NormalizedBBox(l=10.0, t=200.0, r=110.0, b=50.0, coord_origin="BOTTOMLEFT")
    assert bbox.width == 100.0
    assert bbox.height == 150.0


def test_bbox_to_top_left_pixels():
    # Page height = 600 pt, bbox: l=72, r=144, t=500, b=400, BOTTOMLEFT
    # At 72 DPI (scale 1.0):
    # x_min = 72, x_max = 144
    # y_min = (600 - 500) = 100, y_max = (600 - 400) = 200
    bbox = NormalizedBBox(l=72.0, t=500.0, r=144.0, b=400.0, coord_origin="BOTTOMLEFT")
    coords = bbox.to_top_left_pixels(page_width_pt=400.0, page_height_pt=600.0, dpi=72)
    assert coords == (72, 100, 144, 200)

    # Test with TOPLEFT origin
    bbox_top = NormalizedBBox(l=72.0, t=100.0, r=144.0, b=200.0, coord_origin="TOPLEFT")
    coords_top = bbox_top.to_top_left_pixels(page_width_pt=400.0, page_height_pt=600.0, dpi=72)
    assert coords_top == (72, 100, 144, 200)


def test_extracted_block_invalid_reading_order():
    prov = BlockProvenance(
        run_id="run_1",
        engine="test_engine",
        engine_version="1.0",
        source_pdf_path="test.pdf",
        source_pdf_sha256="abc",
        page_no=1,
    )
    with pytest.raises(ValidationError):
        ExtractedBlock(
            block_id="b01",
            page_no=1,
            reading_order=0,
            text="text",
            provenance=prov,
        )


def test_docling_dict_conversion_and_round_trip():
    synthetic_docling_dict = {
        "schema_name": "docling_document",
        "version": "1.0",
        "name": "sample_doc",
        "pages": {
            "25": {"size": {"width": 400.0, "height": 600.0}}
        },
        "texts": [
            {
                "self_ref": "#/texts/0",
                "label": "section_header",
                "text": "Chapter Title",
                "orig": "Chapter Title",
                "prov": [
                    {
                        "page_no": 25,
                        "bbox": {"l": 50.0, "t": 550.0, "r": 200.0, "b": 520.0, "coord_origin": "BOTTOMLEFT"},
                    }
                ],
            },
            {
                "self_ref": "#/texts/1",
                "label": "paragraph",
                "text": "First paragraph text.",
                "orig": "First paragraph text.",
                "prov": [
                    {
                        "page_no": 25,
                        "bbox": {"l": 50.0, "t": 500.0, "r": 350.0, "b": 400.0, "coord_origin": "BOTTOMLEFT"},
                    }
                ],
            },
            {
                "self_ref": "#/texts/2",
                "label": "paragraph",
                "text": "Other page text.",
                "prov": [{"page_no": 26}],
            },
        ],
    }

    page = from_docling_dict(
        doc_dict=synthetic_docling_dict,
        run_id="run_test_01",
        engine="docling_native",
        engine_version="2.146.0",
        source_pdf_path=Path("input/test.pdf"),
        source_pdf_sha256="abc123hash",
        target_page_no=25,
        printed_page="23",
        transforms=["native"],
    )

    assert page.run_id == "run_test_01"
    assert page.page_no == 25
    assert page.printed_page == "23"
    assert len(page.blocks) == 2  # page 26 item excluded
    assert page.blocks[0].block_type == "heading"
    assert page.blocks[1].block_type == "paragraph"
    assert page.blocks[0].reading_order == 1
    assert page.blocks[1].reading_order == 2

    # Verify full round-trip of DoclingDocument JSON
    restored = restore_docling_dict(page)
    assert restored == synthetic_docling_dict


def test_crop_block_image():
    # Create a 400x600 test image (RGB, red background)
    img = Image.new("RGB", (400, 600), color=(255, 0, 0))
    # Draw a blue rectangle in region x=(50, 150), y=(100, 200)
    for x in range(50, 150):
        for y in range(100, 200):
            img.putpixel((x, y), (0, 0, 255))

    # BBox in BOTTOMLEFT at scale 1.0 (72 DPI, page_height=600 pt)
    # y=(100, 200) -> top=500, bottom=400 in BOTTOMLEFT
    bbox = NormalizedBBox(l=50.0, t=500.0, r=150.0, b=400.0, coord_origin="BOTTOMLEFT")
    crop = crop_block_image(
        page_image=img,
        bbox=bbox,
        page_width_pt=400.0,
        page_height_pt=600.0,
        dpi=72,
        padding_px=0,
    )

    assert crop.size == (100, 100)
    # Verify the center pixel is blue
    assert crop.getpixel((50, 50)) == (0, 0, 255)


def test_save_and_load_extracted_page(tmp_path: Path):
    prov = BlockProvenance(
        run_id="run_disk_01",
        engine="docling_easyocr",
        engine_version="2.146.0",
        source_pdf_path="input/ksiazka.pdf",
        source_pdf_sha256="abc",
        page_no=58,
        printed_page="56",
    )
    page = ExtractedPage(
        run_id="run_disk_01",
        page_no=58,
        printed_page="56",
        width_pt=416.55,
        height_pt=577.70,
        blocks=[
            ExtractedBlock(
                block_id="run_disk_01_p058_b01",
                page_no=58,
                reading_order=1,
                block_type="heading",
                text="POLISH PORK SEASONING",
                provenance=prov,
            )
        ],
    )

    out_file = save_extracted_page(page, tmp_path)
    assert out_file.exists()

    loaded = load_extracted_page(out_file)
    assert loaded.run_id == page.run_id
    assert loaded.page_no == 58
    assert len(loaded.blocks) == 1
    assert loaded.blocks[0].text == "POLISH PORK SEASONING"
    assert loaded.blocks[0].provenance.engine == "docling_easyocr"


def test_map_docling_labels():
    assert map_docling_label_to_block_type("section_header") == "heading"
    assert map_docling_label_to_block_type("page_header") == "header_footer"
    assert map_docling_label_to_block_type("caption") == "caption"
    assert map_docling_label_to_block_type("table") == "table"
    assert map_docling_label_to_block_type("list_item") == "ingredient_item"
    assert map_docling_label_to_block_type("unknown_label") == "paragraph"


@pytest.mark.document_integration
def test_real_docling_native_adapter_page_25(tmp_path: Path, text_pdf):
    pdf_path = text_pdf

    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    import importlib.metadata

    opts = PdfPipelineOptions()
    opts.do_ocr = False
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    result = converter.convert(pdf_path, page_range=(1, 1))
    doc_dict = result.document.export_to_dict()

    extracted = from_docling_dict(
        doc_dict=doc_dict,
        run_id="run_native_p25",
        engine="docling_native",
        engine_version=importlib.metadata.version("docling"),
        source_pdf_path=pdf_path,
        source_pdf_sha256="dummy_sha",
        target_page_no=1,
        printed_page="23",
        transforms=["native"],
    )

    assert extracted.page_no == 1
    assert len(extracted.blocks) > 0
    # First block should be Foreword header
    first_block = extracted.blocks[0]
    assert first_block.bbox is not None
    assert first_block.bbox.coord_origin == "BOTTOMLEFT"
    assert "Foreword" in first_block.text

    # Test saving extracted page
    out_file = save_extracted_page(extracted, tmp_path)
    assert out_file.exists()
    reloaded = load_extracted_page(out_file)
    assert len(reloaded.blocks) == len(extracted.blocks)
    assert restore_docling_dict(reloaded) is not None
