"""Read-only, targeted re-OCR of source pages. Never extracts or writes Markdown."""
import hashlib
import json
import re
import statistics
import sys
from difflib import SequenceMatcher
from pathlib import Path


def words(text):
    return list(re.finditer(r"[^\W_]+", text.casefold(), re.UNICODE))


def align_fragment(query, source):
    """Find a bounded source span; reject weak or incomplete alignments."""
    q, s = words(query), words(source)
    if len(q) < 6:
        raise ValueError("Za krótki fragment do pewnego dopasowania strony PDF")
    matches = SequenceMatcher(None, [w[0] for w in q], [w[0] for w in s], autojunk=False).get_matching_blocks()
    blocks = [b for b in matches if b.size >= 3]
    coverage = sum(b.size for b in blocks) / len(q)
    if not blocks or coverage < .28 or blocks[0].a > 8 or len(q) - (blocks[-1].a + blocks[-1].size) > 8:
        raise ValueError("Nie znaleziono pewnych granic fragmentu w odczycie skanu")
    first, last = blocks[0], blocks[-1]
    # A corrupted word at the boundary can leave only a two-word closing anchor.
    trailing = [b for b in matches if b.size >= 2 and b.a >= last.a and len(q)-(b.a+b.size) <= 4 and b.b-(last.b+last.size) < 30]
    if trailing:
        last = trailing[-1]
    # OCR corruptions insert/delete tokens, so extrapolating query token counts
    # leaks adjacent paragraphs. Use the bounding OCR lines of reliable anchors.
    start_char = source.rfind('\n', 0, s[first.b].start()) + 1
    end_char = source.find('\n', s[last.b + last.size - 1].end())
    if end_char < 0:
        end_char = len(source)
    return source[start_char:end_char].strip(), round(coverage, 3)



def align_short_fragment(query, source):
    """Match a short title against actual OCR lines, never complete it from memory."""
    import unicodedata
    def canonical(text):
        text = unicodedata.normalize('NFKD', text.casefold()).replace('0', 'o')
        return ''.join(ch for ch in text if ch.isalnum())
    q = canonical(query)
    ranked = []
    lines = source.splitlines()
    for i in range(len(lines)):
        for size in (1, 2, 3):
            line = '\n'.join(lines[i:i+size])
            normalized = canonical(line)
            if not normalized or len(normalized) > max(120, len(q)*2):
                continue
            similarity = SequenceMatcher(None, q, normalized, autojunk=False).ratio()
            ranked.append((similarity, line, i, i+size))
    ranked.sort(reverse=True)
    if not ranked or ranked[0][0] < .55:
        raise ValueError('Nie znaleziono podobnego nagłówka w odczycie skanu')
    best = ranked[0]
    alternatives = [r for r in ranked[1:] if r[3] <= best[2] or r[2] >= best[3]]
    if alternatives and best[0]-alternatives[0][0] < .08:
        raise ValueError('Nagłówek pasuje do kilku różnych wierszy skanu')
    return best[1], round(best[0], 3)



def ordered_lines(rows):
    """Restore reading order after deskew; EasyOCR returns small boxes separately."""
    boxes = []
    for box, text, confidence in rows:
        xs, ys = zip(*box)
        boxes.append((statistics.mean(ys), min(xs), max(ys) - min(ys), str(text), float(confidence)))
    lines = []
    for row in sorted(boxes):
        if not lines or abs(row[0] - statistics.median(r[0] for r in lines[-1])) > statistics.median(r[2] for r in lines[-1]) * .5:
            lines.append([])
        lines[-1].append(row)
    return [{"text": " ".join(r[3] for r in sorted(line, key=lambda r: r[1])),
             "confidence": round(sum(r[4] * len(r[3]) for r in line) / max(1, sum(len(r[3]) for r in line)), 3)} for line in lines]


def deskew(image):
    import cv2
    import numpy as np
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    bw = (gray[int(.12*h):int(.85*h), int(.18*w):int(.94*w)] < 130).astype(np.uint8)
    best = (-1, 0)
    for angle in np.arange(-3, 3.01, .2):
        matrix = cv2.getRotationMatrix2D((bw.shape[1]/2, bw.shape[0]/2), angle, 1)
        rotated = cv2.warpAffine(bw, matrix, (bw.shape[1], bw.shape[0]))
        score = float(np.var(np.sum(rotated, axis=1)))
        if score > best[0]:
            best = (score, float(angle))
    return cv2.warpAffine(image, cv2.getRotationMatrix2D((w/2, h/2), best[1], 1), (w, h), borderValue=(255,255,255))


def locate_pages(fragment, page_texts):
    q = [w[0] for w in words(fragment)]
    ranked = []
    for i, text in enumerate(page_texts):
        tokens = [w[0] for w in words(text)]
        blocks = SequenceMatcher(None, q, tokens, autojunk=False).get_matching_blocks()
        significant = [b for b in blocks if b.size >= 3]
        score = sum(b.size for b in significant)
        ranked.append((score, i, significant))
    ranked.sort(reverse=True, key=lambda r: r[0])
    if not ranked or ranked[0][0] < max(6, len(q)*.2):
        raise ValueError("Nie można jednoznacznie odnaleźć fragmentu w warstwie tekstowej PDF")
    best = ranked[0]
    # A paragraph may continue on the following page. Require unique, consecutive pages.
    selected = [best[1]]
    covered = {j for b in best[2] for j in range(b.a, b.a+b.size)}
    for score, i, blocks in ranked[1:]:
        novel = {j for b in blocks for j in range(b.a, b.a+b.size)} - covered
        if abs(i-best[1]) == 1 and len(novel) >= max(6, len(q)*.08):
            selected.append(i)
            break
    # Similar text on a distant page is ambiguous; do not silently pick one.
    if len(ranked)>1 and ranked[1][0] >= best[0]*.9 and abs(ranked[1][1]-best[1]) > 1:
        raise ValueError("Fragment pasuje do kilku różnych stron PDF")
    return sorted(selected)


def capture(pdf_path, fragment, cache_dir, context=None):
    import pypdfium2 as pdfium
    import numpy as np
    pdf_path, cache_dir = Path(pdf_path), Path(cache_dir)
    stat = pdf_path.stat()
    key = hashlib.sha256(f"v1:{pdf_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:24]
    cache_dir = cache_dir / key
    cache_dir.mkdir(parents=True, exist_ok=True)
    with pdfium.PdfDocument(pdf_path) as pdf:
        index_path = cache_dir / 'index.json'
        if index_path.exists():
            texts = json.loads(index_path.read_text())
        else:
            texts = []
            for i in range(len(pdf)):
                page = pdf[i]
                tp = page.get_textpage()
                try:
                    texts.append(tp.get_text_range())
                finally:
                    tp.close()
                    page.close()
            index_path.write_text(json.dumps(texts, ensure_ascii=False))
        try:
            pages = locate_pages(fragment, texts)
        except ValueError:
            pages = set()
            for adjacent in context or []:
                try:
                    pages.update(locate_pages(adjacent, texts))
                except ValueError:
                    pass
            if not pages or len(pages) > 3:
                raise ValueError('Brak jednoznacznego dopasowania strony także na podstawie sąsiednich akapitów')
            pages = sorted(pages)
        reader = None
        results = []
        for i in pages:
            target = cache_dir / f'{i+1}.json'
            if target.exists():
                lines = json.loads(target.read_text())
            else:
                import easyocr
                import torch
                torch.set_num_threads(4)
                if reader is None:
                    reader = easyocr.Reader(['pl','en'], gpu=False, download_enabled=False, verbose=False)
                page = pdf[i]
                try:
                    bitmap = page.render(scale=2.5)
                    image = np.array(bitmap.to_pil())
                    bitmap.close()
                finally:
                    page.close()
                lines = ordered_lines(reader.readtext(deskew(image), detail=1, paragraph=False))
                target.write_text(json.dumps(lines, ensure_ascii=False))
            # Running page numbers are layout, not part of the paragraph.
            lines = list(lines)
            while lines and re.fullmatch(r"(?:\d+\s+[\w '\-]{1,40}|[\w '\-]{1,40}\s+\d+|\d+)", lines[0]['text']):
                lines.pop(0)
            results.extend(lines)
        source = '\n'.join(r['text'] for r in results)
        if fragment.lstrip().startswith('#') or len(words(fragment)) < 12:
            aligned, coverage = align_short_fragment(fragment, source)
        else:
            aligned, coverage = align_fragment(fragment, source)
        relevant = [r for r in results if r['text'] in aligned or any(w in aligned for w in [r['text'][:40]] if len(w)>15)]
        confidence = round(sum(r['confidence'] * len(r['text']) for r in relevant) / max(1, sum(len(r['text']) for r in relevant)), 3)
        return {"text": aligned, "pages": [i+1 for i in pages], "coverage": coverage, "confidence": confidence, "method": "easyocr"}


def main():
    try:
        body = json.load(sys.stdin)
        root = Path(__file__).resolve().parent.parent
        book = Path(body['file']).stem
        result = capture(root/'input'/f'{book}.pdf', body['fragment'], root/'output'/'source_cache', body.get('context'))
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
