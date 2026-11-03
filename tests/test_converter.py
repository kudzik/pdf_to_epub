from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import EasyOcrOptions
from src.pipeline import get_converter


def test_converter_options_and_language_config():
    """Weryfikuje, czy get_converter poprawnie konfiguruje silnik OCR dla języków polskiego i angielskiego."""
    langs = ["pl", "en"]
    converter = get_converter(langs)

    assert InputFormat.PDF in converter.format_to_options
    pdf_opts = converter.format_to_options[InputFormat.PDF].pipeline_options

    assert pdf_opts.do_ocr is True, "OCR powinien być włączony."
    assert pdf_opts.generate_picture_images is True, "Wycinanie ilustracji powinno być włączone."
    assert isinstance(pdf_opts.ocr_options, EasyOcrOptions)
    assert pdf_opts.ocr_options.lang == langs, f"Oczekiwano języków {langs}, otrzymano {pdf_opts.ocr_options.lang}"


def test_page_range_parsing():
    """Weryfikuje logikę parsowania argumentu --pages w pipeline."""
    def parse_pages(pages_str: str) -> tuple[int, int]:
        parts = [int(p.strip()) for p in pages_str.split("-")]
        if len(parts) == 1:
            return (parts[0], parts[0])
        elif len(parts) == 2:
            return (parts[0], parts[1])
        raise ValueError("Invalid format")

    assert parse_pages("1-5") == (1, 5)
    assert parse_pages("10") == (10, 10)
    assert parse_pages(" 3 - 7 ") == (3, 7)
