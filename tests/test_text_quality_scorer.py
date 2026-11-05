"""Tests for intelligent text layer quality evaluation and OCR auto-detection."""

from pathlib import Path
import pypdfium2 as pdfium
import pytest
from src.pipeline import evaluate_text_layer_quality, has_text_layer, get_converter


def test_detects_corrupted_text_layer(make_text_pdf):
    pdf = make_text_pdf([[
        "Recipes and traditional dishes made with fresh herbs and vegetables.",
        "Add the food^stuff to the warm water and mix the dough~mix carefully.",
        "Use flour and salt to make a loaf of bread for the family dinner.",
        "The recipe describes several useful methods of cooking vegetables.",
    ]])
    result = evaluate_text_layer_quality(str(pdf))
    assert result["has_text"] is True
    assert result["is_clean"] is False
    assert result["recommended_ocr"] is True
    assert result["total_garbage"] > 0
    assert has_text_layer(str(pdf)) is False


def test_clean_vector_pdf_quality(text_pdf):
    result = evaluate_text_layer_quality(str(text_pdf))
    assert result["has_text"] is True
    assert result["is_clean"] is True
    assert result["recommended_ocr"] is False
    assert has_text_layer(str(text_pdf)) is True


def test_get_converter_supports_rapidocr():
    """Verify get_converter initializes with RapidOCR options properly."""
    conv = get_converter(do_ocr=True, langs=["pl", "en"], engine="rapidocr")
    assert conv is not None


def test_get_converter_supports_easyocr():
    """Verify get_converter initializes with EasyOCR options properly."""
    conv = get_converter(do_ocr=True, langs=["pl", "en"], engine="easyocr")
    assert conv is not None

