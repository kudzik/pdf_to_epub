"""Evidence-based G2 evaluation. Missing measurements cannot authorize G3."""
from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from src.benchmark import compute_percentiles
from src.benchmark_manifest import load_test_pages_manifest
from src.extraction_adapter import compute_file_sha256, load_extracted_page
from src.gate_quality import check_page, combine
from src.ground_truth import load_page_ground_truth, validate_ground_truth_against_manifest
from src.ocr_modes import OcrEngineMode, ensure_candidate_does_not_overwrite_production, run_candidate_conversion

CRITERIA = (
    '1_no_column_interleaving', '2_measure_and_temp_invariants',
    '3_table_and_text_stability', '4_latency_under_30s_limit',
    '5_holdout_10_pages_completed', '6_production_truth_preserved',
)


def criterion_status(value: bool | None) -> str:
    return 'NOT_EVALUATED' if value is None else 'PASS' if value else 'FAIL'


class GateG2PageSummary(BaseModel):
    page_no: int
    duration_seconds: float | None = None
    total_blocks: int = 0
    skew_angle_deg: float | None = None
    column_count: int | None = None
    has_gutter: bool = False
    interleaving_detected: bool | None = None
    invariants_intact: bool | None = None
    notes: str | None = None


class GateG2EvaluationReport(BaseModel):
    run_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    frozen_config: dict[str, Any]
    holdout_pages: list[int]
    total_pages: int = 0
    total_blocks: int = 0
    page_summaries: list[GateG2PageSummary] = Field(default_factory=list)
    p50_duration_s: float | None = None
    p95_duration_s: float | None = None
    max_duration_s: float | None = None
    column_interleaving_cases: int = 0
    production_sha_before: str
    production_sha_after: str
    production_preserved: bool
    criteria_checklist: dict[str, bool | None]
    verdict: Literal['PASS', 'FAIL', 'NOT_EVALUATED'] = 'NOT_EVALUATED'
    candidate_markdown_path: str
    evidence: dict[int, dict[str, Any]] = Field(default_factory=dict)
    not_evaluated_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def derive_verdict(self):
        values = [self.criteria_checklist.get(key) for key in CRITERIA]
        self.verdict = 'FAIL' if any(v is False for v in values) else 'PASS' if all(v is True for v in values) else 'NOT_EVALUATED'
        return self


def _production_fingerprint() -> str:
    """Protect all books and assets, including additions/deletions, not one legacy name."""
    import hashlib
    root = Path('output/markdown')
    entries = {str(p): compute_file_sha256(p) for p in sorted(root.rglob('*')) if p.is_file()}
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def _environment() -> dict:
    import platform
    result = {'python': platform.python_version(), 'packages': {}, 'file_hashes': {}}
    for name in ['docling', 'docling-core', 'rapidocr-onnxruntime', 'onnxruntime', 'torch']:
        try:
            result['packages'][name] = version(name)
        except PackageNotFoundError:
            result['packages'][name] = None
    try:
        import onnxruntime
        result['onnx_providers'] = onnxruntime.get_available_providers()
    except ImportError:
        result['onnx_providers'] = []
    for file in [Path('uv.lock'), Path('web/package-lock.json'), *[Path('src') / name for name in ['gate_g2_evaluation.py', 'gate_quality.py', 'benchmark.py', 'ocr_modes.py', 'extraction_adapter.py', 'geometry.py', 'block_validator.py']]]:
        if file.is_file():
            result['file_hashes'][str(file)] = compute_file_sha256(file)
    return result


def run_gate_g2_holdout_evaluation(
    pdf_path: Path = Path('input/ksiazka.pdf'),
    holdout_manifest_path: Path = Path('benchmarks/fixtures/test_pages_manifest.json'),
    output_base_dir: Path = Path('output/candidates'),
    run_id: str | None = None,
    ground_truth_dir: Path = Path('benchmarks/fixtures/ground_truth'),
) -> GateG2EvaluationReport:
    if not pdf_path.is_file():
        raise FileNotFoundError(f'Source PDF not found: {pdf_path}')
    ensure_candidate_does_not_overwrite_production(output_base_dir)
    manifest = load_test_pages_manifest(holdout_manifest_path)
    pages = [entry.pdf_page for entry in manifest.holdout_pages]
    if len(pages) != 10 or len(set(pages)) != 10 or set(pages) & {p.pdf_page for p in manifest.dev_pages}:
        raise ValueError('Expected ten unique holdout pages disjoint from dev')
    if any(page < 1 or page > manifest.total_pages_in_pdf for page in pages):
        raise ValueError('Holdout page outside the source range')
    run_id = run_id or f'gate_g2_{datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")}'
    cand_dir = output_base_dir / run_id
    ensure_candidate_does_not_overwrite_production(cand_dir)
    before = _production_fingerprint()
    source_sha = compute_file_sha256(pdf_path)
    config = {'ocr_mode': OcrEngineMode.RAPID_OCR.value, 'languages': ['pl', 'en'], 'apply_geometry': True,
              'max_cer': 0.01, 'max_wer': 0.03, 'max_page_p95_s': 30,
              'environment': _environment(), 'source_pdf_sha256': source_sha,
              'holdout_manifest_sha256': compute_file_sha256(holdout_manifest_path), 'references': {}, 'reference_images': {}}
    reasons = []
    if manifest.source_pdf_sha256 != source_sha:
        reasons.append('Missing or mismatched source PDF SHA-256 in holdout manifest')
    references = {}
    for page in pages:
        file = ground_truth_dir / f'page_{page:03d}.json'
        try:
            gt = load_page_ground_truth(file)
            gt.validate_integrity()
            validate_ground_truth_against_manifest(gt, holdout_manifest_path)
            image = Path(gt.source_image_path)
            if gt.page_no != page or not image.is_file() or compute_file_sha256(image) != gt.source_image_sha256:
                raise ValueError('Page identity or reference image hash mismatch')
            if gt.has_unresolved_glyphs or gt.all_unresolved_glyphs() or not gt.blocks:
                raise ValueError('Unresolved or empty reference')
            if gt.metadata.get('source_pdf_sha256') != source_sha:
                raise ValueError('Reference is not bound to the source PDF hash')
            references[page] = gt
            config['references'][str(page)] = compute_file_sha256(file)
            config['reference_images'][str(image)] = gt.source_image_sha256
        except (OSError, ValueError) as error:
            reasons.append(f'Page {page}: {error}')
    criteria = dict.fromkeys(CRITERIA)
    # Refuse to consume holdout through OCR without usable, source-bound references.
    if reasons:
        after = _production_fingerprint()
        criteria[CRITERIA[5]] = before == after
        return GateG2EvaluationReport(run_id=run_id, frozen_config=config, holdout_pages=pages,
            production_sha_before=before, production_sha_after=after, production_preserved=before == after,
            criteria_checklist=criteria, candidate_markdown_path=str(cand_dir / f'{pdf_path.stem}.md'),
            not_evaluated_reasons=reasons)

    candidate = run_candidate_conversion(pdf_path=pdf_path, pages=pages, mode=OcrEngineMode.RAPID_OCR,
        languages=['pl', 'en'], apply_geometry=True, output_base_dir=output_base_dir, run_id=run_id)
    config['candidate_config'] = candidate.config.model_dump(mode='json')
    evidence = {}
    summaries = []
    durations = []
    actual_pages = []
    counts = 0
    for entry in manifest.holdout_pages:
        page = entry.pdf_page
        file = cand_dir / 'pages' / f'page_{page:03d}_extracted.json'
        try:
            extracted = load_extracted_page(file)
            if extracted.page_no != page or extracted.run_id != run_id:
                raise ValueError('Sidecar page/run identity mismatch')
            if not extracted.blocks or any(b.page_no != page or b.provenance.page_no != page or b.provenance.run_id != run_id or b.provenance.source_pdf_sha256 != source_sha for b in extracted.blocks):
                raise ValueError('Missing blocks or wrong source provenance')
        except (OSError, ValueError) as error:
            evidence[page] = {'invariants': None, 'text_and_tables': None, 'reading_order': None, 'error': str(error)}
            summaries.append(GateG2PageSummary(page_no=page, notes=str(error)))
            continue
        actual_pages.append(page)
        quality = check_page(references[page], extracted)
        if entry.has_tables and not references[page].tables:
            quality['text_and_tables'] = None
        evidence[page] = quality
        duration = candidate.page_durations.get(page)
        if duration is not None and math.isfinite(duration) and duration > 0:
            durations.append(duration)
        else:
            duration = None
        counts += len(extracted.blocks)
        summaries.append(GateG2PageSummary(page_no=page, duration_seconds=duration, total_blocks=len(extracted.blocks),
            invariants_intact=quality['invariants'], interleaving_detected=None if quality['reading_order'] is None else not quality['reading_order']))
    p50, p95 = compute_percentiles(durations) if len(durations) == len(pages) else (None, None)
    after = _production_fingerprint()
    reference_inputs_intact = all((ground_truth_dir / f'page_{int(page):03d}.json').is_file() and compute_file_sha256(ground_truth_dir / f'page_{int(page):03d}.json') == expected for page, expected in config['references'].items()) and all(Path(image).is_file() and compute_file_sha256(Path(image)) == expected for image, expected in config['reference_images'].items())
    config['reference_inputs_intact'] = reference_inputs_intact
    criteria.update({
        CRITERIA[0]: combine([evidence[p]['reading_order'] for p in pages]),
        CRITERIA[1]: combine([evidence[p]['invariants'] for p in pages]),
        CRITERIA[2]: combine([reference_inputs_intact, *[evidence[p]['text_and_tables'] for p in pages]]),
        CRITERIA[3]: None if p95 is None else p95 < 30,
        CRITERIA[4]: sorted(actual_pages) == sorted(pages) and sorted(candidate.pages_converted) == sorted(pages)
            and not candidate.is_production_truth and candidate.total_pages == len(pages) and candidate.total_blocks == counts
            and candidate.config.source_pdf_sha256 == source_sha and candidate.config.pages == pages and compute_file_sha256(pdf_path) == source_sha,
        CRITERIA[5]: before == after,
    })
    for key, value in criteria.items():
        if value is None:
            reasons.append(f'{key}: incomplete or unsupported evidence')
    return GateG2EvaluationReport(run_id=run_id, frozen_config=config, holdout_pages=pages,
        total_pages=len(actual_pages), total_blocks=counts, page_summaries=summaries,
        p50_duration_s=p50, p95_duration_s=p95, max_duration_s=max(durations) if durations else None,
        column_interleaving_cases=sum(ps.interleaving_detected is True for ps in summaries),
        production_sha_before=before, production_sha_after=after, production_preserved=before == after,
        criteria_checklist=criteria, candidate_markdown_path=str(cand_dir / f'{pdf_path.stem}.md'),
        evidence=evidence, not_evaluated_reasons=reasons)


def save_gate_g2_report(report: GateG2EvaluationReport, output_dir: Path) -> tuple[Path, Path]:
    ensure_candidate_does_not_overwrite_production(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # Revalidate derived verdict instead of trusting a manually changed field.
    report = GateG2EvaluationReport.model_validate(report.model_dump())
    stem = Path(report.run_id).name
    json_path, md_path = output_dir / f'{stem}.json', output_dir / f'{stem}.md'
    json_path.write_text(report.model_dump_json(indent=2) + '\n', encoding='utf-8')
    lines = [f'# Gate G2 — {report.verdict}', '', f'Przebieg: `{report.run_id}`',
        f'Strony: {report.total_pages} / {len(report.holdout_pages)}', '',
        '| Kryterium | Wynik |', '| --- | --- |']
    lines.extend(f'| {key} | {criterion_status(report.criteria_checklist.get(key))} |' for key in CRITERIA)
    lines += ['', '## Brakujące pomiary', ''] + [f'- {reason}' for reason in report.not_evaluated_reasons]
    lines += ['', '## Dane pomiarowe', '', '```json', json.dumps(report.evidence, ensure_ascii=False, indent=2), '```', '',
        'Zezwolono na przejście do Gate G3.' if report.verdict == 'PASS' else 'Brak zgody na Gate G3: napraw wykryte błędy lub uzupełnij pomiary.']
    md_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return json_path, md_path
