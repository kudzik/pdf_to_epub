import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class AnomalyIssue:
    paragraph_index: int
    approx_line: int
    severity: str  # "high", "medium", "low"
    score: float
    categories: list[str]
    sample_tokens: list[str]
    snippet: str


@dataclass
class AuditReport:
    total_paragraphs: int
    total_anomalies: int
    high_severity_count: int
    medium_severity_count: int
    low_severity_count: int
    overlong_headings_count: int
    missing_chapters: list[str] = field(default_factory=list)
    anomalies: list[AnomalyIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def detect_corrupted_tokens(text: str) -> list[str]:
    """Wyszukuje tokeny z ewidentnymi uszkodzeniami OCR (daszki, stopnie, nawiasy wewnątrz słów)."""
    # Tokeny zawierające daszki w środku liter (np. "ha^been", "vegetables^o^brine")
    tokens = re.findall(r"\b[a-zA-Z0-9ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+(?:\^|°|<|>|~|`)[a-zA-Z0-9ąćęłńóśźżĄĆĘŁŃÓŚŹŻ\^°<>~`]*\b", text)

    # Tokeny z dziwnymi symbolami w ułamkach (np. "1/2-%", "'/2-I", "1/2-I")
    fractions_garbage = re.findall(r"(?:'/\d+-\w+|\d+/\d+-\%|\d+/\d+-[a-zA-Z]+|\%\s*c\.)", text)
    tokens.extend(fractions_garbage)

    # Zbitki literowe z losowymi cyframi w środku słów (np. "cl1e", "wl1o", "th1e", "b0lled")
    mixed_digits = re.findall(r"\b[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+\d+[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+\b", text)
    tokens.extend(mixed_digits)

    return list(set(tokens))


def detect_ocr_substitutions(text: str) -> list[str]:
    """Wykrywa charakterystyczne zamienniki OCR (np. 'die' zamiast 'the' w zdaniu angielskim)."""
    # Wyszukujemy "die" w kontekście angielskim, np. "in die oven", "die meat", "die water"
    substitutions = []
    die_matches = re.findall(r"\b(?:in|on|of|with|to|cook|add|heat|stir|pour)\s+(die)\s+[a-z]+", text, re.IGNORECASE)
    if die_matches:
        substitutions.extend(die_matches)

    # Słowa typu "diis", "widi", "tiien", "tiiat"
    for word in ["diis", "widi", "tiien", "tiiat", "diese"]:
        if re.search(rf"\b{word}\b", text, re.IGNORECASE):
            substitutions.append(word)

    return list(set(substitutions))


def is_overlong_heading(text: str) -> bool:
    """Sprawdza, czy linia nagłówka nie jest przypadkowo całym akapitem."""
    stripped = text.strip()
    if stripped.startswith("## ") and len(stripped) > 180:
        return True
    return False


def extract_toc_chapters(markdown_content: str) -> list[str]:
    """Wyciąga nazwy głównych rozdziałów ze spisu treści (CONTENTS) w pliku Markdown."""
    toc_match = re.search(r"## CONTENTS\s*\n\n(.*?)(?:\n\n## |\Z)", markdown_content, re.DOTALL)
    if not toc_match:
        return []

    table_text = toc_match.group(1)
    chapters = []
    for line in table_text.splitlines():
        if "|" in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if parts and not parts[0].startswith("---"):
                name = parts[0]
                # Oczyszczenie nazwy rozdziału
                name = re.sub(r"\s*8c\s*", " & ", name)
                name = re.sub(r"\s+", " ", name)
                if name.lower() not in ["foreword", "contents"]:
                    chapters.append(name)
    return chapters


def audit_markdown_content(markdown_content: str) -> AuditReport:
    """
    Główna funkcja audytująca plik Markdown pod kątem uszkodzeń OCR,
    ułamków, nagłówków oraz zgodności ze spisem treści.
    """
    paragraphs = markdown_content.split("\n\n")
    anomalies: list[AnomalyIssue] = []

    current_line = 1
    overlong_headings_count = 0

    for idx, p in enumerate(paragraphs):
        p_strip = p.strip()
        p_lines = p.count("\n") + 2  # +2 za podwójny enter

        if not p_strip or p_strip.startswith("|"):
            current_line += p_lines
            continue

        categories = []
        score = 0.0

        # 1. Sprawdzanie przerośniętych nagłówków
        if is_overlong_heading(p_strip):
            categories.append("overlong_heading")
            overlong_headings_count += 1
            score += 5.0

        # 2. Wykrywanie uszkodzonych tokenów i krzaków OCR
        corrupted_tokens = detect_corrupted_tokens(p_strip)
        if corrupted_tokens:
            categories.append("corrupted_ocr_symbols")
            score += len(corrupted_tokens) * 3.0

        # 3. Wykrywanie fałszywych zamienników słownikowych (die/the)
        ocr_substitutions = detect_ocr_substitutions(p_strip)
        if ocr_substitutions:
            categories.append("ocr_substitutions")
            score += len(ocr_substitutions) * 2.0

        # Jeśli akapit ma błędy, zapisujemy go do raportu
        all_issues = corrupted_tokens + ocr_substitutions
        if score > 0:
            if score >= 6.0:
                severity = "high"
            elif score >= 3.0:
                severity = "medium"
            else:
                severity = "low"

            snippet = p_strip[:180].replace("\n", " ") + ("..." if len(p_strip) > 180 else "")

            anomalies.append(
                AnomalyIssue(
                    paragraph_index=idx,
                    approx_line=current_line,
                    severity=severity,
                    score=score,
                    categories=categories,
                    sample_tokens=all_issues[:8],
                    snippet=snippet,
                )
            )

        current_line += p_lines

    # 4. Sprawdzanie spójności rozdziałów ze spisu treści
    expected_chapters = extract_toc_chapters(markdown_content)
    missing_chapters = []
    for chap in expected_chapters:
        # Sprawdzamy czy pierwsze 3 słowa rozdziału istnieją w pliku jako nagłówek
        clean_chap = re.sub(r"[^\w\s]", "", chap).strip()
        words = clean_chap.split()[:3]
        if words:
            pattern = re.compile(rf"#+.*?" + r".*?".join(re.escape(w) for w in words), re.IGNORECASE)
            if not pattern.search(markdown_content):
                missing_chapters.append(chap)

    high_count = sum(1 for a in anomalies if a.severity == "high")
    med_count = sum(1 for a in anomalies if a.severity == "medium")
    low_count = sum(1 for a in anomalies if a.severity == "low")

    return AuditReport(
        total_paragraphs=len(paragraphs),
        total_anomalies=len(anomalies),
        high_severity_count=high_count,
        medium_severity_count=med_count,
        low_severity_count=low_count,
        overlong_headings_count=overlong_headings_count,
        missing_chapters=missing_chapters,
        anomalies=anomalies,
    )


def audit_markdown_file(
    input_path: Path,
    report_output_path: Optional[Path] = None,
) -> AuditReport:
    """Wczytuje plik Markdown, wykonuje audyt i zapisuje raport JSON."""
    if not input_path.exists():
        raise FileNotFoundError(f"Plik {input_path} nie istnieje.")

    content = input_path.read_text(encoding="utf-8")
    report = audit_markdown_content(content)

    if report_output_path:
        report_output_path.parent.mkdir(parents=True, exist_ok=True)
        report_output_path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    return report
