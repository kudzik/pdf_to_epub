"""Unit tests for controlled VLM transcription, prompt isolation, capability probing, and benchmark reporting."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from PIL import Image

from src.vlm_transcription import (
    VlmModelCapability,
    VlmTranscriptionResult,
    VlmCropComparison,
    VlmBenchmarkReport,
    clean_vlm_transcription_text,
    detect_vlm_hallucination,
    evaluate_quantities_preserved,
    probe_ollama_vlm_capability,
    transcribe_crop_vlm,
    save_vlm_benchmark_report,
)
from src.ocr_modes import ProductionOverwriteError
from src.ground_truth import CriticalQuantity


def test_clean_vlm_transcription_text():
    # Empty string
    assert clean_vlm_transcription_text("") == ""

    # Fenced code block
    fenced = "```\nSome transcribed text\n```"
    assert clean_vlm_transcription_text(fenced) == "Some transcribed text"

    # Preambles
    preamble = "The image features a white piece of paper with black text that reads: Polish mild herb pepper"
    assert clean_vlm_transcription_text(preamble) == "Polish mild herb pepper"

    # Quotes
    quoted = '"1 t. savory"'
    assert clean_vlm_transcription_text(quoted) == "1 t. savory"


def test_detect_vlm_hallucination():
    gt = "POLISH MILD HERB PEPPER"
    # Hallucinating person/face
    hallucinated = "The image contains a portion of a person's face and text saying something else"
    assert detect_vlm_hallucination(hallucinated, gt) is True

    # Hallucinating invented recipe
    invented = "This seems to be a dry rub for fish with black pepper shaker."
    assert detect_vlm_hallucination(invented, gt) is True

    # Verbatim match
    exact = "POLISH MILD HERB PEPPER"
    assert detect_vlm_hallucination(exact, gt) is False


def test_evaluate_quantities_preserved():
    crit_quants = [
        CriticalQuantity(value="1", unit="t.", ingredient_or_context="savory", raw_token="1 t. savory"),
        CriticalQuantity(value="2", unit="t.", ingredient_or_context="marjoram", raw_token="2 t. marjoram"),
    ]

    # Valid preservation
    valid_text = "Combine: 1 t. savory, 2 t. marjoram"
    assert evaluate_quantities_preserved(valid_text, crit_quants) is True

    # Missing quantity value
    corrupted_val = "Combine: t. savory, 2 t. marjoram"
    assert evaluate_quantities_preserved(corrupted_val, crit_quants) is False

    # Missing unit
    corrupted_unit = "Combine: 1 savory, 2 t. marjoram"
    assert evaluate_quantities_preserved(corrupted_unit, crit_quants) is False


def test_vlm_production_guard(tmp_path: Path):
    report = VlmBenchmarkReport(
        run_id="test_run",
        model="moondream:latest",
        endpoint="http://127.0.0.1:11434",
        total_crops=1,
        vlm_mean_cer=0.5,
        vlm_mean_wer=0.5,
        ocr_mean_cer=0.1,
        ocr_mean_wer=0.1,
        vlm_mean_duration_s=0.2,
        hallucination_rate=0.0,
        architectural_decision="REJECT",
    )

    # Attempting to save to production Markdown must fail
    with pytest.raises(ProductionOverwriteError):
        save_vlm_benchmark_report(report, Path("output/markdown/ksiazka.md"))

    # Saving to candidate/benchmark directory must succeed
    out_dir = tmp_path / "benchmarks" / "vlm_test"
    json_path, md_path = save_vlm_benchmark_report(report, out_dir)
    assert json_path.exists()
    assert md_path.exists()

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["run_id"] == "test_run"
    assert data["model"] == "moondream:latest"


def test_probe_ollama_vlm_capability():
    # If Ollama is reachable, probe text-only model vs vision model
    text_cap = probe_ollama_vlm_capability(model="qwen2.5:14b")
    if text_cap.available:
        assert text_cap.supports_vision is False

    vision_cap = probe_ollama_vlm_capability(model="moondream:latest")
    if vision_cap.available:
        assert vision_cap.supports_vision is True


def test_transcribe_crop_vlm_mocked():
    fake_img = Image.new("RGB", (100, 50), color="white")

    mock_resp_data = {
        "response": "POLISH MILD HERB PEPPER",
        "prompt_eval_count": 15,
        "eval_count": 8,
        "eval_duration": 45000000,
    }

    mock_http_resp = MagicMock()
    mock_http_resp.status = 200
    mock_http_resp.read.return_value = json.dumps(mock_resp_data).encode("utf-8")
    mock_http_resp.__enter__.return_value = mock_http_resp

    with patch("urllib.request.urlopen", return_value=mock_http_resp):
        res = transcribe_crop_vlm(fake_img, model="test_model", endpoint="http://127.0.0.1:11434")
        assert res.success is True
        assert res.cleaned_text == "POLISH MILD HERB PEPPER"
        assert res.eval_count == 8
        assert res.eval_duration_ms == 45.0
