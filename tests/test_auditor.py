from pathlib import Path
from src.auditor import (
    audit_markdown_content,
    audit_markdown_file,
    detect_corrupted_tokens,
    detect_ocr_substitutions,
    extract_toc_chapters,
    is_overlong_heading,
)


def test_detect_corrupted_tokens():
    """Weryfikuje wykrywanie uszkodzonych symboli OCR i zniekształconych ułamków."""
    text = (
        "This sentence has ha^been corrupted with vegetables^o^brine. "
        "Also take 1/2-% c. beans and '/2-I c. cream and cl1e water."
    )
    tokens = detect_corrupted_tokens(text)
    assert any("ha^been" in t for t in tokens)
    assert any("vegetables^o^brine" in t for t in tokens)
    assert any("1/2-%" in t for t in tokens)
    assert any("'/2-I" in t for t in tokens)
    assert any("cl1e" in t for t in tokens)


def test_detect_ocr_substitutions():
    """Weryfikuje detekcję zamienników słownikowych typowych dla OCR (die/the, diis/this)."""
    text = "Put in die oven and bake until diis is ready widi salt."
    subs = detect_ocr_substitutions(text)
    assert "die" in subs
    assert "diis" in subs
    assert "widi" in subs


def test_is_overlong_heading():
    """Weryfikuje wykrywanie nagłówków, które omyłkowo objęły cały akapit tekstu."""
    short_heading = "## BIGOS MYŚLIWSKI hunter's stew"
    assert is_overlong_heading(short_heading) is False

    long_heading = (
        "## TABLE SALT sol kuchenna. When the term salt is used, nearly everyone thinks of fine, "
        "This is nothing more than sodium chloride (NaCl). Salt is often iodized in areas whose "
        "atmosphere, water supply, and soil conditions lack this important element."
    )
    assert is_overlong_heading(long_heading) is True


def test_clean_paragraph_has_no_anomalies():
    """Weryfikuje, czy czysty, poprawny akapit nie generuje fałszywych alarmów."""
    clean_md = (
        "## CHICKEN BROTH rosół z kury\n\n"
        "Place 1 whole chicken in 3 quarts cold water. Add 2 carrots, 1 parsnip, and 1 leek. "
        "Simmer gently for 2 hours until the meat is tender. Season with salt and pepper to taste."
    )
    report = audit_markdown_content(clean_md)
    assert report.total_anomalies == 0
    assert report.high_severity_count == 0


def test_extract_toc_chapters():
    """Weryfikuje wyciąganie nazw rozdziałów ze spisu treści."""
    sample_toc = (
        "## CONTENTS\n\n"
        "| Foreword | 7 |\n"
        "|---|---|\n"
        "| Spices, Herbs, Seasonings & Other Ingredients | 40 |\n"
        "| Cold Hors d'Oeuvres 8c Appetizers | 73 |\n"
        "| Hot Meat & Meat Dishes | 243 |\n\n"
        "## CHAPTER 1\n"
    )
    chapters = extract_toc_chapters(sample_toc)
    assert "Spices, Herbs, Seasonings & Other Ingredients" in chapters
    assert "Cold Hors d'Oeuvres & Appetizers" in chapters
    assert "Hot Meat & Meat Dishes" in chapters


def test_audit_markdown_file_creates_json_report(tmp_path: Path):
    """Weryfikuje pełny audyt pliku na dysku i zapis raportu do formatu JSON."""
    dirty_md = tmp_path / "book.md"
    dirty_md.write_text(
        "## CONTENTS\n\n"
        "| Soups & Broths | 10 |\n\n"
        "## WHY THIS BOOK\n\n"
        "Normal text paragraph without errors.\n\n"
        "Corrupted paragraph with ha^been and b°ynthe in die oven.\n\n"
        "## OVERLONG " + ("A" * 200) + "\n\n",
        encoding="utf-8",
    )

    report_path = tmp_path / "report.json"
    report = audit_markdown_file(dirty_md, report_path)

    assert report_path.exists()
    assert report.total_anomalies >= 2
    assert report.high_severity_count >= 1
    assert "Soups & Broths" in report.missing_chapters

