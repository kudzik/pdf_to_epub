"""Unit tests for Gate G2 evaluation and criteria checklist."""

import json
from pathlib import Path
import pytest

from src.gate_g2_evaluation import (
    GateG2EvaluationReport,
    GateG2PageSummary,
    save_gate_g2_report,
)
from src.ocr_modes import ProductionOverwriteError


def test_gate_g2_report_structure(tmp_path: Path):
    rep = GateG2EvaluationReport(
        run_id="test_g2_run",
        frozen_config={"engine": "rapidocr"},
        holdout_pages=[517, 560, 620],
        total_pages=3,
        total_blocks=45,
        page_summaries=[
            GateG2PageSummary(
                page_no=517,
                duration_seconds=1.2,
                total_blocks=15,
                skew_angle_deg=0.3,
                column_count=2,
                has_gutter=True,
                interleaving_detected=False,
                invariants_intact=True,
            )
        ],
        p50_duration_s=1.2,
        p95_duration_s=1.5,
        max_duration_s=1.5,
        column_interleaving_cases=0,
        production_sha_before="abc123sha",
        production_sha_after="abc123sha",
        production_preserved=True,
        criteria_checklist={
            "1_no_column_interleaving": True,
            "2_measure_and_temp_invariants": True,
            "3_table_and_text_stability": True,
            "4_latency_under_30s_limit": True,
            "5_holdout_10_pages_completed": True,
            "6_production_truth_preserved": True,
        },
        verdict="PASS",
        candidate_markdown_path="output/candidates/test_g2_run/ksiazka.md",
    )

    # Test saving
    out_dir = tmp_path / "g2_reports"
    j_path, m_path = save_gate_g2_report(rep, out_dir)
    assert j_path.exists()
    assert m_path.exists()

    data = json.loads(j_path.read_text(encoding="utf-8"))
    assert data["run_id"] == "test_g2_run"
    assert data["verdict"] == "PASS"
    assert data["criteria_checklist"]["1_no_column_interleaving"] is True

    # Guard against saving to production
    with pytest.raises(ProductionOverwriteError):
        save_gate_g2_report(rep, Path("output/markdown/ksiazka.md"))

