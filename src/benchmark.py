"""Quality metrics, error analysis, and benchmark comparison runner for OCR evaluations.

Implements:
- CER (Character Error Rate) and WER (Word Error Rate) with documented normalization.
- Detection of omissions (missing text blocks/sentences) and long repetitions.
- Strict evaluation of critical quantities, fractions, units (e.g. t. vs T.), and temperatures (375° vs 3758).
- Table and header structural score evaluation.
- Per-page and per-variant benchmarking reports.
- Negative test verification: deliberately corrupted inputs MUST trigger critical error flags.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from difflib import SequenceMatcher
import re
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.ground_truth import (
    PageGroundTruth,
    CriticalQuantity,
    load_all_ground_truths,
    load_page_ground_truth,
)
from src.extraction_adapter import ExtractedPage, load_extracted_page
from src.ocr_modes import (
    CandidateRunManifest,
    load_candidate_manifest,
    OcrEngineMode,
    run_candidate_conversion,
)


def normalize_text_for_metrics(text: str, casefold: bool = False) -> str:
    """Documented standard text normalization for CER/WER computation.

    - Normalizes Unicode punctuation (curly quotes, long dashes to standard equivalents).
    - Replaces multiple consecutive whitespace/newlines with a single space.
    - Strips leading and trailing whitespace.
    """
    if not text:
        return ""
    # Map typographic quotes and dashes
    s = (
        text.replace("“", '"')
        .replace("”", '"')
        .replace("’", "'")
        .replace("‘", "'")
        .replace("—", "-")
        .replace("–", "-")
    )
    if casefold:
        s = s.casefold()
    # Normalize whitespaces
    return " ".join(s.split())


def levenshtein_distance(seq1: list[Any] | str, seq2: list[Any] | str) -> int:
    """Compute standard Levenshtein edit distance between two sequences (chars or tokens)."""
    m, n = len(seq1), len(seq2)
    if m == 0:
        return n
    if n == 0:
        return m

    # Use two rows for memory efficiency O(min(m, n))
    current_row = list(range(n + 1))
    for i in range(1, m + 1):
        previous_row = current_row
        current_row = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if seq1[i - 1] == seq2[j - 1] else 1
            current_row[j] = min(
                previous_row[j] + 1,  # deletion
                current_row[j - 1] + 1,  # insertion
                previous_row[j - 1] + cost,  # substitution
            )
    return current_row[n]


def compute_cer(reference: str, hypothesis: str, normalize: bool = True) -> float:
    """Compute Character Error Rate (CER = edit_distance / len(ref)).

    Returns float from 0.0 (identical) upwards. If ref is empty, returns 0.0 if hyp empty else 1.0.
    """
    ref_norm = normalize_text_for_metrics(reference) if normalize else reference
    hyp_norm = normalize_text_for_metrics(hypothesis) if normalize else hypothesis

    if not ref_norm:
        return 0.0 if not hyp_norm else 1.0

    dist = levenshtein_distance(ref_norm, hyp_norm)
    return round(dist / len(ref_norm), 4)


def compute_wer(reference: str, hypothesis: str, normalize: bool = True) -> float:
    """Compute Word Error Rate (WER = edit_distance / len(ref_words)).

    Returns float from 0.0 (identical) upwards.
    """
    ref_norm = normalize_text_for_metrics(reference) if normalize else reference
    hyp_norm = normalize_text_for_metrics(hypothesis) if normalize else hypothesis

    ref_words = ref_norm.split()
    hyp_words = hyp_norm.split()

    if not ref_words:
        return 0.0 if not hyp_words else 1.0

    dist = levenshtein_distance(ref_words, hyp_words)
    return round(dist / len(ref_words), 4)


def detect_omissions_and_repetitions(
    reference: str,
    hypothesis: str,
    min_omission_words: int = 4,
    min_repetition_words: int = 5,
) -> tuple[list[str], list[str]]:
    """Detect omitted text segments and long repeated loops in the hypothesis.

    Returns:
        (omissions_list, repetitions_list)
    """
    ref_words = normalize_text_for_metrics(reference).split()
    hyp_words = normalize_text_for_metrics(hypothesis).split()

    matcher = SequenceMatcher(None, ref_words, hyp_words, autojunk=False)
    omissions: list[str] = []

    last_ref_idx = 0
    for block in matcher.get_matching_blocks():
        gap_size = block.a - last_ref_idx
        if gap_size >= min_omission_words:
            missing_text = " ".join(ref_words[last_ref_idx : block.a])
            omissions.append(missing_text)
        last_ref_idx = block.a + block.size

    # Repetition detection in hypothesis
    repetitions: list[str] = []
    n_words = len(hyp_words)
    for length in range(min_repetition_words, min(30, n_words // 2 + 1)):
        for i in range(n_words - 2 * length + 1):
            w1 = hyp_words[i : i + length]
            w2 = hyp_words[i + length : i + 2 * length]
            if w1 == w2:
                rep_phrase = " ".join(w1)
                if not any(rep_phrase in r for r in repetitions):
                    repetitions.append(rep_phrase)

    return omissions, repetitions


class QuantityMatch(BaseModel):
    """Result of evaluating a single critical quantity against candidate text."""

    quantity: CriticalQuantity
    status: str = Field(
        description="'exact' (perfect match), 'corrupted_value', 'corrupted_unit', 'missed'"
    )
    candidate_token: Optional[str] = None
    error_details: Optional[str] = None


def normalize_fraction_str(val: str) -> str:
    """Normalize fractional representations (e.g. '½' -> '1/2', '1 2' -> '1/2', '1-1/2' -> '1 1/2')."""
    return (
        val.replace("½", "1/2")
        .replace("¼", "1/4")
        .replace("¾", "3/4")
        .replace("⅛", "1/8")
        .strip()
    )


def evaluate_critical_quantities(
    ground_truth_quantities: list[CriticalQuantity],
    candidate_text: str,
) -> list[QuantityMatch]:
    """Evaluate candidate text against ground truth critical quantities."""
    results: list[QuantityMatch] = []
    cand_norm = normalize_text_for_metrics(candidate_text)

    for cq in ground_truth_quantities:
        target_val = normalize_fraction_str(cq.value)
        target_unit = cq.unit.strip()
        context_word = cq.ingredient_or_context.strip()

        # Check exact token first (respect case sensitivity for T. vs t.)
        case_sensitive = target_unit in ("T.", "t.") or " T." in cq.raw_token or " t." in cq.raw_token
        flags = 0 if case_sensitive else re.IGNORECASE
        exact_pattern = re.escape(normalize_fraction_str(cq.raw_token))
        if re.search(exact_pattern, cand_norm, flags):
            results.append(QuantityMatch(quantity=cq, status="exact", candidate_token=cq.raw_token))
            continue

        # Look for the context / ingredient in the candidate text
        ctx_regex = re.escape(context_word.split()[0]) if context_word else None
        context_found = False
        window_text = ""

        if ctx_regex:
            m = re.search(rf"\b{ctx_regex}", cand_norm, re.IGNORECASE)
            if m:
                context_found = True
                start = max(0, m.start() - 60)
                end = min(len(cand_norm), m.end() + 60)
                window_text = cand_norm[start:end]

        search_area = window_text if context_found else cand_norm

        # Check if value is present in search area
        val_escaped = re.escape(target_val)
        val_match = bool(re.search(rf"\b{val_escaped}\b", search_area))

        # Check unit presence (strict case sensitivity for T. vs t.)
        if target_unit in ("T.", "t.", "tbsp", "tsp"):
            unit_match = bool(re.search(rf"\b{re.escape(target_unit)}", search_area))
        else:
            unit_match = bool(re.search(rf"\b{re.escape(target_unit)}", search_area, re.IGNORECASE))

        # Check for case swap between T. and t.
        if target_unit == "T." and re.search(r"\b[0-9/ ]+\s*t\.\b", search_area):
            results.append(
                QuantityMatch(
                    quantity=cq,
                    status="corrupted_unit",
                    candidate_token="t.",
                    error_details="Critical unit mismatch: expected tablespoon ('T.'), found teaspoon ('t.')",
                )
            )
            continue
        elif target_unit == "t." and re.search(r"\b[0-9/ ]+\s*T\.\b", search_area):
            results.append(
                QuantityMatch(
                    quantity=cq,
                    status="corrupted_unit",
                    candidate_token="T.",
                    error_details="Critical unit mismatch: expected teaspoon ('t.'), found tablespoon ('T.')",
                )
            )
            continue

        # Known corruption check: 375° vs 3758 or 375"
        if target_val == "375" and ("3758" in cand_norm or '375"' in cand_norm):
            corrupted = "3758" if "3758" in cand_norm else '375"'
            results.append(
                QuantityMatch(
                    quantity=cq,
                    status="corrupted_value",
                    candidate_token=corrupted,
                    error_details=f"Oven temperature corrupted: expected '375°', found '{corrupted}'",
                )
            )
            continue

        # Fraction corruption check: 1/2 vs 1 2 or Va
        if "/" in target_val:
            components = target_val.split("/")
            spaced_form = f"{components[0]} {components[1]}"
            if spaced_form in search_area or "Va" in search_area or "V " in search_area:
                results.append(
                    QuantityMatch(
                        quantity=cq,
                        status="corrupted_value",
                        candidate_token="broken fraction",
                        error_details=f"Fraction corrupted: expected '{target_val}', found broken fraction tokens",
                    )
                )
                continue

        # Check if unit got swapped (e.g. expected T. but got t., or expected t. but got T.)
        if val_match:
            if target_unit == "T." and " t." in search_area:
                results.append(
                    QuantityMatch(
                        quantity=cq,
                        status="corrupted_unit",
                        candidate_token="t.",
                        error_details="Critical unit mismatch: expected tablespoon ('T.'), found teaspoon ('t.')",
                    )
                )
                continue
            if target_unit == "t." and " T." in search_area:
                results.append(
                    QuantityMatch(
                        quantity=cq,
                        status="corrupted_unit",
                        candidate_token="T.",
                        error_details="Critical unit mismatch: expected teaspoon ('t.'), found tablespoon ('T.')",
                    )
                )
                continue

        if val_match and unit_match:
            results.append(QuantityMatch(quantity=cq, status="exact", candidate_token=f"{target_val} {target_unit}"))
        elif val_match and not unit_match:
            results.append(
                QuantityMatch(
                    quantity=cq,
                    status="corrupted_unit",
                    error_details=f"Value '{target_val}' found, but unit '{target_unit}' missing or corrupted",
                )
            )
        elif not val_match and unit_match:
            results.append(
                QuantityMatch(
                    quantity=cq,
                    status="corrupted_value",
                    error_details=f"Unit '{target_unit}' found, but value '{target_val}' missing or corrupted",
                )
            )
        else:
            results.append(
                QuantityMatch(
                    quantity=cq,
                    status="missed",
                    error_details=f"Neither value '{target_val}' nor unit '{target_unit}' found near '{context_word}'",
                )
            )

    return results


class PageQualityReport(BaseModel):
    """Quality evaluation report for a single page compared against ground truth."""

    page_no: int
    printed_page: Optional[str] = None
    layout_type: str
    cer: float
    wer: float
    total_ref_chars: int
    total_hyp_chars: int
    total_ref_words: int
    total_hyp_words: int
    omissions: list[str] = Field(default_factory=list)
    repetitions: list[str] = Field(default_factory=list)
    quantity_matches: list[QuantityMatch] = Field(default_factory=list)
    total_quantities: int = 0
    exact_quantities: int = 0
    corrupted_quantities: int = 0
    missed_quantities: int = 0
    table_score: Optional[float] = None
    critical_violations: list[str] = Field(default_factory=list)


def evaluate_page_quality(
    ground_truth: PageGroundTruth,
    candidate_text: str,
) -> PageQualityReport:
    """Evaluate candidate text against ground truth for a page."""
    ref_text = ground_truth.full_text(normalize=False, include_header_footer=False)
    cer = compute_cer(ref_text, candidate_text)
    wer = compute_wer(ref_text, candidate_text)

    omissions, repetitions = detect_omissions_and_repetitions(ref_text, candidate_text)
    quantities = ground_truth.all_critical_quantities()
    qty_matches = evaluate_critical_quantities(quantities, candidate_text)

    exact_count = sum(1 for q in qty_matches if q.status == "exact")
    corrupted_count = sum(1 for q in qty_matches if q.status in ("corrupted_value", "corrupted_unit"))
    missed_count = sum(1 for q in qty_matches if q.status == "missed")

    critical_violations: list[str] = []
    if wer > 0.05:
        critical_violations.append(f"High WER on page {ground_truth.page_no}: {wer*100:.1f}%")
    if corrupted_count > 0:
        critical_violations.append(
            f"Corrupted quantities on page {ground_truth.page_no}: {corrupted_count} measurement(s) altered"
        )
    if len(omissions) > 0:
        critical_violations.append(
            f"Omissions detected on page {ground_truth.page_no}: {len(omissions)} sentence block(s) omitted"
        )
    if len(repetitions) > 0:
        critical_violations.append(
            f"Repetition loops detected on page {ground_truth.page_no}: {len(repetitions)} block(s) repeated"
        )

    # Table evaluation if ground truth has tables
    table_score: Optional[float] = None
    if ground_truth.tables:
        matched_cells = 0
        total_cells = 0
        cand_lower = candidate_text.lower()
        for t in ground_truth.tables:
            for c in t.cells:
                if c.text.strip():
                    total_cells += 1
                    if c.text.lower() in cand_lower:
                        matched_cells += 1
        table_score = round(matched_cells / max(1, total_cells), 3) if total_cells > 0 else 1.0

    return PageQualityReport(
        page_no=ground_truth.page_no,
        printed_page=str(ground_truth.printed_page_no) if ground_truth.printed_page_no else None,
        layout_type=ground_truth.layout_type,
        cer=cer,
        wer=wer,
        total_ref_chars=len(ref_text),
        total_hyp_chars=len(candidate_text),
        total_ref_words=len(ref_text.split()),
        total_hyp_words=len(candidate_text.split()),
        omissions=omissions,
        repetitions=repetitions,
        quantity_matches=qty_matches,
        total_quantities=len(quantities),
        exact_quantities=exact_count,
        corrupted_quantities=corrupted_count,
        missed_quantities=missed_count,
        table_score=table_score,
        critical_violations=critical_violations,
    )


def compute_percentiles(values: list[float]) -> tuple[float, float]:
    """Compute p50/p95 using the documented nearest-rank convention."""
    if not values:
        return 0.0, 0.0
    import math
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("Durations must be finite and non-negative")
    s = sorted(values)
    # Nearest-rank quantiles: for ten samples p95 is the slowest sample.
    return tuple(round(s[max(0, math.ceil(q * len(s)) - 1)], 3) for q in (0.5, 0.95))



class BenchmarkRunReport(BaseModel):
    """Aggregate quality evaluation across multiple pages for an extraction run."""

    run_id: str
    engine_mode: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    pages_evaluated: list[int]
    mean_cer: float
    mean_wer: float
    worst_page_wer: float
    worst_page_no: int
    total_critical_quantities: int
    exact_critical_quantities: int
    corrupted_critical_quantities: int
    missed_critical_quantities: int
    quantity_accuracy_pct: float
    total_omissions: int
    total_repetitions: int
    critical_error_count: int
    duration_seconds: Optional[float] = None
    p50_page_duration_seconds: Optional[float] = None
    p95_page_duration_seconds: Optional[float] = None
    page_reports: list[PageQualityReport] = Field(default_factory=list)


def evaluate_candidate_run(
    cand_manifest_path: Path,
    fixtures_dir: Path = Path("benchmarks/fixtures/ground_truth"),
) -> BenchmarkRunReport:
    """Evaluate an entire candidate run against available ground truth fixtures."""
    cand_manifest = load_candidate_manifest(cand_manifest_path)
    cand_dir = cand_manifest_path.parent
    ground_truths = load_all_ground_truths(fixtures_dir)

    page_reports: list[PageQualityReport] = []
    eval_pages: list[int] = []

    pages_dir = cand_dir / "pages"
    for pno in cand_manifest.pages_converted:
        if pno not in ground_truths:
            continue
        gt = ground_truths[pno]
        eval_pages.append(pno)

        # Load candidate text from ExtractedPage sidecar if present
        sidecar_file = pages_dir / f"page_{pno:03d}_extracted.json"
        if sidecar_file.exists():
            extracted_page = load_extracted_page(sidecar_file)
            candidate_text = extracted_page.full_text()
        else:
            # Fall back to candidate markdown
            md_file = cand_dir / (cand_manifest.candidate_markdown_path or "")
            candidate_text = md_file.read_text(encoding="utf-8") if md_file.exists() else ""

        report = evaluate_page_quality(gt, candidate_text)
        page_reports.append(report)

    if not page_reports:
        raise ValueError(
            f"No overlapping pages found between candidate run {cand_manifest.config.run_id} and fixtures in {fixtures_dir}"
        )

    mean_cer = round(sum(r.cer for r in page_reports) / len(page_reports), 4)
    mean_wer = round(sum(r.wer for r in page_reports) / len(page_reports), 4)

    worst_report = max(page_reports, key=lambda r: r.wer)
    worst_wer = worst_report.wer
    worst_page_no = worst_report.page_no

    total_qty = sum(r.total_quantities for r in page_reports)
    exact_qty = sum(r.exact_quantities for r in page_reports)
    corrupted_qty = sum(r.corrupted_quantities for r in page_reports)
    missed_qty = sum(r.missed_quantities for r in page_reports)
    qty_accuracy = round((exact_qty / max(1, total_qty)) * 100, 2)

    total_omissions = sum(len(r.omissions) for r in page_reports)
    total_repetitions = sum(len(r.repetitions) for r in page_reports)
    critical_errors = sum(len(r.critical_violations) for r in page_reports)

    # Compute p50 and p95 latency if available
    durations = [
        cand_manifest.page_durations[p]
        for p in eval_pages
        if p in cand_manifest.page_durations
    ]
    p50, p95 = compute_percentiles(durations) if durations else (None, None)

    return BenchmarkRunReport(
        run_id=cand_manifest.config.run_id,
        engine_mode=cand_manifest.config.mode.value,
        pages_evaluated=eval_pages,
        mean_cer=mean_cer,
        mean_wer=mean_wer,
        worst_page_wer=worst_wer,
        worst_page_no=worst_page_no,
        total_critical_quantities=total_qty,
        exact_critical_quantities=exact_qty,
        corrupted_critical_quantities=corrupted_qty,
        missed_critical_quantities=missed_qty,
        quantity_accuracy_pct=qty_accuracy,
        total_omissions=total_omissions,
        total_repetitions=total_repetitions,
        critical_error_count=critical_errors,
        duration_seconds=cand_manifest.duration_seconds,
        p50_page_duration_seconds=p50,
        p95_page_duration_seconds=p95,
        page_reports=page_reports,
    )


class MultiEngineComparisonReport(BaseModel):
    """Comparative evaluation across multiple OCR engines on identical pages."""

    benchmark_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    pages_evaluated: list[int]
    engine_reports: dict[str, BenchmarkRunReport] = Field(default_factory=dict)
    summary_table: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    best_overall_engine: Optional[str] = None
    fastest_engine: Optional[str] = None


def compare_candidate_manifests(
    manifest_paths: list[Path],
    fixtures_dir: Path = Path("benchmarks/fixtures/ground_truth"),
    benchmark_id: Optional[str] = None,
) -> MultiEngineComparisonReport:
    """Compare multiple candidate conversion runs on the same pages."""
    if not manifest_paths:
        raise ValueError("manifest_paths list cannot be empty")

    b_id = benchmark_id or f"bench_cmp_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    engine_reports: dict[str, BenchmarkRunReport] = {}
    pages_set: set[int] = set()

    for m_path in manifest_paths:
        report = evaluate_candidate_run(m_path, fixtures_dir=fixtures_dir)
        engine_reports[report.engine_mode] = report
        pages_set.update(report.pages_evaluated)

    common_pages = sorted(pages_set)

    summary_table: list[dict[str, Any]] = []
    for mode_name, rep in engine_reports.items():
        # Gate G1/G2 criteria evaluation:
        # Mean CER <= 0.5% (0.005), Mean WER <= 1.0% (0.010), zero corrupted quantities, zero omissions
        meets_cer = rep.mean_cer <= 0.005
        meets_wer = rep.mean_wer <= 0.010
        meets_worst = rep.worst_page_wer <= 0.020
        meets_quantities = rep.corrupted_critical_quantities == 0
        meets_omissions = rep.total_omissions == 0
        passes_gate = (
            meets_cer
            and meets_wer
            and meets_worst
            and meets_quantities
            and meets_omissions
        )

        summary_table.append({
            "engine": mode_name,
            "run_id": rep.run_id,
            "mean_cer": rep.mean_cer,
            "mean_wer": rep.mean_wer,
            "worst_page_wer": rep.worst_page_wer,
            "worst_page_no": rep.worst_page_no,
            "quantity_accuracy_pct": rep.quantity_accuracy_pct,
            "corrupted_quantities": rep.corrupted_critical_quantities,
            "omissions": rep.total_omissions,
            "repetitions": rep.total_repetitions,
            "critical_errors": rep.critical_error_count,
            "duration_s": rep.duration_seconds,
            "p50_s": rep.p50_page_duration_seconds,
            "p95_s": rep.p95_page_duration_seconds,
            "passes_gate_g2": passes_gate,
        })

    # Find best accuracy and fastest engine
    best_wer_rep = min(engine_reports.values(), key=lambda r: (r.corrupted_critical_quantities, r.mean_wer))
    best_engine = best_wer_rep.engine_mode

    timed_reports = [r for r in engine_reports.values() if r.duration_seconds is not None]
    fastest_engine = min(timed_reports, key=lambda r: r.duration_seconds or 999999.0).engine_mode if timed_reports else None

    recommendations: list[str] = []
    # Analyze results
    for mode_name, rep in engine_reports.items():
        if rep.corrupted_critical_quantities > 0:
            recommendations.append(
                f"Silnik '{mode_name}' uszkadza krytyczne miary kulinarnych składników ({rep.corrupted_critical_quantities} błędów) - wymaga walidacji w R13."
            )
        if rep.total_omissions > 0:
            recommendations.append(
                f"Silnik '{mode_name}' pomija całe bloki/zdania ({rep.total_omissions} pominięć) - wymaga kontroli segmentacji i geometrii w R10."
            )

    if all(not row["passes_gate_g2"] for row in summary_table):
        recommendations.append(
            "Żaden silnik podstawowy nie spełnia jeszcze w 100% rygorystycznych progów Gate G2 "
            "(WER <= 1.0%, brak uszkodzeń miar, brak pominięć). "
            "Konieczne jest wdrożenie R10 (kolejność czytania i dewarping dla układów wielokolumnowych) "
            "oraz R13 (reguły walidacji bloków)."
        )

    return MultiEngineComparisonReport(
        benchmark_id=b_id,
        pages_evaluated=common_pages,
        engine_reports=engine_reports,
        summary_table=summary_table,
        recommendations=recommendations,
        best_overall_engine=best_engine,
        fastest_engine=fastest_engine,
    )


def format_comparison_markdown(report: MultiEngineComparisonReport) -> str:
    """Format MultiEngineComparisonReport into a readable GitHub-flavored Markdown report."""
    lines: list[str] = [
        f"# Raport z Benchmarku OCR — {report.benchmark_id}",
        "",
        f"- **Data wykonania:** {report.created_at}",
        f"- **Strony testowane (z Ground Truth):** {report.pages_evaluated}",
        f"- **Najlepszy silnik (jakość tekstu/miar):** `{report.best_overall_engine}`",
        f"- **Najszybszy silnik:** `{report.fastest_engine}`",
        "",
        "## Tabela porównawcza silników OCR",
        "",
        "| Silnik | Średni CER | Średni WER | Najgorszy WER | Dokładność miar | Błędy miar | Pominięcia | p50 / p95 (s) | Gate G2? |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for row in report.summary_table:
        p50_p95 = f"{row['p50_s']}s / {row['p95_s']}s" if row['p50_s'] is not None else "N/A"
        passes = "✅ ZALICZONE" if row["passes_gate_g2"] else "❌ DO POPRAWY"
        lines.append(
            f"| `{row['engine']}` | {row['mean_cer']*100:.2f}% | {row['mean_wer']*100:.2f}% | "
            f"{row['worst_page_wer']*100:.2f}% (str. {row['worst_page_no']}) | {row['quantity_accuracy_pct']:.1f}% | "
            f"{row['corrupted_quantities']} | {row['omissions']} | {p50_p95} | {passes} |"
        )

    lines.extend([
        "",
        "## Kluczowe wnioski i rekomendacje (R09 ➔ R10)",
        "",
    ])
    for rec in report.recommendations:
        lines.append(f"- {rec}")
    lines.append("")

    return "\n".join(lines)


def run_multi_engine_benchmark(
    pdf_path: Path,
    pages: list[int],
    modes: Optional[list[OcrEngineMode]] = None,
    languages: Optional[list[str]] = None,
    benchmarks_dir: Path = Path("output/benchmarks"),
    candidates_dir: Path = Path("output/candidates"),
    fixtures_dir: Path = Path("benchmarks/fixtures/ground_truth"),
    benchmark_id: Optional[str] = None,
) -> tuple[MultiEngineComparisonReport, Path, Path]:
    """Execute candidate runs for multiple engines, evaluate them, and export comparison artifacts."""
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    benchmarks_dir.mkdir(parents=True, exist_ok=True)
    candidates_dir.mkdir(parents=True, exist_ok=True)

    b_id = benchmark_id or f"bench_ocr_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    target_modes = modes or [
        OcrEngineMode.NATIVE,
        OcrEngineMode.FULL_PAGE,
        OcrEngineMode.RAPID_OCR,
    ]
    langs = languages or ["pl", "en"]

    manifest_paths: list[Path] = []

    for mode in target_modes:
        run_id = f"{b_id}_{mode.value}"
        manifest = run_candidate_conversion(
            pdf_path=pdf_path,
            pages=pages,
            mode=mode,
            languages=langs,
            output_base_dir=candidates_dir,
            run_id=run_id,
        )
        cand_dir = candidates_dir / run_id
        manifest_paths.append(cand_dir / "manifest.json")

    report = compare_candidate_manifests(
        manifest_paths=manifest_paths,
        fixtures_dir=fixtures_dir,
        benchmark_id=b_id,
    )

    json_path = benchmarks_dir / f"{b_id}.json"
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")

    md_path = benchmarks_dir / f"{b_id}.md"
    md_path.write_text(format_comparison_markdown(report), encoding="utf-8")

    return report, json_path, md_path
