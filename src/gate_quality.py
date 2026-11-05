"""Conservative reference-based checks for the G2 gate; unknown is never success."""
from collections import Counter
from difflib import SequenceMatcher
import re

from src.benchmark import compute_cer, compute_wer, normalize_text_for_metrics
from src.block_validator import extract_culinary_measurements
from src.extraction_adapter import ExtractedPage
from src.ground_truth import PageGroundTruth


def combine(results: list[bool | None]) -> bool | None:
    if any(value is False for value in results):
        return False
    if not results or any(value is None for value in results):
        return None
    return True


def table_matches(reference: PageGroundTruth, candidate: ExtractedPage) -> bool | None:
    if not reference.tables:
        return True  # Explicit reference confirms that no table is expected.
    tables = []
    for block in sorted(candidate.blocks, key=lambda b: b.reading_order):
        if block.block_type != 'table':
            continue
        rows = []
        for line in block.text.splitlines():
            if not line.strip().startswith('|'):
                continue
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if cells and all(re.fullmatch(r':?-{3,}:?', cell) for cell in cells):
                continue
            rows.append(cells)
        if rows:
            tables.append(rows)
    if not tables:
        return None  # No supported cell-position representation; never infer stability.
    if len(tables) != len(reference.tables):
        return False
    results = []
    for ref, rows in zip(sorted(reference.tables, key=lambda t: t.reading_order), tables):
        if not ref.cells or any(c.row_span != 1 or c.col_span != 1 for c in ref.cells):
            results.append(None)
            continue
        if len(rows) != ref.num_rows or any(len(row) != ref.num_cols for row in rows):
            results.append(False)
            continue
        row_base = 0 if any(c.row == 0 for c in ref.cells) else 1
        col_base = 0 if any(c.col == 0 for c in ref.cells) else 1
        if len({(c.row, c.col) for c in ref.cells}) != ref.num_rows * ref.num_cols:
            results.append(None)
            continue
        try:
            results.append(all(normalize_text_for_metrics(c.text) == normalize_text_for_metrics(rows[c.row-row_base][c.col-col_base]) for c in ref.cells))
        except IndexError:
            results.append(None)
    return combine(results)


def check_page(reference: PageGroundTruth, candidate: ExtractedPage) -> dict:
    ref_blocks = [b for b in sorted(reference.blocks, key=lambda b: b.reading_order) if b.block_type not in {'header_footer', 'separator'}]
    blocks = [b for b in sorted(candidate.blocks, key=lambda b: b.reading_order) if b.block_type not in {'header_footer', 'separator'}]
    ref_text = '\n\n'.join(b.text for b in ref_blocks)
    text = '\n\n'.join(b.text for b in blocks)
    if not ref_text.strip() or reference.has_unresolved_glyphs or reference.all_unresolved_glyphs():
        return {'invariants': None, 'text_and_tables': None, 'reading_order': None, 'reason': 'Empty or unresolved reference'}
    cer, wer = compute_cer(ref_text, text), compute_wer(ref_text, text)
    quantities: list[bool | None] = []
    matched_orders = []
    used = set()
    # Align independently of the measured quantities so an altered amount cannot
    # obtain a better match merely by borrowing another block's identical amount.
    def anchor(value):
        return re.sub(r'\d+(?:[.,/]\d+)*', '#', normalize_text_for_metrics(value))
    for ref in ref_blocks:
        ranked = sorted(((SequenceMatcher(None, anchor(ref.text), anchor(b.text), autojunk=False).ratio(), i, b) for i, b in enumerate(blocks) if i not in used), key=lambda item: item[0], reverse=True)
        if not ranked or ranked[0][0] < 0.6:
            quantities.append(False)
            continue
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.05:
            quantities.append(None)
            continue
        _, index, block = ranked[0]
        used.add(index)
        matched_orders.append(block.reading_order)
        tokens_match = Counter(extract_culinary_measurements(ref.text)) == Counter(extract_culinary_measurements(block.text))
        annotations_match = all(normalize_text_for_metrics(q.raw_token) in normalize_text_for_metrics(block.text) for q in ref.critical_quantities)
        quantities.append(tokens_match and annotations_match)
    # Detect extra measured content too, not only missing reference quantities.
    quantities.append(Counter(extract_culinary_measurements(ref_text)) == Counter(extract_culinary_measurements(text)))
    order = None if len(matched_orders) != len(ref_blocks) else matched_orders == sorted(matched_orders) and len(set(matched_orders)) == len(matched_orders)
    table = table_matches(reference, candidate)
    return {'invariants': combine(quantities), 'text_and_tables': combine([cer <= 0.01 and wer <= 0.03, table]),
            'reading_order': order, 'cer': cer, 'wer': wer, 'table_match': table,
            'matched_blocks': len(used), 'reference_blocks': len(ref_blocks)}
