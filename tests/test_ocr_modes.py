"""Unit tests for explicit OCR modes, candidate export isolation, and production guards."""

import json
from pathlib import Path
import pytest

from src.ocr_modes import (
    OcrEngineMode,
    ProductionOverwriteError,
    CandidateRunConfig,
    CandidateRunManifest,
    ensure_candidate_does_not_overwrite_production,
    get_mode_converter,
    run_candidate_conversion,
    load_candidate_manifest,
)
from docling.datamodel.pipeline_options import OcrMode


def test_production_overwrite_guard():
    # Attempting to target production Markdown raises error
    prod_md = Path("output/markdown/ksiazka.md")
    with pytest.raises(ProductionOverwriteError, match="Zablokowano próbę bezpośredniego zapisu"):
        ensure_candidate_does_not_overwrite_production(prod_md)

    # Attempting to target production directory raises error
    prod_dir = Path("output/markdown")
    with pytest.raises(ProductionOverwriteError, match="Zablokowano próbę zapisu kandydata do katalogu produkcyjnego"):
        ensure_candidate_does_not_overwrite_production(prod_dir)

    # Candidate directories are safe and permitted
    ensure_candidate_does_not_overwrite_production(Path("output/candidates/test_run"))
    ensure_candidate_does_not_overwrite_production(Path("output/benchmarks/test_run"))


def test_ocr_engine_modes():
    assert OcrEngineMode.NATIVE.value == "native"
    assert OcrEngineMode.FULL_PAGE.value == "full_page"
    assert OcrEngineMode.LAYOUT_REGIONS.value == "layout_regions"
    assert OcrEngineMode.RAPID_OCR.value == "rapidocr"


def test_get_mode_converter_native():
    converter, summary = get_mode_converter(OcrEngineMode.NATIVE, ["pl", "en"])
    assert summary["mode"] == "native"
    assert summary["do_ocr"] is False


def test_get_mode_converter_full_page():
    converter, summary = get_mode_converter(OcrEngineMode.FULL_PAGE, ["pl", "en"])
    assert summary["mode"] == "full_page"
    assert summary["do_ocr"] is True
    assert summary["ocr_mode"] == "FULL_PAGE"


def test_get_mode_converter_rapidocr():
    converter, summary = get_mode_converter(OcrEngineMode.RAPID_OCR, ["pl", "en"])
    assert summary["mode"] == "rapidocr"
    assert summary["do_ocr"] is True
    assert summary["ocr_engine"] == "RapidOCR"
    assert summary["ocr_mode"] == "FULL_PAGE"
    assert summary["rapid_lang"] == "pl"


@pytest.mark.document_integration
def test_candidate_run_conversion_native(tmp_path: Path, text_pdf):
    pdf_path = text_pdf

    cand_base = tmp_path / "candidates"
    manifest = run_candidate_conversion(
        pdf_path=pdf_path,
        pages=[1],
        mode=OcrEngineMode.NATIVE,
        languages=["pl", "en"],
        output_base_dir=cand_base,
        run_id="test_run_native_p25",
    )

    assert manifest.config.run_id == "test_run_native_p25"
    assert manifest.config.mode == OcrEngineMode.NATIVE
    assert manifest.total_pages == 1
    assert manifest.pages_converted == [1]
    assert manifest.total_blocks > 0
    assert manifest.is_production_truth is False
    assert manifest.candidate_markdown_sha256 is not None
    assert manifest.duration_seconds is not None and manifest.duration_seconds >= 0.0
    assert 1 in manifest.page_durations and manifest.page_durations[1] >= 0.0

    cand_dir = cand_base / "test_run_native_p25"
    manifest_file = cand_dir / "manifest.json"
    assert manifest_file.exists()

    loaded_manifest = load_candidate_manifest(manifest_file)
    assert loaded_manifest.config.run_id == manifest.config.run_id
    assert loaded_manifest.candidate_markdown_sha256 == manifest.candidate_markdown_sha256

    # Verify extracted page sidecar exists
    extracted_page_file = cand_dir / "pages" / "page_001_extracted.json"
    assert extracted_page_file.exists()

    # Verify candidate markdown file exists
    cand_md = cand_dir / f"{pdf_path.stem}.md"
    assert cand_md.exists()


@pytest.mark.document_integration
def test_candidate_run_conversion_non_contiguous(tmp_path: Path, text_pdf):
    pdf_path = text_pdf

    cand_base = tmp_path / "candidates"
    manifest = run_candidate_conversion(
        pdf_path=pdf_path,
        pages=[1, 3],
        mode=OcrEngineMode.NATIVE,
        languages=["pl", "en"],
        output_base_dir=cand_base,
        run_id="test_run_non_contiguous",
    )

    assert manifest.total_pages == 2
    assert manifest.pages_converted == [1, 3]
    assert 1 in manifest.page_durations
    assert 3 in manifest.page_durations
    assert manifest.duration_seconds is not None and manifest.duration_seconds > 0.0

    cand_dir = cand_base / "test_run_non_contiguous"
    assert (cand_dir / "pages" / "page_001_extracted.json").exists()
    assert (cand_dir / "pages" / "page_003_extracted.json").exists()
    assert (cand_dir / f"{pdf_path.stem}.md").exists()


def test_additional_engine_evaluation_d07():
    """Verify architectural Decision D07:

    PaddleOCR/PP-Structure is rejected in favor of RapidOCR ONNX + Geometry + VLM.
    Ensure that RapidOCR is directly accessible without requiring full paddlepaddle,
    and that OcrEngineMode supports all benchmarked engines.
    """
    import importlib.util

    # paddleocr should not be a required dependency in pyproject.toml
    # RapidOCR provides PP-OCRv4 models via onnxruntime without paddle dependencies
    paddle_spec = importlib.util.find_spec("paddle")
    assert paddle_spec is None, "PaddlePaddle runtime should not be installed; RapidOCR ONNX is used instead."

    rapid_spec = importlib.util.find_spec("rapidocr_onnxruntime")
    assert rapid_spec is not None, "RapidOCR ONNX must be available."

    # Validate engine mode definitions
    modes = [m.value for m in OcrEngineMode]
    assert "rapidocr" in modes
    assert "native" in modes
    assert "full_page" in modes

