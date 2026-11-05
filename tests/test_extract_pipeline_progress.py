"""Tests for extraction progress tracking and page options."""

import json
from pathlib import Path
import pytest
from src.pipeline import extract_pdf


@pytest.mark.document_integration
def test_extract_pdf_tracks_progress(tmp_path: Path, text_pdf):
    """Verify that extract_pdf writes progress to extract_progress_<book>.json in output_dir."""
    input_pdf = text_pdf

    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Extract 1 page with no-ocr to test fast progress writing
    extract_pdf(
        input_pdf=input_pdf,
        output_dir=output_dir,
        pages="1",
        ocr=False,
    )

    progress_file = output_dir / f"extract_progress_{input_pdf.stem}.json"
    assert progress_file.exists(), "Progress file should be created"

    data = json.loads(progress_file.read_text())
    assert data["status"] == "completed"
    assert data["percent"] == 100.0
    assert "updated_at" in data

