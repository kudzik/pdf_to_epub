import re
from collections import Counter
from pathlib import Path
from typing import Optional


# Mapa popularnych ułamków typograficznych Unicode na ich reprezentację ASCII
UNICODE_FRACTIONS: dict[str, str] = {
    "½": "1/2",
    "¼": "1/4",
    "¾": "3/4",
    "⅓": "1/3",
    "⅔": "2/3",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}


def fix_hyphenation(text: str) -> str:
    """
    Usuwa łączniki dzielenia wyrazów na końcach wierszy (de-hyphenation)
    oraz soft-hyphens (\\xad / \\u00ad), łącząc podzielone słowa.
    """
    # 1. Usuwanie soft hyphens z opcjonalnymi spacjami (np. "cul\xad tural" -> "cultural")
    text = re.sub(
        r"(\b[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+)[\xad\u00ad]\s*([a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+\b)",
        r"\1\2",
        text,
    )

    # 2. Usuwanie łącznika na końcu linii, gdy następna linia zaczyna się z małej litery (podział słowa)
    text = re.sub(
        r"(\b[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ]{2,})-\s*\n\s*([a-zà-żąćęłńóśźż]{2,}\b)",
        r"\1\2",
        text,
    )

    # 3. Przypadki typu "Ameri - can" lub "back - ground" (łącznik rozdzielony spacjami wewnątrz słowa)
    # Wykluczamy myślniki oddzielające całe zdania/wyrażenia
    text = re.sub(
        r"(\b[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ]{3,})\s+-\s+([a-zà-żąćęłńóśźż]{2,}\b)",
        r"\1\2",
        text,
    )

    return text


def normalize_culinary_terms(text: str) -> str:
    """
    Normalizuje ułamki typograficzne i jednostki miar w przepisach kulinarnych.
    """
    # 1. Zamiana ułamków Unicode na ASCII (np. ½ -> 1/2)
    for uni_frac, ascii_frac in UNICODE_FRACTIONS.items():
        text = text.replace(uni_frac, ascii_frac)

    # 2. Usuwanie spacji wokół kreski ułamkowej (np. "1 / 2" -> "1/2")
    text = re.sub(r"(\b\d+)\s*/\s*(\d+\b)", r"\1/\2", text)

    # 3. Naprawa błędów OCR w ułamkach przed jednostkami kulinarnymi:
    # np. "1 2 tsp" -> "1/2 tsp", "1 4 cup" -> "1/4 cup"
    units_pattern = r"(?:tsp|tbsp|cup|cups|teaspoon|teaspoons|tablespoon|tablespoons|oz|lb|pound|pounds|gram|grams|g|kg|ml|l|pinch|pinches)\b"
    text = re.sub(
        rf"(\b[1-9])\s+([2348])\s+({units_pattern})",
        r"\1/\2 \3",
        text,
        flags=re.IGNORECASE,
    )

    return text


def remove_scan_noise(text: str) -> str:
    """
    Usuwa linie będące artefaktami skanera (losowe znaczki, plamki, puste linie).
    """
    lines = text.splitlines()
    cleaned_lines = []

    for line in lines:
        stripped = line.strip()

        # Zachowaj linie puste, separatory Markdown lub linie tabel
        if not stripped or stripped.startswith("---") or stripped.startswith("|") or stripped.startswith("#"):
            cleaned_lines.append(line)
            continue

        # 1. Znaczniki typowe dla szumu OCR (kwadraciki, daszki, tyldy, itp.)
        if any(c in "■·▪♦▲▼◄►`~^<>\\" for c in stripped) and len(stripped) < 35:
            continue

        alnum_count = sum(1 for c in stripped if c.isalnum())

        # 2. Bardzo krótkie linie z małą liczbą liter/cyfr i symbolami (np. "b *", "·", "1 \.")
        if alnum_count <= 2 and len(stripped) < 20 and not stripped.isdigit():
            continue

        # 3. Krótkie linie z przewagą znaków interpunkcyjnych lub dziwnych zakończeń (np. "FlraS -*")
        non_alnum_ratio = (len(stripped) - alnum_count) / len(stripped)
        if len(stripped) < 25 and non_alnum_ratio >= 0.35:
            continue

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def clean_running_headers(text: str) -> str:
    """
    Wykrywa i usuwa powtarzające się nagłówki żywej paginy stron
    (np. 'Foreword 9', '9 Foreword', wielokrotne '## POLISH HERITAGE COOKERY').
    """
    # 1. Usuwanie linii typu "Foreword 12" lub "12 Foreword"
    text = re.sub(r"^\s*(?:Foreword\s+\d+|\d+\s+Foreword)\s*$", "", text, flags=re.MULTILINE | re.IGNORECASE)

    # 2. Usuwanie numerów stron będących osobnymi liniami (np. sam numer "14" na początku/końcu strony)
    # ale zachowując nagłówki i tabele
    text = re.sub(r"^\s*\d{1,4}\s*$", "", text, flags=re.MULTILINE)

    # 3. Deduplikacja powtarzających się nagłówków H2, które występują wielokrotnie jako żywa pagina
    lines = text.splitlines()
    header_counts = Counter(line.strip() for line in lines if line.strip().startswith("## "))

    seen_headers = set()
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## ") and header_counts[stripped] > 3:
            # Jeśli ten nagłówek pojawia się > 3 razy w książce (jak tytuł książki),
            # zostaw tylko pierwsze wystąpienie, resztę pomiń jako żywą paginę
            if stripped in seen_headers:
                continue
            seen_headers.add(stripped)
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def normalize_typography(text: str) -> str:
    """
    Normalizuje encje HTML, spacje i interpunkcję.
    """
    # 1. Encje HTML
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

    # 2. Usuwanie wielokrotnych spacji w wierszach (ale nie na początku linii wcięcia)
    text = re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", text)

    # 3. Usuwanie spacji przed znakami interpunkcyjnymi (np. "wyraz , " -> "wyraz, ")
    text = re.sub(r"\s+([,.:;?!])", r"\1", text)

    # 4. Redukcja wielokrotnych pustych linii do maksymalnie dwóch
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip() + "\n"


def clean_markdown_text(text: str) -> str:
    """
    Kompleksowy proces czyszczenia i normalizacji tekstu Markdown ze skanów.
    """
    text = fix_hyphenation(text)
    text = normalize_culinary_terms(text)
    text = remove_scan_noise(text)
    text = clean_running_headers(text)
    text = normalize_typography(text)
    return text


def clean_markdown_file(
    input_path: Path,
    output_path: Optional[Path] = None,
) -> Path:
    """
    Wczytuje plik Markdown, czyści go i zapisuje do pliku wyjściowego
    (lub nadpisuje plik wejściowy, jeśli output_path nie został podany).
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Plik {input_path} nie istnieje.")

    raw_text = input_path.read_text(encoding="utf-8")
    cleaned_text = clean_markdown_text(raw_text)

    dest = output_path or input_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(cleaned_text, encoding="utf-8")
    return dest
