import pytest
from pathlib import Path
from src.cleaner import restore_polish_and_ocr_artifacts, clean_markdown_text


def test_polish_diacritics_restoration():
    """Weryfikuje rekonstrukcję polskich znaków diakrytycznych z uszkodzeń starego OCR."""
    sample = (
        "## STUFFED CABBAGE ROLLS gol^bki\n"
        "Serve with meat and rice gol^bki and golgbki z migsem.\n"
        "Sprinkle with cebulk^ and fresh zielenink^.\n"
        "Stew with kapust^ and fasol^ in a pot.\n"
        "Preparation of w^trobki ciel^ce and pstr^gi po Sl^sku.\n"
        "King LeSZCZ7Aski loved fresh pfczki and ogdrki.\n"
        "Prepare nale£niki z grzyb6w and kluski £l?skie.\n"
        "Serve g£s z nadzieniem and kurcz£ pieczone.\n"
    )
    result = restore_polish_and_ocr_artifacts(sample)

    assert "gołąbki" in result
    assert "gol^bki" not in result
    assert "mięsem" in result
    assert "cebulką" in result
    assert "zieleninką" in result
    assert "kapustą" in result
    assert "fasolą" in result
    assert "wątróbki cielęce" in result
    assert "pstrągi po Śląsku" in result
    assert "Leszczyński" in result
    assert "pączki" in result
    assert "ogórki" in result
    assert "naleśniki" in result
    assert "grzybów" in result
    assert "kluski śląskie" in result
    assert "gęś" in result
    assert "kurczę" in result


def test_culinary_accents_restoration():
    """Weryfikuje zamianę znaków walutowych (£) na właściwe akcenty kulinarne (é, è)."""
    sample = (
        "Saut£ 1 c. chopped onion and prepare pur£e or purde.\n"
        "Serve with delicate canap£ and cr£me sauce.\n"
        "Season the liver pât£ and add gruy£re cheese.\n"
    )
    result = restore_polish_and_ocr_artifacts(sample)

    assert "sauté" in result.lower()
    assert "purée" in result.lower()
    assert "canapé" in result.lower()
    assert "crème" in result.lower()
    assert "pâté" in result.lower()
    assert "gruyère" in result.lower()
    assert "£" not in result


def test_broken_fractions_restoration():
    """Weryfikuje naprawę ułamków zniekształconych przez OCR (¥2, «, !, Vz, '/z)."""
    sample = (
        "Use ¥2 c. dry white mustard and 1 ¥2 c. diced pork.\n"
        "Mix Vz tsp of salt and add 1-114 lbs. meat with '/z c. water.\n"
        "Trim 3/« c. basic mayonnaise and add */« t. ground bay leaf.\n"
        "In blender, mix 14 c. canned cranberries.\n"
        "Bake for 1!4 hrs until golden brown.\n"
    )
    result = restore_polish_and_ocr_artifacts(sample)

    assert "1/2 c." in result
    assert "1 1/2 c." in result
    assert "1/2 tsp" in result
    assert "1-1 1/4 lbs." in result
    assert "1/2 c. water" in result
    assert "3/4 c." in result
    assert "1/4 t." in result
    assert "1/4 c." in result
    assert "1 1/4 hrs" in result
    assert "¥" not in result
    assert "«" not in result


def test_temperature_preservation_and_ocr_substitutions():
    """Weryfikuje zachowanie temperatur pieca (°F) przy jednoczesnym usuwaniu śmieci OCR."""
    sample = (
        "Preheat oven to 350° and bake for 45 min at 375°F.\n"
        "The recipe ha^been tested b°ynthe author.\n"
        "Put the meat in die oven and watch die sauce on die heat.\n"
        "Available on tire American market.\n"
        "Prepare bigos instead of biros with tasty pastlies.\n"
    )
    result = restore_polish_and_ocr_artifacts(sample)

    # Temperatury muszą pozostać nienaruszone
    assert "350°" in result
    assert "375°F" in result

    # Usterki OCR muszą być naprawione
    assert "has been" in result
    assert "by the" in result
    assert "in the oven" in result
    assert "watch the sauce" in result
    assert "on the heat" in result
    assert "on the American market" in result
    assert "bigos" in result
    assert "pastries" in result


def test_utf8_encoding_and_roundtrip(tmp_path: Path):
    """Weryfikuje poprawność kodowania UTF-8 i brak uszkodzeń diakrytyki przy zapisie na dysk."""
    polish_sample = "Zażółć gęślą jaźń: gołąbki, barszcz, żurek, pączki, śmietana, wątróbka."
    test_file = tmp_path / "polish.md"
    test_file.write_text(polish_sample, encoding="utf-8")

    read_back = test_file.read_text(encoding="utf-8")
    assert read_back == polish_sample

    cleaned = clean_markdown_text(read_back)
    assert "gołąbki" in cleaned
    assert "barszcz" in cleaned
    assert "żurek" in cleaned
    assert "pączki" in cleaned
