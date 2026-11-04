"""Tests for intelligent text layer quality evaluation and OCR auto-detection."""

from pathlib import Path
import pypdfium2 as pdfium
import pytest
from src.pipeline import evaluate_text_layer_quality, has_text_layer, get_converter


def test_ksiazka_pdf_detects_corrupted_legacy_ocr():
    """Verify that input/ksiazka.pdf with 20-year-old embedded OCR is flagged as not clean, enforcing OCR."""
    pdf_path = Path("input/ksiazka.pdf")
    if not pdf_path.exists():
        pytest.skip("input/ksiazka.pdf not present in workspace")

    res = evaluate_text_layer_quality(str(pdf_path))
    assert res["has_text"] is True
    assert res["is_clean"] is False, "Legacy scanned book should not be considered clean digital text"
    assert res["recommended_ocr"] is True
    assert res["total_garbage"] > 0
    assert 0.0 < res["quality_score"] < 0.95

    # has_text_layer should return False so OCR is activated instead of dumping garbage
    assert has_text_layer(str(pdf_path)) is False


def test_clean_vector_pdf_quality(tmp_path: Path):
    """Verify that a synthetic PDF with clean digital text is recognized as high quality (no OCR needed)."""
    # Create a tiny 1-page PDF using pypdfium2 or text
    clean_pdf_path = tmp_path / "clean_digital.pdf"
    
    # We can create a simple PDF document using reportlab or pypdfium2
    try:
        from reportlab.pdfgen import canvas
        c = canvas.Canvas(str(clean_pdf_path))
        c.drawString(100, 750, "Chapter 1: The Foundations of Polish Cookery")
        c.drawString(100, 730, "Polish cuisine has evolved over centuries through diverse regional traditions.")
        c.drawString(100, 710, "Ingredients such as dill, parsley, mushrooms, and sourdough form the basis of many soups.")
        c.save()

        res = evaluate_text_layer_quality(str(clean_pdf_path))
        assert res["has_text"] is True
        assert res["is_clean"] is True
        assert res["recommended_ocr"] is False
        assert res["quality_score"] >= 0.90
        assert has_text_layer(str(clean_pdf_path)) is True
    except ImportError:
        pytest.skip("reportlab not installed, skipping synthetic vector PDF test")


def test_get_converter_supports_rapidocr():
    """Verify get_converter initializes with RapidOCR options properly."""
    conv = get_converter(do_ocr=True, langs=["pl", "en"], engine="rapidocr")
    assert conv is not None


def test_get_converter_supports_easyocr():
    """Verify get_converter initializes with EasyOCR options properly."""
    conv = get_converter(do_ocr=True, langs=["pl", "en"], engine="easyocr")
    assert conv is not None

