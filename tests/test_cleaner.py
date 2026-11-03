from pathlib import Path
from src.cleaner import (
    clean_markdown_text,
    clean_markdown_file,
    clean_running_headers,
    fix_hyphenation,
    normalize_culinary_terms,
    remove_scan_noise,
    normalize_typography,
)


def test_fix_hyphenation():
    """Weryfikuje poprawne scalanie słów rozbitych łącznikami i soft-hyphens."""
    # Soft hyphen
    raw = "cul\xad tural influences and ingre\u00ad dients"
    assert fix_hyphenation(raw) == "cultural influences and ingredients"

    # Łącznik ze spacjami wewnątrz słowa
    raw2 = "Ameri - can and back - ground"
    assert fix_hyphenation(raw2) == "American and background"

    # Łącznik na końcu linii
    raw3 = "prepa-\nration of food"
    assert fix_hyphenation(raw3) == "preparation of food"


def test_normalize_culinary_terms():
    """Weryfikuje normalizację ułamków typograficznych i błędów miar."""
    # Ułamki Unicode
    raw = "Add ½ tsp salt and ¼ cup sugar and 1 ½ tbsp butter"
    assert normalize_culinary_terms(raw) == "Add 1/2 tsp salt and 1/4 cup sugar and 1 1/2 tbsp butter"

    # Rozbite ułamki ze spacją
    raw2 = "Mix 1 / 2 cup water"
    assert normalize_culinary_terms(raw2) == "Mix 1/2 cup water"

    # Błędy OCR typu '1 2 tsp'
    raw3 = "Take 1 2 tsp of pepper and 1 4 cup flour"
    assert normalize_culinary_terms(raw3) == "Take 1/2 tsp of pepper and 1/4 cup flour"


def test_remove_scan_noise():
    """Weryfikuje usuwanie śmieci skanera."""
    noise_sample = (
        "## Real Header\n"
        "■ ^p-\n"
        "FlraS -*\n"
        "Valid text line.\n"
        "<<<^/\n"
        ".' AS !\n"
        "---\n"  # Separator markdown - nie może być usunięty!
        "Final valid line.\n"
    )
    cleaned = remove_scan_noise(noise_sample)
    assert "■ ^p-" not in cleaned
    assert "FlraS -*" not in cleaned
    assert "<<<^/" not in cleaned
    assert "Valid text line." in cleaned
    assert "Final valid line." in cleaned
    assert "---" in cleaned


def test_clean_running_headers():
    """Weryfikuje deduplikację powtarzających się nagłówków żywej paginy."""
    sample = (
        "## POLISH HERITAGE COOKERY\nTekst 1\n"
        "## POLISH HERITAGE COOKERY\nTekst 2\n"
        "## POLISH HERITAGE COOKERY\nTekst 3\n"
        "## POLISH HERITAGE COOKERY\nTekst 4\n"
        "Foreword 9\n"
        "Treść rozdziału\n"
    )
    cleaned = clean_running_headers(sample)
    # Tytuł powinien wystąpić tylko raz
    assert cleaned.count("## POLISH HERITAGE COOKERY") == 1
    assert "Foreword 9" not in cleaned
    assert "Treść rozdziału" in cleaned


def test_normalize_typography():
    """Weryfikuje czyszczenie encji HTML, wielokrotnych spacji i interpunkcji."""
    raw = "Robert &amp; Maria Strybel  ,  authors   of   book . \n\n\n\nNext line."
    cleaned = normalize_typography(raw)
    assert "Robert & Maria Strybel," in cleaned
    assert "authors of book." in cleaned
    assert "\n\n\n" not in cleaned


def test_clean_markdown_file_roundtrip(tmp_path: Path):
    """Weryfikuje pełne czyszczenie pliku na dysku."""
    dirty_file = tmp_path / "dirty.md"
    dirty_file.write_text(
        "## POLISH HERITAGE COOKERY\n"
        "■ ^p-\n"
        "Add ½ tsp salt and cul\xad tural food.\n"
        "## POLISH HERITAGE COOKERY\n"
        "Another ½ cup.\n"
        "## POLISH HERITAGE COOKERY\n"
        "## POLISH HERITAGE COOKERY\n",
        encoding="utf-8",
    )

    clean_file = tmp_path / "cleaned.md"
    result_path = clean_markdown_file(dirty_file, clean_file)

    assert result_path == clean_file
    assert clean_file.exists()

    content = clean_file.read_text(encoding="utf-8")
    assert "■ ^p-" not in content
    assert "cultural food" in content
    assert "1/2 tsp salt" in content
    assert "1/2 cup" in content
    assert content.count("## POLISH HERITAGE COOKERY") == 1

