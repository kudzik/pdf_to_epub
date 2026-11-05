import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class PageLayoutType(str, Enum):
    PROSE_SINGLE_COLUMN = "prose_single_column"
    RECIPE_SINGLE_COLUMN = "recipe_single_column"
    RECIPE_TWO_COLUMN = "recipe_two_column"
    TABLE_OR_GLOSSARY = "table_or_glossary"
    INDEX_MULTI_COLUMN = "index_multi_column"
    PAGE_SPREAD_TRANSITION = "page_spread_transition"


class TestPageEntry(BaseModel):
    __test__ = False
    pdf_page: int
    printed_page: Optional[str] = None
    chapter: str
    layout_type: PageLayoutType
    has_fractions: bool = False
    has_temperatures: bool = False
    has_bilingual_titles: bool = False
    has_tables: bool = False
    has_illustrations: bool = False
    has_curved_gutter_or_noise: bool = False
    key_challenges: list[str] = Field(default_factory=list)
    description: str


class BenchmarkPagesManifest(BaseModel):
    manifest_version: str = "1.0"
    book_name: str = "ksiazka"
    source_pdf_path: str = "input/ksiazka.pdf"
    source_pdf_sha256: Optional[str] = None
    total_pages_in_pdf: int = 914
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    description: str = (
        "Zestaw 30 reprezentatywnych stron testowych: 20 rozwojowych (dev) do strojenia "
        "benchmarków i reguł oraz 10 odłożonych (holdout) do ślepego testu końcowego."
    )
    dev_pages: list[TestPageEntry] = Field(default_factory=list)
    holdout_pages: list[TestPageEntry] = Field(default_factory=list)


def validate_manifest_consistency(manifest: BenchmarkPagesManifest) -> dict:
    """Weryfikuje poprawność logiczną i rozłączność zbiorów stron w manifeście."""
    dev_nums = [p.pdf_page for p in manifest.dev_pages]
    holdout_nums = [p.pdf_page for p in manifest.holdout_pages]

    errors: list[str] = []

    if len(dev_nums) != 20:
        errors.append(f"Zbiór dev powinien liczyć dokładnie 20 stron (znaleziono {len(dev_nums)}).")

    if len(holdout_nums) != 10:
        errors.append(f"Zbiór holdout powinien liczyć dokładnie 10 stron (znaleziono {len(holdout_nums)}).")

    dev_set = set(dev_nums)
    if len(dev_set) != len(dev_nums):
        errors.append("Wykryto duplikaty wewnątrz zbioru stron dev.")

    holdout_set = set(holdout_nums)
    if len(holdout_set) != len(holdout_nums):
        errors.append("Wykryto duplikaty wewnątrz zbioru stron holdout.")

    overlap = dev_set.intersection(holdout_set)
    if overlap:
        errors.append(f"Zbiory dev i holdout nie są rozłączne! Nakładające się strony: {sorted(overlap)}")

    all_pages = dev_nums + holdout_nums
    for p in all_pages:
        if p < 1 or p > manifest.total_pages_in_pdf:
            errors.append(f"Numer strony PDF {p} wykracza poza zakres (1..{manifest.total_pages_in_pdf}).")

    required_known_cases = {25, 26, 33, 34, 40, 54, 58}
    missing_required = required_known_cases - dev_set
    if missing_required:
        errors.append(f"W zbiorze dev brakuje wymaganych trudnych stron: {sorted(missing_required)}")

    return {
        "is_valid": len(errors) == 0,
        "dev_count": len(dev_nums),
        "holdout_count": len(holdout_nums),
        "total_test_pages": len(all_pages),
        "errors": errors,
    }


def build_canonical_manifest() -> BenchmarkPagesManifest:
    """Buduje kanoniczny manifest 30 stron testowych zgodnie z wytycznymi PLAN_REKONSTRUKCJI.md."""
    dev_pages: list[TestPageEntry] = [
        # 1-7: Wymagane znane przypadki
        TestPageEntry(
            pdf_page=25,
            printed_page="23",
            chapter="Foreword",
            layout_type=PageLayoutType.PROSE_SINGLE_COLUMN,
            has_curved_gutter_or_noise=True,
            key_challenges=["de-hyphenation na końcu linii", "tekst ciągły", "wygięcie strony przy grzbiecie"],
            description="Wstęp: ciągły tekst narracyjny, rozbite słowa przy prawym marginesie.",
        ),
        TestPageEntry(
            pdf_page=26,
            printed_page="24",
            chapter="Foreword",
            layout_type=PageLayoutType.PAGE_SPREAD_TRANSITION,
            has_curved_gutter_or_noise=True,
            key_challenges=["przejście zdania między stronami 25 a 26", "nagłówek żywej paginy"],
            description="Wstęp: przejście zdania ze strony 25, żywa pagina '24 Foreword'.",
        ),
        TestPageEntry(
            pdf_page=33,
            printed_page="31",
            chapter="Foreword",
            layout_type=PageLayoutType.PROSE_SINGLE_COLUMN,
            has_curved_gutter_or_noise=True,
            key_challenges=["uszkodzenie OCR numeru strony (odczytane jako 211 zamiast 31)", "szum skanera"],
            description="Wstęp: zniekształcenia OCR w nagłówku, artefakty znakowe.",
        ),
        TestPageEntry(
            pdf_page=34,
            printed_page="32",
            chapter="Foreword",
            layout_type=PageLayoutType.PROSE_SINGLE_COLUMN,
            has_curved_gutter_or_noise=False,
            key_challenges=["zakończenie akapitu narracyjnego", "definicje technik kulinarnych"],
            description="Wstęp: definicje pojęć kulinarnych, podsumowanie rozdziału wstępnego.",
        ),
        TestPageEntry(
            pdf_page=40,
            printed_page="38",
            chapter="Foreword",
            layout_type=PageLayoutType.PROSE_SINGLE_COLUMN,
            has_temperatures=True,
            has_curved_gutter_or_noise=True,
            key_challenges=["temperatury ze stopniami", "skróty miar", "drobny druk"],
            description="Wstęp techniczny: temperatury, stopnie i miary w tekście ciągłym.",
        ),
        TestPageEntry(
            pdf_page=54,
            printed_page="52",
            chapter="Spices, Herbs, Seasonings",
            layout_type=PageLayoutType.TABLE_OR_GLOSSARY,
            has_bilingual_titles=True,
            has_curved_gutter_or_noise=True,
            key_challenges=["uszkodzone polskie znaki", "daszki i stopnie w środku wyrazów", "krótkie bloki"],
            description="Przyprawy: słowniczek ziół, uszkodzenia diakrytyków przez brak UTF-8.",
        ),
        TestPageEntry(
            pdf_page=58,
            printed_page="56",
            chapter="Spices, Herbs, Seasonings",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_fractions=True,
            has_temperatures=True,
            has_bilingual_titles=True,
            key_challenges=["temperatura 375°F vs zniekształcenie 3758", "ułamki w proporcjach ziół"],
            description="Mieszanki przypraw: krytyczny test rozpoznania temperatury i ułamków.",
        ),
        # 8-20: Uzupełnienie zbioru dev z kolejnych rozdziałów książki
        TestPageEntry(
            pdf_page=72,
            printed_page="70",
            chapter="Spices, Herbs, Seasonings",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_fractions=True,
            key_challenges=["ułamki wagowe (2/3 oz.)", "drożdże świeże", "proporcje zakwasu"],
            description="Drożdże i zaczyny: miary ułamkowe uncji i gramów.",
        ),
        TestPageEntry(
            pdf_page=96,
            printed_page="94",
            chapter="Cold Hors d'Oeuvres, Appetizers & Starter Courses",
            layout_type=PageLayoutType.RECIPE_TWO_COLUMN,
            has_fractions=True,
            has_bilingual_titles=True,
            key_challenges=["układ dwukolumnowy", "bilingwalny tytuł SALT HERRING (SLEDZ)", "lista składników"],
            description="Zimne zakąski: dwukolumnowy przepis na śledzia, polskie znaki w tytule.",
        ),
        TestPageEntry(
            pdf_page=119,
            printed_page="117",
            chapter="Cold Hors d'Oeuvres, Appetizers & Starter Courses",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_fractions=True,
            has_temperatures=True,
            key_challenges=["kilka podprzepisów na jednej stronie", "czas chłodzenia", "ułamki łyżeczek"],
            description="Zakąski mięsne: plasterkowanie, zawijanie w gazę, chłodzenie.",
        ),
        TestPageEntry(
            pdf_page=125,
            printed_page="123",
            chapter="Cold Hors d'Oeuvres, Appetizers & Starter Courses",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_fractions=True,
            has_tables=True,
            key_challenges=["galarety i auszpiki", "proporcje żelatyny", "struktura tabelaryczna"],
            description="Galarety mięsne: proporcje wywaru i żelatyny, klarowanie.",
        ),
        TestPageEntry(
            pdf_page=142,
            printed_page="140",
            chapter="Cold Savory Sauces, Dressings & Relishes",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_fractions=True,
            key_challenges=["sosy zimne", "skróty łyżek i łyżeczek (tbsp, tsp)", "ocet vs cytryna"],
            description="Sosy zimne: majonezy i sosy chrzanowe, skróty kulinarne.",
        ),
        TestPageEntry(
            pdf_page=161,
            printed_page="159",
            chapter="Cold-Appetizer & Supper Salads",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_fractions=True,
            has_bilingual_titles=True,
            key_challenges=["sałatki warzywne i mięsne", "polskie znaki diakrytyczne", "składniki w punktach"],
            description="Sałatki: surówki i sałatki jarzynowe, polska terminologia.",
        ),
        TestPageEntry(
            pdf_page=193,
            printed_page="191",
            chapter="Soups—Hot & Cold, Savory & Sweet",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_fractions=True,
            has_bilingual_titles=True,
            key_challenges=["barszcz czysty i zabielany", "przecedzanie wywaru", "zakwas buraczany"],
            description="Zupy: klasyczny barszcz czerwony, etapy gotowania i cedzenia.",
        ),
        TestPageEntry(
            pdf_page=234,
            printed_page="232",
            chapter="Soup Garnishes & Accompaniments",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_fractions=True,
            has_bilingual_titles=True,
            key_challenges=["dodatki do zup: kołduny, uszka", "lepienie ciasta", "farsz grzybowy"],
            description="Dodatki do zup: ciasto na uszka, proporcje farszu i ciasta.",
        ),
        TestPageEntry(
            pdf_page=263,
            printed_page="261",
            chapter="Hot Meats & Meat Dishes",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_bilingual_titles=True,
            key_challenges=["dania mięsne na gorąco", "wstawki historyczne o kuchni szlacheckiej"],
            description="Dania mięsne: pieczenie staropolskie, tło kulturowe.",
        ),
        TestPageEntry(
            pdf_page=329,
            printed_page="327",
            chapter="Poultry & Rabbit",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_temperatures=True,
            has_bilingual_titles=True,
            key_challenges=["drób i dziczyzna", "faszerowanie kaczki jabłkami", "temperatura pieczenia"],
            description="Drób: pieczona kaczka, temperatura i czas pieczenia.",
        ),
        TestPageEntry(
            pdf_page=386,
            printed_page="375",
            chapter="Fish Entrees",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_fractions=True,
            has_bilingual_titles=True,
            key_challenges=["dania rybne", "ryba po polsku", "jaja na twardo i masło"],
            description="Ryby: ryba po polsku z jajkiem i koperkiem, proporcje masła.",
        ),
        TestPageEntry(
            pdf_page=435,
            printed_page="420",
            chapter="Mushroom Dishes",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_temperatures=True,
            has_bilingual_titles=True,
            key_challenges=["grzyby suszone i świeże", "moczenie i wywar grzybowy", "czas duszenia"],
            description="Dania z grzybów: suszone borowiki, duszenie ze śmietaną.",
        ),
        TestPageEntry(
            pdf_page=485,
            printed_page="470",
            chapter="Noodles, Dumplings, Pancakes & C.",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_bilingual_titles=True,
            key_challenges=["pierogi i kluski", "zagniatanie ciasta", "lepienie brzegów"],
            description="Mączne: ciasto pierogowe, technika wałkowania i zagniatania.",
        ),
    ]

    holdout_pages: list[TestPageEntry] = [
        TestPageEntry(
            pdf_page=517,
            printed_page="502",
            chapter="Hot Savory Sauces, Gravies & Toppings",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_fractions=True,
            key_challenges=["zasmażka biała i ciemna", "proporcje mąki do tłuszczu", "sos grzybowy"],
            description="Sosy gorące: technika zasmażki i sosy na bazie wywarów.",
        ),
        TestPageEntry(
            pdf_page=560,
            printed_page="527",
            chapter="Cooked Vegetables",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_bilingual_titles=True,
            key_challenges=["kapusta zasmażana", "buraczki zasmażane", "balans słodko-kwaśny"],
            description="Warzywa gotowane: zasmażana kapusta, terminologia diakrytyczna.",
        ),
        TestPageEntry(
            pdf_page=620,
            printed_page="587",
            chapter="Dairy Products, Home Cheese Making & Egg Dishes",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_temperatures=True,
            key_challenges=["domowy twaróg", "temperatura ścinania skrzepu", "odsączanie serwatki"],
            description="Nabiał: produkcja domowego białego sera, kontrola temperatury.",
        ),
        TestPageEntry(
            pdf_page=650,
            printed_page="617",
            chapter="Puddings, Kisiels, Jellies & Other Desserts",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_bilingual_titles=True,
            key_challenges=["kisiel żurawinowy", "mąka ziemniaczana", "kremy i budynie"],
            description="Desery: tradycyjny kisiel, rozpuszczanie skrobi, gotowanie owoców.",
        ),
        TestPageEntry(
            pdf_page=680,
            printed_page="647",
            chapter="Babkas, Mazurkas, Tortes & Other Cakes",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_temperatures=True,
            key_challenges=["ciasta drożdżowe", "temperatura pieczenia babki", "bakalie i lukier"],
            description="Ciasta: wielkanocna babka drożdżowa, wyrastanie i pieczenie.",
        ),
        TestPageEntry(
            pdf_page=700,
            printed_page="667",
            chapter="Pastries & Small Cakes",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_temperatures=True,
            key_challenges=["chruściki / faworki", "temperatura głębokiego tłuszczu", "kruchość ciasta"],
            description="Drobne wypieki: smażenie faworków, spirytus w cieście, temperatura smalcu.",
        ),
        TestPageEntry(
            pdf_page=740,
            printed_page="707",
            chapter="Beverages Hot & Cold",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_temperatures=True,
            key_challenges=["kompoty i napoje ziołowe", "czas parzenia", "miód i przyprawy korzenne"],
            description="Napoje: kompot z suszu, parzenie ziół, syropy owocowe.",
        ),
        TestPageEntry(
            pdf_page=800,
            printed_page="767",
            chapter="Home-canning, Pickling & Preserving",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_temperatures=True,
            key_challenges=["ogórki kiszone i konserwowe", "proporcje solanki", "pasteryzacja słoików"],
            description="Przetwory: kiszenie ogórków, koper, chrzan, sterylizacja słoików.",
        ),
        TestPageEntry(
            pdf_page=840,
            printed_page="807",
            chapter="Bread & Rolls",
            layout_type=PageLayoutType.RECIPE_SINGLE_COLUMN,
            has_temperatures=True,
            key_challenges=["chleb na zakwasie", "chałka", "temperatury zapiekania i studzenia"],
            description="Pieczywo: chleb polski, wyrabianie ciasta żytniego, pieczenie.",
        ),
        TestPageEntry(
            pdf_page=905,
            printed_page="872",
            chapter="Recipe Index in Polish",
            layout_type=PageLayoutType.INDEX_MULTI_COLUMN,
            has_bilingual_titles=True,
            key_challenges=["3-kolumnowy gęsty skorowidz", "alfabetyczny porządek polskich liter", "numery stron przepisów"],
            description="Skorowidz polski: gęsty indeks wielokolumnowy, polskie znaki w nagłówkach.",
        ),
    ]

    manifest = BenchmarkPagesManifest(
        dev_pages=dev_pages,
        holdout_pages=holdout_pages,
    )
    validation = validate_manifest_consistency(manifest)
    if not validation["is_valid"]:
        raise ValueError(f"Błąd spójności manifestu: {validation['errors']}")

    return manifest


def save_test_pages_manifest(
    manifest: BenchmarkPagesManifest,
    target_path: Path = Path("benchmarks/fixtures/test_pages_manifest.json"),
) -> Path:
    """Zapisuje manifest do wskazanego pliku JSON."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return target_path


def load_test_pages_manifest(
    manifest_path: Path = Path("benchmarks/fixtures/test_pages_manifest.json"),
) -> BenchmarkPagesManifest:
    """Wczytuje i waliduje manifest stron testowych z pliku JSON."""
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Plik manifestu {manifest_path} nie istnieje.")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = BenchmarkPagesManifest.model_validate(data)
    validation = validate_manifest_consistency(manifest)
    if not validation["is_valid"]:
        raise ValueError(f"Nieprawidłowy manifest stron testowych: {validation['errors']}")
    return manifest
